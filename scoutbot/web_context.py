"""Fresh Discord authorization and a private command-response adapter."""

from datetime import datetime, timezone
import inspect
import math

import discord

from .access import AccessDenied, staff_access
from .web_catalog import command_for, field


async def current_member_context(bot, guild_id, actor_id):
    """Never use gateway member/role caches as website authorization authority."""
    if not bot.is_ready():
        raise ConnectionError("Discord is disconnected. Current server access cannot be verified.")
    guild = await bot.fetch_guild(int(guild_id))
    actor = await guild.fetch_member(int(actor_id))
    me = await guild.fetch_member(bot.user.id)
    # fetch_guild includes current roles/owner; add only the individually fetched
    # members needed by discord.py's permission calculators. No member enumeration.
    guild._members[actor.id] = actor
    guild._members[me.id] = me
    interaction = WebInteraction(bot, guild, actor)
    return interaction


async def current_context(bot, guild_id, actor_id):
    interaction = await current_member_context(bot, guild_id, actor_id)
    await staff_access(interaction)
    return interaction


class WebInteraction:
    def __init__(self, bot, guild, actor):
        self.client, self.guild, self.user = bot, guild, actor
        self.guild_id = guild.id
        self.created_at = datetime.now(timezone.utc)
        self.command = None
        self.messages = []
        self.view = None
        self.response = WebResponse(self)
        self.followup = self.response

    async def edit_original_response(self, **kwargs):
        await self.response.send(**kwargs)

    def result(self):
        return {"message": "\n\n".join(m["text"] for m in self.messages if m["title"] == ""),
                "blocks": [m for m in self.messages if m["title"]]}


class WebResponse:
    def __init__(self, interaction):
        self.interaction = interaction
        self.done = False

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        self.done = True

    async def send(self, content=None, *, embed=None, embeds=None, view=None, **kwargs):
        self.done = True
        target = self.interaction
        if content:
            target.messages.append({"title": "", "text": str(content)})
        for item in ([embed] if embed else embeds or []):
            parts = [item.description or ""]
            parts.extend(f"**{f.name}**\n{f.value}" for f in item.fields)
            if item.footer.text:
                parts.append(item.footer.text)
            target.messages.append({"title": item.title or "Saved evidence", "text": "\n\n".join(parts)})
        if view:
            target.view = view

    send_message = send
    edit_message = send


async def parameter_value(interaction, parameter, value):
    spec = field(parameter)
    kind = spec["type"]
    if kind in {"integer", "number"}:
        if isinstance(value, bool):
            raise ValueError("Enter a number, not a checkbox value.")
        try:
            number = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"{spec['label']} must be a number.") from None
        if not math.isfinite(number) or (kind == "integer" and not number.is_integer()):
            raise ValueError(f"{spec['label']} must be a finite {kind}.")
        value = int(number) if kind == "integer" else number
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{spec['label']} must be true or false.")
    elif kind in {"member", "role", "channel"}:
        if not isinstance(value, str) or not value.isdecimal() or not 15 <= len(value) <= 22:
            raise ValueError(f"{spec['label']}: enter an exact numeric Discord ID.")
        identifier = int(value)
        if kind == "member":
            value = await interaction.guild.fetch_member(identifier)
        elif kind == "role":
            value = interaction.guild.get_role(identifier)
        else:
            value = await interaction.guild.fetch_channel(identifier)
            if not isinstance(value, discord.TextChannel):
                raise ValueError("Choose a text channel in this server.")
        if value is None or value.guild.id != interaction.guild_id:
            raise ValueError("That item is unavailable in the selected server.")
    else:
        if not isinstance(value, str):
            raise ValueError(f"{spec['label']} must be text.")
        if len(value) > 4000:
            raise ValueError("Text is too long.")
    if parameter.choices and str(value) not in {str(c.value) for c in parameter.choices}:
        raise ValueError(f"Choose one of the supported {spec['label']} values.")
    bounded = len(value) if isinstance(value, str) else value
    if kind not in {"member", "role", "channel"}:
        if parameter.min_value is not None and bounded < parameter.min_value:
            raise ValueError(f"{spec['label']} is below its minimum.")
        if parameter.max_value is not None and bounded > parameter.max_value:
            raise ValueError(f"{spec['label']} exceeds its maximum.")
    return value


async def invoke(bot, interaction, name, values, confirmed):
    command = command_for(bot, name)
    interaction.command = command
    accepted = {p.name for p in command.parameters}
    if set(values) - accepted:
        raise ValueError("Unknown operation fields.")
    kwargs = {}
    for parameter in command.parameters:
        if parameter.name == "confirm":
            kwargs["confirm"] = confirmed
        elif parameter.name in values and values[parameter.name] not in (None, ""):
            kwargs[parameter.name] = await parameter_value(interaction, parameter, values[parameter.name])
        elif parameter.required:
            raise ValueError(f"{parameter.name.replace('_', ' ').capitalize()} is required.")
    # Group checks are reinforced by current_context; command checks include
    # existing cooldowns. Manager/role safety stays in the callbacks themselves.
    for check in command.checks:
        passed = check(interaction)
        if inspect.isawaitable(passed):
            passed = await passed
        if not passed:
            raise AccessDenied("This operation is not currently available.")
    if name == "scout ingest":
        # This callback intentionally reads get_channel; populate only this
        # fresh guild's channels and supply a scoped facade, not global cache.
        config = await bot.db.settings(interaction.guild_id)
        if config:
            channel = await interaction.guild.fetch_channel(int(config["recap_channel"]))
            interaction.guild._channels[channel.id] = channel
    # Setup/status and notification commands use guild.get_channel. Retrieve
    # current channel definitions instead of authorizing against gateway cache.
    if name in {"scout setup", "scout status", "schedule notifications"}:
        channels = await interaction.guild.fetch_channels()
        interaction.guild._channels = {channel.id: channel for channel in channels}
    await command.callback(command.binding, interaction, **kwargs)
