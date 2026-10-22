"""Async SQLite repository with tenant isolation and serialized transactions."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import uuid
from pathlib import Path

import aiosqlite

from .domain import handle
from .parser import PlayerStats
from .rubric import VERSION, validate_payload
from .club_store import ClubStore, CLUB_SCHEMA
from .assistant_store import AssistantStore, ASSISTANT_SCHEMA


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id TEXT PRIMARY KEY, staff_role TEXT NOT NULL, staff_channel TEXT NOT NULL,
    recap_channel TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_roles (
    guild_id TEXT NOT NULL, stage TEXT NOT NULL, role_id TEXT NOT NULL,
    PRIMARY KEY (guild_id, stage)
);
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL, candidate_name TEXT NOT NULL,
    scout_id TEXT NOT NULL, scout_name TEXT NOT NULL, position TEXT NOT NULL,
    score REAL NOT NULL CHECK(score BETWEEN 1 AND 10 AND NOT(score BETWEEN 6.8 AND 7.2)),
    notes TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evaluation_history ON evaluations(guild_id,candidate_id,id DESC);
CREATE INDEX IF NOT EXISTS evaluation_docket ON evaluations(guild_id,id DESC);
CREATE TABLE IF NOT EXISTS player_links (
    guild_id TEXT NOT NULL, handle_key TEXT NOT NULL, player_handle TEXT NOT NULL,
    discord_id TEXT NOT NULL, PRIMARY KEY(guild_id,handle_key)
);
CREATE TABLE IF NOT EXISTS recap_receipts (
    guild_id TEXT NOT NULL, message_id TEXT NOT NULL, channel_id TEXT NOT NULL,
    match_date TEXT NOT NULL, imported_at TEXT NOT NULL, player_count INTEGER NOT NULL,
    PRIMARY KEY(guild_id,message_id)
);
CREATE TABLE IF NOT EXISTS match_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
    message_id TEXT NOT NULL, handle_key TEXT NOT NULL, player_handle TEXT NOT NULL,
    rating REAL, goals INTEGER, assists INTEGER, passing_acc REAL, tackles INTEGER,
    UNIQUE(guild_id,message_id,handle_key),
    FOREIGN KEY(guild_id,message_id) REFERENCES recap_receipts(guild_id,message_id)
);
CREATE INDEX IF NOT EXISTS stats_handle ON match_stats(guild_id,handle_key,id DESC);
CREATE INDEX IF NOT EXISTS links_member ON player_links(guild_id,discord_id);
CREATE TABLE IF NOT EXISTS dockets (
    guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, message_id TEXT NOT NULL,
    PRIMARY KEY(guild_id,channel_id)
);
CREATE TABLE IF NOT EXISTS start_panels (
    guild_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, message_id TEXT,
    marker TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
    actor_id TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_guild ON audit_log(guild_id,id DESC);
CREATE TABLE IF NOT EXISTS evaluation_drafts (
    id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
    candidate_name TEXT NOT NULL, scout_id TEXT NOT NULL, scout_name TEXT NOT NULL,
    position TEXT NOT NULL, rubric_version TEXT NOT NULL, payload TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
    evaluation_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


class Database(ClubStore, AssistantStore):
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.connection: aiosqlite.Connection | None = None
        self.workspace_views = set()
        # Keep Discord publication serialized separately from SQL transactions.
        self.start_panel_lock = asyncio.Lock()

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path, isolation_level=None)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA busy_timeout=5000")
        await self.connection.executescript(SCHEMA)
        await self.connection.executescript(CLUB_SCHEMA)
        await self.connection.executescript(ASSISTANT_SCHEMA)
        # Additive upgrades: never rewrite or recalculate existing club records.
        async with self.transaction() as conn:
            async with conn.execute("PRAGMA table_info(evaluations)") as cursor:
                columns = {row["name"] for row in await cursor.fetchall()}
            for name in ("rubric_version", "scorecard", "draft_id"):
                if name not in columns:
                    await conn.execute(f"ALTER TABLE evaluations ADD COLUMN {name} TEXT")
            async with conn.execute("PRAGMA table_info(club_events)") as cursor:
                event_columns = {row["name"] for row in await cursor.fetchall()}
            for name, definition in (
                ("revision", "INTEGER NOT NULL DEFAULT 0"),
                ("revision_changed_at", "TEXT"),
                ("revision_time_changed", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in event_columns:
                    await conn.execute(f"ALTER TABLE club_events ADD COLUMN {name} {definition}")
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS evaluation_draft ON evaluations(draft_id)")
            await conn.execute("INSERT OR IGNORE INTO schema_version VALUES (2)")
            await conn.execute("INSERT OR IGNORE INTO schema_version VALUES (3)")
            await conn.execute("INSERT OR IGNORE INTO schema_version VALUES (4)")

    async def close(self):
        for view in tuple(self.workspace_views):
            await view.on_timeout()
        if self.connection:
            await self.connection.close()
            self.connection = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not open.")
        return self.connection

    async def all(self, sql: str, args=()) -> list[dict]:
        async with self.lock:
            async with self.conn.execute(sql, args) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def rows(self, sql: str, args=()):
        """Bounded live reads; workspace continuations use WorkspaceSnapshot instead."""
        from .workspace_snapshot import BATCH_SIZE
        async with self.lock:
            async with self.conn.execute(sql, args) as cursor:
                while batch := await cursor.fetchmany(BATCH_SIZE):
                    for row in batch:
                        yield dict(row)

    async def one(self, sql: str, args=()) -> dict | None:
        rows = await self.all(sql, args)
        return rows[0] if rows else None

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def _audit(self, conn, guild: int, actor: int, action: str, details: dict):
        await conn.execute(
            "INSERT INTO audit_log(guild_id,actor_id,action,details,created_at) VALUES (?,?,?,?,?)",
            (str(guild), str(actor), action, json.dumps(details), utcnow()),
        )

    async def audit(self, guild: int, actor: int, action: str, details: dict):
        async with self.transaction() as conn:
            await self._audit(conn, guild, actor, action, details)

    async def settings(self, guild: int) -> dict | None:
        return await self.one("SELECT * FROM guild_settings WHERE guild_id=?", (str(guild),))

    async def start_panel(self, guild):
        return await self.one("SELECT * FROM start_panels WHERE guild_id=?", (str(guild),))

    async def reserve_start_panel(self, guild, channel):
        async with self.transaction() as conn:
            await conn.execute(
                """INSERT INTO start_panels VALUES (?,?,NULL,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,
                message_id=NULL, marker=excluded.marker, created_at=excluded.created_at""",
                (str(guild), str(channel), uuid.uuid4().hex, utcnow()))
        return await self.start_panel(guild)

    async def save_start_panel(self, guild, message):
        async with self.transaction() as conn:
            await conn.execute("UPDATE start_panels SET message_id=? WHERE guild_id=?",
                               (str(message), str(guild)))

    async def configure(self, guild: int, actor: int, **values):
        async with self.transaction() as conn:
            await conn.execute(
                """INSERT INTO guild_settings VALUES (?,?,?,?,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET staff_role=excluded.staff_role,
                staff_channel=excluded.staff_channel,recap_channel=excluded.recap_channel,
                source_kind=excluded.source_kind,source_id=excluded.source_id""",
                (str(guild), *(str(values[k]) for k in (
                    "staff_role", "staff_channel", "recap_channel", "source_kind", "source_id"))),
            )
            await self._audit(conn, guild, actor, "settings.updated", values)

    async def evaluate(self, guild: int, candidate_id: int, candidate_name: str,
                       scout_id: int, scout_name: str, position: str, score: float,
                       notes: str):
        async with self.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO evaluations(guild_id,candidate_id,candidate_name,
                scout_id,scout_name,position,score,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(guild), str(candidate_id), candidate_name, str(scout_id),
                 scout_name, position, score, notes, utcnow()),
            )
            identifier = cursor.lastrowid
            await cursor.close()
            await self._audit(conn, guild, scout_id, "evaluation.created",
                              {"evaluation_id": identifier, "candidate_id": str(candidate_id)})
            return identifier

    async def start_draft(self, guild, candidate, candidate_name, actor, actor_name, position):
        payload = {"categories": {}, "count": None, "context": "", "notes": ""}
        validate_payload(payload, position)
        async with self.transaction() as conn:
            async with conn.execute(
                """SELECT * FROM evaluation_drafts WHERE guild_id=? AND candidate_id=?
                AND scout_id=? AND position=? AND status='draft' ORDER BY created_at DESC LIMIT 1""",
                (str(guild), str(candidate), str(actor), position),
            ) as cursor:
                previous = await cursor.fetchone()
            if previous:
                return dict(previous)
            identifier, now = uuid.uuid4().hex, utcnow()
            await conn.execute(
                """INSERT INTO evaluation_drafts
                (id,guild_id,candidate_id,candidate_name,scout_id,scout_name,position,
                rubric_version,payload,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, str(guild), str(candidate), candidate_name, str(actor),
                 actor_name, position, VERSION, json.dumps(payload), now, now),
            )
            await self._audit(conn, guild, actor, "draft.created", {"draft_id": identifier})
        return await self.draft(guild, actor, identifier)

    async def draft(self, guild, actor, identifier):
        row = await self.one(
            "SELECT * FROM evaluation_drafts WHERE id=? AND guild_id=? AND scout_id=?",
            (identifier, str(guild), str(actor)),
        )
        if row is None:
            raise ValueError("Draft not found in your private server-scoped evaluations.")
        return row

    async def change_draft(self, guild, actor, identifier, revision, *, payload=None, action="correct"):
        if action not in {"correct", "save", "discard"}:
            raise ValueError("Unknown draft action.")
        async with self.transaction() as conn:
            async with conn.execute(
                "SELECT * FROM evaluation_drafts WHERE id=? AND guild_id=? AND scout_id=?",
                (identifier, str(guild), str(actor)),
            ) as cursor:
                fetched = await cursor.fetchone()
            if fetched is None:
                raise ValueError("Draft not found in your private server-scoped evaluations.")
            row = dict(fetched)
            if action == "save" and row["status"] == "saved":
                return row["evaluation_id"]  # idempotent even after restart
            if row["status"] != "draft" or row["revision"] != revision:
                raise ValueError("This draft changed or was closed. Reopen /scout evaluate to review the latest version.")
            data = payload if action == "correct" else json.loads(row["payload"])
            result = validate_payload(data, row["position"], complete=action == "save",
                                      version=row["rubric_version"])
            evaluation_id = None
            if action == "save":
                cursor = await conn.execute(
                    """INSERT INTO evaluations(guild_id,candidate_id,candidate_name,scout_id,
                    scout_name,position,score,notes,created_at,rubric_version,scorecard,draft_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["guild_id"], row["candidate_id"], row["candidate_name"], row["scout_id"],
                     row["scout_name"], row["position"], float(result.score), data["notes"], utcnow(),
                     row["rubric_version"], json.dumps(data), identifier),
                )
                evaluation_id = cursor.lastrowid
                await cursor.close()
            await conn.execute(
                """UPDATE evaluation_drafts SET payload=?,revision=revision+1,status=?,
                evaluation_id=?,updated_at=? WHERE id=?""",
                (json.dumps(data), {"correct": "draft", "save": "saved", "discard": "discarded"}[action],
                 evaluation_id, utcnow(), identifier),
            )
            await self._audit(conn, guild, actor, f"draft.{action}",
                              {"draft_id": identifier, "revision": revision + 1,
                               "before": json.loads(row["payload"]), "after": data,
                               "evaluation_id": evaluation_id})
            return evaluation_id

    async def history(self, guild: int, member: int, page: int = 0):
        return await self.all(
            """SELECT * FROM evaluations WHERE guild_id=? AND candidate_id=?
            ORDER BY id DESC LIMIT 5 OFFSET ?""",
            (str(guild), str(member), max(0, page) * 5),
        )

    async def recent(self, guild: int):
        return await self.all(
            "SELECT * FROM evaluations WHERE guild_id=? ORDER BY id DESC LIMIT 5", (str(guild),),
        )

    async def link(self, guild: int, actor: int, ea_handle: str, member: int):
        key, display = handle(ea_handle)
        async with self.transaction() as conn:
            # Refuse accidental reassignment; unlink explicitly before transferring a handle.
            async with conn.execute(
                "SELECT discord_id FROM player_links WHERE guild_id=? AND handle_key=?",
                (str(guild), key),
            ) as cursor:
                previous = await cursor.fetchone()
            if previous and previous["discord_id"] != str(member):
                raise ValueError("That handle belongs to another member. Unlink it before reassignment.")
            await conn.execute(
                """INSERT INTO player_links VALUES (?,?,?,?)
                ON CONFLICT(guild_id,handle_key) DO UPDATE SET player_handle=excluded.player_handle""",
                (str(guild), key, display, str(member)),
            )
            await self._audit(conn, guild, actor, "handle.linked", {"member": str(member), "handle": display})

    async def unlink(self, guild: int, actor: int, ea_handle: str) -> bool:
        key, _ = handle(ea_handle)
        async with self.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM player_links WHERE guild_id=? AND handle_key=?", (str(guild), key),
            )
            deleted = cursor.rowcount > 0
            await cursor.close()
            if deleted:
                await self._audit(conn, guild, actor, "handle.unlinked", {"handle": key})
            return deleted

    async def ingest(self, guild: int, message: int, channel: int, match_date: str,
                     players: list[PlayerStats], actor: int) -> bool:
        async with self.transaction() as conn:
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO recap_receipts VALUES (?,?,?,?,?,?)",
                (str(guild), str(message), str(channel), match_date, utcnow(), len(players)),
            )
            inserted = cursor.rowcount > 0
            await cursor.close()
            if not inserted:
                return False
            await conn.executemany(
                """INSERT INTO match_stats(guild_id,message_id,handle_key,player_handle,
                rating,goals,assists,passing_acc,tackles) VALUES (?,?,?,?,?,?,?,?,?)""",
                [(str(guild), str(message), p.handle_key, p.player_handle, p.rating,
                  p.goals, p.assists, p.passing_acc, p.tackles) for p in players],
            )
            await self._audit(conn, guild, actor, "recap.imported",
                              {"message_id": str(message), "players": len(players)})
            return True

    async def stats(self, guild: int, member: int, page: int = 0):
        # Joining links at query time also attributes recaps received BEFORE linking.
        return await self.all(
            """SELECT s.*,r.match_date,r.channel_id FROM match_stats s
            JOIN player_links l ON l.guild_id=s.guild_id AND l.handle_key=s.handle_key
            JOIN recap_receipts r ON r.guild_id=s.guild_id AND r.message_id=s.message_id
            WHERE s.guild_id=? AND l.discord_id=? ORDER BY r.match_date DESC,s.id DESC
            LIMIT 5 OFFSET ?""", (str(guild), str(member), max(0, page) * 5),
        )

    async def aggregate(self, guild: int, member: int):
        return await self.one(
            """SELECT COUNT(*) AS matches,AVG(s.rating) AS rating,
            SUM(s.goals) AS goals,SUM(s.assists) AS assists,
            AVG(s.passing_acc) AS passing_acc,SUM(s.tackles) AS tackles
            FROM match_stats s JOIN player_links l
            ON l.guild_id=s.guild_id AND l.handle_key=s.handle_key
            WHERE s.guild_id=? AND l.discord_id=?""", (str(guild), str(member)),
        )

    async def set_stage_role(self, guild: int, actor: int, stage: str, role_id: int):
        async with self.transaction() as conn:
            await conn.execute(
                """INSERT INTO stage_roles VALUES (?,?,?) ON CONFLICT(guild_id,stage)
                DO UPDATE SET role_id=excluded.role_id""", (str(guild), stage, str(role_id)),
            )
            await self._audit(conn, guild, actor, "stage.configured", {"stage": stage, "role": str(role_id)})

    async def set_docket(self, guild: int, channel: int, message: int):
        async with self.transaction() as conn:
            await conn.execute(
                """INSERT INTO dockets VALUES (?,?,?) ON CONFLICT(guild_id,channel_id)
                DO UPDATE SET message_id=excluded.message_id""", (str(guild), str(channel), str(message)),
            )
