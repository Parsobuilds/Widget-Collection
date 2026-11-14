"""Tests for the Linux side of Usage Monitor: key store, data folder, key safety.

Everything here runs on Windows and on Linux. Keys are fake, the key store is a
fake (or the keyring "fail"/"null" backends), HTTP is mocked and every file is
written to a temporary folder. The pieces that need ``usage_monitor`` itself
(autostart file, lock, tray label) are in ``test_usage_monitor_ui.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import keyring  # noqa: E402
from keyring.backends import chainer, fail, null  # noqa: E402

from fakes import FAKE_KEY, BrokenKeyring, FakeHttp, KeyringMixin, MemoryKeyring, TempProfile  # noqa: E402

import provider_config  # noqa: E402
import secrets_store  # noqa: E402

# The exact sentence Settings shows on Linux when no Secret Service is there.
LINUX_NO_STORE_MESSAGE = (
    "No key store is available on this computer, so keys cannot be saved. "
    "Install gnome-keyring or another Secret Service provider."
)


def _all_text_under(folder: Path) -> str:
    """Every file below ``folder`` as text, for "is the key anywhere?" checks."""
    parts: list[str] = []
    for path in folder.rglob("*"):
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


class _KeyringRestore(unittest.TestCase):
    """Base class: put the original keyring back after every test."""

    def setUp(self) -> None:
        super().setUp()
        self._old_keyring = keyring.get_keyring()
        self.addCleanup(keyring.set_keyring, self._old_keyring)


class NoKeyStoreTests(_KeyringRestore):
    def _assert_refused(self) -> None:
        self.assertEqual(secrets_store.key_store_problem(), secrets_store.NO_KEY_STORE_MESSAGE)
        self.assertFalse(secrets_store.key_store_available())
        with self.assertRaises(secrets_store.KeyStoreUnavailableError) as ctx:
            secrets_store.set_secret("abcd1234", FAKE_KEY)
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertEqual(str(ctx.exception), secrets_store.NO_KEY_STORE_MESSAGE)
        with self.assertRaises(secrets_store.SecretStoreError):
            secrets_store.get_secret("abcd1234")
        self.assertFalse(secrets_store.has_secret("abcd1234"))

    def test_message_text_on_linux(self) -> None:
        if sys.platform == "win32":
            self.skipTest("the Linux sentence is only used on Linux")
        self.assertEqual(secrets_store.NO_KEY_STORE_MESSAGE, LINUX_NO_STORE_MESSAGE)

    def test_fail_backend_is_refused(self) -> None:
        keyring.set_keyring(fail.Keyring())
        self._assert_refused()

    def test_null_backend_is_refused(self) -> None:
        # The null backend would accept a key and throw it away: that must be a refusal too.
        keyring.set_keyring(null.Keyring())
        self._assert_refused()

    def test_empty_chainer_is_refused(self) -> None:
        # What keyring gives when no real backend was found: a chainer with nothing in it.
        # (Patched rather than subclassed: a subclass would be picked up by keyring's own search.)
        self.enterContext(mock.patch.object(chainer.ChainerBackend, "backends", new=[]))
        keyring.set_keyring(chainer.ChainerBackend())
        self._assert_refused()

    def test_chainer_with_only_fail_is_refused(self) -> None:
        self.enterContext(mock.patch.object(chainer.ChainerBackend, "backends", new=[fail.Keyring()]))
        keyring.set_keyring(chainer.ChainerBackend())
        self._assert_refused()

    def test_backend_that_errors_on_use_is_refused(self) -> None:
        keyring.set_keyring(BrokenKeyring())
        self.assertEqual(secrets_store.key_store_problem(), secrets_store.NO_KEY_STORE_MESSAGE)
        with self.assertRaises(secrets_store.SecretStoreError) as ctx:
            secrets_store.set_secret("abcd1234", FAKE_KEY)
        self.assertNotIn(FAKE_KEY, str(ctx.exception))

    def test_working_backend_is_accepted(self) -> None:
        keyring.set_keyring(MemoryKeyring())
        self.assertEqual(secrets_store.key_store_problem(), "")
        self.assertTrue(secrets_store.key_store_available())

    def test_refusal_never_falls_back_to_a_file(self) -> None:
        keyring.set_keyring(fail.Keyring())
        with TempProfile() as profile:
            with self.assertRaises(secrets_store.KeyStoreUnavailableError):
                secrets_store.set_secret("abcd1234", FAKE_KEY)
            self.assertNotIn(FAKE_KEY, _all_text_under(profile.root))

    def test_delete_with_no_store_is_quiet(self) -> None:
        keyring.set_keyring(fail.Keyring())
        secrets_store.delete_secret("abcd1234")  # nothing could be saved, so nothing to delete

    def test_removing_a_provider_still_works_with_no_store(self) -> None:
        keyring.set_keyring(fail.Keyring())
        config = provider_config.default_config()
        provider_config.add_instance(
            config, provider_config.ProviderInstance("abcd1234", "manual_budget", "Budget")
        )
        provider_config.remove_instance(config, "abcd1234")
        self.assertEqual(config["providers"], [])


class RemoveDeletesCredentialTests(KeyringMixin, unittest.TestCase):
    def test_removing_a_provider_deletes_its_key(self) -> None:
        config = provider_config.default_config()
        provider_config.add_instance(
            config, provider_config.ProviderInstance("abcd1234", "custom_json", "My service")
        )
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        self.assertEqual(len(self.keyring.store), 1)
        provider_config.remove_instance(config, "abcd1234")
        self.assertEqual(self.keyring.store, {})
        self.assertEqual(config["providers"], [])


class LinuxPathTests(unittest.TestCase):
    """provider_config picks the Linux folder when the platform is not Windows."""

    def _as_linux(self):
        return mock.patch.object(provider_config, "sys", mock.Mock(platform="linux"))

    def test_xdg_config_home_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._as_linux(), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            folder = provider_config.data_dir()
            self.assertEqual(folder, Path(tmp) / "DesktopWidgets" / "UsageMonitor")
            self.assertTrue(folder.is_dir())
            self.assertEqual(provider_config.config_path(), folder / "config.json")

    def test_default_is_dot_config_under_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._as_linux(), \
                mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}):
            os.environ.pop("XDG_CONFIG_HOME", None)
            self.assertEqual(provider_config.config_root(), Path(tmp) / ".config")
            self.assertEqual(
                provider_config.data_dir(), Path(tmp) / ".config" / "DesktopWidgets" / "UsageMonitor"
            )

    def test_relative_xdg_value_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._as_linux(), \
                mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp, "XDG_CONFIG_HOME": "relative/dir"}):
            self.assertEqual(provider_config.config_root(), Path(tmp) / ".config")

    def test_first_run_has_no_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._as_linux(), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            self.assertEqual(provider_config.load_config()["providers"], [])
            self.assertEqual(provider_config.list_instances(provider_config.load_config()), [])


class KeyNeverInFilesTests(KeyringMixin, unittest.TestCase):
    """A key typed in Settings reaches the key store only, never config.json or a log."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _pick(self, win, kind_id: str) -> None:
        from PySide6 import QtCore

        for index in range(win._picker.count()):
            if win._picker.itemData(index, QtCore.Qt.ItemDataRole.UserRole) == kind_id:
                win._picker.setCurrentIndex(index)
                return
        self.fail(f"{kind_id} is not in the Add provider list")

    def _fill_custom_provider(self, win) -> None:
        win.start_add()
        self._pick(win, "custom_json")
        win._inputs["__label__"].setText("Local test")
        win._inputs["url"].setText("http://127.0.0.1:9/usage")
        win._inputs["api_key"].setText(FAKE_KEY)
        win._inputs["used_path"].setText("data.used")
        win._inputs["limit_path"].setText("data.limit")

    def test_saved_key_is_only_in_the_key_store(self) -> None:
        import settings_window

        with TempProfile() as profile:
            win = settings_window.SettingsWindow()
            self.addCleanup(win.deleteLater)
            self._fill_custom_provider(win)
            win._save_btn.click()

            config_text = provider_config.config_path().read_text(encoding="utf-8")
            self.assertIn("Local test", config_text)
            self.assertNotIn(FAKE_KEY, config_text)
            instance_id = provider_config.list_instances(provider_config.load_config())[0].instance_id
            self.assertEqual(self.keyring.store[(secrets_store.SERVICE_NAME, instance_id)], FAKE_KEY)
            self.assertNotIn(FAKE_KEY, _all_text_under(profile.root))

            # Removing the provider deletes the key as well.
            yes = settings_window.QtWidgets.QMessageBox.StandardButton.Yes
            with mock.patch.object(settings_window.QtWidgets.QMessageBox, "question", return_value=yes):
                win.remove_instance(instance_id)
            self.assertEqual(self.keyring.store, {})
            self.assertEqual(provider_config.load_config()["providers"], [])

    def test_test_button_result_and_log_never_contain_the_key(self) -> None:
        import settings_window
        from providers import registry

        reply = {"data": {"used": 3, "limit": 10}}
        with TempProfile() as profile, FakeHttp(reply):
            win = settings_window.SettingsWindow()
            self.addCleanup(win.deleteLater)
            self._fill_custom_provider(win)
            instance = win._make_instance("abcd1234", "Local test", win._collect()[1])
            result = registry.test_instance(instance, secret_override=FAKE_KEY)
            self.assertTrue(result.ok, result.message)
            self.assertNotIn(FAKE_KEY, result.message)
            self.assertNotIn(FAKE_KEY, _all_text_under(profile.root))

    def test_no_key_store_blocks_saving_a_key(self) -> None:
        import settings_window

        keyring.set_keyring(fail.Keyring())
        with TempProfile() as profile:
            win = settings_window.SettingsWindow()
            self.addCleanup(win.deleteLater)
            self._fill_custom_provider(win)
            warning = win.findChild(settings_window.QtWidgets.QLabel, "keyStoreProblem")
            self.assertIsNotNone(warning)
            self.assertEqual(warning.text(), secrets_store.NO_KEY_STORE_MESSAGE)
            win._save_btn.click()
            # Refused: nothing in config.json, nothing anywhere on disk.
            self.assertEqual(provider_config.load_config()["providers"], [])
            self.assertNotIn(FAKE_KEY, _all_text_under(profile.root))
            self.assertEqual(win._message.text(), secrets_store.NO_KEY_STORE_MESSAGE)


if __name__ == "__main__":
    unittest.main()
