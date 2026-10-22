"""Private operator-only SQLite backup, rehearsal, and explicit restore operations."""

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

from .database import CURRENT_SCHEMA_VERSION, Database, DatabaseProcessLock


class BackupError(ValueError):
    """A backup is invalid, incompatible, or unsafe to use."""


def _validate_sqlite_file(path: Path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BackupError("Backup must be an existing regular file.")
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise BackupError("SQLite integrity check failed.")
            violations = conn.execute("PRAGMA foreign_key_check").fetchone()
            if violations:
                raise BackupError("Backup contains foreign-key violations.")
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"schema_version", "guild_settings", "evaluations", "player_links"} <= tables:
                raise BackupError("Backup does not contain the expected Club Agent schema.")
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
            if not versions or max(versions) > CURRENT_SCHEMA_VERSION:
                raise BackupError("Backup schema is newer than this Club Agent version.")
    except (sqlite3.Error, OSError) as error:
        raise BackupError("Backup is not a readable SQLite database.") from error


async def _validate_compatible(path: Path):
    """Integrity-check the file, then exercise additive schema upgrades on a disposable copy."""
    path = Path(path)
    _validate_sqlite_file(path)
    with tempfile.TemporaryDirectory(prefix="club-restore-check-") as directory:
        test_copy = Path(directory) / "rehearsal.sqlite3"
        shutil.copyfile(path, test_copy)
        os.chmod(test_copy, 0o600)
        database = Database(test_copy)
        try:
            await database.open()
            async with database.conn.execute("PRAGMA integrity_check") as cursor:
                if tuple(await cursor.fetchone()) != ("ok",):
                    raise BackupError("Backup failed integrity validation after opening.")
        except (sqlite3.Error, RuntimeError) as error:
            raise BackupError("Backup schema is not compatible with this Club Agent version.") from error
        finally:
            await database.close()


def _sqlite_backup(source_path: Path, target_path: Path):
    source_path = Path(source_path)
    if source_path.is_symlink() or not source_path.is_file():
        raise BackupError("The configured database must be an existing regular file.")
    target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target_path.parent, 0o700)
    fd = os.open(target_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source_path)) as source, closing(
            sqlite3.connect(target_path)
        ) as target:
            source.backup(target)
        os.chmod(target_path, 0o600)
        with open(target_path, "rb") as backup_file:
            os.fsync(backup_file.fileno())
    except BaseException:
        target_path.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path):
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _new_backup_path(directory: Path, prefix: str):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}.sqlite3"


async def create_backup(
    database_path: Path,
    backup_dir: Path | None = None,
    *,
    prefix: str = "club-agent",
) -> Path:
    """Create an online-consistent standalone snapshot, including committed WAL pages."""
    database_path = Path(database_path)
    backup_dir = Path(backup_dir or Path(".data") / "backups")
    target = _new_backup_path(backup_dir, prefix)
    staging = target.with_name(f".{target.name}.partial.sqlite3")
    try:
        await asyncio.to_thread(_sqlite_backup, database_path, staging)
        await _validate_compatible(staging)
        os.replace(staging, target)
        await asyncio.to_thread(_fsync_directory, target.parent)
    except BaseException:
        staging.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return target


async def rehearse_restore(backup_path: Path) -> None:
    """Verify integrity and compatibility using only a disposable copy."""
    await _validate_compatible(Path(backup_path))


def _checkpoint_database(path: Path):
    with closing(sqlite3.connect(path)) as conn:
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result and result[0] != 0:
            raise BackupError("Could not checkpoint the live database before restore.")


async def restore_database(
    database_path: Path, backup_path: Path, backup_dir: Path | None = None
) -> Path:
    """Replace a stopped live DB only after validation and preserving its current contents."""
    database_path, backup_path = Path(database_path), Path(backup_path)
    if backup_path.resolve() == database_path.resolve():
        raise BackupError("The restore source must not be the live database.")
    await _validate_compatible(backup_path)
    if database_path.is_symlink() or not database_path.is_file():
        raise BackupError("A live database must exist before restore so it can be preserved.")

    directory = Path(backup_dir or Path(".data") / "backups")
    with DatabaseProcessLock(database_path, exclusive=True):
        _checkpoint_database(database_path)
        preserved = await create_backup(database_path, directory, prefix="pre-restore")

        staging = database_path.parent / (
            f".{database_path.name}.restore-{uuid.uuid4().hex}.sqlite3"
        )
        try:
            shutil.copyfile(backup_path, staging)
            os.chmod(staging, 0o600)
            await _validate_compatible(staging)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{database_path}{suffix}").unlink(missing_ok=True)
            os.replace(staging, database_path)
            _fsync_directory(database_path.parent)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
    return preserved
