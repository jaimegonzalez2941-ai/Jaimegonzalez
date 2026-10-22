"""Additive staff workspace records, using the repository's serialized transactions."""

from datetime import datetime, timedelta

from .club_domain import local_datetime, stamp, utc_now, zone
from .domain import POSITIONS

ASSISTANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_records (
 guild_id TEXT NOT NULL, id INTEGER NOT NULL, kind TEXT NOT NULL
 CHECK(kind IN ('recruitment','goal','task')),
 member_id TEXT, position TEXT, owner_id TEXT NOT NULL,
 description TEXT NOT NULL, criteria TEXT NOT NULL DEFAULT '',
 due_at TEXT NOT NULL, timezone TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed','archived')),
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 completed_by TEXT, completed_at TEXT, outcome TEXT,
 PRIMARY KEY(guild_id,id)
);
CREATE UNIQUE INDEX IF NOT EXISTS active_shortlist
 ON staff_records(guild_id,member_id,position)
 WHERE kind='recruitment' AND status='active';
CREATE INDEX IF NOT EXISTS staff_deadlines ON staff_records(guild_id,kind,status,due_at);
"""


def bounded(value, label, maximum=1000, minimum=3):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{label} must contain {minimum}–{maximum} characters.")
    return value.strip()


def deadline(date, time, timezone):
    """Explicit local deadlines share scheduling's DST ambiguity protections."""
    return stamp(local_datetime(date, time, timezone))


def validated_deadline(due_at, timezone):
    zone(timezone)
    try:
        parsed = datetime.fromisoformat(due_at)
        if parsed.tzinfo is None:
            raise ValueError
        return stamp(parsed)
    except (TypeError, ValueError):
        raise ValueError("A timezone-aware deadline is required.") from None


def validate_owner(owner):
    if not str(owner).isascii() or not str(owner).isdecimal() or int(owner) <= 0:
        raise ValueError("A staff owner is required.")


class AssistantStore:
    async def save_staff_record(self, guild, actor, kind, owner, description, due_at,
                               timezone, *, member=None, position=None, criteria=""):
        if kind not in ("recruitment", "goal", "task"):
            raise ValueError("Unknown staff record kind.")
        description = bounded(description, "Next action or description")
        if kind in ("recruitment", "goal") and member is None:
            raise ValueError("Choose a player.")
        if kind == "recruitment" and position not in POSITIONS:
            raise ValueError("Choose a supported target position.")
        if kind == "goal":
            criteria = bounded(criteria, "Observable success criteria")
        due_at = validated_deadline(due_at, timezone)
        validate_owner(owner)
        now = stamp(utc_now())
        async with self.transaction() as conn:
            old = None
            if kind == "recruitment":
                async with conn.execute(
                    """SELECT * FROM staff_records WHERE guild_id=? AND kind='recruitment'
                    AND member_id=? AND position=? AND status='active'""",
                    (str(guild), str(member), position),
                ) as cur:
                    old = await cur.fetchone()
            if old:
                identifier = old["id"]
                await conn.execute(
                    """UPDATE staff_records SET owner_id=?,description=?,due_at=?,timezone=?,
                    updated_at=? WHERE guild_id=? AND id=?""",
                    (str(owner), description, due_at, timezone, now, str(guild), identifier))
            else:
                async with conn.execute(
                    "SELECT COALESCE(MAX(id),0)+1 FROM staff_records WHERE guild_id=?",
                    (str(guild),),
                ) as cur:
                    identifier = (await cur.fetchone())[0]
                await conn.execute(
                    """INSERT INTO staff_records
                    (guild_id,id,kind,member_id,position,owner_id,description,criteria,
                    due_at,timezone,created_by,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(guild), identifier, kind, str(member) if member is not None else None,
                     position, str(owner), description, criteria, due_at, timezone,
                     str(actor), now, now))
            await self._audit(conn, guild, actor, f"{kind}.{'updated' if old else 'created'}",
                              {"id": identifier, "before": dict(old) if old else None,
                               "owner": str(owner), "member": str(member), "position": position,
                               "description": description, "criteria": criteria,
                               "due_at": due_at, "timezone": timezone})
        return identifier

    async def edit_staff_record(self, guild, actor, kind, identifier, *,
                               owner=None, due_at=None, timezone=None):
        """Edit unfinished work in place; eligibility of a new owner is checked by commands."""
        if kind not in ("goal", "task"):
            raise ValueError("Only development goals and staff tasks can be edited here.")
        if owner is None and due_at is None and timezone is None:
            raise ValueError("Choose a new staff owner or deadline.")
        if owner is not None:
            validate_owner(owner)
        if (due_at is None) != (timezone is None):
            raise ValueError("Supply both a deadline and its timezone.")
        if due_at is not None:
            due_at = validated_deadline(due_at, timezone)
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM staff_records WHERE guild_id=? AND kind=? AND id=?",
                (str(guild), kind, identifier),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise ValueError("No matching record with that ID in this server.")
            if row["status"] != "active":
                raise ValueError("Completed records cannot be edited; their history is preserved.")
            before = dict(row)
            after = before | {
                "owner_id": str(owner) if owner is not None else row["owner_id"],
                "due_at": due_at if due_at is not None else row["due_at"],
                "timezone": timezone if timezone is not None else row["timezone"],
            }
            if after == before:
                return False
            after["updated_at"] = stamp(utc_now())
            await conn.execute(
                """UPDATE staff_records SET owner_id=?,due_at=?,timezone=?,updated_at=?
                WHERE guild_id=? AND kind=? AND id=?""",
                (after["owner_id"], after["due_at"], after["timezone"], after["updated_at"],
                 str(guild), kind, identifier))
            await self._audit(conn, guild, actor, f"{kind}.updated",
                              {"id": identifier, "before": before, "after": after})
            return True

    async def finish_staff_record(self, guild, actor, kind, identifier, outcome=None):
        if kind not in ("recruitment", "goal", "task"):
            raise ValueError("Unknown staff record kind.")
        if kind != "recruitment":
            outcome = bounded(outcome, "Completion outcome note")
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM staff_records WHERE guild_id=? AND kind=? AND id=?",
                (str(guild), kind, identifier),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise ValueError("No matching record with that ID in this server.")
            if row["status"] != "active":
                return False
            status = "archived" if kind == "recruitment" else "completed"
            now = stamp(utc_now())
            await conn.execute(
                """UPDATE staff_records SET status=?,completed_by=?,completed_at=?,outcome=?,
                updated_at=? WHERE guild_id=? AND id=?""",
                (status, str(actor), now, outcome, now, str(guild), identifier))
            await self._audit(conn, guild, actor, f"{kind}.{status}",
                              {"id": identifier, "outcome": outcome, "before": dict(row)})
            return True

    async def staff_records(self, guild, kind, status="active", *, member=None, now=None):
        if kind not in ("recruitment", "goal", "task"):
            raise ValueError("Unknown staff record kind.")
        if status not in ("active", "overdue", "completed", "archived", "all"):
            raise ValueError("Unknown record filter.")
        sql, args = "SELECT * FROM staff_records WHERE guild_id=? AND kind=?", [str(guild), kind]
        if status != "all":
            sql += " AND status=?"
            args.append("active" if status == "overdue" else status)
        if status == "overdue":
            sql += " AND due_at<?"
            args.append(stamp(now or utc_now()))
        if member is not None:
            sql += " AND member_id=?"
            args.append(str(member))
        return await self.all(sql + " ORDER BY due_at,id", args)

    async def latest_position_evaluation(self, guild, member, position):
        if position not in POSITIONS:
            raise ValueError("Choose a supported position.")
        return await self.one(
            """SELECT * FROM evaluations WHERE guild_id=? AND candidate_id=? AND position=?
            ORDER BY id DESC LIMIT 1""", (str(guild), str(member), position))

    async def trusted_samples(self, guild, member):
        # Every reported metric has its own denominator; missing values never become zero.
        return await self.one(
            """SELECT COUNT(*) AS records, COUNT(DISTINCT s.message_id) AS recaps,
            COUNT(s.rating) AS rating_n, AVG(s.rating) AS rating,
            COUNT(s.goals) AS goals_n, SUM(s.goals) AS goals,
            COUNT(s.assists) AS assists_n, SUM(s.assists) AS assists,
            COUNT(s.passing_acc) AS passing_acc_n, AVG(s.passing_acc) AS passing_acc,
            COUNT(s.tackles) AS tackles_n, SUM(s.tackles) AS tackles
            FROM match_stats s JOIN player_links l
            ON l.guild_id=s.guild_id AND l.handle_key=s.handle_key
            WHERE s.guild_id=? AND l.discord_id=?""", (str(guild), str(member)))

    async def staff_brief(self, guild, *, now=None):
        now = now or utc_now()
        current, end = stamp(now), stamp(now + timedelta(days=7))
        policy = await self.policy(guild)
        sessions = await self.all(
            """SELECT e.*,
            (SELECT COUNT(*) FROM club_roster r WHERE r.guild_id=e.guild_id
              AND r.event_id=e.id AND r.response='yes') AS yes_count,
            (SELECT COUNT(*) FROM club_roster r WHERE r.guild_id=e.guild_id
              AND r.event_id=e.id AND r.response='assigned') AS assigned_count,
            (SELECT COUNT(*) FROM club_roster r WHERE r.guild_id=e.guild_id
              AND r.event_id=e.id AND r.response='maybe') AS maybe_count,
            (SELECT COUNT(*) FROM club_roster r WHERE r.guild_id=e.guild_id
              AND r.event_id=e.id AND r.response='no') AS no_count,
            (SELECT COUNT(*) FROM club_roster r WHERE r.guild_id=e.guild_id
              AND r.event_id=e.id AND r.response='pending') AS pending_count
            FROM club_events e WHERE e.guild_id=? AND e.cancelled=0
            AND e.starts_at>=? AND e.starts_at<? ORDER BY e.starts_at,e.id""",
            (str(guild), current, end))
        leave = await self.all(
            """SELECT id,member_id,starts_at,ends_at FROM club_leave
            WHERE guild_id=? AND status='pending' ORDER BY starts_at,id""", (str(guild),))
        gaps = await self.all(
            """SELECT e.id,e.title,e.starts_at,r.member_id FROM club_events e
            JOIN club_roster r ON r.guild_id=e.guild_id AND r.event_id=e.id
            LEFT JOIN club_attendance a ON a.guild_id=r.guild_id AND a.event_id=r.event_id
              AND a.member_id=r.member_id
            WHERE e.guild_id=? AND e.cancelled=0 AND e.starts_at>=? AND e.ends_at<=?
            AND r.response IN ('yes','assigned') AND (a.status IS NULL OR a.status='unmarked')
            AND NOT EXISTS(SELECT 1 FROM club_leave l WHERE l.guild_id=e.guild_id
              AND l.member_id=r.member_id AND l.status='approved'
              AND l.starts_at<e.ends_at AND l.ends_at>e.starts_at)
            ORDER BY e.starts_at,e.id,r.member_id""",
            (str(guild), stamp(now - timedelta(days=policy["days"])), current))
        followups = await self.all(
            """SELECT * FROM staff_records WHERE guild_id=? AND status='active'
            AND due_at<? ORDER BY due_at,id""", (str(guild), end))
        return {"now": current, "end": end, "policy": policy, "sessions": sessions,
                "leave": leave, "gaps": gaps, "followups": followups}
