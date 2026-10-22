"""Club operations and Discord command contracts without a live Discord server."""

import asyncio
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from scoutbot.access import AccessDenied
from scoutbot.availability_commands import AvailabilityCommands
from scoutbot.attendance_commands import AttendanceCommands, PromotionCommands
from scoutbot.bot import ScoutBot
from scoutbot.branding import apply_branding
from scoutbot.club_domain import (
    leave_times, local_datetime, readiness, session_times, stamp, utc_now,
)
from scoutbot.config import Settings
from scoutbot.commands import ScoutCommands
from scoutbot.database import Database
from scoutbot.schedule_commands import ScheduleCommands


class TimeRuleTests(unittest.TestCase):
    def test_timezone_conversion_and_daylight_saving(self):
        self.assertEqual(local_datetime("2026-10-24", "20:30", "America/Chicago").hour, 1)
        for day, time in (("2026-11-01", "01:30"), ("2026-03-08", "02:30")):
            with self.assertRaises(ValueError):
                local_datetime(day, time, "America/Chicago")
        with self.assertRaises(ValueError):
            local_datetime("2026-10-24", "20:30", "Not/AZone")

    def test_inclusive_leave_dates_and_invalid_ranges(self):
        now = local_datetime("2026-10-21", "00:00", "UTC")
        start, end = leave_times("2026-10-24", "2026-10-24", "America/Chicago", now=now)
        self.assertEqual(local_datetime("2026-10-25", "00:00", "America/Chicago").isoformat(timespec="seconds"), end)
        self.assertLess(start, end)
        for first, last in (("2026-10-25", "2026-10-24"), ("2020-01-01", "2020-01-01")):
            with self.assertRaises(ValueError):
                leave_times(first, last, "UTC", now=now)

    def test_future_times_and_duration(self):
        now = local_datetime("2026-10-21", "00:00", "UTC")
        start, end = session_times("2026-10-22", "20:00", "UTC", 90, now=now)
        self.assertGreater(end, start)
        for day, duration in (("2020-01-01", 90), ("2026-10-22", 0), ("2030-01-01", 90)):
            with self.assertRaises(ValueError):
                session_times(day, "20:00", "UTC", duration, now=now)

    def test_unrounded_attendance_and_score_thresholds(self):
        policy = dict(days=30, minimum=5, percent=80)
        summary = dict(eligible=5, attended=4, rate=80)
        self.assertTrue(readiness(summary, policy, {"score": 8})[0])
        self.assertFalse(readiness(summary, policy, {"score": 7.9})[0])
        self.assertFalse(readiness(summary, policy, None)[0])
        self.assertFalse(readiness(dict(eligible=999, attended=799, rate=79.97998), policy, {"score": 8.5})[0])


class ClubRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "club.db"
        self.db = Database(self.path)
        await self.db.open()
        self.now = utc_now()

    async def asyncTearDown(self):
        await self.db.close()
        self.temp.cleanup()

    async def session(self, *, guild=1, member=42, days=None, status=None, response="yes"):
        start = self.now + timedelta(days=2)
        identifier = await self.db.create_event(guild, 7, "Training", "training", stamp(start),
                                                 stamp(start + timedelta(hours=1)), "UTC")
        await self.db.rsvp(guild, member, identifier, response)
        if days is not None:
            start = self.now - timedelta(days=days)
            async with self.db.transaction() as conn:
                await conn.execute("UPDATE club_events SET starts_at=?,ends_at=? WHERE id=?",
                                   (stamp(start), stamp(start + timedelta(hours=1)), identifier))
        if status:
            await self.db.mark_attendance(guild, 7, identifier, member, status)
        return identifier

    async def test_availability_replaces_and_is_tenant_scoped(self):
        await self.db.set_availability(1, 42, 0, "20:00", "24:00", "America/Chicago")
        await self.db.set_availability(1, 42, 0, "19:00", "21:00", "UTC")
        await self.db.set_availability(2, 42, 0, "12:00", "14:00", "UTC")
        rows = await self.db.all("SELECT * FROM availability_windows WHERE guild_id='1'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["start_min"], 19 * 60)
        await self.db.remove_availability(1, 42, 0)
        self.assertEqual(len(await self.db.all("SELECT * FROM availability_windows")), 1)
        with self.assertRaises(ValueError):
            await self.db.set_availability(1, 42, 0, "22:00", "02:00", "UTC")

    async def test_complete_attendance_denominator(self):
        for day, status in enumerate(("present", "present", "present", "late", "absent", "excused", None), 1):
            await self.session(days=day, status=status)
        on_leave = await self.session(days=8, status="absent")
        row = await self.db.event(1, on_leave)
        leave = await self.db.request_leave(1, 42, row["starts_at"], row["ends_at"], "UTC")
        await self.db.review_leave(1, 7, leave, True)
        cancelled = await self.session(days=9, status="absent")
        await self.db.cancel_event(1, 7, cancelled)
        await self.session(days=10, response="no")
        await self.session(days=40, status="absent")
        await self.session(guild=2, days=1, status="absent")
        summary = await self.db.attendance_summary(1, 42, 30, now=self.now)
        self.assertEqual((summary["eligible"], summary["attended"], summary["rate"]), (5, 4, 80))
        self.assertEqual((summary["excused"], summary["leave"], summary["unmarked"]), (1, 1, 1))
        self.assertEqual(summary["late"], 1)
        no_data = await self.db.attendance_summary(1, 999, 30)
        self.assertIsNone(no_data["rate"])

    async def test_unmarked_not_absent_and_correction_is_idempotent(self):
        identifier = await self.session(days=2)
        self.assertEqual((await self.db.attendance_summary(1, 42, 30))["eligible"], 0)
        await asyncio.gather(*(self.db.mark_attendance(1, 7, identifier, 42, "present") for _ in range(3)))
        summary = await self.db.attendance_summary(1, 42, 30)
        self.assertEqual(summary["eligible"], 1)
        await self.db.mark_attendance(1, 7, identifier, 42, "excused")
        self.assertEqual((await self.db.attendance_summary(1, 42, 30))["eligible"], 0)
        await self.db.mark_attendance(1, 7, identifier, 42, "unmarked")
        self.assertEqual((await self.db.attendance_summary(1, 42, 30))["unmarked"], 1)

    async def test_assignment_cannot_be_evaded_by_acknowledging(self):
        identifier = await self.session(response="maybe")
        await self.db.rsvp(1, 42, identifier, "assigned", actor=7)
        await self.db.rsvp(1, 42, identifier, "yes")
        with self.assertRaises(ValueError):
            await self.db.rsvp(1, 42, identifier, "no")
        await self.db.rsvp(1, 42, identifier, "pending", actor=7)
        await self.db.rsvp(1, 42, identifier, "no")

    async def test_edits_reconfirm_and_past_or_cancelled_sessions_lock(self):
        identifier = await self.session()
        event = await self.db.event(1, identifier)
        changed = await self.db.edit_event(1, 7, identifier, "New title", event["starts_at"], event["ends_at"], "UTC")
        self.assertFalse(changed)
        new_start = self.now + timedelta(days=3)
        changed = await self.db.edit_event(1, 7, identifier, "Moved", stamp(new_start),
                                          stamp(new_start + timedelta(hours=1)), "UTC")
        self.assertTrue(changed)
        row = await self.db.one("SELECT response FROM club_roster WHERE event_id=?", (identifier,))
        self.assertEqual(row["response"], "pending")
        await self.db.cancel_event(1, 7, identifier)
        with self.assertRaises(ValueError):
            await self.db.rsvp(1, 42, identifier, "yes")
        past = await self.session(days=2, status="present")
        with self.assertRaises(ValueError):
            await self.db.rsvp(1, 42, past, "no")
        with self.assertRaises(ValueError):
            await self.db.edit_event(1, 7, past, "Moved", stamp(new_start),
                                     stamp(new_start + timedelta(hours=1)), "UTC")

    async def test_leave_approval_revocation_and_isolation(self):
        identifier = await self.session(days=1, status="absent")
        row = await self.db.event(1, identifier)
        leave = await self.db.request_leave(1, 42, row["starts_at"], row["ends_at"], "UTC")
        self.assertEqual((await self.db.attendance_summary(1, 42, 30))["absent"], 1)
        with self.assertRaises(ValueError):
            await self.db.review_leave(2, 7, leave, True)
        await self.db.review_leave(1, 7, leave, True)
        self.assertEqual((await self.db.attendance_summary(1, 42, 30))["eligible"], 0)
        with self.assertRaises(ValueError):
            await self.db.cancel_leave(1, 42, leave)
        await self.db.review_leave(1, 7, leave, False)
        self.assertEqual((await self.db.attendance_summary(1, 42, 30))["absent"], 1)
        future = await self.db.request_leave(1, 42, stamp(self.now + timedelta(days=2)),
                                             stamp(self.now + timedelta(days=4)), "UTC")
        with self.assertRaises(ValueError):
            await self.db.cancel_leave(1, 999, future)
        await self.db.cancel_leave(1, 42, future)
        with self.assertRaises(ValueError):
            await self.db.review_leave(1, 7, future, True)

    async def test_marking_requires_committed_roster_and_started_session(self):
        identifier = await self.session()
        with self.assertRaises(ValueError):
            await self.db.mark_attendance(1, 7, identifier, 42, "present")
        with self.assertRaises(ValueError):
            await self.db.mark_attendance(2, 7, identifier, 42, "present")
        past_no = await self.session(days=2, response="no")
        with self.assertRaises(ValueError):
            await self.db.mark_attendance(1, 7, past_no, 42, "absent")
        cancelled = await self.session(days=2, status="present")
        await self.db.cancel_event(1, 7, cancelled)
        with self.assertRaises(ValueError):
            await self.db.mark_attendance(1, 7, cancelled, 42, "absent")

    async def test_policy_evidence_and_restart_persistence(self):
        self.assertEqual(await self.db.policy(1), dict(days=30, minimum=5, percent=80))
        for status in ("present", "present", "present", "late", "absent"):
            await self.session(days=2, status=status)
        self.assertFalse((await self.db.promotion_evidence(1, 42))["ready"])
        await self.db.evaluate(1, 42, "Test Player", 7, "Test Scout", "Midfielder", 8.5, "Evidence")
        self.assertTrue((await self.db.promotion_evidence(1, 42))["ready"])
        await self.db.set_policy(1, 7, 60, 5, 90)
        self.assertFalse((await self.db.promotion_evidence(1, 42))["ready"])
        self.assertEqual((await self.db.policy(2))["percent"], 80)
        await self.db.close()
        await self.db.open()
        self.assertEqual((await self.db.policy(1))["percent"], 90)
        self.assertEqual((await self.db.attendance_summary(1, 42, 60))["eligible"], 5)
        self.assertEqual(len(await self.db.history(1, 42)), 1)

    async def test_branding_changes_only_once(self):
        user = SimpleNamespace(id=123, name="Old name")
        async def update(**kwargs):
            user.name = kwargs.get("username", user.name)
        user.edit = AsyncMock(side_effect=update)
        bot = SimpleNamespace(user=user, db=self.db)
        await apply_branding(bot)
        await apply_branding(bot)
        user.edit.assert_awaited_once()
        self.assertEqual(user.name, "Club Agent")
        self.assertTrue(user.edit.call_args.kwargs["avatar"].startswith(b"\xff\xd8"))


class OperationsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = ScoutBot(Settings(Path(":memory:"), None, False, "INFO"))
        await self.bot.db.open()
        self.bot.register_commands()
        self.player = self.interaction(42)
        self.staff = self.interaction(7, manager=True)

    def interaction(self, identifier, manager=False):
        member = Mock(spec=discord.Member)
        member.id, member.bot, member.display_name = identifier, False, f"Test {identifier}"
        member.roles = []
        member.guild_permissions = discord.Permissions(manage_guild=manager)
        return SimpleNamespace(guild=SimpleNamespace(id=1), guild_id=1, user=member,
                               client=self.bot, response=AsyncMock(), followup=AsyncMock())

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_all_slash_commands_serialize(self):
        commands = self.bot.tree.get_commands()
        self.assertEqual({c.name for c in commands}, {"club", "scout", "availability", "schedule", "attendance", "promotion"})
        for command in commands:
            payload = command.to_dict(self.bot.tree)
            self.assertEqual(payload["name"], command.name)
            self.assertLessEqual(len(payload.get("options", [])), 25)
        self.assertFalse(self.bot.intents.members)

    async def test_nonstaff_cannot_mutate_staff_state_or_view_others(self):
        group = ScheduleCommands(self.bot)
        with self.assertRaises(AccessDenied):
            await group.create.callback(group, self.player, "Training", "training", "2099-01-01", "20:00", "UTC", 90)
        attendance = AttendanceCommands(self.bot)
        with self.assertRaises(AccessDenied):
            await attendance.mark.callback(attendance, self.player, 1, self.player.user, "present")
        with self.assertRaises(AccessDenied):
            await attendance.report.callback(attendance, self.player, self.staff.user, None)
        promotions = PromotionCommands(self.bot)
        with self.assertRaises(AccessDenied):
            await promotions.review.callback(promotions, self.player, self.player.user)
        with self.assertRaises(AccessDenied):
            await promotions.policy.callback(promotions, self.player, 80, 30, 5)
        availability = AvailabilityCommands(self.bot)
        with self.assertRaises(AccessDenied):
            await availability.review_leave.callback(availability, self.player, 1, True)
        self.player.guild = None
        with self.assertRaises(AccessDenied):
            await availability.interaction_check(self.player)

    async def test_player_and_staff_command_journey(self):
        group = ScheduleCommands(self.bot)
        day = (utc_now() + timedelta(days=2)).strftime("%Y-%m-%d")
        await group.create.callback(group, self.staff, "Training", "training", day, "20:00", "UTC", 90)
        identifier = (await self.bot.db.one("SELECT id FROM club_events"))["id"]
        await group.rsvp.callback(group, self.player, identifier, "yes")
        await group.show.callback(group, self.player, identifier)
        availability = AvailabilityCommands(self.bot)
        await availability.set.callback(availability, self.player, 0, "20:00", "22:00", "UTC")
        await availability.view.callback(availability, self.player, None)
        await availability.leave.callback(availability, self.player, day, day, "UTC")
        leave_id = (await self.bot.db.one("SELECT id FROM club_leave"))["id"]
        await availability.review_leave.callback(availability, self.staff, leave_id, True)
        start = utc_now() - timedelta(days=1)
        async with self.bot.db.transaction() as conn:
            await conn.execute("UPDATE club_events SET starts_at=?,ends_at=? WHERE id=?",
                               (stamp(start), stamp(start + timedelta(hours=1)), identifier))
        attendance = AttendanceCommands(self.bot)
        await attendance.mark.callback(attendance, self.staff, identifier, self.player.user, "present")
        await attendance.report.callback(attendance, self.player, None, None)
        promotions = PromotionCommands(self.bot)
        await promotions.review.callback(promotions, self.staff, self.player.user)
        for interaction in (self.player, self.staff):
            for call in interaction.followup.send.call_args_list:
                self.assertTrue(call.kwargs["ephemeral"])
        summary = await self.bot.db.attendance_summary(1, 42, 30)
        self.assertEqual(summary["attended"], 1)
        self.assertGreater(len(await self.bot.db.all("SELECT id FROM audit_log")), 4)

    async def test_promotion_assignment_requires_evidence_or_explicit_audited_override(self):
        interaction = self.staff
        interaction.user.guild_permissions.manage_roles = True
        interaction.user.top_role = 99
        interaction.guild.owner_id = 999
        interaction.guild.me = SimpleNamespace(top_role=50)
        selected = SimpleNamespace(id=100, name="Academia Prospect")
        interaction.guild.get_role = lambda identifier: selected
        candidate = self.player.user
        candidate.roles, candidate.top_role = [], 1
        candidate.add_roles = AsyncMock()
        await self.bot.db.set_stage_role(1, 7, "Academia Prospect", selected.id)
        commands = ScoutCommands(self.bot)
        with patch("scoutbot.commands.safe_recruitment_role"):
            with self.assertRaises(ValueError):
                await commands.assign.callback(commands, interaction, candidate, "Academia Prospect", None)
            candidate.add_roles.assert_not_awaited()
            await commands.assign.callback(commands, interaction, candidate, "Academia Prospect",
                                            "Coach approved return after vacation")
        candidate.add_roles.assert_awaited_once()
        event = await self.bot.db.one("SELECT details FROM audit_log WHERE action='role.assigned'")
        self.assertIn("Coach approved return after vacation", event["details"])
        self.assertIn('"promotion_ready": false', event["details"])
