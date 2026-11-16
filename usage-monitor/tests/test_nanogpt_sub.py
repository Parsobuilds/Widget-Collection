"""Subscription fractions, resets and transport are checked offline."""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(__file__))
from fakes import instance
from models import build_credit_rows
from providers import registry
from providers.nanogpt_sub import NanoGPTSubscriptionKind, parse_payload, USAGE_URL

PAYLOAD = {"active": True, "state": "active",
    "limits": {"weeklyInputTokens": 60000000, "dailyImages": 100},
    "weeklyInputTokens": {"used": 18931491, "percentUsed": .31552485, "resetAt": 1795392000000},
    "dailyImages": {"used": 0, "percentUsed": 0, "resetAt": 1794873600000}}


class SubscriptionTests(unittest.TestCase):
    def test_two_quotas_without_credit_card(self):
        rows = parse_payload(PAYLOAD, instance("nanogpt_sub", "Harry"),
                             datetime(2026, 11, 16, tzinfo=timezone.utc))
        self.assertEqual([r.percent_used for r in rows], [31.6, 0])
        self.assertEqual(rows[0].secondary_label, "18.9M / 60M")
        self.assertTrue(rows[0].resets_at.startswith("2026-11-23"))
        self.assertEqual(rows[1].secondary_label, "0 / 100")
        self.assertEqual(build_credit_rows(rows), [])

    def test_http_and_catalogue(self):
        with patch("providers.nanogpt_sub.http_json", return_value=PAYLOAD) as http:
            rows = NanoGPTSubscriptionKind().fetch(instance("nanogpt_sub"), "test-only-key")
        http.assert_called_once_with(USAGE_URL, headers={"x-api-key": "test-only-key", "Accept": "application/json"})
        self.assertNotIn("test-only-key", repr(rows))
        self.assertIsNotNone(registry.get_kind("nanogpt_sub"))

    def test_invalid_one_quota_does_not_hide_the_other(self):
        rows = parse_payload({**PAYLOAD, "weeklyInputTokens": {}}, instance("nanogpt_sub"))
        self.assertEqual(rows[0].status, "error")
        self.assertEqual(rows[1].percent_used, 0)
        self.assertEqual(parse_payload({"active": False}, instance("nanogpt_sub"))[0].status, "no_data")


if __name__ == "__main__":
    unittest.main()
