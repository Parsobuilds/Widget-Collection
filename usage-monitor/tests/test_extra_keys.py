"""Tests for the extra API-key kinds (OpenRouter, DeepSeek) using the doc samples."""

from __future__ import annotations

import copy
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from fakes import FAKE_KEY, FakeHttp, KeyringMixin, http_error, instance  # noqa: E402

from providers import registry  # noqa: E402
from providers.base import ProviderError  # noqa: E402
from providers.deepseek import DeepSeekKind  # noqa: E402
from providers.deepseek import parse_payload as parse_deepseek  # noqa: E402
from providers.openrouter import OpenRouterKind  # noqa: E402
from providers.openrouter import parse_payload as parse_openrouter  # noqa: E402

NOW = datetime(2030, 1, 1, 20, 0, tzinfo=timezone.utc)


class OpenRouterTests(KeyringMixin, unittest.TestCase):
    def test_key_with_limit(self) -> None:
        rows = parse_openrouter(samples.OPENROUTER, instance("openrouter", "OpenRouter"), now=NOW)
        self.assertEqual([r.model_id for r in rows], ["key_limit", "free_daily"])
        limit = rows[0]
        self.assertEqual(limit.model_label, "Key credit limit")
        self.assertEqual(limit.percent_used, 25.0)
        self.assertEqual(limit.credit_balance_usd, 30.0)
        self.assertEqual(limit.credit_spent_usd, 10.0)
        self.assertEqual(limit.credit_cap_usd, 40.0)
        self.assertEqual(limit.reason, "$30.00 left of $40.00 (monthly)")
        free = rows[1]
        self.assertEqual(free.percent_used, 24.0)
        self.assertEqual(free.secondary_label, "12/50")
        self.assertEqual(free.resets_at, "2030-01-02T00:00:00Z")
        self.assertEqual(free.reset_text, "resets in 4h 00m")

    def test_key_without_limit(self) -> None:
        payload = copy.deepcopy(samples.OPENROUTER)
        payload["data"].update(limit=None, limit_remaining=None, free_model_daily_requests=None)
        rows = parse_openrouter(payload, instance("openrouter"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].model_label, "Spend (no key limit)")
        self.assertIsNone(rows[0].percent_used)
        self.assertEqual(rows[0].reason, "$10.00 this month · $10.00 all time")

    def test_bad_reply(self) -> None:
        for payload in ({}, {"data": "x"}, {"data": {"limit": "lots"}}):
            with self.assertRaises(ProviderError):
                parse_openrouter(payload, instance("openrouter"))

    def test_fetch_uses_bearer(self) -> None:
        with FakeHttp(samples.OPENROUTER) as http:
            result = registry.test_instance(instance("openrouter"), secret_override=FAKE_KEY)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(http.requests[0].full_url, "https://openrouter.ai/api/v1/key")
        self.assertEqual(http.header(0, "Authorization"), f"Bearer {FAKE_KEY}")

    def test_rejected_key(self) -> None:
        with FakeHttp(http_error("https://openrouter.ai/api/v1/key", 401)):
            result = registry.test_instance(instance("openrouter"), secret_override=FAKE_KEY)
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "The service rejected the key (HTTP 401).")

    def test_metadata(self) -> None:
        kind = OpenRouterKind()
        self.assertEqual(kind.stability, "official")
        self.assertTrue(kind.docs_url.startswith("https://openrouter.ai/docs/"))


class DeepSeekTests(KeyringMixin, unittest.TestCase):
    def test_doc_sample_cny(self) -> None:
        rows = parse_deepseek(samples.DEEPSEEK, instance("deepseek", "DeepSeek"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.model_id, "balance_cny")
        self.assertEqual(row.model_label, "Balance (CNY)")
        self.assertEqual(row.reason, "¥42.00 balance (¥2.00 granted)")
        self.assertIsNone(row.credit_balance_usd)  # money fields are US dollars only

    def test_usd_and_unavailable(self) -> None:
        payload = {"is_available": False, "balance_infos": [
            {"currency": "USD", "total_balance": "0.25", "granted_balance": "0.00", "topped_up_balance": "0.25"}]}
        row = parse_deepseek(payload, instance("deepseek"))[0]
        self.assertEqual(row.credit_balance_usd, 0.25)
        self.assertIn("not enough for API calls", row.reason)

    def test_bad_reply(self) -> None:
        for payload in ({}, {"balance_infos": []}, {"balance_infos": [{"total_balance": "x"}]}):
            with self.assertRaises(ProviderError):
                parse_deepseek(payload, instance("deepseek"))

    def test_fetch(self) -> None:
        with FakeHttp(samples.DEEPSEEK) as http:
            result = registry.test_instance(instance("deepseek"), secret_override=FAKE_KEY)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(http.requests[0].full_url, "https://api.deepseek.com/user/balance")
        self.assertEqual(http.header(0, "Authorization"), f"Bearer {FAKE_KEY}")

    def test_metadata(self) -> None:
        self.assertEqual(DeepSeekKind().docs_url, "https://api-docs.deepseek.com/api/get-user-balance")


if __name__ == "__main__":
    unittest.main()
