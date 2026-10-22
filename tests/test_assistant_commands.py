"""Offline command journeys, authorization and safe Discord continuation contracts."""

import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from scoutbot.access import AccessDenied, staff_assignee
from scoutbot.assistant_store import deadline
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
        self.assertEqual(count, 14)
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

    async def test_edit_transfers_departed_owner_and_edits_deadline_without_lookup(self):
        await self.bot.db.configure(1, 7, staff_role=99, staff_channel=2, recap_channel=3,
                                    source_kind="bot", source_id=4)
        coach = self.interaction(8, roles=(99,))
        self.guild.fetch_member.return_value = coach.user
        for kind, path, id_key, owner_key, date_key in (
            ("goal", "development edit", "goal_id", "responsible", "target_date"),
            ("task", "assistant task edit", "task_id", "owner", "due_date"),
        ):
            identifier = await self.bot.db.save_staff_record(
                1, 7, kind, 123, "Original work", deadline("2026-10-30", "20:00", "UTC"),
                "UTC", member=42, criteria="Five observed accurate passes")
            before = (await self.bot.db.staff_records(1, kind))[0]
            self.guild.fetch_member.reset_mock()
            # No lookup of the departed historical owner is needed for deadline-only edits.
            await self.invoke(path, coach, **{id_key: identifier, date_key: "2026-11-05",
                                             "time": "20:00", "timezone": "America/Chicago"})
            self.guild.fetch_member.assert_not_awaited()
            row = (await self.bot.db.staff_records(1, kind))[0]
            self.assertEqual((row["owner_id"], row["due_at"], row["timezone"]),
                             ("123", "2026-11-06T02:00:00+00:00", "America/Chicago"))
            await self.invoke(path, coach, **{id_key: identifier, owner_key: coach.user})
            self.guild.fetch_member.assert_awaited_once_with(8)
            row = (await self.bot.db.staff_records(1, kind))[0]
            self.assertEqual(row["owner_id"], "8")
            for key in ("id", "created_at", "created_by", "member_id", "description", "criteria",
                        "completed_at", "completed_by", "outcome"):
                self.assertEqual(row[key], before[key])
            await self.invoke(path, coach, **{
                id_key: identifier, owner_key: coach.user, date_key: "2026-11-06",
                "time": "21:00", "timezone": "UTC"})
            await self.bot.db.finish_staff_record(1, 8, kind, identifier, "Observed outcome")
            completed = await self.bot.db.staff_records(1, kind, "all")
            audits = await self.bot.db.all("SELECT * FROM audit_log")
            with self.assertRaisesRegex(ValueError, "Completed"):
                await self.invoke(path, coach, **{id_key: identifier, date_key: "2026-11-07",
                                                 "time": "21:00", "timezone": "UTC"})
            with self.assertRaisesRegex(ValueError, "Completed"):
                await self.invoke(path, coach, **{id_key: identifier, owner_key: coach.user})
            self.assertEqual(await self.bot.db.staff_records(1, kind, "all"), completed)
            self.assertEqual(await self.bot.db.all("SELECT * FROM audit_log"), audits)
        for call in coach.followup.send.call_args_list:
            self.assertTrue(call.kwargs["ephemeral"])
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(),
                             discord.AllowedMentions.none().to_dict())
        self.assertEqual(await self.bot.db.all("SELECT * FROM evaluations"), [])
        self.assertEqual(await self.bot.db.all("SELECT * FROM stage_roles"), [])

    async def test_edit_rejects_stale_ineligible_and_unavailable_assignees_atomically(self):
        selected = self.interaction(8, manager=True)
        foreign = self.interaction(9, manager=True)
        foreign.user.guild = SimpleNamespace(id=2)
        for kind, path, id_key, owner_key in (
            ("goal", "development edit", "goal_id", "responsible"),
            ("task", "assistant task edit", "task_id", "owner"),
        ):
            identifier = await self.bot.db.save_staff_record(
                1, 7, kind, 123, "Original work", deadline("2026-10-30", "20:00", "UTC"),
                "UTC", member=42, criteria="Observe five passes")
            before = await self.bot.db.staff_records(1, kind)
            audits = await self.bot.db.all("SELECT * FROM audit_log")
            self.guild.fetch_member.return_value = self.interaction(8).user
            with self.assertRaises(AccessDenied):
                await self.invoke(path, **{id_key: identifier, owner_key: selected.user})
            self.guild.fetch_member.assert_awaited_with(8)
            bot_member = self.interaction(8, manager=True).user
            bot_member.bot = True
            self.guild.fetch_member.return_value = bot_member
            with self.assertRaises(AccessDenied):
                await self.invoke(path, **{id_key: identifier, owner_key: selected.user})
            self.guild.fetch_member.reset_mock()
            with self.assertRaises(AccessDenied):
                await self.invoke(path, **{id_key: identifier, owner_key: foreign.user})
            self.guild.fetch_member.assert_not_awaited()
            for error in (discord.NotFound, discord.Forbidden):
                self.guild.fetch_member.side_effect = error(
                    SimpleNamespace(status=404 if error is discord.NotFound else 403, reason="Unavailable"),
                    "Member unavailable")
                with self.assertRaises(error):
                    await self.invoke(path, **{id_key: identifier, owner_key: selected.user})
            self.guild.fetch_member.side_effect = None
            self.assertEqual(await self.bot.db.staff_records(1, kind), before)
            self.assertEqual(await self.bot.db.all("SELECT * FROM audit_log"), audits)

    async def test_edit_input_permissions_and_server_isolation(self):
        for kind, path, id_key, owner_key, date_key in (
            ("goal", "development edit", "goal_id", "responsible", "target_date"),
            ("task", "assistant task edit", "task_id", "owner", "due_date"),
        ):
            identifier = await self.bot.db.save_staff_record(
                2, 7, kind, 7, "Other server", deadline("2026-10-30", "20:00", "UTC"),
                "UTC", member=42, criteria="Observe five passes")
            rows = await self.bot.db.all("SELECT * FROM staff_records")
            audits = await self.bot.db.all("SELECT * FROM audit_log")
            for values in ({}, {date_key: "2026-11-01"}, {"time": "20:00"},
                           {"timezone": "UTC"}, {date_key: "bad", "time": "20:00", "timezone": "UTC"},
                           {date_key: "2026-11-01", "time": "01:30", "timezone": "America/Chicago"}):
                with self.assertRaises(ValueError):
                    await self.invoke(path, **{id_key: identifier, **values})
            self.guild.fetch_member.assert_not_awaited()
            with self.assertRaisesRegex(ValueError, "this server"):
                await self.invoke(path, **{id_key: identifier, owner_key: self.staff.user})
            revoked = self.interaction(7)
            with self.assertRaises(AccessDenied):
                await self.invoke(path, revoked, **{id_key: identifier, owner_key: self.staff.user})
            self.assertEqual(await self.bot.db.all("SELECT * FROM staff_records"), rows)
            self.assertEqual(await self.bot.db.all("SELECT * FROM audit_log"), audits)
            self.guild.fetch_member.reset_mock()

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
