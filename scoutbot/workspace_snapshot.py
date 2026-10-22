"""Short-lived SQLite read snapshots and bounded reads for private workspaces.

File databases use a pinned WAL reader: writers never wait for navigation.
In-memory databases (offline commands/tests) use SQLite's page-wise backup to
a private temporary file, not a Python copy of all records.
"""

from contextlib import aclosing
from datetime import timedelta
from tempfile import TemporaryDirectory

import aiosqlite

from .assistant_store import AssistantStore
from .club_store import ClubStore
from .club_domain import ATTENDANCE, stamp, utc_now

BATCH_SIZE = 20


class WorkspaceSnapshot(AssistantStore, ClubStore):
    def __init__(self, connection, directory):
        self.connection = connection
        self.directory = directory
        self.now = utc_now()
        self.closed = False

    @classmethod
    async def open(cls, database):
        directory = TemporaryDirectory(prefix="club-workspace-")
        connection = None
        try:
            async with database.lock:
                memory = str(database.path) == ":memory:"
                connection = await aiosqlite.connect(
                    directory.name + "/snapshot.db" if memory else database.path,
                    isolation_level=None)
                connection.row_factory = aiosqlite.Row
                if memory:
                    await database.conn.backup(connection, pages=64)
                await connection.execute("PRAGMA query_only=ON")
                await connection.execute("BEGIN")
                # BEGIN alone is deferred; pin the snapshot before releasing the writer lock.
                async with connection.execute("SELECT version FROM schema_version LIMIT 1") as cur:
                    await cur.fetchone()
            return cls(connection, directory)
        except BaseException:
            if connection:
                await connection.close()
            directory.cleanup()
            raise

    async def close(self):
        if not self.closed:
            self.closed = True
            await self.connection.close()
            self.directory.cleanup()

    async def rows(self, sql, args=()):
        """Fetch bounded batches, never fetchall or hold the live writer lock."""
        if self.closed:
            raise ValueError("Workspace expired. Reopen the command for a fresh snapshot.")
        cursor = await self.connection.execute(sql, args)
        try:
            while batch := await cursor.fetchmany(BATCH_SIZE):
                for row in batch:
                    yield dict(row)
        finally:
            # Closing a view closes the connection even if a nested stream is paused.
            if not self.closed:
                await cursor.close()

    async def one(self, sql, args=()):
        async with aclosing(self.rows(sql, args)) as rows:
            return await anext(rows, None)

    async def all(self, sql, args=()):
        # Workspace code must explicitly opt in to streaming. Catch accidental regressions.
        raise RuntimeError("Use bounded rows() in a workspace snapshot.")

    async def attendance_summary(self, guild, member, days, *, now=None):
        """Same attendance rules, grouped in SQLite rather than retained per session."""
        now = now or self.now
        counts = {key: 0 for key in (*ATTENDANCE, "leave")}
        sql = """SELECT status,on_leave,COUNT(*) AS n FROM (
            SELECT a.status,EXISTS(
                SELECT 1 FROM club_leave l WHERE l.guild_id=e.guild_id AND l.member_id=r.member_id
                AND l.status='approved' AND l.starts_at<e.ends_at AND l.ends_at>e.starts_at
            ) AS on_leave
            FROM club_events e JOIN club_roster r ON r.guild_id=e.guild_id AND r.event_id=e.id
            LEFT JOIN club_attendance a ON a.guild_id=r.guild_id AND a.event_id=r.event_id
                AND a.member_id=r.member_id
            WHERE e.guild_id=? AND r.member_id=? AND e.cancelled=0
            AND e.starts_at>=? AND e.ends_at<=? AND r.response IN ('yes','assigned')
        ) GROUP BY status,on_leave"""
        async for row in self.rows(sql, (str(guild), str(member),
                                         stamp(now - timedelta(days=days)), stamp(now))):
            counts["leave" if row["on_leave"] else row["status"] or "unmarked"] += row["n"]
        eligible = counts["present"] + counts["late"] + counts["absent"]
        attended = counts["present"] + counts["late"]
        return {**counts, "eligible": eligible, "attended": attended,
                "rate": 100 * attended / eligible if eligible else None}
