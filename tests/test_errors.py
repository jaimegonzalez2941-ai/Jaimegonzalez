import re
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import discord
from discord import app_commands

from scoutbot.access import AccessDenied
from scoutbot.bot import CommandTree
from scoutbot.errors import describe_error, respond_error
from scoutbot.views import SecureView, EvaluationModal, CategoryModal


def interaction(command="scout setup", *, done=False):
    return SimpleNamespace(
        command=SimpleNamespace(qualified_name=command) if command else None,
        data={}, is_expired=Mock(return_value=False),
        response=SimpleNamespace(is_done=Mock(return_value=done), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def http_error(cls=discord.HTTPException, *, code=50013, status=403):
    return cls(SimpleNamespace(status=status, reason="Test"), {"code": code, "message": "PRIVATE_API_DETAIL"})


class ErrorDescriptionsTests(unittest.TestCase):
    def setUp(self):
        self.i = interaction()

    def test_validation_keeps_specific_instruction_and_adds_command_recovery(self):
        message = describe_error(self.i, ValueError("Choose exactly one source: bot OR webhook."))
        self.assertIn("Choose exactly one source", message)
        self.assertIn("Command: /scout setup", message)
        self.assertIn("Next step", message)
        self.assertIn("private staff channel", message)
        self.assertNotIn("Reference:", message)

    def test_access_reason_is_retained_and_authorization_not_bypassed(self):
        message = describe_error(self.i, AccessDenied("This private menu belongs to another member."))
        self.assertIn("belongs to another member", message)
        self.assertIn("Access or privacy check", message)
        self.assertNotIn("success", message.lower())

    def test_cooldown_rounds_up_and_has_wait_guidance(self):
        error = app_commands.CommandOnCooldown(app_commands.Cooldown(1, 10), 2.1)
        message = describe_error(self.i, error)
        self.assertIn("3 second(s)", message)
        self.assertIn("Repeated taps", message)

    def test_missing_permissions_distinguish_bot_from_viewer(self):
        bot = describe_error(self.i, app_commands.BotMissingPermissions(["embed_links", "send_messages"]))
        viewer = describe_error(self.i, app_commands.MissingPermissions(["manage_guild"]))
        self.assertIn("Club Agent is missing permissions", bot)
        self.assertIn("embed links", bot)
        self.assertIn("not Administrator", bot)
        self.assertIn("Your account", viewer)
        self.assertIn("manage guild", viewer)

    def test_dm_and_general_checks_have_recovery(self):
        self.assertIn("not available in direct messages",
                      describe_error(self.i, app_commands.NoPrivateMessage()))
        self.assertIn("own menu", describe_error(self.i, app_commands.CheckFailure()))

    def test_stale_commands_are_not_misdiagnosed_as_permissions(self):
        for error in (app_commands.CommandNotFound("old_command", []),
                      app_commands.CommandSignatureMismatch(SimpleNamespace(name="setup"))):
            message = describe_error(self.i, error)
            self.assertIn("command lists do not match", message)
            self.assertIn("command sync", message)
            self.assertIn("changing channel permissions will not fix", message)

    def test_transform_errors_do_not_echo_supplied_value(self):
        transformer = SimpleNamespace(_error_display_name="Member")
        error = app_commands.TransformerError("PRIVATE_INPUT", discord.AppCommandOptionType.user, transformer)
        message = describe_error(self.i, error)
        self.assertNotIn("PRIVATE_INPUT", message)
        self.assertIn("select the option", message)

    def test_discord_errors_distinguish_denial_missing_item_and_uncertain_outcomes(self):
        denied = describe_error(self.i, http_error(discord.Forbidden))
        missing = describe_error(self.i, http_error(discord.NotFound, code=10008, status=404))
        uncertain = describe_error(self.i, http_error(code=0, status=503))
        self.assertIn("Discord denied", denied)
        self.assertIn("role order", denied)
        self.assertIn("could not be found", missing)
        self.assertIn("not proof", missing)
        self.assertIn("result has not been confirmed", uncertain)
        self.assertIn("before retrying", uncertain)
        for message in (denied, missing, uncertain):
            self.assertNotIn("PRIVATE_API_DETAIL", message)

    def test_expired_and_already_acknowledged_errors_are_explained(self):
        expired = describe_error(self.i, http_error(discord.NotFound, code=10062, status=404))
        acknowledged = describe_error(self.i, http_error(code=40060, status=400))
        self.assertIn("interaction has expired", expired)
        self.assertIn("already received a response", acknowledged)

    def test_unexpected_wrapped_errors_have_private_reference_not_exception_details(self):
        original = RuntimeError("PRIVATE_TOKEN_VALUE")
        wrapped = app_commands.CommandInvokeError(SimpleNamespace(name="setup"), original)
        with self.assertLogs("scoutbot.errors", level="ERROR") as captured:
            message = describe_error(self.i, wrapped)
        self.assertNotIn("PRIVATE_TOKEN_VALUE", message)
        self.assertIn("Completion has not been confirmed", message)
        reference = re.search(r"Reference: ([0-9a-f]{8})", message).group(1)
        self.assertIn(reference, "\n".join(captured.output))
        self.assertIn("Check current records", message)

    def test_timeouts_do_not_assert_that_writes_failed(self):
        message = describe_error(self.i, TimeoutError())
        self.assertIn("may have completed", message)
        self.assertIn("Check current records", message)

    def test_output_is_bounded_and_mentions_neutralized(self):
        message = describe_error(self.i, ValueError("@everyone <@123456789012345678> " + "x" * 6000))
        self.assertLessEqual(len(message), 1900)
        self.assertNotIn("@everyone", message)
        self.assertNotIn("<@123456789012345678>", message)
        self.assertIn("Next step", message)

    def test_guidance_covers_every_existing_root_and_component_context(self):
        for name in ("club", "start_panel", "scout player", "availability set", "schedule create",
                     "attendance mark", "promotion review", "development goal", "assistant task edit"):
            with self.subTest(command=name):
                message = describe_error(interaction(name), ValueError("Specific validation."))
                self.assertIn("Next step", message)
                self.assertIn("/" + name, message)
        self.i.command = None
        self.i.data = {"custom_id": "club:launch:v1"}
        self.assertIn("Command: /club", describe_error(self.i, ValueError("Menu expired.")))
        self.i.data = {}
        self.assertIn("Reopen the original slash command", describe_error(self.i, ValueError("Invalid.")))


class ErrorDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_response_is_private_and_does_not_ping(self):
        i = interaction()
        self.assertTrue(await respond_error(i, ValueError("Bad option.")))
        kwargs = i.response.send_message.call_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())
        i.followup.send.assert_not_awaited()

    async def test_already_deferred_response_uses_private_followup(self):
        i = interaction(done=True)
        self.assertTrue(await respond_error(i, AccessDenied("Not your menu.")))
        i.response.send_message.assert_not_awaited()
        self.assertTrue(i.followup.send.call_args.kwargs["ephemeral"])

    async def test_acknowledgement_race_falls_back_once_to_private_followup(self):
        for error in (http_error(code=40060, status=400), discord.InteractionResponded(interaction())):
            i = interaction()
            i.response.send_message.side_effect = error
            self.assertTrue(await respond_error(i, ValueError("Invalid selection.")))
            i.response.send_message.assert_awaited_once()
            i.followup.send.assert_awaited_once()
            self.assertTrue(i.followup.send.call_args.kwargs["ephemeral"])

    async def test_expired_error_delivery_never_raises_or_attempts_public_fallback(self):
        i = interaction()
        i.response.send_message.side_effect = http_error(discord.NotFound, code=10062, status=404)
        with self.assertLogs("scoutbot.errors", level="WARNING"):
            self.assertFalse(await respond_error(i, ValueError("Invalid.")))
        i.response.send_message.assert_awaited_once()
        i.followup.send.assert_not_awaited()

    async def test_failed_followup_is_logged_without_recursive_error_responses(self):
        i = interaction(done=True)
        i.followup.send.side_effect = http_error(discord.NotFound, code=10015, status=404)
        with self.assertLogs("scoutbot.errors", level="WARNING"):
            self.assertFalse(await respond_error(i, ValueError("Invalid.")))
        i.followup.send.assert_awaited_once()
        i.response.send_message.assert_not_awaited()

    async def test_known_expired_token_is_not_used(self):
        i = interaction()
        i.is_expired.return_value = True
        with self.assertLogs("scoutbot.errors", level="WARNING"):
            self.assertFalse(await respond_error(i, ValueError("Invalid.")))
        i.response.send_message.assert_not_awaited()
        i.followup.send.assert_not_awaited()

    async def test_delivery_network_failure_does_not_trigger_another_action(self):
        for error in (TimeoutError(), aiohttp.ClientConnectionError()):
            i = interaction()
            i.response.send_message.side_effect = error
            with self.assertLogs("scoutbot.errors", level="WARNING"):
                self.assertFalse(await respond_error(i, ValueError("Invalid.")))
            i.followup.send.assert_not_awaited()

    async def test_tree_buttons_and_both_forms_use_shared_handler(self):
        error, i = ValueError("Invalid."), interaction()
        with patch("scoutbot.bot.respond_error", new_callable=AsyncMock) as handler:
            await CommandTree.on_error(None, i, error)
            handler.assert_awaited_once_with(i, error)
        for cls in (SecureView, EvaluationModal, CategoryModal):
            with self.subTest(handler=cls.__name__):
                with patch("scoutbot.views.respond_error", new_callable=AsyncMock) as handler:
                    if cls is SecureView:
                        await cls.on_error(None, i, error, None)
                    else:
                        await cls.on_error(None, i, error)
                    handler.assert_awaited_once_with(i, error)
