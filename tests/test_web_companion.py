"""Website boundary tests use isolated SQLite and mocked individual Discord REST."""

import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord

from scoutbot.access import AccessDenied
from scoutbot.bot import ScoutBot
from scoutbot.club_domain import stamp, utc_now
from scoutbot.config import Settings
from scoutbot.database import Database
from scoutbot.web_bridge import WebBridge
from scoutbot.web_catalog import operations
from scoutbot.web_context import WebInteraction, current_context, invoke
from scoutbot.web_drafts import draft_action
from scoutbot.web_identity import IdentityLinks, WEB_SCHEMA
from scoutbot.web_workspace import workspace

GUILD = 111111111111111111
OTHER = 222222222222222222
ACTOR = 333333333333333333
PLAYER = 444444444444444444
SUBJECT = "user_test_isolated"


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.bot = ScoutBot(Settings(Path(self.directory.name) / "club.db", None, False, "INFO"))
        self.bot.register_commands()
        await self.bot.db.open()
        await self.bot.db.conn.executescript(WEB_SCHEMA)
        self.db = self.bot.db
        self.bridge = self.bot.web_bridge
        self.bot._connection.user = discord.ClientUser(
            state=self.bot._connection, data={"id": "999999999999999999", "username": "Test bot", "discriminator": "0", "avatar": None})
        self.guild = discord.Guild(state=self.bot._connection, data={
            "id": str(GUILD), "name": "Isolated Test Club", "owner_id": str(PLAYER),
            "roles": [{"id": str(GUILD), "name": "@everyone", "permissions": "0", "position": 0},
                      {"id": "555555555555555555", "name": "Managers", "permissions": "32", "position": 1}]})
        self.actor = self.member(ACTOR, ["555555555555555555"])
        self.player = self.member(PLAYER, [])
        self.context = WebInteraction(self.bot, self.guild, self.actor)
        await self.bridge.identities.redeem((await self.bridge.identities.challenge(SUBJECT))["code"], self.actor, GUILD)

    async def asyncTearDown(self):
        await self.bot.close()
        self.directory.cleanup()

    async def dispatch(self, **kwargs):
        request = {"subject": SUBJECT, "guild": str(GUILD), **kwargs}
        with patch("scoutbot.web_bridge.current_context", AsyncMock(return_value=self.context)), \
                patch.object(discord.Guild, "fetch_member", AsyncMock(return_value=self.player)):
            return await self.bridge.dispatch(request)

    def member(self, identifier, roles):
        return discord.Member(data={"user": {"id": str(identifier), "username": "Test member",
                                             "discriminator": "0", "avatar": None},
                                    "roles": roles, "joined_at": None, "flags": 0},
                              guild=self.guild, state=self.bot._connection)

    async def test_challenge_single_use_expiry_and_no_identity_injection(self):
        links = IdentityLinks(self.db)
        code = (await links.challenge("user_new"))["code"]
        await links.redeem(code, self.actor, GUILD)
        self.assertEqual(await links.actor_id("user_new"), ACTOR)
        with self.assertRaises(ValueError):
            await links.redeem(code, self.actor, GUILD)
        code = (await links.challenge("user_expired"))["code"]
        subject, _ = links.challenges[code]
        links.challenges[code] = (subject, 0)
        with self.assertRaises(ValueError):
            await links.redeem(code, self.actor, GUILD)
        with self.assertRaises(AccessDenied):
            await self.bridge.dispatch({"kind": "workspace", "subject": "user_unlinked",
                                        "guild": str(GUILD), "actor_id": str(ACTOR), "params": {"screen": "overview"}})
        self.assertIsNone(await links.actor_id("user_unlinked"))

    async def test_no_silent_relink_or_first_signup_admin(self):
        new_actor = SimpleNamespace(id=PLAYER)
        code = (await self.bridge.identities.challenge(SUBJECT))["code"]
        with self.assertRaises(ValueError):
            await self.bridge.identities.redeem(code, new_actor, GUILD)
        self.assertEqual(await self.bridge.identities.actor_id(SUBJECT), ACTOR)
        self.assertIsNone(await self.db.settings(GUILD))

    async def test_every_request_reauthorizes_and_denies_revocation(self):
        request = {"kind": "workspace", "subject": SUBJECT, "guild": str(GUILD), "params": {"screen": "overview"}}
        with patch("scoutbot.web_bridge.current_context", AsyncMock(return_value=self.context)) as authorize:
            await self.bridge.dispatch(request)
            await self.bridge.dispatch(request)
            self.assertEqual(authorize.await_count, 2)
            authorize.side_effect = AccessDenied("Revoked")
            with self.assertRaises(AccessDenied):
                await self.bridge.dispatch(request)

    async def test_catalog_keeps_manager_actions_private(self):
        staff = {o["id"] for o in operations(self.bot, "setup", False)}
        self.assertNotIn("scout setup", staff)
        self.assertNotIn("scout stage_role", staff)
        self.assertNotIn("promotion policy", staff)
        self.context.user = self.member(ACTOR, [])
        for name in ("scout setup", "scout stage_role", "promotion policy", "setup.verify"):
            with self.assertRaises(AccessDenied):
                await self.dispatch(kind="action", operation=name, values={})

    async def test_no_raw_sql_actor_or_unknown_actions(self):
        for name in ("sql", "scout evaluate", "start_panel", "scout docket"):
            with self.assertRaises(AccessDenied):
                await self.dispatch(kind="action", operation=name, values={"sql": "DROP TABLE evaluations"})
        with self.assertRaises(ValueError):
            await invoke(self.bot, self.context, "schedule create", {"actor_id": PLAYER}, False)

    async def test_isolated_bounded_records_and_server_filtering(self):
        now = utc_now()
        for index in range(25):
            await self.db.create_event(GUILD, ACTOR, f"Practice {index}", "training",
                                       stamp(now + timedelta(days=index + 1)), stamp(now + timedelta(days=index + 1, hours=1)), "UTC")
        await self.db.create_event(OTHER, ACTOR, "Other private session", "trial",
                                   stamp(now + timedelta(days=1)), stamp(now + timedelta(days=1, hours=1)), "UTC")
        result = await workspace(self.bot, self.context, {"screen": "schedule"})
        self.assertEqual(len(result["rows"]), 20)
        self.assertTrue(result["hasNext"])
        self.assertNotIn("Other private session", json.dumps(result))
        last = await workspace(self.bot, self.context, {"screen": "schedule", "page": 2})
        self.assertEqual(len(last["rows"]), 5)
        self.assertFalse(last["hasNext"])
        filtered = await workspace(self.bot, self.context, {"screen": "schedule", "search": "Practice 24"})
        self.assertEqual(len(filtered["rows"]), 1)
        with self.assertRaises(ValueError):
            await workspace(self.bot, self.context, {"screen": "schedule", "page": 0})

    async def test_schedule_command_adapter_shared_writes_reconfirmation_and_confirmation(self):
        date = (utc_now() + timedelta(days=2)).date().isoformat()
        await self.dispatch(kind="action", operation="schedule create", values={
            "title": "Website training", "kind": "training", "date": date, "time": "20:00",
            "timezone": "UTC", "minutes": 90})
        row = await self.db.one("SELECT * FROM club_events WHERE guild_id=?", (str(GUILD),))
        self.assertEqual(row["created_by"], str(ACTOR))
        await self.db.rsvp(GUILD, PLAYER, row["id"], "yes")
        self.context.messages.clear()
        await self.dispatch(kind="action", operation="schedule edit", confirmed=True, values={
            "session_id": row["id"], "title": "New time", "date": date, "time": "21:00", "timezone": "UTC"})
        roster = await self.db.one("SELECT response FROM club_roster WHERE guild_id=?", (str(GUILD),))
        self.assertEqual(roster["response"], "pending")
        with self.assertRaises(ValueError):
            await self.dispatch(kind="action", operation="schedule cancel", values={"session_id": row["id"]})
        await self.dispatch(kind="action", operation="schedule cancel", confirmed=True, values={"session_id": row["id"]})
        self.assertEqual((await self.db.event(GUILD, row["id"]))["cancelled"], 1)
        audit = await self.db.all("SELECT actor_id FROM audit_log WHERE guild_id=? AND action IN ('session.created','session.edited','session.cancelled')", (str(GUILD),))
        self.assertTrue(audit)
        self.assertTrue(all(r["actor_id"] == str(ACTOR) for r in audit))

    async def test_adapter_validates_choices_ranges_and_ids(self):
        base = {"title": "Training", "kind": "training", "date": "2099-01-01", "time": "21:00", "timezone": "UTC"}
        for values in ({**base, "minutes": 1}, {**base, "minutes": 15.5}, {**base, "kind": "invented"}):
            with self.assertRaises(ValueError):
                await self.dispatch(kind="action", operation="schedule create", values=values)
        self.assertEqual(await self.db.all("SELECT * FROM club_events"), [])

    async def test_drafts_actor_scope_revisions_borderline_and_save(self):
        with patch.object(discord.Guild, "fetch_member", AsyncMock(return_value=self.player)):
            result = await draft_action(self.bot, self.context, "draft.start", {"candidate": str(PLAYER), "position": "Defender"}, False)
        row = result["draft"]
        payload = {"categories": {c["key"]: {"score": "7.0", "evidence": "Observed consistent action"} for c in row["categories"]},
                   "count": 3, "context": "Training against a matched side", "notes": "Private observation"}
        await draft_action(self.bot, self.context, "draft.correct", {"id": row["id"], "revision": 0, "payload": payload}, False)
        with self.assertRaises(ValueError):
            await draft_action(self.bot, self.context, "draft.save", {"id": row["id"], "revision": 1}, True)
        with self.assertRaises(ValueError):
            await self.db.draft(OTHER, ACTOR, row["id"])
        with self.assertRaises(ValueError):
            await self.db.draft(GUILD, PLAYER, row["id"])
        for value in payload["categories"].values():
            value["score"] = "8.0"
        await draft_action(self.bot, self.context, "draft.correct", {"id": row["id"], "revision": 1, "payload": payload}, False)
        with self.assertRaises(ValueError):
            await draft_action(self.bot, self.context, "draft.save", {"id": row["id"], "revision": 1}, True)
        await draft_action(self.bot, self.context, "draft.save", {"id": row["id"], "revision": 2}, True)
        history = await self.db.history(GUILD, PLAYER)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["score"], 8.0)

    async def test_snapshots_bound_to_subject_guild_and_expiry(self):
        view = SimpleNamespace(items=[discord.Embed(title="Private evidence", description="Isolated text")])
        token = await self.bridge.remember_snapshot(SUBJECT, str(GUILD), view)
        for subject, guild in (("user_other", str(GUILD)), (SUBJECT, str(OTHER))):
            with self.assertRaises(AccessDenied):
                await self.bridge.snapshot_page(subject, guild, self.context, {"snapshot": token})
        self.bridge.snapshots[token]["expires"] = 0
        await self.bridge.cleanup()
        with self.assertRaises(AccessDenied):
            await self.bridge.snapshot_page(SUBJECT, str(GUILD), self.context, {"snapshot": token})

    async def test_socket_is_local_private_and_rejects_unverified_actor(self):
        path = Path(self.directory.name) / "bridge" / "web.sock"
        with patch("scoutbot.web_bridge.socket_path", return_value=path):
            await self.bridge.start()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(json.dumps({"kind": "workspace", "guild": str(GUILD), "actor_id": ACTOR}).encode() + b"\n")
            await writer.drain()
            result = json.loads(await reader.readline())
            self.assertEqual(result["status"], 403)
            writer.close()
            await writer.wait_closed()
            await self.bridge.close()
            self.assertFalse(path.exists())

    async def test_leave_review_and_attendance_protections_share_records(self):
        now = utc_now()
        session = await self.db.create_event(GUILD, ACTOR, "Attendance fixture", "training",
                                             stamp(now + timedelta(days=1)), stamp(now + timedelta(days=1, hours=1)), "UTC")
        await self.db.rsvp(GUILD, PLAYER, session, "yes")
        # Only isolated fixture data is moved into the past; no real sessions.
        async with self.db.transaction() as conn:
            await conn.execute("UPDATE club_events SET starts_at=?,ends_at=? WHERE id=?",
                               (stamp(now - timedelta(hours=2)), stamp(now - timedelta(hours=1)), session))
        initial = await self.db.attendance_summary(GUILD, PLAYER, 30)
        self.assertEqual(initial["unmarked"], 1)
        await self.dispatch(kind="action", operation="attendance mark",
                            values={"session_id": session, "member": str(PLAYER), "status": "absent"})
        leave = await self.db.request_leave(GUILD, PLAYER, stamp(now - timedelta(days=1)),
                                            stamp(now + timedelta(days=1)), "UTC")
        await self.dispatch(kind="action", operation="availability review_leave", confirmed=True,
                            values={"leave_id": leave, "approved": True})
        protected = await self.db.attendance_summary(GUILD, PLAYER, 30)
        self.assertEqual(protected["absent"], 0)
        self.assertEqual(protected["leave"], 1)
        with self.assertRaises(ValueError):
            await self.db.review_leave(OTHER, ACTOR, leave, False)

    async def test_concurrent_discord_and_web_writes_keep_one_completion_and_audit(self):
        identifier = await self.db.save_staff_record(GUILD, ACTOR, "task", ACTOR, "Concurrent fixture work",
                                                     stamp(utc_now() + timedelta(days=1)), "UTC")
        async def web():
            return await self.dispatch(kind="action", operation="assistant task complete", confirmed=True,
                                       values={"task_id": identifier, "outcome": "Website outcome"})
        await asyncio.gather(web(), self.db.finish_staff_record(GUILD, PLAYER, "task", identifier, "Discord outcome"))
        row = await self.db.one("SELECT * FROM staff_records WHERE guild_id=? AND id=?", (str(GUILD), identifier))
        self.assertIn(row["outcome"], {"Website outcome", "Discord outcome"})
        audits = await self.db.all("SELECT * FROM audit_log WHERE guild_id=? AND action='task.completed'", (str(GUILD),))
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["actor_id"], row["completed_by"])

    async def test_all_main_screens_have_real_bounded_read_contracts(self):
        for screen in ("overview", "setup", "schedule", "availability", "attendance", "scouting",
                       "evaluations", "recruitment", "goals", "tasks", "briefing", "audit"):
            response = await workspace(self.bot, self.context, {"screen": screen})
            self.assertLessEqual(len(response["rows"]), 20)
            self.assertIn("live", response)
            self.assertIn("operations", response)

    async def test_current_authorization_uses_fresh_roles_owner_and_member(self):
        # Real discord.py objects prove permission computation uses current role
        # data, not client-side flags or the bot gateway's cached role grants.
        state = self.bot._connection
        payload = {"id": str(GUILD), "name": "Fresh guild", "owner_id": str(PLAYER),
                   "roles": [{"id": str(GUILD), "name": "@everyone", "permissions": "0", "position": 0},
                             {"id": "555555555555555555", "name": "Managers", "permissions": "32", "position": 1}]}
        guild = discord.Guild(data=payload, state=state)
        def member(identifier, roles):
            return discord.Member(data={"user": {"id": str(identifier), "username": "Member", "discriminator": "0", "avatar": None},
                                        "roles": roles, "joined_at": None, "flags": 0}, guild=guild, state=state)
        manager = member(ACTOR, ["555555555555555555"])
        bot_member = member(state.user.id, [])
        guild_class = type(guild)
        bot = SimpleNamespace(is_ready=lambda: True, fetch_guild=AsyncMock(return_value=guild),
                              user=state.user, db=self.db)
        with patch.object(guild_class, "fetch_member", AsyncMock(side_effect=[manager, bot_member])):
            allowed = await current_context(bot, GUILD, ACTOR)
            self.assertTrue(allowed.user.guild_permissions.manage_guild)
        revoked = member(ACTOR, [])
        with patch.object(guild_class, "fetch_member", AsyncMock(side_effect=[revoked, bot_member])):
            with self.assertRaises(AccessDenied):
                await current_context(bot, GUILD, ACTOR)
