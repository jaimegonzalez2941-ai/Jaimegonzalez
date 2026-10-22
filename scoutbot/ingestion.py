"""Trusted-source ingestion; atomic, bounded, and idempotent."""

import discord

from .domain import source_allowed
from .parser import parse_recaps


async def import_message(bot, message: discord.Message, actor: int) -> tuple[bool, int]:
    if message.guild is None:
        raise ValueError("Recaps must belong to a server.")
    config = await bot.db.settings(message.guild.id)
    if not config or not source_allowed(
        config, channel_id=message.channel.id, author_id=message.author.id,
        author_is_bot=message.author.bot, webhook_id=message.webhook_id,
    ):
        raise ValueError("This message is not from the configured recap channel and trusted source.")
    texts = [message.content] if message.content else []
    for item in message.embeds:
        if item.description:
            texts.append(item.description)
        texts.extend(field.value for field in item.fields)
    players = parse_recaps(texts)
    inserted = await bot.db.ingest(
        message.guild.id, message.id, message.channel.id,
        message.created_at.isoformat(), players, actor,
    )
    return inserted, len(players)
