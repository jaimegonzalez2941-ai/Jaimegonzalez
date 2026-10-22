"""Offline command journeys, authorization and safe Discord continuation contracts."""

import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from scoutbot.access import AccessDenied, staff_assignee
from scoutbot.assistant_ui import StaffPages, pages
from scoutbot.bot import ScoutBot
from scoutbot.config import Settings


class AssistantCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = ScoutBot(Settings(Path(":memory:"), None, False, "INFO"))
        await self.bot.db.open()
        self.bot.register_commands()
        self.guild = SimpleNamespace(id=1, fetch_member=AsyncMock())
        self.staff = self.interaction(7, manager=True)
        self.regular = self.interaction(42)
        self.guild.fetch_member.return_value = self.staff.user

    async def asyncTearDown(self):
        await self.bot.close()

    def interaction(self, identifier, *, manager=False, roles=()):
        member = Mock(spec=discord.Member)
        member.id, member.bot, member.display_name = identifier, False, f"Member {identifier}"
        member.guild = self.guild
        member.roles = [SimpleNamespace(id=role) for role in roles]
        member.guild_permissions = discord.Permissions(manage_guild=manager)
        return SimpleNamespace(guild=self.guild, guild_id=self.guild.id, user=member,
                               client=self.bot, response=AsyncMock(), followup=AsyncMock())

    def command(self, path):
        current = self.bot.tree.get_command(path.split()[0])
        for name in path.split()[1:]:
            current = current.get_command(name)
        return current

    async def invoke(self, path, interaction=None, **kwargs):
        command = self.command(path)
        return await command.callback(command.parent, interaction or self.staff, **kwargs)

    async def test_every_new_command_fails_closed_for_players_and_dm(self):
        count = 0
        for command in self.bot.tree.walk_commands():
            if not isinstance(command, app_commands.Command):
                continue
            name = command.qualified_name
            if not (name.startswith(("assistant ", "development ", "scout shortlist ")) or
                    name in ("scout player", "scout compare")):
                continue
            args = {name: None for name, parameter in inspect.signature(command.callback).parameters.items()
                    if name not in ("self", "interaction") and parameter.default is inspect.Parameter.empty}
            with self.subTest(command=name):
                with self.assertRaises(AccessDenied):
                    await command.callback(command.parent, self.regular, **args)
                dm = self.interaction(42)
                dm.guild = None
                with self.assertRaises(AccessDenied):
                    await command.callback(command.parent, dm, **args)
            count += 1
        self.assertEqual(count, 12)
        self.assertEqual(await self.bot.db.all("SELECT * FROM staff_records"), [])

    async def test_nested_groups_have_staff_checks_and_serialize(self):
        for name in ("assistant", "assistant task", "development", "scout shortlist"):
            with self.subTest(group=name), self.assertRaises(AccessDenied):
                await self.command(name).interaction_check(self.regular)
        for command in self.bot.tree.get_commands():
            payload = command.to_dict(self.bot.tree)
            self.assertLessEqual(len(payload.get("options", [])), 25)
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.message_content)

    async def test_assignee_checks_fresh_single_member_and_configured_role(self):
        self.guild.fetch_member.return_value = self.regular.user
        with self.assertRaises(AccessDenied):
            await staff_assignee(self.staff, self.staff.user)  # selected staff is stale
        self.guild.fetch_member.assert_awaited_with(self.staff.user.id)
        await self.bot.db.configure(1, 7, staff_role=99, staff_channel=2, recap_channel=3,
                                    source_kind="bot", source_id=4)
        current = self.interaction(8, roles=(99,))
        self.guild.fetch_member.return_value = current.user
        self.assertIs(await staff_assignee(self.staff, current.user), current.user)
        current.user.bot = True
        with self.assertRaises(AccessDenied):
            await staff_assignee(self.staff, current.user)
        other = self.interaction(9, manager=True)
        other.user.guild = SimpleNamespace(id=2)
        with self.assertRaises(AccessDenied):
            await staff_assignee(self.staff, other.user)

    async def test_staff_workflow_and_safe_private_output(self):
        common = dict(time="20:00", timezone="America/Chicago")
        await self.invoke("scout shortlist add", candidate=self.regular.user, position="Defender",
                          responsible=self.staff.user, next_action="Observe **pressing** @everyone",
                          review_date="2026-10-30", **common)
        await self.invoke("scout shortlist add", candidate=self.regular.user, position="Defender",
                          responsible=self.staff.user, next_action="Updated observation plan",
                          review_date="2026-10-31", **common)
        row = (await self.bot.db.staff_records(1, "recruitment"))[0]
        self.assertEqual(len(await self.bot.db.staff_records(1, "recruitment")), 1)
        with self.assertRaises(ValueError):
            await self.invoke("scout shortlist remove", entry_id=row["id"])
        self.assertEqual(len(await self.bot.db.staff_records(1, "recruitment")), 1)
        await self.invoke("scout shortlist remove", entry_id=row["id"], confirm=True)
        await self.invoke("scout shortlist list", status="archived")
        await self.invoke("development goal", member=self.regular.user,
                          description="Pass out under pressure",
                          criteria="Observe five accurate passes @everyone **criteria**",
                          target_date="2026-10-30", **common)
        goal = (await self.bot.db.staff_records(1, "goal"))[0]
        await self.invoke("development complete", goal_id=goal["id"], outcome="Observed 4/5; follow up")
        await self.invoke("development complete", goal_id=goal["id"], outcome="Do not overwrite")
        await self.invoke("development review", status="completed")
        await self.invoke("assistant task add", owner=self.staff.user,
                          description="Review evidence @everyone **task**", due_date="2026-10-30", **common)
        task = (await self.bot.db.staff_records(1, "task"))[0]
        await self.invoke("assistant task list", status="active")
        await self.invoke("assistant task complete", task_id=task["id"], outcome="Reviewed all evidence")
        await self.invoke("assistant task complete", task_id=task["id"], outcome="Repeated completion")
        await self.invoke("assistant task list", status="completed")
        await self.invoke("scout player", member=self.regular.user)
        other = self.interaction(43)
        await self.invoke("scout compare", first=self.regular.user, second=other.user, position="Defender")
        await self.invoke("assistant brief")
        self.assertEqual((await self.bot.db.staff_records(1, "goal", "completed"))[0]["outcome"],
                         "Observed 4/5; follow up")
        self.assertEqual(len(await self.bot.db.all(
            "SELECT * FROM audit_log WHERE action='task.completed'")), 1)
        for call in self.staff.followup.send.call_args_list:
            self.assertTrue(call.kwargs["ephemeral"])
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(),
                             discord.AllowedMentions.none().to_dict())
            if "embed" in call.kwargs:
                content = call.kwargs["embed"].description
                self.assertNotIn("@everyone", content)
                self.assertNotIn("**criteria**", content)

    async def test_paginated_continuation_rechecks_permissions_owner_and_server(self):
        view = StaffPages(7, 1, pages("Private", ["x" * 7001]))
        self.assertTrue(view.previous.disabled)
        await view.move(self.staff, 1)
        self.assertEqual(view.index, 1)
        self.assertFalse(view.previous.disabled)
        kwargs = self.staff.response.edit_message.call_args.kwargs
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())
        with self.assertRaises(AccessDenied):
            await view.move(self.regular, 1)
        other_staff = self.interaction(8, manager=True)
        with self.assertRaises(AccessDenied):
            await view.move(other_staff, 1)
        self.staff.user.guild_permissions = discord.Permissions.none()
        with self.assertRaises(AccessDenied):
            await view.move(self.staff, 1)
        self.staff.user.guild_permissions = discord.Permissions(manage_guild=True)
        self.staff.guild = SimpleNamespace(id=2)
        with self.assertRaises(AccessDenied):
            await view.move(self.staff, 1)

    async def test_input_edge_cases_and_help_limits(self):
        self.regular.user.bot = True
        with self.assertRaises(ValueError):
            await self.invoke("scout player", member=self.regular.user)
        with self.assertRaises(ValueError):
            await self.invoke("scout compare", first=self.staff.user, second=self.staff.user,
                              position="Defender")
        await self.invoke("scout help")
        self.assertLessEqual(len(self.staff.response.send_message.call_args.kwargs["embed"]), 6000)
        self.assertIn("/assistant brief", self.staff.response.send_message.call_args.kwargs["embed"].fields[4].value)
