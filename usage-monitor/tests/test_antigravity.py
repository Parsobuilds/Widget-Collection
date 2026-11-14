"""Tests for the Antigravity provider (token file + two Google calls), offline."""

from __future__ import annotations

import os
import sys
import unittest
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from fakes import FakeHttp, KeyringMixin, TempProfile, http_error, instance  # noqa: E402

from providers import antigravity, registry  # noqa: E402
from providers.antigravity import AntigravityKind, parse_payload, read_token_file  # noqa: E402
from providers.base import ProviderError  # noqa: E402

TOKEN_FILE = {
    "client_id": "fake-client-id.example",
    "client_secret": "fake-client-secret",
    "project_id": "fake-project",
    "refresh_token": "fake-refresh-token-0000",
    "scopes": ["openid"],
    "token_uri": "https://oauth2.googleapis.com/token",
}
TOKEN_PATH = ".t3/userdata/providers/antigravity/fakeid01/antigravity-acp/acp_token.json"
NOW = datetime(2030, 1, 1, 3, 0, tzinfo=timezone.utc)


class ParseTests(unittest.TestCase):
    def test_sample_rows(self) -> None:
        rows = parse_payload(samples.ANTIGRAVITY, instance("antigravity", "Antigravity"), now=NOW)
        self.assertEqual(
            [r.model_label for r in rows],
            ["Gemini Flash", "Claude Sonnet (Thinking)", "Example Model (used up)"],
        )
        self.assertEqual([r.percent_used for r in rows], [25.0, 50.0, 100])
        self.assertEqual(rows[0].model_id, "example-flash-tiered")
        self.assertEqual(rows[0].reset_text, "resets in 1h 00m")

    def test_duplicate_labels_and_bad_fractions(self) -> None:
        payload = {
            "models": {
                "a": {"displayName": "Same", "quotaInfo": {"remainingFraction": 1.0}},
                "b": {"displayName": "Same", "quotaInfo": {"remainingFraction": 0.1}},
                "c": {"displayName": "Odd", "quotaInfo": {"remainingFraction": "half"}},
            }
        }
        rows = parse_payload(payload, instance("antigravity"))
        self.assertEqual([(r.model_label, r.percent_used) for r in rows], [("Same", 0.0)])

    def test_bad_replies(self) -> None:
        for payload in ({}, {"models": []}, {"models": {"x": {"displayName": "no quota"}}}, None):
            with self.assertRaises(ProviderError):
                parse_payload(payload, instance("antigravity"))


class TokenFileTests(KeyringMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        antigravity._token_cache.clear()

    def test_detect_found(self) -> None:
        with TempProfile() as profile:
            profile.write_json(TOKEN_PATH, TOKEN_FILE)
            result = AntigravityKind().detect()
        self.assertTrue(result.found)
        self.assertTrue(result.path.startswith("%USERPROFILE%\\.t3\\"))
        self.assertNotIn("fake-refresh", repr(result))

    def test_detect_not_found_says_so(self) -> None:
        with TempProfile():
            result = AntigravityKind().detect()
        self.assertFalse(result.found)
        self.assertIn("No Antigravity sign-in found", result.message)

    def test_missing_client_details(self) -> None:
        with TempProfile() as profile:
            details = dict(TOKEN_FILE)
            del details["client_secret"]
            path = profile.write_json(TOKEN_PATH, details)
            with self.assertRaises(ProviderError) as ctx:
                read_token_file(path)
        self.assertEqual(ctx.exception.code, ProviderError.CONFIG)

    def test_untrusted_token_uri_is_replaced(self) -> None:
        with TempProfile() as profile:
            details = dict(TOKEN_FILE, token_uri="https://evil.example.com/token")
            path = profile.write_json(TOKEN_PATH, details)
            self.assertEqual(read_token_file(path)["token_uri"], "https://oauth2.googleapis.com/token")

    def test_fetch_refreshes_then_reads_models(self) -> None:
        token_reply = {"access_token": "fake-access-0000", "expires_in": 3600}
        with TempProfile() as profile, FakeHttp(token_reply, samples.ANTIGRAVITY) as http:
            profile.write_json(TOKEN_PATH, TOKEN_FILE)
            rows = registry.fetch_instance(instance("antigravity", "AG"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(http.requests[0].full_url, "https://oauth2.googleapis.com/token")
        form = urllib.parse.parse_qs(http.requests[0].data.decode())
        self.assertEqual(form["client_id"], ["fake-client-id.example"])
        self.assertEqual(form["grant_type"], ["refresh_token"])
        self.assertEqual(http.header(1, "Authorization"), "Bearer fake-access-0000")
        self.assertIn("fetchAvailableModels", http.requests[1].full_url)

    def test_access_token_is_reused(self) -> None:
        token_reply = {"access_token": "fake-access-0000", "expires_in": 3600}
        with TempProfile() as profile, FakeHttp(token_reply, samples.ANTIGRAVITY, samples.ANTIGRAVITY) as http:
            profile.write_json(TOKEN_PATH, TOKEN_FILE)
            registry.fetch_instance(instance("antigravity"))
            registry.fetch_instance(instance("antigravity"))
        self.assertEqual(len(http.requests), 3)

    def test_401_renews_once_and_retries(self) -> None:
        models_url = antigravity.MODELS_URL
        replies = (
            {"access_token": "fake-old", "expires_in": 3600},
            http_error(models_url, 401),
            {"access_token": "fake-new", "expires_in": 3600},
            samples.ANTIGRAVITY,
        )
        with TempProfile() as profile, FakeHttp(*replies) as http:
            profile.write_json(TOKEN_PATH, TOKEN_FILE)
            rows = registry.fetch_instance(instance("antigravity"))
        self.assertEqual(rows[0].status, "ok")
        self.assertEqual(http.header(3, "Authorization"), "Bearer fake-new")

    def test_expired_sign_in(self) -> None:
        with TempProfile() as profile, FakeHttp(http_error("https://oauth2.googleapis.com/token", 400)):
            profile.write_json(TOKEN_PATH, TOKEN_FILE)
            rows = registry.fetch_instance(instance("antigravity"))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("expired", rows[0].reason)
        self.assertNotIn("fake-refresh", rows[0].reason)

    def test_no_token_file(self) -> None:
        with TempProfile() as profile:
            (profile.home / ".t3").mkdir()
            rows = registry.fetch_instance(instance("antigravity"))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("No Antigravity sign-in found", rows[0].reason)


if __name__ == "__main__":
    unittest.main()
