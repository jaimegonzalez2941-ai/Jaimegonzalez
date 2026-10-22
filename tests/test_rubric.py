import asyncio
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from scoutbot.access import AccessDenied, staff_access
from scoutbot.database import Database, SCHEMA
from scoutbot import presentation as ui
from scoutbot.rubric import (
    ANCHORS, CATEGORIES, OBSERVABLES, VERSION, WEIGHTS,
    calculate, category_score, coverage, validate_payload,
)
from scoutbot.views import CategoryModal, CategoryView, EvaluationModal, PositionView, ScorecardView


def payload(score="8.0"):
    return {"categories": {key: {"score": score, "evidence": "Observed successful action"}
                           for key in CATEGORIES}, "count": 2,
            "context": "League match, assigned role, date and opposition",
            "notes": "Consider trial; discuss coverage first"}


class RubricTests(unittest.TestCase):
    def test_weights_and_role_observables(self):
        expected = [(35, 20, 20, 15, 10), (30, 20, 25, 15, 10),
                    (20, 25, 20, 25, 10), (35, 20, 20, 15, 10)]
        self.assertEqual(list(WEIGHTS.values()), expected)
        for role, weights in WEIGHTS.items():
            self.assertEqual(sum(weights), 100)
            self.assertEqual(len(OBSERVABLES[role]), 5)
            for n in range(1, 11):
                self.assertIn(f"{n}:", ANCHORS)
            data = payload()
            for key, score in zip(CATEGORIES, ("10", "8", "6", "4", "2")):
                data["categories"][key]["score"] = score
            expected_raw = sum(Decimal(v["score"]) * w / 100
                               for v, w in zip(data["categories"].values(), weights))
            self.assertEqual(calculate(role, data["categories"]).raw, expected_raw)

    def test_decimal_precision_and_half_up(self):
        data = payload("8")
        data["categories"]["teamwork"]["score"] = "8.5"
        result = calculate("Defender", data["categories"])
        self.assertEqual(result.raw, Decimal("8.05"))
        self.assertEqual(result.score, Decimal("8.1"))
        self.assertFalse(result.borderline)
        for score in ("1", "7", "10", "6.8", "7.2", "8.0"):
            self.assertEqual(category_score(score), f"{float(score):.1f}")
        for score in ("0", "10.1", "8.01", "8.00", "nan", "inf", "-inf", "bad", "8e-2"):
            with self.subTest(score=score), self.assertRaises(ValueError):
                category_score(score)
        self.assertIsNone(category_score(" "))

    def test_band_and_trial_boundaries_for_every_role(self):
        for role in WEIGHTS:
            for score in ("6.7", "6.8", "7.0", "7.2", "7.3", "7.9", "8.0", "10"):
                result = calculate(role, payload(score)["categories"])
                self.assertEqual(result.borderline, score in ("6.8", "7.0", "7.2"))
                self.assertEqual(result.recommendation.startswith("Trial-ready"), Decimal(score) >= 8)
        # Rounding cannot be used to evade a boundary.
        for base, extra, raw in (("6.7", "7.3", "6.76"), ("7.2", "7.6", "7.24")):
            data = payload(base)
            data["categories"]["teamwork"]["score"] = extra
            result = calculate("Attacker", data["categories"])
            self.assertEqual(result.raw, Decimal(raw))
            self.assertTrue(result.borderline)
        data = payload("7.9")
        data["categories"]["teamwork"]["score"] = "8.4"
        self.assertEqual(calculate("Attacker", data["categories"]).score, Decimal("8.0"))
        self.assertTrue(calculate("Attacker", data["categories"]).recommendation.startswith("Trial-ready"))

    def test_missing_categories_evidence_context_and_limits(self):
        for key in CATEGORIES:
            data = payload()
            data["categories"][key]["score"] = None
            self.assertIsNone(validate_payload(data, "Defender"))
            with self.assertRaises(ValueError):
                validate_payload(data, "Defender", complete=True)
        for change in (
            {"count": 0}, {"count": -1}, {"count": 1.5}, {"count": True},
            {"count": 10001}, {"context": "x" * 501}, {"notes": "x" * 1001},
            {"context": " "}, {"notes": " "}, {"count": None},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_payload(payload() | change, "Defender", complete=True)
        for evidence in (" ", "x" * 501):
            data = payload()
            data["categories"][CATEGORIES[0]]["evidence"] = evidence
            with self.assertRaises(ValueError):
                validate_payload(data, "Defender")
        for count, label in ((1, "Limited"), (2, "Limited"), (3, "Developing"),
                             (4, "Developing"), (5, "Broader"), (10000, "Broader")):
            self.assertEqual(coverage(count), label)
        with self.assertRaises(ValueError):
            calculate("Striker", {})
        with self.assertRaises(ValueError):
            calculate("Attacker", {}, "future")


class DraftTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "scout.db"
        self.db = Database(self.path)
        await self.db.open()

    async def asyncTearDown(self):
        await self.db.close()
        self.temp.cleanup()

    async def start(self, role="Defender"):
        return await self.db.start_draft(1, 10, "Candidate", 20, "Scout", role)

    async def fill(self, row, data=None):
        await self.db.change_draft(1, 20, row["id"], row["revision"], payload=data or payload())
        return await self.db.draft(1, 20, row["id"])

    async def test_save_all_positions_and_duplicate_concurrent_submissions(self):
        for role in WEIGHTS:
            row = await self.fill(await self.start(role))
            ids = await asyncio.gather(*[
                self.db.change_draft(1, 20, row["id"], row["revision"], action="save")
                for _ in range(4)])
            self.assertEqual(len(set(ids)), 1)
        rows = await self.db.history(1, 10)
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row["rubric_version"], VERSION)
            self.assertEqual(json.loads(row["scorecard"]), payload())
            self.assertEqual(row["score"], 8)
        self.assertEqual(len(await self.db.all("SELECT * FROM audit_log WHERE action='draft.save'")), 4)
        self.assertEqual(await self.db.all("SELECT * FROM stage_roles"), [])

    async def test_resume_reopen_stale_revision_and_audit(self):
        row = await self.start()
        same = await self.start()
        self.assertEqual(row["id"], same["id"])
        updated = await self.fill(row)
        with self.assertRaises(ValueError):
            await self.fill(row)
        with self.assertRaises(ValueError):
            await self.db.change_draft(1, 20, row["id"], 0, action="save")
        await self.db.close()
        await self.db.open()
        self.assertEqual((await self.start())["payload"], updated["payload"])
        audit = await self.db.one("SELECT * FROM audit_log WHERE action='draft.correct'")
        self.assertEqual(json.loads(audit["details"])["after"], payload())
        self.assertEqual(await self.db.history(1, 10), [])

    async def test_incomplete_borderline_correction_and_discard(self):
        row = await self.start()
        with self.assertRaises(ValueError):
            await self.db.change_draft(1, 20, row["id"], 0, action="save")
        row = await self.fill(row, payload("7.0"))
        with self.assertRaisesRegex(ValueError, "Borderline"):
            await self.db.change_draft(1, 20, row["id"], 1, action="save")
        self.assertEqual(await self.db.recent(1), [])
        row = await self.fill(row, payload("7.3"))
        await self.db.change_draft(1, 20, row["id"], row["revision"], action="discard")
        with self.assertRaises(ValueError):
            await self.db.change_draft(1, 20, row["id"], row["revision"], action="save")
        self.assertNotEqual((await self.start())["id"], row["id"])
        self.assertEqual(await self.db.recent(1), [])

    async def test_tenant_and_owner_boundaries(self):
        row = await self.start()
        for guild, actor in ((2, 20), (1, 21)):
            with self.assertRaises(ValueError):
                await self.db.draft(guild, actor, row["id"])
            for action in ("correct", "save", "discard"):
                with self.assertRaises(ValueError):
                    await self.db.change_draft(guild, actor, row["id"], 0,
                                               action=action, payload=payload())

    async def test_actual_old_schema_upgrade_legacy_and_stats_survive(self):
        await self.db.close()
        self.path.unlink()
        conn = sqlite3.connect(self.path)
        # Real pre-upgrade schema, no scorecard columns or draft table.
        conn.executescript(SCHEMA.split("CREATE TABLE IF NOT EXISTS evaluation_drafts")[0])
        conn.execute("INSERT INTO evaluations VALUES (1,'1','10','Old','20','Scout','Defender',8.4,'Old notes','2026-01-01')")
        conn.execute("INSERT INTO recap_receipts VALUES ('1','100','200','date','date',1)")
        conn.execute("INSERT INTO match_stats VALUES (1,'1','100','handle','Handle',7,2,NULL,NULL,NULL)")
        conn.commit()
        conn.close()
        await self.db.open()
        old = (await self.db.history(1, 10))[0]
        self.assertIsNone(old["scorecard"])
        self.assertIn("Legacy", ui.record_summary(old, details=True))
        self.assertNotIn("Role execution:", ui.record_summary(old, details=True))
        self.assertEqual((await self.db.one("SELECT * FROM match_stats"))["rating"], 7)
        await self.fill(await self.start())
        await self.db.close()
        await self.db.open()
        self.assertEqual((await self.db.history(1, 10))[0], old)
        self.assertEqual(len(await self.db.all("SELECT * FROM evaluation_drafts")), 1)

    def interaction(self, *, guild=1, owner=20):
        return SimpleNamespace(
            guild=SimpleNamespace(id=guild), user=SimpleNamespace(id=owner, display_name="Scout"),
            client=SimpleNamespace(db=self.db),
            response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    async def test_guided_discord_flow_context_categories_review_and_save(self):
        candidate = SimpleNamespace(id=10, display_name="Candidate")
        interaction = self.interaction()
        with patch("scoutbot.views.staff_access", new=AsyncMock()):
            for role in WEIGHTS:
                positions = PositionView(candidate, 20, 1)
                positions.select._values = [role]
                await positions.select_position(interaction)
                row = await self.start(role)
                context = EvaluationModal(row)
                context.count._value = "2"
                context.context._value = "Observed match on date against opposition"
                context.notes._value = "Repeated successful actions"
                await context.on_submit(interaction)
                row = await self.db.draft(1, 20, row["id"])
                for key in CATEGORIES:
                    review = ScorecardView(row)
                    review.select._values = [key]
                    await review.category(interaction)
                    guide = interaction.response.edit_message.call_args.kwargs["embed"]
                    self.assertIn(OBSERVABLES[role][CATEGORIES.index(key)], guide.description)
                    category = CategoryView(row, key)
                    await category.edit.callback(interaction)
                    modal = interaction.response.send_modal.call_args.args[0]
                    modal.score._value = "8.0"
                    modal.evidence._value = "Observed action under pressure"
                    await modal.on_submit(interaction)
                    row = await self.db.draft(1, 20, row["id"])
                await ScorecardView(row).statistics.callback(interaction)
                self.assertIn("0 verified imported", interaction.followup.send.call_args.kwargs["embed"].description)
                await ScorecardView(row).save.callback(interaction)
                await ScorecardView(row).save.callback(interaction)
                self.assertIn("saved", interaction.edit_original_response.call_args.kwargs["embed"].title)
        self.assertEqual(len(await self.db.history(1, 10)), 4)

    async def test_each_component_rechecks_staff_owner_and_guild(self):
        row = await self.start()
        for interaction in (self.interaction(guild=2), self.interaction(owner=21)):
            with patch("scoutbot.views.staff_access", new=AsyncMock()):
                for method in ("context", "statistics", "save", "discard"):
                    with self.assertRaises(AccessDenied):
                        await getattr(ScorecardView(row), method).callback(interaction)
                with self.assertRaises(AccessDenied):
                    await EvaluationModal(row).on_submit(interaction)
                with self.assertRaises(AccessDenied):
                    await CategoryModal(row, CATEGORIES[0]).on_submit(interaction)
        with patch("scoutbot.views.staff_access", new=AsyncMock(side_effect=AccessDenied("Not staff"))):
            with self.assertRaises(AccessDenied):
                await ScorecardView(row).save.callback(self.interaction())
        with self.assertRaises(AccessDenied):
            await staff_access(SimpleNamespace(guild=None))

    async def test_real_staff_access_rejects_removed_role_and_accepts_manager(self):
        await self.db.configure(1, 20, staff_role=100, staff_channel=200, recap_channel=300,
                                source_kind="bot", source_id=400)
        interaction = self.interaction()
        user = MagicMock(spec=discord.Member)
        user.guild_permissions = SimpleNamespace(manage_guild=False)
        user.roles = [SimpleNamespace(id=99)]
        interaction.user = user
        with self.assertRaises(AccessDenied):
            await staff_access(interaction)
        user.roles = [SimpleNamespace(id=100)]
        self.assertIsNotNone(await staff_access(interaction))
        user.roles = []
        user.guild_permissions.manage_guild = True
        self.assertIsNotNone(await staff_access(interaction))

    async def test_embed_limits_and_legacy_labels(self):
        data = payload()
        for entry in data["categories"].values():
            entry["evidence"] = "*@" * 250
        data.update(context="*" * 500, notes="*" * 1000, count=1)
        row = await self.fill(await self.start(), data)
        card = ui.scorecard(row)
        self.assertIn("Limited", card.description)
        self.assertIn("not verified statistical confidence", card.description)
        self.assertLessEqual(len(card), 6000)
        for field in card.fields:
            self.assertLessEqual(len(field.value), 1024)
        await self.db.change_draft(1, 20, row["id"], row["revision"], action="save")
        await self.db.evaluate(1, 10, "Old", 20, "Scout", "Defender", 8.4, "Legacy")
        rows = await self.db.history(1, 10)
        for card in (ui.history(rows * 2, SimpleNamespace(display_name="Candidate"), 0),
                     ui.docket(rows * 2)):
            self.assertLessEqual(len(card), 6000)
            for field in card.fields:
                self.assertLessEqual(len(field.value), 1024)
            self.assertIn("Legacy", str(card.to_dict()))
