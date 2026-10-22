import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from scoutbot.access import AccessDenied, private_channel


def role(identifier, permissions=None):
    result = Mock(spec=discord.Role)
    result.id = identifier
    result.name = f"Role {identifier}"
    result.permissions = permissions or discord.Permissions.none()
    result.guild = SimpleNamespace(id=1)
    result.managed = False
    result.tags = None
    return result


def member(identifier, *, manager=False, roles=()):
    result = Mock(spec=discord.Member)
    result.id = identifier
    result.display_name = f"Member {identifier}"
    result.guild_permissions = discord.Permissions(manage_guild=manager)
    result.roles = list(roles)
    result.guild = SimpleNamespace(id=1)
    result.bot = False
    return result


class PrivateChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.staff = role(20)
        self.everyone = role(1)
        self.bot = member(99)
        self.guild = SimpleNamespace(
            id=1, default_role=self.everyone, me=self.bot, fetch_member=AsyncMock())
        self.channel = SimpleNamespace(
            guild=self.guild,
            overwrites={
                self.staff: discord.PermissionOverwrite(view_channel=True),
                self.bot: discord.PermissionOverwrite(view_channel=True),
            },
            overwrites_for=Mock(return_value=discord.PermissionOverwrite(view_channel=False)),
            permissions_for=Mock(return_value=discord.Permissions(
                view_channel=True, send_messages=True, embed_links=True)),
        )

    async def test_allows_staff_role_and_bot_viewer_overwrites(self):
        await private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_called_once_with(self.bot)

    async def test_allows_a_verified_server_manager_overwrite(self):
        manager = member(100, manager=True)
        self.channel.overwrites[manager] = discord.PermissionOverwrite(view_channel=True)
        await private_channel(self.channel, self.staff)

    async def test_allows_a_verified_member_who_has_the_staff_role(self):
        staff_member = member(101, roles=(self.staff,))
        self.channel.overwrites[staff_member] = discord.PermissionOverwrite(view_channel=True)
        await private_channel(self.channel, self.staff)

    async def test_rejects_unresolved_member_overwrite_without_assuming_staff_access(self):
        former_member = discord.Object(id=102)
        self.channel.overwrites[former_member] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaises(AccessDenied) as caught:
            await private_channel(self.channel, self.staff)
        self.assertIn("former or unresolved members", str(caught.exception))
        self.assertIn("unresolved member entry (ID 102)", str(caught.exception))
        self.channel.permissions_for.assert_not_called()
        self.guild.fetch_member.assert_not_awaited()

    async def test_rejects_a_known_nonstaff_member_with_view_access(self):
        other = member(103)
        self.channel.overwrites[other] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaises(AccessDenied) as caught:
            await private_channel(self.channel, self.staff)
        self.assertIn('member "Member 103" (ID 103)', str(caught.exception))
        self.assertIn('Selected staff role: role "Role 20" (ID 20)', str(caught.exception))

    async def test_nonstaff_role_diagnostic_escapes_names_and_is_bounded(self):
        other = role(104)
        other.name = "@everyone **Other**\nRole" + "*" * 4000
        self.channel.overwrites[other] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaises(AccessDenied) as caught:
            await private_channel(self.channel, self.staff)
        message = str(caught.exception)
        self.assertIn("(ID 104)", message)
        self.assertNotIn("@everyone", message)
        self.assertNotIn("**Other**", message)
        self.assertLess(len(message), 1500)

    async def test_requires_an_explicit_deny_for_everyone(self):
        self.channel.overwrites_for.return_value = discord.PermissionOverwrite()
        with self.assertRaisesRegex(AccessDenied, "@everyone"):
            await private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_not_called()

    async def test_requires_the_bots_membership_before_checking_overwrites(self):
        self.guild.me = None
        with self.assertRaisesRegex(AccessDenied, "membership is unavailable"):
            await private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_not_called()

    async def test_requires_bot_view_send_and_embed_permissions(self):
        self.channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, send_messages=True, embed_links=False)
        with self.assertRaisesRegex(AccessDenied, "Embed Links"):
            await private_channel(self.channel, self.staff)

    async def test_uncached_current_staff_or_manager_is_verified(self):
        for current in (member(102, roles=(self.staff,)), member(102, manager=True)):
            with self.subTest(current=current):
                target = discord.Object(id=102, type=discord.User)
                self.channel.overwrites[target] = discord.PermissionOverwrite(view_channel=True)
                self.guild.fetch_member.return_value = current
                await private_channel(self.channel, self.staff)
                self.guild.fetch_member.assert_awaited_with(102)

    async def test_uncached_nonstaff_is_named_and_stays_blocked(self):
        target = discord.Object(id=102, type=discord.User)
        self.channel.overwrites[target] = discord.PermissionOverwrite(view_channel=True)
        self.guild.fetch_member.return_value = member(102)
        with self.assertRaisesRegex(AccessDenied, 'member "Member 102"'):
            await private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_not_called()

    def sdk_uncached_viewer(self):
        """Use discord.py's real payload parser and overwrite property."""
        self.guild.get_member = Mock(return_value=None)
        self.guild.get_role = Mock(return_value=None)
        channel = discord.TextChannel(
            state=Mock(), guild=self.guild,
            data={
                "id": "44", "name": "staff", "type": 0, "position": 0,
                "permission_overwrites": [{
                    "id": "102", "type": 1,
                    "allow": str(discord.Permissions(view_channel=True).value),
                    "deny": "0",
                }],
            },
        )
        entries = channel.overwrites
        target = next(iter(entries))
        self.assertIsInstance(target, discord.Object)
        self.assertIs(target.type, discord.abc.User)
        self.channel.overwrites.update(entries)

    async def test_real_sdk_uncached_staff_overwrite_is_resolved(self):
        self.sdk_uncached_viewer()
        self.guild.fetch_member.return_value = member(102, roles=(self.staff,))
        await private_channel(self.channel, self.staff)
        self.guild.fetch_member.assert_awaited_once_with(102)
        self.channel.permissions_for.assert_called_once_with(self.bot)

    async def test_real_sdk_uncached_nonstaff_still_rejected_by_identity(self):
        self.sdk_uncached_viewer()
        self.guild.fetch_member.return_value = member(102)
        with self.assertRaisesRegex(AccessDenied, 'member "Member 102"'):
            await private_channel(self.channel, self.staff)
        self.guild.fetch_member.assert_awaited_once_with(102)
        self.channel.permissions_for.assert_not_called()

    async def test_departed_member_is_reported_as_stale(self):
        self.channel.overwrites[discord.Object(id=102, type=discord.User)] = (
            discord.PermissionOverwrite(view_channel=True))
        self.guild.fetch_member.side_effect = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), {"code": 10007, "message": "Unknown Member"})
        with self.assertRaisesRegex(AccessDenied, "stale member entry"):
            await private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_not_called()

    async def test_failed_member_lookup_never_grants_access(self):
        self.channel.overwrites[discord.Object(id=102, type=discord.User)] = (
            discord.PermissionOverwrite(view_channel=True))
        for error in (
            TimeoutError(), ConnectionError(),
            discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Forbidden"),
        ):
            self.guild.fetch_member.side_effect = error
            with self.assertRaisesRegex(AccessDenied, "could not verify"):
                await private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_not_called()

    async def test_member_lookup_checks_returned_identity_and_server(self):
        self.channel.overwrites[discord.Object(id=102, type=discord.User)] = (
            discord.PermissionOverwrite(view_channel=True))
        wrong_server = member(102, roles=(self.staff,))
        wrong_server.guild = SimpleNamespace(id=2)
        for current in (wrong_server, member(103, manager=True), discord.Object(id=102)):
            self.guild.fetch_member.return_value = current
            with self.assertRaisesRegex(AccessDenied, "matching member"):
                await private_channel(self.channel, self.staff)

    async def test_own_managed_bot_role_is_allowed_but_lookalikes_are_not(self):
        bot_role = role(104)
        bot_role.managed = True
        bot_role.tags = SimpleNamespace(bot_id=self.bot.id)
        self.channel.overwrites[bot_role] = discord.PermissionOverwrite(view_channel=True)
        await private_channel(self.channel, self.staff)
        for managed, tags, guild in (
            (False, bot_role.tags, bot_role.guild),
            (True, SimpleNamespace(bot_id=999), bot_role.guild),
            (True, None, bot_role.guild),
            (True, SimpleNamespace(bot_id=self.bot.id), SimpleNamespace(id=2)),
        ):
            bot_role.managed, bot_role.tags, bot_role.guild = managed, tags, guild
            with self.assertRaises(AccessDenied):
                await private_channel(self.channel, self.staff)
        self.guild.fetch_member.assert_not_awaited()

    async def test_unresolved_roles_and_nonviewer_entries_are_not_member_lookups(self):
        missing_role = discord.Object(id=104, type=discord.Role)
        self.channel.overwrites[missing_role] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaisesRegex(AccessDenied, "unresolved role entry"):
            await private_channel(self.channel, self.staff)
        del self.channel.overwrites[missing_role]
        self.channel.overwrites[discord.Object(id=102, type=discord.User)] = (
            discord.PermissionOverwrite(send_messages=True))
        await private_channel(self.channel, self.staff)
        self.guild.fetch_member.assert_not_awaited()

    async def test_staff_role_must_belong_to_channel_server(self):
        self.staff.guild = SimpleNamespace(id=2)
        with self.assertRaisesRegex(AccessDenied, "same server"):
            await private_channel(self.channel, self.staff)
        self.guild.fetch_member.assert_not_awaited()

    async def test_lookup_timeout_budget_is_shared_between_entries(self):
        for identifier in (102, 103):
            self.channel.overwrites[discord.Object(id=identifier, type=discord.User)] = (
                discord.PermissionOverwrite(view_channel=True))
        self.guild.fetch_member.side_effect = [member(102, manager=True), member(103, manager=True)]
        budgets = []
        original_timeout = asyncio.timeout

        def record_timeout(seconds):
            budgets.append(seconds)
            return original_timeout(seconds)

        with patch("scoutbot.access.asyncio.timeout", side_effect=record_timeout):
            await private_channel(self.channel, self.staff)
        self.assertEqual(len(budgets), 2)
        self.assertLessEqual(budgets[1], budgets[0])
        self.assertLessEqual(budgets[0], 8)

    async def test_timeout_cancels_slow_lookup_and_fails_closed(self):
        self.channel.overwrites[discord.Object(id=102, type=discord.User)] = (
            discord.PermissionOverwrite(view_channel=True))

        async def slow_lookup(identifier):
            await asyncio.sleep(10)

        self.guild.fetch_member.side_effect = slow_lookup
        original_timeout = asyncio.timeout
        with patch("scoutbot.access.asyncio.timeout", side_effect=lambda _: original_timeout(0.001)):
            with self.assertRaisesRegex(AccessDenied, "could not verify"):
                await private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_not_called()


if __name__ == "__main__":
    unittest.main()
