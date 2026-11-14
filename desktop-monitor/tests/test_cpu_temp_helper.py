"""CpuTempHelper install / uninstall logic with everything external mocked."""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

if not sys.platform.startswith("win"):
    # The CPU temperature helper is Windows-only and is never imported on Linux.
    raise unittest.SkipTest("cpu_temp_helper is Windows-only")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cpu_temp_helper as helper  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class FakeKey:
    pass


class FakeRegistry:
    """Minimal winreg: keys are dicts path -> {name: value}."""
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    KEY_WOW64_64KEY = 0x100
    REG_DWORD = 4

    def __init__(self, keys=None):
        self.keys = {k.lower(): dict(v) for k, v in (keys or {}).items()}
        self.deleted = []

    def OpenKey(self, root, path, reserved=0, access=0):
        if path.lower() not in self.keys:
            raise FileNotFoundError(path)
        key = FakeKey()
        key.path = path.lower()
        return key

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        self.keys.setdefault(path.lower(), {})
        key = FakeKey()
        key.path = path.lower()
        return key

    def QueryValueEx(self, key, name):
        values = self.keys[key.path]
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    def SetValueEx(self, key, name, _r, _t, value):
        self.keys[key.path][name] = value

    def CloseKey(self, key):
        pass

    def DeleteKeyEx(self, root, path, access=0, reserved=0):
        if path.lower() not in self.keys:
            raise FileNotFoundError(path)
        del self.keys[path.lower()]
        self.deleted.append(path)


PAWNIO_PRESENT = {helper.PAWNIO_UNINSTALL_KEY: {"DisplayName": "PawnIO",
                                                "QuietUninstallString": '"C:\\p\\unins.exe" /S'}}


def done(code=0, out="", err=""):
    return mock.Mock(returncode=code, stdout=out, stderr=err)


class TaskXmlTests(unittest.TestCase):
    def test_structure(self):
        xml_text = helper.build_task_xml("PC\\Alice", r"C:\Program Files\DesktopWidgets\CpuTempHelper\CpuTempHelper.exe")
        root = ET.fromstring(xml_text.split("?>", 1)[1])
        self.assertEqual(root.find("t:Principals/t:Principal/t:UserId", NS).text, "PC\\Alice")
        self.assertEqual(root.find("t:Principals/t:Principal/t:RunLevel", NS).text, "HighestAvailable")
        self.assertEqual(root.find("t:Principals/t:Principal/t:LogonType", NS).text, "InteractiveToken")
        self.assertEqual(root.find("t:Settings/t:ExecutionTimeLimit", NS).text, "PT0S")
        self.assertEqual(root.find("t:Settings/t:MultipleInstancesPolicy", NS).text, "IgnoreNew")
        self.assertIsNotNone(root.find("t:Settings/t:RestartOnFailure", NS))
        self.assertEqual(root.find("t:Triggers/t:LogonTrigger/t:Repetition/t:Interval", NS).text, "PT1M")
        self.assertEqual(root.find("t:Triggers/t:LogonTrigger/t:UserId", NS).text, "PC\\Alice")
        self.assertTrue(root.find("t:Actions/t:Exec/t:Command", NS).text.endswith("CpuTempHelper.exe"))

    def test_escaping(self):
        xml_text = helper.build_task_xml("DOM\\A&B <x>", r"C:\Program Files\R&D\a<b>.exe")
        root = ET.fromstring(xml_text.split("?>", 1)[1])        # must still parse
        self.assertEqual(root.find("t:Principals/t:Principal/t:UserId", NS).text, "DOM\\A&B <x>")
        self.assertEqual(root.find("t:Actions/t:Exec/t:Command", NS).text,
                         r"C:\Program Files\R&D\a<b>.exe")
        self.assertEqual(root.find("t:Actions/t:Exec/t:WorkingDirectory", NS).text,
                         r"C:\Program Files\R&D")

    def test_register_task_uses_schtasks_xml_and_cleans_up(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["exists"] = os.path.exists(cmd[cmd.index("/XML") + 1])
            return done()
        helper.register_task("PC\\Alice", r"C:\x\CpuTempHelper.exe", run=fake_run)
        self.assertEqual(seen["cmd"][1:4], ["/Create", "/TN", "DesktopWidgets CPU Temperature"])
        self.assertIn("/F", seen["cmd"])
        self.assertTrue(seen["exists"])
        self.assertFalse(os.path.exists(seen["cmd"][seen["cmd"].index("/XML") + 1]))

    def test_register_task_failure_is_plain_english(self):
        with self.assertRaises(helper.SetupError) as ctx:
            helper.register_task("u", "x.exe", run=lambda *a, **k: done(1, err="Access is denied.\n"))
        self.assertIn("Access is denied", str(ctx.exception))


class PawnioTests(unittest.TestCase):
    def test_present_via_uninstall_entry(self):
        self.assertTrue(helper.pawnio_installed(FakeRegistry(PAWNIO_PRESENT)))

    def test_present_via_service(self):
        self.assertTrue(helper.pawnio_installed(FakeRegistry({helper.PAWNIO_SERVICE_KEY: {}})))

    def test_absent(self):
        self.assertFalse(helper.pawnio_installed(FakeRegistry()))

    def test_decision(self):
        self.assertEqual(helper.decide_pawnio(True, "setup.exe"), ("keep", 0))
        self.assertEqual(helper.decide_pawnio(False, "setup.exe"), ("install", 1))
        self.assertEqual(helper.decide_pawnio(False, None), ("missing", 0))

    def test_install_runs_setup_with_constant_switches(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as handle:
            path = handle.name
        try:
            calls = []
            helper.install_pawnio(path, run=lambda cmd, **k: calls.append(cmd) or done())
            self.assertEqual(calls[0], [path, *helper.PAWNIO_SETUP_ARGS])
            with self.assertRaises(helper.SetupError):
                helper.install_pawnio(path, run=lambda *a, **k: done(5))
        finally:
            os.remove(path)

    def test_uninstall_only_if_installed_by_us(self):
        for flag, expected in ((1, True), (0, False)):
            registry = FakeRegistry(dict(PAWNIO_PRESENT, **{helper.OWN_REG_KEY: {helper.OWN_REG_VALUE: flag}}))
            run = mock.Mock(return_value=done())
            self.assertEqual(helper.uninstall_pawnio(registry, run), expected)
            self.assertEqual(run.called, expected)

    def test_uninstall_without_our_key_leaves_pawnio(self):
        run = mock.Mock()
        self.assertFalse(helper.uninstall_pawnio(FakeRegistry(PAWNIO_PRESENT), run))
        run.assert_not_called()


class ReparseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cth_"))

    def test_plain_folder_ok_and_missing_ok(self):
        helper.ensure_safe_target(self.tmp / "new")
        (self.tmp / "real").mkdir()
        self.assertFalse(helper.is_reparse_point(self.tmp / "real"))

    def test_reparse_flag_refused(self):
        fake = mock.Mock(st_mode=0o040000, st_file_attributes=0x400)
        with mock.patch("os.lstat", return_value=fake):
            self.assertTrue(helper.is_reparse_point(self.tmp))
            with self.assertRaises(helper.SetupError):
                helper.ensure_safe_target(self.tmp / "CpuTempHelper")

    def test_copy_refuses_reparse_target(self):
        source = self.tmp / "src"
        source.mkdir()
        (source / helper.HELPER_EXE_NAME).write_text("x")
        with mock.patch.object(helper, "is_reparse_point", return_value=True), \
                mock.patch("shutil.rmtree") as rmtree, mock.patch("shutil.copytree") as copytree:
            with self.assertRaises(helper.SetupError):
                helper.copy_helper(source, self.tmp / "target")
            rmtree.assert_not_called()
            copytree.assert_not_called()

    def test_copy_replaces_old_copy(self):
        source = self.tmp / "src"
        (source / "lhm").mkdir(parents=True)
        (source / helper.HELPER_EXE_NAME).write_text("new")
        target = self.tmp / "pf" / "CpuTempHelper"
        target.mkdir(parents=True)
        (target / "old.txt").write_text("old")
        helper.copy_helper(source, target, run=lambda *a, **k: done())
        self.assertEqual((target / helper.HELPER_EXE_NAME).read_text(), "new")
        self.assertFalse((target / "old.txt").exists())

    def test_copy_without_exe_fails(self):
        with self.assertRaises(helper.SetupError):
            helper.copy_helper(self.tmp, self.tmp / "t")


class InstallUninstallFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cth_flow_"))
        self.source = self.tmp / "src"
        self.source.mkdir()
        (self.source / helper.HELPER_EXE_NAME).write_text("x")
        self.pf = self.tmp / "pf"
        env = {"ProgramFiles": str(self.pf), "ProgramData": str(self.tmp / "pd")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_install(self, registry, setup="pawnio.exe"):
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            return done()
        with mock.patch.object(
            helper,
            "install_pawnio",
            side_effect=lambda s, run=None, **kw: commands.append(["PAWNIO", s, kw.get("verify_hash")]),
        ):
            helper.do_install("PC\\Alice", setup, source_dir=self.source, run=fake_run,
                              registry=registry)
        return commands

    def test_install_when_pawnio_absent(self):
        registry = FakeRegistry()
        commands = self.run_install(registry)
        self.assertIn(["PAWNIO", "pawnio.exe", True], commands)
        self.assertEqual(registry.keys[helper.OWN_REG_KEY.lower()][helper.OWN_REG_VALUE], 1)
        self.assertTrue((self.pf / "DesktopWidgets" / "CpuTempHelper" / helper.HELPER_EXE_NAME).is_file())
        self.assertTrue(any(c[1:2] == ["/Create"] for c in commands if c[0] != "PAWNIO"))
        self.assertTrue(any(c[1:2] == ["/Run"] for c in commands if c[0] != "PAWNIO"))

    def test_install_when_pawnio_present_does_not_touch_it(self):
        registry = FakeRegistry(PAWNIO_PRESENT)
        commands = self.run_install(registry)
        self.assertNotIn(["PAWNIO", "pawnio.exe", True], commands)
        self.assertEqual(registry.keys[helper.OWN_REG_KEY.lower()][helper.OWN_REG_VALUE], 0)

    def test_install_without_setup_exe_fails_clearly(self):
        with self.assertRaises(helper.SetupError):
            self.run_install(FakeRegistry(), setup=None)

    def test_main_logs_and_returns_nonzero(self):
        with mock.patch.object(helper, "do_install", side_effect=helper.SetupError("No good.")):
            self.assertEqual(helper.main(["--install", "--user", "PC\\A"]), 1)
        log_text = helper.setup_log_path().read_text(encoding="utf-8")
        self.assertIn("No good.", log_text)

    def test_uninstall_removes_everything_and_keeps_foreign_pawnio(self):
        target = self.pf / "DesktopWidgets" / "CpuTempHelper"
        target.mkdir(parents=True)
        (target / "a.txt").write_text("x")
        registry = FakeRegistry(dict(PAWNIO_PRESENT,
                                     **{helper.OWN_REG_KEY: {helper.OWN_REG_VALUE: 0}}))
        commands = []
        helper.do_uninstall(run=lambda cmd, **k: commands.append(cmd) or done(),
                            registry=registry, popen=mock.Mock())
        self.assertFalse(target.exists())
        self.assertTrue(any(c[1:2] == ["/Delete"] for c in commands))
        self.assertNotIn('"C:\\p\\unins.exe" /S', commands)
        self.assertNotIn(helper.OWN_REG_KEY.lower(), registry.keys)

    def test_uninstall_removes_pawnio_we_installed(self):
        registry = FakeRegistry(dict(PAWNIO_PRESENT,
                                     **{helper.OWN_REG_KEY: {helper.OWN_REG_VALUE: 1}}))
        commands = []
        helper.do_uninstall(run=lambda cmd, **k: commands.append(cmd) or done(),
                            registry=registry, popen=mock.Mock())
        self.assertIn('"C:\\p\\unins.exe" /S', commands)


class IntegrityTests(unittest.TestCase):
    def test_hash_check_fails_closed(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"not the dll")
            path = handle.name
        try:
            with self.assertRaises(RuntimeError):
                helper.verify_dll_integrity(path)
            import hashlib
            good = hashlib.sha256(b"not the dll").hexdigest()
            self.assertEqual(helper.verify_dll_integrity(path, good), good)
        finally:
            os.remove(path)
        with self.assertRaises(FileNotFoundError):
            helper.verify_dll_integrity(path + ".missing")

    def test_names(self):
        self.assertEqual(helper.TASK_NAME, "DesktopWidgets CPU Temperature")
        self.assertEqual(helper.MUTEX_NAME, "Local\\DesktopWidgets.CpuTempHelper")
        self.assertEqual(len(helper.EXPECTED_LHM_DLL_SHA256), 64)


if __name__ == "__main__":
    unittest.main()
