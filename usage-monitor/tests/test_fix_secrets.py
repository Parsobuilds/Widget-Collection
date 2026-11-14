"""Regression tests for the review's Scope A findings (A-01 to A-08).

Every finding of the independent review gets one test here, named after its
id. All of them are offline: the key store is a fake (or the keyring "fail"
backend), every key is a fake value, and every file goes to a temporary
folder. No real key store is used, and no real key is written anywhere.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keyring  # noqa: E402
import keyring.errors  # noqa: E402

from fakes import FAKE_KEY, KeyringMixin, MemoryKeyring, TempProfile  # noqa: E402

import provider_config  # noqa: E402
import secrets_store  # noqa: E402

# A fake key that looks like a real one: letters, digits and separators.
LOOKS_LIKE_A_KEY = "FAKE-KEY-0000"

_usage_monitor: Any = None


def load_usage_monitor() -> Any:
    """Import usage_monitor with a throwaway profile.

    The module works out its data folder at import time, so it must not be
    imported with the real user profile. When another test module has already
    imported it, that one is used as it is.
    """
    global _usage_monitor
    if _usage_monitor is None:
        if "usage_monitor" not in sys.modules:
            tmp = tempfile.mkdtemp(prefix="um_fixes_test_")
            os.environ["APPDATA"] = tmp
            os.environ["XDG_CONFIG_HOME"] = tmp
        import usage_monitor

        _usage_monitor = usage_monitor
    return _usage_monitor


class LockedKeyring(MemoryKeyring):
    """A keyring whose collection is locked (the Secret Service answer)."""

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringLocked("Failed to unlock the collection!")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.KeyringLocked("Failed to unlock the collection!")


class CountingKeyring(MemoryKeyring):
    """A keyring that counts how often it is asked for a password."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    def get_password(self, service: str, username: str) -> str | None:
        self.get_calls += 1
        return super().get_password(service, username)


class FakeSecretStorageItem:
    """One item of the fake Secret Service."""

    def __init__(self, username: str) -> None:
        self._attributes = {"service": secrets_store.SERVICE_NAME, "username": username}

    def get_attributes(self) -> dict[str, str]:
        return dict(self._attributes)


class FakeSecretStorageCollection:
    """A fake collection that holds a few items."""

    def __init__(self, usernames: list[str]) -> None:
        self._items = [FakeSecretStorageItem(name) for name in usernames]

    def search_items(self, attributes: dict[str, str]) -> list[FakeSecretStorageItem]:
        service = attributes.get("service")
        return [item for item in self._items
                if item.get_attributes().get("service") == service]


class FakeSecretStorage:
    """A stand-in for the ``secretstorage`` library."""

    def __init__(self, usernames: list[str] | None = None, broken: bool = False) -> None:
        self.usernames = usernames or []
        self.broken = broken
        self.queries: list[dict[str, str]] = []

    def dbus_init(self) -> object:
        return object()

    def get_default_collection(self, bus: object) -> FakeSecretStorageCollection:
        if self.broken:
            raise RuntimeError("the Secret Service is not running")
        return FakeSecretStorageCollection(self.usernames)


# ---------------------------------------------------------------- A-01
class PurgeSecretsTests(KeyringMixin, unittest.TestCase):
    """A-01: the purge must delete every key of the service, not only the ids in config.json."""

    def test_a01_purge_secrets_removes_keys_without_config(self) -> None:
        um = load_usage_monitor()
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        self.assertEqual(len(self.keyring.store), 1)
        with TempProfile():
            # No config.json at all: the key store itself is the only list.
            with mock.patch.object(secrets_store, "list_service_instance_ids",
                                   return_value=["abcd1234"]):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    removed = um.purge_secrets()
        self.assertEqual(removed, 1)
        self.assertEqual(self.keyring.store, {})
        self.assertIn("1", out.getvalue())

    def test_a01_purge_secrets_fails_when_the_store_cannot_be_listed(self) -> None:
        um = load_usage_monitor()
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        with TempProfile():
            with mock.patch.object(secrets_store, "list_service_instance_ids", return_value=None):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    removed = um.purge_secrets()
        if sys.platform == "win32":
            # Windows has no list API: the ids in the config are all it has.
            self.assertEqual(removed, 0)
        else:
            # It must not claim success when it cannot confirm that nothing is left.
            self.assertNotEqual(removed, 0)
        self.assertIn((secrets_store.SERVICE_NAME, "abcd1234"), self.keyring.store)

    def test_a01_list_service_instance_ids_asks_the_secret_service(self) -> None:
        fake = FakeSecretStorage(usernames=["abcd1234", "efgh5678"])
        with mock.patch.dict(sys.modules, {"secretstorage": fake}):
            ids = secrets_store.list_service_instance_ids()
        self.assertEqual(ids, ["abcd1234", "efgh5678"])

    def test_a01_list_service_instance_ids_reports_a_broken_service(self) -> None:
        fake = FakeSecretStorage(broken=True)
        with mock.patch.dict(sys.modules, {"secretstorage": fake}):
            self.assertIsNone(secrets_store.list_service_instance_ids())


# ---------------------------------------------------------------- A-02
class FailedSaveTests(KeyringMixin, unittest.TestCase):
    """A-02: a failed config save must not leave a key behind in the key store."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _fill_custom_provider(self, win: Any) -> None:
        from PySide6 import QtCore

        win.start_add()
        for index in range(win._picker.count()):
            if win._picker.itemData(index, QtCore.Qt.ItemDataRole.UserRole) == "custom_json":
                win._picker.setCurrentIndex(index)
                break
        win._inputs["__label__"].setText("Local test")
        win._inputs["url"].setText("http://127.0.0.1:9/usage")
        win._inputs["api_key"].setText(FAKE_KEY)
        win._inputs["used_path"].setText("data.used")
        win._inputs["limit_path"].setText("data.limit")

    def test_a02_failed_save_leaves_no_key(self) -> None:
        import settings_window

        with TempProfile():
            win = settings_window.SettingsWindow()
            self.addCleanup(win.deleteLater)
            self._fill_custom_provider(win)
            # The config folder is read-only: saving the config fails.
            with mock.patch.object(provider_config, "save_config",
                                   side_effect=OSError("read-only folder")):
                win._save_btn.click()
            self.assertEqual(win._message.text(), "The settings could not be saved.")
            # No provider in the config and no key in the key store.
            self.assertEqual(provider_config.load_config()["providers"], [])
            self.assertEqual(self.keyring.store, {})


# ---------------------------------------------------------------- A-03
class ConfigSecretTests(unittest.TestCase):
    """A-03: a config.json that holds a secret must not keep it."""

    def _write_config(self, data: dict[str, Any]) -> Path:
        path = provider_config.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_a03_secret_in_the_config_is_dropped_on_load_and_save(self) -> None:
        with TempProfile():
            path = self._write_config({
                "version": 2,
                "poll_minutes": 5,
                "providers": [{
                    "instance_id": "abcd1234",
                    "kind": "custom_json",
                    "label": "Hand edited",
                    "settings": {"url": "https://example.com/u", "api_key": LOOKS_LIKE_A_KEY},
                }],
            })
            config = provider_config.load_config()
            self.assertNotIn(LOOKS_LIKE_A_KEY, json.dumps(config))
            self.assertNotIn("api_key", config["providers"][0]["settings"])
            self.assertIn("url", config["providers"][0]["settings"])
            provider_config.save_config(config)
            self.assertNotIn(LOOKS_LIKE_A_KEY, path.read_text(encoding="utf-8"))

    def test_a03_unknown_version_is_refused_and_the_file_is_kept(self) -> None:
        with TempProfile():
            path = self._write_config({
                "version": 99,
                "providers": [{"instance_id": "abcd1234", "kind": "manual_budget", "label": "Future"}],
            })
            config = provider_config.load_config()
            # A file from a newer app is not used blindly.
            self.assertEqual(config["providers"], [])
            self.assertEqual(config["version"], 99)
            # And it is not overwritten either.
            with self.assertRaises(ValueError):
                provider_config.save_config(config)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 99)


# ---------------------------------------------------------------- A-04
class LockedStoreTests(KeyringMixin, unittest.TestCase):
    """A-04: a locked key store is not a missing one."""

    def test_a04_locked_store_has_its_own_message(self) -> None:
        keyring.set_keyring(LockedKeyring())
        secrets_store.reset_key_store_probe()
        problem = secrets_store.key_store_problem()
        if sys.platform == "win32":
            # Windows keeps its own message.
            self.assertEqual(problem, secrets_store.NO_KEY_STORE_MESSAGE)
        else:
            self.assertEqual(problem, secrets_store.LOCKED_KEY_STORE_MESSAGE)
            self.assertIn("Unlock", problem)
            self.assertNotIn("No key store is available", problem)

    def test_a04_locked_store_saving_says_unlock(self) -> None:
        keyring.set_keyring(LockedKeyring())
        with self.assertRaises(secrets_store.SecretStoreError) as ctx:
            secrets_store.set_secret("abcd1234", FAKE_KEY)
        message = str(ctx.exception)
        self.assertNotIn(FAKE_KEY, message)
        if sys.platform == "win32":
            # Windows keeps its own wording.
            self.assertIn("Windows Credential Manager", message)
        else:
            self.assertEqual(message, secrets_store.LOCKED_KEY_STORE_MESSAGE)


# ---------------------------------------------------------------- A-05
class LogRedactionTests(unittest.TestCase):
    """A-05: the log must never hold a key, not even by accident."""

    def test_a05_log_line_redacts_the_key(self) -> None:
        um = load_usage_monitor()
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "usage_monitor.log"
            with mock.patch.object(um, "LOG_PATH", log):
                um.log_line(f"provider poll failed with the key {LOOKS_LIKE_A_KEY}")
                um.log_line("provider poll failed: ValueError")
            text = log.read_text(encoding="utf-8")
        self.assertNotIn(LOOKS_LIKE_A_KEY, text)
        self.assertIn("***", text)
        # The ordinary line is still written as it is.
        self.assertIn("provider poll failed: ValueError", text)


# ---------------------------------------------------------------- A-06
class NoExceptionChainTests(KeyringMixin, unittest.TestCase):
    """A-06: the backend error must not stay in the exception chain."""

    def test_a06_backend_error_is_not_in_the_chain(self) -> None:
        from fakes import BrokenKeyring

        keyring.set_keyring(BrokenKeyring())
        with self.assertRaises(secrets_store.SecretStoreError) as ctx:
            secrets_store.set_secret("abcd1234", FAKE_KEY)
        error = ctx.exception
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertNotIn(FAKE_KEY, str(error))
        self.assertNotIn(FAKE_KEY, repr(error))


# ---------------------------------------------------------------- A-07
class DeleteCountTests(KeyringMixin, unittest.TestCase):
    """A-07: only the ids that really held a key may be counted."""

    def test_a07_delete_all_secrets_counts_only_real_keys(self) -> None:
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        count = secrets_store.delete_all_secrets(["abcd1234", "efgh5678", "ijkl9012"])
        self.assertEqual(count, 1)
        self.assertEqual(self.keyring.store, {})


# ---------------------------------------------------------------- A-08
class ProbeCacheTests(KeyringMixin, unittest.TestCase):
    """A-08: the key store is asked once, not on every form."""

    def test_a08_key_store_problem_is_asked_once(self) -> None:
        backend = CountingKeyring()
        keyring.set_keyring(backend)
        secrets_store.reset_key_store_probe()
        self.assertEqual(secrets_store.key_store_problem(), "")
        self.assertEqual(secrets_store.key_store_problem(), "")
        self.assertEqual(backend.get_calls, 1)

    def test_a08_reset_makes_it_ask_again(self) -> None:
        backend = CountingKeyring()
        keyring.set_keyring(backend)
        secrets_store.reset_key_store_probe()
        secrets_store.key_store_problem()
        secrets_store.reset_key_store_probe()
        secrets_store.key_store_problem()
        self.assertEqual(backend.get_calls, 2)


if __name__ == "__main__":
    unittest.main()
