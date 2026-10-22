"""Public launch-panel contracts without posting to a real Discord server."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from scoutbot.access import AccessDenied
from scoutbot.bot import ScoutBot
from scoutbot.config import Settings
from scoutbot.start_panel import StartPanel, panel_card, start_panel


def http_error(kind, status):
    return kind(SimpleNamespace(status=status, reason="test"), "test")


class StartPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.bot = ScoutBot(Settings(Path(self.temp.name) / "bot.db", None, False, "INFO"))
        await self.bot.db.open()
        self.guild = SimpleNamespace(id=1, me=SimpleNamespace(id=900), fetch_channel=AsyncMock())
        self.channel = self.make_channel(20)
        self.guild.fetch_channel.return_value = self.channel
        self.manager = self.interaction(44, manager=True)
        self.player = self.interaction(42)

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    def interaction(self, identifier, *, manager=False, staff=False):
        user = Mock(spec=discord.Member)
        user.id, user.bot, user.guild = identifier, False, self.guild
        user.guild_permissions = discord.Permissions(manage_guild=manager)
        user.roles = [SimpleNamespace(id=10)] if staff else []
        return SimpleNamespace(
            guild=self.guild, guild_id=1, user=user, client=self.bot, channel_id=20,
            message=SimpleNamespace(id=100),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(),
                                     is_done=Mock(return_value=True)),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    def make_channel(self, identifier):
        channel = Mock(spec=discord.TextChannel)
        channel.id, channel.guild = identifier, self.guild
        channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, send_messages=True, embed_links=True, read_message_history=True)
        message = SimpleNamespace(id=100 + identifier - 20, edit=AsyncMock(), delete=AsyncMock(),
                                  jump_url="https://discord.com/channels/1/20/100")
        channel.send = AsyncMock(return_value=message)
        channel.fetch_message = AsyncMock(return_value=message)
        return channel

    async def publish(self, channel=None, interaction=None):
        await start_panel.callback(interaction or self.manager, channel or self.channel)
        return await self.bot.db.start_panel(1)

    def assert_no_mentions(self, kwargs):
        self.assertEqual(kwargs["allowed_mentions"].to_dict(),
                         discord.AllowedMentions.none().to_dict())

    async def test_explicit_manager_selection_posts_generic_panel_and_updates_same_message(self):
        self.bot.register_commands()
        self.channel.send.assert_not_awaited()  # Registration never publishes.
        row = await self.publish()
        self.assertEqual((row["channel_id"], row["message_id"]), ("20", "100"))
        kwargs = self.channel.send.call_args.kwargs
        self.assert_no_mentions(kwargs)
        self.assertTrue(kwargs["view"].is_persistent())
        self.assertEqual(kwargs["view"].children[0].label, "Start Here")
        text = str(kwargs["embed"].to_dict())
        for forbidden in ("RSVP", "leave", "staff", "@everyone", "<@"):
            self.assertNotIn(forbidden, text)
        self.manager.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertTrue(self.manager.followup.send.call_args.kwargs["ephemeral"])
        self.assert_no_mentions(self.manager.followup.send.call_args.kwargs)
        await self.publish()
        self.channel.send.assert_awaited_once()
        self.assertEqual(await self.bot.db.start_panel(2), None)
        message = self.channel.send.return_value
        message.edit.assert_awaited_once()
        self.assert_no_mentions(message.edit.call_args.kwargs)
        command = self.bot.tree.get_command("start_panel").to_dict(self.bot.tree)
        self.assertEqual(command["default_member_permissions"], discord.Permissions(manage_guild=True).value)
        self.assertEqual(command["options"][0]["name"], "channel")
        self.assertTrue(command["options"][0]["required"])

    async def test_public_click_opens_only_current_viewers_private_role_aware_guide(self):
        await self.publish()
        await self.bot.db.configure(1, 44, staff_role=10, staff_channel=30, recap_channel=31,
                                    source_kind="bot", source_id=900)
        view = StartPanel()
        for person, expected in (
            (self.player, ["player", "reference"]),
            (self.interaction(43, staff=True), ["player", "session", "scouting", "workspace", "reference"]),
            (self.manager, ["player", "session", "scouting", "workspace", "manager", "reference"]),
        ):
            await view.launch.callback(person)
            sent = person.followup.send.call_args.kwargs
            self.assertTrue(sent["ephemeral"])
            self.assert_no_mentions(sent)
            self.assertEqual(sent["view"].owner, person.user.id)
            self.assertEqual(sent["view"].guild, 1)
            self.assertEqual([o.value for o in sent["view"].menu.options], expected)
            person.edit_original_response.assert_not_awaited()
        # A former manager clicking again gets no manager or staff guidance.
        self.manager.user.guild_permissions = discord.Permissions.none()
        self.manager.followup.send.reset_mock()
        await view.launch.callback(self.manager)
        self.assertEqual([o.value for o in self.manager.followup.send.call_args.kwargs["view"].menu.options],
                         ["player", "reference"])

    async def test_restart_keeps_message_identity_and_button_registration(self):
        await self.publish()
        await self.bot.db.close()
        await self.bot.db.open()
        with patch.object(self.bot.tree, "sync", AsyncMock(return_value=[])), \
                patch("scoutbot.bot.apply_branding", AsyncMock()), \
                patch.object(self.bot.web_bridge, "start", AsyncMock()), \
                patch.object(self.bot.notification_worker, "start"), \
                patch.object(self.bot.db, "open", AsyncMock()):
            await self.bot.setup_hook()
        registered = [view for view in self.bot.persistent_views if isinstance(view, StartPanel)]
        self.assertEqual(len(registered), 1)
        self.assertTrue(registered[0].is_persistent())
        await registered[0].launch.callback(self.player)
        self.assertTrue(self.player.followup.send.call_args.kwargs["ephemeral"])
        await self.publish()
        self.channel.send.assert_awaited_once()

    async def test_unprivileged_dm_bot_and_cross_server_inputs_are_denied(self):
        for person in (self.player, self.interaction(43, staff=True)):
            with self.assertRaises(AccessDenied):
                await self.publish(interaction=person)
        self.manager.guild = None
        with self.assertRaises(AccessDenied):
            await self.publish()
        self.manager.guild = self.guild
        self.manager.user.bot = True
        with self.assertRaises(AccessDenied):
            await self.publish()
        self.manager.user.bot = False
        foreign = self.make_channel(21)
        foreign.guild = SimpleNamespace(id=2)
        with self.assertRaises(AccessDenied):
            await self.publish(foreign)
        self.manager.user.guild = SimpleNamespace(id=2)
        with self.assertRaises(AccessDenied):
            await self.publish()
        self.channel.send.assert_not_awaited()

    async def test_click_rejects_foreign_stale_dm_or_bot_interactions(self):
        await self.publish()
        for attribute, bad in (
            ("guild_id", 2), ("channel_id", 99), ("message", SimpleNamespace(id=999)), ("guild", None)
        ):
            original = getattr(self.player, attribute)
            setattr(self.player, attribute, bad)
            with self.assertRaises(AccessDenied):
                await StartPanel().launch.callback(self.player)
            setattr(self.player, attribute, original)
        self.player.user.guild = SimpleNamespace(id=2)
        with self.assertRaises(AccessDenied):
            await StartPanel().launch.callback(self.player)
        self.player.user.guild = self.guild
        self.player.user.bot = True
        with self.assertRaises(AccessDenied):
            await StartPanel().launch.callback(self.player)
        self.player.response.defer.assert_not_awaited()
        self.player.followup.send.assert_not_awaited()

    async def test_missing_panel_is_replaced_once_even_for_concurrent_requests(self):
        await self.publish()
        self.channel.fetch_message.side_effect = http_error(discord.NotFound, 404)
        replacement = self.channel.send.return_value
        replacement.id = 101
        # First fetch is missing, subsequent fetches retrieve the replacement.
        self.channel.fetch_message.side_effect = [http_error(discord.NotFound, 404), replacement]
        await asyncio.gather(self.publish(), self.publish())
        self.assertEqual(self.channel.send.await_count, 2)  # Original plus exactly one replacement.
        self.assertEqual((await self.bot.db.start_panel(1))["message_id"], "101")
        with self.assertRaises(AccessDenied):
            await StartPanel().launch.callback(self.player)

    async def test_move_deletes_old_panel_before_posting_new_and_rejects_old_clicks(self):
        await self.publish()
        target = self.make_channel(21)
        deleted = False

        async def delete():
            nonlocal deleted
            deleted = True

        async def send(**kwargs):
            self.assertTrue(deleted)
            return SimpleNamespace(id=101, jump_url="https://discord.com/channels/1/21/101")

        self.channel.send.return_value.delete.side_effect = delete
        target.send.side_effect = send
        await self.publish(target)
        row = await self.bot.db.start_panel(1)
        self.assertEqual((row["channel_id"], row["message_id"]), ("21", "101"))
        with self.assertRaises(AccessDenied):
            await StartPanel().launch.callback(self.player)

    async def test_deleted_channel_can_be_replaced_but_inaccessible_channel_cannot(self):
        await self.publish()
        target = self.make_channel(21)
        self.guild.fetch_channel.side_effect = http_error(discord.Forbidden, 403)
        with self.assertRaisesRegex(AccessDenied, "Restore"):
            await self.publish(target)
        target.send.assert_not_awaited()
        self.assertEqual((await self.bot.db.start_panel(1))["channel_id"], "20")
        self.guild.fetch_channel.side_effect = http_error(discord.NotFound, 404)
        await self.publish(target)
        target.send.assert_awaited_once()

    async def test_revoked_channel_permissions_do_not_post_replacements(self):
        await self.publish()
        self.channel.permissions_for.return_value = discord.Permissions.none()
        with self.assertRaises(AccessDenied):
            await self.publish()
        self.assertEqual(self.channel.send.await_count, 1)
        self.channel.permissions_for.return_value = discord.Permissions.all()
        self.channel.fetch_message.side_effect = http_error(discord.Forbidden, 403)
        with self.assertRaises(AccessDenied):
            await self.publish()
        self.assertEqual(self.channel.send.await_count, 1)

    async def test_permission_revocation_while_waiting_is_rechecked(self):
        async with self.bot.db.start_panel_lock:
            operation = asyncio.create_task(self.publish())
            await asyncio.sleep(0)
            self.manager.user.guild_permissions = discord.Permissions.none()
        with self.assertRaises(AccessDenied):
            await operation
        self.channel.send.assert_not_awaited()

    async def test_failed_old_panel_deletion_blocks_move(self):
        await self.publish()
        target = self.make_channel(21)
        self.channel.send.return_value.delete.side_effect = http_error(discord.Forbidden, 403)
        with self.assertRaises(AccessDenied):
            await self.publish(target)
        target.send.assert_not_awaited()
        self.assertEqual((await self.bot.db.start_panel(1))["channel_id"], "20")

    async def test_pending_send_is_recovered_after_restart_without_duplicate(self):
        row = await self.bot.db.reserve_start_panel(1, 20)
        recovered = SimpleNamespace(
            id=100, author=SimpleNamespace(id=900), embeds=[panel_card(row["marker"])],
            edit=AsyncMock(), jump_url="https://discord.com/channels/1/20/100")
        self.bot._connection.user = SimpleNamespace(id=900)

        async def history(**kwargs):
            yield SimpleNamespace(author=SimpleNamespace(id=800), embeds=[panel_card(row["marker"])])
            yield SimpleNamespace(author=SimpleNamespace(id=900), embeds=[panel_card("unrelated")])
            yield recovered

        self.channel.history = history
        await self.bot.db.close()
        await self.bot.db.open()
        await self.publish()
        self.channel.send.assert_not_awaited()
        recovered.edit.assert_awaited_once()
        self.assertEqual((await self.bot.db.start_panel(1))["message_id"], "100")
        await StartPanel().launch.callback(self.player)

    async def test_pending_publication_only_reposts_after_complete_successful_history_scan(self):
        await self.bot.db.reserve_start_panel(1, 20)

        async def inaccessible(**kwargs):
            raise http_error(discord.Forbidden, 403)
            yield

        self.channel.history = inaccessible
        with self.assertRaises(AccessDenied):
            await self.publish()
        self.channel.send.assert_not_awaited()
        self.assertIsNone((await self.bot.db.start_panel(1))["message_id"])

        async def empty(**kwargs):
            if False:
                yield

        self.channel.history = empty
        await self.publish()
        self.channel.send.assert_awaited_once()

    async def test_http_failure_preserves_pending_intent_and_reports_retry(self):
        self.channel.send.side_effect = http_error(discord.HTTPException, 500)
        with self.assertRaisesRegex(ValueError, "Retry /start_panel"):
            await self.publish()
        row = await self.bot.db.start_panel(1)
        self.assertIsNone(row["message_id"])
        self.assertEqual(row["channel_id"], "20")

    async def test_deletion_during_update_is_clear_and_never_posts_untracked_duplicate(self):
        await self.publish()
        self.channel.send.return_value.edit.side_effect = http_error(discord.NotFound, 404)
        with self.assertRaisesRegex(ValueError, "deleted during"):
            await self.publish()
        self.assertEqual(self.channel.send.await_count, 1)
