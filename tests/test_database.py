import asyncio
from pathlib import Path
import tempfile
import unittest

from scoutbot.database import Database
from scoutbot.parser import parse_recaps


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "scout.db")
        await self.db.open()

    async def asyncTearDown(self):
        await self.db.close()
        self.temp.cleanup()

    async def save_evaluation(self, guild=1, score=8.4):
        return await self.db.evaluate(guild, 10, "Candidate", 20, "Scout", "Defender", score, "Good positioning")

    async def test_configuration_round_trip_and_audit(self):
        await self.db.configure(1, 20, staff_role=100, staff_channel=200, recap_channel=300,
                                source_kind="bot", source_id=400)
        self.assertEqual((await self.db.settings(1))["staff_role"], "100")
        self.assertIsNone(await self.db.settings(2))
        self.assertEqual((await self.db.one("SELECT COUNT(*) n FROM audit_log"))["n"], 1)

    async def test_server_isolation_history_docket_and_pagination(self):
        for _ in range(7):
            await self.save_evaluation()
        await self.save_evaluation(guild=2)
        self.assertEqual(len(await self.db.history(1, 10)), 5)
        self.assertEqual(len(await self.db.history(1, 10, 1)), 2)
        self.assertEqual(len(await self.db.history(2, 10)), 1)
        self.assertEqual(len(await self.db.history(3, 10)), 0)
        self.assertEqual(len(await self.db.recent(1)), 5)

    async def test_ingestion_is_idempotent_even_concurrently(self):
        players = parse_recaps(["Handle | Rating: 8.4 | Goals: 2"])
        results = await asyncio.gather(*[
            self.db.ingest(1, 100, 200, "2026-10-21T12:00:00+00:00", players, 999)
            for _ in range(5)
        ])
        self.assertEqual(results.count(True), 1)
        self.assertEqual((await self.db.one("SELECT COUNT(*) n FROM match_stats"))["n"], 1)
        self.assertTrue(await self.db.ingest(2, 100, 200, "2026-10-21", players, 999))

    async def test_late_link_case_insensitive_and_no_cross_server_leak(self):
        players = parse_recaps(["EA.Player | Goals: 2"])
        await self.db.ingest(1, 100, 200, "2026-10-21", players, 999)
        self.assertEqual(await self.db.stats(1, 10), [])
        await self.db.link(1, 20, "ea.PLAYER", 10)
        self.assertEqual(len(await self.db.stats(1, 10)), 1)
        self.assertEqual(await self.db.stats(2, 10), [])
        totals = await self.db.aggregate(1, 10)
        self.assertEqual(totals["goals"], 2)
        self.assertIsNone(totals["rating"])
        with self.assertRaises(ValueError):
            await self.db.link(1, 20, "ea.player", 11)

    async def test_unlink_preserves_matches_and_requires_explicit_transfer(self):
        await self.db.link(1, 20, "Handle", 10)
        await self.db.ingest(1, 100, 200, "2026-10-21", parse_recaps(["Handle | Goals: 2"]), 999)
        self.assertTrue(await self.db.unlink(1, 20, "HANDLE"))
        self.assertEqual(await self.db.stats(1, 10), [])
        await self.db.link(1, 20, "Handle", 11)
        self.assertEqual(len(await self.db.stats(1, 11)), 1)
        self.assertFalse(await self.db.unlink(2, 20, "Handle"))

    async def test_failed_transaction_leaves_no_receipt(self):
        player = parse_recaps(["Handle | Goals: 2"])[0]
        with self.assertRaises(Exception):
            await self.db.ingest(1, 100, 200, "2026-10-21", [player, player], 999)
        self.assertIsNone(await self.db.one("SELECT * FROM recap_receipts"))
        self.assertIsNone(await self.db.one("SELECT * FROM match_stats"))

    async def test_settings_update_stage_roles_and_dockets_are_idempotent(self):
        for channel in (200, 201):
            await self.db.configure(1, 20, staff_role=100, staff_channel=channel, recap_channel=300,
                                    source_kind="bot", source_id=400)
        self.assertEqual((await self.db.settings(1))["staff_channel"], "201")
        for role in (10, 11):
            await self.db.set_stage_role(1, 20, "Trialist", role)
        self.assertEqual((await self.db.one("SELECT * FROM stage_roles"))["role_id"], "11")
        for message in (101, 102):
            await self.db.set_docket(1, 200, message)
        self.assertEqual((await self.db.one("SELECT * FROM dockets"))["message_id"], "102")

    async def test_persistence_across_reopen(self):
        await self.save_evaluation()
        await self.db.close()
        await self.db.open()
        self.assertEqual(len(await self.db.history(1, 10)), 1)

    async def test_sql_like_handles_are_data_not_queries(self):
        await self.db.link(1, 20, "Robert'); DROP TABLE evaluations;--", 10)
        await self.save_evaluation()
        self.assertEqual(len(await self.db.history(1, 10)), 1)
