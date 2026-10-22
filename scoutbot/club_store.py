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
  cancelled INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0, revision_changed_at TEXT,
  revision_time_changed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_lookup ON club_events(guild_id,starts_at);
CREATE TABLE IF NOT EXISTS club_notification_settings (
  guild_id TEXT PRIMARY KEY, channel_id TEXT, lead_minutes INTEGER NOT NULL DEFAULT 1440,
  enabled_since TEXT, last_error TEXT, last_error_at TEXT
);
CREATE TABLE IF NOT EXISTS club_notification_deliveries (
  guild_id TEXT NOT NULL, delivery_key TEXT NOT NULL, event_id INTEGER NOT NULL,
  kind TEXT NOT NULL, revision INTEGER NOT NULL, starts_at TEXT NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1,
  last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(guild_id,delivery_key)
);
CREATE INDEX IF NOT EXISTS notification_delivery_status
  ON club_notification_deliveries(guild_id,status,updated_at);
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
    async def notification_settings(self, guild):
        row = await self.one(
            "SELECT * FROM club_notification_settings WHERE guild_id=?", (str(guild),))
        if row:
            return row
        return {
            "guild_id": str(guild), "channel_id": None, "lead_minutes": 1440,
            "enabled_since": None, "last_error": None, "last_error_at": None,
        }

    async def set_notification_settings(self, guild, actor, channel_id, lead_minutes):
        if not 5 <= lead_minutes <= 10080:
            raise ValueError("Reminder lead time must be between 5 minutes and 7 days.")
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT channel_id,enabled_since FROM club_notification_settings WHERE guild_id=?",
                (str(guild),)) as cur:
                previous = await cur.fetchone()
            enabled_since = None
            if channel_id is not None:
                enabled_since = stamp(utc_now())
                if (previous and previous["channel_id"] == str(channel_id)
                        and previous["enabled_since"] is not None):
                    enabled_since = previous["enabled_since"]
            await conn.execute(
                """INSERT INTO club_notification_settings
                (guild_id,channel_id,lead_minutes,enabled_since,last_error,last_error_at)
                VALUES (?,?,?,?,NULL,NULL)
                ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,
                lead_minutes=excluded.lead_minutes,enabled_since=excluded.enabled_since,
                last_error=NULL,last_error_at=NULL""",
                (str(guild), str(channel_id) if channel_id is not None else None,
                 lead_minutes, enabled_since))
            await self._audit(conn, guild, actor, "notifications.configured",
                              {"enabled": channel_id is not None, "lead_minutes": lead_minutes})

    async def notification_candidates(self, now=None):
        current_dt = now or utc_now()
        current = stamp(current_dt)
        retry_before = stamp(current_dt - timedelta(minutes=15))
        settings = await self.all(
            "SELECT * FROM club_notification_settings WHERE channel_id IS NOT NULL "
            "AND enabled_since IS NOT NULL")
        candidates = []
        for config in settings:
            guild = config["guild_id"]
            reminder_rows = await self.all(
                """SELECT e.* FROM club_events e
                WHERE e.guild_id=? AND e.cancelled=0 AND e.starts_at>?
                AND e.starts_at<=?
                AND NOT EXISTS (
                  SELECT 1 FROM club_notification_deliveries d
                  WHERE d.guild_id=e.guild_id AND d.delivery_key=
                    'reminder:' || e.id || ':' || e.starts_at
                    AND (d.status IN ('sent','sending','uncertain','obsolete')
                      OR (d.status='failed' AND d.updated_at>?))
                ) ORDER BY e.starts_at""",
                (guild, current, stamp(current_dt + timedelta(minutes=config["lead_minutes"])),
                 retry_before))
            for event in reminder_rows:
                candidates.append({
                    "guild_id": guild, "channel_id": config["channel_id"], "event": event,
                    "kind": "reminder",
                    "delivery_key": f'reminder:{event["id"]}:{event["starts_at"]}',
                })
            notice_rows = await self.all(
                """SELECT e.* FROM club_events e
                WHERE e.guild_id=? AND e.revision>0 AND e.revision_changed_at>=?
                AND NOT EXISTS (
                  SELECT 1 FROM club_notification_deliveries d
                  WHERE d.guild_id=e.guild_id
                  AND d.delivery_key='notice:' || e.id || ':' || e.revision
                    AND (d.status IN ('sent','sending','uncertain','obsolete')
                      OR (d.status='failed' AND d.updated_at>?))
                ) ORDER BY e.revision_changed_at""",
                (guild, config["enabled_since"], retry_before))
            for event in notice_rows:
                candidates.append({
                    "guild_id": guild, "channel_id": config["channel_id"], "event": event,
                    "kind": "cancelled" if event["cancelled"] else "edited",
                    "delivery_key": f'notice:{event["id"]}:{event["revision"]}',
                })
        return candidates

    async def claim_notification(self, guild, channel, event_id, kind, delivery_key,
                                 revision, starts_at, *, now=None):
        current = stamp(now or utc_now())
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM club_notification_settings WHERE guild_id=?", (str(guild),)) as cur:
                config = await cur.fetchone()
            async with conn.execute(
                "SELECT * FROM club_events WHERE guild_id=? AND id=?",
                (str(guild), event_id)) as cur:
                event = await cur.fetchone()
            valid_config = (config and config["channel_id"] == str(channel)
                            and config["enabled_since"] is not None)
            valid_event = event is not None and event["revision"] == revision
            if kind == "reminder":
                valid_event = bool(valid_event and not event["cancelled"]
                    and event["starts_at"] == starts_at and event["starts_at"] > current
                    and event["starts_at"] <= stamp(
                        (now or utc_now()) + timedelta(minutes=config["lead_minutes"])
                        if valid_config else utc_now()))
            else:
                expected_kind = "cancelled" if event and event["cancelled"] else "edited"
                valid_event = bool(valid_config and valid_event and expected_kind == kind
                                   and event["revision_changed_at"] is not None
                                   and event["revision_changed_at"] >= config["enabled_since"])
            async with conn.execute(
                "SELECT * FROM club_notification_deliveries WHERE guild_id=? AND delivery_key=?",
                (str(guild), delivery_key)) as cur:
                delivery = await cur.fetchone()
            if not valid_config or not valid_event:
                if delivery is None:
                    await conn.execute(
                        """INSERT INTO club_notification_deliveries
                        (guild_id,delivery_key,event_id,kind,revision,starts_at,status,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,'obsolete',?,?)""",
                        (str(guild), delivery_key, event_id, kind, revision, starts_at, current, current))
                elif delivery["status"] == "failed":
                    await conn.execute(
                        """UPDATE club_notification_deliveries SET status='obsolete',updated_at=?
                        WHERE guild_id=? AND delivery_key=?""",
                        (current, str(guild), delivery_key))
                return None
            if delivery and delivery["status"] in ("sent", "sending", "uncertain", "obsolete"):
                return None
            if delivery and delivery["updated_at"] > stamp((now or utc_now()) - timedelta(minutes=15)):
                return None
            if delivery:
                await conn.execute(
                    """UPDATE club_notification_deliveries SET status='sending',attempts=attempts+1,
                    last_error=NULL,updated_at=? WHERE guild_id=? AND delivery_key=?""",
                    (current, str(guild), delivery_key))
            else:
                await conn.execute(
                    """INSERT INTO club_notification_deliveries
                    (guild_id,delivery_key,event_id,kind,revision,starts_at,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,'sending',?,?)""",
                    (str(guild), delivery_key, event_id, kind, revision, starts_at, current, current))
            return True

    async def validate_notification(self, guild, channel, event_id, kind, delivery_key,
                                    revision, starts_at, *, now=None):
        current = stamp(now or utc_now())
        settings = await self.notification_settings(guild)
        event = await self.event(guild, event_id)
        valid = (settings["channel_id"] == str(channel)
                 and settings["enabled_since"] is not None)
        if kind == "reminder":
            valid = bool(valid and not event["cancelled"] and event["revision"] == revision
                         and event["starts_at"] == starts_at and event["starts_at"] > current
                         and event["starts_at"] <= stamp(
                             (now or utc_now()) + timedelta(minutes=settings["lead_minutes"])))
        else:
            expected_kind = "cancelled" if event["cancelled"] else "edited"
            valid = bool(valid and expected_kind == kind and event["revision"] == revision
                         and event["revision_changed_at"] >= settings["enabled_since"])
        delivery = await self.one(
            "SELECT status FROM club_notification_deliveries WHERE guild_id=? AND delivery_key=?",
            (str(guild), delivery_key))
        if not valid or not delivery or delivery["status"] != "sending":
            if delivery and delivery["status"] == "sending":
                async with self.transaction() as conn:
                    await conn.execute(
                        """UPDATE club_notification_deliveries SET status='obsolete',updated_at=?
                        WHERE guild_id=? AND delivery_key=? AND status='sending'""",
                        (current, str(guild), delivery_key))
            return None
        return event

    async def finish_notification(self, guild, delivery_key, *, status, error=None):
        if status not in ("sent", "failed", "uncertain"):
            raise ValueError("Invalid notification delivery status.")
        async with self.transaction() as conn:
            await conn.execute(
                """UPDATE club_notification_deliveries SET status=?,last_error=?,updated_at=?
                WHERE guild_id=? AND delivery_key=? AND status='sending'""",
                (status, str(error)[:500] if error else None, stamp(utc_now()),
                 str(guild), delivery_key))
            if error:
                await conn.execute(
                    """UPDATE club_notification_settings SET last_error=?,last_error_at=?
                    WHERE guild_id=?""",
                    (str(error)[:500], stamp(utc_now()), str(guild)))
            elif status == "sent":
                await conn.execute(
                    "UPDATE club_notification_settings SET last_error=NULL,last_error_at=NULL WHERE guild_id=?",
                    (str(guild),))

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
            meaningful = (changed or row["title"] != title.strip()
                          or row["timezone"] != timezone_name)
            if changed:
                await conn.execute("UPDATE club_roster SET response='pending',updated_at=? WHERE guild_id=? AND event_id=?",
                                   (stamp(utc_now()), str(guild), identifier))
            if meaningful:
                await conn.execute(
                    """UPDATE club_events SET revision=revision+1,revision_changed_at=?,
                    revision_time_changed=? WHERE guild_id=? AND id=?""",
                    (stamp(utc_now()), int(changed), str(guild), identifier))
            await self._audit(conn, guild, actor, "session.edited",
                              {"id": identifier, "reconfirmation_required": changed,
                               "notification_revision_created": meaningful})
        return changed

    async def cancel_event(self, guild, actor, identifier):
        async with self.transaction() as conn:
            cur = await conn.execute(
                """UPDATE club_events SET cancelled=1,revision=revision+1,
                revision_changed_at=?,revision_time_changed=0
                WHERE guild_id=? AND id=? AND cancelled=0""",
                (stamp(utc_now()), str(guild), identifier))
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
