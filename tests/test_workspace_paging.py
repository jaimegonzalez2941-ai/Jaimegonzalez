"""Large-history, stable snapshot, resource lifetime and private continuation regressions."""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiosqlite
import discord

from scoutbot.access import AccessDenied
from scoutbot.assistant_ui import (
    briefing_blocks, briefing_stream, dossier_stream, escaped, pages, records_stream,
)
from scoutbot.club_domain import stamp, utc_now
from scoutbot.database import Database
from scoutbot.rubric import CATEGORIES
from scoutbot.workspace_pages import LazyStaffPages, WorkspacePages, descriptions, send_workspace
from scoutbot.workspace_snapshot import BATCH_SIZE, WorkspaceSnapshot


class WorkspacePagingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "club.db")
        await self.db.open()
        self.now = utc_now()
        self.sources = []
        self.member = SimpleNamespace(id=42, display_name="Player @everyone")

    async def asyncTearDown(self):
        await self.db.close()
        for source in self.sources:
            await source.close()
        self.temp.cleanup()

    async def source(self, factory):
        source = await WorkspacePages.open(self.db, "Private evidence", factory)
        self.sources.append(source)
        return source

    async def contents(self, source):
        result = []
        index = 0
        while True:
            actual, item = await source.page(index)
            self.assertEqual(actual, index)
            self.assertLessEqual(len(item.description), 3500)
            self.assertLessEqual(len(item), 6000)
            self.assertIn("Snapshot", item.footer.text)
            result.append(item.description)
            if source.exhausted and index == source.loaded - 1:
                break
            index += 1
        return "\n".join(result)

    async def seed_records(self, count=350):
        rows = []
        for kind in ("task", "recruitment", "goal"):
            for index in range(count):
                identifier = len(rows) + 1
                rows.append(("1", identifier, kind, str(42 + index) if kind == "recruitment" else "42",
                             "Defender" if kind == "recruitment" else None, "7",
                             f"WORK_{kind}_{index:04d} " + "e" * 150, "Observed five passes" if kind == "goal" else "",
                             stamp(self.now + timedelta(days=index % 3)), "UTC", "7",
                             stamp(self.now), stamp(self.now)))
        async with self.db.transaction() as conn:
            await conn.executemany(
                """INSERT INTO staff_records
                (guild_id,id,kind,member_id,position,owner_id,description,criteria,due_at,
                timezone,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

    async def seed_history(self, count=1205):
        scorecard = json.dumps({
            "count": 4, "context": "Declared context, not verified",
            "categories": {key: {"score": 8, "evidence": f"EVIDENCE_{key}_END"}
                           for key in CATEGORIES},
            "notes": "STRUCTURED_NOTES_END",
        })
        async with self.db.transaction() as conn:
            await conn.executemany(
                """INSERT INTO evaluations
                (guild_id,candidate_id,candidate_name,scout_id,scout_name,position,score,
                notes,created_at,rubric_version,scorecard) VALUES ('1','42','Player','7',
                'Coach','Defender',8,?,?,?,?)""",
                [(f"OBSERVATION_{i:05d} " + ("x" * 20000 + " LEGACY_NOTES_END" if i == 0 else ""),
                  stamp(self.now), "staff-v1" if i == 1 else None, scorecard if i == 1 else None)
                 for i in range(count)])
            await conn.execute(
                "INSERT INTO player_links VALUES ('1','ea','EA','42')")
            await conn.executemany(
                "INSERT INTO recap_receipts VALUES ('1',?,'200',?,? ,1)",
                [(str(i), stamp(self.now), stamp(self.now)) for i in range(count)])
            await conn.executemany(
                """INSERT INTO match_stats
                (guild_id,message_id,handle_key,player_handle,rating,goals)
                VALUES ('1',?,'ea','EA',?,?)""",
                [(str(i), 8 if i % 2 else None, 1) for i in range(count)])
            await conn.execute(
                """INSERT INTO evaluations
                (guild_id,candidate_id,candidate_name,scout_id,scout_name,position,score,
                notes,created_at) VALUES ('2','42','Player','7','Coach','Defender',8,
                'OTHER_SERVER_SECRET',?)""", (stamp(self.now),))
        draft = await self.db.start_draft(1, 42, "Player", 99, "Other coach", "Defender")
        data = json.loads(draft["payload"]) | {"notes": "OTHER_OWNER_PRIVATE_DRAFT"}
        await self.db.change_draft(1, 99, draft["id"], 0, payload=data)

    async def test_large_dossier_is_bounded_complete_and_keeps_metric_samples_and_labels(self):
        await self.seed_history()
        fetched = []
        original = aiosqlite.Cursor.fetchmany

        async def fetchmany(cursor, size=None):
            fetched.append(size)
            return await original(cursor, size)

        with patch.object(aiosqlite.Cursor, "fetchall", AsyncMock(
                side_effect=AssertionError("Workspace must not fetch all rows"))), \
                patch.object(aiosqlite.Cursor, "fetchmany", fetchmany):
            source = await self.source(lambda db: dossier_stream(db, 1, self.member))
            await source.page(0)
            self.assertEqual(source.loaded, 2)
            self.assertFalse(source.exhausted)
            # Only scalar summaries and a small number of row batches are read up front.
            self.assertLess(len(fetched), 12)
            rendered = await self.contents(source)
            first = (await source.page(0))[1].description
            self.assertIn("Player dossier", first)
        self.assertTrue(fetched)
        self.assertEqual(set(fetched), {BATCH_SIZE})
        identifiers = re.findall(r"Saved evaluation #(\d+)", rendered)
        self.assertEqual([int(i) for i in identifiers], list(range(1205, 0, -1)))
        self.assertEqual(len(re.findall(r"Trusted imported row #", rendered)), 1205)
        for key in CATEGORIES:
            # Escaping preserves every character, including underscores.
            self.assertIn(escaped(f"EVIDENCE_{key}_END"), rendered)
        self.assertIn("LEGACY\\_NOTES\\_END", rendered)
        self.assertEqual(sum(len(part) for part in re.findall(r"x{20,}", rendered)), 20000)
        self.assertIn("STRUCTURED\\_NOTES\\_END", rendered)
        self.assertIn("sample n=602", rendered)
        self.assertIn("sample n=1205", rendered)
        self.assertIn("sample n=0", rendered)
        self.assertIn("not statistical confidence", rendered)
        self.assertIn("not independently verified", rendered)
        self.assertNotIn("OTHER_SERVER_SECRET", rendered)
        self.assertNotIn("OTHER_OWNER_PRIVATE_DRAFT", rendered)

    async def test_all_record_kinds_have_no_caps_and_stable_tie_order(self):
        await self.seed_records()
        for kind in ("task", "recruitment", "goal"):
            source = await self.source(lambda db: records_stream(db, 1, kind, "all"))
            rendered = await self.contents(source)
            ids = [int(value) for value in re.findall(
                r"(?:Staff task|Recruitment follow-up|Development goal) #(\d+)", rendered)]
            expected = await self.db.staff_records(1, kind, "all")
            self.assertEqual(ids, [row["id"] for row in expected])
            self.assertEqual(len(ids), 350)
            self.assertNotIsInstance(source.loaded, list)
            self.assertTrue(source.exhausted)
            self.assertEqual((await source.page(0))[0], 0)

    async def test_concurrent_inserts_edits_and_completion_do_not_skip_or_duplicate(self):
        await self.seed_records(120)
        before = await self.db.staff_records(1, "task")
        source = await self.source(lambda db: records_stream(db, 1, "task", "active"))
        await source.page(0)
        # Separate writer connection proves the pinned reader does not block commits.
        writer = Database(self.db.path)
        await writer.open()
        try:
            await asyncio.wait_for(asyncio.gather(
                writer.edit_staff_record(1, 8, "task", 119, owner=8,
                                         due_at=stamp(self.now - timedelta(days=3)), timezone="UTC"),
                writer.finish_staff_record(1, 8, "task", 120, "NEW_COMPLETION_OUTCOME"),
                writer.save_staff_record(1, 8, "task", 8, "NEW_INSERT_AFTER_SNAPSHOT",
                                         stamp(self.now - timedelta(days=4)), "UTC"),
            ), timeout=5)
        finally:
            await writer.close()
        rendered = await self.contents(source)
        ids = [int(value) for value in re.findall(r"Staff task #(\d+)", rendered)]
        self.assertEqual(ids, [row["id"] for row in before])
        self.assertNotIn("NEW\\_INSERT", rendered)
        self.assertNotIn("NEW\\_COMPLETION", rendered)
        self.assertNotIn("Responsible staff ID: 8", rendered)
        refreshed = await self.source(lambda db: records_stream(db, 1, "task", "active"))
        changed = await self.contents(refreshed)
        self.assertIn("NEW\\_INSERT\\_AFTER\\_SNAPSHOT", changed)
        self.assertIn("Responsible staff ID: 8", changed)
        self.assertNotIn("Staff task #120 ", changed)

    async def test_dossier_link_and_evaluation_changes_only_appear_on_refresh(self):
        await self.seed_history(35)
        source = await self.source(lambda db: dossier_stream(db, 1, self.member))
        # Snapshot is pinned at open, not at first page/query of each section.
        await self.db.evaluate(1, 42, "Player", 7, "Coach", "Defender", 8, "NEW_EVIDENCE_AFTER_OPEN")
        await self.db.unlink(1, 7, "EA")
        await self.db.link(1, 7, "EA", 43)
        rendered = await self.contents(source)
        self.assertEqual(len(re.findall("Trusted imported row #", rendered)), 35)
        self.assertNotIn("NEW\\_EVIDENCE", rendered)
        refreshed = await self.source(lambda db: dossier_stream(db, 1, self.member))
        changed = await self.contents(refreshed)
        self.assertIn("NEW\\_EVIDENCE\\_AFTER\\_OPEN", changed)
        self.assertNotIn("Trusted imported row #", changed)

    async def test_large_brief_stream_matches_facts_and_preserves_snapshot_protections(self):
        await self.seed_records(150)
        async with self.db.transaction() as conn:
            await conn.executemany(
                """INSERT INTO club_events
                (guild_id,title,kind,starts_at,ends_at,timezone,created_by)
                VALUES ('1',?,'training',?,?,'UTC','7')""",
                [(f"Session {i}", stamp(self.now + timedelta(days=1)),
                  stamp(self.now + timedelta(days=1, hours=1))) for i in range(160)])
            await conn.executemany("INSERT INTO club_roster VALUES ('1',?,'42','yes',?)",
                                   [(i, stamp(self.now)) for i in range(1, 161)])
            await conn.executemany(
                """INSERT INTO club_leave
                (guild_id,member_id,starts_at,ends_at,timezone,status)
                VALUES ('1',?,?,?,'UTC','pending')""",
                [(str(i), stamp(self.now), stamp(self.now + timedelta(days=1))) for i in range(170)])
        source = await self.source(lambda db: briefing_stream(db, 1))
        expected = briefing_blocks(await self.db.staff_brief(1, now=source.snapshot.now))
        await self.db.set_policy(1, 7, 10, 3, 60)
        await self.db.review_leave(1, 7, 170, True)
        await self.db.cancel_event(1, 7, 160)
        rendered = await self.contents(source)
        expected_pages = pages("Brief", expected)
        self.assertEqual(rendered, "\n".join(item.description for item in expected_pages))
        self.assertIn("Leave #170", rendered)
        self.assertIn("Session #160", rendered)
        refreshed = await self.source(lambda db: briefing_stream(db, 1))
        changed = await self.contents(refreshed)
        self.assertNotIn("Leave #170", changed)
        self.assertNotIn("Session #160", changed)
        self.assertIn("10-day policy", changed)

    async def test_large_attendance_gaps_preserve_summary_and_concurrent_leave_protection(self):
        start, end = stamp(self.now - timedelta(days=1)), stamp(self.now - timedelta(hours=23))
        async with self.db.transaction() as conn:
            await conn.executemany(
                """INSERT INTO club_events
                (guild_id,title,kind,starts_at,ends_at,timezone,created_by)
                VALUES ('1','Past training','training',?,?,'UTC','7')""",
                [(start, end)] * 180)
            await conn.executemany("INSERT INTO club_roster VALUES ('1',?,?,'assigned',?)",
                                   [(i, str(member), start) for i in range(1, 181)
                                    for member in (42, 43, 44)])
            await conn.executemany(
                "INSERT INTO club_attendance VALUES ('1',?,'42',?,'7',?)",
                [(i, ("present", "late", "absent", "excused", "unmarked")[i % 5], end)
                 for i in range(1, 181)])
        pending = await self.db.request_leave(1, 43, start, end, "UTC")
        approved = await self.db.request_leave(1, 44, start, end, "UTC")
        await self.db.review_leave(1, 7, approved, True)
        source = await self.source(lambda db: briefing_stream(db, 1))
        current = source.snapshot.now
        expected = await self.db.staff_brief(1, now=current)
        self.assertEqual(len(expected["gaps"]), 216)  # 180 unmarked + 36 explicit unmarked
        self.assertEqual(await source.snapshot.attendance_summary(1, 42, 30),
                         await self.db.attendance_summary(1, 42, 30, now=current))
        self.assertEqual(await source.snapshot.attendance_summary(1, 44, 30),
                         await self.db.attendance_summary(1, 44, 30, now=current))
        await asyncio.gather(
            self.db.review_leave(1, 7, pending, True),
            self.db.mark_attendance(1, 7, 180, 43, "present"),
        )
        rendered = await self.contents(source)
        self.assertEqual(rendered, "\n".join(item.description for item in pages(
            "Brief", briefing_blocks(expected))))
        self.assertEqual(rendered.count("attendance unmarked."), 216)
        self.assertNotIn("Player ID 44: attendance unmarked.", rendered)
        refreshed = await self.source(lambda db: briefing_stream(db, 1))
        self.assertEqual((await self.contents(refreshed)).count("attendance unmarked."), 36)

    def interaction(self, owner=7, guild=1, staff=True):
        member = Mock(spec=discord.Member)
        member.id = owner
        member.roles = []
        member.guild_permissions = discord.Permissions(manage_guild=staff)
        return SimpleNamespace(user=member, guild=SimpleNamespace(id=guild), guild_id=guild,
                               client=SimpleNamespace(db=self.db), response=AsyncMock(),
                               followup=AsyncMock(), edit_original_response=AsyncMock())

    async def test_lazy_navigation_refresh_authorization_expiry_and_shutdown(self):
        await self.seed_records(30)
        source = await self.source(lambda db: records_stream(db, 1, "task", "active"))
        await source.page(0)
        view = LazyStaffPages(7, 1, source, self.db,
                              lambda db: records_stream(db, 1, "task", "active"))
        allowed = self.interaction()
        for interaction in (self.interaction(owner=8), self.interaction(guild=2),
                            self.interaction(staff=False)):
            for action in (lambda: view.move(interaction, 1),
                           lambda: view.refresh.callback(interaction)):
                with self.assertRaises(AccessDenied):
                    await action()
            interaction.response.defer.assert_not_awaited()
        dm = self.interaction()
        dm.guild = None
        with self.assertRaises(AccessDenied):
            await view.move(dm, 1)
        await self.db.configure(1, 7, staff_role=99, staff_channel=2, recap_channel=3,
                                source_kind="bot", source_id=4)
        coach = self.interaction(staff=False)
        coach.user.roles = [SimpleNamespace(id=99)]
        await view.move(coach, 1)
        await self.db.configure(1, 7, staff_role=100, staff_channel=2, recap_channel=3,
                                source_kind="bot", source_id=4)
        with self.assertRaises(AccessDenied):
            await view.refresh.callback(coach)
        with self.assertRaises(AccessDenied):
            await view.move(coach, -1)
        await view.move(allowed, -1)
        await view.move(allowed, 1)
        self.assertEqual(view.index, 1)
        self.assertTrue(view.previous.disabled is False)
        await view.move(allowed, -1)
        self.assertEqual(view.index, 0)
        await self.db.save_staff_record(1, 7, "task", 7, "REFRESHED_WORK",
                                         stamp(self.now - timedelta(days=3)), "UTC")
        old_directory = Path(source.snapshot.directory.name)
        await view.refresh.callback(allowed)
        self.assertTrue(source.closed)
        self.assertFalse(old_directory.exists())
        self.assertEqual(view.index, 0)
        self.assertIn("REFRESHED\\_WORK", allowed.edit_original_response.call_args.kwargs["embed"].description)
        await asyncio.sleep(0)  # cancelled old expiry must not close the replacement
        self.assertFalse(view.source.closed)
        self.assertEqual(allowed.edit_original_response.call_args.kwargs["allowed_mentions"].to_dict(),
                         discord.AllowedMentions.none().to_dict())
        await self.db.close()
        self.assertTrue(view.source.closed)
        self.assertFalse(self.db.workspace_views)
        with self.assertRaises(AccessDenied):
            await view.source.page(0)

    async def test_failed_open_send_and_refresh_release_private_files(self):
        interaction = self.interaction()
        interaction.followup.send.side_effect = RuntimeError("Discord unavailable")
        factory = lambda db: records_stream(db, 1, "task", "active")
        with self.assertRaisesRegex(RuntimeError, "Discord unavailable"):
            await send_workspace(interaction, "Tasks", factory)
        self.assertFalse(self.db.workspace_views)
        source = await self.source(factory)
        await source.page(0)
        view = LazyStaffPages(7, 1, source, self.db, factory)
        interaction.edit_original_response.side_effect = RuntimeError("Cannot edit")
        with self.assertRaisesRegex(RuntimeError, "Cannot edit"):
            await view.refresh.callback(interaction)
        self.assertIs(view.source, source)
        self.assertFalse(source.closed)
        await view.on_timeout()
        self.assertFalse(Path(source.snapshot.directory.name).exists())

    async def test_in_memory_database_uses_private_disk_backup(self):
        db = Database(Path(":memory:"))
        await db.open()
        snapshot = None
        try:
            await db.evaluate(1, 42, "Player", 7, "Coach", "Defender", 8, "Before")
            snapshot = await WorkspaceSnapshot.open(db)
            await db.evaluate(1, 42, "Player", 7, "Coach", "Defender", 8, "After")
            rows = [row async for row in snapshot.rows(
                "SELECT notes FROM evaluations WHERE guild_id='1' ORDER BY id")]
            self.assertEqual(rows, [{"notes": "Before"}])
            with self.assertRaisesRegex(RuntimeError, "bounded"):
                await snapshot.all("SELECT * FROM evaluations")
        finally:
            if snapshot:
                await snapshot.close()
            await db.close()

    async def test_incremental_text_paging_is_lossless_and_matches_existing_splitter(self):
        blocks = ["Header", "x" * 20000, "Evidence " + "\\*" * 4000, "Tail"]

        async def stream():
            for block in blocks:
                yield block

        actual = [description async for description in descriptions(stream())]
        self.assertEqual(actual, [item.description for item in pages("Legacy", blocks)])


if __name__ == "__main__":
    unittest.main()
