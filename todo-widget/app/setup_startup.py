"""
Add the To-Do Widget to Windows startup
=======================================

Writes the "Start with Windows" entry for the current user: a value named
``DesktopWidgets.TodoWidget`` under ``HKCU\Software\Microsoft\Windows\CurrentVersion\Run``.
This is the very same entry the widget's own tray menu toggle ("Start with Windows") and the
installer use, so they always agree.

On Linux the same toggle writes (or deletes) a Freedesktop autostart file,
``$XDG_CONFIG_HOME/autostart/desktopwidgets-todo.desktop``, whose ``Exec=`` line is the
absolute path of the running program. The tray / header menu toggle and this script share
the helper functions below, so they always agree.

Usage:
    python setup_startup.py            add the entry
    python setup_startup.py --remove   delete the entry

The entry needs no administrator rights: it lives under HKEY_CURRENT_USER on Windows and in
the user's own config folder on Linux.
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


# ----------------------------------------------------------------------------------------
# Linux: Freedesktop autostart file
# ----------------------------------------------------------------------------------------
def _quote_exec_arg(arg: str) -> str:
    """Quotes one argument for a .desktop ``Exec=`` line (double quotes, escaped specials)."""
    escaped = arg.replace("\\", "\\\\")
    for special in ('"', "`", "$"):
        escaped = escaped.replace(special, "\\" + special)
    return '"' + escaped.replace("%", "%%") + '"'


def linux_exec_line() -> str:
    """The ``Exec=`` value: the frozen binary, or the Python interpreter plus the script."""
    if paths.is_frozen():
        return _quote_exec_arg(sys.executable)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "todo_widget.py")
    return _quote_exec_arg(sys.executable) + " " + _quote_exec_arg(script)


def linux_desktop_entry() -> str:
    """Text of the autostart ``.desktop`` file."""
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=To-Do Widget",
        "Comment=Desktop to-do list and goals widget",
        "Exec=" + linux_exec_line(),
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
    ]
    return "\n".join(lines) + "\n"


def linux_autostart_enabled() -> bool:
    """True when the autostart file exists."""
    return os.path.isfile(paths.autostart_file())


def linux_set_autostart(enabled: bool) -> bool:
    """Writes or deletes the autostart file. Returns True when the change succeeded."""
    target = paths.autostart_file()
    try:
        if enabled:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(linux_desktop_entry())
        elif os.path.exists(target):
            os.remove(target)
        return True
    except OSError:
        return False


def _main_linux(args: list[str]) -> int:
    """Linux version of ``main``: add or remove the autostart file."""
    if "--remove" in args:
        ok = linux_set_autostart(False)
        print("The widget will no longer start when you sign in." if ok else "Could not remove the autostart file.")
        return 0 if ok else 1
    ok = linux_set_autostart(True)
    print(f"Widget will start when you sign in: {paths.autostart_file()}" if ok else "Could not write the autostart file.")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if paths.is_linux():
        return _main_linux(args)
    import winreg  # Windows only, so it is imported here rather than at the top

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
