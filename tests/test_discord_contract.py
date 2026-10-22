from pathlib import Path
import unittest
from unittest.mock import AsyncMock

import discord

from scoutbot.access import AccessDenied
from scoutbot.bot import ScoutBot
from scoutbot.commands import ScoutCommands
from scoutbot.config import Settings
from scoutbot.views import DocketView, EvaluationModal, PositionView
import json


class DiscordContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_periodic_worker_continues_after_one_maintenance_step_fails(self):
        bot = ScoutBot(Settings(Path(":memory:"), None, False, "INFO"))
        failed = AsyncMock(side_effect=RuntimeError("temporary maintenance failure"))
        succeeded = AsyncMock()

        with self.assertLogs("scoutbot.bot", level="ERROR"):
            await bot._run_worker_step("failing step", failed)
        await bot._run_worker_step("next step", succeeded)

        failed.assert_awaited_once()
        succeeded.assert_awaited_once()
        await bot.close()

    async def test_commands_serialize_and_docket_is_persistent(self):
        bot = ScoutBot(Settings(Path(":memory:"), None, True, "INFO"))
        group = ScoutCommands(bot)
        bot.tree.add_command(group)
        payload = group.to_dict(bot.tree)
        self.assertEqual(payload["name"], "scout")
        self.assertEqual(len(group.commands), 15)
        self.assertTrue(DocketView().is_persistent())
        self.assertTrue(issubclass(AccessDenied, discord.app_commands.CheckFailure))
        self.assertFalse(bot.intents.members)
        await bot.close()

    async def test_form_contracts(self):
        candidate = type("Member", (), {"display_name": "Player"})()
        modal = EvaluationModal({"payload": json.dumps(
            {"count": None, "context": "", "notes": ""})})
        self.assertEqual(len(modal.children), 3)
        self.assertEqual(modal.notes.max_length, 1000)
        self.assertEqual(len(PositionView(candidate, 20, 1).select.options), 4)

    async def test_recaps_can_run_without_privileged_intents(self):
        bot = ScoutBot(Settings(Path(":memory:"), None, False, "INFO"))
        self.assertFalse(bot.intents.message_content)
        self.assertFalse(bot.intents.guild_messages)
        await bot.close()
