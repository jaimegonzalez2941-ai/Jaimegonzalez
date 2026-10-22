"""Permission checks shared by commands, buttons, and modals."""

import asyncio

import aiohttp
import discord
from discord import app_commands

from .domain import is_staff


class AccessDenied(ValueError, app_commands.CheckFailure):
    pass


async def staff_access(interaction: discord.Interaction) -> dict | None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        raise AccessDenied("Use this command inside your server, not in a direct message.")
    config = await interaction.client.db.settings(interaction.guild.id)
    if not is_staff(
        manage_guild=interaction.user.guild_permissions.manage_guild,
        role_ids={r.id for r in interaction.user.roles},
        staff_role=config["staff_role"] if config else None,
    ):
        raise AccessDenied("Only the configured scouting staff or server managers can use this.")
    return config


def manager_access(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
        raise AccessDenied("You need Manage Server permission to change bot settings.")


async def staff_assignee(interaction, member):
    """Fetch one selected member over REST; never enumerate the member list."""
    config = await staff_access(interaction)
    if member.guild.id != interaction.guild.id:
        raise AccessDenied("Choose a staff member in this server.")
    current = await interaction.guild.fetch_member(member.id)
    if current.bot or not is_staff(
        manage_guild=current.guild_permissions.manage_guild,
        role_ids={role.id for role in current.roles},
        staff_role=config["staff_role"] if config else None,
    ):
        raise AccessDenied("The responsible member must currently be configured staff or a server manager.")
    return current


def permission_entry(target):
    """Label a permission target without mentions or assuming unresolved membership."""
    if isinstance(target, discord.Role):
        kind, name = "role", target.name
    elif isinstance(target, discord.Member):
        kind, name = "member", target.display_name
    else:
        kind = "role" if isinstance(target, discord.Object) and target.type is discord.Role else "member"
        return f"unresolved {kind} entry (ID {target.id})"
    name = discord.utils.escape_markdown(discord.utils.escape_mentions(" ".join(str(name).split())[:100]))
    return f'{kind} "{name}" (ID {target.id})'


async def resolve_permission_member(guild, target, deadline):
    """Verify one uncached viewer over REST without enumerating guild members."""
    try:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            current = await guild.fetch_member(target.id)
    except discord.NotFound as error:
        raise AccessDenied(
            f"Staff-channel check blocked by a stale member entry (ID {target.id}). "
            "Discord says this account is no longer in this server. Remove its entry from "
            "the staff channel's Permissions, then try again. No permissions have been changed."
        ) from error
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError, ConnectionError) as error:
        raise AccessDenied(
            f"Discord could not verify the staff-channel member entry (ID {target.id}) right now. "
            "Access remains unverified. Try again shortly; do not grant Administrator or make "
            "the channel public. No permissions have been changed."
        ) from error
    if (
        not isinstance(current, discord.Member)
        or current.id != target.id
        or current.guild.id != guild.id
    ):
        raise AccessDenied(
            f"Discord did not return a matching member of this server for entry ID {target.id}. "
            "The staff-channel check remains blocked. Try again or review that permission entry."
        )
    return current


async def private_channel(channel: discord.TextChannel, staff_role: discord.Role):
    """Verify explicit viewers, failing closed on unsafe or unverifiable access."""
    if staff_role.guild.id != channel.guild.id:
        raise AccessDenied("Choose a staff role and staff channel from the same server.")
    if channel.overwrites_for(channel.guild.default_role).view_channel is not False:
        raise AccessDenied("The staff channel must explicitly deny View Channel to @everyone.")
    me = channel.guild.me
    if me is None:
        raise AccessDenied("The bot's server membership is unavailable. Try again shortly.")
    # One total lookup budget, not a fresh timeout for every permission entry.
    deadline = asyncio.get_running_loop().time() + 8
    for target, overwrite in channel.overwrites.items():
        if overwrite.view_channel is not True:
            continue
        if (
            isinstance(target, discord.Object)
            # GuildChannel.overwrites uses the abc.User protocol, not the
            # concrete User class, for uncached member permission entries.
            and target.type in (discord.abc.User, discord.User, discord.Member)
            and target.id != me.id
        ):
            target = await resolve_permission_member(channel.guild, target, deadline)
        if target.id == me.id:
            allowed = True
        elif isinstance(target, discord.Role):
            own_bot_role = (
                target.managed and target.tags is not None
                and target.tags.bot_id == me.id
            )
            allowed = target.guild.id == channel.guild.id and (
                own_bot_role or target.id == staff_role.id
                or target.permissions.administrator or target.permissions.manage_guild
            )
        elif isinstance(target, discord.Member):
            allowed = target.guild.id == channel.guild.id and (
                target.guild_permissions.manage_guild
                or staff_role in target.roles
            )
        else:
            # Missing roles and untyped objects cannot be resolved as members.
            allowed = False
        if not allowed:
            raise AccessDenied(
                f"Staff-channel check blocked by {permission_entry(target)}.\n"
                "View Channel is explicitly set to Allow for this entry, but staff access cannot be verified.\n"
                f"Selected staff role: {permission_entry(staff_role)}.\n"
                "Review this entry in the selected staff channel's Permissions. For non-staff viewers, "
                "including former or unresolved members, remove this override or set View Channel to Neutral. "
                "If this is intended staff access, verify the staff role selected in /scout setup instead. "
                "No permissions have been changed."
            )
    permissions = channel.permissions_for(me)
    if not all((permissions.view_channel, permissions.send_messages, permissions.embed_links)):
        raise AccessDenied("The bot needs View Channel, Send Messages, and Embed Links in the staff channel.")


def safe_recruitment_role(role: discord.Role, actor: discord.Member):
    me = role.guild.me
    if me is None or not me.guild_permissions.manage_roles:
        raise AccessDenied("The bot needs Manage Roles to assign recruitment roles.")
    if role.is_default() or role.managed or role >= me.top_role:
        raise AccessDenied("Choose a normal role below the bot's highest role.")
    permissions = role.permissions
    unsafe = (
        permissions.administrator or permissions.manage_guild or permissions.manage_roles
        or permissions.manage_channels or permissions.manage_webhooks
        or permissions.kick_members or permissions.ban_members
        or permissions.moderate_members or permissions.mention_everyone
    )
    if unsafe:
        raise AccessDenied("Recruitment roles must not grant administrative or moderation powers.")
    if actor.id != role.guild.owner_id and role >= actor.top_role:
        raise AccessDenied("The recruitment role must be below your highest role.")
