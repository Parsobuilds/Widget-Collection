"""Unit tests for the pure logic in verify_install.py (no installs, no registry)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_install as vi  # noqa: E402


class ArgParsingTests(unittest.TestCase):
    def test_required_args(self) -> None:
        args = vi.parse_args(["--installer", "setup.exe", "--out", "cards"])
        self.assertEqual(args.installer, Path("setup.exe"))
        self.assertEqual(args.out, Path("cards"))

    def test_missing_args_exit(self) -> None:
        with self.assertRaises(SystemExit):
            vi.parse_args(["--installer", "setup.exe"])


class PathAndCommandTests(unittest.TestCase):
    def test_install_args_with_tasks(self) -> None:
        cmd = vi.install_args(Path("s.exe"), Path("target"), "todo,usage", "startup")
        self.assertIn("/VERYSILENT", cmd)
        self.assertIn("/COMPONENTS=todo,usage", cmd)
        self.assertIn("/TASKS=startup", cmd)
        self.assertIn(f"/DIR={Path('target')}", cmd)

    def test_install_args_without_tasks_and_never_cputemp(self) -> None:
        cmd = vi.install_args(Path("s.exe"), Path("t"), "todo")
        self.assertFalse(any(a.startswith("/TASKS") for a in cmd))
        self.assertFalse(any("cputemp" in a for a in cmd))

    def test_paths(self) -> None:
        self.assertEqual(vi.uninstaller_path(Path("t")), Path("t") / "unins000.exe")
        png = vi.selftest_png_path(Path("ad"), "UsageMonitor.exe")
        self.assertEqual(png, Path("ad") / "DesktopWidgets" / "UsageMonitor" / "selftest_card.png")
        self.assertEqual(vi.marker_path(Path("ad")).parent.name, "TodoWidget")


class RunValueTests(unittest.TestCase):
    def test_all_present(self) -> None:
        fake = {n: "x" for n in vi.RUN_VALUE_NAMES}
        self.assertEqual(vi.present_run_values(fake.get), vi.RUN_VALUE_NAMES)

    def test_none_present(self) -> None:
        self.assertEqual(vi.present_run_values(lambda name: None), [])

    def test_partial(self) -> None:
        fake = {"DesktopWidgets.UsageMonitor": "x"}
        self.assertEqual(vi.present_run_values(fake.get), ["DesktopWidgets.UsageMonitor"])


class ProgramFilesGoneTests(unittest.TestCase):
    def test_missing_and_empty_folder_ok_file_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(vi.program_files_gone(root / "nope"))
            (root / "a" / "b").mkdir(parents=True)
            self.assertTrue(vi.program_files_gone(root))
            (root / "a" / "b" / "f.txt").write_text("x")
            self.assertFalse(vi.program_files_gone(root))


if __name__ == "__main__":
    unittest.main()
