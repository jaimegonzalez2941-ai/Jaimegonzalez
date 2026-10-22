"""Restart-safe, server-scoped delivery for session reminders and notices."""

import logging
from datetime import datetime

import discord

from .presentation import text

logger = logging.getLogger(__name__)


def notification_message(kind, event):
    start = int(datetime.fromisoformat(event["starts_at"]).timestamp())
    end = int(datetime.fromisoformat(event["ends_at"]).timestamp())
    title = text(event["title"], 100)
    details = f"**{title}** · {event['kind']}\n<t:{start}:F> – <t:{end}:t>"
    if kind == "reminder":
        return f"Upcoming session reminder\n{details}"
    if kind == "cancelled":
        return f"Session cancelled\n{details}"
    prefix = "Session updated"
    if event["revision_time_changed"]:
        prefix += " · existing RSVPs must be reconfirmed"
    return f"{prefix}\n{details}"


async def _report_failure(bot, candidate, error, *, uncertain=False):
    guild_id = int(candidate["guild_id"])
    await bot.db.finish_notification(
        guild_id, candidate["delivery_key"],
        status="uncertain" if uncertain else "failed", error=error)
    logger.error("Session notification delivery problem guild=%s event=%s: %s",
                 guild_id, candidate["event"]["id"], error)


async def deliver_candidate(bot, candidate):
    guild_id = int(candidate["guild_id"])
    event = candidate["event"]
    kind = candidate["kind"]
    channel_id = candidate["channel_id"]
    delivery_key = candidate["delivery_key"]

    claimed = await bot.db.claim_notification(
        guild_id, channel_id, event["id"], kind, delivery_key, event["revision"],
        event["starts_at"])
    if not claimed:
        return False

    guild = bot.get_guild(guild_id)
    if guild is None:
        await _report_failure(bot, candidate, "The bot is not connected to this server.")
        return False

    try:
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
    except (discord.Forbidden, discord.NotFound) as exc:
        await _report_failure(bot, candidate, f"Cannot access the configured channel ({type(exc).__name__}).")
        return False
    except discord.HTTPException as exc:
        await _report_failure(bot, candidate, f"Channel lookup failed ({type(exc).__name__}).",
                              uncertain=exc.status >= 500 or exc.status == 0)
        return False
    except Exception as exc:
        await _report_failure(bot, candidate, f"Channel lookup outcome is unknown ({type(exc).__name__}).",
                              uncertain=True)
        return False

    bot_member = guild.me
    if bot_member is None or not hasattr(channel, "permissions_for"):
        await _report_failure(bot, candidate, "The configured channel is unavailable or not a text channel.")
        return False
    try:
        permissions = channel.permissions_for(bot_member)
    except Exception as exc:
        await _report_failure(bot, candidate, f"Cannot check channel permissions ({type(exc).__name__}).")
        return False
    if not permissions.view_channel or not permissions.send_messages:
        await _report_failure(
            bot, candidate, "Missing View Channel or Send Messages permission in the configured channel.")
        return False

    # Re-read the current revision and settings immediately before the external send.
    event = await bot.db.validate_notification(
        guild_id, channel_id, event["id"], kind, delivery_key, event["revision"],
        event["starts_at"])
    if event is None:
        return False

    try:
        await channel.send(
            notification_message(kind, event),
            allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.NotFound) as exc:
        await _report_failure(bot, candidate, f"Discord rejected the channel send ({type(exc).__name__}).")
        return False
    except discord.HTTPException as exc:
        await _report_failure(
            bot, candidate, f"Discord send failed ({type(exc).__name__}, status {exc.status}).",
            uncertain=exc.status >= 500 or exc.status == 0)
        return False
    except Exception as exc:
        # A transport failure may happen after Discord accepted the message.
        await _report_failure(bot, candidate, f"Send outcome is unknown ({type(exc).__name__}).",
                              uncertain=True)
        return False

    await bot.db.finish_notification(guild_id, delivery_key, status="sent")
    logger.info("Session notification delivered guild=%s event=%s kind=%s",
                guild_id, event["id"], kind)
    return True


async def deliver_due_notifications(bot):
    try:
        candidates = await bot.db.notification_candidates()
    except Exception:
        logger.exception("Could not load due session notifications.")
        return 0
    delivered = 0
    for candidate in candidates:
        try:
            delivered += await deliver_candidate(bot, candidate)
        except Exception:
            logger.exception("Session notification worker failed guild=%s event=%s",
                             candidate["guild_id"], candidate["event"]["id"])
    return delivered
