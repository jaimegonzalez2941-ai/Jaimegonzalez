"""Apply the requested Discord identity only when its name/image changes."""

import hashlib
import logging
from pathlib import Path

import discord

NAME = "Club Agent"
AVATAR = Path(__file__).parent / "assets" / "club-agent.jpg"
logger = logging.getLogger(__name__)


async def apply_branding(bot):
    avatar = AVATAR.read_bytes()
    digest = hashlib.sha256(avatar).hexdigest()
    saved = await bot.db.one("SELECT digest FROM club_branding WHERE bot_id=?", (str(bot.user.id),))
    updates = {}
    if bot.user.name != NAME:
        updates["username"] = NAME
    if not saved or saved["digest"] != digest:
        updates["avatar"] = avatar
    if not updates:
        return
    try:
        await bot.user.edit(**updates)
    except discord.HTTPException as exc:
        logger.warning("Club Agent identity update failed (HTTP %s); existing identity retained.", exc.status)
        return
    async with bot.db.transaction() as conn:
        await conn.execute(
            "INSERT INTO club_branding VALUES (?,?) ON CONFLICT(bot_id) DO UPDATE SET digest=excluded.digest",
            (str(bot.user.id), digest))
    logger.info("Club Agent name and uploaded avatar applied successfully.")
