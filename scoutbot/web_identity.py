"""Verified website-to-Discord account linking; registration confers no authority."""

import asyncio
import secrets
import time

import discord
from discord import app_commands

WEB_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_player_goals (
 guild_id TEXT NOT NULL, goal_id INTEGER NOT NULL, player_text TEXT NOT NULL,
 PRIMARY KEY(guild_id,goal_id)
);
CREATE TABLE IF NOT EXISTS web_discord_links (
 subject TEXT PRIMARY KEY, discord_id TEXT NOT NULL, linked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS web_link_discord ON web_discord_links(discord_id);
CREATE INDEX IF NOT EXISTS web_pending_leave ON club_leave(guild_id,status,id DESC);
"""


class IdentityLinks:
    def __init__(self, db):
        self.db = db
        self.challenges = {}
        self.lock = asyncio.Lock()

    async def challenge(self, subject):
        async with self.lock:
            now = time.monotonic()
            self.challenges = {code: item for code, item in self.challenges.items()
                               if item[1] > now and item[0] != subject}
            if len(self.challenges) >= 1000:
                raise ValueError("Too many pending verifications. Try again in a few minutes.")
            code = secrets.token_urlsafe(18)
            self.challenges[code] = (subject, now + 300)
            return {"code": code, "expiresIn": 300}

    async def redeem(self, code, member, guild):
        async with self.lock:
            item = self.challenges.pop(code, None)
            if item is None or item[1] <= time.monotonic():
                raise ValueError("That code expired or was already used. Generate a new code on the website.")
            subject = item[0]
            # Never silently relink an existing web account to a different Discord identity.
            async with self.db.transaction() as conn:
                async with conn.execute("SELECT discord_id FROM web_discord_links WHERE subject=?", (subject,)) as cur:
                    previous = await cur.fetchone()
                if previous and previous["discord_id"] != str(member.id):
                    raise ValueError("This website account is already linked to another Discord account.")
                from .database import utcnow
                await conn.execute(
                    "INSERT INTO web_discord_links VALUES (?,?,?) ON CONFLICT(subject) DO NOTHING",
                    (subject, str(member.id), utcnow()))
                await self.db._audit(conn, guild, member.id, "web.identity_verified", {})

    async def actor_id(self, subject):
        row = await self.db.one("SELECT discord_id FROM web_discord_links WHERE subject=?", (subject,))
        return int(row["discord_id"]) if row else None


@app_commands.command(name="website", description="Privately verify your Discord account for the Club Agent website")
@app_commands.guild_only()
@app_commands.describe(code="Your own short-lived website verification code. Never redeem someone else's code.")
@app_commands.checks.cooldown(3, 60, key=lambda i: i.user.id)
async def website_link(interaction: discord.Interaction, code: app_commands.Range[str, 20, 64]):
    await interaction.response.defer(ephemeral=True)
    await interaction.client.web_bridge.identities.redeem(code, interaction.user, interaction.guild_id)
    await interaction.followup.send(
        "Discord account verified. Return to the website and refresh access. "
        "This does not grant staff or manager permissions. Only redeem codes from your own signed-in account.",
        ephemeral=True)
