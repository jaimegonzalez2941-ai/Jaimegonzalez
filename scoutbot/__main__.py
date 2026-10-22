"""Run with `python -m scoutbot`. Tokens never appear in source or log output."""

import argparse
import asyncio
import logging
import os
import json

import discord

from .bot import ScoutBot
from .config import Settings


async def check(settings: Settings):
    """Offline startup check: schema, command serialization, and persistent views."""
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
    modes.add_argument("--inspect-setup", type=int, metavar="SERVER_ID",
                        help="Read-only live setup report; does not connect a gateway")
    parser.add_argument("--as-member", type=int, metavar="MEMBER_ID",
                        help="Verify this requesting server manager before an operator report")
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
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing. Add it through this project's Secrets tool.")
    if args.inspect_setup:
        if not args.as_member:
            parser.error("--inspect-setup requires --as-member")
        from .setup_operator import report_setup
        asyncio.run(report_setup(token, args.inspect_setup, args.as_member))
        return
    if args.apply_setup:
        from .setup_operator import apply_setup
        try:
            result = asyncio.run(apply_setup(token, settings, json.loads(args.apply_setup)))
        except ValueError as error:
            raise SystemExit(str(error)) from None
        print(json.dumps(result, ensure_ascii=False))
        return
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
