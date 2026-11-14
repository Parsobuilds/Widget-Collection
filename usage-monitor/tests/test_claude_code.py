"""Tests for the Claude Code provider (sign-in file + usage reply), offline."""

from __future__ import annotations

import copy
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from fakes import FakeHttp, KeyringMixin, TempProfile, http_error, instance  # noqa: E402

from cache import UsageCache  # noqa: E402
from providers import registry  # noqa: E402
from providers.base import ProviderError  # noqa: E402
from providers.claude_code import ClaudeCodeKind, parse_payload  # noqa: E402

FAKE_TOKEN = "fake-claude-token-0000"
CREDS = {"claudeAiOauth": {"accessToken": FAKE_TOKEN, "refreshToken": "fake-refresh", "expiresAt": 0}}
NOW = datetime(2030, 1, 1, 3, 0, tzinfo=timezone.utc)


class ParseTests(unittest.TestCase):
    def test_limits_rows_from_sample(self) -> None:
        rows = parse_payload(samples.CLAUDE_CODE, instance("claude_code", "Claude"), now=NOW)
        self.assertEqual([r.model_label for r in rows], ["Current session", "All models", "Opus only"])
        self.assertEqual([r.model_id for r in rows], ["session", "weekly_all", "weekly_scoped_opus"])
        self.assertEqual([r.percent_used for r in rows], [12.0, 34.0, 5.0])
        self.assertEqual(rows[0].resets_at, "2030-01-01T05:00:00.000000+00:00")
        self.assertEqual(rows[0].reset_text, "resets in 2h 00m")
        self.assertTrue(all(r.provider_id == "abcd1234" and r.provider_label == "CLAUDE" for r in rows))
        self.assertTrue(all(r.provider_kind == "claude_code" for r in rows))

    def test_older_titled_shape(self) -> None:
        rows = parse_payload(samples.CLAUDE_CODE_TITLED, instance("claude_code", "Claude"), now=NOW)
        self.assertEqual([r.model_label for r in rows], ["Current session", "All models", "Opus only"])
        self.assertEqual([r.model_id for r in rows], ["session", "weekly_all", "weekly_scoped_example"])
        self.assertEqual(rows[0].resets_at, "2030-01-01T05:00:00Z")

    def test_unknown_kind_gets_readable_label(self) -> None:
        payload = {"limits": [{"kind": "monthly_extra", "percent": 9, "scope": None},
                              {"kind": "weekly_scoped", "percent": 1, "scope": {"model": None, "surface": "Desktop"}}]}
        rows = parse_payload(payload, instance("claude_code"))
        self.assertEqual([r.model_label for r in rows], ["Monthly extra", "Desktop only"])
        self.assertEqual([r.model_id for r in rows], ["monthly_extra", "weekly_scoped_desktop"])

    def test_fallback_to_five_hour_and_seven_day(self) -> None:
        payload = copy.deepcopy(samples.CLAUDE_CODE)
        payload.pop("limits")
        rows = parse_payload(payload, instance("claude_code"), now=NOW)
        self.assertEqual([r.model_label for r in rows], ["5-hour session", "7-day, all models"])
        self.assertEqual([r.percent_used for r in rows], [12.0, 34.0])

    def test_odd_limit_entries_skipped(self) -> None:
        payload = {"limits": [{"title": "No percent"}, "junk", {"title": "Weird", "percent": "abc"},
                              {"title": "Good one", "utilization": 7}]}
        rows = parse_payload(payload, instance("claude_code"))
        self.assertEqual([r.model_label for r in rows], ["Good one"])
        self.assertEqual(rows[0].model_id, "good-one")

    def test_empty_reply_raises(self) -> None:
        for payload in ({}, [], {"limits": []}):
            with self.assertRaises(ProviderError):
                parse_payload(payload, instance("claude_code"))


class DetectAndFetchTests(KeyringMixin, unittest.TestCase):
    def test_detect_found(self) -> None:
        with TempProfile() as profile:
            profile.write_json(".claude/.credentials.json", CREDS)
            result = ClaudeCodeKind().detect()
        self.assertTrue(result.found)
        self.assertEqual(result.path, "%USERPROFILE%\\.claude\\.credentials.json")
        self.assertNotIn(FAKE_TOKEN, repr(result))

    def test_detect_not_found(self) -> None:
        with TempProfile():
            result = ClaudeCodeKind().detect()
        self.assertFalse(result.found)
        self.assertIn("No Claude Code sign-in found", result.message)

    def test_detect_file_without_token(self) -> None:
        with TempProfile() as profile:
            profile.write_json(".claude/.credentials.json", {"claudeAiOauth": {}})
            result = ClaudeCodeKind().detect()
        self.assertFalse(result.found)
        self.assertIn("no sign-in", result.message)

    def test_fetch_sends_token_and_parses(self) -> None:
        with TempProfile() as profile, FakeHttp(samples.CLAUDE_CODE) as http:
            profile.write_json(".claude/.credentials.json", CREDS)
            rows = ClaudeCodeKind().fetch(instance("claude_code", settings={"credentials_path": "%USERPROFILE%\\.claude\\.credentials.json"}), None)
        self.assertEqual(len(rows), 3)
        self.assertEqual(http.header(0, "Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertEqual(http.header(0, "anthropic-beta"), "oauth-2025-04-20")
        self.assertEqual(http.requests[0].full_url, "https://api.anthropic.com/api/oauth/usage")

    def test_missing_file(self) -> None:
        with TempProfile():
            rows = registry.fetch_instance(instance("claude_code"))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("not found at %USERPROFILE%", rows[0].reason)

    def test_malformed_file(self) -> None:
        with TempProfile() as profile:
            path = profile.home / ".claude" / ".credentials.json"
            path.parent.mkdir(parents=True)
            path.write_text("{oops", encoding="utf-8")
            rows = registry.fetch_instance(instance("claude_code"))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("could not be read", rows[0].reason)

    def test_expired_sign_in(self) -> None:
        with TempProfile() as profile, FakeHttp(http_error("https://api.anthropic.com/api/oauth/usage", 401)):
            profile.write_json(".claude/.credentials.json", CREDS)
            rows = registry.fetch_instance(instance("claude_code"))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("Open Claude Code once", rows[0].reason)
        self.assertNotIn(FAKE_TOKEN, rows[0].reason)

    def test_network_error(self) -> None:
        with TempProfile() as profile, FakeHttp(urllib.error.URLError("offline")):
            profile.write_json(".claude/.credentials.json", CREDS)
            rows = registry.fetch_instance(instance("claude_code"))
        self.assertEqual(rows[0].reason, "Could not reach api.anthropic.com. Check your internet connection or the URL.")

    def test_429_falls_back_to_cache(self) -> None:
        with TempProfile() as profile:
            profile.write_json(".claude/.credentials.json", CREDS)
            cache = UsageCache(profile.root / "cache.json")
            item = instance("claude_code", "Claude")
            with FakeHttp(samples.CLAUDE_CODE):
                first = cache.fetch_with_fallback(item.instance_id, lambda: registry.fetch_instance(item))
            with FakeHttp(http_error("https://api.anthropic.com/api/oauth/usage", 429)):
                second = cache.fetch_with_fallback(item.instance_id, lambda: registry.fetch_instance(item))
            self.assertFalse(first.is_stale)
            self.assertTrue(second.is_stale)
            self.assertEqual([r.percent_used for r in second.rows], [12.0, 34.0, 5.0])
            self.assertEqual(second.rows[0].status, "stale")
            self.assertIn("HTTP 429", second.error)
            self.assertNotIn(FAKE_TOKEN, (profile.root / "cache.json").read_text(encoding="utf-8"))

    def test_kind_metadata(self) -> None:
        kind = ClaudeCodeKind()
        self.assertEqual(kind.stability, "unofficial")
        self.assertEqual(kind.stability_note, "Unofficial endpoint used by Claude Code, may stop working.")
        self.assertTrue(kind.supports_detect)
        self.assertGreaterEqual(kind.min_poll_seconds, 600)


if __name__ == "__main__":
    unittest.main()
