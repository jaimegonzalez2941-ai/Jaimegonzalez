"""Staff workspace persistence and evidence contracts, without contacting Discord."""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from scoutbot.assistant_store import deadline
from scoutbot.assistant_ui import (
    briefing_blocks, comparison_blocks, dossier_blocks, escaped, evaluation_blocks, pages,
)
from scoutbot.club_domain import stamp, utc_now
from scoutbot.database import Database, SCHEMA
from scoutbot.parser import parse_recaps
from scoutbot.rubric import CATEGORIES


class AssistantStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "staff.db"
        self.db = Database(self.path)
        await self.db.open()
        self.now = utc_now().replace(microsecond=0)
        self.first = SimpleNamespace(id=42, display_name="First @everyone **player**")
        self.second = SimpleNamespace(id=43, display_name="Second")

    async def asyncTearDown(self):
        await self.db.close()
        self.temp.cleanup()

    async def save(self, kind="task", *, guild=1, delta=1, **kwargs):
        values = dict(member=42, position="Defender") if kind == "recruitment" else (
            dict(member=42, criteria="Complete five observed accurate passes") if kind == "goal" else {})
        values.update(kwargs)
        return await self.db.save_staff_record(
            guild, 7, kind, 7, "Observe the next session", stamp(self.now + timedelta(days=delta)),
            "UTC", **values)

    async def saved_evaluation(self, member=42, position="Defender", *, guild=1):
        row = await self.db.start_draft(guild, member, "Player", 7, "Coach", position)
        data = dict(categories={key: {"score": "8.0", "evidence": f"{key} observed " + "*" * 470}
                                for key in CATEGORIES},
                    count=3, context="Observed 2026-10-20; training against reserves",
                    notes="FULL_NOTES_START " + "n" * 950 + " FULL_NOTES_END")
        await self.db.change_draft(guild, 7, row["id"], row["revision"], payload=data)
        return await self.db.change_draft(guild, 7, row["id"], row["revision"] + 1, action="save")

    async def test_additive_migration_preserves_old_records(self):
        old_path = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(old_path) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                """INSERT INTO evaluations(guild_id,candidate_id,candidate_name,scout_id,scout_name,
                position,score,notes,created_at) VALUES ('1','42','Player','7','Scout','Defender',
                8.0,'legacy evidence','2026-10-01T00:00:00+00:00')""")
        legacy = Database(old_path)
        try:
            await legacy.open()
            self.assertEqual((await legacy.history(1, 42))[0]["notes"], "legacy evidence")
            self.assertIsNone((await legacy.history(1, 42))[0]["rubric_version"])
            await legacy.close()
            await legacy.open()
            self.assertEqual(len(await legacy.history(1, 42)), 1)
            self.assertEqual(await legacy.staff_records(1, "goal"), [])
        finally:
            await legacy.close()

    async def test_concurrent_shortlist_upsert_archive_and_readd(self):
        ids = await asyncio.gather(*(self.save("recruitment") for _ in range(5)))
        self.assertEqual(len(set(ids)), 1)
        changed = await self.db.save_staff_record(
            1, 8, "recruitment", 8, "New action", stamp(self.now), "UTC",
            member=42, position="Defender")
        self.assertEqual(changed, ids[0])
        entry = (await self.db.staff_records(1, "recruitment"))[0]
        self.assertEqual((entry["owner_id"], entry["description"]), ("8", "New action"))
        await self.save("recruitment", position="Attacker")
        self.assertEqual(len(await self.db.staff_records(1, "recruitment")), 2)
        self.assertTrue(await self.db.finish_staff_record(1, 7, "recruitment", changed))
        self.assertFalse(await self.db.finish_staff_record(1, 7, "recruitment", changed))
        self.assertEqual(len(await self.db.staff_records(1, "recruitment", "archived")), 1)
        self.assertNotEqual(await self.save("recruitment"), changed)
        audit = await self.db.all("SELECT action,details FROM audit_log WHERE action='recruitment.updated'")
        self.assertTrue(json.loads(audit[-1]["details"])["before"])
        self.assertEqual(len(await self.db.all("SELECT id FROM audit_log WHERE action='recruitment.archived'")), 1)

    async def test_lifecycle_isolation_idempotence_and_restart(self):
        for kind in ("task", "goal"):
            identifier = await self.save(kind)
            with self.assertRaises(ValueError):
                await self.db.finish_staff_record(2, 7, kind, identifier, "Other server")
            with self.assertRaises(ValueError):
                await self.db.finish_staff_record(1, 7, "recruitment", identifier)
            with self.assertRaises(ValueError):
                await self.db.finish_staff_record(1, 7, kind, identifier, " ")
            outcomes = await asyncio.gather(*(
                self.db.finish_staff_record(1, 7, kind, identifier, "Observed outcome") for _ in range(4)))
            self.assertEqual(outcomes.count(True), 1)
            await self.db.close()
            await self.db.open()
            self.assertFalse(await self.db.finish_staff_record(1, 8, kind, identifier, "Replacement outcome"))
            row = (await self.db.staff_records(1, kind, "completed"))[0]
            self.assertEqual((row["outcome"], row["completed_by"]), ("Observed outcome", "7"))
            self.assertEqual(await self.db.staff_records(2, kind, "all"), [])
            self.assertEqual(len(await self.db.all(
                "SELECT id FROM audit_log WHERE action=?", (kind + ".completed",))), 1)
        self.assertEqual(await self.db.all("SELECT * FROM evaluations"), [])
        self.assertEqual(await self.db.all("SELECT * FROM stage_roles"), [])

    async def test_edit_preserves_identity_and_history_across_restart(self):
        for kind in ("goal", "task"):
            identifier = await self.save(kind, delta=-1)
            before = (await self.db.staff_records(1, kind))[0]
            due = deadline("2026-11-05", "20:00", "America/Chicago")
            self.assertTrue(await self.db.edit_staff_record(
                1, 8, kind, identifier, owner=9, due_at=due, timezone="America/Chicago"))
            after = (await self.db.staff_records(1, kind))[0]
            self.assertEqual(after, before | {
                "owner_id": "9", "due_at": due, "timezone": "America/Chicago",
                "updated_at": after["updated_at"]})
            audit = await self.db.one(
                "SELECT * FROM audit_log WHERE action=?", (kind + ".updated",))
            self.assertEqual((audit["guild_id"], audit["actor_id"]), ("1", "8"))
            self.assertEqual(json.loads(audit["details"]),
                             {"id": identifier, "before": before, "after": after})
            self.assertTrue(await self.db.edit_staff_record(1, 8, kind, identifier, owner=10))
            self.assertTrue(await self.db.edit_staff_record(
                1, 8, kind, identifier, due_at=stamp(self.now), timezone="UTC"))
            self.assertFalse(await self.db.edit_staff_record(1, 8, kind, identifier, owner=10))
            await self.db.finish_staff_record(1, 8, kind, identifier, "Observed success")
            completed = (await self.db.staff_records(1, kind, "completed"))[0]
            completion_audit = await self.db.all(
                "SELECT * FROM audit_log WHERE action=?", (kind + ".completed",))
            for values in ({"owner": 11}, {"due_at": due, "timezone": "UTC"},
                           {"owner": 11, "due_at": due, "timezone": "UTC"}):
                with self.assertRaisesRegex(ValueError, "Completed"):
                    await self.db.edit_staff_record(1, 8, kind, identifier, **values)
            await self.db.close()
            await self.db.open()
            self.assertEqual(await self.db.staff_records(1, kind, "all"), [completed])
            self.assertEqual((completed["id"], completed["owner_id"], completed["completed_by"],
                              completed["outcome"]), (identifier, "10", "8", "Observed success"))
            self.assertEqual(await self.db.all(
                "SELECT * FROM audit_log WHERE action=?", (kind + ".completed",)), completion_audit)
            self.assertEqual(len(await self.db.all(
                "SELECT * FROM audit_log WHERE action=?", (kind + ".updated",))), 3)

    async def test_edit_isolation_validation_and_audit_rollback(self):
        for kind in ("goal", "task"):
            identifier = await self.save(kind)
            # The same server-local ID can identify a different kind in another server.
            other_kind = "task" if kind == "goal" else "goal"
            other_id = await self.save(other_kind, guild=identifier + 1)
            before = await self.db.all("SELECT * FROM staff_records ORDER BY guild_id,id")
            audits = await self.db.all("SELECT * FROM audit_log")
            for guild, target_kind, target_id in (
                (999, kind, identifier), (1, other_kind, identifier),
                (identifier + 1, kind, other_id), (1, kind, 999),
                (1, "recruitment", identifier), (1, "unknown", identifier),
            ):
                with self.assertRaises(ValueError):
                    await self.db.edit_staff_record(guild, 8, target_kind, target_id, owner=9)
            for values in (
                {}, {"owner": 0}, {"owner": -1}, {"owner": "invalid"}, {"owner": "９"},
                {"due_at": stamp(self.now)}, {"timezone": "UTC"},
                {"due_at": "2026-11-01", "timezone": "UTC"},
                {"due_at": "garbage", "timezone": "UTC"},
                {"due_at": stamp(self.now), "timezone": "Invalid/Zone"},
            ):
                with self.subTest(kind=kind, values=values), self.assertRaises(ValueError):
                    await self.db.edit_staff_record(1, 8, kind, identifier, **values)
            with patch.object(self.db, "_audit", AsyncMock(side_effect=RuntimeError("audit failed"))):
                with self.assertRaises(RuntimeError):
                    await self.db.edit_staff_record(
                        1, 8, kind, identifier, owner=9,
                        due_at=stamp(self.now), timezone="UTC")
            self.assertEqual(await self.db.all("SELECT * FROM staff_records ORDER BY guild_id,id"), before)
            self.assertEqual(await self.db.all("SELECT * FROM audit_log"), audits)

    async def test_concurrent_edits_and_completion_serialize_history(self):
        for kind in ("goal", "task"):
            identifier = await self.save(kind)
            before = (await self.db.staff_records(1, kind))[0]
            # Partial edits must not overwrite unrelated fields with stale values.
            await asyncio.gather(
                self.db.edit_staff_record(1, 8, kind, identifier, owner=9),
                self.db.edit_staff_record(1, 8, kind, identifier,
                                          due_at=stamp(self.now), timezone="UTC"))
            row = (await self.db.staff_records(1, kind))[0]
            self.assertEqual((row["owner_id"], row["due_at"]), ("9", stamp(self.now)))
            audits = await self.db.all(
                "SELECT details FROM audit_log WHERE action=? ORDER BY id", (kind + ".updated",))
            first, second = [json.loads(a["details"]) for a in audits]
            self.assertEqual(first["before"], before)
            self.assertEqual(first["after"], second["before"])
            self.assertEqual(second["after"], row)
            results = await asyncio.gather(
                self.db.finish_staff_record(1, 8, kind, identifier, "Final observation"),
                self.db.edit_staff_record(1, 8, kind, identifier, owner=11),
                return_exceptions=True)
            self.assertTrue(results[0])
            completed = (await self.db.staff_records(1, kind, "completed"))[0]
            if isinstance(results[1], ValueError):
                self.assertEqual(completed["owner_id"], "9")
            else:
                self.assertIs(results[1], True)
                self.assertEqual(completed["owner_id"], "11")
            self.assertEqual(completed["outcome"], "Final observation")
            audit_count = len(await self.db.all("SELECT * FROM audit_log"))
            with self.assertRaises(ValueError):
                await self.db.edit_staff_record(1, 8, kind, identifier, owner=12)
            self.assertEqual(len(await self.db.all("SELECT * FROM audit_log")), audit_count)

    async def test_edit_same_local_id_changes_only_selected_server(self):
        for kind in ("goal", "task"):
            first = await self.save(kind)
            second = await self.save(kind, guild=2)
            self.assertEqual(first, second)
            other = await self.db.staff_records(2, kind)
            await self.db.edit_staff_record(1, 8, kind, first, owner=9)
            self.assertEqual(await self.db.staff_records(2, kind), other)

    async def test_failed_audit_rolls_back_mutation(self):
        with patch.object(self.db, "_audit", AsyncMock(side_effect=RuntimeError("audit failed"))):
            with self.assertRaises(RuntimeError):
                await self.save()
        self.assertEqual(await self.db.staff_records(1, "task"), [])
        identifier = await self.save()
        with patch.object(self.db, "_audit", AsyncMock(side_effect=RuntimeError("audit failed"))):
            with self.assertRaises(RuntimeError):
                await self.db.finish_staff_record(1, 7, "task", identifier, "Done successfully")
        self.assertEqual(len(await self.db.staff_records(1, "task")), 1)

    async def test_server_local_ids_and_concurrent_allocation(self):
        ids = await asyncio.gather(*(self.save() for _ in range(10)))
        self.assertEqual(sorted(ids), list(range(1, 11)))
        self.assertEqual(await self.save(guild=2), 1)
        self.assertEqual(len(await self.db.staff_records(1, "task")), 10)
        self.assertEqual(len(await self.db.staff_records(2, "task")), 1)

    async def test_validation_limits_and_filters(self):
        for kind, extra in (("unknown", {}), ("goal", {"criteria": " "}),
                            ("recruitment", {"position": "Striker"})):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                await self.save(kind, **extra)
        for due, owner, description in (("2026-10-01", 7, "Valid description"),
                                        ("garbage", 7, "Valid description"),
                                        (stamp(self.now), 0, "Valid description"),
                                        (stamp(self.now), 7, "x" * 1001),
                                        (stamp(self.now), 7, "  ")):
            with self.assertRaises(ValueError):
                await self.db.save_staff_record(1, 7, "task", owner, description, due, "UTC")
        with self.assertRaises(ValueError):
            await self.db.staff_records(1, "task", "deleted")
        before, at, after = [await self.save(delta=d) for d in (-1, 0, 1)]
        overdue = await self.db.staff_records(1, "task", "overdue", now=self.now)
        self.assertEqual([r["id"] for r in overdue], [before])
        self.assertNotIn(at, [r["id"] for r in overdue])
        self.assertNotIn(after, [r["id"] for r in overdue])

    async def test_dossier_has_full_saved_evidence_no_drafts_and_separate_samples(self):
        await self.saved_evaluation()
        draft = await self.db.start_draft(1, 42, "Player", 99, "Other scout", "Attacker")
        private = json.loads(draft["payload"]) | {"notes": "SECRET_OTHER_SCOUT_DRAFT"}
        await self.db.change_draft(1, 99, draft["id"], 0, payload=private)
        await self.saved_evaluation(guild=2)
        await self.db.ingest(1, 100, 200, stamp(self.now),
                             parse_recaps(["EAPlayer | Goals: 2"]), 7)
        await self.db.ingest(1, 101, 200, stamp(self.now),
                             parse_recaps(["EAPlayer | Rating: 8.5 | Goals: 0"]), 7)
        await self.db.link(1, 7, "EAPlayer", 42)
        await self.save("recruitment")
        await self.save("goal")
        samples = await self.db.trusted_samples(1, 42)
        self.assertEqual((samples["records"], samples["rating_n"], samples["goals_n"]), (2, 1, 2))
        self.assertEqual(samples["tackles_n"], 0)
        blocks = await dossier_blocks(self.db, 1, self.first)
        rendered = "\n".join(blocks)
        self.assertIn(escaped("FULL_NOTES_END"), rendered)
        self.assertIn("staff-v1", rendered)
        self.assertIn("Structured observation dates: Unreported", rendered)
        self.assertIn("Developing coverage", rendered)
        self.assertIn("sample n=0", rendered)
        self.assertNotIn("SECRET_OTHER_SCOUT_DRAFT", rendered)
        self.assertIn("Recruitment follow-up", rendered)
        self.assertIn("Development goal", rendered)
        items = pages("Dossier", blocks)
        self.assertGreater(len(items), 1)
        joined = "\n".join(item.description for item in items)
        for key in CATEGORIES:
            self.assertIn(escaped(f"{key} observed " + "*" * 470), joined)
        self.assertIn(escaped("FULL_NOTES_END"), joined)
        self.assertTrue(all(len(item) <= 6000 and len(item.description) <= 3500 for item in items))

    async def test_compare_latest_saved_at_position_and_versions(self):
        first_id = await self.saved_evaluation()
        await self.saved_evaluation(43)
        await self.db.evaluate(1, 42, "Player", 7, "Coach", "Attacker", 8.7, "Other position")
        await self.db.evaluate(2, 42, "Player", 7, "Coach", "Defender", 8.9, "Other server")
        rendered = "\n".join(await comparison_blocks(self.db, 1, self.first, self.second, "Defender"))
        self.assertIn("Compatible rubric", rendered)
        self.assertIn(f"Saved evaluation #{first_id}", rendered)
        self.assertIn("Saved category: 8.0", rendered)
        self.assertIn("No overall winner", rendered)
        self.assertNotIn("Other position", rendered)
        self.assertNotIn("Other server", rendered)
        async with self.db.transaction() as conn:
            await conn.execute("UPDATE evaluations SET rubric_version='future-v2' WHERE id=?", (first_id,))
        rendered = "\n".join(await comparison_blocks(self.db, 1, self.first, self.second, "Defender"))
        self.assertIn("incompatible", rendered)
        self.assertNotIn("Saved category:", rendered)
        self.assertIn("Full saved notes", rendered)
        legacy = await self.db.evaluate(1, 42, "Player", 7, "Coach", "Defender", 8.5, "Legacy note")
        rendered = "\n".join(await comparison_blocks(self.db, 1, self.first, self.second, "Defender"))
        self.assertIn(f"evaluation #{legacy}", rendered)
        self.assertIn("Legacy single-score", rendered)
        self.assertNotIn("Saved category:", rendered)
        self.assertIn("Legacy note", rendered)

    async def test_empty_partial_views_are_explicit(self):
        rendered = "\n".join(await dossier_blocks(self.db, 1, self.first))
        self.assertIn("Unreported; no saved evaluations", rendered)
        self.assertIn("Not reported", rendered)
        await self.saved_evaluation()
        rendered = "\n".join(await comparison_blocks(self.db, 1, self.first, self.second, "Defender"))
        self.assertIn("Unreported at this position", rendered)
        self.assertNotIn("Saved category:", rendered)
        self.assertIn("No eligible incomplete attendance", "\n".join(
            briefing_blocks(await self.db.staff_brief(1, now=self.now))))

    async def session(self, *, days=-1, response="yes", status=None, leave=False,
                      cancelled=False, guild=1, ongoing=False):
        start = self.now + timedelta(days=days)
        end = self.now + timedelta(hours=1) if ongoing else start + timedelta(hours=1)
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                """INSERT INTO club_events
                (guild_id,title,kind,starts_at,ends_at,timezone,cancelled,created_by)
                VALUES (?,'Practice','training',?,?,'UTC',?,'7')""",
                (str(guild), stamp(start), stamp(end), int(cancelled)))
            identifier = cur.lastrowid
            await cur.close()
            await conn.execute("INSERT INTO club_roster VALUES (?,?,?,?,?)",
                               (str(guild), identifier, "42", response, stamp(start)))
        if status:
            await self.db.mark_attendance(guild, 7, identifier, 42, status)
        if leave:
            leave_id = await self.db.request_leave(guild, 42, stamp(start), stamp(end), "UTC")
            await self.db.review_leave(guild, 7, leave_id, True)
        return identifier

    async def test_brief_attendance_and_leave_protections_current_policy(self):
        await self.db.set_policy(1, 7, 10, 5, 80)
        eligible = await self.session()
        explicit = await self.session(status="unmarked")
        await self.session(status="excused")
        await self.session(status="absent")
        await self.session(days=-3, leave=True)
        await self.session(days=-11)
        await self.session(cancelled=True)
        await self.session(response="maybe")
        await self.session(response="no")
        await self.session(response="pending")
        await self.session(ongoing=True)
        await self.session(guild=2)
        leave_id = await self.db.request_leave(
            1, 42, stamp(self.now + timedelta(days=30)), stamp(self.now + timedelta(days=31)), "UTC")
        data = await self.db.staff_brief(1, now=self.now)
        self.assertEqual({r["id"] for r in data["gaps"]}, {eligible, explicit})
        self.assertEqual([r["id"] for r in data["leave"]], [leave_id])
        rendered = "\n".join(briefing_blocks(data))
        self.assertIn("not absent", rendered)
        self.assertIn("Suggested next actions", rendered)
        self.assertIn("10-day policy", rendered)

    async def test_brief_seven_day_and_overdue_boundaries_and_counts(self):
        upcoming = await self.session(days=1, response="yes")
        async with self.db.transaction() as conn:
            await conn.executemany("INSERT INTO club_roster VALUES (?,?,?,?,?)", [
                ("1", upcoming, str(member), response, stamp(self.now))
                for member, response in enumerate(("assigned", "maybe", "no", "pending"), 100)])
        await self.session(days=7)
        await self.session(days=2, cancelled=True)
        await self.session(days=2, guild=2)
        for kind in ("task", "goal", "recruitment"):
            await self.save(kind, delta=-1)
            if kind == "recruitment":
                await self.save(kind, delta=0, member=43)
                await self.save(kind, delta=7, member=44)
            else:
                await self.save(kind, delta=0)
                await self.save(kind, delta=7)
                done = await self.save(kind, delta=-2)
                await self.db.finish_staff_record(1, 7, kind, done, "Already complete")
        data = await self.db.staff_brief(1, now=self.now)
        self.assertEqual([r["id"] for r in data["sessions"]], [upcoming])
        session = data["sessions"][0]
        self.assertEqual([session[k + "_count"] for k in ("yes", "assigned", "maybe", "no", "pending")],
                         [1, 1, 1, 1, 1])
        self.assertEqual(len(data["followups"]), 6)
        self.assertTrue(all(r["due_at"] < stamp(self.now + timedelta(days=7)) for r in data["followups"]))
        self.assertIn("Yes: 1 · Assigned: 1 · Maybe: 1 · No: 1 · Pending: 1",
                      "\n".join(briefing_blocks(data)))


class DeadlineTests(unittest.TestCase):
    def test_explicit_timezone_and_dst(self):
        self.assertEqual(deadline("2026-10-22", "12:00", "America/Chicago"),
                         "2026-10-22T17:00:00+00:00")
        for date, time, zone in (("2026-11-01", "01:30", "America/Chicago"),
                                 ("2026-03-08", "02:30", "America/Chicago"),
                                 ("bad", "12:00", "UTC"),
                                 ("2026-10-22", "12:00", "Unknown/Zone")):
            with self.subTest(date=date), self.assertRaises(ValueError):
                deadline(date, time, zone)

    def test_large_legacy_notes_paginate_without_loss(self):
        row = dict(id=1, position="Defender", created_at=stamp(utc_now()), scout_name="Coach",
                   score=8, notes="x" * 20000, rubric_version=None)
        items = pages("Legacy", evaluation_blocks(row))
        self.assertGreater(len(items), 5)
        self.assertEqual("".join(p.description for p in items).count("x"), 20000)
