"""Isolated signed-in browser fixture. Never imported by application code.

Run explicitly with --socket and --subject-file. Only the Clerk subject written
to that local file can access these temporary records. There is no HTTP test
login endpoint, production flag, real server membership or Discord role write.
"""

import argparse
import asyncio
from datetime import timedelta
from pathlib import Path
import signal
import tempfile
from unittest.mock import AsyncMock, patch

import discord

from scoutbot.access import AccessDenied, staff_access
from scoutbot.bot import ScoutBot
from scoutbot.club_domain import stamp, utc_now
from scoutbot.config import Settings
from scoutbot.web_context import WebInteraction
from scoutbot.web_identity import WEB_SCHEMA

GUILD = 111111111111111111
ACTOR = 333333333333333333
PLAYER = 444444444444444444


async def main(socket, subject_file):
    import os
    os.environ["CLUB_WEB_SOCKET"] = str(socket)
    with tempfile.TemporaryDirectory(prefix="club-browser-fixture-") as directory:
        bot = ScoutBot(Settings(Path(directory) / "fixture.db", None, False, "INFO"))
        bot.register_commands()
        await bot.db.open()
        state = bot._connection
        state.user = discord.ClientUser(state=state, data={
            "id": "999999999999999999", "username": "Fixture bot", "discriminator": "0", "avatar": None})
        guild = discord.Guild(state=state, data={
            "id": str(GUILD), "name": "Isolated browser test club", "owner_id": str(ACTOR),
            "roles": [{"id": str(GUILD), "name": "@everyone", "permissions": "0", "position": 0}]})
        def member(identifier, username):
            return discord.Member(data={"user": {"id": str(identifier), "username": username,
                                                  "discriminator": "0", "avatar": None},
                                         "roles": [], "joined_at": None, "flags": 0}, guild=guild, state=state)
        actor = member(ACTOR, "Fixture manager")
        player = member(PLAYER, "Fixture player")
        guild._members[actor.id] = actor
        guild._members[player.id] = player
        state._guilds[guild.id] = guild
        bot.is_ready = lambda: True
        now = utc_now()
        await bot.db.create_event(GUILD, ACTOR, "Isolated planned training", "training",
                                  stamp(now + timedelta(days=3)), stamp(now + timedelta(days=3, hours=1)), "UTC")
        await bot.db.save_staff_record(GUILD, ACTOR, "task", ACTOR, "Review the isolated session",
                                      stamp(now + timedelta(days=2)), "UTC")
        async def current(_bot, guild_id, actor_id):
            if int(guild_id) != GUILD or int(actor_id) != ACTOR:
                raise AccessDenied("This server is not authorized for the fixture account.")
            interaction = WebInteraction(bot, guild, actor)
            await staff_access(interaction)
            return interaction
        async def actor_id(subject):
            allowed = subject_file.read_text().strip() if subject_file.exists() else ""
            return ACTOR if subject == allowed and allowed.startswith("user_") else None
        async def fetch_member(_guild, identifier):
            if identifier == ACTOR:
                return actor
            if identifier == PLAYER:
                return player
            raise AccessDenied("Use an isolated fixture player or manager ID.")
        bot.web_bridge.identities.actor_id = actor_id
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopped.set)
        with patch("scoutbot.web_bridge.current_context", current), \
                patch.object(discord.Guild, "fetch_member", fetch_member):
            await bot.web_bridge.start()
            print("Isolated browser fixture ready", flush=True)
            try:
                await stopped.wait()
            finally:
                await bot.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--subject-file", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.socket, args.subject_file))
