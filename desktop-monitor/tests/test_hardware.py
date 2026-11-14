"""Graceful hardware: missing pynvml, NVML init failure, WMI failure."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import support  # noqa: E402

dm = support.import_monitor()

# On Linux the worker reads this machine's hwmon / drm. Pin both to "nothing" so
# the "all sensors missing" tests give the same answer on every computer.
_NO_LINUX_HARDWARE = [mock.patch.object(dm, "read_drm_gpu", return_value=None),
                      mock.patch.object(dm, "linux_cpu_temp", return_value=None)]


def setUpModule():
    for patcher in _NO_LINUX_HARDWARE:
        patcher.start()


def tearDownModule():
    for patcher in _NO_LINUX_HARDWARE:
        patcher.stop()


def make_worker(pynvml=None, wmi=None):
    worker = dm.MetricsWorker(1000)
    with mock.patch.object(dm, "pynvml", pynvml), mock.patch.object(dm, "wmi", wmi):
        worker._prime_static()
    return worker


class BrokenWmi:
    """Stands in for the wmi module: every connection raises."""
    @staticmethod
    def WMI(*args, **kwargs):
        raise RuntimeError("WMI is broken")


class RaisingNvml:
    @staticmethod
    def nvmlInit():
        raise RuntimeError("NVML driver not loaded")

    @staticmethod
    def nvmlShutdown():
        pass


class SnapshotTests(unittest.TestCase):
    def check_gpu_none(self, snapshot):
        for key in ("gpu_load", "gpu_temp", "vram_used", "vram_total"):
            self.assertIsNone(snapshot[key], key)
        self.assertFalse(snapshot["gpu_present"])
        self.assertTrue(snapshot["host"])

    def poll(self, pynvml=None, wmi=None):
        worker = make_worker(pynvml=pynvml, wmi=wmi)
        with mock.patch.object(dm, "pynvml", pynvml), mock.patch.object(dm, "wmi", wmi):
            return worker.poll()

    def test_pynvml_missing_and_wmi_missing(self):
        self.check_gpu_none(self.poll())

    def test_nvml_init_raises(self):
        self.check_gpu_none(self.poll(pynvml=RaisingNvml))

    def test_wmi_raises(self):
        snap = self.poll(wmi=BrokenWmi)
        self.check_gpu_none(snap)
        self.assertIsNone(snap["cpu_temp"])

    def test_both_fail(self):
        self.check_gpu_none(self.poll(pynvml=RaisingNvml, wmi=BrokenWmi))

    def test_host_is_this_pc(self):
        import platform
        self.assertEqual(self.poll()["host"], platform.node())


class PaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = support.qapp()

    def paint(self, snapshot):
        config = dm.load_config()
        dm.normalise_config(config)
        widget = dm.MonitorWidget(config)
        widget._snapshot = snapshot
        widget._disk_count = len(snapshot.get("disks") or [])
        widget._min_h = widget._compute_height()
        widget.apply_size()
        widget.show()
        self.app.processEvents()
        pixmap = widget.grab()
        widget.close()
        return pixmap

    def test_paint_with_all_none(self):
        self.assertFalse(self.paint(make_worker().poll()).isNull())

    def test_paint_with_empty_snapshot(self):
        self.assertFalse(self.paint({}).isNull())

    def test_paint_no_gpu_no_disks_is_shorter(self):
        full = self.paint({"gpu_present": True, "disks": [
            {"mount": "C:\\", "total_gb": 100.0, "free_gb": 50.0, "used_pct": 50.0,
             "read_mbps": None, "write_mbps": None}]})
        bare = self.paint({"gpu_present": False, "disks": []})
        self.assertLess(bare.height(), full.height())

    def test_paint_with_failed_wmi_and_nvml(self):
        worker = make_worker(pynvml=RaisingNvml, wmi=BrokenWmi)
        with mock.patch.object(dm, "pynvml", RaisingNvml), mock.patch.object(dm, "wmi", BrokenWmi):
            snap = worker.poll()
        self.assertFalse(self.paint(snap).isNull())


class HelperFunctionTests(unittest.TestCase):
    def test_pick_gpu_name(self):
        self.assertIsNone(dm.pick_gpu_name([]))
        self.assertIsNone(dm.pick_gpu_name(["Microsoft Remote Display Adapter"]))
        self.assertEqual(dm.pick_gpu_name(["Microsoft Remote Display Adapter", "AMD Radeon"]),
                         "AMD Radeon")
        self.assertEqual(dm.pick_gpu_name(["Intel UHD", "NVIDIA RTX"], prefer="nvidia"),
                         "NVIDIA RTX")

    def test_parse_gpu_engine_load(self):
        self.assertIsNone(dm.parse_gpu_engine_load([]))
        rows = [("pid_1_luid_0xA_0xB_phys_0_eng_0_engtype_3D", 30),
                ("pid_2_luid_0xA_0xB_phys_0_eng_0_engtype_3D", 25),
                ("pid_2_luid_0xA_0xB_phys_0_eng_1_engtype_Copy", 70),
                ("garbage", 99)]
        self.assertEqual(dm.parse_gpu_engine_load(rows), 70.0)
        self.assertEqual(dm.parse_gpu_engine_load([("x_luid_0x1_phys_0_engtype_3D", 250)]), 100.0)

    def test_engine_counter_failure_degrades_to_none(self):
        worker = dm.MetricsWorker(1000)
        with mock.patch.object(dm, "pynvml", None), mock.patch.object(dm, "wmi", BrokenWmi):
            data = worker.gpu_data()
        self.assertIsNone(data["load"])

    def test_feed_state(self):
        import tempfile
        import time
        self.assertIsNone(dm.feed_state("", 10))
        self.assertFalse(dm.feed_state(os.path.join(support.TEMP_APPDATA, "nope.txt"), 10))
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name
        try:
            self.assertTrue(dm.feed_state(path, 10))
            self.assertFalse(dm.feed_state(path, 10, now=time.time() + 3600))
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
