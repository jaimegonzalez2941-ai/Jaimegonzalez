"""Run with `python -m scoutbot`. Tokens never appear in source or log output."""

import argparse
import asyncio
import logging
import os
import json
from pathlib import Path

from .config import Settings


async def check(settings: Settings):
    """Offline startup check: schema, command serialization, and persistent views."""
    from .bot import ScoutBot

    bot = ScoutBot(settings)
    try:
        await bot.db.open()
        from .views import DocketView
        from .start_panel import StartPanel
        bot.register_commands()
        commands = bot.tree.get_commands()
        for command in commands:
            command.to_dict(bot.tree)
        assert DocketView().is_persistent()
        assert StartPanel().is_persistent()
        logging.getLogger(__name__).info(
            "Startup check passed: database, %d top-level slash commands/groups, persistent docket and Start Here.",
            len(commands),
        )
    finally:
        await bot.db.close()


def main():
    parser = argparse.ArgumentParser(description="Club Agent — scouting, availability and attendance")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Validate startup without connecting to Discord")
    modes.add_argument("--backup", action="store_true",
                       help="Create a private, consistent SQLite backup without connecting to Discord")
    modes.add_argument("--rehearse-restore", metavar="BACKUP",
                       help="Validate and rehearse a backup using a disposable database copy")
    modes.add_argument("--restore", metavar="BACKUP",
                       help="Replace the configured database from a validated backup")
    modes.add_argument("--inspect-setup", type=int, metavar="SERVER_ID",
                        help="Read-only live setup report; does not connect a gateway")
    modes.add_argument("--audit-permissions", type=int, metavar="SERVER_ID",
                        help="Read-only inventory of server roles, hierarchy, and channel overwrites")
    modes.add_argument("--remove-report-tier-access", type=int, metavar="SERVER_ID",
                        help="Remove only Trialist and Dev Squad View Channel grants from #staff-reports")
    modes.add_argument("--audit-report-member-overlaps", type=int, metavar="SERVER_ID",
                        help="Read explicit #staff-reports viewers and check for Trialist/Dev Squad overlap")
    modes.add_argument("--remove-report-member-overlaps", type=int, metavar="SERVER_ID",
                        help="Remove individual #staff-reports grants for approved tier members")
    parser.add_argument("--as-member", type=int, metavar="MEMBER_ID",
                        help="Verify this requesting server manager before an operator report")
    parser.add_argument("--approved-member-name", action="append", default=[],
                        help=argparse.SUPPRESS)
    parser.add_argument("--confirm-live-restore", action="store_true",
                        help="Required explicit confirmation for --restore")
    modes.add_argument("--apply-setup", metavar="JSON",
                        help="Trusted-workspace maintenance: validate and save a complete setup manifest")
    args = parser.parse_args()
    settings = Settings.load()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    if args.check:
        asyncio.run(check(settings))
        return
    if args.confirm_live_restore and args.restore is None:
        parser.error("--confirm-live-restore is only valid with --restore")
    if args.restore is not None and not args.confirm_live_restore:
        parser.error("--restore requires --confirm-live-restore")
    if args.backup or args.rehearse_restore is not None or args.restore is not None:
        from .backup_ops import BackupError, create_backup, rehearse_restore, restore_database

        try:
            if args.backup:
                print(asyncio.run(create_backup(settings.database, settings.backup_directory)))
            elif args.rehearse_restore is not None:
                asyncio.run(rehearse_restore(Path(args.rehearse_restore)))
                print("Backup integrity and schema compatibility verified; isolated rehearsal passed.")
            else:
                preserved = asyncio.run(restore_database(
                    settings.database, Path(args.restore), settings.backup_directory
                ))
                print(f"Restore completed. Pre-restore database preserved at: {preserved}")
        except (BackupError, OSError, RuntimeError) as error:
            raise SystemExit(str(error)) from None
        return
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing. Add it through this project's Secrets tool.")
    if args.inspect_setup:
        if not args.as_member:
            parser.error("--inspect-setup requires --as-member")
        from .setup_operator import report_setup
        asyncio.run(report_setup(token, args.inspect_setup, args.as_member))
        return
    if args.audit_permissions:
        if not args.as_member:
            parser.error("--audit-permissions requires --as-member")
        from .permission_audit import print_permission_audit
        asyncio.run(print_permission_audit(token, settings, args.audit_permissions, args.as_member))
        return
    if args.remove_report_tier_access is not None:
        if not args.as_member:
            parser.error("--remove-report-tier-access requires --as-member")
        from .permission_audit import print_report_tier_access_change
        try:
            asyncio.run(print_report_tier_access_change(
                token, args.remove_report_tier_access, args.as_member
            ))
        except ValueError as error:
            raise SystemExit(str(error)) from None
        return
    if args.audit_report_member_overlaps is not None:
        if not args.as_member:
            parser.error("--audit-report-member-overlaps requires --as-member")
        from .permission_audit import print_report_member_overlap_audit
        try:
            asyncio.run(print_report_member_overlap_audit(
                token, args.audit_report_member_overlaps, args.as_member
            ))
        except ValueError as error:
            raise SystemExit(str(error)) from None
        return
    if args.remove_report_member_overlaps is not None:
        if not args.as_member:
            parser.error("--remove-report-member-overlaps requires --as-member")
        if not args.approved_member_name:
            parser.error("--remove-report-member-overlaps requires --approved-member-name")
        from .permission_audit import print_report_member_access_change
        try:
            asyncio.run(print_report_member_access_change(
                token,
                args.remove_report_member_overlaps,
                args.as_member,
                args.approved_member_name,
            ))
        except ValueError as error:
            raise SystemExit(str(error)) from None
        return
    if args.apply_setup:
        from .setup_operator import apply_setup
        try:
            result = asyncio.run(apply_setup(token, settings, json.loads(args.apply_setup)))
        except ValueError as error:
            raise SystemExit(str(error)) from None
        print(json.dumps(result, ensure_ascii=False))
        return
    import discord
    from .bot import ScoutBot

    bot = ScoutBot(settings)
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("Discord rejected the bot token. Update DISCORD_TOKEN securely in Secrets.") from None
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Enable Message Content Intent in Discord Developer Portal > Bot, then restart. "
            "Alternatively set ENABLE_RECAPS=false to use scouting without automatic recaps."
        ) from None


if __name__ == "__main__":
    main()
