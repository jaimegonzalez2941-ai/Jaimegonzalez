"""Async SQLite repository with tenant isolation and serialized transactions."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

import aiosqlite

from .domain import handle
from .parser import PlayerStats


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
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
    actor_id TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_guild ON audit_log(guild_id,id DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.connection: aiosqlite.Connection | None = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path, isolation_level=None)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA busy_timeout=5000")
        await self.connection.executescript(SCHEMA)

    async def close(self):
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
