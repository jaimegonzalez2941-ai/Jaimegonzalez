import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scoutbot.config import Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_load_without_optional_guild_id(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.load()
        self.assertEqual(settings.database, Path(".data/scout-v2.db"))
        self.assertIsNone(settings.test_guild)
        self.assertTrue(settings.recaps_enabled)
        self.assertEqual(settings.log_level, "INFO")

    def test_invalid_boolean_and_log_level_fail_fast(self):
        for key, value in (("ENABLE_RECAPS", "sometimes"), ("LOG_LEVEL", "verbose")):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}, clear=True):
                with self.assertRaisesRegex(ValueError, key):
                    Settings.load()

    def test_empty_storage_paths_fail_fast(self):
        for key in ("SCOUT_DB_PATH", "SCOUT_BACKUP_DIR"):
            with self.subTest(key=key), patch.dict(os.environ, {key: "  "}, clear=True):
                with self.assertRaisesRegex(ValueError, key):
                    Settings.load()

    def test_invalid_optional_guild_value_fails_without_echoing_credentials(self):
        with patch.dict(os.environ, {"DISCORD_TEST_GUILD_ID": "not-a-server"}, clear=True):
            with self.assertRaisesRegex(ValueError, "positive server ID"):
                Settings.load()
