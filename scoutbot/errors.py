"""Shared, private error reports for slash commands, buttons and modal forms."""

import logging
import math
import uuid

import aiohttp
import discord
from discord import app_commands

from .access import AccessDenied

logger = logging.getLogger(__name__)

# These are recovery directions, never automatic retries or permission changes.
RECOVERY = {
    "club": "Run /club to open a fresh private menu.",
    "start_panel": "Review the chosen channel and the existing panel before retrying /start_panel; do not post a second panel to bypass an uncertain result.",
    "scout setup": "Review the selected staff role, private staff channel and recap channel. Supply exactly one source: another bot OR a numeric webhook ID.",
    "scout stage_role": "Choose a normal role below Club Agent's highest role, without administrative powers. Do not use the scouting staff role as a recruitment tier.",
    "scout assign": "Check /promotion review and the bot's role order. Check whether the role is already assigned before retrying.",
    "scout evaluate": "Reopen /scout evaluate or resume your saved draft. Confirm the saved history before submitting again.",
    "scout ingest": "Check /scout status and the trusted recap's message ID before retrying. An uncertain result does not prove that an import failed.",
    "scout": "Check the supplied player, position or record ID. Use /scout help for scouting guidance.",
    "availability": "Check the weekday, date range and IANA timezone. Players can manage their own records; staff review other players and leave decisions.",
    "schedule": "Check the session ID with /schedule list or /schedule show. Confirm current session details before repeating a create, edit, RSVP or assignment.",
    "attendance": "Check the session and player. Staff can mark committed players after kickoff; reports include ended sessions. Missing marks are not automatic absences.",
    "promotion": "Use /promotion review to check current evidence. Changing attendance policy requires Manage Server; role decisions remain staff-controlled.",
    "development": "Check the goal ID, responsible staff member and deadline. A deadline change needs date, time and timezone together.",
    "assistant": "Check the task ID, current staff owner and deadline. Review the current task before retrying a change or completion.",
}


def command_name(interaction):
    command = getattr(interaction, "command", None)
    name = getattr(command, "qualified_name", None)
    if isinstance(name, str) and name:
        return name[:100]
    data = getattr(interaction, "data", None)
    if isinstance(data, dict):
        if isinstance(data.get("name"), str):
            return data["name"][:100]
        custom_id = data.get("custom_id", "")
        if isinstance(custom_id, str) and custom_id.startswith(("club:start:", "club:launch:")):
            return "club"
        if custom_id == "scout:v2:docket:refresh":
            return "scout docket"
    return None


def recovery(name):
    if not name:
        return "Reopen the original slash command or /club. Review current records before repeating a change."
    return RECOVERY.get(name, RECOVERY.get(name.split()[0],
        "Check the supplied options and reopen the command. Use /club for guided help."))


def safe(value, limit):
    return discord.utils.escape_mentions(str(value))[:limit]


def describe_error(interaction, error):
    while isinstance(error, app_commands.CommandInvokeError):
        error = error.original
    name = command_name(interaction)
    next_step = recovery(name)
    reference = None
    if isinstance(error, AccessDenied):
        title, reason = "Access or privacy check blocked this action", str(error)
        next_step = "Follow the permission or ownership requirement above. Only a server manager should change access settings. " + next_step
    elif isinstance(error, app_commands.CommandOnCooldown):
        title = "Please wait before trying again"
        seconds = max(1, math.ceil(error.retry_after))
        reason = f"This command is rate-limited. Wait {seconds} second(s)."
        next_step = "Wait for the cooldown, then retry once. Repeated taps will not bypass it."
    elif isinstance(error, app_commands.BotMissingPermissions):
        title = "Club Agent is missing permissions"
        reason = "Required: " + ", ".join(p.replace("_", " ") for p in error.missing_permissions) + "."
        next_step = "Ask a server manager to check the bot's role and channel overrides. Grant only the required permissions, not Administrator."
    elif isinstance(error, app_commands.MissingPermissions):
        title = "Your account needs permission for this action"
        reason = "Required: " + ", ".join(p.replace("_", " ") for p in error.missing_permissions) + "."
        next_step = "Ask a server manager to perform this action or review your intended staff access."
    elif isinstance(error, app_commands.NoPrivateMessage):
        title, reason = "Use this command in your server", "This action is not available in direct messages."
        next_step = "Open a channel in your server and run the command there."
    elif isinstance(error, (app_commands.CommandNotFound, app_commands.CommandSignatureMismatch)):
        title, reason = "Discord and the bot's command lists do not match", "This command or its options are unavailable in the current running version."
        next_step = "Reopen Discord's slash-command picker. If it persists, the bot operator must check the running version and command sync; changing channel permissions will not fix this."
    elif isinstance(error, app_commands.TransformerError):
        title, reason = "An option could not be read", "A selected member, role, channel or option could not be resolved. It may have changed or be unavailable."
        next_step = "Reopen the command and select the option from Discord's picker again. Use a server role for a role field, not an account name."
    elif isinstance(error, app_commands.CheckFailure):
        title, reason = "This action is not available to you here", "A command access check did not pass."
        next_step = "Use your own menu in the correct server. Ask a manager to review intended access; use /club for the routes available to you."
    elif isinstance(error, ValueError):
        title, reason = "Check the supplied details", str(error) or "A supplied value is not valid."
    elif isinstance(error, discord.Forbidden):
        title, reason = "Discord denied this action", "The bot lacks access to the requested channel, message, member or role."
        next_step = "Ask a manager to check this channel's overrides and the bot's role order. Do not assume a saved configuration proves current access. " + next_step
    elif isinstance(error, discord.NotFound):
        if error.code in (10062, 10015):
            title, reason = "This interaction has expired", "Discord no longer accepts replies to this interaction."
            next_step = "Open a fresh command or /club. Check current records before repeating any change."
        else:
            title, reason = "The selected Discord item is unavailable", "The requested message, channel, member or other item could not be found. This is not proof that all earlier work failed."
            next_step = "Re-select the current item or record. " + next_step
    elif isinstance(error, discord.InteractionResponded) or (
        isinstance(error, discord.HTTPException) and error.code == 40060
    ):
        title, reason = "Discord already received a response", "This interaction was already acknowledged."
        next_step = "Check the existing response. Open a fresh command if needed, but verify current records before repeating a change."
    elif isinstance(error, discord.HTTPException):
        title, reason = "Discord could not confirm this request", "Discord is temporarily unavailable, rate-limiting requests, or rejected the request. The result has not been confirmed."
        next_step = "Wait briefly, then check the existing record or message before retrying. Do not repeat role assignments or panel publication blindly."
    elif isinstance(error, (TimeoutError, ConnectionError, aiohttp.ClientError)):
        title, reason = "The request did not finish in time", "A network operation was interrupted. The action may have completed even though its confirmation did not arrive."
        next_step = "Check current records or messages first, then retry only if needed."
    else:
        title, reason = "The action could not be confirmed", "An unexpected error interrupted this action. Completion has not been confirmed."
        reference = uuid.uuid4().hex[:8]
        logger.error("Unhandled interaction error reference=%s command=%s", reference, safe(name or "component", 100),
                     exc_info=(type(error), error, error.__traceback__))
        next_step = "Check current records before retrying so work is not duplicated. Share the reference below with the bot operator; do not send passwords, tokens or webhook URLs."
    parts = [f"**{title}**"]
    if name:
        parts.append(f"Command: /{safe(name, 100)}")
    parts.extend((safe(reason, 950), "**Next step**\n" + safe(next_step, 650)))
    if reference:
        parts.append(f"Reference: {reference}")
    return "\n\n".join(parts)[:1900]


async def respond_error(interaction, error):
    """Never retry the action or fall back to a public error response."""
    message = describe_error(interaction, error)
    expiry = getattr(interaction, "is_expired", None)
    if callable(expiry) and expiry() is True:
        logger.warning("Error reply skipped: interaction expired")
        return False
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            try:
                await interaction.response.send_message(
                    message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            except discord.InteractionResponded:
                await interaction.followup.send(
                    message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as delivery:
                if delivery.code != 40060:
                    raise
                # Discord can acknowledge a response before the local flag updates.
                await interaction.followup.send(
                    message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as delivery:
        # An expired token cannot receive a new error about its own expired error reply.
        logger.warning("Private error reply unavailable: Discord status=%s code=%s",
                       delivery.status, delivery.code)
        return False
    except (TimeoutError, ConnectionError, aiohttp.ClientError):
        logger.warning("Private error reply unavailable: network interruption")
        return False
    return True
