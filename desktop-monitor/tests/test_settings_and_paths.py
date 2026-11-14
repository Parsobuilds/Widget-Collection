"""Settings dialog, shared background file, autostart, resource_path, disk alerts."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import support  # noqa: E402

dm = support.import_monitor()
import disk_alert  # noqa: E402


class FakeWinreg:
    HKEY_CURRENT_USER = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, *args):
        return "key"

    def SetValueEx(self, key, name, _r, _t, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, key):
        pass


class PathTests(unittest.TestCase):
    def test_data_folder(self):
        self.assertEqual(dm.APP_DIR, Path(support.TEMP_APPDATA) / "DesktopWidgets" / "DesktopMonitor")
        self.assertTrue(dm.APP_DIR.is_dir())

    def test_resource_path_frozen(self):
        with mock.patch.object(sys, "_MEIPASS", r"C:\bundle", create=True):
            self.assertEqual(dm.resource_path("a", "b.txt"), Path(r"C:\bundle", "a", "b.txt"))

    def test_resource_path_source(self):
        self.assertEqual(dm.resource_path("x").parent, Path(dm.__file__).parent)

    def test_autostart_value_name_and_write(self):
        fake = FakeWinreg()
        with mock.patch.object(dm, "winreg", fake):
            self.assertTrue(dm.set_autostart(True))
            self.assertIn("DesktopWidgets.DesktopMonitor", fake.values)
            self.assertTrue(dm.set_autostart(False))
            self.assertNotIn("DesktopWidgets.DesktopMonitor", fake.values)
            self.assertTrue(dm.set_autostart(False))      # removing twice is fine

    def test_autostart_command_frozen(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\Apps\DM.exe"):
            self.assertEqual(dm.autostart_command(), '"C:\\Apps\\DM.exe"')

    def test_helper_not_installed_does_nothing(self):
        with mock.patch.object(dm, "helper_installed", return_value=False), \
                mock.patch("subprocess.Popen") as popen, mock.patch("subprocess.run") as run:
            dm._ensure_helper_running()
            popen.assert_not_called()
            run.assert_not_called()
            self.assertIsNone(dm.helper_cpu_temp())


class BackgroundFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dm_bg_"))
        self.new = self.tmp / "new" / "widget_background.json"
        self.legacy = self.tmp / "legacy" / "widget_background.json"
        self.legacy.parent.mkdir()
        for patch in (mock.patch.object(dm, "WIDGET_BG_CONFIG_PATH", self.new),
                      mock.patch.object(dm, "LEGACY_BG_CONFIG_PATH", self.legacy)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_legacy_is_read_when_new_missing(self):
        self.legacy.write_text(json.dumps({"image_path": "x.png", "blur_radius": 9}))
        self.assertEqual(dm.read_bg_file()["blur_radius"], 9)

    def test_new_wins_over_legacy(self):
        self.legacy.write_text(json.dumps({"blur_radius": 9}))
        self.new.parent.mkdir()
        self.new.write_text(json.dumps({"blur_radius": 3}))
        self.assertEqual(dm.read_bg_file()["blur_radius"], 3)

    def test_save_writes_new_file_only(self):
        legacy_text = json.dumps({"image_path": "old.png", "blur_radius": 9})
        self.legacy.write_text(legacy_text)
        dm.save_custom_bg_settings({"tint_opacity": 0.4})
        saved = json.loads(self.new.read_text())
        self.assertEqual(saved["tint_opacity"], 0.4)
        self.assertEqual(saved["image_path"], "old.png")        # legacy contents carried over
        self.assertEqual(self.legacy.read_text(), legacy_text)  # legacy never modified

    def test_missing_everywhere(self):
        self.assertEqual(dm.read_bg_file(), {})
        self.assertIsNone(dm.custom_bg_settings())


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = support.qapp()

    def make(self, config=None, background=None):
        import settings_dialog
        base = {"feed_label": "FEED", "feed_path": "", "feed_minutes": 10,
                "hidden_drives": [], "disk_alert_enabled": False, "disk_alert_gb": 10.0,
                "opacity": 0.5}
        base.update(config or {})
        return settings_dialog.SettingsDialog(base, ["C:\\", "D:\\"], background or {})

    def test_defaults_roundtrip(self):
        dialog = self.make()
        updates = dialog.config_updates()
        self.assertEqual(updates["feed_label"], "FEED")
        self.assertEqual(updates["feed_minutes"], 10)
        self.assertEqual(updates["hidden_drives"], [])
        self.assertFalse(updates["disk_alert_enabled"])
        self.assertEqual(dialog.background_updates(), {})

    def test_drive_checkboxes(self):
        dialog = self.make({"hidden_drives": ["d:"]})
        self.assertTrue(dialog.drive_checks["C:\\"].isChecked())
        self.assertFalse(dialog.drive_checks["D:\\"].isChecked())
        dialog.drive_checks["C:\\"].setChecked(False)
        self.assertEqual(sorted(dialog.config_updates()["hidden_drives"]), ["C:\\", "D:\\"])

    def test_feed_and_alert_values(self):
        dialog = self.make()
        dialog.feed_label.setText("Bot")
        dialog.feed_path.setText(r"C:\x\feed.log")
        dialog.feed_minutes.setValue(25)
        dialog.alert_enabled.setChecked(True)
        dialog.alert_gb.setValue(15.0)
        updates = dialog.config_updates()
        self.assertEqual((updates["feed_label"], updates["feed_path"], updates["feed_minutes"]),
                         ("Bot", r"C:\x\feed.log", 25))
        self.assertTrue(updates["disk_alert_enabled"])
        self.assertEqual(updates["disk_alert_gb"], 15.0)

    def test_background_only_changed_keys(self):
        dialog = self.make(background={"image_path": "a.png", "blur_radius": 6,
                                       "tint_opacity": 0.35})
        dialog.blur_slider.setValue(20)
        self.assertEqual(dialog.background_updates(), {"blur_radius": 20.0})
        dialog._clear_image()
        dialog.tint_slider.setValue(50)
        self.assertEqual(dialog.background_updates(),
                         {"blur_radius": 20.0, "image_path": "", "tint_opacity": 0.5})

    def test_pictures_dir_exists(self):
        import settings_dialog
        self.assertTrue(os.path.isdir(settings_dialog.pictures_dir()))


class DiskAlertTests(unittest.TestCase):
    @staticmethod
    def rows(free):
        return [{"mount": "C:\\", "free_gb": free}, {"mount": "All disks", "free_gb": None}]

    def test_off_by_default(self):
        self.assertEqual(disk_alert.DiskAlertManager().check(self.rows(1.0)), [])

    def test_alert_cooldown_and_recovery(self):
        manager = disk_alert.DiskAlertManager(threshold_gb=10, cooldown_seconds=100, enabled=True)
        tray = mock.Mock()
        self.assertEqual(manager.check(self.rows(5.0), tray, now=0), ["C:\\"])
        tray.showMessage.assert_called_once()
        self.assertEqual(manager.check(self.rows(5.0), tray, now=50), [])      # cooldown
        self.assertEqual(manager.check(self.rows(5.0), tray, now=150), ["C:\\"])
        self.assertEqual(manager.check(self.rows(10.5), tray, now=200), [])    # not recovered yet
        self.assertEqual(manager.check(self.rows(50.0), tray, now=210), [])    # recovered
        self.assertEqual(manager.check(self.rows(5.0), tray, now=220), ["C:\\"])

    def test_threshold_configurable_and_safe_without_tray(self):
        manager = disk_alert.DiskAlertManager()
        manager.configure(True, 20)
        self.assertEqual(manager.check(self.rows(15.0), None, now=0), ["C:\\"])
        manager.configure(True, "bad")
        self.assertEqual(manager.threshold_gb, 10.0)


if __name__ == "__main__":
    unittest.main()
