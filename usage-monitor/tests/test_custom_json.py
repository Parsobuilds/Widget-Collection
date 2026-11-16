"""Tests for the custom JSON endpoint provider (all HTTP mocked)."""

from __future__ import annotations

import os
import socket
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from fakes import FAKE_KEY, FakeHttp, FakeResponse, KeyringMixin, http_error, instance  # noqa: E402

from providers import registry  # noqa: E402
from providers.base import ProviderError  # noqa: E402
from providers.custom_json import CustomJsonKind  # noqa: E402

URL = "https://usage.example.com/api/usage"


def _settings(**extra: str) -> dict[str, str]:
    base = {
        "url": URL,
        "method": "GET",
        "key_header": "Authorization",
        "key_prefix": "Bearer ",
        "used_path": "data.used",
        "limit_path": "data.limit",
        "balance_path": "",
        "unit": "USD",
    }
    base.update(extra)
    return base


class CustomJsonTests(KeyringMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.kind = CustomJsonKind()

    def _fetch(self, settings: dict[str, str], *replies, secret: str | None = FAKE_KEY):
        with FakeHttp(*replies) as http:
            rows = self.kind.fetch(instance("custom_json", "My API", settings), secret)
        return rows, http

    def test_percent_success(self) -> None:
        rows, http = self._fetch(_settings(), samples.CUSTOM_JSON)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.model_label, "My API")
        self.assertEqual(row.percent_used, 25.0)
        self.assertEqual(row.secondary_label, "$12.50 of $50.00")
        self.assertEqual(row.credit_spent_usd, 12.5)
        self.assertEqual(row.credit_cap_usd, 50.0)
        self.assertEqual(http.header(0, "Authorization"), f"Bearer {FAKE_KEY}")
        self.assertEqual(http.requests[0].get_method(), "GET")

    def test_percent_left_path_draws_a_bar(self) -> None:
        settings = {"url": "https://example.com/u", "percent_left_path": "a.0.left", "unit": "%"}
        rows, _ = self._fetch(settings, {"a": [{"left": 84}]})
        self.assertEqual(rows[0].percent_used, 16.0)
        self.assertEqual(rows[0].secondary_label, "84% left")

    def test_balance_success_with_custom_header_and_post(self) -> None:
        settings = _settings(
            method="POST", key_header="x-api-key", key_prefix="", used_path="", limit_path="",
            balance_path="data.balance",
        )
        rows, http = self._fetch(settings, samples.CUSTOM_JSON)
        row = rows[0]
        self.assertIsNone(row.percent_used)
        self.assertEqual(row.credit_balance_usd, 37.5)
        self.assertEqual(row.reason, "$37.50 left")
        self.assertEqual(http.header(0, "x-api-key"), FAKE_KEY)
        self.assertEqual(http.requests[0].get_method(), "POST")
        self.assertEqual(http.requests[0].data, b"{}")

    def test_list_index_path_and_other_unit(self) -> None:
        rows, _ = self._fetch(_settings(used_path="data.items.0.used", unit="credits"), samples.CUSTOM_JSON)
        row = rows[0]
        self.assertEqual(row.percent_used, 6.0)
        self.assertEqual(row.secondary_label, "3 credits of 50 credits")
        self.assertIsNone(row.credit_spent_usd)

    def test_no_key_sends_no_auth_header(self) -> None:
        _, http = self._fetch(_settings(), samples.CUSTOM_JSON, secret=None)
        self.assertIsNone(http.header(0, "Authorization"))

    def test_auth_failure(self) -> None:
        for code in (401, 403):
            with self.assertRaises(ProviderError) as ctx:
                self._fetch(_settings(), http_error(URL, code))
            self.assertEqual(ctx.exception.code, ProviderError.AUTH)
            self.assertEqual(ctx.exception.message, f"The service rejected the key (HTTP {code}).")

    def test_network_failure(self) -> None:
        for error in (urllib.error.URLError("no route"), socket.timeout("timed out"), ConnectionRefusedError()):
            with self.assertRaises(ProviderError) as ctx:
                self._fetch(_settings(), error)
            self.assertEqual(ctx.exception.code, ProviderError.NETWORK)
            self.assertEqual(
                ctx.exception.message,
                "Could not reach usage.example.com. Check your internet connection or the URL.",
            )

    def test_bad_json(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self._fetch(_settings(), FakeResponse("<html>Sign in</html>"))
        self.assertEqual(ctx.exception.code, ProviderError.BAD_RESPONSE)
        self.assertEqual(ctx.exception.message, "The reply was not JSON.")

    def test_missing_path(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self._fetch(_settings(used_path="data.nothing"), samples.CUSTOM_JSON)
        self.assertEqual(ctx.exception.code, ProviderError.BAD_RESPONSE)
        self.assertEqual(ctx.exception.message, "The reply has no value at 'data.nothing'.")
        with self.assertRaises(ProviderError):
            self._fetch(_settings(used_path="data.items.5.used"), samples.CUSTOM_JSON)

    def test_non_numeric_value(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self._fetch(_settings(), {"data": {"used": "lots", "limit": 50}})
        self.assertEqual(ctx.exception.message, "The value at 'data.used' is not a number.")
        with self.assertRaises(ProviderError):
            self._fetch(_settings(), {"data": {"used": True, "limit": 50}})
        with self.assertRaises(ProviderError):
            self._fetch(_settings(), {"data": {"used": {"x": 1}, "limit": 50}})

    def test_numeric_strings_accepted(self) -> None:
        rows, _ = self._fetch(_settings(), {"data": {"used": "5", "limit": "20"}})
        self.assertEqual(rows[0].percent_used, 25.0)

    def test_zero_limit(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self._fetch(_settings(), {"data": {"used": 1, "limit": 0}})
        self.assertEqual(ctx.exception.code, ProviderError.BAD_RESPONSE)

    def test_oversized_reply(self) -> None:
        big = b'{"data": "' + b"x" * 1_000_001 + b'"}'
        with self.assertRaises(ProviderError) as ctx:
            self._fetch(_settings(), FakeResponse(big))
        self.assertIn("larger than 1 MB", ctx.exception.message)

    def test_http_to_remote_refused_without_a_request(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self._fetch(_settings(url="http://usage.example.com/api"))
        self.assertEqual(ctx.exception.code, ProviderError.CONFIG)
        self.assertIn("only allowed for localhost", ctx.exception.message)

    def test_http_to_localhost_allowed(self) -> None:
        for host in ("localhost", "127.0.0.1", "[::1]"):
            rows, http = self._fetch(_settings(url=f"http://{host}:8080/usage"), samples.CUSTOM_JSON)
            self.assertEqual(rows[0].percent_used, 25.0)

    def test_other_schemes_refused(self) -> None:
        for url in ("ftp://example.com/x", "file:///etc/passwd", "not a url", ""):
            with self.assertRaises(ProviderError) as ctx:
                self._fetch(_settings(url=url))
            self.assertEqual(ctx.exception.code, ProviderError.CONFIG)

    def test_config_errors(self) -> None:
        with self.assertRaises(ProviderError):
            self._fetch(_settings(limit_path=""))
        with self.assertRaises(ProviderError):
            self._fetch(_settings(used_path="", limit_path=""))
        with self.assertRaises(ProviderError):
            self._fetch(_settings(method="DELETE"))
        with self.assertRaises(ProviderError):
            self._fetch(_settings(key_header="Bad Header:"))
        with self.assertRaises(ProviderError) as ctx:
            self._fetch(_settings(), secret="line\r\nInjected: yes")
        self.assertNotIn("Injected", ctx.exception.message)

    def test_redirect_to_other_host_not_followed(self) -> None:
        from providers import base

        handler = base._SafeRedirectHandler()
        request = urllib.request.Request(URL, headers={"Authorization": f"Bearer {FAKE_KEY}"})
        with self.assertRaises(ProviderError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example.net/steal")
        same = handler.redirect_request(request, None, 302, "Found", {}, "https://usage.example.com/v2")
        self.assertEqual(same.full_url, "https://usage.example.com/v2")

    def test_error_text_from_registry_has_no_key(self) -> None:
        with FakeHttp(http_error(URL, 401)):
            result = registry.test_instance(instance("custom_json", "My API", _settings()), secret_override=FAKE_KEY)
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "The service rejected the key (HTTP 401).")


import urllib.request  # noqa: E402  (used in the redirect test)

if __name__ == "__main__":
    unittest.main()
