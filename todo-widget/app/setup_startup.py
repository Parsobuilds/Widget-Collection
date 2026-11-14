"""
Add the To-Do Widget to Windows startup
=======================================

Writes the "Start with Windows" entry for the current user: a value named
``DesktopWidgets.TodoWidget`` under ``HKCU\Software\Microsoft\Windows\CurrentVersion\Run``.
This is the very same entry the widget's own tray menu toggle ("Start with Windows") and the
installer use, so they always agree.

Usage:
    python setup_startup.py            add the entry
    python setup_startup.py --remove   delete the entry

The entry needs no administrator rights because it lives under HKEY_CURRENT_USER.
"""

from __future__ import annotations

import os
import sys

import paths

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
FROZEN = paths.is_frozen()
HERE = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(HERE, "todo_widget.pyw")


def startup_command() -> str:
    """Command line to store: the installed .exe, or pythonw.exe plus the launcher script."""
    if FROZEN:
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = "pythonw.exe"
    return f'"{pythonw}" "{LAUNCHER}"'


def main(argv: list[str] | None = None) -> int:
    import winreg  # Windows only, so it is imported here rather than at the top

    args = sys.argv[1:] if argv is None else argv
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
        if "--remove" in args:
            try:
                winreg.DeleteValue(key, paths.RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
            print("The widget will no longer start with Windows.")
            return 0
        if not FROZEN and not os.path.exists(LAUNCHER):
            print(f"Launcher not found: {LAUNCHER}")
            return 1
        winreg.SetValueEx(key, paths.RUN_VALUE_NAME, 0, winreg.REG_SZ, startup_command())
    print(f"Widget will start with Windows: {startup_command()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
