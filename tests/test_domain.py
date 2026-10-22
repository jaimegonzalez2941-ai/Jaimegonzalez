import unittest

from scoutbot.domain import handle, is_staff, rating, source_allowed
from scoutbot.parser import RecapFormatError, parse_recaps


class RatingTests(unittest.TestCase):
    def test_allowed_boundaries(self):
        for value in ("1", "6.7", "7.3", "8.0", "10"):
            self.assertEqual(rating(value), float(value))

    def test_no_seven_band_and_bad_numbers(self):
        for value in (
            "6.8", "6.9", "7", "7.1", "7.2", "nan", "inf", "-inf",
            "0", "11", "text", "8.55", "8.400", "8e-2",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rating(value)

    def test_handle_normalization(self):
        self.assertEqual(handle("  EA.Handle_23  "), ("ea.handle_23", "EA.Handle_23"))
        for value in ("", " ", "a\nb", "a" * 101):
            with self.assertRaises(ValueError):
                handle(value)


class SecurityTests(unittest.TestCase):
    def test_staff_policy(self):
        self.assertTrue(is_staff(manage_guild=True, role_ids=set(), staff_role=None))
        self.assertTrue(is_staff(manage_guild=False, role_ids={100}, staff_role="100"))
        self.assertFalse(is_staff(manage_guild=False, role_ids={99}, staff_role="100"))
        self.assertFalse(is_staff(manage_guild=False, role_ids={99}, staff_role=None))

    def test_trusted_bot_cannot_be_spoofed_by_user_or_other_channel(self):
        config = {"recap_channel": "10", "source_kind": "bot", "source_id": "20"}
        args = dict(channel_id=10, author_id=20, author_is_bot=True, webhook_id=None)
        self.assertTrue(source_allowed(config, **args))
        for override in (
            {"channel_id": 11}, {"author_id": 21}, {"author_is_bot": False}, {"webhook_id": 20},
        ):
            self.assertFalse(source_allowed(config, **(args | override)))

    def test_webhook_must_match_exact_id(self):
        config = {"recap_channel": "10", "source_kind": "webhook", "source_id": "30"}
        args = dict(channel_id=10, author_id=99, author_is_bot=True, webhook_id=30)
        self.assertTrue(source_allowed(config, **args))
        self.assertFalse(source_allowed(config, **(args | {"webhook_id": None})))
        self.assertFalse(source_allowed(config, **(args | {"webhook_id": 31})))


class ParserTests(unittest.TestCase):
    def test_real_values_and_zero_are_preserved(self):
        player = parse_recaps([
            "**EA.Player** | Rating: 8.4 | Goals: 2 | Assists: 0 | Pass Acc: 88% | Tackles: 3",
        ])[0]
        self.assertEqual(player.player_handle, "EA.Player")
        self.assertEqual((player.rating, player.goals, player.assists, player.passing_acc, player.tackles),
                         (8.4, 2, 0, 88, 3))

    def test_missing_metrics_are_null(self):
        player = parse_recaps(["Player | Goals: 0"])[0]
        self.assertEqual(player.goals, 0)
        self.assertIsNone(player.rating)
        self.assertIsNone(player.assists)

    def test_match_rating_seven_is_allowed(self):
        # The no-seven policy applies only to scouts, never objective match stats.
        self.assertEqual(parse_recaps(["Player | Rating: 7.0"])[0].rating, 7)

    def test_multiple_rows_and_embed_fragments(self):
        self.assertEqual(len(parse_recaps(["Match recap\nA | Goals: 2", "B | Tackles: 4"])), 2)

    def test_unsupported_invalid_duplicate_rows_reject_entire_recap(self):
        for lines in (
            ["No structured statistics"], ["A | 8.4 | 2"],
            ["A | Goals: -1"], ["A | Rating: NaN"],
            ["A | Goals: 1.5"], ["A | Pass Acc: 101%"],
            ["A | Rating: 11"], ["A | Goals: 2 | Saves: 5"],
            ["A | Goals: 2 | Goals: 3"],
            ["A | Goals: 2", "a | Goals: 3"],
            ["A | Goals: 2", "B | Rating: bad"],
        ):
            with self.subTest(lines=lines), self.assertRaises(RecapFormatError):
                parse_recaps(lines)
