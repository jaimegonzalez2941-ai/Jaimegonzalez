"""Club operations persistence; mixed into the existing serialized repository."""

from datetime import timedelta

from .club_domain import DEFAULT_POLICY, ATTENDANCE, utc_now, stamp, summarize, readiness, clock, zone

CLUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS club_policy (
 guild_id TEXT PRIMARY KEY, days INTEGER NOT NULL, minimum INTEGER NOT NULL,
 percent INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS availability_windows (
 guild_id TEXT NOT NULL, member_id TEXT NOT NULL, weekday INTEGER NOT NULL,
 start_min INTEGER NOT NULL, end_min INTEGER NOT NULL, timezone TEXT NOT NULL,
 PRIMARY KEY(guild_id,member_id,weekday)
);
CREATE TABLE IF NOT EXISTS club_leave (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, member_id TEXT NOT NULL,
 starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, timezone TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS leave_lookup ON club_leave(guild_id,member_id,starts_at,ends_at);
CREATE TABLE IF NOT EXISTS club_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, title TEXT NOT NULL,
 kind TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, timezone TEXT NOT NULL,
 cancelled INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_lookup ON club_events(guild_id,starts_at);
CREATE TABLE IF NOT EXISTS club_roster (
 guild_id TEXT NOT NULL, event_id INTEGER NOT NULL, member_id TEXT NOT NULL,
 response TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(guild_id,event_id,member_id),
 FOREIGN KEY(event_id) REFERENCES club_events(id)
);
CREATE TABLE IF NOT EXISTS club_attendance (
 guild_id TEXT NOT NULL, event_id INTEGER NOT NULL, member_id TEXT NOT NULL,
 status TEXT NOT NULL, marked_by TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(guild_id,event_id,member_id),
 FOREIGN KEY(event_id) REFERENCES club_events(id)
);
CREATE TABLE IF NOT EXISTS club_branding (bot_id TEXT PRIMARY KEY, digest TEXT NOT NULL);
"""


class ClubStore:
    async def policy(self, guild):
        return await self.one("SELECT days,minimum,percent FROM club_policy WHERE guild_id=?",
                              (str(guild),)) or dict(DEFAULT_POLICY)

    async def set_policy(self, guild, actor, days, minimum, percent):
        if not (1 <= days <= 365 and 1 <= minimum <= 100 and 1 <= percent <= 100):
            raise ValueError("Policy limits: 1–365 days, 1–100 sessions, 1–100 percent.")
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO club_policy VALUES (?,?,?,?) ON CONFLICT(guild_id) DO UPDATE "
                "SET days=excluded.days,minimum=excluded.minimum,percent=excluded.percent",
                (str(guild), days, minimum, percent))
            await self._audit(conn, guild, actor, "promotion.policy", dict(days=days, minimum=minimum, percent=percent))

    async def set_availability(self, guild, member, weekday, start, end, timezone_name):
        if weekday not in range(7):
            raise ValueError("Choose a valid weekday.")
        beginning = clock(start)
        ending = 1440 if end.strip() == "24:00" else clock(end)
        zone(timezone_name)
        if ending <= beginning:
            raise ValueError("End time must be after start time on the same day. Split overnight windows across days.")
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO availability_windows VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(guild_id,member_id,weekday) DO UPDATE SET "
                "start_min=excluded.start_min,end_min=excluded.end_min,timezone=excluded.timezone",
                (str(guild), str(member), weekday, beginning, ending, timezone_name))
            await self._audit(conn, guild, member, "availability.set", {"weekday": weekday})

    async def remove_availability(self, guild, member, weekday):
        async with self.transaction() as conn:
            await conn.execute("DELETE FROM availability_windows WHERE guild_id=? AND member_id=? AND weekday=?",
                               (str(guild), str(member), weekday))
            await self._audit(conn, guild, member, "availability.removed", {"weekday": weekday})

    async def request_leave(self, guild, member, start, end, timezone_name):
        zone(timezone_name)
        if end <= start:
            raise ValueError("Invalid leave dates.")
        async with self.transaction() as conn:
            cursor = await conn.execute(
                "INSERT INTO club_leave(guild_id,member_id,starts_at,ends_at,timezone) VALUES (?,?,?,?,?)",
                (str(guild), str(member), start, end, timezone_name))
            identifier = cursor.lastrowid
            await cursor.close()
            await self._audit(conn, guild, member, "leave.requested", {"id": identifier})
        return identifier

    async def review_leave(self, guild, actor, identifier, approved):
        async with self.transaction() as conn:
            async with conn.execute("SELECT * FROM club_leave WHERE guild_id=? AND id=?",
                                    (str(guild), identifier)) as cur:
                row = await cur.fetchone()
            if not row or row["status"] == "cancelled":
                raise ValueError("No reviewable leave request with that ID in this server.")
            status = "approved" if approved else "declined"
            await conn.execute("UPDATE club_leave SET status=? WHERE guild_id=? AND id=?",
                               (status, str(guild), identifier))
            await self._audit(conn, guild, actor, "leave.reviewed",
                              {"id": identifier, "status": status, "member": row["member_id"]})

    async def cancel_leave(self, guild, member, identifier):
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM club_leave WHERE guild_id=? AND member_id=? AND id=?",
                (str(guild), str(member), identifier)) as cur:
                row = await cur.fetchone()
            if not row:
                raise ValueError("That is not your leave request in this server.")
            if row["status"] == "approved" and row["starts_at"] <= stamp(utc_now()):
                raise ValueError("Ask staff to change approved leave that has already started.")
            await conn.execute("UPDATE club_leave SET status='cancelled' WHERE guild_id=? AND id=?",
                               (str(guild), identifier))
            await self._audit(conn, guild, member, "leave.cancelled", {"id": identifier})

    async def event(self, guild, identifier):
        row = await self.one("SELECT * FROM club_events WHERE guild_id=? AND id=?",
                             (str(guild), identifier))
        if not row:
            raise ValueError("No session with that ID in this server.")
        return row

    async def create_event(self, guild, actor, title, kind, start, end, timezone_name):
        if not 1 <= len(title.strip()) <= 100 or kind not in ("training", "match", "trial"):
            raise ValueError("Use a 1–100 character title and training, match, or trial.")
        if not stamp(utc_now()) < start < end:
            raise ValueError("Sessions must start in the future and end after they start.")
        zone(timezone_name)
        async with self.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO club_events(guild_id,title,kind,starts_at,ends_at,timezone,created_by) VALUES (?,?,?,?,?,?,?)",
                (str(guild), title.strip(), kind, start, end, timezone_name, str(actor)))
            identifier = cur.lastrowid
            await cur.close()
            await self._audit(conn, guild, actor, "session.created", {"id": identifier})
        return identifier

    async def edit_event(self, guild, actor, identifier, title, start, end, timezone_name):
        zone(timezone_name)
        if not 1 <= len(title.strip()) <= 100 or not stamp(utc_now()) < start < end:
            raise ValueError("Use a 1–100 character title and future session times.")
        async with self.transaction() as conn:
            async with conn.execute("SELECT * FROM club_events WHERE guild_id=? AND id=?",
                                    (str(guild), identifier)) as cur:
                row = await cur.fetchone()
            if not row or row["cancelled"] or row["starts_at"] <= stamp(utc_now()):
                raise ValueError("Only an upcoming, active session can be edited.")
            await conn.execute(
                "UPDATE club_events SET title=?,starts_at=?,ends_at=?,timezone=? WHERE guild_id=? AND id=?",
                (title.strip(), start, end, timezone_name, str(guild), identifier))
            changed = row["starts_at"] != start or row["ends_at"] != end
            if changed:
                await conn.execute("UPDATE club_roster SET response='pending',updated_at=? WHERE guild_id=? AND event_id=?",
                                   (stamp(utc_now()), str(guild), identifier))
            await self._audit(conn, guild, actor, "session.edited",
                              {"id": identifier, "reconfirmation_required": changed})
        return changed

    async def cancel_event(self, guild, actor, identifier):
        async with self.transaction() as conn:
            cur = await conn.execute("UPDATE club_events SET cancelled=1 WHERE guild_id=? AND id=?",
                                     (str(guild), identifier))
            changed = cur.rowcount
            await cur.close()
            if not changed:
                raise ValueError("No session with that ID in this server.")
            await self._audit(conn, guild, actor, "session.cancelled", {"id": identifier})

    async def rsvp(self, guild, member, identifier, response, *, actor=None):
        if response not in ("yes", "maybe", "no", "assigned", "pending"):
            raise ValueError("Invalid RSVP.")
        if response in ("assigned", "pending") and actor is None:
            raise ValueError("Only staff can assign or release players.")
        async with self.transaction() as conn:
            async with conn.execute("SELECT * FROM club_events WHERE guild_id=? AND id=?",
                                    (str(guild), identifier)) as cur:
                event = await cur.fetchone()
            if not event or event["cancelled"] or event["starts_at"] <= stamp(utc_now()):
                raise ValueError("RSVPs and assignments close when the session starts or is cancelled.")
            async with conn.execute(
                "SELECT response FROM club_roster WHERE guild_id=? AND event_id=? AND member_id=?",
                (str(guild), identifier, str(member))) as cur:
                old = await cur.fetchone()
            if actor is None and old and old["response"] == "assigned" and response != "yes":
                raise ValueError("Staff assigned you to this session. Ask staff to release you, or request leave.")
            if actor is None and old and old["response"] == "assigned":
                response = "assigned"  # Acknowledging cannot turn an assignment into a removable RSVP.
            await conn.execute(
                "INSERT INTO club_roster VALUES (?,?,?,?,?) ON CONFLICT(guild_id,event_id,member_id) "
                "DO UPDATE SET response=excluded.response,updated_at=excluded.updated_at",
                (str(guild), identifier, str(member), response, stamp(utc_now())))
            await self._audit(conn, guild, actor or member, "session.rsvp",
                              {"id": identifier, "member": str(member), "response": response})

    async def mark_attendance(self, guild, actor, identifier, member, status):
        if status not in ATTENDANCE:
            raise ValueError("Invalid attendance status.")
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT e.starts_at,e.cancelled,r.response FROM club_events e JOIN club_roster r "
                "ON r.guild_id=e.guild_id AND r.event_id=e.id WHERE e.guild_id=? AND e.id=? AND r.member_id=?",
                (str(guild), identifier, str(member))) as cur:
                row = await cur.fetchone()
            if not row or row["response"] not in ("yes", "assigned"):
                raise ValueError("Only players who committed or were assigned before kickoff are eligible.")
            if row["cancelled"] or row["starts_at"] > stamp(utc_now()):
                raise ValueError("Attendance can only be marked after an active session starts.")
            await conn.execute(
                "INSERT INTO club_attendance VALUES (?,?,?,?,?,?) ON CONFLICT(guild_id,event_id,member_id) "
                "DO UPDATE SET status=excluded.status,marked_by=excluded.marked_by,updated_at=excluded.updated_at",
                (str(guild), identifier, str(member), status, str(actor), stamp(utc_now())))
            await self._audit(conn, guild, actor, "attendance.marked",
                              {"id": identifier, "member": str(member), "status": status})

    async def attendance_summary(self, guild, member, days, *, now=None):
        now = now or utc_now()
        rows = await self.all(
            """SELECT a.status,EXISTS(
                SELECT 1 FROM club_leave l WHERE l.guild_id=e.guild_id AND l.member_id=r.member_id
                AND l.status='approved' AND l.starts_at<e.ends_at AND l.ends_at>e.starts_at
            ) AS on_leave
            FROM club_events e JOIN club_roster r ON r.guild_id=e.guild_id AND r.event_id=e.id
            LEFT JOIN club_attendance a ON a.guild_id=r.guild_id AND a.event_id=r.event_id AND a.member_id=r.member_id
            WHERE e.guild_id=? AND r.member_id=? AND e.cancelled=0
            AND e.starts_at>=? AND e.ends_at<=? AND r.response IN ('yes','assigned')""",
            (str(guild), str(member), stamp(now - timedelta(days=days)), stamp(now)))
        return summarize(rows)

    async def promotion_evidence(self, guild, member):
        policy = await self.policy(guild)
        summary = await self.attendance_summary(guild, member, policy["days"])
        evaluation = await self.one(
            "SELECT * FROM evaluations WHERE guild_id=? AND candidate_id=? AND created_at>=? ORDER BY id DESC LIMIT 1",
            (str(guild), str(member), stamp(utc_now() - timedelta(days=policy["days"]))))
        ready, reasons = readiness(summary, policy, evaluation)
        return {"policy": policy, "attendance": summary, "evaluation": evaluation,
                "ready": ready, "reasons": reasons}
