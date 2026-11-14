"""Tests for the Codex provider (auth.json + wham/usage reply), offline."""

from __future__ import annotations

import copy
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from fakes import FakeHttp, KeyringMixin, TempProfile, http_error, instance  # noqa: E402

from providers import registry  # noqa: E402
from providers.base import ProviderError  # noqa: E402
from providers.codex import CodexKind, default_auth_path, parse_payload, window_name  # noqa: E402

FAKE_ACCESS = "fake-codex-access-0000"
AUTH = {"tokens": {"access_token": FAKE_ACCESS, "account_id": "account-fake-1", "refresh_token": "fake"}}
NOW = datetime(2030, 1, 1, 4, 0, tzinfo=timezone.utc)


class ParseTests(unittest.TestCase):
    def test_sample_rows(self) -> None:
        rows = parse_payload(samples.CODEX, instance("codex", "Codex"), now=NOW)
        self.assertEqual([r.model_id for r in rows], ["primary", "secondary", "code_review"])
        self.assertEqual([r.model_label for r in rows], ["5-hour limit", "7-day limit", "Code review, 7-day limit"])
        self.assertEqual([r.percent_used for r in rows], [42.0, 17.0, 3.0])
        self.assertEqual(rows[0].resets_at, "2030-01-01T05:00:00Z")
        self.assertEqual(rows[0].reset_text, "resets in 1h 00m")
        self.assertTrue(all(r.provider_label == "CODEX" for r in rows))

    def test_reset_after_seconds_when_no_epoch(self) -> None:
        payload = copy.deepcopy(samples.CODEX)
        del payload["rate_limit"]["primary_window"]["reset_at"]
        rows = parse_payload(payload, instance("codex"), now=NOW)
        self.assertEqual(rows[0].resets_at, "2030-01-01T05:00:00Z")

    def test_window_names(self) -> None:
        self.assertEqual(window_name(18000, "x"), "5-hour limit")
        self.assertEqual(window_name(604800, "x"), "7-day limit")
        self.assertEqual(window_name(900, "x"), "15-minute limit")
        self.assertEqual(window_name(None, "Weekly limit"), "Weekly limit")

    def test_empty_and_corrupt(self) -> None:
        for payload in ({}, [], {"rate_limit": {"primary_window": None}}, {"rate_limit": "junk"}):
            with self.assertRaises(ProviderError):
                parse_payload(payload, instance("codex"))


class DetectAndFetchTests(KeyringMixin, unittest.TestCase):
    def test_detect_found(self) -> None:
        with TempProfile() as profile:
            profile.write_json(".codex/auth.json", AUTH)
            result = CodexKind().detect()
        self.assertTrue(result.found)
        self.assertEqual(result.path, "%USERPROFILE%\\.codex\\auth.json")

    def test_detect_not_found(self) -> None:
        with TempProfile():
            result = CodexKind().detect()
        self.assertFalse(result.found)
        self.assertIn("No Codex sign-in found", result.message)

    def test_detect_api_key_only(self) -> None:
        with TempProfile() as profile:
            profile.write_json(".codex/auth.json", {"OPENAI_API_KEY": "fake-not-a-key"})
            result = CodexKind().detect()
        self.assertFalse(result.found)
        self.assertIn("API key", result.message)
        self.assertNotIn("fake-not-a-key", result.message)

    def test_codex_home_is_honoured(self) -> None:
        with TempProfile() as profile:
            other = profile.root / "codexhome"
            other.mkdir()
            (other / "auth.json").write_text('{"tokens": {"access_token": "fake"}}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(other)}):
                path, shown = default_auth_path()
                result = CodexKind().detect()
        self.assertEqual(path, other / "auth.json")
        self.assertEqual(shown, "%CODEX_HOME%\\auth.json")
        self.assertTrue(result.found)

    def test_fetch_headers_and_rows(self) -> None:
        with TempProfile() as profile, FakeHttp(samples.CODEX) as http:
            profile.write_json(".codex/auth.json", AUTH)
            rows = registry.fetch_instance(instance("codex", "Codex"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(http.header(0, "Authorization"), f"Bearer {FAKE_ACCESS}")
        self.assertEqual(http.header(0, "ChatGPT-Account-Id"), "account-fake-1")
        self.assertEqual(http.requests[0].full_url, "https://chatgpt.com/backend-api/wham/usage")

    def test_missing_auth_file(self) -> None:
        with TempProfile():
            rows = registry.fetch_instance(instance("codex"))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("not found", rows[0].reason)

    def test_missing_tokens_and_corrupt_file(self) -> None:
        with TempProfile() as profile:
            profile.write_json(".codex/auth.json", {"tokens": {}})
            rows = registry.fetch_instance(instance("codex"))
            self.assertIn("no ChatGPT sign-in", rows[0].reason)
            (profile.home / ".codex" / "auth.json").write_text("{bad", encoding="utf-8")
            rows = registry.fetch_instance(instance("codex"))
            self.assertIn("could not be read", rows[0].reason)

    def test_network_and_http_errors(self) -> None:
        with TempProfile() as profile:
            profile.write_json(".codex/auth.json", AUTH)
            with FakeHttp(urllib.error.URLError("offline")):
                rows = registry.fetch_instance(instance("codex"))
            self.assertIn("Could not reach chatgpt.com", rows[0].reason)
            with FakeHttp(http_error("https://chatgpt.com/backend-api/wham/usage", 401)):
                rows = registry.fetch_instance(instance("codex"))
            self.assertIn("rejected the Codex sign-in", rows[0].reason)
            with FakeHttp(http_error("https://chatgpt.com/backend-api/wham/usage", 500)):
                rows = registry.fetch_instance(instance("codex"))
            self.assertEqual(rows[0].reason, "The service returned HTTP 500.")
            self.assertNotIn(FAKE_ACCESS, rows[0].reason)


if __name__ == "__main__":
    unittest.main()
