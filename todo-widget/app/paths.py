"""
Where the app keeps its files
=============================

Two kinds of file matter to this widget:

1. **Bundled files** (program code, icons) that ship inside the installed app. These are
   read-only. When the app is frozen by PyInstaller they unpack into a temporary folder
   named by ``sys._MEIPASS``; while developing, they simply sit next to this script.
2. **User data** (the to-do list, the goals, backups and the window position) that must
   survive upgrades and be writable without administrator rights. These live in
   ``%APPDATA%\\DesktopWidgets\\TodoWidget``.

Keeping the two separate is what lets the app be installed under
``%LOCALAPPDATA%\\Programs`` and still keep the user's data safe.

Every location is worked out when a function is called (not when the module is imported),
so a test can point the ``APPDATA`` environment variable at a temporary folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Folder shared by all the Desktop Widgets apps, and this app's own sub-folder inside it.
SUITE_NAME = "DesktopWidgets"
APP_NAME = "TodoWidget"

# Single-instance key and the HKCU Run value name used for "Start with Windows".
INSTANCE_KEY = "DesktopWidgets.TodoWidget"
RUN_VALUE_NAME = "DesktopWidgets.TodoWidget"

# Linux names (fixed for the whole suite): autostart entry and single-instance lock.
AUTOSTART_FILE_NAME = "desktopwidgets-todo.desktop"
LOCK_FILE_NAME = "desktopwidgets-todo.lock"


def is_frozen() -> bool:
    """True when running from the PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def resource_path(*parts: str) -> str:
    """Builds a path to a file bundled with the program (read-only)."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def _make(folder: str) -> str:
    """Creates a folder, tolerating a missing or read-only %APPDATA%."""
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    return folder


def is_linux() -> bool:
    """True on Linux. Checked at call time so a test can patch ``sys.platform``."""
    return sys.platform.startswith("linux")


def appdata_dir() -> str:
    """The folder that holds the ``DesktopWidgets`` suite folder.

    Windows: the roaming application-data folder (``%APPDATA%``).
    Linux: ``$XDG_CONFIG_HOME`` (default ``~/.config``).
    """
    if is_linux():
        return os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")


def runtime_dir() -> str:
    """Folder for the single-instance lock: ``$XDG_RUNTIME_DIR``, else the data folder."""
    folder = os.environ.get("XDG_RUNTIME_DIR", "")
    if folder and os.path.isdir(folder):
        return folder
    return data_dir()


def lock_file() -> str:
    """Path of the Linux single-instance lock file."""
    return os.path.join(runtime_dir(), LOCK_FILE_NAME)


def autostart_file() -> str:
    """Path of the Linux autostart entry (``$XDG_CONFIG_HOME/autostart/...desktop``)."""
    return os.path.join(appdata_dir(), "autostart", AUTOSTART_FILE_NAME)


def suite_dir() -> str:
    """Returns (and creates) ``%APPDATA%\\DesktopWidgets``, shared by every widget."""
    return _make(os.path.join(appdata_dir(), SUITE_NAME))


def data_dir() -> str:
    """Returns (and creates) this app's folder: ``%APPDATA%\\DesktopWidgets\\TodoWidget``."""
    return _make(os.path.join(suite_dir(), APP_NAME))


def widget_background_file() -> str:
    """Path of the background setting shared by all the widgets (read and written)."""
    return os.path.join(appdata_dir(), SUITE_NAME, "widget_background.json")


def todo_file() -> str:
    """Path of the user's to-do list file (its folder is created on demand)."""
    return os.path.join(_make(os.path.join(data_dir(), "todo")), "To Do List.txt")


def backup_file() -> str:
    """Path of the rolling backup taken before each change to the to-do list."""
    return os.path.join(data_dir(), "To Do List.txt.bak")


def goals_file() -> str:
    """Path of the goals file (its readable ``Goals Log.txt`` copy sits beside it)."""
    return os.path.join(data_dir(), "Goals.json")


def goals_backup_file() -> str:
    """Path of the rolling backup taken before each change to the goals."""
    return os.path.join(data_dir(), "Goals.json.bak")


def config_file() -> str:
    """Path of the saved window position, size and tab."""
    return os.path.join(data_dir(), "widget_config.json")
