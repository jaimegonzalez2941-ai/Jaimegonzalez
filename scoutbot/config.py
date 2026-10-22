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
        return cls(
            database=Path(os.getenv("SCOUT_DB_PATH", ".data/scout-v2.db")),
            test_guild=int(guild) if guild else None,
            recaps_enabled=os.getenv("ENABLE_RECAPS", "true").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            backup_directory=Path(os.getenv("SCOUT_BACKUP_DIR", ".data/backups")),
        )
