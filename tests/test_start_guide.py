"""Discord-native onboarding contracts; no gateway or user credentials needed."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from scoutbot.access import AccessDenied
from scoutbot.bot import ScoutBot
from scoutbot.club_ui import club_help
from scoutbot.config import Settings
from scoutbot.domain import STAGES
from scoutbot.start_content import JOURNEYS, REFERENCE, ROUTES
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
