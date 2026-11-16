# -*- mode: python ; coding: utf-8 -*-
"""Linux freeze: three GUI binaries share one Qt runtime in one folder.

Mirrors installer/DesktopWidgets.spec but has no CpuTempHelper (psutil reads the
temperatures), no win32 imports and no .ico/version resources.
"""
from pathlib import Path

# SPECPATH is installer/linux, so the project root is two levels up.
ROOT = Path(SPECPATH).parents[1]
BUILD = ROOT / "build"

# keyring finds the Secret Service (gnome-keyring) through these pure-Python modules.
KEYRING_LINUX = ["keyring.backends.SecretService", "keyring.backends.kwallet",
                 "keyring.backends.libsecret", "secretstorage", "jeepney",
                 "jeepney.io.blocking"]


def widget(name, directory, script, hidden, datas):
    source = ROOT / directory
    analysis = Analysis(
        [str(source / script)], pathex=[str(source)],
        binaries=[], datas=datas, hiddenimports=hidden,
        hookspath=[], runtime_hooks=[], excludes=["tkinter", "unittest"],
        noarchive=False,
    )
    executable = EXE(
        PYZ(analysis.pure), analysis.scripts, [], exclude_binaries=True,
        name=name, console=False, debug=False, strip=False, upx=False,
    )
    return analysis, executable


todo_source = ROOT / "todo-widget" / "app"
todo, todo_exe = widget(
    "TodoWidget", "todo-widget/app", "todo_widget.py",
    ["glass_theme", "paths", "todo_manager", "goals_manager", "goals_tab",
     "snap_widgets", "setup_startup"],
    [(str(todo_source / "todo_icon.png"), "."),
     (str(todo_source / "todo" / "To Do List.txt"), "todo")],
)
usage, usage_exe = widget(
    "UsageMonitor", "usage-monitor", "usage_monitor.py",
    ["provider_config", "secrets_store", "settings_window", "cache", "models",
     "providers", "providers.base", "providers.registry", "providers._t3db",
     "providers.claude_code", "providers.codex", "providers.antigravity",
     "providers.nanogpt", "providers.nanogpt_sub", "providers.openrouter", "providers.deepseek",
     "providers.google_ai_studio_t3", "providers.custom_json",
     "providers.manual_budget"] + KEYRING_LINUX,
    [],
)
monitor, monitor_exe = widget(
    "DesktopMonitor", "desktop-monitor", "desktop_monitor.py",
    ["settings_dialog", "disk_alert", "psutil", "pynvml", "PIL.Image",
     "PIL.ImageDraw", "PIL.ImageFilter", "PIL.ImageEnhance"],
    [],
)
# Each EXE keeps its own PYZ. COLLECT deduplicates the shared binaries into one _internal.
gui = COLLECT(
    todo_exe, usage_exe, monitor_exe,
    todo.binaries, todo.datas, usage.binaries, usage.datas,
    monitor.binaries, monitor.datas,
    strip=False, upx=False, name="DesktopWidgets",
)
