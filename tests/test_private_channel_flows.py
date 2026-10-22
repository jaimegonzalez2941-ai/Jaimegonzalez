"""Async privacy checks must finish before saving settings or publishing staff data."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from scoutbot.access import AccessDenied
from scoutbot.commands import ScoutCommands
from scoutbot.views import DocketView


class PrivateChannelFlowTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        role = Mock(spec=discord.Role)
        role.id = 20
        role.is_default.return_value = False
        channel = Mock(spec=discord.TextChannel)
        channel.id = 44
        channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, read_message_history=True)
        channel.send = AsyncMock(return_value=SimpleNamespace(id=60, jump_url="docket-link"))
        config = {"staff_role": "20", "staff_channel": "44"}
        db = SimpleNamespace(
            settings=AsyncMock(return_value=config), configure=AsyncMock(),
            recent=AsyncMock(return_value=[]), one=AsyncMock(return_value=None),
            set_docket=AsyncMock(),
        )
        bot = SimpleNamespace(user=SimpleNamespace(id=99), db=db)
        actor = Mock(spec=discord.Member)
        actor.id = 50
        actor.roles = []
        actor.guild_permissions = discord.Permissions(manage_guild=True)
        guild = SimpleNamespace(id=1, me=bot.user, get_channel=Mock(return_value=channel),
                                get_role=Mock(return_value=role))
        interaction = SimpleNamespace(
            guild=guild, user=actor, channel=channel, channel_id=44, client=bot,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock(),
        )
        return interaction, SimpleNamespace(bot=bot), role, channel, db

    def validator(self, interaction, *, blocked):
        async def check(channel, role):
            interaction.response.defer.assert_awaited_once()
            if blocked:
                raise AccessDenied("Unverified viewer.")
        return AsyncMock(side_effect=check)

    async def test_setup_defers_before_verifying_and_only_saves_on_success(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                interaction, commands, role, channel, db = self.context()
                verify = self.validator(interaction, blocked=blocked)
                with patch("scoutbot.commands.private_channel", verify):
                    call = ScoutCommands.setup.callback(
                        commands, interaction, role, channel, channel,
                        webhook_id="123456789012345678")
                    if blocked:
                        with self.assertRaises(AccessDenied):
                            await call
                        db.configure.assert_not_awaited()
                        interaction.followup.send.assert_not_awaited()
                    else:
                        await call
                        db.configure.assert_awaited_once()
                verify.assert_awaited_once_with(channel, role)
                interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)

    async def test_setup_manager_guard_precedes_lookup_and_settings_write(self):
        interaction, commands, role, channel, db = self.context()
        interaction.user.guild_permissions = discord.Permissions.none()
        with patch("scoutbot.commands.private_channel", new_callable=AsyncMock) as verify:
            with self.assertRaises(AccessDenied):
                await ScoutCommands.setup.callback(
                    commands, interaction, role, channel, channel,
                    webhook_id="123456789012345678")
        verify.assert_not_awaited()
        db.configure.assert_not_awaited()

    async def test_docket_defers_and_cannot_publish_before_validation(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                interaction, commands, role, channel, db = self.context()
                verify = self.validator(interaction, blocked=blocked)
                with patch("scoutbot.commands.private_channel", verify):
                    call = ScoutCommands.docket.callback(commands, interaction)
                    if blocked:
                        with self.assertRaises(AccessDenied):
                            await call
                        db.recent.assert_not_awaited()
                        channel.send.assert_not_awaited()
                        db.set_docket.assert_not_awaited()
                    else:
                        await call
                        channel.send.assert_awaited_once()
                verify.assert_awaited_once_with(channel, role)

    async def test_refresh_defers_and_cannot_edit_before_validation(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                interaction, _, role, channel, db = self.context()
                verify = self.validator(interaction, blocked=blocked)
                view = DocketView()
                with patch("scoutbot.views.private_channel", verify):
                    call = view.refresh.callback(interaction)
                    if blocked:
                        with self.assertRaises(AccessDenied):
                            await call
                        db.recent.assert_not_awaited()
                        interaction.edit_original_response.assert_not_awaited()
                    else:
                        await call
                        interaction.edit_original_response.assert_awaited_once()
                verify.assert_awaited_once_with(channel, role)
