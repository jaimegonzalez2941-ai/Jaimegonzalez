"""A generic public launcher; all role-aware content stays in private guides."""

from datetime import datetime

import discord
from discord import app_commands

from .access import AccessDenied, manager_access
from .club_ui import card, member_access
from .start_guide import send_start
from .views import SecureView


class StartPanel(SecureView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start Here", style=discord.ButtonStyle.primary,
                       custom_id="club:launch:v1")
    async def launch(self, interaction, button):
        member_access(interaction)
        if (interaction.guild_id != interaction.guild.id or interaction.user.bot
                or interaction.user.guild.id != interaction.guild.id):
            raise AccessDenied("Open Start Here inside your own server.")
        # A fixed custom ID survives restarts, but only the saved, active panel is accepted.
        row = await interaction.client.db.start_panel(interaction.guild.id)
        if (not row or row["message_id"] != str(interaction.message.id)
                or row["channel_id"] != str(interaction.channel_id)):
            raise AccessDenied("This panel is no longer active. Use /club for a private guide.")
        await send_start(interaction)


def panel_card(marker):
    result = card("Club Agent · Start Here",
                  "Need help getting started? Press **Start Here** for your private guide.\n"
                  "Only you can see the menu that opens.")
    # A durable publication marker lets retries recover a send whose reply was lost.
    result.set_footer(text=f"Club Agent • Launch {marker}")
    return result


def check_manager(interaction):
    member_access(interaction)
    manager_access(interaction)
    if (interaction.guild_id != interaction.guild.id or interaction.user.bot
            or interaction.user.guild.id != interaction.guild.id):
        raise AccessDenied("Choose a channel in your own server.")


def check_channel(interaction, channel):
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild.id:
        raise AccessDenied("Choose a text channel in this server.")
    me = interaction.guild.me
    if me is None:
        raise AccessDenied("The bot's server membership is unavailable. Try again shortly.")
    actor = channel.permissions_for(interaction.user)
    if not actor.view_channel:
        raise AccessDenied("Choose a channel you can currently view.")
    permissions = channel.permissions_for(me)
    if not all((permissions.view_channel, permissions.send_messages,
                permissions.embed_links, permissions.read_message_history)):
        raise AccessDenied(
            "The bot needs View Channel, Send Messages, Embed Links and Read Message History "
            "in the selected channel. No panel was posted.")


async def saved_message(interaction, row):
    """Only a confirmed missing message permits replacement; access errors never do."""
    channel = await interaction.guild.fetch_channel(int(row["channel_id"]))
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild.id:
        raise AccessDenied("The saved panel channel is unavailable. Restore access before moving it.")
    if row["message_id"]:
        try:
            return await channel.fetch_message(int(row["message_id"]))
        except discord.NotFound:
            return None
    # Recover publication after a restart or an uncertain HTTP/database outcome.
    # A complete successful scan is required before deciding that no panel exists.
    after = datetime.fromisoformat(row["created_at"])
    async for message in channel.history(limit=None, after=after, oldest_first=True):
        if (message.author.id == interaction.client.user.id and message.embeds
                and message.embeds[0].footer.text == panel_card(row["marker"]).footer.text):
            await interaction.client.db.save_start_panel(interaction.guild.id, message.id)
            return message
    return None


@app_commands.command(name="start_panel", description="Post or update the server's public Start Here button")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(channel="Explicitly choose the welcome/help text channel for the one launch panel")
async def start_panel(interaction: discord.Interaction, channel: discord.TextChannel):
    check_manager(interaction)
    check_channel(interaction, channel)
    await interaction.response.defer(ephemeral=True)
    db = interaction.client.db
    try:
        async with db.start_panel_lock:
            check_manager(interaction)
            check_channel(interaction, channel)
            row = await db.start_panel(interaction.guild.id)
            message = None
            if row:
                try:
                    message = await saved_message(interaction, row)
                except discord.NotFound:
                    # A deleted channel cannot contain a surviving launch message.
                    message = None
            check_manager(interaction)
            check_channel(interaction, channel)
            if message and row["channel_id"] != str(channel.id):
                # Fail closed if the old channel became inaccessible: never leave two panels.
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                message = None
            if message is None:
                # Save intent BEFORE sending; retry reconciles an uncertain send instead of duplicating it.
                row = await db.reserve_start_panel(interaction.guild.id, channel.id)
                message = await channel.send(embed=panel_card(row["marker"]), view=StartPanel(),
                                             allowed_mentions=discord.AllowedMentions.none())
                await db.save_start_panel(interaction.guild.id, message.id)
            else:
                try:
                    await message.edit(content=None, embed=panel_card(row["marker"]), view=StartPanel(),
                                       allowed_mentions=discord.AllowedMentions.none())
                except discord.NotFound:
                    # Deleted during this update: retry will safely replace the saved missing message.
                    raise ValueError("The panel was deleted during the update. Run /start_panel again.") from None
    except discord.Forbidden:
        raise AccessDenied(
            "Discord denied access to the selected or saved panel channel/message. Restore the bot's "
            "channel permissions, then retry /start_panel. No replacement panel was posted.") from None
    except discord.HTTPException:
        raise ValueError(
            "Discord could not confirm the panel operation. Retry /start_panel in the same channel; "
            "the saved message or pending publication will be checked before posting.") from None
    await interaction.followup.send(f"Start Here panel ready: {message.jump_url}", ephemeral=True,
                                    allowed_mentions=discord.AllowedMentions.none())
