"""Linux behaviour: disk filtering, CPU temperature, GPU fallbacks, paths, autostart, lock.

Most tests run on any operating system by mocking psutil and the platform flags;
the ones that need a real Linux (flock, a subprocess selftest) skip elsewhere.
"""

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import support  # noqa: E402

dm = support.import_monitor()

ON_LINUX = sys.platform.startswith("linux")
Part = namedtuple("Part", "device mountpoint fstype opts")
Temp = namedtuple("Temp", "label current high critical")


def part(device, mount, fstype="ext4"):
    return Part(device, mount, fstype, "rw")


class DiskFilterTests(unittest.TestCase):
    def test_pseudo_filesystems_and_loops_are_skipped(self):
        parts = [
            part("/dev/nvme0n1p2", "/"),
            part("/dev/nvme0n1p1", "/boot/efi", "vfat"),
            part("/dev/sda1", "/home"),
            part("/dev/loop3", "/snap/core/123", "squashfs"),
            part("/dev/loop4", "/mnt/img", "ext4"),            # loop device, any mount
            part("snapfuse", "/snap/x/1", "squashfs"),
            part("tmpfs", "/run/user/1000", "tmpfs"),
            part("tmpfs", "/dev/shm", "tmpfs"),
            part("overlay", "/var/lib/docker/overlay2/x", "overlay"),
            part("devtmpfs", "/dev", "devtmpfs"),
            part("portal", "/run/user/1000/doc", "fuse.portal"),
            part("gvfsd-fuse", "/run/user/1000/gvfs", "fuse.gvfsd-fuse"),
            part("proc", "/proc", "proc"),
            part("/dev/sr0", "/media/cd", "iso9660"),
        ]
        mounts = [p.mountpoint for p in dm.linux_partitions(parts)]
        self.assertEqual(mounts, ["/", "/home"])    # EFI partition is left out

    def test_removable_drive_under_run_media_is_kept(self):
        parts = [part("/dev/sdb1", "/run/media/someone/USB", "exfat"),
                 part("tmpfs", "/run/lock", "tmpfs")]
        self.assertEqual([p.mountpoint for p in dm.linux_partitions(parts)],
                         ["/run/media/someone/USB"])

    def test_duplicate_devices_keep_the_shortest_mount(self):
        parts = [part("/dev/sda2", "/var/lib/data"), part("/dev/sda2", "/"),
                 part("/dev/sda2", "/mnt/bind")]
        self.assertEqual([p.mountpoint for p in dm.linux_partitions(parts)], ["/"])

    def test_io_counter_key_is_the_kernel_device_name(self):
        worker = dm.MetricsWorker(1000)
        with mock.patch.object(dm, "IS_WINDOWS", False),                 mock.patch("os.path.realpath", side_effect=lambda p: p.replace("/dev/mapper/vg-root", "/dev/dm-0")):
            self.assertEqual(worker._io_key_for("/", "/dev/nvme0n1p2"), "nvme0n1p2")
            self.assertEqual(worker._io_key_for("/", "/dev/mapper/vg-root"), "dm-0")
            self.assertIsNone(worker._io_key_for("/mnt/nas", "server:/share"))

    def test_empty_fstype_or_mount_is_skipped(self):
        self.assertEqual(dm.linux_partitions([part("/dev/sda1", "/x", ""),
                                              part("/dev/sda2", "", "ext4")]), [])

    def test_fixed_partitions_uses_linux_rules_off_windows(self):
        fake_psutil = mock.Mock()
        fake_psutil.disk_partitions.return_value = [part("/dev/sda1", "/"),
                                                    part("tmpfs", "/tmp", "tmpfs")]
        with mock.patch.object(dm, "IS_WINDOWS", False), \
                mock.patch.object(dm, "psutil", fake_psutil):
            self.assertEqual([p.mountpoint for p in dm.fixed_partitions()], ["/"])

    def test_normalise_mount(self):
        self.assertEqual(dm.normalise_mount("/"), "/")
        self.assertEqual(dm.normalise_mount("/Home/"), "/Home")      # case is kept
        self.assertEqual(dm.normalise_mount("c:\\"), "C:")

    def test_dialog_norm_matches(self):
        import settings_dialog
        self.assertEqual(settings_dialog._norm("/"), "/")
        self.assertEqual(settings_dialog._norm("/home/"), "/home")
        self.assertEqual(settings_dialog._norm("d:\\"), "D:")


class CpuTemperatureTests(unittest.TestCase):
    def test_coretemp_package_wins(self):
        sensors = {"coretemp": [Temp("Core 0", 50.0, None, None),
                                Temp("Package id 0", 61.0, None, None),
                                Temp("Core 1", 55.0, None, None)]}
        self.assertEqual(dm.linux_cpu_temp(sensors), 61.0)

    def test_coretemp_without_package_uses_hottest(self):
        sensors = {"coretemp": [Temp("Core 0", 50.0, None, None), Temp("Core 1", 57.0, None, None)]}
        self.assertEqual(dm.linux_cpu_temp(sensors), 57.0)

    def test_fallback_order(self):
        base = {"k10temp": [Temp("Tctl", 44.0, None, None)],
                "zenpower": [Temp("Tdie", 45.0, None, None)],
                "cpu_thermal": [Temp("", 46.0, None, None)]}
        self.assertEqual(dm.linux_cpu_temp(base), 44.0)
        del base["k10temp"]
        self.assertEqual(dm.linux_cpu_temp(base), 45.0)
        del base["zenpower"]
        self.assertEqual(dm.linux_cpu_temp(base), 46.0)

    def test_other_chips_are_not_cpu(self):
        self.assertIsNone(dm.linux_cpu_temp({"nvme": [Temp("Composite", 40.0, None, None)],
                                             "acpitz": [Temp("", 30.0, None, None)]}))

    def test_nothing_available_is_none_without_error(self):
        self.assertIsNone(dm.linux_cpu_temp({}))
        fake = mock.Mock()
        fake.sensors_temperatures.return_value = {}
        with mock.patch.object(dm, "psutil", fake):
            self.assertIsNone(dm.linux_cpu_temp())

    def test_psutil_without_sensor_support_or_failing(self):
        with mock.patch.object(dm, "psutil", object()):               # no such attribute
            self.assertIsNone(dm.linux_cpu_temp())
        broken = mock.Mock()
        broken.sensors_temperatures.side_effect = OSError("no hwmon")
        with mock.patch.object(dm, "psutil", broken):
            self.assertIsNone(dm.linux_cpu_temp())

    def test_worker_uses_hwmon_and_never_the_helper_off_windows(self):
        worker = dm.MetricsWorker(1000)
        with mock.patch.object(dm, "IS_WINDOWS", False), \
                mock.patch.object(dm, "linux_cpu_temp", return_value=58.0), \
                mock.patch.object(dm, "helper_cpu_temp") as helper:
            self.assertEqual(worker.cpu_temperature(), (58.0, False))
            helper.assert_not_called()


class GpuFallbackTests(unittest.TestCase):
    def make_card(self, root, name="card0", vendor="0x1002", busy=None, vram=None, temp=None):
        device = Path(root) / name / "device"
        device.mkdir(parents=True)
        (device / "vendor").write_text(vendor + "\n")
        if busy is not None:
            (device / "gpu_busy_percent").write_text("%d\n" % busy)
        if vram is not None:
            (device / "mem_info_vram_used").write_text("%d\n" % vram[0])
            (device / "mem_info_vram_total").write_text("%d\n" % vram[1])
        if temp is not None:
            hwmon = device / "hwmon" / "hwmon3"
            hwmon.mkdir(parents=True)
            (hwmon / "temp1_input").write_text("%d\n" % temp)
        return device

    def test_amd_card_readings(self):
        with tempfile.TemporaryDirectory() as root:
            gib = 1024 ** 3
            self.make_card(root, busy=37, vram=(2 * gib, 8 * gib), temp=51000)
            (Path(root) / "card0-DP-1").mkdir()                  # a connector, not a card
            info = dm.read_drm_gpu(root)
        self.assertEqual(info["name"], "AMD graphics")
        self.assertEqual(info["load"], 37.0)
        self.assertEqual(info["temp"], 51.0)
        self.assertEqual((info["vram_used"], info["vram_total"]), (2.0, 8.0))

    def test_card_with_no_readings_gives_dashes(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_card(root, vendor="0x8086")
            info = dm.read_drm_gpu(root)
        self.assertEqual(info["name"], "Intel graphics")
        self.assertIsNone(info["load"])
        self.assertIsNone(info["temp"])
        self.assertIsNone(info["vram_total"])

    def test_no_card_or_no_folder(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(dm.read_drm_gpu(root))
            self.assertIsNone(dm.read_drm_gpu(os.path.join(root, "missing")))

    def test_nvml_missing_means_no_gpu_readings_and_no_error(self):
        worker = dm.MetricsWorker(1000)
        with mock.patch.object(dm, "IS_LINUX", True), mock.patch.object(dm, "pynvml", None), \
                mock.patch.object(dm, "read_drm_gpu", return_value=None):
            worker._prime_static()
            snap = worker.poll()
        for key in ("gpu_load", "gpu_temp", "vram_used", "vram_total", "gpu_name"):
            self.assertIsNone(snap[key], key)
        self.assertFalse(snap["gpu_present"])

    def test_nvml_failing_falls_back_to_drm(self):
        class Broken:
            @staticmethod
            def nvmlInit():
                raise RuntimeError("driver not loaded")

        drm = {"name": "AMD graphics", "load": 12.0, "temp": 40.0,
               "vram_used": 1.0, "vram_total": 4.0}
        worker = dm.MetricsWorker(1000)
        with mock.patch.object(dm, "IS_LINUX", True), mock.patch.object(dm, "pynvml", Broken), \
                mock.patch.object(dm, "read_drm_gpu", return_value=drm):
            worker._prime_static()
            snap = worker.poll()
        self.assertEqual(snap["gpu_name"], "AMD graphics")
        self.assertEqual(snap["gpu_load"], 12.0)
        self.assertTrue(snap["gpu_present"])

    def test_card_keeps_a_gpu_block_on_linux(self):
        widget = dm.MonitorWidget.__new__(dm.MonitorWidget)
        widget._snapshot = {"gpu_present": False}
        with mock.patch.object(dm, "IS_LINUX", True):
            self.assertTrue(widget._show_gpu())
        with mock.patch.object(dm, "IS_LINUX", False):
            self.assertFalse(widget._show_gpu())


class CardControlTests(unittest.TestCase):
    """No tray needed: the gear on the card opens Settings (Linux cards take clicks)."""

    @classmethod
    def setUpClass(cls):
        cls.app = support.qapp()

    def test_clicking_the_gear_opens_settings(self):
        from PySide6 import QtCore, QtTest
        with mock.patch.object(dm, "IS_LINUX", True), mock.patch.object(dm, "save_config"):
            widget = dm.MonitorWidget(dm.normalise_config(dm.load_config()))
            self.addCleanup(widget.close)
            widget.show()
            widget.grab()                                   # paints once, which lays out the zones
            gear = [rect for rect, name in widget._zones if name == "settings"][0]
            widget.open_settings = mock.Mock()
            QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                                    gear.center().toPoint())
            QtTest.QTest.qWait(50)                          # the click schedules open_settings
            widget.open_settings.assert_called_once()

    def test_settings_flag_is_handled_in_main(self):
        source = Path(support.PARENT, "desktop_monitor.py").read_text(encoding="utf-8")
        self.assertIn('"--settings" in sys.argv', source)


class LinuxPathTests(unittest.TestCase):
    def test_xdg_config_home_is_the_root(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(dm, "IS_WINDOWS", False), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": root}):
            self.assertEqual(dm.appdata_root(), Path(root))
            self.assertEqual(dm.autostart_file_path(),
                             Path(root) / "autostart" / "desktopwidgets-desktop.desktop")

    def test_default_is_dot_config_under_home(self):
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(dm, "IS_WINDOWS", False), \
                mock.patch.dict(os.environ, {"HOME": home, "XDG_CONFIG_HOME": ""}):
            self.assertEqual(dm.appdata_root(), Path(home) / ".config")

    def test_empty_home_and_xdg_never_give_a_root_level_folder(self):
        with mock.patch.object(dm, "IS_WINDOWS", False), \
                mock.patch.dict(os.environ, {"HOME": "", "XDG_CONFIG_HOME": ""}):
            folder = dm.appdata_root()
        self.assertNotEqual(folder, Path("/.config"))
        self.assertNotEqual(folder, Path(".config"))
        self.assertTrue(str(folder).endswith(".config"))

    def test_lock_file_location(self):
        with tempfile.TemporaryDirectory() as runtime:
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
                self.assertEqual(dm.lock_file_path(), Path(runtime) / "desktopwidgets-desktop.lock")
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": os.path.join(runtime, "nope")}):
                self.assertEqual(dm.lock_file_path(), dm.APP_DIR / "desktopwidgets-desktop.lock")

    def test_no_shared_background_legacy_fallback_on_linux(self):
        if ON_LINUX:
            self.assertIsNone(dm.LEGACY_BG_CONFIG_PATH)
        self.assertEqual(dm.WIDGET_BG_CONFIG_PATH.name, "widget_background.json")
        self.assertEqual(dm.WIDGET_BG_CONFIG_PATH.parent.name, "DesktopWidgets")


class LinuxAutostartTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        for patcher in (mock.patch.object(dm, "IS_LINUX", True),
                        mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.root.name})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = Path(self.root.name) / "autostart" / "desktopwidgets-desktop.desktop"

    def test_enable_writes_a_valid_entry_and_disable_removes_it(self):
        self.assertTrue(dm.set_autostart(True))
        text = self.target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("[Desktop Entry]\n"))
        self.assertIn("Type=Application\n", text)
        self.assertIn("Terminal=false\n", text)
        exec_line = [line for line in text.splitlines() if line.startswith("Exec=")][0]
        self.assertIn(dm.desktop_entry_quote(sys.executable), exec_line)
        self.assertIn("desktop_monitor.py", exec_line)
        self.assertTrue(dm.set_autostart(False))
        self.assertFalse(self.target.exists())
        self.assertTrue(dm.set_autostart(False))                  # removing twice is fine

    def test_frozen_exec_is_just_the_binary(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "/opt/dw/DesktopMonitor"):
            self.assertEqual(dm.linux_exec_line(), '"/opt/dw/DesktopMonitor"')

    def test_exec_quoting(self):
        self.assertEqual(dm.desktop_entry_quote('/a b/$x"y`z'), '"/a b/\\$x\\"y\\`z"')

    def test_tray_toggle_label(self):
        support.qapp()
        widget = dm.MonitorWidget(dm.normalise_config(dm.load_config()))
        self.addCleanup(widget.close)
        with mock.patch.object(dm.QSystemTrayIcon, "isSystemTrayAvailable", return_value=True):
            icon = dm.build_tray(widget)
        try:
            labels = [a.text() for a in icon.contextMenu().actions()]
        finally:
            icon.hide()
        self.assertIn("Start when I sign in", labels)
        self.assertNotIn("Start with Windows", labels)


class LinuxDragTests(unittest.TestCase):
    """Left-drag moves the card on Linux, locked or not (Wayland and X11)."""

    def setUp(self):
        for patcher in (mock.patch.object(dm, "IS_LINUX", True),
                        mock.patch.object(dm, "save_config")):
            patcher.start()
            self.addCleanup(patcher.stop)
        support.qapp()
        self.widget = dm.MonitorWidget(dm.normalise_config(dm.load_config()))
        self.addCleanup(self.widget.close)

    @staticmethod
    def mouse(kind, x, y):
        from PySide6 import QtCore, QtGui
        point = QtCore.QPointF(x, y)
        buttons = QtCore.Qt.NoButton if kind == QtCore.QEvent.MouseButtonRelease else QtCore.Qt.LeftButton
        return QtGui.QMouseEvent(kind, point, point, QtCore.Qt.LeftButton, buttons,
                                 QtCore.Qt.NoModifier)

    def test_wayland_drag_is_handed_to_the_compositor(self):
        from PySide6 import QtCore
        handle = mock.Mock()
        with mock.patch.object(dm, "wayland_session", return_value=True), \
                mock.patch.object(self.widget, "windowHandle", return_value=handle):
            self.widget.mousePressEvent(self.mouse(QtCore.QEvent.MouseButtonPress, 50, 50))
            self.widget.mouseMoveEvent(self.mouse(QtCore.QEvent.MouseMove, 90, 90))
        handle.startSystemMove.assert_called_once_with()

    def test_small_movement_is_still_a_click(self):
        from PySide6 import QtCore
        handle = mock.Mock()
        with mock.patch.object(dm, "wayland_session", return_value=True), \
                mock.patch.object(self.widget, "windowHandle", return_value=handle):
            self.widget.mousePressEvent(self.mouse(QtCore.QEvent.MouseButtonPress, 50, 50))
            self.widget.mouseMoveEvent(self.mouse(QtCore.QEvent.MouseMove, 52, 51))
        handle.startSystemMove.assert_not_called()

    def test_x11_drag_moves_the_locked_card_and_saves(self):
        from PySide6 import QtCore
        start = self.widget._pos
        self.assertFalse(dm.move_mode(self.widget.config))        # default: locked
        with mock.patch.object(dm, "wayland_session", return_value=False):
            self.widget.mousePressEvent(self.mouse(QtCore.QEvent.MouseButtonPress, 50, 50))
            self.widget.mouseMoveEvent(self.mouse(QtCore.QEvent.MouseMove, 130, 150))
            self.widget.mouseReleaseEvent(self.mouse(QtCore.QEvent.MouseButtonRelease, 130, 150))
        self.assertEqual(self.widget._pos, (start[0] + 80, start[1] + 100))
        dm.save_config.assert_called()

    def test_drag_can_be_switched_off(self):
        from PySide6 import QtCore
        self.widget.config["drag_on_desktop"] = False
        handle = mock.Mock()
        with mock.patch.object(dm, "wayland_session", return_value=True), \
                mock.patch.object(self.widget, "windowHandle", return_value=handle):
            self.widget.mousePressEvent(self.mouse(QtCore.QEvent.MouseButtonPress, 50, 50))
            self.widget.mouseMoveEvent(self.mouse(QtCore.QEvent.MouseMove, 120, 120))
        handle.startSystemMove.assert_not_called()


@unittest.skipUnless(ON_LINUX, "fcntl.flock needs Linux")
class LinuxLockTests(unittest.TestCase):
    def test_second_instance_is_refused_until_the_first_closes(self):
        with tempfile.TemporaryDirectory() as root:
            lock_path = Path(root) / "desktopwidgets-desktop.lock"
            first = dm.acquire_linux_lock(lock_path)
            self.assertIsNotNone(first)
            self.assertIsNone(dm.acquire_linux_lock(lock_path))
            first.close()
            third = dm.acquire_linux_lock(lock_path)
            self.assertIsNotNone(third)
            third.close()

    def test_singleton_uses_the_lock_on_linux(self):
        with tempfile.TemporaryDirectory() as runtime, \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            first = dm.acquire_singleton()
            self.assertIsNotNone(first)
            self.assertTrue((Path(runtime) / "desktopwidgets-desktop.lock").exists())
            self.assertIsNone(dm.acquire_singleton())
            first.close()


class ImportSafetyTests(unittest.TestCase):
    WINDOWS_ONLY = {"wmi", "pythoncom", "winreg", "win32api", "win32con", "win32gui", "win32com"}

    def test_no_windows_only_import_at_module_level(self):
        for name in ("desktop_monitor.py", "disk_alert.py", "settings_dialog.py"):
            tree = ast.parse(Path(support.PARENT, name).read_text(encoding="utf-8"))
            for node in tree.body:                      # only top-level statements
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [(node.module or "").split(".")[0]]
                else:
                    continue
                self.assertFalse(self.WINDOWS_ONLY & set(modules), "%s imports %s" % (name, modules))

    def test_no_segoe_font_on_linux(self):
        with mock.patch.object(dm, "IS_WINDOWS", False):
            self.assertNotIn("Segoe", dm.qt_font(9).family())
            self.assertEqual(dm._font_name(), "")

    def test_disk_alert_imports_and_runs(self):
        import disk_alert
        manager = disk_alert.DiskAlertManager(threshold_gb=10, enabled=True)
        tray = mock.Mock()
        self.assertEqual(manager.check([{"mount": "/home", "free_gb": 2.0}], tray, now=0), ["/home"])
        self.assertIn("/home", tray.showMessage.call_args[0][0])


@unittest.skipUnless(ON_LINUX, "runs the app as a Linux process")
class LinuxSelftestTests(unittest.TestCase):
    def test_selftest_with_empty_home_and_no_xdg(self):
        import platform
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, QT_QPA_PLATFORM="offscreen")
            for name in ("XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"):
                env.pop(name, None)
            result = subprocess.run([sys.executable, str(Path(support.PARENT, "desktop_monitor.py")),
                                     "--selftest"], env=env, capture_output=True, text=True,
                                    timeout=240)
            self.assertEqual(result.returncode, 0, result.stderr[-800:])
            self.assertIn('"host": "%s"' % platform.node(), result.stdout)
            self.assertTrue(Path(home, ".config", "DesktopWidgets", "DesktopMonitor",
                                 "selftest_card.png").is_file())


if __name__ == "__main__":
    unittest.main()
