"""Discord-native onboarding contracts; no gateway or user credentials needed."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands

from scoutbot.access import AccessDenied
from scoutbot.bot import ScoutBot
from scoutbot.club_ui import club_help
from scoutbot.config import Settings
from scoutbot.domain import STAGES
from scoutbot.database import Database, GUIDE_RETENTION_SECONDS
from scoutbot.start_content import CONTENT_VERSION, JOURNEYS, REFERENCE, ROUTES
from scoutbot.start_guide import StartGuide, audience, manager_steps, reference_steps


def contents(embed):
    return embed.title + "\n" + (embed.description or "") + "\n" + "\n".join(
        field.name + ": " + field.value for field in embed.fields)


class StartGuideTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = ScoutBot(Settings(Path(":memory:"), None, False, "INFO"))
        await self.bot.db.open()
        self.bot.register_commands()
        self.roles, self.channels = {}, {}
        self.guild = SimpleNamespace(id=1, get_role=self.roles.get, get_channel=self.channels.get)
        self.player = self.interaction(42)
        self.staff = self.interaction(43, staff=True)
        self.manager = self.interaction(44, manager=True)

    async def asyncTearDown(self):
        await self.bot.close()

    def interaction(self, identifier, *, staff=False, manager=False, guild=None):
        user = Mock(spec=discord.Member)
        user.id, user.bot, user.display_name = identifier, False, "Private @everyone"
        user.guild_permissions = discord.Permissions(manage_guild=manager)
        user.roles = [SimpleNamespace(id=10)] if staff else []
        guild = guild or self.guild
        return SimpleNamespace(
            guild=guild, guild_id=guild.id, user=user, client=self.bot,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(),
                                     is_done=Mock(return_value=True)),
            edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))

    async def configure(self, *, stages=False):
        await self.bot.db.configure(
            1, 44, staff_role=10, staff_channel=20, recap_channel=21,
            source_kind="bot", source_id=200)
        self.roles[10] = Mock(spec=discord.Role)
        for identifier in (20, 21):
            self.channels[identifier] = Mock(spec=discord.TextChannel)
        if stages:
            for index, stage in enumerate(STAGES, 11):
                self.roles[index] = Mock(spec=discord.Role)
                await self.bot.db.set_stage_role(1, 44, stage, index)

    async def opened(self, interaction):
        await club_help.callback(interaction)
        sent = interaction.followup.send.call_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertEqual(sent["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())
        return sent["view"], sent["embed"]

    def assert_card(self, embed):
        self.assertLessEqual(len(embed), 2200)  # Compact enough for a phone, below Discord's 6000 limit.
        self.assertLessEqual(len(embed.title), 256)
        self.assertLessEqual(len(embed.description or ""), 1000)
        for field in embed.fields:
            self.assertLessEqual(len(field.value), 1024)
        self.assertNotIn("@everyone", contents(embed))
        self.assertNotIn("<@", contents(embed))

    async def test_any_server_member_can_open_short_private_home(self):
        view, embed = await self.opened(self.player)
        self.assertIn("Start Here", embed.title)
        self.assertIn("New player", embed.description)
        self.assertLess(len(embed.description), 450)
        self.assertNotIn("/scout setup", embed.description)
        self.assertNotIn("/schedule", embed.description)
        self.assertEqual([option.value for option in view.menu.options], ["player", "reference"])
        self.player.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertTrue(view.back.disabled)
        self.assertTrue(view.home.disabled)
        self.assertTrue(view.next.disabled)
        self.assert_card(embed)
        self.assertEqual(view.timeout, 600)
        self.assertFalse(view.is_persistent())

    async def test_staff_manager_and_unconfigured_setup_recommendations(self):
        await self.configure()
        staff_view, staff_card = await self.opened(self.staff)
        self.assertIn("Staff workspace", staff_card.description)
        self.assertEqual(len(staff_view.menu.options), 5)
        self.assertNotIn("manager", [o.value for o in staff_view.menu.options])
        await self.bot.db.configure(
            2, 44, staff_role=70, staff_channel=80, recap_channel=81,
            source_kind="bot", source_id=200)
        manager_view, _ = await self.opened(self.manager)
        self.assertEqual(len(manager_view.menu.options), 6)
        new_manager = self.interaction(55, manager=True,
            guild=SimpleNamespace(id=3, get_role=lambda _: None, get_channel=lambda _: None))
        _, card = await self.opened(new_manager)
        self.assertIn("Manager setup", card.description)

    async def test_buttons_and_select_follow_the_complete_player_workflow(self):
        view, _ = await self.opened(self.player)
        view.menu._values = ["player"]
        await view.menu.callback(self.player)
        self.assertEqual((view.route, view.index), ("player", 0))
        self.assertIn("/availability set", contents(self.player.edit_original_response.call_args.kwargs["embed"]))
        await view.next.callback(self.player)
        self.assertIn("/schedule rsvp", contents(self.player.edit_original_response.call_args.kwargs["embed"]))
        await view.back.callback(self.player)
        self.assertEqual(view.index, 0)
        for _ in range(3):
            await view.next.callback(self.player)
        self.assertIn("Approved leave", contents(self.player.edit_original_response.call_args.kwargs["embed"]))
        self.assertTrue(view.next.disabled)
        self.assertEqual(view.index, 3)
        await view.home.callback(self.player)
        self.assertIsNone(view.route)
        self.assertTrue(view.back.disabled)
        self.assertTrue(view.next.disabled)
        view.menu._values = ["player"]
        await view.menu.callback(self.player)
        await view.back.callback(self.player)
        self.assertIsNone(view.route)

    async def test_all_staff_journeys_and_reference_fit_discord_components(self):
        await self.configure(stages=True)
        view, _ = await self.opened(self.manager)
        for route in ROUTES:
            await view.navigate(self.manager, "choose", selected=route)
            seen = 0
            while True:
                embed = self.manager.edit_original_response.call_args.kwargs["embed"]
                self.assert_card(embed)
                self.assertEqual(self.manager.edit_original_response.call_args.kwargs["allowed_mentions"].to_dict(),
                                 discord.AllowedMentions.none().to_dict())
                components = view.to_components()
                self.assertLessEqual(len(components), 5)
                for option in view.menu.options:
                    self.assertLessEqual(len(option.label), 100)
                    self.assertLessEqual(len(option.description), 100)
                seen += 1
                if view.next.disabled:
                    break
                await view.next.callback(self.manager)
            expected = (5 if route == "manager" else len(REFERENCE) if route == "reference"
                        else len(JOURNEYS[route]))
            self.assertEqual(seen, expected)

    async def test_regular_players_cannot_open_forged_staff_or_manager_routes(self):
        view, _ = await self.opened(self.player)
        for route in ("session", "scouting", "workspace", "manager"):
            with self.assertRaises(AccessDenied):
                await view.navigate(self.player, "choose", selected=route)
            self.assertIsNone(view.route)
        self.player.edit_original_response.assert_not_awaited()
        await view.navigate(self.player, "choose", selected="reference")
        self.assertEqual(len(reference_steps(await audience(self.player))), 2)
        self.assertNotIn("/assistant", contents(self.player.edit_original_response.call_args.kwargs["embed"]))

    async def test_button_owner_server_and_dm_boundaries(self):
        view, _ = await self.opened(self.player)
        stranger = self.interaction(99)
        foreign = self.interaction(42, guild=SimpleNamespace(id=2))
        dm = self.interaction(42)
        dm.guild = None
        for interaction in (stranger, foreign, dm):
            with self.assertRaises(AccessDenied):
                await view.home.callback(interaction)
            interaction.response.defer.assert_not_awaited()
            interaction.edit_original_response.assert_not_awaited()

    async def test_role_revocation_and_staff_configuration_changes_rechecked(self):
        await self.configure()
        view, _ = await self.opened(self.staff)
        await view.navigate(self.staff, "choose", selected="scouting")
        self.staff.user.roles = []
        with self.assertRaises(AccessDenied):
            await view.next.callback(self.staff)
        self.assertEqual(view.index, 0)
        await view.home.callback(self.staff)  # Revoked staff can still get safely back to public guidance.
        self.assertEqual([o.value for o in view.menu.options], ["player", "reference"])
        self.staff.user.roles = [SimpleNamespace(id=10)]
        await view.navigate(self.staff, "choose", selected="workspace")
        await self.bot.db.configure(
            1, 44, staff_role=11, staff_channel=20, recap_channel=21,
            source_kind="bot", source_id=200)
        with self.assertRaises(AccessDenied):
            await view.next.callback(self.staff)

    async def test_manager_permission_revocation_rechecked_on_every_continuation(self):
        await self.configure()
        view, _ = await self.opened(self.manager)
        await view.navigate(self.manager, "choose", selected="manager")
        self.manager.user.guild_permissions = discord.Permissions.none()
        with self.assertRaises(AccessDenied):
            await view.next.callback(self.manager)
        await view.home.callback(self.manager)
        self.assertNotIn("manager", [o.value for o in view.menu.options])

    async def test_manager_checklist_missing_partial_complete_and_stale_resource(self):
        view, _ = await self.opened(self.manager)
        await view.navigate(self.manager, "choose", selected="manager")
        text = contents(self.manager.edit_original_response.call_args.kwargs["embed"])
        self.assertIn("Not configured", text)
        self.assertIn("`/scout setup`", text)
        await self.configure()
        await self.bot.db.set_stage_role(1, 44, STAGES[0], 11)
        self.roles[11] = Mock(spec=discord.Role)
        await view.navigate(self.manager, "choose", selected="manager")
        text = contents(self.manager.edit_original_response.call_args.kwargs["embed"])
        self.assertIn("Next setup action: `/scout stage_role`", text)
        self.assertIn("not a live privacy/permission test", text)
        await view.next.callback(self.manager)
        self.assertIn("Next missing mapping: Academia Prospect", contents(
            self.manager.edit_original_response.call_args.kwargs["embed"]))
        await self.configure(stages=True)
        await view.navigate(self.manager, "choose", selected="manager")
        self.assertIn("Next setup action: `/assistant brief`", contents(
            self.manager.edit_original_response.call_args.kwargs["embed"]))
        del self.channels[20]
        await view.navigate(self.manager, "choose", selected="manager")
        text = contents(self.manager.edit_original_response.call_args.kwargs["embed"])
        self.assertIn("Staff text channel: not found", text)
        self.assertIn("Next setup action: `/scout setup`", text)

    async def test_manager_policy_notification_status_refresh_and_server_isolation(self):
        await self.configure(stages=True)
        await self.bot.db.set_policy(1, 44, 45, 7, 75)
        await self.bot.db.set_policy(2, 44, 20, 9, 99)
        access = await audience(self.manager)
        steps = await manager_steps(self.manager, access)
        self.assertIn("75% over 45 days", steps[2].action)
        self.assertNotIn("99%", steps[2].action)
        self.assertIn("Optional reminders: off", steps[3].action)
        await self.bot.db.set_notification_settings(1, 44, 33, 360)
        steps = await manager_steps(self.manager, await audience(self.manager))
        self.assertIn("6 hour(s)", steps[3].action)
        self.assertIn("not proof of channel access", steps[3].action)

    async def test_navigation_only_reads_data_and_never_exposes_saved_notes(self):
        await self.configure(stages=True)
        await self.bot.db.evaluate(1, 42, "PRIVATE_PLAYER", 44, "PRIVATE_STAFF",
                                   "Midfielder", 8.5, "SECRET_OBSERVATION")
        before = await self.bot.db.all("SELECT * FROM audit_log")
        view, _ = await self.opened(self.manager)
        for route in ROUTES:
            await view.navigate(self.manager, "choose", selected=route)
            while True:
                text = contents(self.manager.edit_original_response.call_args.kwargs["embed"])
                for secret in ("PRIVATE_PLAYER", "PRIVATE_STAFF", "SECRET_OBSERVATION"):
                    self.assertNotIn(secret, text)
                if view.next.disabled:
                    break
                await view.next.callback(self.manager)
        self.assertEqual(await self.bot.db.all("SELECT * FROM audit_log"), before)
        self.assertEqual(len(await self.bot.db.history(1, 42)), 1)

    async def test_invalid_stale_expired_navigation_is_explicit(self):
        view, _ = await self.opened(self.player)
        with self.assertRaises(ValueError):
            await view.next.callback(self.player)
        with self.assertRaises(ValueError):
            await view.navigate(self.player, "choose", selected="unknown")
        with self.assertRaises(ValueError):
            await view.render(self.player, "player", 500)
        with self.assertRaises(ValueError):
            await view.render(self.player, "unknown", 0)
        view.menu._values = []
        with self.assertRaises(ValueError):
            await view.menu.callback(self.player)
        view.expires_at = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            await view.home.callback(self.player)
        await view.on_timeout()
        self.assertTrue(all(child.disabled for child in view.children))
        self.assertIn("10 minutes", (await self.opened(self.player))[1].footer.text)

    async def test_reference_authorization_changes_do_not_reveal_old_staff_pages(self):
        await self.configure()
        view, _ = await self.opened(self.staff)
        await view.navigate(self.staff, "choose", selected="reference")
        for _ in range(4):
            await view.next.callback(self.staff)
        self.staff.user.roles = []
        with self.assertRaises(ValueError):
            await view.next.callback(self.staff)
        await view.home.callback(self.staff)
        await view.navigate(self.staff, "choose", selected="reference")
        self.assertEqual(len(reference_steps(await audience(self.staff))), 2)

    async def checkpoint(self, interaction=None):
        interaction = interaction or self.player
        return await self.bot.db.guide_checkpoint(interaction.guild.id, interaction.user.id)

    async def test_timeout_reopening_requires_explicit_resume_or_start_over(self):
        view, _ = await self.opened(self.player)
        await view.navigate(self.player, "choose", selected="player")
        await view.next.callback(self.player)
        await view.on_timeout()
        with self.assertRaisesRegex(ValueError, "expired"):
            await view.next.callback(self.player)
        reopened, embed = await self.opened(self.player)
        self.assertIn("Resume", embed.title)
        self.assertEqual([item.label for item in reopened.children], ["Resume", "Start over"])
        self.assertIsNone(reopened.route)  # Reopening is not implicit consent to resume.
        self.assertEqual((await self.checkpoint())["step"], 1)
        await reopened.resume.callback(self.player)
        self.assertEqual((reopened.route, reopened.index), ("player", 1))
        self.assertIn("/schedule list", contents(
            self.player.edit_original_response.call_args.kwargs["embed"]))
        self.assertEqual(len(reopened.children), 4)
        again, _ = await self.opened(self.player)
        await again.start_over.callback(self.player)
        self.assertIsNone(again.route)
        self.assertIsNone(await self.checkpoint())
        self.assertIn("Start Here", self.player.edit_original_response.call_args.kwargs["embed"].title)
        with self.assertRaisesRegex(ValueError, "already used"):
            await again.resume.callback(self.player)
        home, embed = await self.opened(self.player)
        self.assertNotIn("Resume", embed.title)
        await home.navigate(self.player, "choose", selected="player")
        await home.home.callback(self.player)
        self.assertIsNone(await self.checkpoint())

    async def test_resume_choice_checks_owner_server_dm_and_expiry(self):
        view, _ = await self.opened(self.player)
        await view.navigate(self.player, "choose", selected="player")
        prompt, _ = await self.opened(self.player)
        foreign = self.interaction(42, guild=SimpleNamespace(id=2))
        dm = self.interaction(42)
        dm.guild = None
        for wrong in (self.interaction(99), foreign, dm):
            for button in (prompt.resume, prompt.start_over):
                with self.assertRaises(AccessDenied):
                    await button.callback(wrong)
            wrong.response.defer.assert_not_awaited()
        self.assertIsNotNone(await self.checkpoint())
        await prompt.on_timeout()
        with self.assertRaisesRegex(ValueError, "expired"):
            await prompt.resume.callback(self.player)

    async def test_resume_isolated_by_both_viewer_and_server(self):
        view, _ = await self.opened(self.player)
        await view.navigate(self.player, "choose", selected="player")
        for other in (self.interaction(99), self.interaction(42, guild=SimpleNamespace(id=2))):
            other_view, embed = await self.opened(other)
            self.assertNotIn("Resume", embed.title)
            await other_view.navigate(other, "choose", selected="reference")
            self.assertEqual((await self.checkpoint(other))["route"], "reference")
        self.assertEqual((await self.checkpoint())["route"], "player")
        prompt, _ = await self.opened(self.player)
        await prompt.start_over.callback(self.player)
        self.assertEqual(len(await self.bot.db.all("SELECT * FROM guide_checkpoints")), 2)

    async def test_resume_obsolete_removed_and_invalid_positions_fall_back_home(self):
        for route, step, version in (
            ("player", 1, "obsolete"), ("removed", 0, CONTENT_VERSION),
            ("player", 99, CONTENT_VERSION),
        ):
            await self.bot.db.save_guide_checkpoint(1, 42, route, step, version)
            view, _ = await self.opened(self.player)
            await view.resume.callback(self.player)
            self.assertIsNone(view.route)
            self.assertIsNone(await self.checkpoint())
            self.assertEqual([o.value for o in view.menu.options], ["player", "reference"])

    async def test_resume_rechecks_permissions_even_after_choice_was_opened(self):
        await self.configure()
        for interaction, route, revoke in (
            (self.staff, "scouting", lambda: setattr(self.staff.user, "roles", [])),
            (self.manager, "manager", lambda: setattr(
                self.manager.user, "guild_permissions", discord.Permissions.none())),
        ):
            view, _ = await self.opened(interaction)
            await view.navigate(interaction, "choose", selected=route)
            prompt, _ = await self.opened(interaction)
            revoke()
            await prompt.resume.callback(interaction)
            self.assertIsNone(prompt.route)
            self.assertIsNone(await self.checkpoint(interaction))
            self.assertNotIn("/scout setup", contents(
                interaction.edit_original_response.call_args.kwargs["embed"]))
        # Reference is member-visible but its staff-only steps must not resume.
        self.staff.user.roles = [SimpleNamespace(id=10)]
        view, _ = await self.opened(self.staff)
        await view.navigate(self.staff, "choose", selected="reference")
        await view.next.callback(self.staff)
        await view.next.callback(self.staff)
        prompt, _ = await self.opened(self.staff)
        await self.bot.db.configure(
            1, 44, staff_role=11, staff_channel=20, recap_channel=21,
            source_kind="bot", source_id=200)
        await prompt.resume.callback(self.staff)
        self.assertIsNone(prompt.route)
        self.assertIsNone(await self.checkpoint(self.staff))

    async def test_concurrent_menus_share_latest_successful_position_without_history(self):
        first, _ = await self.opened(self.player)
        second, _ = await self.opened(self.player)
        await first.navigate(self.player, "choose", selected="player")
        choice, _ = await self.opened(self.player)
        other_interaction = self.interaction(42)
        await asyncio.gather(
            first.next.callback(self.player),
            second.navigate(other_interaction, "choose", selected="reference"))
        saved = await self.checkpoint()
        await choice.resume.callback(self.player)
        self.assertEqual((choice.route, choice.index), (saved["route"], saved["step"]))
        self.assertEqual(len(await self.bot.db.all("SELECT * FROM guide_checkpoints")), 1)
        await choice.home.callback(self.player)
        await second.home.callback(other_interaction)
        # A choice opened before another menu cleared the checkpoint is safe.
        await second.navigate(other_interaction, "choose", selected="player")
        pending, _ = await self.opened(self.player)
        await second.home.callback(other_interaction)
        await pending.resume.callback(self.player)
        self.assertIsNone(pending.route)
        self.assertIsNone(await self.checkpoint())

    async def test_slow_navigation_does_not_block_an_unrelated_viewer(self):
        blocked = StartGuide(self.player.user.id, self.player.guild.id)
        unrelated_interaction = self.interaction(45)
        unrelated = StartGuide(unrelated_interaction.user.id, unrelated_interaction.guild.id)
        edit_started, release_edit = asyncio.Event(), asyncio.Event()

        async def slow_edit(**kwargs):
            edit_started.set()
            await release_edit.wait()

        self.player.edit_original_response.side_effect = slow_edit
        blocked_task = asyncio.create_task(
            blocked.navigate(self.player, "choose", selected="player"))
        try:
            await asyncio.wait_for(edit_started.wait(), timeout=1)
            await asyncio.wait_for(
                unrelated.navigate(unrelated_interaction, "choose", selected="reference"),
                timeout=1)
            self.assertEqual((await self.checkpoint(unrelated_interaction))["route"], "reference")
        finally:
            release_edit.set()
            await blocked_task

        self.assertEqual((await self.checkpoint())["route"], "player")
        self.assertEqual(self.bot.db._guide_locks, {})

    async def test_same_viewer_menus_serialize_publication_and_checkpoint(self):
        first = StartGuide(self.player.user.id, self.player.guild.id)
        second_interaction = self.interaction(self.player.user.id)
        second = StartGuide(second_interaction.user.id, second_interaction.guild.id)
        edit_started, release_edit, second_deferred = (
            asyncio.Event(), asyncio.Event(), asyncio.Event())

        async def slow_edit(**kwargs):
            edit_started.set()
            await release_edit.wait()

        self.player.edit_original_response.side_effect = slow_edit
        second_interaction.response.defer.side_effect = second_deferred.set
        first_task = asyncio.create_task(first.navigate(self.player, "choose", selected="player"))
        try:
            await asyncio.wait_for(edit_started.wait(), timeout=1)
            second_task = asyncio.create_task(
                second.navigate(second_interaction, "choose", selected="reference"))
            await asyncio.wait_for(second_deferred.wait(), timeout=1)
            await asyncio.sleep(0)
            self.assertFalse(second_task.done())
            second_interaction.edit_original_response.assert_not_awaited()
        finally:
            release_edit.set()
            await first_task
        await second_task

        checkpoint = await self.checkpoint()
        self.assertEqual((checkpoint["route"], checkpoint["step"]), ("reference", 0))
        self.assertEqual((first.route, second.route), ("player", "reference"))
        self.assertEqual(self.bot.db._guide_locks, {})

    async def test_failed_discord_navigation_does_not_replace_checkpoint(self):
        view, _ = await self.opened(self.player)
        await view.navigate(self.player, "choose", selected="player")
        before = await self.checkpoint()
        self.player.edit_original_response.side_effect = RuntimeError("Discord unavailable")
        with self.assertRaises(RuntimeError):
            await view.next.callback(self.player)
        self.assertEqual(await self.checkpoint(), before)
        self.assertEqual(view.index, 0)
        prompt, _ = await self.opened(self.player)
        with self.assertRaises(RuntimeError):
            await prompt.resume.callback(self.player)
        self.assertTrue(prompt.resume_pending)
        self.assertEqual([item.label for item in prompt.children], ["Resume", "Start over"])
        self.assertEqual(await self.checkpoint(), before)
        self.player.edit_original_response.side_effect = None
        await prompt.resume.callback(self.player)
        self.assertEqual((prompt.route, prompt.index), ("player", 0))

    async def test_content_change_in_active_menu_cannot_write_old_step_as_new(self):
        view, _ = await self.opened(self.player)
        await view.navigate(self.player, "choose", selected="player")
        with patch("scoutbot.start_guide.CONTENT_VERSION", "new"):
            await view.next.callback(self.player)
        self.assertIsNone(view.route)
        self.assertIsNone(await self.checkpoint())

    async def test_navigation_storage_is_minimal_and_resume_does_not_mutate_club_records(self):
        await self.configure(stages=True)
        before = await self.bot.db.all("SELECT * FROM audit_log")
        view, _ = await self.opened(self.manager)
        for route in ROUTES:
            await view.navigate(self.manager, "choose", selected=route)
            reopened, _ = await self.opened(self.manager)
            await reopened.resume.callback(self.manager)
        row = await self.checkpoint(self.manager)
        self.assertEqual(set(row), {
            "guild_id", "viewer_id", "route", "step", "content_version", "updated_at"})
        self.assertEqual(await self.bot.db.all("SELECT * FROM audit_log"), before)
        for table in ("evaluations", "evaluation_drafts", "club_events", "match_stats"):
            self.assertEqual(await self.bot.db.all(f"SELECT * FROM {table}"), [])

    async def test_restart_and_bounded_retention_cleanup(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bot.db"
            original = self.bot.db
            self.bot.db = Database(path)
            await self.bot.db.open()
            try:
                with patch("scoutbot.database.time.time", return_value=2_000_000_000):
                    view, _ = await self.opened(self.player)
                    await view.navigate(self.player, "choose", selected="player")
                    await view.next.callback(self.player)
                    # Recreate the repository and all views like a process restart.
                    await self.bot.db.close()
                    self.bot.db = Database(path)
                    await self.bot.db.open()
                    resumed, _ = await self.opened(self.player)
                    await resumed.resume.callback(self.player)
                    self.assertEqual((resumed.route, resumed.index), ("player", 1))
                    await self.bot.db.save_guide_checkpoint(2, 99, "player", 0, CONTENT_VERSION)
                with patch("scoutbot.database.time.time",
                           return_value=2_000_000_000 + GUIDE_RETENTION_SECONDS - 1):
                    prompt, _ = await self.opened(self.player)
                    self.assertTrue(prompt.resume_pending)
                    # Opening the prompt does not renew retention.
                    self.assertEqual((await self.checkpoint())["updated_at"], 2_000_000_000)
                with patch("scoutbot.database.time.time",
                           return_value=2_000_000_000 + GUIDE_RETENTION_SECONDS):
                    await prompt.resume.callback(self.player)
                    self.assertIsNone(prompt.route)
                    self.assertEqual(await self.bot.db.all("SELECT * FROM guide_checkpoints"), [])
                    # Also test startup cleanup without first reading the checkpoint.
                    await self.bot.db.save_guide_checkpoint(1, 42, "player", 0, CONTENT_VERSION)
                await self.bot.db.close()
                self.bot.db = Database(path)
                with patch("scoutbot.database.time.time",
                           return_value=2_000_000_000 + 2 * GUIDE_RETENTION_SECONDS):
                    await self.bot.db.open()
                self.assertEqual(await self.bot.db.all("SELECT * FROM guide_checkpoints"), [])
            finally:
                await self.bot.db.close()
                self.bot.db = original

    async def test_every_guided_command_exists_and_reference_covers_all_commands(self):
        def leaves(command, prefix=""):
            path = prefix + "/" + command.name if not prefix else prefix + " " + command.name
            if isinstance(command, app_commands.Group):
                return set().union(*(leaves(child, path) for child in command.commands))
            return {path}
        actual = set().union(*(leaves(command) for command in self.bot.tree.get_commands()))
        reference = {command for _, _, entries in REFERENCE for command, _ in entries}
        self.assertEqual(actual - {"/club"}, reference)
        access = await audience(self.manager)
        steps = [step for journey in JOURNEYS.values() for step in journey]
        steps += list(await manager_steps(self.manager, access))
        for step in steps:
            self.assertIn(step.command, actual)
            for command, _ in step.related:
                self.assertIn(command, actual)
        payload = self.bot.tree.get_command("club").to_dict(self.bot.tree)
        self.assertEqual(payload["name"], "club")
        self.assertFalse(payload.get("default_member_permissions"))
        self.assertFalse(payload.get("options"))  # /club stays a root command, not a new command group.
