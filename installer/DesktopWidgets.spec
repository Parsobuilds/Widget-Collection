# -*- mode: python ; coding: utf-8 -*-
"""Three GUI executables share Qt; the elevated helper has its own runtime."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
BUILD = ROOT / "build"


def widget(name, directory, script, hidden, datas, icon):
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
        icon=str(icon), version=str(BUILD / f"version_{name}.txt"),
    )
    return analysis, executable


todo_source = ROOT / "todo-widget" / "app"
todo, todo_exe = widget(
    "TodoWidget", "todo-widget/app", "todo_widget.py",
    ["glass_theme", "paths", "todo_manager", "goals_manager", "goals_tab",
     "snap_widgets", "setup_startup"],
    [(str(todo_source / "todo_icon.ico"), "."),
     (str(todo_source / "todo_icon.png"), "."),
     (str(todo_source / "todo" / "To Do List.txt"), "todo")],
    todo_source / "todo_icon.ico",
)
usage, usage_exe = widget(
    "UsageMonitor", "usage-monitor", "usage_monitor.py",
    ["provider_config", "secrets_store", "settings_window", "cache", "models",
     "providers", "providers.base", "providers.registry", "providers._t3db",
     "providers.claude_code", "providers.codex", "providers.antigravity",
     "providers.nanogpt", "providers.nanogpt_sub", "providers.openrouter", "providers.deepseek",
     "providers.google_ai_studio_t3", "providers.custom_json", "providers.manual_budget",
     "keyring.backends.Windows", "win32cred", "win32timezone"],
    [], BUILD / "usage_icon.ico",
)
monitor, monitor_exe = widget(
    "DesktopMonitor", "desktop-monitor", "desktop_monitor.py",
    ["settings_dialog", "disk_alert", "psutil", "pynvml", "wmi", "pythoncom",
     "pywintypes", "win32com", "win32com.client", "PIL.Image", "PIL.ImageDraw",
     "PIL.ImageFilter"], [], BUILD / "monitor_icon.ico",
)
# Each EXE keeps its own PYZ. COLLECT deduplicates shared binary/data destinations.
gui = COLLECT(
    todo_exe, usage_exe, monitor_exe,
    todo.binaries, todo.datas, usage.binaries, usage.datas,
    monitor.binaries, monitor.datas,
    strip=False, upx=False, name="DesktopWidgets",
)

net_data, net_binaries, net_hidden = collect_all("pythonnet")
clr_data, clr_binaries, clr_hidden = collect_all("clr_loader")
helper = Analysis(
    [str(ROOT / "desktop-monitor" / "cpu_temp_helper.py")],
    pathex=[str(ROOT / "desktop-monitor")],
    binaries=net_binaries + clr_binaries,
    datas=net_data + clr_data,
    hiddenimports=net_hidden + clr_hidden + ["clr"] + collect_submodules("cffi"),
    hookspath=[], runtime_hooks=[],
    excludes=["PySide6", "PIL", "tkinter", "unittest"], noarchive=False,
)
helper_exe = EXE(
    PYZ(helper.pure), helper.scripts, [], exclude_binaries=True,
    name="CpuTempHelper", console=False, debug=False, strip=False, upx=False,
    icon=str(BUILD / "monitor_icon.ico"),
    version=str(BUILD / "version_CpuTempHelper.txt"),
)
helper_collection = COLLECT(
    helper_exe, helper.binaries, helper.datas,
    strip=False, upx=False, name="CpuTempHelper",
)
