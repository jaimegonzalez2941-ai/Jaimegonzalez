"""Permission checks shared by commands, buttons, and modals."""

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


def private_channel(channel: discord.TextChannel, staff_role: discord.Role):
    """Fail closed when explicit overwrites allow a non-staff audience."""
    if channel.overwrites_for(channel.guild.default_role).view_channel is not False:
        raise AccessDenied("The staff channel must explicitly deny View Channel to @everyone.")
    me = channel.guild.me
    if me is None:
        raise AccessDenied("The bot's server membership is unavailable. Try again shortly.")
    for target, overwrite in channel.overwrites.items():
        if overwrite.view_channel is not True:
            continue
        if target.id == me.id:
            allowed = True
        elif isinstance(target, discord.Role):
            allowed = (
                target.id == staff_role.id
                or target.permissions.administrator or target.permissions.manage_guild
            )
        elif isinstance(target, discord.Member):
            allowed = (
                target.guild_permissions.manage_guild
                or staff_role in target.roles
            )
        else:
            # discord.py may represent an uncached former member's overwrite as
            # discord.Object. Never treat an unknown target as a verified member.
            allowed = False
        if not allowed:
            raise AccessDenied(
                "That channel explicitly permits a viewer the bot cannot verify as staff. "
                "Remove View Channel overrides for non-staff users, including former or unresolved "
                "members, before saving this staff channel."
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
