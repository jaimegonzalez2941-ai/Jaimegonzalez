"""Run with `python -m scoutbot`. Tokens never appear in source or log output."""

import argparse
import asyncio
import logging
import os

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
    parser.add_argument("--check", action="store_true", help="Validate startup without connecting to Discord")
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
