"""Tests for settings_window.py using fake registry, config and credential store.

Run from the usage-monitor folder:
    ..\\.build-venv\\Scripts\\python.exe -m unittest tests.test_settings_window -v
"""

from __future__ import annotations

import copy
import os
import sys
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import settings_window  # noqa: E402

SHOTS = HERE / "docs_shots"
FAKE_KEY = "sk-fake-1234567890"


# ---------------------------------------------------------------- stand-ins for the real classes
@dataclass
class FieldSpec:
    key: str
    label: str
    kind: str = "text"
    required: bool = True
    default: str = ""
    placeholder: str = ""
    help: str = ""
    choices: list = field(default_factory=list)


@dataclass
class DetectResult:
    found: bool
    path: str = ""
    message: str = ""


@dataclass
class Row:
    model_label: str
    provider_label: str = "X"
    percent_used: float | None = None
    credit_balance_usd: float | None = None


@dataclass
class TestResult:
    __test__ = False  # not a unittest class
    ok: bool
    message: str
    rows: list = field(default_factory=list)


@dataclass
class ProviderInstance:
    instance_id: str
    kind: str
    label: str
    enabled: bool = True
    settings: dict = field(default_factory=dict)


class StubKind:
    kind = ""
    display_name = ""
    category = ""
    description = ""
    stability = "official"
    stability_note = ""
    docs_url = ""
    fields: list = []
    supports_detect = False
    detect_result = DetectResult(False)

    def default_label(self) -> str:
        return self.display_name

    def detect(self) -> DetectResult:
        return self.detect_result


class SigninKind(StubKind):
    kind, display_name, category = "signin_a", "Sign-in Service", "signin"
    description = "Reads the sign-in this PC already has."
    stability, stability_note = "unofficial", "This service has no public API and may stop working."
    supports_detect = True
    fields = [FieldSpec("credentials_path", "Sign-in file", "path")]
    detect_result = DetectResult(True, "%USERPROFILE%\\.svc\\auth.json", "Found your sign-in.")


class ApiKind(StubKind):
    kind, display_name, category = "api_a", "Key Service", "api_key"
    description = "Shows your balance using an API key."
    docs_url = "https://example.com/keys"
    fields = [FieldSpec("api_key", "API key", "secret", placeholder="paste key"),
              FieldSpec("region", "Region", "choice", choices=["eu", "us"], required=False)]


class ManualKind(StubKind):
    kind, display_name, category = "manual_a", "Manual Budget", "budget"
    description = "Track a budget you type in."
    stability = "manual"
    fields = [FieldSpec("cap", "Monthly cap", "number")]


class FakeRegistry:
    def __init__(self) -> None:
        self.kinds = [ManualKind(), ApiKind(), SigninKind()]  # deliberately unordered
        self.result = TestResult(True, "Connected.", [Row("All models", percent_used=42.0)])
        self.calls: list[tuple[Any, Any]] = []

    def catalogue(self):
        return list(self.kinds)

    def get_kind(self, kind: str):
        return next((k for k in self.kinds if k.kind == kind), None)

    def test_instance(self, instance, secret_override=None):
        self.calls.append((instance, secret_override))
        time.sleep(0.05)
        return self.result


class FakeSecretError(Exception):
    pass


class FakeSecrets:
    SecretStoreError = FakeSecretError

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.fail = False

    def set_secret(self, instance_id: str, value: str) -> None:
        if self.fail:
            raise FakeSecretError("Windows Credential Manager is not available.")
        self.store[instance_id] = value

    def has_secret(self, instance_id: str) -> bool:
        return instance_id in self.store

    def delete_secret(self, instance_id: str) -> None:
        self.store.pop(instance_id, None)


class FakeConfig:
    """In-memory provider_config; ``disk`` plays the role of config.json."""
    ProviderInstance = ProviderInstance

    def __init__(self, secrets: FakeSecrets) -> None:
        self.secrets = secrets
        self.disk: dict[str, Any] = {"version": 2, "poll_minutes": 5, "providers": []}
        self.counter = 0

    def load_config(self, path=None):
        return copy.deepcopy(self.disk)

    def save_config(self, config, path=None):
        self.disk = copy.deepcopy(config)

    def list_instances(self, config):
        return [ProviderInstance(**p) for p in config["providers"]]

    def new_instance_id(self, config):
        self.counter += 1
        return f"id{self.counter:06d}"

    def add_instance(self, config, instance):
        config["providers"].append(instance.__dict__.copy())

    def update_instance(self, config, instance):
        config["providers"] = [instance.__dict__.copy() if p["instance_id"] == instance.instance_id
                               else p for p in config["providers"]]

    def remove_instance(self, config, instance_id):
        config["providers"] = [p for p in config["providers"] if p["instance_id"] != instance_id]
        self.secrets.delete_secret(instance_id)

    def set_enabled(self, config, instance_id, enabled):
        for p in config["providers"]:
            if p["instance_id"] == instance_id:
                p["enabled"] = enabled


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _all_text(widget: QtWidgets.QWidget) -> str:
    """All label text under a widget, for leak checks."""
    return "\n".join(lab.text() for lab in widget.findChildren(QtWidgets.QLabel))


class SettingsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.secrets = FakeSecrets()
        self.config = FakeConfig(self.secrets)
        self.registry = FakeRegistry()
        patches = [
            mock.patch("settings_window.registry", self.registry),
            mock.patch("settings_window.provider_config", self.config),
            mock.patch("settings_window.secrets_store", self.secrets),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.win: settings_window.SettingsWindow | None = None
        self.changed = 0

    def make(self) -> settings_window.SettingsWindow:
        win = settings_window.SettingsWindow()
        win.providers_changed.connect(self._bump)
        self.addCleanup(win.deleteLater)
        self.win = win
        return win

    def _bump(self) -> None:
        self.changed += 1

    def seed_two(self) -> None:
        self.config.disk["providers"] = [
            {"instance_id": "aaaa1111", "kind": "signin_a", "label": "Work account",
             "enabled": True, "settings": {"credentials_path": "%USERPROFILE%\\.svc\\auth.json"}},
            {"instance_id": "bbbb2222", "kind": "api_a", "label": "Image credits",
             "enabled": False, "settings": {"region": "eu"}},
        ]
        self.secrets.store["bbbb2222"] = FAKE_KEY

    def pick(self, win, kind_id: str) -> None:
        for i in range(win._picker.count()):
            if win._picker.itemData(i, QtCore.Qt.ItemDataRole.UserRole) == kind_id:
                win._picker.setCurrentIndex(i)
                return
        self.fail("kind not in picker")

    def wait_for(self, cond, timeout: float = 5.0) -> None:
        end = time.time() + timeout
        while time.time() < end and not cond():
            QTest.qWait(20)
        self.assertTrue(cond(), "timed out")

    # ---------------------------------------------------------------- tests
    def test_empty_state(self) -> None:
        win = self.make()
        self.assertIn("No providers yet. Press Add a provider to start.", _all_text(win))

    def test_list_shows_instances(self) -> None:
        self.seed_two()
        win = self.make()
        text = _all_text(win)
        self.assertIn("Work account", text)
        self.assertIn("Image credits", text)
        self.assertIn("Key Service", text)
        self.assertIn("Not tested yet", text)
        self.assertNotIn("No providers yet", text)
        toggles = win.findChildren(QtWidgets.QCheckBox, "rowToggle")
        self.assertEqual([t.isChecked() for t in toggles], [True, False])

    def test_picker_groups_in_order(self) -> None:
        win = self.make()
        win.start_add()
        items = [win._picker.itemText(i).strip() for i in range(win._picker.count())]
        self.assertEqual(items, ["SIGNED IN ON THIS PC", "Sign-in Service", "API KEY",
                                 "Key Service", "SPENDING BUDGETS", "Manual Budget"])

    def test_add_flow_keeps_secret_out_of_config(self) -> None:
        win = self.make()
        win.start_add()
        self.pick(win, "api_a")
        win._inputs["__label__"].setText("My images")
        win._inputs["api_key"].setText(FAKE_KEY)
        win._inputs["region"].setCurrentText("us")
        win._save_btn.click()
        saved = self.config.disk["providers"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["label"], "My images")
        self.assertEqual(saved[0]["settings"], {"region": "us"})
        self.assertNotIn(FAKE_KEY, repr(self.config.disk))
        self.assertEqual(self.secrets.store[saved[0]["instance_id"]], FAKE_KEY)
        self.assertNotIn(FAKE_KEY, _all_text(win))
        self.assertEqual(self.changed, 1)
        self.assertEqual(win._stack.currentIndex(), 0)

    def test_required_field_validation(self) -> None:
        win = self.make()
        win.start_add()
        self.pick(win, "api_a")
        win._save_btn.click()
        self.assertIn("API key", win._message.text())
        self.assertEqual(self.config.disk["providers"], [])
        self.assertEqual(self.changed, 0)
        self.pick(win, "manual_a")
        win._inputs["cap"].setText("12.5")
        win._inputs["__label__"].setText("")
        win._save_btn.click()
        self.assertIn("name", win._message.text())

    def test_secret_store_error_blocks_save(self) -> None:
        self.secrets.fail = True
        win = self.make()
        win.start_add()
        self.pick(win, "api_a")
        win._inputs["api_key"].setText(FAKE_KEY)
        win._save_btn.click()
        self.assertIn("Credential Manager", win._message.text())
        self.assertEqual(self.config.disk["providers"], [])
        self.assertEqual(self.changed, 0)

    def test_detect_fills_path(self) -> None:
        win = self.make()
        win.start_add()
        self.pick(win, "signin_a")
        self.assertFalse(win._detect_btn.isHidden())
        win._detect_btn.click()
        self.assertEqual(win._inputs["credentials_path"].text(), "%USERPROFILE%\\.svc\\auth.json")
        self.assertEqual(win._message.text(), "Found your sign-in.")
        self.assertEqual(win._message.property("role"), "ok")
        # Not found: message in amber, path untouched.
        SigninKind.detect_result = DetectResult(False, "", "Could not find it.")
        self.addCleanup(setattr, SigninKind, "detect_result",
                        DetectResult(True, "%USERPROFILE%\\.svc\\auth.json", "Found your sign-in."))
        win._inputs["credentials_path"].setText("")
        win._detect_btn.click()
        self.assertEqual(win._message.property("role"), "warn")
        self.assertEqual(win._inputs["credentials_path"].text(), "")

    def test_detect_button_hidden_without_support(self) -> None:
        win = self.make()
        win.start_add()
        self.pick(win, "api_a")
        self.assertTrue(win._detect_btn.isHidden())

    def test_test_success_and_failure(self) -> None:
        win = self.make()
        win.start_add()
        self.pick(win, "api_a")
        win._inputs["api_key"].setText(FAKE_KEY)
        win._test_btn.click()
        self.assertFalse(win._test_btn.isEnabled())
        self.assertEqual(win._test_btn.text(), "Testing...")
        self.wait_for(lambda: win._test_btn.isEnabled())
        self.assertIn("Connected.", win._message.text())
        self.assertIn("All models: 42% used", win._message.text())
        self.assertEqual(win._message.property("role"), "ok")
        instance, override = self.registry.calls[-1]
        self.assertEqual(override, FAKE_KEY)
        self.assertNotIn("api_key", instance.settings)
        self.assertEqual(self.config.disk["providers"], [])  # Test never saves

        self.registry.result = TestResult(False, "The key was not accepted.")
        win._test_btn.click()
        self.wait_for(lambda: win._test_btn.isEnabled())
        self.assertEqual(win._message.text(), "The key was not accepted.")
        self.assertEqual(win._message.property("role"), "bad")

    def test_toggle_and_remove(self) -> None:
        self.seed_two()
        win = self.make()
        toggles = win.findChildren(QtWidgets.QCheckBox, "rowToggle")
        toggles[1].setChecked(True)
        self.assertTrue(self.config.disk["providers"][1]["enabled"])
        self.assertEqual(self.changed, 1)

        yes = QtWidgets.QMessageBox.StandardButton.Yes
        no = QtWidgets.QMessageBox.StandardButton.No
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=no) as q:
            win.remove_instance("bbbb2222")
            self.assertIn("Remove Image credits?", q.call_args[0][2])
        self.assertEqual(len(self.config.disk["providers"]), 2)
        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=yes):
            win.remove_instance("bbbb2222")
        self.assertEqual([p["instance_id"] for p in self.config.disk["providers"]], ["aaaa1111"])
        self.assertNotIn("bbbb2222", self.secrets.store)
        self.assertEqual(self.changed, 2)

    def test_notes_unofficial_and_manual(self) -> None:
        win = self.make()
        win.start_add()
        self.pick(win, "signin_a")
        text = _all_text(win._form_host)
        self.assertIn("Unofficial: This service has no public API and may stop working.", text)
        self.pick(win, "manual_a")
        self.assertIn("Manual: you type these numbers yourself; nothing is fetched from the service.",
                      _all_text(win._form_host))
        self.pick(win, "api_a")
        self.assertIn("Where to get this", _all_text(win._form_host))

    def test_edit_keeps_old_key_when_blank(self) -> None:
        self.seed_two()
        win = self.make()
        win.start_edit("bbbb2222")
        key_input = win._inputs["api_key"]
        self.assertEqual(key_input.text(), "")
        self.assertEqual(key_input.placeholderText(), settings_window.SECRET_PLACEHOLDER)
        self.assertEqual(win._inputs["__label__"].text(), "Image credits")
        win._inputs["__label__"].setText("Renamed")
        win._save_btn.click()
        saved = self.config.disk["providers"][1]
        self.assertEqual(saved["label"], "Renamed")
        self.assertFalse(saved["enabled"])  # edit keeps the on/off state
        self.assertEqual(self.secrets.store["bbbb2222"], FAKE_KEY)
        # Typing a new key replaces it.
        win.start_edit("bbbb2222")
        win._inputs["api_key"].setText("sk-fake-new")
        win._save_btn.click()
        self.assertEqual(self.secrets.store["bbbb2222"], "sk-fake-new")
        self.assertNotIn("sk-fake-new", repr(self.config.disk))

    def test_enter_does_not_save(self) -> None:
        win = self.make()
        win.start_add()
        self.pick(win, "manual_a")
        win._inputs["cap"].setText("10")
        QTest.keyClick(win._inputs["cap"], QtCore.Qt.Key.Key_Return)
        self.assertEqual(self.config.disk["providers"], [])

    def test_key_prefix_preserves_trailing_space(self) -> None:
        win = self.make()
        win.start_add()
        prefix_kind = StubKind()
        prefix_kind.kind = "custom_json"
        prefix_kind.display_name = "Custom"
        prefix_kind.category = "custom"
        prefix_kind.fields = [FieldSpec("key_prefix", "Key prefix", default="Bearer ", required=False)]
        win._build_form(prefix_kind, None)
        _label, settings, _secrets = win._collect()
        self.assertEqual(settings["key_prefix"], "Bearer ")

    def test_banned_strings_absent(self) -> None:
        win = self.make()
        src = Path(settings_window.__file__).read_text(encoding="utf-8")
        for banned in ["C:\\Users\\", "D:\\"]:
            self.assertNotIn(banned, src)
        self.assertIsNotNone(win)

    def test_screenshots(self) -> None:
        """Save fake-data screenshots for a visual check."""
        SHOTS.mkdir(exist_ok=True)
        # The offscreen platform may have no fonts; load a Windows one so text is readable.
        from PySide6 import QtGui
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
        if fonts.exists():
            fid = QtGui.QFontDatabase.addApplicationFont(str(fonts))
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid)
            if fams:
                self.app.setFont(QtGui.QFont(fams[0], 10))
        self.seed_two()
        win = self.make()
        win.show()
        QTest.qWait(50)
        self.assertTrue(win.grab().save(str(SHOTS / "settings_list.png")))
        win.start_add()
        self.pick(win, "api_a")
        win._inputs["api_key"].setText("sk-fake-not-real")
        win._test_btn.click()
        self.wait_for(lambda: win._test_btn.isEnabled())
        QTest.qWait(50)
        self.assertTrue(win.grab().save(str(SHOTS / "settings_add.png")))
        win.close()


if __name__ == "__main__":
    unittest.main()
