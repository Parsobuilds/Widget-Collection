"""Tests for secrets_store with an in-memory keyring backend."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keyring  # noqa: E402

from fakes import FAKE_KEY, BrokenKeyring, KeyringMixin  # noqa: E402

import secrets_store  # noqa: E402


class SecretStoreTests(KeyringMixin, unittest.TestCase):
    def test_set_get_delete(self) -> None:
        self.assertIsNone(secrets_store.get_secret("abcd1234"))
        self.assertFalse(secrets_store.has_secret("abcd1234"))
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        self.assertEqual(self.keyring.store[("DesktopWidgets.UsageMonitor", "abcd1234")], FAKE_KEY)
        self.assertEqual(secrets_store.get_secret("abcd1234"), FAKE_KEY)
        self.assertTrue(secrets_store.has_secret("abcd1234"))
        secrets_store.delete_secret("abcd1234")
        self.assertIsNone(secrets_store.get_secret("abcd1234"))

    def test_delete_absent_is_quiet(self) -> None:
        secrets_store.delete_secret("never-set")

    def test_service_name(self) -> None:
        self.assertEqual(secrets_store.SERVICE_NAME, "DesktopWidgets.UsageMonitor")

    def test_backend_errors_never_show_the_secret(self) -> None:
        keyring.set_keyring(BrokenKeyring())
        with self.assertRaises(secrets_store.SecretStoreError) as ctx:
            secrets_store.set_secret("abcd1234", FAKE_KEY)
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertNotIn(FAKE_KEY, repr(ctx.exception))
        with self.assertRaises(secrets_store.SecretStoreError):
            secrets_store.get_secret("abcd1234")
        self.assertFalse(secrets_store.has_secret("abcd1234"))


class RedactTests(unittest.TestCase):
    def test_redacts_given_secrets(self) -> None:
        text = f"failed with key {FAKE_KEY} and {FAKE_KEY}"
        self.assertEqual(secrets_store.redact(text, FAKE_KEY), "failed with key *** and ***")

    def test_longest_secret_first(self) -> None:
        self.assertEqual(secrets_store.redact("abc123456", "abc", "abc123456"), "***")

    def test_redacts_bearer_and_header_shapes(self) -> None:
        out = secrets_store.redact("Authorization: Bearer fake.token.value x-api-key=other-fake-value")
        self.assertNotIn("fake.token.value", out)
        self.assertNotIn("other-fake-value", out)
        self.assertIn("***", out)

    def test_none_and_empty_secrets_ignored(self) -> None:
        self.assertEqual(secrets_store.redact("plain text", None, ""), "plain text")


if __name__ == "__main__":
    unittest.main()
