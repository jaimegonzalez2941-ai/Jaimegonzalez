"""Bot lifecycle and event handling."""

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .commands import ScoutCommands
from .availability_commands import AvailabilityCommands
from .schedule_commands import ScheduleCommands
from .attendance_commands import AttendanceCommands, PromotionCommands
from .assistant_commands import AssistantCommands, DevelopmentCommands
from .club_ui import club_help
from .branding import apply_branding
from .config import Settings
from .database import Database
from .domain import source_allowed
from .ingestion import import_message
from .parser import RecapFormatError
from .views import DocketView, respond_error
from .notification_worker import deliver_due_notifications

logger = logging.getLogger(__name__)


class CommandTree(app_commands.CommandTree):
    async def on_error(self, interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"Please wait {error.retry_after:.0f} seconds before trying that command again.",
                ephemeral=True,
            )
            return
        await respond_error(interaction, getattr(error, "original", error))


class ScoutBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = settings.recaps_enabled
        intents.message_content = settings.recaps_enabled
        super().__init__(
            command_prefix=commands.when_mentioned, intents=intents,
            allowed_mentions=discord.AllowedMentions.none(), tree_cls=CommandTree,
            max_messages=200, help_command=None,
        )
        self.settings = settings
        self.db = Database(settings.database)

    def register_commands(self):
        for command in (ScoutCommands(self), AvailabilityCommands(self), ScheduleCommands(self),
                        AttendanceCommands(self), PromotionCommands(self), AssistantCommands(self),
                        DevelopmentCommands(self), club_help):
            self.tree.add_command(command)

    async def setup_hook(self):
        await self.db.open()
        self.add_view(DocketView())
        self.register_commands()
        if self.settings.test_guild:
            guild = discord.Object(id=self.settings.test_guild)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %d command groups to the test server.", len(synced))
        else:
            synced = await self.tree.sync()
            logger.info("Synced %d global command groups.", len(synced))
        await apply_branding(self)
        if not self.notification_worker.is_running():
            self.notification_worker.start()

    @tasks.loop(seconds=60)
    async def notification_worker(self):
        await deliver_due_notifications(self)

    @notification_worker.before_loop
    async def wait_for_notification_ready(self):
        await self.wait_until_ready()

    async def close(self):
        try:
            if self.notification_worker.is_running():
                self.notification_worker.cancel()
            await super().close()
        finally:
            await self.db.close()

    async def on_ready(self):
        logger.info("Club Agent connected; bot_id=%s server_count=%d", self.user.id, len(self.guilds))
        await self.change_presence(activity=discord.Game(name="/club • Start Here"))
        for guild in self.guilds:
            if guild.me and guild.me.nick and guild.me.nick != "Club Agent":
                try:
                    await guild.me.edit(nick="Club Agent", reason="Owner requested Club Agent branding")
                except discord.HTTPException:
                    logger.warning("Could not update the bot nickname in server %s.", guild.id)
        permissions = discord.Permissions(
            view_channel=True, send_messages=True, embed_links=True,
            read_message_history=True, manage_roles=True,
        )
        logger.info(
            "Install this bot using: %s",
            discord.utils.oauth_url(
                self.user.id, permissions=permissions, scopes=("bot", "applications.commands"),
            ),
        )

    async def process_recap(self, message: discord.Message):
        if not self.settings.recaps_enabled or message.guild is None or message.author.id == self.user.id:
            return
        config = await self.db.settings(message.guild.id)
        if not config or not source_allowed(
            config, channel_id=message.channel.id, author_id=message.author.id,
            author_is_bot=message.author.bot, webhook_id=message.webhook_id,
        ):
            return
        # Accepted messages are immutable, including after Discord edits.
        if await self.db.one(
            "SELECT message_id FROM recap_receipts WHERE guild_id=? AND message_id=?",
            (str(message.guild.id), str(message.id)),
        ):
            return
        try:
            inserted, count = await import_message(self, message, message.author.id)
            if inserted:
                logger.info("Recap imported guild=%s message=%s players=%d",
                            message.guild.id, message.id, count)
        except (RecapFormatError, ValueError) as exc:
            await self.db.audit(
                message.guild.id, message.author.id, "recap.rejected",
                {"message_id": str(message.id), "reason": str(exc)},
            )
            logger.warning("Unsupported recap guild=%s message=%s; see /scout status",
                           message.guild.id, message.id)

    async def on_message(self, message):
        await self.process_recap(message)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if not self.settings.recaps_enabled or not payload.guild_id:
            return
        config = await self.db.settings(payload.guild_id)
        if not config or str(payload.channel_id) != config["recap_channel"]:
            return
        if await self.db.one(
            "SELECT message_id FROM recap_receipts WHERE guild_id=? AND message_id=?",
            (str(payload.guild_id), str(payload.message_id)),
        ):
            return
        channel = self.get_channel(payload.channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                await self.process_recap(await channel.fetch_message(payload.message_id))
            except (discord.NotFound, discord.Forbidden):
                logger.warning("Cannot fetch edited recap message=%s", payload.message_id)

    async def on_error(self, event_method, *args, **kwargs):
        logger.exception("Discord event failed: %s", event_method)
