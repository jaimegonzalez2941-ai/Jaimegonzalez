import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from scoutbot.backup_ops import (
    BackupError,
    create_backup,
    rehearse_restore,
    restore_database,
)
from scoutbot.database import Database, DatabaseProcessLock


class BackupOperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live_path = self.root / "club.sqlite3"
        self.backup_dir = self.root / "private-backups"
        self.db = Database(self.live_path)
        await self.db.open()

    async def asyncTearDown(self):
        await self.db.close()
        self.temp.cleanup()

    async def add_evaluation(self, guild, candidate):
        await self.db.evaluate(
            guild, candidate, f"Player {candidate}", 20, "Coach",
            "Defender", 8.4, f"Evidence for {candidate}",
        )

    async def test_online_backup_includes_committed_wal_writes(self):
        reader = sqlite3.connect(self.live_path)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM evaluations").fetchone()
        try:
            for candidate in range(1, 8):
                await self.add_evaluation(1, candidate)
            wal_path = Path(f"{self.live_path}-wal")
            self.assertTrue(wal_path.exists())
            self.assertGreater(wal_path.stat().st_size, 0)

            backup = await create_backup(self.live_path, self.backup_dir)
            check = sqlite3.connect(backup)
            try:
                self.assertEqual(check.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0], 7)
                self.assertEqual(check.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            finally:
                check.close()
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.backup_dir.stat().st_mode & 0o777, 0o700)
        finally:
            reader.rollback()
            reader.close()

    async def test_backup_preserves_server_scoping(self):
        await self.add_evaluation(1001, 11)
        await self.add_evaluation(2002, 22)
        backup = await create_backup(self.live_path, self.backup_dir)
        restored_path = self.root / "isolated-copy.sqlite3"
        restored_path.write_bytes(backup.read_bytes())
        isolated = Database(restored_path)
        await isolated.open()
        try:
            self.assertEqual(len(await isolated.history(1001, 11)), 1)
            self.assertEqual(len(await isolated.history(2002, 22)), 1)
            self.assertEqual(await isolated.history(1001, 22), [])
            self.assertEqual(await isolated.history(2002, 11), [])
        finally:
            await isolated.close()

    async def test_corrupt_and_wrong_schema_backups_are_rejected(self):
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")
        with self.assertRaises(BackupError):
            await rehearse_restore(corrupt)
        with self.assertRaises(BackupError):
            await restore_database(self.live_path, corrupt, self.backup_dir)

        wrong_schema = self.root / "wrong-schema.sqlite3"
        connection = sqlite3.connect(wrong_schema)
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaises(BackupError):
            await rehearse_restore(wrong_schema)

        self.assertEqual(await self.db.history(1, 11), [])

    async def test_rehearsal_is_non_destructive_and_restore_preserves_live_copy(self):
        await self.add_evaluation(1, 11)
        backup = await create_backup(self.live_path, self.backup_dir)
        await self.add_evaluation(1, 22)
        await rehearse_restore(backup)
        self.assertEqual(len(await self.db.history(1, 11)), 1)
        self.assertEqual(len(await self.db.history(1, 22)), 1)

        await self.db.close()
        preserved = await restore_database(self.live_path, backup, self.backup_dir)
        await self.db.open()
        self.assertTrue(preserved.name.startswith("pre-restore-"))
        preserved_db = Database(preserved)
        await preserved_db.open()
        try:
            self.assertEqual(len(await self.db.history(1, 11)), 1)
            self.assertEqual(await self.db.history(1, 22), [])
            self.assertEqual(len(await preserved_db.history(1, 11)), 1)
            self.assertEqual(len(await preserved_db.history(1, 22)), 1)
        finally:
            await preserved_db.close()

    async def test_restore_is_refused_while_bot_holds_database_lock(self):
        await self.add_evaluation(1, 11)
        backup = await create_backup(self.live_path, self.backup_dir)
        with self.assertRaisesRegex(RuntimeError, "database is open or reserved"):
            await restore_database(self.live_path, backup, self.backup_dir)
        self.assertEqual(len(await self.db.history(1, 11)), 1)

    async def test_new_database_connection_cannot_start_during_restore(self):
        await self.db.close()
        second = Database(self.live_path)
        with DatabaseProcessLock(self.live_path, exclusive=True):
            with self.assertRaisesRegex(RuntimeError, "database is open or reserved"):
                await second.open()
        await self.db.open()

    async def test_backup_cli_needs_no_discord_token_or_gateway(self):
        env = os.environ.copy()
        env.pop("DISCORD_TOKEN", None)
        env["SCOUT_DB_PATH"] = str(self.live_path)
        result = subprocess.run(
            [sys.executable, "-m", "scoutbot", "--backup"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(".sqlite3", result.stdout)
        self.assertNotIn("DISCORD_TOKEN", result.stdout + result.stderr)

    async def test_restore_cli_requires_explicit_confirmation(self):
        await self.add_evaluation(1, 11)
        backup = await create_backup(self.live_path, self.backup_dir)
        await self.add_evaluation(1, 22)
        env = os.environ.copy()
        env.pop("DISCORD_TOKEN", None)
        env["SCOUT_DB_PATH"] = str(self.live_path)
        result = subprocess.run(
            [sys.executable, "-m", "scoutbot", "--restore", str(backup)],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--confirm-live-restore", result.stderr)
        self.assertEqual(len(await self.db.history(1, 22)), 1)

        await self.db.close()
        env["SCOUT_BACKUP_DIR"] = str(self.backup_dir)
        confirmed = subprocess.run(
            [
                sys.executable, "-m", "scoutbot", "--restore", str(backup),
                "--confirm-live-restore",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertIn("Restore completed", confirmed.stdout)
        await self.db.open()
        self.assertEqual(len(await self.db.history(1, 11)), 1)
        self.assertEqual(await self.db.history(1, 22), [])


if __name__ == "__main__":
    unittest.main()
