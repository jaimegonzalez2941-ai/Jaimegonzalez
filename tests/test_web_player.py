"""Own-account player reads never inherit staff authority or private evidence."""

from datetime import timedelta
import json
import unittest
from unittest.mock import AsyncMock, patch

import discord

from scoutbot.access import AccessDenied
from scoutbot.club_domain import stamp, utc_now
from scoutbot.web_context import current_member_context
from tests import test_web_companion as companion
from tests.test_web_companion import GUILD, OTHER, ACTOR, PLAYER, SUBJECT


class PlayerTests(unittest.IsolatedAsyncioTestCase):
    # Reuse isolated fixture setup without rerunning the staff suite by inheritance.
    asyncSetUp = companion.WebTests.asyncSetUp
    asyncTearDown = companion.WebTests.asyncTearDown
    dispatch = companion.WebTests.dispatch
    member = companion.WebTests.member
    async def goal(self, member=ACTOR, guild=GUILD, kind="goal"):
        return await self.db.save_staff_record(
            guild, ACTOR, kind, ACTOR, "SECRET STAFF DESCRIPTION",
            stamp(utc_now() + timedelta(days=3)), "UTC", member=member,
            criteria="SECRET STAFF CRITERIA" if kind == "goal" else "")

    async def read_player(self, **params):
        with patch("scoutbot.web_bridge.current_member_context",
                   AsyncMock(return_value=self.context)):
            return await self.bridge.dispatch(
                {"kind": "player.development", "subject": SUBJECT,
                 "guild": str(GUILD), "params": params})

    async def share(self, identifier, text="Practice first-time passing with your coach"):
        return await self.dispatch(kind="action", operation="goal.share", confirmed=True,
                                   values={"id": identifier, "text": text})

    async def test_only_explicit_player_text_and_own_member_server_goals(self):
        own = await self.goal()
        await self.share(own)
        await self.goal()  # Unshared own goal.
        other = await self.goal(PLAYER)
        await self.share(other, "OTHER PLAYER SECRET")
        other_guild = await self.goal(guild=OTHER)
        async with self.db.transaction() as conn:
            await conn.execute("INSERT INTO web_player_goals VALUES (?,?,?)",
                               (str(OTHER), other_guild, "OTHER SERVER SECRET"))
        task = await self.goal(kind="task")
        with self.assertRaises(ValueError):
            await self.share(task)
        result = await self.read_player()
        self.assertEqual(len(result["goals"]), 1)
        self.assertEqual(set(result), {"goals", "page", "hasNext"})
        self.assertEqual(set(result["goals"][0]), {"id", "text", "status", "dueAt", "timezone"})
        self.assertEqual(result["goals"][0]["id"], own)
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("operations", result)
        await self.db.finish_staff_record(GUILD, ACTOR, "goal", own, "SECRET COMPLETION NOTE")
        result = await self.read_player()
        self.assertEqual(result["goals"][0]["status"], "completed")
        self.assertNotIn("SECRET", json.dumps(result))

    async def test_share_update_withdraw_and_confirmation_are_staff_only(self):
        own = await self.goal()
        with self.assertRaises(ValueError):
            await self.dispatch(kind="action", operation="goal.share",
                                values={"id": own, "text": "Safe player text"})
        await self.share(own)
        await self.share(own, "Updated observable action")
        self.assertEqual((await self.read_player())["goals"][0]["text"], "Updated observable action")
        await self.dispatch(kind="action", operation="goal.unshare", confirmed=True, values={"id": own})
        self.assertEqual((await self.read_player())["goals"], [])
        self.assertIsNotNone(await self.db.one("SELECT id FROM staff_records WHERE guild_id=? AND id=?",
                                             (str(GUILD), own)))
        with patch("scoutbot.web_bridge.current_context", AsyncMock(side_effect=AccessDenied("Not staff"))):
            for kind in ("action", "workspace"):
                with self.assertRaises(AccessDenied):
                    await self.bridge.dispatch({"kind": kind, "subject": SUBJECT, "guild": str(GUILD),
                                                "operation": "goal.share", "confirmed": True,
                                                "values": {"id": own, "text": "Forged"},
                                                "params": {"screen": "goals"}})
        audit = await self.db.all("SELECT action FROM audit_log WHERE action IN ('goal.share','goal.unshare')")
        self.assertEqual(len(audit), 3)

    async def test_player_scope_override_and_invalid_pagination_rejected(self):
        for params in ({"member": str(PLAYER)}, {"screen": "evaluations"}, {"snapshot": "staff"},
                       {"actor_id": PLAYER}, {"page": True}, {"page": 0}, {"page": 10001}):
            with self.assertRaises(ValueError):
                await self.read_player(**params)
        with self.assertRaises(AccessDenied):
            await self.bridge.dispatch({"kind": "player.development", "subject": "user_unlinked",
                                        "guild": str(GUILD), "actor_id": ACTOR, "params": {}})

    async def test_player_pages_are_bounded(self):
        for _ in range(21):
            await self.share(await self.goal())
        first, last = await self.read_player(), await self.read_player(page=2)
        self.assertEqual(len(first["goals"]), 20)
        self.assertTrue(first["hasNext"])
        self.assertEqual(len(last["goals"]), 1)
        self.assertFalse(last["hasNext"])
        self.assertTrue({r["id"] for r in first["goals"]}.isdisjoint({r["id"] for r in last["goals"]}))

    async def test_membership_is_fresh_individual_rest_without_staff_requirement(self):
        # Actor is neither owner nor staff; current membership alone is sufficient.
        ordinary = self.member(ACTOR, [])
        with patch.object(self.bot, "is_ready", return_value=True), \
                patch.object(self.bot, "fetch_guild", AsyncMock(return_value=self.guild)) as fetch_guild, \
                patch.object(discord.Guild, "fetch_member", AsyncMock(return_value=ordinary)) as fetch_member:
            for _ in range(2):
                context = await current_member_context(self.bot, GUILD, ACTOR)
                self.assertEqual(context.user.id, ACTOR)
            self.assertEqual(fetch_guild.await_count, 2)
            self.assertEqual(fetch_member.await_count, 4)
            fetch_member.assert_any_await(ACTOR)
            fetch_member.assert_any_await(self.bot.user.id)
            fetch_member.side_effect = AccessDenied("Membership revoked")
            with self.assertRaises(AccessDenied):
                await current_member_context(self.bot, GUILD, ACTOR)
        with patch.object(self.bot, "is_ready", return_value=False):
            with self.assertRaises(ConnectionError):
                await current_member_context(self.bot, GUILD, ACTOR)

    async def test_player_dispatch_rechecks_member_and_does_not_call_staff_authority(self):
        with patch("scoutbot.web_bridge.current_member_context",
                   AsyncMock(return_value=self.context)) as member, \
                patch("scoutbot.web_bridge.current_context", AsyncMock(side_effect=AssertionError("staff authority"))):
            await self.bridge.dispatch({"kind": "player.development", "subject": SUBJECT, "guild": str(GUILD)})
            await self.bridge.dispatch({"kind": "player.development", "subject": SUBJECT, "guild": str(GUILD)})
            self.assertEqual(member.await_count, 2)
            member.assert_awaited_with(self.bot, str(GUILD), ACTOR)
            member.side_effect = AccessDenied("Membership revoked")
            with self.assertRaises(AccessDenied):
                await self.bridge.dispatch({"kind": "player.development", "subject": SUBJECT, "guild": str(GUILD)})

    async def test_player_discovery_exposes_only_member_server_names(self):
        self.bot._connection._guilds[GUILD] = self.guild
        with patch.object(self.bot, "is_ready", return_value=True), \
                patch("scoutbot.web_bridge.current_member_context", AsyncMock(return_value=self.context)) as member:
            result = await self.bridge.dispatch({"kind": "player.access", "subject": SUBJECT})
            self.assertEqual(result["servers"], [{"id": str(GUILD), "name": self.guild.name}])
            member.side_effect = AccessDenied("No longer member")
            result = await self.bridge.dispatch({"kind": "player.access", "subject": SUBJECT})
            self.assertEqual(result["servers"], [])
            member.side_effect = ConnectionError("Discord unavailable")
            with self.assertRaises(ConnectionError):
                await self.bridge.dispatch({"kind": "player.access", "subject": SUBJECT})
