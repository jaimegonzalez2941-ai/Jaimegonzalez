import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import discord

from scoutbot.access import AccessDenied, private_channel


def role(identifier, permissions=None):
    result = Mock(spec=discord.Role)
    result.id = identifier
    result.name = f"Role {identifier}"
    result.permissions = permissions or discord.Permissions.none()
    return result


def member(identifier, *, manager=False, roles=()):
    result = Mock(spec=discord.Member)
    result.id = identifier
    result.display_name = f"Member {identifier}"
    result.guild_permissions = discord.Permissions(manage_guild=manager)
    result.roles = list(roles)
    return result


class PrivateChannelTests(unittest.TestCase):
    def setUp(self):
        self.staff = role(20)
        self.everyone = role(1)
        self.bot = member(99)
        self.guild = SimpleNamespace(default_role=self.everyone, me=self.bot)
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

    def test_allows_staff_role_and_bot_viewer_overwrites(self):
        private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_called_once_with(self.bot)

    def test_allows_a_verified_server_manager_overwrite(self):
        manager = member(100, manager=True)
        self.channel.overwrites[manager] = discord.PermissionOverwrite(view_channel=True)
        private_channel(self.channel, self.staff)

    def test_allows_a_verified_member_who_has_the_staff_role(self):
        staff_member = member(101, roles=(self.staff,))
        self.channel.overwrites[staff_member] = discord.PermissionOverwrite(view_channel=True)
        private_channel(self.channel, self.staff)

    def test_rejects_unresolved_member_overwrite_without_assuming_staff_access(self):
        former_member = discord.Object(id=102)
        self.channel.overwrites[former_member] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaises(AccessDenied) as caught:
            private_channel(self.channel, self.staff)
        self.assertIn("former or unresolved members", str(caught.exception))
        self.assertIn("unresolved member entry (ID 102)", str(caught.exception))
        self.channel.permissions_for.assert_not_called()

    def test_rejects_a_known_nonstaff_member_with_view_access(self):
        other = member(103)
        self.channel.overwrites[other] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaises(AccessDenied) as caught:
            private_channel(self.channel, self.staff)
        self.assertIn('member "Member 103" (ID 103)', str(caught.exception))
        self.assertIn('Selected staff role: role "Role 20" (ID 20)', str(caught.exception))

    def test_nonstaff_role_diagnostic_escapes_names_and_is_bounded(self):
        other = role(104)
        other.name = "@everyone **Other**\nRole" + "*" * 4000
        self.channel.overwrites[other] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaises(AccessDenied) as caught:
            private_channel(self.channel, self.staff)
        message = str(caught.exception)
        self.assertIn("(ID 104)", message)
        self.assertNotIn("@everyone", message)
        self.assertNotIn("**Other**", message)
        self.assertLess(len(message), 1500)

    def test_requires_an_explicit_deny_for_everyone(self):
        self.channel.overwrites_for.return_value = discord.PermissionOverwrite()
        with self.assertRaisesRegex(AccessDenied, "@everyone"):
            private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_not_called()

    def test_requires_the_bots_membership_before_checking_overwrites(self):
        self.guild.me = None
        with self.assertRaisesRegex(AccessDenied, "membership is unavailable"):
            private_channel(self.channel, self.staff)
        self.channel.permissions_for.assert_not_called()

    def test_requires_bot_view_send_and_embed_permissions(self):
        self.channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, send_messages=True, embed_links=False)
        with self.assertRaisesRegex(AccessDenied, "Embed Links"):
            private_channel(self.channel, self.staff)


if __name__ == "__main__":
    unittest.main()
