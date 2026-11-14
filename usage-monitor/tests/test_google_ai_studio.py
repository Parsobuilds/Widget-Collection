"""Tests for Google AI Studio request counts read from T3 Code's database."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes import KeyringMixin, TempProfile, instance  # noqa: E402
from test_nanogpt import make_db  # noqa: E402

from providers import registry  # noqa: E402
from providers.base import ProviderError  # noqa: E402
from providers.google_ai_studio_t3 import GoogleAIStudioT3Kind, parse_counts, quota_window  # noqa: E402

NOW = datetime(2030, 1, 2, 10, 0, tzinfo=timezone.utc)  # window started 2030-01-02 08:00 UTC
DB_PATH = ".t3/userdata/statev2.sqlite"


class WindowTests(unittest.TestCase):
    def test_after_reset_hour(self) -> None:
        start, reset = quota_window(NOW)
        self.assertEqual(start, datetime(2030, 1, 2, 8, tzinfo=timezone.utc))
        self.assertEqual(reset, datetime(2030, 1, 3, 8, tzinfo=timezone.utc))

    def test_before_reset_hour_uses_yesterday(self) -> None:
        start, reset = quota_window(datetime(2030, 1, 2, 7, 59, tzinfo=timezone.utc))
        self.assertEqual(start, datetime(2030, 1, 1, 8, tzinfo=timezone.utc))
        self.assertEqual(reset, datetime(2030, 1, 2, 8, tzinfo=timezone.utc))

    def test_naive_is_utc(self) -> None:
        self.assertEqual(quota_window(datetime(2030, 1, 2, 10)), quota_window(NOW))


class ParseCountsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reset = datetime(2030, 1, 3, 8, tzinfo=timezone.utc)

    def test_with_cap(self) -> None:
        rows = parse_counts(instance("google_ai_studio_t3", "Gemini"), {"model-a": 4}, ["model-a", "model-b"],
                            20, self.reset, now=NOW)
        self.assertEqual([r.model_label for r in rows], ["model-a", "model-b"])
        self.assertEqual(rows[0].percent_used, 20.0)
        self.assertEqual(rows[0].secondary_label, "4/20")
        self.assertEqual(rows[1].percent_used, 0.0)
        self.assertEqual(rows[0].reset_text, "resets in 22h 00m")

    def test_cap_clamps(self) -> None:
        rows = parse_counts(instance("google_ai_studio_t3"), {"m": 30}, ["m"], 20, self.reset, now=NOW)
        self.assertEqual(rows[0].percent_used, 100)

    def test_without_cap_counts_only(self) -> None:
        rows = parse_counts(instance("google_ai_studio_t3"), {"m": 3}, ["m"], 0, self.reset, now=NOW)
        self.assertIsNone(rows[0].percent_used)
        self.assertEqual(rows[0].reason, "3 requests today")
        self.assertEqual(rows[0].status, "ok")

    def test_no_models(self) -> None:
        rows = parse_counts(instance("google_ai_studio_t3"), {}, [], 0, self.reset, now=NOW)
        self.assertEqual(rows[0].status, "no_data")
        self.assertEqual(rows[0].model_label, "No requests recorded")


class FetchTests(KeyringMixin, unittest.TestCase):
    def _make(self, profile) -> None:
        make_db(profile.home / DB_PATH, [
            ("google_ai_studio", "model-flash", "2030-01-02T09:00:00Z"),
            ("google_ai_studio", "model-flash", "2030-01-02T09:30:00Z"),
            ("google_ai_studio", "model-pro", "2030-01-02T09:45:00Z"),
            ("google_ai_studio", "model-old", "2029-12-30T09:00:00Z"),
            ("google_ai_studio", "model-ancient", "2029-10-01T09:00:00Z"),
            ("someone_else", "model-flash", "2030-01-02T09:00:00Z"),
        ])

    def test_unavailable_without_database(self) -> None:
        with TempProfile():
            self.assertFalse(GoogleAIStudioT3Kind().is_available())

    def test_models_come_from_database(self) -> None:
        with TempProfile() as profile:
            self._make(profile)
            kind = GoogleAIStudioT3Kind()
            self.assertTrue(kind.is_available())
            rows = kind.fetch(instance("google_ai_studio_t3", settings={"daily_cap": "0"}), None, now=NOW)
        self.assertEqual([r.model_id for r in rows], ["model-flash", "model-pro", "model-old"])
        self.assertEqual([r.reason for r in rows], ["2 requests today", "1 requests today", "0 requests today"])

    def test_daily_cap_setting(self) -> None:
        with TempProfile() as profile:
            self._make(profile)
            rows = GoogleAIStudioT3Kind().fetch(
                instance("google_ai_studio_t3", settings={"daily_cap": "20"}), None, now=NOW
            )
        self.assertEqual(rows[0].percent_used, 10.0)
        self.assertEqual(rows[0].secondary_label, "2/20")

    def test_other_t3_provider_name(self) -> None:
        with TempProfile() as profile:
            self._make(profile)
            rows = GoogleAIStudioT3Kind().fetch(
                instance("google_ai_studio_t3", settings={"t3_provider_instance": "someone_else"}), None, now=NOW
            )
        self.assertEqual([r.model_id for r in rows], ["model-flash"])

    def test_bad_cap_and_registry_never_raises(self) -> None:
        with TempProfile() as profile:
            self._make(profile)
            with self.assertRaises(ProviderError):
                GoogleAIStudioT3Kind().fetch(instance("google_ai_studio_t3", settings={"daily_cap": "-1"}), None)
            rows = registry.fetch_instance(instance("google_ai_studio_t3", settings={"daily_cap": "lots"}))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("must be a number", rows[0].reason)

    def test_corrupt_database_gives_error_row(self) -> None:
        with TempProfile() as profile:
            db = profile.home / DB_PATH
            db.parent.mkdir(parents=True)
            db.write_bytes(b"not a database" * 20)
            rows = registry.fetch_instance(instance("google_ai_studio_t3"))
        self.assertEqual(rows[0].status, "error")


if __name__ == "__main__":
    unittest.main()
