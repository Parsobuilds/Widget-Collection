"""Regression tests for ``installer/linux/install.sh`` and ``uninstall.sh``.

Each test is named after the finding it protects (B-01 to B-11 in
``temp/evidence/final-review/findings.md``). The scripts are run as ``/bin/sh``
against a throwaway ``HOME`` and a small fake widget bundle, so nothing on the
real machine is touched.

Run it with no arguments from this folder::

    cd installer/linux/tests && python -m unittest

The scripts are Linux-only, so the whole file is skipped elsewhere.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:                          # pty is Unix-only, so Windows must still import this file
    import pty
    import select
except ImportError:           # pragma: no cover - only on Windows
    pty = None
    select = None

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
INSTALL_SH = SCRIPTS_DIR / "install.sh"
UNINSTALL_SH = SCRIPTS_DIR / "uninstall.sh"

# A stand-in for a frozen widget. It takes the single-instance lock and waits,
# which is exactly what a running widget looks like to uninstall.sh.
FAKE_BINARY = """#!/usr/bin/env python3
'''Fake widget used by the installer tests: hold the lock, then wait.'''
import fcntl
import os
import sys
import time

if "--purge-secrets" in sys.argv:
    sys.exit(0)                     # the Usage Monitor's key-store cleanup
lock_path = os.environ.get("FAKE_WIDGET_LOCK")
if not lock_path:
    sys.exit(0)                     # nothing to hold: just end
handle = open(lock_path, "a+")
fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
time.sleep(600)
"""

APP_FOR_KEY = {"todo": "TodoWidget", "usage": "UsageMonitor", "desktop": "DesktopMonitor"}


@unittest.skipUnless(sys.platform.startswith("linux"), "install.sh is a Linux script")
class InstallScriptsTest(unittest.TestCase):
    """One test per finding, plus two smoke tests for the old behaviour."""

    # ---- throwaway world ---------------------------------------------------
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dw-installer-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.data_home = self.tmp / "xdg-data"
        self.config_home = self.tmp / "xdg-config"
        self.runtime_dir = self.tmp / "xdg-run"
        for folder in (self.home, self.data_home, self.config_home, self.runtime_dir):
            folder.mkdir(parents=True)
        os.chmod(self.runtime_dir, 0o700)
        self.env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.data_home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "XDG_RUNTIME_DIR": str(self.runtime_dir),
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
        self.bundle = self.tmp / "bundle"
        self.make_bundle(self.bundle)

    def make_bundle(self, folder: Path, icons: bool = True) -> None:
        """Build the unpacked release layout: scripts, DesktopWidgets/, icons/."""
        folder.mkdir(parents=True)
        for script in (INSTALL_SH, UNINSTALL_SH):
            shutil.copy2(script, folder / script.name)
        payload = folder / "DesktopWidgets"
        (payload / "_internal").mkdir(parents=True)
        (payload / "_internal" / "libfake.so").write_text("fake")
        for binary in APP_FOR_KEY.values():
            program = payload / binary
            program.write_text(FAKE_BINARY)
            program.chmod(0o755)
        if icons:
            (folder / "icons").mkdir()
            for key in APP_FOR_KEY:
                (folder / "icons" / f"desktopwidgets-{key}.png").write_bytes(
                    b"\x89PNG\r\n\x1a\n")

    @property
    def prefix(self) -> Path:
        return self.data_home / "DesktopWidgets"

    def launcher(self, key: str) -> Path:
        return self.home / ".local" / "bin" / f"desktopwidgets-{key}"

    def menu_entry(self, key: str) -> Path:
        return self.data_home / "applications" / f"desktopwidgets-{key}.desktop"

    def autostart_entry(self, key: str) -> Path:
        return self.config_home / "autostart" / f"desktopwidgets-{key}.desktop"

    def widget_data(self, key: str) -> Path:
        return self.config_home / "DesktopWidgets" / APP_FOR_KEY[key]

    # ---- running the scripts ----------------------------------------------
    def run_script(self, script: Path, *args: str, env=None, cwd=None,
                   check: bool = False, timeout: int = 90):
        result = subprocess.run(
            ["/bin/sh", str(script), *args],
            env=dict(self.env if env is None else env),
            cwd=str(cwd or self.bundle),
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
        if check:
            self.assertEqual(result.returncode, 0,
                             f"{script.name} failed:\n{result.stdout}\n{result.stderr}")
        return result

    def install(self, *args: str, env=None, cwd=None, check: bool = False):
        return self.run_script(self.bundle / "install.sh", *args,
                               env=env, cwd=cwd, check=check)

    def uninstall(self, *args: str, env=None, cwd=None, check: bool = False):
        return self.run_script(self.bundle / "uninstall.sh", *args,
                               env=env, cwd=cwd, check=check)

    def run_with_answers(self, script: Path, answers: str, *args: str):
        """Run a script on a pseudo-terminal and type the answers to its questions.

        The scripts only ask when stdin is a terminal, so a pipe would not do.
        """
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(
                ["/bin/sh", str(script), *args], env=dict(self.env),
                cwd=str(self.bundle), stdin=slave, stdout=slave, stderr=slave,
                close_fds=True,
            )
        finally:
            os.close(slave)
        os.write(master, answers.encode())
        output = b""
        deadline = time.monotonic() + 90
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    self.fail(f"{script.name} did not finish")
                ready, _, _ = select.select([master], [], [], remaining)
                if not ready:
                    continue
                try:
                    chunk = os.read(master, 4096)
                except OSError:          # the terminal closed: the child is done
                    break
                if not chunk:
                    break
                output += chunk
        finally:
            process.wait(timeout=30)
            os.close(master)
        return process.returncode, output.decode(errors="replace")

    # ---- test material setup helpers --------------------------------------
    def seed_user_data(self) -> None:
        """Write one settings file per widget plus the shared background file."""
        (self.config_home / "DesktopWidgets").mkdir(parents=True, exist_ok=True)
        shared = self.config_home / "DesktopWidgets" / "widget_background.json"
        shared.write_text('{"colour": "#101010"}')
        for key in APP_FOR_KEY:
            folder = self.widget_data(key)
            folder.mkdir(parents=True)
            (folder / "settings.json").write_text('{"key": "%s"}' % key)
        self.assertTrue(shared.is_file())

    # ---- B-01: purge only deletes the widgets' own data --------------------
    def test_b01_purge_keeps_shared_background_and_other_widgets(self):
        self.install("--yes", check=True)
        self.seed_user_data()
        shared = self.config_home / "DesktopWidgets" / "widget_background.json"

        # Nobody is there to answer: --purge-data deletes the widget folders
        # only and keeps the file every widget shares.
        result = self.uninstall("--prefix", str(self.prefix), "--purge-data", check=True)
        for key in APP_FOR_KEY:
            self.assertFalse(self.widget_data(key).exists(), f"{key} data survived the purge")
        self.assertTrue(shared.is_file(), "the shared widget_background.json was deleted")
        self.assertIn("shared card background", result.stdout)

        # With a terminal, the shared file gets its own question and a default
        # of "keep": answering yes to the widget data keeps it.
        self.install("--yes", check=True)
        self.seed_user_data()
        code, output = self.run_with_answers(
            self.bundle / "uninstall.sh", "y\nn\n", "--prefix", str(self.prefix))
        self.assertEqual(code, 0, output)
        self.assertIn("shared card background setting", output)
        self.assertTrue(shared.is_file(), "answering no to the shared question must keep it")
        for key in APP_FOR_KEY:
            self.assertFalse(self.widget_data(key).exists())

        # Answering yes to both deletes it too.
        self.install("--yes", check=True)
        self.seed_user_data()
        code, output = self.run_with_answers(
            self.bundle / "uninstall.sh", "y\ny\n", "--prefix", str(self.prefix))
        self.assertEqual(code, 0, output)
        self.assertFalse(shared.exists(), "answering yes must delete the shared file")

    def test_b01_purge_deletes_only_the_installed_widgets_data(self):
        self.install("--yes", "--components", "usage", check=True)
        self.seed_user_data()
        self.uninstall("--prefix", str(self.prefix), "--purge-data", check=True)
        self.assertFalse(self.widget_data("usage").exists())
        self.assertTrue((self.widget_data("todo") / "settings.json").is_file())
        self.assertTrue((self.widget_data("desktop") / "settings.json").is_file())

    # ---- B-02: re-installing with fewer widgets leaves no stale entries ----
    def test_b02_reinstall_removes_stale_launcher_menu_and_autostart(self):
        self.install("--yes", "--autostart", check=True)
        for key in APP_FOR_KEY:
            self.assertTrue(self.launcher(key).is_file())
            self.assertTrue(self.menu_entry(key).is_file())
            self.assertTrue(self.autostart_entry(key).is_file())

        self.install("--yes", "--components", "usage", check=True)

        self.assertFalse((self.prefix / "TodoWidget").exists())
        self.assertFalse(self.launcher("todo").exists(), "stale launcher left behind")
        self.assertFalse(self.menu_entry("todo").exists(), "stale menu entry left behind")
        self.assertFalse(self.autostart_entry("todo").exists(), "stale autostart entry left behind")
        self.assertFalse(self.launcher("desktop").exists())
        self.assertFalse(self.menu_entry("desktop").exists())
        self.assertTrue(self.launcher("usage").is_file())
        self.assertTrue(self.menu_entry("usage").is_file())
        self.assertTrue(self.autostart_entry("usage").is_file())

    # ---- B-03: a relative XDG folder is ignored ----------------------------
    def test_b03_relative_xdg_folders_are_ignored(self):
        env = dict(self.env, XDG_DATA_HOME="relative/data",
                   XDG_CONFIG_HOME="relative/config")
        cwd = self.tmp / "empty-cwd"
        cwd.mkdir()

        self.install("--yes", "--autostart", env=env, cwd=cwd, check=True)

        self.assertFalse((cwd / "relative").exists(),
                         "a relative XDG folder must be ignored, not created")
        self.assertTrue((self.home / ".local" / "share" / "DesktopWidgets" / "UsageMonitor").is_file())
        self.assertTrue((self.home / ".local" / "bin" / "desktopwidgets-todo").is_file())
        self.assertTrue((self.home / ".local" / "share" / "applications"
                         / "desktopwidgets-todo.desktop").is_file())
        self.assertTrue((self.home / ".config" / "autostart"
                         / "desktopwidgets-todo.desktop").is_file())

        # The uninstaller reads the same XDG variables, so it must find the same files.
        self.uninstall(env=env, cwd=cwd, check=True)
        self.assertFalse((self.home / ".local" / "share" / "applications"
                          / "desktopwidgets-todo.desktop").exists())
        self.assertFalse((self.home / ".config" / "autostart"
                          / "desktopwidgets-todo.desktop").exists())
        self.assertFalse((self.home / ".local" / "bin" / "desktopwidgets-todo").exists())

    # ---- B-04: never remove another install's entries ----------------------
    def test_b04_uninstall_keeps_the_entries_of_another_install(self):
        first = self.tmp / "install one"
        second = self.tmp / "install two"
        self.install("--yes", "--components", "usage", "--prefix", str(first),
                     "--autostart", check=True)
        self.assertIn(str(first), self.autostart_entry("usage").read_text())
        self.install("--yes", "--components", "usage", "--prefix", str(second),
                     "--autostart", check=True)
        self.assertIn(str(second), self.autostart_entry("usage").read_text())

        self.uninstall("--prefix", str(first), "--keep-data", check=True)

        self.assertFalse(first.exists())
        self.assertTrue((second / "UsageMonitor").is_file())
        self.assertTrue(self.menu_entry("usage").is_file(),
                        "the menu entry belongs to the second install")
        self.assertTrue(self.launcher("usage").is_file(),
                        "the launcher belongs to the second install")
        self.assertTrue(self.autostart_entry("usage").is_file(),
                        "the autostart entry belongs to the second install")

        # And the second install's own uninstaller still cleans up everything.
        self.uninstall("--prefix", str(second), "--keep-data", check=True)
        self.assertFalse(second.exists())
        self.assertFalse(self.menu_entry("usage").exists())
        self.assertFalse(self.launcher("usage").exists())
        self.assertFalse(self.autostart_entry("usage").exists())

    # ---- B-05: stop a running widget before deleting it --------------------
    def test_b05_uninstall_stops_the_running_widget_only(self):
        self.install("--yes", "--components", "usage", check=True)
        lock = self.runtime_dir / "desktopwidgets-usage.lock"

        widget = subprocess.Popen(
            [str(self.prefix / "UsageMonitor")],
            env=dict(self.env, FAKE_WIDGET_LOCK=str(lock)),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.reap, widget)

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not lock.exists():
            time.sleep(0.05)
        self.assertTrue(lock.exists(), "the fake widget never made its lock file")

        # A second process that has the same lock file open but does not run
        # our binary must be left alone.
        decoy = subprocess.Popen(
            [sys.executable, "-c",
             "import os, time\n"
             "handle = open(os.environ['DECOY_LOCK'], 'r')\n"
             "time.sleep(600)\n"],
            env=dict(self.env, DECOY_LOCK=str(lock)),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.reap, decoy)
        time.sleep(0.5)                       # let the decoy open the file too

        self.uninstall("--prefix", str(self.prefix), "--keep-data", check=True)

        widget.poll()
        self.assertIsNotNone(widget.returncode,
                             "uninstall.sh left the running widget alive")
        self.assertIsNone(decoy.poll(), "uninstall.sh killed a process that is not ours")
        self.assertFalse((self.prefix / "UsageMonitor").exists())

    @staticmethod
    def reap(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    # ---- B-06: a failed copy still leaves a folder we can remove ----------
    def test_b06_failed_copy_leaves_a_cleanable_prefix(self):
        broken = self.tmp / "broken-bundle"
        self.make_bundle(broken, icons=False)
        result = self.run_script(broken / "install.sh", "--yes")
        self.assertNotEqual(result.returncode, 0, "the missing icons should fail the install")
        self.assertTrue((self.prefix / "install-manifest.txt").is_file(),
                        "a failed copy must leave the manifest behind")

        self.uninstall("--prefix", str(self.prefix), "--keep-data", check=True)
        self.assertFalse(self.prefix.exists(), "the uninstaller could not clean up")

    # ---- B-07: Exec= is quoted, and a strange HOME is refused -------------
    def test_b07_exec_is_quoted_for_a_path_with_a_space(self):
        home = self.tmp / "my home"
        prefix = self.tmp / "install dir"
        env = dict(self.env, HOME=str(home))
        self.install("--yes", "--prefix", str(prefix), "--autostart",
                     env=env, check=True)

        for entry in (self.menu_entry("usage"), self.autostart_entry("usage")):
            exec_line = [line for line in entry.read_text().splitlines()
                         if line.startswith("Exec=")][0]
            self.assertTrue(exec_line.startswith('Exec="'), f"unquoted Exec line: {exec_line}")

        launcher = home / ".local" / "bin" / "desktopwidgets-usage"
        self.assertIn(f'exec "{prefix}/UsageMonitor" "$@"', launcher.read_text())

        if shutil.which("desktop-file-validate"):
            for entry in (self.menu_entry("usage"), self.autostart_entry("usage")):
                check = subprocess.run(["desktop-file-validate", str(entry)],
                                       capture_output=True, text=True)
                self.assertEqual(check.returncode, 0,
                                 f"desktop-file-validate: {check.stdout}{check.stderr}")

    def test_b07_a_home_folder_with_special_characters_is_refused(self):
        base = dict(self.env)
        for bad in ('my"home', "my%fhome", "my\\home", "my$home", "my`home"):
            env = dict(base, HOME=str(self.tmp / bad))
            result = self.install("--yes", env=env)
            self.assertNotEqual(result.returncode, 0, f"HOME={bad!r} should be refused")
            self.assertIn("home folder", result.stderr)
            self.assertNotIn("parameter not set", result.stderr)

    # ---- B-08: "--components ''" installs nothing -------------------------
    def test_b08_an_empty_components_value_installs_nothing(self):
        result = self.install("--yes", "--components", "")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing chosen", result.stderr)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.launcher("todo").exists())
        self.assertFalse(self.menu_entry("todo").exists())

    # ---- B-09: the autostart file matches what the apps write -------------
    def test_b09_autostart_matches_the_app_format(self):
        self.install("--yes", "--autostart", check=True)
        prefix = str(self.prefix)
        # The three apps each write their own file; these are their frozen
        # formats (Name, Comment, Exec). The Usage Monitor leaves a plain path
        # unquoted, the other two always quote it.
        expected = {
            "todo": ("To-Do Widget", "Desktop to-do list and goals widget",
                     f'"{prefix}/TodoWidget"'),
            "usage": ("Usage Monitor", "AI usage and credits desktop widget",
                      f"{prefix}/UsageMonitor"),
            "desktop": ("Desktop Monitor", "Desktop Monitor card (CPU, GPU, RAM, disks)",
                        f'"{prefix}/DesktopMonitor"'),
        }
        for key, (name, comment, exec_value) in expected.items():
            want = ("[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={name}\n"
                    f"Comment={comment}\n"
                    f"Exec={exec_value}\n"
                    "Terminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n")
            self.assertEqual(self.autostart_entry(key).read_text(), want,
                             f"{key}: install.sh and the app disagree about the file")

        # The menu entries stay in the richer format; the apps never write them.
        menu = self.menu_entry("usage").read_text()
        self.assertIn("Name=Desktop Widgets: Usage Monitor", menu)
        self.assertIn("Icon=", menu)
        self.assertIn("Categories=Utility;", menu)

    # ---- B-10: the manifest names the folder and drives removal ----------
    def test_b10_manifest_lists_the_install_folder_and_is_used(self):
        self.install("--yes", "--components", "usage", check=True)
        manifest = self.prefix / "install-manifest.txt"
        text = manifest.read_text()
        self.assertIn(f"folder={self.prefix}", text)
        self.assertIn("components=usage", text)
        self.assertIn(f"file={self.launcher('usage')}", text)

        # A file that only the manifest names is removed as well.
        extra = self.home / ".local" / "bin" / "desktopwidgets-usage-extra"
        extra.write_text(f'#!/bin/sh\nexec "{self.prefix}/UsageMonitor" "$@"\n')
        extra.chmod(0o755)
        with manifest.open("a") as stream:
            stream.write(f"file={extra}\n")

        # A copy whose manifest names another folder is refused, not removed.
        copy = self.tmp / "copied-install"
        shutil.copytree(self.prefix, copy)
        result = self.uninstall("--prefix", str(copy), "--keep-data")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest says", result.stderr)
        self.assertTrue(copy.exists())

        self.uninstall("--prefix", str(self.prefix), "--keep-data", check=True)
        self.assertFalse(extra.exists(), "the manifest's file= line was not used")
        self.assertFalse(self.prefix.exists())

    # ---- B-11: a missing HOME gets a plain message ------------------------
    def test_b11_missing_or_empty_home_says_what_to_do(self):
        for home in (None, ""):
            env = dict(self.env)
            if home is None:
                env.pop("HOME")
            else:
                env["HOME"] = ""
            for script in (self.bundle / "install.sh", self.bundle / "uninstall.sh"):
                result = self.run_script(script, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Set HOME to your home folder first", result.stderr)
                self.assertNotIn("parameter not set", result.stderr)

    # ---- the old behaviour the review confirmed must keep working --------
    def test_scripts_are_valid_posix_dash(self):
        if not shutil.which("dash"):
            self.skipTest("dash is not installed")
        for script in (INSTALL_SH, UNINSTALL_SH):
            check = subprocess.run(["dash", "-n", str(script)],
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0,
                             f"{script.name} is not valid dash: {check.stderr}")

    def test_upgrade_keeps_config_and_keep_data_keeps_user_files(self):
        self.install("--yes", check=True)
        widget_config = self.widget_data("usage")
        widget_config.mkdir(parents=True)
        config = widget_config / "config.json"
        config.write_text('{"opacity": 0.9}')

        self.install("--yes", "--components", "usage", check=True)
        self.assertEqual(config.read_text(), '{"opacity": 0.9}')

        self.install("--yes", "--autostart", check=True)
        self.uninstall("--prefix", str(self.prefix), "--keep-data", check=True)
        self.assertFalse(self.prefix.exists())
        self.assertEqual(config.read_text(), '{"opacity": 0.9}')
        self.assertFalse(self.menu_entry("usage").exists())
        self.assertFalse(self.autostart_entry("usage").exists())


if __name__ == "__main__":
    unittest.main()
