"""Tests for the card wiring in usage_monitor.py (offscreen Qt, temp profile).

APPDATA is pointed at a temporary folder *before* usage_monitor is imported,
because the module works out its data folder at import time. Nothing here
touches the real user profile or the network.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="um_ui_test_")
os.environ["APPDATA"] = _TMP
os.environ["XDG_CONFIG_HOME"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from PySide6 import QtWidgets  # noqa: E402

import provider_config  # noqa: E402
import usage_monitor  # noqa: E402
from models import UsageRow  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

STATE = {
    "x": 0, "y": 0, "w": 320, "h": 453, "opacity": 75, "locked": True,
    "autostart": False, "drag_on_desktop": True, "tab": None, "favourites": [],
}


def make_row(pid: str, label: str, model: str, **kw) -> UsageRow:
    return UsageRow(provider_id=pid, provider_label=label, model_id=model,
                    model_label=model.title(), **kw)


class TestPaths(unittest.TestCase):
    def test_data_folder_is_under_desktopwidgets(self) -> None:
        self.assertEqual(usage_monitor.APP_DIR, Path(_TMP) / "DesktopWidgets" / "UsageMonitor")
        self.assertEqual(usage_monitor.STATE_PATH.name, "widget_state.json")
        self.assertNotEqual(usage_monitor.STATE_PATH, provider_config.config_path())

    @unittest.skipUnless(sys.platform == "win32", "the legacy background file is Windows only")
    def test_background_prefers_new_file_and_falls_back_to_legacy(self) -> None:
        self.assertEqual(usage_monitor.background_config_path(), usage_monitor.BG_CONFIG_PATH)
        legacy = usage_monitor.LEGACY_BG_CONFIG_PATH
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("{}", encoding="utf-8")
        try:
            self.assertEqual(usage_monitor.background_config_path(), legacy)
            usage_monitor.BG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            usage_monitor.BG_CONFIG_PATH.write_text("{}", encoding="utf-8")
            self.assertEqual(usage_monitor.background_config_path(), usage_monitor.BG_CONFIG_PATH)
        finally:
            legacy.unlink(missing_ok=True)
            usage_monitor.BG_CONFIG_PATH.unlink(missing_ok=True)

    @unittest.skipUnless(sys.platform == "win32", "Windows exe path")
    def test_autostart_command_frozen_uses_exe(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"X:\Apps\UsageMonitor.exe"):
            self.assertEqual(usage_monitor.autostart_command(), '"X:\\Apps\\UsageMonitor.exe"')
        self.assertEqual(usage_monitor.RUN_VALUE_NAME, "DesktopWidgets.UsageMonitor")

    def test_background_file_has_no_legacy_fallback_on_linux(self) -> None:
        with mock.patch.object(usage_monitor, "IS_WINDOWS", False):
            legacy = usage_monitor.LEGACY_BG_CONFIG_PATH
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text("{}", encoding="utf-8")
            try:
                self.assertEqual(usage_monitor.background_config_path(), usage_monitor.BG_CONFIG_PATH)
            finally:
                legacy.unlink(missing_ok=True)


class TestLinuxAutostartAndLock(unittest.TestCase):
    """The Linux autostart file, single-instance lock and menu label (run on any OS)."""

    def setUp(self) -> None:
        self.addCleanup(usage_monitor.linux_autostart_path().unlink, missing_ok=True)

    def test_file_and_label_names(self) -> None:
        self.assertEqual(usage_monitor.AUTOSTART_FILE_NAME, "desktopwidgets-usage.desktop")
        self.assertEqual(usage_monitor.LOCK_FILE_NAME, "desktopwidgets-usage.lock")
        with mock.patch.object(usage_monitor, "IS_WINDOWS", False):
            self.assertEqual(
                usage_monitor.linux_autostart_path(),
                Path(_TMP) / "autostart" / "desktopwidgets-usage.desktop",
            )

    def test_autostart_label_matches_platform(self) -> None:
        expected = "Start with Windows" if sys.platform == "win32" else "Start when I sign in"
        self.assertEqual(usage_monitor.AUTOSTART_LABEL, expected)

    def test_enable_and_disable_writes_a_desktop_file(self) -> None:
        path = usage_monitor.linux_autostart_path()
        self.assertFalse(usage_monitor.linux_autostart_enabled())
        self.assertTrue(usage_monitor.set_linux_autostart(True))
        text = path.read_text(encoding="utf-8")
        self.assertIn("[Desktop Entry]", text)
        self.assertIn("Type=Application", text)
        self.assertIn("Exec=", text)
        self.assertIn("Terminal=false", text)
        self.assertTrue(usage_monitor.linux_autostart_enabled())
        self.assertTrue(usage_monitor.set_linux_autostart(False))
        self.assertFalse(path.exists())
        self.assertFalse(usage_monitor.linux_autostart_enabled())

    def test_file_switched_off_inside_counts_as_disabled(self) -> None:
        path = usage_monitor.linux_autostart_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Desktop Entry]\nType=Application\nExec=x\nHidden=true\n", encoding="utf-8")
        self.assertFalse(usage_monitor.linux_autostart_enabled())

    def test_exec_quote(self) -> None:
        self.assertEqual(usage_monitor.desktop_exec_quote("python3"), "python3")
        self.assertEqual(usage_monitor.desktop_exec_quote("100%"), "100%%")
        quoted = usage_monitor.desktop_exec_quote("/opt/my app/run.py")
        self.assertTrue(quoted.startswith('"') and quoted.endswith('"'))
        self.assertIn("my app", quoted)

    def test_linux_command_uses_launcher_when_installed(self) -> None:
        with mock.patch.object(usage_monitor, "IS_WINDOWS", False), \
                mock.patch.object(usage_monitor.shutil, "which", return_value="/usr/bin/desktopwidgets-usage"):
            self.assertEqual(usage_monitor.autostart_command(), "/usr/bin/desktopwidgets-usage")

    def test_lock_path_prefers_runtime_dir(self) -> None:
        runtime = tempfile.mkdtemp(prefix="um_runtime_")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            self.assertEqual(usage_monitor.lock_file_path(), Path(runtime) / "desktopwidgets-usage.lock")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertEqual(usage_monitor.lock_file_path(), provider_config.data_dir() / "desktopwidgets-usage.lock")

    @unittest.skipIf(sys.platform == "win32", "fcntl locks are Linux/macOS only")
    def test_second_lock_is_refused_until_the_first_is_released(self) -> None:
        lock_path = Path(tempfile.mkdtemp(prefix="um_lock_")) / "test.lock"
        first = usage_monitor.acquire_instance_lock(lock_path)
        self.assertIsNotNone(first)
        self.assertIsNone(usage_monitor.acquire_instance_lock(lock_path))
        first.close()
        again = usage_monitor.acquire_instance_lock(lock_path)
        self.assertIsNotNone(again)
        again.close()


class TestCollect(unittest.TestCase):
    def test_no_providers_gives_no_rows(self) -> None:
        rows, stale, stamp = usage_monitor.collect_rows(force=True)
        self.assertEqual(rows, [])
        self.assertFalse(stale)
        self.assertRegex(stamp, r"^\d\d:\d\d$")


class TestWidget(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("usage_monitor.save_config")
        patcher.start()
        self.addCleanup(patcher.stop)

    def widget(self, rows=None) -> usage_monitor.UsageMonitorWidget:
        w = usage_monitor.UsageMonitorWidget(dict(STATE), initial_rows=rows or [])
        w.grab()  # runs paintEvent, which fills the click zones
        return w

    def zones(self, w) -> list[str]:
        return [name for _rect, name in w._zones]

    def test_bar_color_thresholds(self) -> None:
        bar_color = usage_monitor.bar_color
        self.assertEqual(bar_color(59.9).name().upper(), "#34D399")
        self.assertEqual(bar_color(60.0).name().upper(), "#F59E0B")
        self.assertEqual(bar_color(85.1).name().upper(), "#EF4444")

    def test_first_run_shows_add_a_provider_button_and_gear(self) -> None:
        w = self.widget()
        self.assertEqual(w._provider_count, 0)
        names = self.zones(w)
        self.assertIn("settings", names)
        self.assertEqual(names.count("settings"), 2)  # gear + "Add a provider" button

    def test_settings_zone_emits_signal(self) -> None:
        w = self.widget()
        seen: list[bool] = []
        w.settings_requested.connect(lambda: seen.append(True))
        w.handle_zone("settings")
        self.assertEqual(seen, [True])

    def test_rows_grouped_by_instance_in_order(self) -> None:
        rows = [
            make_row("bbbb0002", "SECOND", "primary", percent_used=40.0),
            make_row("aaaa0001", "FIRST", "session", percent_used=10.0),
            make_row("bbbb0002", "SECOND", "secondary", percent_used=70.0),
        ]
        w = self.widget(rows)
        names = self.zones(w)
        self.assertEqual(names.count("settings"), 1)  # gear only, no empty state
        stars = [n for n in names if n.startswith("star:")]
        self.assertEqual(stars, ["star:bbbb0002:primary", "star:bbbb0002:secondary",
                                 "star:aaaa0001:session"])

    def test_error_row_paints_without_raising(self) -> None:
        rows = [make_row("cccc0003", "BROKEN", "error", status="error",
                         reason="The service rejected the key (HTTP 401).")]
        w = self.widget(rows)
        self.assertIn("star:cccc0003:error", self.zones(w))

    def test_credits_tab_budget_and_empty(self) -> None:
        w = self.widget([make_row("dddd0004", "PLAIN", "session", percent_used=5.0)])
        w.config["tab"] = "credits"
        w.grab()  # no money rows: "No balances yet." and no crash
        budget = make_row("eeee0005", "BUDGET", "budget", percent_used=50.0,
                          secondary_label="£5.00 left", credit_balance_usd=5.0,
                          credit_cap_usd=10.0, credit_spent_usd=5.0,
                          provider_kind="manual_budget")
        w.on_snapshot([budget], False, "12:00")
        w.grab()
        self.assertEqual(len(w._credits), 1)

    def test_favourites_default_tab_and_order(self) -> None:
        w = self.widget()
        self.assertEqual(w.active_tab(), "all")
        w.config["favourites"] = ["inst0002:primary", "inst0001:session"]
        self.assertEqual(w.active_tab(), "favourites")
        w.move_favourite("inst0001:session", -1)
        self.assertEqual(w.config["favourites"], ["inst0001:session", "inst0002:primary"])


if __name__ == "__main__":
    unittest.main()
