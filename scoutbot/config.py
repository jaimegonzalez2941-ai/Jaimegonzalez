"""Runtime configuration. Credentials are read only by the running application."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database: Path
    test_guild: int | None
    recaps_enabled: bool
    log_level: str
    backup_directory: Path = Path(".data/backups")

    @classmethod
    def load(cls) -> "Settings":
        guild = os.getenv("DISCORD_TEST_GUILD_ID", "").strip()
        if guild and (not guild.isdecimal() or int(guild) <= 0):
            raise ValueError("DISCORD_TEST_GUILD_ID must be a positive server ID.")
        recaps = os.getenv("ENABLE_RECAPS", "true").strip().lower()
        if recaps not in {"true", "false"}:
            raise ValueError("ENABLE_RECAPS must be either true or false.")
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "WARN", "FATAL"}:
            raise ValueError("LOG_LEVEL must be a standard logging level.")
        database = os.getenv("SCOUT_DB_PATH", ".data/scout-v2.db").strip()
        backup_directory = os.getenv("SCOUT_BACKUP_DIR", ".data/backups").strip()
        if not database:
            raise ValueError("SCOUT_DB_PATH must not be empty.")
        if not backup_directory:
            raise ValueError("SCOUT_BACKUP_DIR must not be empty.")
        return cls(
            database=Path(database),
            test_guild=int(guild) if guild else None,
            recaps_enabled=recaps == "true",
            log_level=log_level,
            backup_directory=Path(backup_directory),
        )
