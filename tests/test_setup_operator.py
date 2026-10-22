import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import discord

from scoutbot.database import Database
from scoutbot.domain import STAGES
from scoutbot.permission_audit import (
    member_tier_overlap,
    other_overwrite_bits_unchanged,
    require_exact_overlap_names,
    summarize_overwrites,
    true_permissions,
    without_view_channel,
)
from scoutbot.setup_operator import persist_setup, validate_manifest


class SetupOperatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(Path(":memory:"))
        await self.db.open()
        self.values = dict(staff_role="20", staff_channel="30", recap_channel="40",
                           source_kind="bot", source_id="50")
        self.stages = dict(zip(STAGES, ("60", "61", "62")))

    async def asyncTearDown(self):
        await self.db.close()

    async def test_complete_setup_is_atomic_audited_and_idempotent(self):
        await persist_setup(self.db, 1, 2, self.values, self.stages)
        await persist_setup(self.db, 1, 2, self.values, self.stages)
        self.assertEqual((await self.db.settings(1))["staff_channel"], "30")
        self.assertEqual(len(await self.db.all("SELECT * FROM stage_roles")), 3)
        self.assertEqual((await self.db.one("SELECT COUNT(*) n FROM audit_log"))["n"], 4)
        self.assertIsNone(await self.db.settings(2))

    async def test_conflicting_settings_are_not_overwritten(self):
        await self.db.configure(1, 2, **{**self.values, "staff_channel": "99"})
        with self.assertRaisesRegex(ValueError, "Saved setup differs"):
            await persist_setup(self.db, 1, 2, self.values, self.stages)
        self.assertEqual((await self.db.settings(1))["staff_channel"], "99")
        self.assertEqual(await self.db.all("SELECT * FROM stage_roles"), [])

    async def test_conflicting_tier_rolls_back_core_configuration(self):
        await self.db.set_stage_role(1, 2, STAGES[0], 99)
        with self.assertRaisesRegex(ValueError, "tier mapping differs"):
            await persist_setup(self.db, 1, 2, self.values, self.stages)
        self.assertIsNone(await self.db.settings(1))
        self.assertEqual((await self.db.one("SELECT COUNT(*) n FROM audit_log"))["n"], 1)

    async def test_existing_partial_mapping_is_preserved_and_completed(self):
        await self.db.set_stage_role(1, 2, STAGES[0], 60)
        await persist_setup(self.db, 1, 2, self.values, self.stages)
        self.assertEqual(len(await self.db.all("SELECT * FROM stage_roles")), 3)
        self.assertEqual((await self.db.one("SELECT COUNT(*) n FROM audit_log"))["n"], 4)

    def test_manifest_rejects_missing_tiers_non_numeric_ids_and_shared_roles(self):
        valid = dict(guild_id="1", actor_id="2", staff_role="20", staff_channel="30",
                     recap_channel="40", source_bot="50", stages=self.stages)
        self.assertEqual(validate_manifest(valid), valid)
        for invalid in (
            {**valid, "extra": "bad"},
            {**valid, "source_bot": True},
            {**valid, "source_bot": "https://discord.com/api/webhooks/private"},
            {**valid, "stages": {STAGES[0]: "60"}},
            {**valid, "stages": {stage: "60" for stage in STAGES}},
        ):
            with self.assertRaises(ValueError):
                validate_manifest(invalid)

    def test_role_and_channel_overwrite_audit_lists_grants_denials_and_unresolved_targets(self):
        manager = Mock(spec=discord.Role)
        manager.id, manager.name = 10, "Coach"
        member = Mock(spec=discord.Member)
        member.id, member.display_name = 20, "Player"
        unresolved = discord.Object(id=30, type=discord.abc.User)
        role_overwrite = discord.PermissionOverwrite(view_channel=True, manage_messages=True)
        member_overwrite = discord.PermissionOverwrite(view_channel=False, send_messages=True)
        channel = SimpleNamespace(overwrites={
            manager: role_overwrite, member: member_overwrite,
            unresolved: discord.PermissionOverwrite(view_channel=True),
        })
        result = summarize_overwrites(channel)
        self.assertEqual(result[0]["name"], "Coach")
        self.assertIn("view_channel", result[0]["allows"])
        self.assertIn("manage_messages", result[0]["allows"])
        self.assertIn("view_channel", result[1]["denies"])
        self.assertIn("send_messages", result[1]["allows"])
        self.assertEqual(result[2]["type"], "uncached-member")
        self.assertEqual(result[2]["id"], "30")
        self.assertIn("administrator", true_permissions(discord.Permissions(administrator=True)))

    def test_role_permission_inventory_is_read_only_and_comprehensive(self):
        perms = discord.Permissions.none()
        perms.manage_guild = True
        perms.view_audit_log = True
        self.assertEqual(true_permissions(perms), ["manage_guild", "view_audit_log"])

    def test_removing_view_grant_preserves_every_other_role_overwrite_bit(self):
        before = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_messages=False, read_message_history=True
        )
        after = without_view_channel(before)
        self.assertIs(before.view_channel, True)
        self.assertIsNone(after.view_channel)
        self.assertTrue(after.send_messages)
        self.assertTrue(after.read_message_history)
        self.assertFalse(other_overwrite_bits_unchanged(
            before, discord.PermissionOverwrite(view_channel=None, send_messages=False)
        ))
        self.assertTrue(other_overwrite_bits_unchanged(before, after))

    def test_member_tier_overlap_uses_only_current_role_ids(self):
        trialist = SimpleNamespace(id=10, name="⏳ Trialist")
        dev_squad = SimpleNamespace(id=11, name="⚽ Dev Squad")
        unrelated = SimpleNamespace(id=12, name="Server Booster")
        member = SimpleNamespace(roles=[trialist, unrelated, dev_squad])
        self.assertEqual(
            member_tier_overlap(member, [trialist, dev_squad]),
            ["⏳ Trialist", "⚽ Dev Squad"],
        )

    def test_individual_override_update_requires_the_exact_approved_accounts(self):
        approved = ["Rocha", "zERoo"]
        require_exact_overlap_names(["zERoo", "Rocha"], approved)
        with self.assertRaisesRegex(ValueError, "no grants were changed"):
            require_exact_overlap_names(["Rocha", "Another member"], approved)
        with self.assertRaisesRegex(ValueError, "no grants were changed"):
            require_exact_overlap_names(["Rocha", "Rocha"], approved)
