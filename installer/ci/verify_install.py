"""Clean-machine proof for the Desktop Widgets installer (Windows only, stdlib only).

Run locally or from CI:
    python installer\\ci\\verify_install.py --installer <setup.exe> --out <folder>

Do NOT run this on a machine you care about: it performs real installs and
writes to the real HKCU Run key and the real %APPDATA%.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Exe name -> (Run value name, data folder name under %APPDATA%\DesktopWidgets)
APPS: dict[str, tuple[str, str]] = {
    "TodoWidget.exe": ("DesktopWidgets.TodoWidget", "TodoWidget"),
    "UsageMonitor.exe": ("DesktopWidgets.UsageMonitor", "UsageMonitor"),
    "DesktopMonitor.exe": ("DesktopWidgets.DesktopMonitor", "DesktopMonitor"),
}
RUN_VALUE_NAMES = [v[0] for v in APPS.values()]
SELFTEST_TIMEOUT = 120
INSTALL_TIMEOUT = 600
UNINSTALL_WAIT = 90


class StepFailed(Exception):
    """Raised when a verification step fails; the message explains why."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the Desktop Widgets installer on a clean machine.")
    parser.add_argument("--installer", required=True, type=Path, help="Path to DesktopWidgets-Setup-*.exe")
    parser.add_argument("--out", required=True, type=Path, help="Folder to copy the selftest PNG cards into")
    return parser.parse_args(argv)


def install_args(installer: Path, target: Path, components: str, tasks: str = "") -> list[str]:
    """Build the silent install command line."""
    args = [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/DIR={target}", f"/COMPONENTS={components}"]
    if tasks:
        args.append(f"/TASKS={tasks}")
    return args


def uninstaller_path(target: Path) -> Path:
    return target / "unins000.exe"


def selftest_png_path(appdata: Path, app_exe: str) -> Path:
    return appdata / "DesktopWidgets" / APPS[app_exe][1] / "selftest_card.png"


def marker_path(appdata: Path) -> Path:
    return appdata / "DesktopWidgets" / "TodoWidget" / "ci_keep_marker.txt"


def present_run_values(read_value: Callable[[str], str | None]) -> list[str]:
    """Return the Run value names that currently exist (read_value returns None if absent)."""
    return [name for name in RUN_VALUE_NAMES if read_value(name) is not None]


def read_run_value(name: str) -> str | None:
    import winreg  # Windows only; imported lazily so the pure logic stays testable anywhere
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except FileNotFoundError:
        return None


def report(ok: bool, text: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {text}", flush=True)


def run(cmd: list[str], timeout: int, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, timeout=timeout, env=env, capture_output=True, text=True)
    except subprocess.TimeoutExpired as exc:
        raise StepFailed(f"timed out after {timeout}s: {Path(cmd[0]).name}") from exc


def do_install(installer: Path, target: Path, components: str, tasks: str = "") -> None:
    result = run(install_args(installer, target, components, tasks), INSTALL_TIMEOUT)
    if result.returncode != 0:
        raise StepFailed(f"installer exit code {result.returncode}\n{result.stdout}{result.stderr}")


def wait_until(condition: Callable[[], bool], seconds: int) -> bool:
    """The uninstaller re-launches itself from a temp copy, so poll rather than trust the exit."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(1)
    return condition()


def program_files_gone(target: Path) -> bool:
    """True when the folder is missing or holds no files at all (empty folders are allowed)."""
    return not target.exists() or not any(p.is_file() for p in target.rglob("*"))


def do_uninstall(target: Path) -> None:
    unins = uninstaller_path(target)
    if not unins.exists():
        raise StepFailed(f"uninstaller missing: {unins}")
    result = run([str(unins), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], INSTALL_TIMEOUT)
    if result.returncode != 0:
        raise StepFailed(f"uninstaller exit code {result.returncode}")
    if not wait_until(lambda: program_files_gone(target), UNINSTALL_WAIT):
        left = sorted(str(p) for p in target.rglob("*") if p.is_file())[:10]
        raise StepFailed(f"program files remain after uninstall: {left}")


def step_install_full(installer: Path, target: Path) -> None:
    do_install(installer, target, "todo,usage,monitor", "startup")
    missing = [exe for exe in APPS if not (target / exe).is_file()]
    if missing:
        raise StepFailed(f"missing after install: {missing}")
    present = present_run_values(read_run_value)
    absent = [n for n in RUN_VALUE_NAMES if n not in present]
    if absent:
        raise StepFailed(f"startup Run values not written: {absent}")


def step_selftests(target: Path, out_dir: Path, work: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for exe in APPS:
        sandbox = work / f"sandbox_{Path(exe).stem}"
        folders = {k: sandbox / k.lower() for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE")}
        for folder in folders.values():
            folder.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", **{k: str(v) for k, v in folders.items()})
        result = run([str(target / exe), "--selftest"], SELFTEST_TIMEOUT, env)
        if result.returncode != 0:
            raise StepFailed(f"{exe} --selftest exit code {result.returncode}\n{result.stdout}{result.stderr}")
        png = selftest_png_path(folders["APPDATA"], exe)
        if not png.is_file():
            raise StepFailed(f"{exe} did not write {png}")
        shutil.copy2(png, out_dir / f"{Path(exe).stem}_selftest_card.png")
        report(True, f"selftest {exe}")


def step_uninstall_keeps_data(target: Path) -> None:
    marker = marker_path(Path(os.environ["APPDATA"]))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ci marker: silent uninstall must keep user data\n", encoding="utf-8")
    do_uninstall(target)
    leftovers = present_run_values(read_run_value)
    if leftovers:
        raise StepFailed(f"Run values remain after uninstall: {leftovers}")
    if not marker.is_file():
        raise StepFailed("user data marker was deleted by a silent uninstall")


def step_partial_install(installer: Path, target: Path) -> None:
    do_install(installer, target, "todo")
    if not (target / "TodoWidget.exe").is_file():
        raise StepFailed("TodoWidget.exe missing after todo-only install")
    unexpected = [exe for exe in APPS if exe != "TodoWidget.exe" and (target / exe).exists()]
    if unexpected:
        raise StepFailed(f"unselected components were installed: {unexpected}")
    do_uninstall(target)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.platform != "win32":
        print("FAIL: this script only runs on Windows")
        return 2
    if not args.installer.is_file():
        print(f"FAIL: installer not found: {args.installer}")
        return 2
    work = Path(tempfile.mkdtemp(prefix="dw_verify_"))
    target = work / "install"
    steps: list[tuple[str, Callable[[], None]]] = [
        ("silent install (todo,usage,monitor + startup)", lambda: step_install_full(args.installer, target)),
        ("run --selftest for all three apps", lambda: step_selftests(target, args.out, work)),
        ("silent uninstall removes program + Run values, keeps user data",
         lambda: step_uninstall_keeps_data(target)),
        ("todo-only reinstall installs only TodoWidget, then uninstall",
         lambda: step_partial_install(args.installer, target)),
    ]
    failed = False
    try:
        for name, func in steps:
            try:
                func()
            except StepFailed as exc:
                report(False, f"{name}: {exc}")
                failed = True
                break
            report(True, name)
    finally:
        # Best-effort cleanup: remove any leftover install and the real-APPDATA marker.
        if uninstaller_path(target).exists():
            try:
                do_uninstall(target)
            except StepFailed:
                pass
        marker = marker_path(Path(os.environ.get("APPDATA", "")))
        if marker.is_file():
            marker.unlink()
        shutil.rmtree(work, ignore_errors=True)
    print("RESULT: FAIL" if failed else "RESULT: PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
