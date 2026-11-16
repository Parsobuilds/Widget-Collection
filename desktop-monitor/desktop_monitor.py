"""desktop_monitor.py - a system monitor that lives on the desktop wallpaper.

What it does
------------
A compact, dark, frosted-glass card (CPU / GPU / RAM / disks) pinned *above the
wallpaper and below every normal window*. Locked by default, so it is fully
click-through: every click goes to the desktop and its icons. A system-tray icon
opens Settings / Move / Lock / Opacity / disk I/O / Start-with-Windows / Quit.

On Linux the same card runs as a normal frameless window (no WorkerW trick),
with the gear on the card (or --settings) opening Settings, and a tray menu
when the desktop has one.

Dependencies
------------
    pip install -r requirements.txt        # Windows (adds WMI / pywin32)
    pip install PySide6 psutil nvidia-ml-py Pillow     # Linux

Run
---
    python desktop_monitor.py                  # with a console, for testing
    pythonw desktop_monitor.py                 # hidden, the normal way
    python desktop_monitor.py --selftest       # print readings, save a picture of the card
    python desktop_monitor.py --selftest --simulate-no-gpu   # same, pretending no NVIDIA card

CPU temperature: Windows needs the optional CpuTempHelper (see cpu_temp_helper.py);
Linux reads it from psutil.sensors_temperatures().
Settings and logs live in the DesktopWidgets/DesktopMonitor folder inside %APPDATA%
(Windows) or $XDG_CONFIG_HOME (Linux, default ~/.config).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import csv
import datetime
import gc
import io
import json
import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------- optional imports
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import (QObject, QPoint, QRect, QRectF, QSize,
                               QThread, QTimer, Qt)
    from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath,
                              QPen, QPixmap, QStaticText)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QSystemTrayIcon
except ImportError:  # hard requirement
    sys.stderr.write("PySide6 is required: pip install PySide6\n")
    raise SystemExit(1)

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pynvml
except ImportError:
    pynvml = None

# Debug switch for testing the "no NVIDIA card" look on a PC that has one.
if os.environ.get("DESKTOP_MONITOR_SIMULATE_NO_GPU") or "--simulate-no-gpu" in sys.argv:
    pynvml = None

# Platform flags first: the Windows-only modules below are only imported on Windows.
IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

wmi = None
pythoncom = None
winreg = None
if IS_WINDOWS:
    try:
        import wmi
    except ImportError:
        wmi = None
    try:
        import pythoncom
    except ImportError:
        pythoncom = None
    try:
        import winreg
    except ImportError:
        winreg = None

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    Image = None
    ImageDraw = None
    ImageFilter = None

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
else:
    user32 = None
    dwmapi = None

# ---------------------------------------------------------------- paths
PRODUCT_DIR_NAME = "DesktopWidgets"
APP_DIR_NAME = "DesktopMonitor"
RUN_VALUE_NAME = "DesktopWidgets.DesktopMonitor"       # HKCU Run value
AUTOSTART_FILE_NAME = "desktopwidgets-desktop.desktop"  # Linux autostart entry
LOCK_FILE_NAME = "desktopwidgets-desktop.lock"          # Linux single-instance lock
SINGLE_INSTANCE_NAME = "DesktopWidgets.DesktopMonitor"
HELPER_TASK_NAME = "DesktopWidgets CPU Temperature"    # scheduled task made by CpuTempHelper --install
HELPER_EXE_NAME = "CpuTempHelper.exe"


def home_folder() -> Path:
    """The user's home folder. An empty or missing HOME is treated as unset."""
    home = os.environ.get("HOME")
    if home:
        return Path(home)
    try:
        import pwd                      # Linux / macOS only
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        import tempfile
        return Path(tempfile.gettempdir())


def config_home() -> Path:
    """Linux: $XDG_CONFIG_HOME, or ~/.config when it is unset, empty or relative."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    if xdg and os.path.isabs(xdg):
        return Path(xdg)
    return home_folder() / ".config"


def appdata_root() -> Path:
    """The folder that holds DesktopWidgets/: Roaming AppData on Windows,
    $XDG_CONFIG_HOME on Linux. Never a literal path."""
    if not IS_WINDOWS:
        return config_home()
    return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))


def app_dir() -> Path:
    """Per-user data folder of this app (created on first run)."""
    return appdata_root() / PRODUCT_DIR_NAME / APP_DIR_NAME


def resource_path(*parts: str) -> Path:
    """Path of a bundled read-only file: inside the PyInstaller bundle when frozen,
    next to this script otherwise. Never write to it."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return Path(base, *parts)


APP_DIR = app_dir()
try:
    APP_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "monitor.log"

# ---------------------------------------------------------------- logging
import logging

_log = logging.getLogger("desktop_monitor")


def setup_logging() -> None:
    """Rotating log, about 512 KB, one line per failure. Never crashes the app."""
    try:
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=2,
                                     encoding="utf-8")
        _log.addHandler(handler)
        _log.setLevel(logging.INFO)
    except Exception:
        _log.setLevel(logging.INFO)


def log_line(message: str) -> None:
    """Record one problem. Called from every guarded Win32/sensor call."""
    try:
        _log.info("%s %s", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    except Exception:
        pass


# ---------------------------------------------------------------- thresholds and colours
CPU_LOAD_WARN, CPU_LOAD_CRIT = 60.0, 85.0
CPU_TEMP_WARN, CPU_TEMP_CRIT = 70.0, 85.0
GPU_TEMP_WARN, GPU_TEMP_CRIT = 70.0, 83.0
DISK_WARN, DISK_CRIT = 80.0, 92.0
RAM_WARN, RAM_CRIT = 70.0, 90.0

C_CYAN = QtGui.QColor("#22D3EE")
C_EMERALD = QtGui.QColor("#34D399")
C_AMBER = QtGui.QColor("#F59E0B")
C_RED = QtGui.QColor("#EF4444")
C_LABEL = QtGui.QColor("#8A93A6")
C_VALUE = QtGui.QColor("#E8ECF2")
C_SECONDARY = QtGui.QColor("#A7B0C0")
C_FLAT = QtGui.QColor("#101218")
C_TRACK = QtGui.QColor(255, 255, 255, 18)          # rgba(255,255,255,0.07)
C_BORDER = QtGui.QColor(255, 255, 255, 20)        # rgba(255,255,255,0.08)


def level_color(value, warn, crit, temp=False) -> QtGui.QColor:
    """One scale: normal cyan/emerald, warning amber, critical red."""
    if value is None:
        return C_SECONDARY
    try:
        v = float(value)
    except (TypeError, ValueError):
        return C_SECONDARY
    if v >= crit:
        return C_RED
    if v >= warn:
        return C_AMBER
    return C_EMERALD if temp else C_CYAN


# ---------------------------------------------------------------- formatting
DASH = "\u2014"


def dash() -> str:
    """The em-dash placeholder used for every unavailable sensor."""
    return DASH


def fmt_percent(value, decimals: int = 0) -> str:
    if value is None:
        return DASH
    try:
        return "%.*f%%" % (decimals, float(value))
    except (TypeError, ValueError):
        return DASH


def fmt_gb(value) -> str:
    """Bytes -> GB with one decimal. None -> the dash."""
    if value is None:
        return DASH
    try:
        return "%.1f" % (float(value) / (1024.0 * 1024.0 * 1024.0))
    except (TypeError, ValueError):
        return DASH


def fmt_temp(value, approximate: bool = False) -> str:
    if value is None:
        return DASH
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DASH
    if number < 0:
        return DASH
    return ("~%.0f\u00b0C" if approximate else "%.0f\u00b0C") % number


def fmt_mbps(value) -> str:
    if value is None:
        return DASH
    try:
        return "%.1f MB/s" % float(value)
    except (TypeError, ValueError):
        return DASH


def fmt_num(value, decimals: int = 1) -> str:
    if value is None:
        return DASH
    try:
        return "%.*f" % (decimals, float(value))
    except (TypeError, ValueError):
        return DASH


# ---------------------------------------------------------------- fonts
def _font_name() -> str:
    if IS_WINDOWS:
        return "Segoe UI Variable"
    return ""         # Linux and others: the Qt default sans family


def make_font(pt: int, semibold: bool = False, letterspacing: float = 0.0) -> QtGui.QFont:
    """Segoe UI Variable, falling back to Segoe UI then the Qt default."""
    name = _font_name()
    font = QtGui.QFont(name) if name else QtGui.QFont()
    if not font.families():
        font = QtGui.QFont()
    font.setPointSize(pt)
    if semibold:
        try:
            font.setWeight(QtGui.QFont.Weight.Bold)
        except Exception:
            font.setBold(True)
    if letterspacing:
        try:
            font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, letterspacing)
        except Exception:
            pass
    return font


# ---------------------------------------------------------------- settings
DEFAULT_CONFIG = {
    "x": None,               # logical screen coordinates; None = top-right, 24 px margin
    "y": 24,
    "opacity": 50,          # tint strength: 35 / 50 / 65 / 80
    "show_disk_io": False,
    "locked": True,         # locked = pinned to the wallpaper and click-through
    "autostart": False,
    "drag_on_desktop": True,   # left-drag the pinned card from anywhere on it
    "tab": "live",             # "live" or "log"
    "log_recording": False,    # LOG tab: save a stats row every 30 minutes
    "log_last": 0.0,           # unix time of the last saved row
    "w": None,                 # card width in logical px (None = default)
    "extra_h": 0,              # extra height added by dragging the bottom edge
    # Settings window ------------------------------------------------------
    "feed_label": "FEED",      # text next to the status-feed dot
    "feed_path": "",           # file whose changes switch the dot green ("" = indicator off)
    "feed_minutes": 10,        # green while the file changed within this many minutes
    "hidden_drives": [],       # mount points the user unticked (all fixed drives show by default)
    "disk_alert_enabled": False,   # tray notification when a drive runs low
    "disk_alert_gb": 10.0,     # free-space threshold in GB, per drive
}


def wayland_session() -> bool:
    """True when Qt runs on Wayland, where an app may not move its own window.

    On Wayland the only way to move a window is to ask the compositor to do it
    with QWindow.startSystemMove(); QWidget.move() is silently ignored.
    """
    return QtGui.QGuiApplication.platformName().startswith("wayland")


def move_mode(config: dict) -> bool:
    """True when the card is detached from the desktop layer and draggable."""
    return not bool(config.get("locked", True))


def opacity_fraction(config: dict) -> float:
    raw = config.get("opacity", 50)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.5
    if value > 1.0:
        value /= 100.0
    return min(1.0, max(0.2, value))


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        # utf-8-sig: a config saved by PowerShell 5.1 starts with a BOM.
        with CONFIG_PATH.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return dict(DEFAULT_CONFIG)
        merged = dict(DEFAULT_CONFIG)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
        return merged
    except Exception as exc:
        log_line(f"config load failed: {exc}")
        return dict(DEFAULT_CONFIG)


CPU_TEMP_PATH = APP_DIR / "cpu_temp.json"   # written by CpuTempHelper (elevated, optional)
DEBUG_LOG = bool(os.environ.get("DESKTOP_MONITOR_DEBUG"))


def helper_exe_path() -> Path:
    """Where the installer puts the elevated CPU temperature helper."""
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(program_files) / PRODUCT_DIR_NAME / "CpuTempHelper" / HELPER_EXE_NAME


def helper_installed() -> bool:
    """True when the CPU temperature helper was installed on this PC."""
    try:
        return helper_exe_path().is_file()
    except OSError:
        return False


_LAST_HELPER_START_ATTEMPT = 0.0
_HELPER_NOTE_LOGGED = False


def _ensure_helper_running() -> None:
    """Start the helper's scheduled task (never elevates, never prompts).

    Does nothing when the helper is not installed. The task already runs at
    every sign-in, so this only covers the rare case that it stopped; at most
    once per 60 s.
    """
    global _LAST_HELPER_START_ATTEMPT, _HELPER_NOTE_LOGGED
    if not IS_WINDOWS or not helper_installed():
        return
    now = time.time()
    if now - _LAST_HELPER_START_ATTEMPT < 60.0:
        return
    _LAST_HELPER_START_ATTEMPT = now
    if not _HELPER_NOTE_LOGGED:
        _HELPER_NOTE_LOGGED = True
        log_line("CPU temperature helper has no fresh reading; starting its scheduled task")
    try:
        subprocess.Popen(
            ["schtasks", "/run", "/tn", HELPER_TASK_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
    except Exception:
        pass


def helper_cpu_temp():
    """Latest reading from the CPU temperature helper, or None.

    None (shown as a dash) when the helper is not installed, has not written
    a file yet, or its last reading is stale.
    """
    try:
        with CPU_TEMP_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        # The helper's sensor read can take 5-25 s under full RAM, so allow a longer gap.
        if time.time() - float(data.get("time", 0)) > 45.0:
            _ensure_helper_running()
            return None
        value = data.get("cpu_temp")
        if value is None:
            _ensure_helper_running()
            return None
        return float(value)
    except (OSError, ValueError, TypeError):
        _ensure_helper_running()
        return None


def save_config(config: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as exc:
        log_line(f"config save failed: {exc}")

# ---------------------------------------------------------------- Win32 window plumbing
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x00000002

GWL_STYLE = -16
GWL_EXSTYLE = -20

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

SMTO_NORMAL = 0x0000
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SPI_GETDESKWALLPAPER = 0x0073

_sentinel = object()


def set_process_dpi_awareness() -> None:
    """Per-monitor DPI awareness, before QApplication is created."""
    if not IS_WINDOWS:
        return
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)) == 0:
            log_line("SetProcessDpiAwarenessContext(PER_MONITOR_V2) failed")
    except Exception as exc:
        log_line(f"SetProcessDpiAwarenessContext unavailable: {exc}")


def get_window_long(hwnd: int, index: int) -> int:
    try:
        value = user32.GetWindowLongW(hwnd, index)
        return int(value) & 0xFFFFFFFF
    except Exception as exc:
        log_line(f"GetWindowLongW failed: {exc}")
        return 0


def set_window_long(hwnd: int, index: int, value: int) -> bool:
    try:
        return int(user32.SetWindowLongW(hwnd, index, value & 0xFFFFFFFF)) != 0
    except Exception as exc:
        log_line(f"SetWindowLongW failed: {exc}")
        return False


def modify_style(hwnd: int, add: int, remove: int, extended: bool = False) -> None:
    index = GWL_EXSTYLE if extended else GWL_STYLE
    current = get_window_long(hwnd, index)
    set_window_long(hwnd, index, (current & ~remove) | add)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)


def is_window(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(user32.IsWindow(hwnd))
    except Exception as exc:
        log_line(f"IsWindow failed: {exc}")
        return False


GA_PARENT = 1
GA_ROOT = 2


def get_parent(hwnd: int) -> int:
    """Direct parent of a window.

    GetParent() returns 0 for a Qt Tool window even after SetParent succeeds,
    so GetAncestor(GA_PARENT) is the reliable probe here. GetParent is kept as
    a fallback.
    """
    if not IS_WINDOWS or not hwnd:
        return 0
    try:
        parent = int(user32.GetAncestor(hwnd, GA_PARENT) or 0)
        if not parent:
            parent = int(user32.GetParent(hwnd) or 0)
        return parent
    except Exception as exc:
        log_line(f"GetParent failed: {exc}")
        return 0


def find_workerw() -> int:
    """Return the WorkerW hwnd that owns the desktop layer, or Progman as a fallback.

    Two layouts exist. On 24H2 and newer the WorkerW is a *child* of Progman.
    On older builds Progman holds SHELLDLL_DefView and the WorkerW is the next
    top-level window after it. New layout first, then old, then Progman.
    """
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        log_line("Progman window not found")
        return 0

    result = ctypes.wintypes.DWORD(0)
    ok = user32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, SMTO_NORMAL, 1000,
                                   ctypes.byref(result))
    if not ok:
        log_line("SendMessageTimeout(0x052C) to Progman timed out")

    # New layout (24H2+, this machine): WorkerW is a child of Progman.
    child = user32.FindWindowExW(progman, None, "WorkerW", None)
    if child:
        return int(child)

    # Old layout: find the top-level window owning SHELLDLL_DefView, then the
    # next top-level WorkerW after it.
    found = [None]

    def _enum(hwnd, _data):
        defview = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if defview:
            sibling = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if sibling:
                found[0] = int(sibling)
        return True

    try:
        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                                     ctypes.wintypes.LPARAM)(_enum)
        user32.EnumWindows(callback, 0)
    except Exception as exc:
        log_line(f"EnumWindows failed: {exc}")

    if found[0]:
        return int(found[0])

    log_line("no WorkerW found, falling back to Progman")
    return int(progman)


def pin_to_desktop(hwnd: int) -> bool:
    """Attach the widget to the desktop layer. Safe to call repeatedly."""
    if not IS_WINDOWS or not is_window(hwnd):
        return False
    target = find_workerw()
    if not target:
        log_line("pin_to_desktop: no desktop window available")
        return False
    try:
        if not user32.SetParent(hwnd, target):
            log_line("SetParent to WorkerW failed")
            return False
    except Exception as exc:
        log_line(f"SetParent raised: {exc}")
        return False

    modify_style(hwnd, WS_CHILD, WS_POPUP | WS_CAPTION, extended=False)
    modify_style(hwnd, WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, 0, extended=True)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                        | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    log_line(f"pinned to desktop layer (parent hwnd {target})")
    return True


def unpin_from_desktop(hwnd: int) -> bool:
    """Return the widget to a normal top-level popup so it can be moved."""
    if not IS_WINDOWS or not is_window(hwnd):
        return False
    try:
        user32.SetParent(hwnd, None)
    except Exception as exc:
        log_line(f"SetParent(None) raised: {exc}")
        return False
    modify_style(hwnd, WS_POPUP, WS_CHILD, extended=False)
    modify_style(hwnd, 0, WS_EX_TRANSPARENT, extended=True)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED)
    return True


def virtual_screen_origin() -> tuple:
    """Physical top-left of the virtual screen (negative on multi-monitor setups)."""
    if not IS_WINDOWS:
        return 0, 0
    try:
        return int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)), int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    except Exception as exc:
        log_line(f"GetSystemMetrics failed: {exc}")
        return 0, 0


def child_rect(hwnd: int):
    """(x, y, w, h) of a child window in its parent's client pixels, or None."""
    if not IS_WINDOWS or not is_window(hwnd):
        return None
    try:
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        parent = get_parent(hwnd)  # GetParent() returns 0 for this Qt Tool window
        origin = ctypes.wintypes.POINT(0, 0)
        if parent and not user32.ClientToScreen(parent, ctypes.byref(origin)):
            return None
        return (rect.left - origin.x, rect.top - origin.y,
                rect.right - rect.left, rect.bottom - rect.top)
    except Exception as exc:
        log_line("child_rect failed: %s" % exc)
        return None


def set_window_position_physical(hwnd: int, px: int, py: int, pw: int, ph: int) -> bool:
    """Move a (child) window using physical pixels relative to its parent."""
    if not IS_WINDOWS or not is_window(hwnd):
        return False
    try:
        return bool(user32.SetWindowPos(hwnd, 0, int(px), int(py), int(pw), int(ph),
                                       SWP_NOZORDER | SWP_NOACTIVATE))
    except Exception as exc:
        log_line(f"SetWindowPos failed: {exc}")
        return False
# ---------------------------------------------------------------- metrics collection
import math
import platform
import socket
import threading


def shutdown_nvml() -> None:
    """Release NVML. Safe to call when NVML was never initialised."""
    if pynvml is None:
        return
    try:
        pynvml.nvmlShutdown()
    except Exception as exc:
        log_line("nvmlShutdown failed: %s" % exc)


def feed_state(path, minutes, now=None):
    """Status feed indicator: None = not configured, True = file changed within
    `minutes`, False = missing or older than that."""
    path = str(path or "").strip()
    if not path:
        return None
    try:
        minutes = max(1.0, float(minutes))
    except (TypeError, ValueError):
        minutes = 10.0
    try:
        age = (time.time() if now is None else now) - os.path.getmtime(path)
    except OSError:
        return False
    return age <= minutes * 60.0


# Video adapters that are not a real graphics card (remote desktop, mirror drivers).
IGNORED_ADAPTERS = ("remote display", "mirror driver", "virtual display", "virtual monitor")


def pick_gpu_name(names, prefer=None):
    """Choose the adapter name to show from Win32_VideoController names."""
    clean = [str(n).strip() for n in names if n and str(n).strip()]
    real = [n for n in clean if not any(bad in n.lower() for bad in IGNORED_ADAPTERS)]
    if prefer:
        for name in real:
            if prefer.lower() in name.lower():
                return name
    return real[0] if real else None


def parse_gpu_engine_load(instances):
    """Overall GPU load (0-100) from Windows "GPU Engine" counter rows.

    `instances` is a list of (instance name, utilisation %). Names look like
    pid_123_luid_0x0_0xABCD_phys_0_eng_0_engtype_3D. Rows are summed per
    adapter and engine type; the busiest group is the load. Works for any
    graphics vendor. Returns None when there is nothing usable.
    """
    groups = {}
    for name, value in instances:
        text = str(name)
        if "engtype_" not in text:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        luid = text.split("_luid_")[1].split("_phys_")[0] if "_luid_" in text else ""
        engine = text.split("engtype_", 1)[1]
        groups[(luid, engine)] = groups.get((luid, engine), 0.0) + number
    if not groups:
        return None
    return max(0.0, min(100.0, max(groups.values())))


def normalise_mount(mount) -> str:
    """Comparable form of a mount point.

    Windows: "c:\\" -> "C:". Linux paths are case sensitive and "/" must stay "/".
    """
    text = str(mount or "")
    if text.startswith("/"):
        return text.rstrip("/") or "/"
    return text.upper().rstrip("\\/")


# File systems that are not real storage (Linux). Anything starting with "fuse."
# is also skipped, except a few that mount real drives (see linux_partitions).
PSEUDO_FSTYPES = {
    "squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2",
    "autofs", "efivarfs", "bpf", "tracefs", "debugfs", "securityfs", "pstore",
    "configfs", "fusectl", "mqueue", "hugetlbfs", "binfmt_misc", "ramfs", "nsfs",
    "rpc_pipefs", "devpts", "iso9660", "udf", "erofs", "aufs", "fuse", "squashfuse",
}
REAL_FUSE_FSTYPES = {"fuse.sshfs", "fuse.ntfs-3g", "fuse.exfat", "fuse.mergerfs"}
# The EFI boot partition is real but is not somewhere a person keeps files.
PSEUDO_MOUNT_PREFIXES = ("/proc", "/sys", "/dev", "/snap", "/var/snap", "/var/lib/snapd",
                         "/var/lib/docker", "/var/lib/containers", "/boot/efi",
                         "/boot/firmware")


def linux_partition_ok(part) -> bool:
    """True for a partition that is real storage a person would want on the card."""
    fstype = (getattr(part, "fstype", "") or "").lower()
    device = getattr(part, "device", "") or ""
    mount = getattr(part, "mountpoint", "") or ""
    if not fstype or not mount:
        return False
    if fstype in PSEUDO_FSTYPES:
        return False
    if fstype.startswith("fuse.") and fstype not in REAL_FUSE_FSTYPES:
        return False
    if device.startswith("/dev/loop"):
        return False
    for prefix in PSEUDO_MOUNT_PREFIXES:
        if mount == prefix or mount.startswith(prefix + "/"):
            return False
    # /run is runtime state, but removable drives are mounted under /run/media.
    if (mount == "/run" or mount.startswith("/run/")) and not mount.startswith("/run/media/"):
        return False
    return True


def linux_partitions(parts):
    """Filter psutil partitions for Linux: no pseudo file systems, one row per device.

    Bind mounts and btrfs subvolumes list the same device several times; the
    shortest mount point wins ("/" before "/var/lib/something").
    """
    best = {}
    order = []
    for part in parts:
        if not linux_partition_ok(part):
            continue
        key = part.device or part.mountpoint
        known = best.get(key)
        if known is None:
            order.append(key)
            best[key] = part
        elif len(part.mountpoint) < len(known.mountpoint):
            best[key] = part
    return [best[key] for key in order]


def fixed_partitions():
    """Fixed (non-removable, non-optical) drives with a file system."""
    if psutil is None:
        return []
    if not IS_WINDOWS:
        return linux_partitions(psutil.disk_partitions(all=False))
    keep = []
    for part in psutil.disk_partitions(all=False):
        opts = (part.opts or "").lower()
        fstype = (part.fstype or "").lower()
        if "fixed" not in opts:
            continue
        if "cdrom" in opts or "removable" in opts or not fstype:
            continue
        keep.append(part)
    return keep


CPU_SENSOR_ORDER = ("coretemp", "k10temp", "zenpower", "cpu_thermal")
CPU_PREFERRED_LABELS = ("package", "tctl", "tdie", "cpu")


def linux_cpu_name():
    """Marketing name of the CPU from /proc/cpuinfo, or None."""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return " ".join(line.split(":", 1)[1].split()) or None
    except OSError:
        pass
    return None


def linux_cpu_temp(sensors=None):
    """CPU temperature in degrees C from psutil's hwmon data, or None.

    Tries coretemp (Intel), k10temp and zenpower (AMD), cpu_thermal (ARM) in that
    order. Within a chip the package/Tctl reading wins, else the hottest core.
    Never raises: no sensors at all simply gives None (a dash on the card).
    """
    if sensors is None:
        if psutil is None or not hasattr(psutil, "sensors_temperatures"):
            return None
        try:
            sensors = psutil.sensors_temperatures()
        except Exception as exc:
            log_line("sensors_temperatures failed: %s" % exc)
            return None
    for chip in CPU_SENSOR_ORDER:
        entries = [e for e in (sensors.get(chip) or [])
                   if getattr(e, "current", None) is not None]
        if not entries:
            continue
        for entry in entries:
            label = (getattr(entry, "label", "") or "").lower()
            if label.startswith(CPU_PREFERRED_LABELS):
                return float(entry.current)
        return max(float(e.current) for e in entries)
    return None


DRM_VENDORS = {"0x1002": "AMD graphics", "0x8086": "Intel graphics", "0x10de": "NVIDIA graphics"}


def _read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None


def _read_number(path: Path):
    text = _read_text(path)
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def read_drm_gpu(root=None):
    """Basic GPU facts from /sys/class/drm for cards without NVML (AMD, Intel...).

    Returns {"name", "load", "temp", "vram_used", "vram_total"} (None for what the
    driver does not expose; VRAM in GiB) or None when there is no graphics card.
    `root` is only given by the tests.
    """
    base = Path(root) if root is not None else Path("/sys/class/drm")
    try:
        cards = sorted(p for p in base.iterdir()
                       if p.name.startswith("card") and p.name[4:].isdigit())
    except OSError:
        return None
    gib = 1024.0 * 1024.0 * 1024.0
    for card in cards:
        device = card / "device"
        vendor = _read_text(device / "vendor")
        if vendor is None:
            continue
        info = {"name": DRM_VENDORS.get(vendor.lower(), "Graphics adapter"),
                "load": _read_number(device / "gpu_busy_percent"),
                "temp": None, "vram_used": None, "vram_total": None}
        used = _read_number(device / "mem_info_vram_used")
        total = _read_number(device / "mem_info_vram_total")
        if used is not None and total:
            info["vram_used"], info["vram_total"] = used / gib, total / gib
        try:
            for hwmon in sorted((device / "hwmon").iterdir()):
                milli = _read_number(hwmon / "temp1_input")
                if milli is not None:
                    info["temp"] = milli / 1000.0
                    break
        except OSError:
            pass
        return info
    return None


def detect_fixed_drives():
    """Mount points of the fixed drives (for the Settings window)."""
    try:
        return [part.mountpoint for part in fixed_partitions()]
    except Exception as exc:
        log_line("drive detection failed: %s" % exc)
        return []


class MetricsWorker(QtCore.QObject):
    """Polls every source on its own thread and emits one dict per tick."""

    snapshot = QtCore.Signal(dict)
    stop_requested = QtCore.Signal()

    def __init__(self, interval_ms: int = 2000):
        super().__init__()
        self.interval_ms = interval_ms
        self._timer = None
        self._com_initialized = False
        self._fail_counts = {}
        self._retry_after = {}
        self._lock = threading.Lock()

        self._cpu_name = None
        self._gpu_handle = None
        self._gpu_name = None
        self._nvml_ready = False
        self._partitions = []
        self._disk_map = {}
        self._io_last = {}
        self._wmi = None
        self._wmi_ns = {}
        self._lhm_namespace = None
        self._acpi_unsupported = False
        self._partitions_time = 0.0
        # Settings pushed from the main thread by configure().
        self.hidden_drives = set()
        self.feed_path = ""
        self.feed_minutes = 10.0

    def configure(self, config: dict) -> None:
        """Take the settings the worker needs (called from the main thread)."""
        self.hidden_drives = {normalise_mount(m) for m in (config.get("hidden_drives") or [])}
        self.feed_path = str(config.get("feed_path") or "").strip()
        try:
            self.feed_minutes = max(1.0, float(config.get("feed_minutes") or 10))
        except (TypeError, ValueError):
            self.feed_minutes = 10.0

    # -- guarded source: never let one failure block a tick -------------------
    def _call_source(self, name, fn, default=None):
        now = time.monotonic()
        if self._retry_after.get(name, 0.0) > now:
            return default
        try:
            value = fn()
            self._fail_counts[name] = 0
            return value
        except Exception as exc:
            count = self._fail_counts.get(name, 0) + 1
            self._fail_counts[name] = count
            if count >= 3:
                self._retry_after[name] = now + 30.0
                # Log the 3rd failure, then only every 20th, so a source that is
                # down for hours does not fill the log.
                if count == 3 or count % 20 == 0:
                    log_line("source '%s' failed %dx in a row, retrying in 30 s: %s"
                             % (name, count, exc))
            else:
                log_line("source '%s' failed: %s" % (name, exc))
            return default

    def _wmi_namespace(self, namespace: str):
        """Cached WMI connection for a namespace; None if it is unavailable."""
        cached = self._wmi_ns.get(namespace)
        if isinstance(cached, float):
            # Failed earlier: retry after 30 s, so LibreHardwareMonitor started
            # after the widget is still picked up.
            if time.monotonic() < cached:
                return None
        elif cached is not None:
            return cached
        try:
            connection = wmi.WMI(namespace=namespace)
        except Exception as exc:
            if cached is None:
                log_line("WMI namespace %s unavailable: %s" % (namespace, exc))
            self._wmi_ns[namespace] = time.monotonic() + 30.0
            return None
        self._wmi_ns[namespace] = connection
        return connection

    # -- static data, collected once -------------------------------------------
    def _prime_static(self) -> None:
        if IS_WINDOWS and wmi is not None:
            try:
                self._wmi = wmi.WMI()
                processors = self._wmi.Win32_Processor()
                if processors:
                    self._cpu_name = (processors[0].Name or "").strip()
            except Exception as exc:
                log_line("WMI CPU name unavailable: %s" % exc)
            self._disk_map = self._build_disk_map()
        if not self._cpu_name and IS_LINUX:
            self._cpu_name = linux_cpu_name()
        if not self._cpu_name:
            try:
                self._cpu_name = platform.processor() or None
            except Exception as exc:
                log_line("platform.processor() failed: %s" % exc)
        if not self._cpu_name:
            self._cpu_name = "unknown CPU"

        if pynvml is not None:
            try:
                pynvml.nvmlInit()
                self._nvml_ready = True
                self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                name = pynvml.nvmlDeviceGetName(self._gpu_handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "replace")
                self._gpu_name = name
            except Exception as exc:
                self._nvml_ready = False
                log_line("NVML unavailable: %s" % exc)
        if not self._gpu_name:
            if IS_LINUX:
                # No NVML: look at /sys/class/drm (AMD / Intel / nothing).
                drm = read_drm_gpu()
                self._gpu_name = drm["name"] if drm else None
            else:
                # No NVIDIA driver: ask Windows which graphics adapter this PC has.
                self._gpu_name = self._wmi_gpu_name()

        if psutil is not None:
            try:
                self._partitions = self._fixed_partitions()
            except Exception as exc:
                log_line("disk partitions unavailable: %s" % exc)
                self._partitions = []

    def _wmi_gpu_name(self):
        """Name of the graphics adapter from WMI, or None (no adapter / no WMI)."""
        if not IS_WINDOWS or wmi is None or self._wmi is None:
            return None
        try:
            names = [adapter.Name for adapter in self._wmi.Win32_VideoController()]
        except Exception as exc:
            log_line("WMI video controller query failed: %s" % exc)
            return None
        return pick_gpu_name(names)

    def _fixed_partitions(self):
        return fixed_partitions()

    def _build_disk_map(self) -> dict:
        """Drive letter -> PhysicalDriveN key used by psutil.disk_io_counters."""
        mapping = {}
        if not IS_WINDOWS or wmi is None or self._wmi is None:
            return mapping
        try:
            for drive in self._wmi.Win32_LogicalDisk():
                for partition in drive.associators("Win32_LogicalDiskToPartition"):
                    for disk in partition.associators("Win32_DiskDriveToDiskPartition"):
                        if disk.Index is not None:
                            mapping[drive.DeviceID] = "PhysicalDrive%s" % disk.Index
        except Exception as exc:
            log_line("disk I/O mapping failed: %s" % exc)
        return mapping

    def _io_key_for(self, mountpoint: str, device: str = ""):
        if IS_WINDOWS:
            return self._disk_map.get(mountpoint.upper().rstrip("\\"))
        # Linux: psutil.disk_io_counters(perdisk=True) is keyed by the kernel
        # name of the partition ("nvme0n1p2", "sda1", "dm-0"), which is the
        # device node's real name (LVM names under /dev/mapper are symlinks).
        if not device.startswith("/dev/"):
            return None
        return os.path.basename(os.path.realpath(device))

    # -- CPU temperature chain -------------------------------------------------
    def _lhm_cpu_temp(self):
        if wmi is None:
            return None
        for namespace in (r"root\LibreHardwareMonitor", r"root\OpenHardwareMonitor"):
            connection = self._wmi_namespace(namespace)
            if connection is None:
                continue
            try:
                sensors = connection.Sensor(SensorType="Temperature")
            except Exception as exc:
                log_line("LHM sensors in %s unavailable: %s" % (namespace, exc))
                continue
            cpu_sensors = [s for s in sensors if "cpu" in (s.Parent or "").lower()]
            if not cpu_sensors:
                continue
            for wanted in ("CPU Package", "Core (Tctl/Tdie)", "Core Max"):
                for sensor in cpu_sensors:
                    if (sensor.Name or "").strip() == wanted:
                        self._lhm_namespace = namespace
                        return float(sensor.Value)
            cores = [float(s.Value) for s in cpu_sensors
                     if (s.Name or "").lower().startswith("core")]
            if cores:
                self._lhm_namespace = namespace
                return max(cores)
        return None

    def _acpi_cpu_temp(self):
        if wmi is None:
            return None
        if self._acpi_unsupported:
            return None
        connection = self._wmi_namespace(r"root\wmi")
        if connection is None:
            return None
        try:
            zones = connection.MSAcpi_ThermalZoneTemperature()
        except Exception as exc:
            # 0x8004100C = not supported by this firmware, 0x80041003 = access
            # denied (not admin). Neither changes while the app runs.
            text = str(exc).lower()
            if "8004100c" in text or "80041003" in text:
                self._acpi_unsupported = True
                log_line("ACPI thermal zone not readable here, skipping it: %s" % exc)
                return None
            raise
        if not zones:
            return None
        return zones[0].CurrentTemperature / 10.0 - 273.15

    def cpu_temperature(self):
        """Returns (degrees, is_approximate). None when nothing is readable."""
        if not IS_WINDOWS:
            # Linux: hwmon through psutil. No helper, no admin rights.
            return linux_cpu_temp(), False
        value = helper_cpu_temp()
        if value is not None:
            return value, False
        if wmi is not None:
            value = self._lhm_cpu_temp()
            if value is not None:
                return value, False
            value = self._acpi_cpu_temp()
            if value is not None:
                return value, True
        return None, False

    # -- GPU -----------------------------------------------------------------
    def _nvml_value(self, fn, default=None):
        """One NVML reading; a failing call gives a dash, not a failed GPU section."""
        try:
            return fn()
        except Exception as exc:
            log_line("NVML read failed: %s" % exc)
            return default

    def _nvml_memory(self):
        """VRAM totals. On Linux ask for the v2 structure, whose "used" matches
        nvidia-smi (the v1 figure also counts memory the driver reserves)."""
        if IS_LINUX and hasattr(pynvml, "nvmlMemory_v2"):
            try:
                return pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle, pynvml.nvmlMemory_v2)
            except Exception:
                pass          # older driver or library: use the plain call below
        return pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)

    def _engine_load(self):
        """GPU load for any vendor from the Windows "GPU Engine" counters."""
        connection = self._wmi_namespace("root\\cimv2")
        if connection is None:
            return None
        rows = connection.Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine()
        return parse_gpu_engine_load([(r.Name, r.UtilizationPercentage) for r in rows])

    def gpu_data(self):
        """Readings that exist are filled in, the rest stay None (shown as dashes)."""
        empty = {"load": None, "temp": None, "vram_used": None, "vram_total": None}
        if self._nvml_ready and self._gpu_handle is not None and pynvml is not None:
            gib = 1024.0 * 1024.0 * 1024.0
            data = dict(empty)
            util = self._nvml_value(lambda: pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle))
            if util is not None:
                data["load"] = float(util.gpu)
            temp = self._nvml_value(lambda: pynvml.nvmlDeviceGetTemperature(
                self._gpu_handle, pynvml.NVML_TEMPERATURE_GPU))
            if temp is not None:
                data["temp"] = float(temp)
            memory = self._nvml_value(self._nvml_memory)
            if memory is not None:
                data["vram_used"] = memory.used / gib
                data["vram_total"] = memory.total / gib
            return data
        if IS_LINUX:
            drm = read_drm_gpu()
            if drm is not None:
                return {k: drm[k] for k in empty}
            return empty
        if IS_WINDOWS and wmi is not None:
            for namespace in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
                connection = self._wmi_namespace(namespace)
                if connection is None:
                    continue
                try:
                    sensors = connection.Sensor()
                except Exception:
                    continue
                gpu_sensors = [s for s in sensors if "gpu" in (s.Parent or "").lower()]
                if not gpu_sensors:
                    continue
                load = temp = None
                for sensor in gpu_sensors:
                    if sensor.SensorType == "Load" and (sensor.Name or "") == "GPU Core":
                        load = float(sensor.Value)
                    elif sensor.SensorType == "Temperature" and (sensor.Name or "") == "GPU Core":
                        temp = float(sensor.Value)
                if load is not None or temp is not None:
                    return {"load": load, "temp": temp, "vram_used": None, "vram_total": None}
            # Any vendor: load from the Windows GPU Engine counters; no temperature.
            load = self._call_source("gpu_engine", self._engine_load)
            if load is not None:
                return dict(empty, load=load)
        return empty

    # -- disks ---------------------------------------------------------------
    def disk_rows(self):
        rows = []
        if psutil is None:
            return rows
        # Re-detect the drives now and then: USB disks come and go.
        if time.monotonic() - self._partitions_time > 30.0:
            self._partitions_time = time.monotonic()
            try:
                self._partitions = self._fixed_partitions()
            except Exception as exc:
                log_line("disk partitions unavailable: %s" % exc)
        counters = None
        try:
            counters = psutil.disk_io_counters(perdisk=True)
        except Exception as exc:
            log_line("disk io counters unavailable: %s" % exc)
        now = time.monotonic()
        gib = 1024.0 * 1024.0 * 1024.0
        for part in self._partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue          # drive removed since the last scan
            row = {
                "mount": part.mountpoint,
                "total_gb": usage.total / gib,
                "free_gb": usage.free / gib,
                "used_pct": float(usage.percent),
                "read_mbps": None,
                "write_mbps": None,
            }
            key = self._io_key_for(part.mountpoint, getattr(part, "device", ""))
            if counters and key and key in counters:
                entry = counters[key]
                previous = self._io_last.get(key)
                if previous:
                    elapsed = now - previous[2]
                    if elapsed > 0.5:
                        row["read_mbps"] = max(0.0, (entry.read_bytes - previous[0]) / elapsed / 1e6)
                        row["write_mbps"] = max(0.0, (entry.write_bytes - previous[1]) / elapsed / 1e6)
                self._io_last[key] = (entry.read_bytes, entry.write_bytes, now)
            rows.append(row)

        if not rows and counters:
            total_read = sum(c.read_bytes for c in counters.values())
            total_write = sum(c.write_bytes for c in counters.values())
            read = write = None
            previous = self._io_last.get("all")
            if previous:
                elapsed = now - previous[2]
                if elapsed > 0.5:
                    read = max(0.0, (total_read - previous[0]) / elapsed / 1e6)
                    write = max(0.0, (total_write - previous[1]) / elapsed / 1e6)
            self._io_last["all"] = (total_read, total_write, now)
            rows.append({"mount": "All disks", "total_gb": None, "free_gb": None,
                        "used_pct": None, "read_mbps": read, "write_mbps": write})
        return rows

    # -- one tick --------------------------------------------------------------
    def poll(self) -> dict:
        cpu_load = None
        if psutil is not None:
            try:
                cpu_load = float(psutil.cpu_percent(None))
            except Exception as exc:
                log_line("cpu_percent failed: %s" % exc)

        # No helper installed -> None -> a dash on the card. No hint text.
        cpu_temp, cpu_approx = self._call_source("cpu_temp", self.cpu_temperature,
                                                default=(None, False))

        gpu = self._call_source("gpu", self.gpu_data,
                                default={"load": None, "temp": None,
                                       "vram_used": None, "vram_total": None})
        gpu_present = bool(self._gpu_name) or any(
            gpu.get(k) is not None for k in ("load", "temp", "vram_total"))

        ram = {"used_gb": None, "total_gb": None, "pct": None}
        if psutil is not None:
            try:
                memory = psutil.virtual_memory()
                used = memory.total - memory.available
                gib = 1024.0 * 1024.0 * 1024.0
                ram = {"used_gb": used / gib, "total_gb": memory.total / gib,
                      "pct": float(memory.percent)}
            except Exception as exc:
                log_line("virtual_memory failed: %s" % exc)

        all_disks = self._call_source("disks", self.disk_rows, default=[])
        disks = [row for row in all_disks
                 if normalise_mount(row.get("mount")) not in self.hidden_drives]
        feed_ok = feed_state(self.feed_path, self.feed_minutes)

        return {
            "time": datetime.datetime.now(),
            "host": platform.node() or socket.gethostname(),
            "cpu_load": cpu_load,
            "cpu_temp": cpu_temp,
            "cpu_temp_approx": cpu_approx,
            "cpu_name": self._cpu_name,
            "gpu_present": gpu_present,
            "gpu_load": gpu.get("load"),
            "gpu_temp": gpu.get("temp"),
            "gpu_name": self._gpu_name,
            "vram_used": gpu.get("vram_used"),
            "vram_total": gpu.get("vram_total"),
            "ram_used": ram["used_gb"],
            "ram_total": ram["total_gb"],
            "ram_pct": ram["pct"],
            "disks": disks,
            "all_drives": [row["mount"] for row in all_disks if row.get("total_gb")],
            "feed_active": feed_ok,
            "hints": [],
        }

    # -- thread lifecycle ------------------------------------------------------
    def start(self) -> None:
        """Runs in the worker thread (thread.started). Timer parented to self."""
        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
                self._com_initialized = True
            except Exception as exc:
                log_line("CoInitialize failed: %s" % exc)
        self._prime_static()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self.poll_and_emit)
        self._timer.start()

    def poll_and_emit(self) -> None:
        try:
            data = self.poll()
        except Exception as exc:
            log_line("tick failed: %s" % exc)
            return
        with self._lock:
            self.snapshot.emit(data)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        # Drop every WMI/COM object before CoUninitialize, or pywin32 prints
        # "Win32 exception occurred releasing IUnknown" when they are freed later.
        self._wmi = None
        self._wmi_ns = {}
        if wmi is not None and getattr(wmi, "obj", None) is not None:
            wmi.obj = None  # the wmi package caches its last connection here
        gc.collect()
        if self._com_initialized and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception as exc:
                log_line("CoUninitialize failed: %s" % exc)
            self._com_initialized = False
# ---------------------------------------------------------------- wallpaper blur
def wallpaper_settings():
    """Return (path, style, tile, mtime) for the current desktop wallpaper."""
    if not IS_WINDOWS:
        return None, None, None, 0
    buffer = ctypes.create_unicode_buffer(260)
    try:
        ok = user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 260, buffer, 0)
    except Exception as exc:
        log_line("SystemParametersInfoW failed: %s" % exc)
        return None, None, None, 0
    if not ok or not buffer.value:
        return None, None, None, 0
    try:
        mtime = os.path.getmtime(buffer.value)
    except OSError:
        mtime = 0
    style = tile = None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
        style = int(winreg.QueryValue(key, "WallpaperStyle"))
        tile = int(winreg.QueryValue(key, "TileWallpaper"))
        winreg.CloseKey(key)
    except FileNotFoundError:
        # The values are absent on this machine; Windows renders the
        # wallpaper in Fill mode by default, so assume that.
        style, tile = 10, 0
    except Exception as exc:
        log_line("reading WallpaperStyle failed: %s" % exc)
    return buffer.value, style, tile, mtime


def scaled_wallpaper(image, width, height, style, tile):
    """Fit the wallpaper to a width x height monitor rect, per Windows rules."""
    if tile:
        canvas = Image.new("RGB", (width, height))
        for x in range(0, width, image.width):
            for y in range(0, height, image.height):
                canvas.paste(image, (x, y))
        return canvas
    if style == 10:  # Fill: cover the screen, crop the overflow
        scale = max(width / image.width, height / image.height)
        scaled = image.resize((max(1, int(image.width * scale)),
                              max(1, int(image.height * scale))), Image.LANCZOS)
        left = (scaled.width - width) // 2
        top = (scaled.height - height) // 2
        return scaled.crop((left, top, left + width, top + height))
    if style == 2:  # Stretch: fit exactly
        return image.resize((max(1, width), max(1, height)), Image.LANCZOS)
    return None


# Shared with the other desktop widgets: one background for all of them.
WIDGET_BG_CONFIG_PATH = appdata_root() / PRODUCT_DIR_NAME / "widget_background.json"
# Older widget versions kept this file here. Read-only fallback, never written.
# Linux never had an older location, so there is no fallback there.
LEGACY_BG_CONFIG_PATH = (appdata_root() / "TomTodoList" / "widget_background.json"
                         if IS_WINDOWS else None)

BG_DEFAULTS = {
    "image_path": "",
    "blur_radius": 6.0,
    "tint_opacity": 0.35,
    "brightness": 0.85,
    "saturation": 0.90,
    "fit_mode": "per_widget",
}


def read_bg_file():
    """The shared background JSON as a dict ({} when missing or unreadable).

    Reads the new file; only when it does not exist, the legacy file.
    """
    for path in (WIDGET_BG_CONFIG_PATH, LEGACY_BG_CONFIG_PATH):
        if path is None:
            continue
        try:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
            return raw if isinstance(raw, dict) else {}
        except Exception as exc:
            log_line("could not read widget background config %s: %s" % (path.name, exc))
            return {}
    return {}


def custom_bg_settings():
    """Return (image_path, img_mtime, cfg_mtime, blur_radius, tint_opacity, brightness, saturation, fit_mode) or None."""
    raw = read_bg_file()
    if not raw:
        return None
    try:
        source = (WIDGET_BG_CONFIG_PATH if WIDGET_BG_CONFIG_PATH.is_file()
                  or LEGACY_BG_CONFIG_PATH is None else LEGACY_BG_CONFIG_PATH)
        cfg_mtime = source.stat().st_mtime
        image_path = str(raw.get("image_path") or "").strip()
        if not image_path or not os.path.isfile(image_path):
            return None
        img_mtime = os.path.getmtime(image_path)
        blur_radius = max(0.0, min(60.0, float(raw.get("blur_radius", 6.0))))
        tint_opacity = max(0.0, min(0.95, float(raw.get("tint_opacity", 0.35))))
        brightness = max(0.1, min(1.5, float(raw.get("brightness", 0.85))))
        saturation = max(0.0, min(1.5, float(raw.get("saturation", 0.90))))
        fit_mode = str(raw.get("fit_mode") or "per_widget").strip().lower()
        return (image_path, img_mtime, cfg_mtime, blur_radius, tint_opacity, brightness, saturation, fit_mode)
    except Exception:
        return None


def save_custom_bg_settings(updates: dict):
    """Merge `updates` into the shared widget_background.json (the legacy file is never touched)."""
    try:
        WIDGET_BG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = dict(BG_DEFAULTS)
        data.update(read_bg_file())
        data.update(updates)
        tmp = WIDGET_BG_CONFIG_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, WIDGET_BG_CONFIG_PATH)
    except Exception as exc:
        log_line("could not save custom widget background config: %s" % exc)


def build_blur_rect(monitor_rect, card_rect, settings, dpr, custom_bg=None):
    """Blurred wallpaper or custom image behind card_rect (physical px, monitor-relative)."""
    path, style, tile, _mtime = settings
    if Image is None:
        return None

    screen_w = max(1, int(monitor_rect[2] * dpr))
    screen_h = max(1, int(monitor_rect[3] * dpr))
    card_w = max(1, int(card_rect[2]))
    card_h = max(1, int(card_rect[3]))
    crop_x = max(0, min(screen_w - 1, int(card_rect[0])))
    crop_y = max(0, min(screen_h - 1, int(card_rect[1])))
    crop_w = max(1, min(screen_w - crop_x, card_w))
    crop_h = max(1, min(screen_h - crop_y, card_h))

    # 1. Prepare outer sharp crop from Windows desktop wallpaper (for seamless rounded corners on WorkerW)
    crop = None
    if path and crop_w >= card_w // 2 and crop_h >= card_h // 2:
        try:
            image = Image.open(path).convert("RGB")
            fitted = scaled_wallpaper(image, screen_w, screen_h, style, tile)
            if fitted is not None:
                crop = fitted.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
        except Exception as exc:
            log_line("could not open wallpaper %s: %s" % (path, exc))

    if crop is None:
        crop = Image.new("RGB", (card_w, card_h), (16, 18, 24))

    inset = int(GLOW * dpr)
    if crop.size[0] < 2 * inset + 4 or crop.size[1] < 2 * inset + 4:
        return None   # card mostly off this monitor: plain dark fill instead

    # 2. Prepare inner card body image (either custom widget background or frosted desktop wallpaper)
    blurred = None
    if custom_bg is not None:
        c_path, _c_imtime, _c_cmtime, c_blur, _c_tint, c_bright, c_sat, c_fit = custom_bg
        try:
            from PIL import ImageEnhance
            c_img = Image.open(c_path).convert("RGB")
            if c_fit == "desktop":
                c_fitted = scaled_wallpaper(c_img, screen_w, screen_h, 10, 0)
                c_crop = c_fitted.crop((crop_x, crop_y, crop_x + crop.size[0], crop_y + crop.size[1])) if c_fitted else None
            else:
                c_crop = scaled_wallpaper(c_img, crop.size[0], crop.size[1], 10, 0)
            if c_crop is not None:
                if c_blur > 0.1:
                    scaled_r = max(0.5, min(80.0, c_blur * dpr))
                    c_crop = c_crop.filter(ImageFilter.GaussianBlur(radius=scaled_r))
                if abs(c_bright - 1.0) > 0.01:
                    c_crop = ImageEnhance.Brightness(c_crop).enhance(c_bright)
                if abs(c_sat - 1.0) > 0.01:
                    c_crop = ImageEnhance.Color(c_crop).enhance(c_sat)
                blurred = c_crop
        except Exception as exc:
            log_line("could not open custom widget bg %s: %s" % (c_path, exc))

    if blurred is None:
        radius = max(18, min(40, int(18 * dpr)))
        blurred = crop.filter(ImageFilter.GaussianBlur(radius=radius))
        # Readability: compress highlights with a soft-knee curve and desaturate slightly
        import math
        curve = [int(130 * (1 - math.exp(-i / 110.0))) for i in range(256)]
        blurred = blurred.point(curve * 3)
        blurred = Image.blend(blurred, blurred.convert("L").convert("RGB"), 0.3)

    # A child of WorkerW has no per-pixel alpha, so the card is fully opaque.
    # Start from the *sharp* wallpaper crop (that is what shows in the rounded
    # corners, so they match the desktop exactly) and put the blur/custom bg inside a
    # rounded-rectangle mask.
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (inset, inset, crop.size[0] - 1 - inset, crop.size[1] - 1 - inset),
        radius=int(CORNER_RADIUS * dpr), fill=255)
    composite = crop.copy()
    composite.paste(blurred, (0, 0), mask)

    buffer = io.BytesIO()
    composite.save(buffer, format="PNG")
    pixmap = QtGui.QImage.fromData(buffer.getvalue())
    if pixmap.isNull():
        return None
    result = QtGui.QPixmap.fromImage(pixmap)
    result.setDevicePixelRatio(dpr)
    return result


# ---------------------------------------------------------------- widget
GLOW = 7               # transparent margin around the card body for the cyan glow
EDGE_PAD = GLOW + 15   # window edge -> text (logical px)
HEADER_H = 18
GAP = 10
LABEL_H = 14           # uppercase section label row
VALUE_ROW_H = 26       # big 16 pt value row
SPARK_H = 24           # sparkline incl. 4 px top margin
SUB_H = 16             # secondary text row
RAM_ROW_H = 22
BAR_H = 10             # 6 px bar + 4 px spacing
DISK_ROW_H = 16
DISK_SPARK_H = 24      # per-disk read/write graph (only with "Show disk I/O")
DISK_SCALE_MIN = 10.0  # MB/s: graph never scales below this, so idle noise stays flat
C_READ = "#A78BFA"     # read line colour on the disk graphs (write uses ACCENT)
FOOTER_H = 16
CORNER_RADIUS = 14
# PROCS tab never offers END for these: ending them crashes or logs out Windows.
PROTECTED_PROCS = {
    "system", "registry", "memcompression", "smss.exe", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe",
    "dwm.exe", "fontdrvhost.exe", "explorer.exe", "sihost.exe", "ctfmon.exe",
    "msmpeng.exe", "secure system", "conhost.exe", "audiodg.exe",
    # Linux: ending these logs the person out or breaks the desktop.
    "systemd", "init", "kthreadd", "xorg", "xwayland", "gnome-shell", "gnome-session-binary",
    "dbus-daemon", "dbus-broker", "gdm", "gdm3", "gdm-session-wor", "sshd", "networkmanager",
    "pipewire", "wireplumber", "systemd-logind", "login", "polkitd",
}
MIN_W = 280           # resize limits (logical px); log table needs ~280
MAX_W = 700
RESIZE_BAND = 12       # grab zone at each window edge (logical px)
TAB_H = 22             # LIVE / LOG tab strip at the top
LOG_ROW_H = 17

# ---------------------------------------------------------------- stats log (LOG tab)
STATS_LOG_PATH = APP_DIR / "stats_log.csv"
LOG_INTERVAL_S = 30 * 60
LOG_FIELDS = ["time", "cpu_load", "cpu_temp", "gpu_load", "gpu_temp",
              "vram_used_gb", "ram_used_gb", "ram_pct"]


def _log_float(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def read_stats_log(limit=200):
    """Last `limit` rows of the CSV as dicts (numbers as floats or None)."""
    if not STATS_LOG_PATH.exists():
        return []
    try:
        with open(STATS_LOG_PATH, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))[-limit:]
    except Exception as exc:
        log_line("stats log read failed: %s" % exc)
        return []
    return [dict({k: _log_float(row.get(k)) for k in LOG_FIELDS[1:]},
                 time=row.get("time") or "") for row in rows]


def append_stats_log(snapshot):
    """Append one row built from a metrics snapshot; returns the row or None."""
    row = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cpu_load": snapshot.get("cpu_load"),
        "cpu_temp": snapshot.get("cpu_temp"),
        "gpu_load": snapshot.get("gpu_load"),
        "gpu_temp": snapshot.get("gpu_temp"),
        "vram_used_gb": snapshot.get("vram_used"),
        "ram_used_gb": snapshot.get("ram_used"),
        "ram_pct": snapshot.get("ram_pct"),
    }
    for key in LOG_FIELDS[1:]:
        if row[key] is not None:
            row[key] = round(float(row[key]), 1)
    try:
        new = not STATS_LOG_PATH.exists()
        with open(STATS_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    except Exception as exc:
        log_line("stats log write failed: %s" % exc)
        return None
    return row


def open_stats_log():
    """Open the CSV in the default app (Excel / Notepad)."""
    try:
        if not STATS_LOG_PATH.exists():
            with open(STATS_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=LOG_FIELDS).writeheader()
        if IS_WINDOWS:
            os.startfile(str(STATS_LOG_PATH))
        else:
            subprocess.Popen(["open" if IS_MAC else "xdg-open", str(STATS_LOG_PATH)])
    except Exception as exc:
        log_line("could not open stats log: %s" % exc)

C_LABEL = "#AEB8CC"    # brighter than before: the tint is lighter now
C_VALUE = "#F4F7FB"
C_TEXT2 = "#D0D7E3"


def qt_font(size_pt, bold=False, family_hint=None):
    font = QtGui.QFont()
    # Segoe fonts only exist on Windows; elsewhere keep Qt's default sans family.
    fallbacks = ("Segoe UI Variable", "Segoe UI") if IS_WINDOWS else ()
    for family in (family_hint,) + fallbacks:
        if family:
            font.setFamily(family)
            break
    font.setPointSize(size_pt)
    font.setBold(bold)
    return font


class MonitorWidget(QtWidgets.QWidget):
    CARD_WIDTH = 320
    MARGIN = 24

    def __init__(self, config):
        super().__init__()
        self.setWindowTitle("Desktop Monitor")
        self.config = config
        self._snapshot = {}
        self._cpu_history = []
        self._gpu_history = []
        self._disk_history = {}           # mount -> [(read_mbps, write_mbps), ...] last 60
        self._disk_count = 2
        self._blurred = None
        self._blur_key = None
        self._wallpaper_settings = wallpaper_settings()
        self._custom_bg = custom_bg_settings()
        self._last_wallpaper_check = 0.0
        self._last_custom_bg_check = 0.0
        self._pinned = False
        self._parent_hwnd = 0
        self._last_minute = None
        self._zones = []                  # (QRectF, name) clickable areas, set in paint
        self._log_rows = read_stats_log()
        self._press = None                # Move mode: press point, for click detection
        self._dragged = False             # a real drag happened since the last press (Linux)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus)
        if not IS_WINDOWS:
            # On Windows the card is opaque on purpose: Qt draws translucent
            # windows with UpdateLayeredWindow, which a WorkerW child cannot
            # use (it shows solid black). paintEvent fills every pixel instead.
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        if IS_LINUX:
            self.apply_linux_window_flags()
        self._disk_zones = []             # (QRectF, mount) right-click areas, set in paint
        self._resizing = False            # live edge resize in progress
        self._pending_resize = None       # latest (l, t, r, b, edges) from the mouse hook
        self._resize_timer = QTimer(self)
        self._resize_timer.setInterval(16)   # apply at most ~60 times a second
        self._resize_timer.timeout.connect(self._apply_pending_resize)
        self._procs = []                  # PROCS tab rows: dicts name/count/rss/pids
        self._procs_time = 0.0
        self._kill_armed = None           # (name, monotonic time) after the first END click
        self._kill_note = None            # (text, monotonic time) result shown briefly
        self._procs_timer = QTimer(self)
        self._procs_timer.setInterval(3000)  # only runs while the PROCS tab is open
        try:
            from disk_alert import DiskAlertManager
            self._disk_alert = DiskAlertManager()
        except Exception as exc:
            log_line("disk alerts unavailable: %s" % exc)
            self._disk_alert = None
        self.worker = None                # set by main(); gets the settings pushed to it
        self._settings_open = False
        self._procs_timer.timeout.connect(self.refresh_procs)
        self._w = self._clamp_w(config.get("w") or self.CARD_WIDTH)
        self._min_h = self._compute_height()
        try:
            self._extra = max(0, int(config.get("extra_h") or 0))
        except (TypeError, ValueError):
            self._extra = 0
        self._height = self._min_h + self._extra
        self.setFixedSize(self._w, self._height)
        self._pos = self._default_position()
        self.setGeometry(self._pos[0], self._pos[1], self._w, self._height)

    def apply_linux_window_flags(self):
        """Linux has no WorkerW layer: locked = ask the window manager to keep the
        card at the bottom (honoured on X11, ignored on Wayland, where it just
        floats); Move mode = on top so it can be seen and dragged."""
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        flags |= (Qt.WindowStaysOnTopHint if move_mode(self.config)
                  else Qt.WindowStaysOnBottomHint)
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()
            self.move(self._pos[0], self._pos[1])

    # -- geometry -------------------------------------------------------------
    def _default_position(self):
        saved = self.config.get("x"), self.config.get("y")
        if saved[0] is not None and saved[1] is not None:
            return int(saved[0]), int(saved[1])
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        width = screen.geometry().width()
        return width - self._w - self.MARGIN, self.MARGIN

    def virtual_origin(self):
        if not IS_WINDOWS:
            return 0, 0
        try:
            return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
                    user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        except Exception as exc:
            log_line("GetSystemMetrics failed: %s" % exc)
            return 0, 0

    def card_screen(self):
        """Monitor under the card's saved position.

        Not self.screen(): once the window is re-parented into the desktop
        layer Qt keeps reporting the monitor it was created on, so the blur
        cropped the wrong monitor's wallpaper after a drag.
        """
        center = QtCore.QPoint(int(self._pos[0] + self._w / 2),
                               int(self._pos[1] + self._height / 2))
        return (QtGui.QGuiApplication.screenAt(center)
                or QtWidgets.QApplication.primaryScreen())

    def monitor_origin(self):
        screen = self.card_screen()
        return screen.geometry().x(), screen.geometry().y()

    def pin_position(self):
        """Widget position in WorkerW child coordinates (physical pixels)."""
        origin_x, origin_y = self.virtual_origin()
        dpr = self.devicePixelRatio()
        return (int(self._pos[0] * dpr) - origin_x, int(self._pos[1] * dpr) - origin_y)

    def _compute_height(self) -> int:
        height = EDGE_PAD + TAB_H + 6 + HEADER_H + GAP
        metric = LABEL_H + VALUE_ROW_H + SPARK_H + SUB_H + GAP
        height += metric * (2 if self._show_gpu() else 1)    # CPU (+ GPU when there is one)
        height += LABEL_H + RAM_ROW_H + BAR_H + GAP          # RAM
        if self._disk_count > 0:
            height += LABEL_H + 2                            # DISKS label
            height += self._disk_count * (DISK_ROW_H + BAR_H)
            if self.config.get("show_disk_io"):
                height += self._disk_count * DISK_SPARK_H
        if self._snapshot.get("hints"):
            height += FOOTER_H
        height += EDGE_PAD - 4
        return height

    def _show_gpu(self):
        """False only when the snapshot says this PC has no graphics adapter at all.

        On Linux the block stays and says "No GPU readings" instead."""
        return bool(self._snapshot.get("gpu_present", True)) or IS_LINUX

    @staticmethod
    def _clamp_w(value):
        try:
            return max(MIN_W, min(MAX_W, int(value)))
        except (TypeError, ValueError):
            return MonitorWidget.CARD_WIDTH

    def _spark_h(self):
        """Extra height from resizing is shared by the two sparklines."""
        return SPARK_H + self._extra // 2

    def apply_size(self):
        self._height = self._min_h + self._extra
        if self.size() != QtCore.QSize(self._w, self._height):
            self.setFixedSize(self._w, self._height)
        if not self._resizing:
            self._blurred = None
        self.place_pinned(force=True)
        if self._resizing:
            self.repaint()             # paint now, at the new size, in one step

    def edges_at(self, x, y, width, height):
        """Resize edges ('L','R','T','B' letters) under a physical point in the card."""
        band = RESIZE_BAND * (self.devicePixelRatio() or 1.0)
        edges = ""
        if x < band:
            edges += "L"
        elif x >= width - band:
            edges += "R"
        if y < band:
            edges += "T"
        elif y >= height - band:
            edges += "B"
        return edges

    def resize_physical(self, left, top, right, bottom, edges):
        """Resize from a physical screen rectangle, keeping the opposite edges put."""
        dpr = self.devicePixelRatio() or 1.0
        min_w, max_w = MIN_W * dpr, MAX_W * dpr
        min_h = self._min_h * dpr
        if "L" in edges:
            left = max(right - max_w, min(left, right - min_w))
        elif "R" in edges:
            right = min(left + max_w, max(right, left + min_w))
        if "T" in edges:
            top = min(top, bottom - min_h)
        elif "B" in edges:
            bottom = max(bottom, top + min_h)
        w = self._clamp_w(round((right - left) / dpr))
        extra = max(0, int(round((bottom - top) / dpr)) - self._min_h)
        # Anchor the edge that is NOT being dragged, so it never wobbles by a
        # pixel from rounding the position and the size separately.
        x = int(round(right / dpr)) - w if "L" in edges else int(round(left / dpr))
        y = (int(round(bottom / dpr)) - (self._min_h + extra) if "T" in edges
             else int(round(top / dpr)))
        if (w, extra, (x, y)) == (self._w, self._extra, self._pos):
            return
        self._w, self._extra, self._pos = w, extra, (x, y)
        self.apply_size()

    def queue_resize(self, left, top, right, bottom, edges):
        """Called from the mouse hook: only store the target, never resize there.

        A low-level mouse hook that does real work stalls the whole system
        cursor, which is what made edge/corner dragging lag. The timer applies
        the newest target at most every 16 ms on the normal event loop.
        """
        self._pending_resize = (left, top, right, bottom, edges)
        if not self._resizing:
            self._resizing = True
            self._resize_timer.start()

    def _apply_pending_resize(self):
        pending, self._pending_resize = self._pending_resize, None
        if pending is not None:
            self.resize_physical(*pending)

    def end_resize(self):
        self._resize_timer.stop()
        self._apply_pending_resize()
        self._resizing = False
        self.finish_resize()

    def finish_resize(self):
        self.config["w"], self.config["extra_h"] = self._w, self._extra
        self.config["x"], self.config["y"] = self._pos
        save_config(self.config)
        log_line("card resized to %dx%d at %s" % (self._w, self._height, (self._pos,)))
        self._blurred = None
        self.refresh_blur(force=True)
        self.update()

    def disk_at(self, x, y):
        """Mount point of the disk row under this widget-local logical point."""
        point = QtCore.QPointF(x, y)
        for rect, mount in self._disk_zones:
            if rect.contains(point):
                return mount
        return None

    def show_disk_menu(self, mount):
        menu = QtWidgets.QMenu()
        action = menu.addAction("Open folder in File Explorer" if IS_WINDOWS
                                else "Open folder in the file manager")
        if menu.exec(QtGui.QCursor.pos()) is action:
            try:
                if IS_WINDOWS:
                    os.startfile(mount)
                else:
                    subprocess.Popen(["open" if IS_MAC else "xdg-open", mount])
                log_line("opened %s in the file viewer" % mount)
            except Exception as exc:
                log_line("could not open %s: %s" % (mount, exc))

    # -- frosted glass --------------------------------------------------------
    def refresh_blur(self, force=False):
        if Image is None:
            return None
        now = time.monotonic()
        if now - self._last_wallpaper_check > 30.0:
            self._last_wallpaper_check = now
            settings = wallpaper_settings()
            if settings != self._wallpaper_settings:
                self._wallpaper_settings = settings
                force = True
        if now - self._last_custom_bg_check > 1.5 or force:
            self._last_custom_bg_check = now
            c_bg = custom_bg_settings()
            if c_bg != self._custom_bg:
                self._custom_bg = c_bg
                force = True
        dpr = self.devicePixelRatio()
        key = (self._pos[0], self._pos[1], self._w, self._height,
               self._wallpaper_settings[0], self._wallpaper_settings[1],
               self._wallpaper_settings[2], self._wallpaper_settings[3],
               self._custom_bg, dpr)
        if self._blurred is not None and key == self._blur_key and not force:
            return self._blurred
        if self._resizing and self._blurred is not None:
            # Rebuilding the blur costs tens of ms; during a live resize the
            # old one is stretched (paintEvent) and rebuilt once on release.
            return self._blurred
        physical_x = int(self._pos[0] * dpr) - self.monitor_origin()[0] * dpr
        physical_y = int(self._pos[1] * dpr) - self.monitor_origin()[1] * dpr
        screen = self.card_screen()
        rect = (screen.geometry().x() * dpr, screen.geometry().y() * dpr,
                screen.geometry().width() * dpr, screen.geometry().height() * dpr)
        self._blurred = build_blur_rect(rect, (physical_x, physical_y,
                                              int(self._w * dpr),
                                              int(self._height * dpr)),
                                        self._wallpaper_settings, dpr,
                                        custom_bg=self._custom_bg)
        self._blur_key = key
        return self._blurred

    # -- painting -------------------------------------------------------------
    def paintEvent(self, _event):
        if os.environ.get("DESKTOP_MONITOR_DEBUG"):
            self._paints = getattr(self, "_paints", 0) + 1
            if self._paints <= 3 or self._paints % 10 == 0:
                log_line("debug: paint #%d, snapshot keys %d" % (self._paints, len(self._snapshot)))
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)

        # The card body is inset by GLOW so the glow can spread onto the
        # wallpaper around it. The window is clipped to a rounded region
        # (apply_round_region) and every pixel inside is painted: outside the
        # body that is the sharp wallpaper crop, so it blends with the desktop.
        body = QRectF(GLOW + 0.5, GLOW + 0.5,
                      self.width() - 2 * GLOW - 1, self.height() - 2 * GLOW - 1)
        path = QPainterPath()
        path.addRoundedRect(body, CORNER_RADIUS, CORNER_RADIUS)

        if not IS_WINDOWS:
            # The window is translucent here (no region clip like on Windows), so
            # keep the background inside the rounded body.
            painter.save()
            painter.setClipPath(path)
        background = self.refresh_blur()
        if background is not None:
            if self._resizing:
                painter.drawPixmap(QRect(0, 0, self.width(), self.height()), background)
            else:
                painter.drawPixmap(0, 0, background)   # sharp edges + blurred body
        else:
            painter.fillRect(QRect(0, 0, self.width(), self.height()), QColor("#101218"))
        if not IS_WINDOWS:
            painter.restore()

        overlay = QColor("#0B0D12")
        eff_opacity = self._custom_bg[4] if self._custom_bg is not None else opacity_fraction(self.config)
        overlay.setAlpha(int(255 * eff_opacity))
        painter.setBrush(overlay)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        # Cyan glow: wide faint strokes fading out, then a crisp bright edge.
        accent = QColor(self.ACCENT)
        painter.setBrush(Qt.NoBrush)
        for width, alpha in ((14, 10), (10, 18), (7, 30), (4.5, 55), (2.5, 95)):
            glow = QColor(accent)
            glow.setAlpha(alpha if not move_mode(self.config) else min(255, alpha * 2))
            painter.setPen(QPen(glow, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)
        edge = QColor("#67E8F9")
        edge.setAlpha(235)
        painter.setPen(QPen(edge, 1.3))
        painter.drawPath(path)

        self._zones = []
        self._disk_zones = []
        y = EDGE_PAD
        y = self._draw_tabs(painter, y)
        y += 6
        y = self._draw_header(painter, y)
        y += GAP
        if self.config.get("tab") == "log":
            self._draw_log(painter, y)
            painter.end()
            return
        if self.config.get("tab") == "procs":
            self._draw_procs(painter, y)
            painter.end()
            return
        snap = self._snapshot
        y = self._draw_metric(painter, y, "CPU",
                              fmt_percent(snap.get("cpu_load")), snap.get("cpu_load"),
                              fmt_temp(snap.get("cpu_temp"), snap.get("cpu_temp_approx")),
                              snap.get("cpu_temp"), 70.0, 85.0)
        y = self._draw_sparkline(painter, y, self._cpu_history)
        y = self._draw_secondary(painter, y, snap.get("cpu_name") or DASH)
        y += GAP
        if self._show_gpu():
            y = self._draw_metric(painter, y, "GPU",
                                  fmt_percent(snap.get("gpu_load")), snap.get("gpu_load"),
                                  fmt_temp(snap.get("gpu_temp")), snap.get("gpu_temp"),
                                  70.0, 83.0)
            y = self._draw_sparkline(painter, y, self._gpu_history)
            name = snap.get("gpu_name") or ""
            vram = DASH
            if IS_LINUX and not snap.get("gpu_present", True):
                vram = "No GPU readings"
            if snap.get("vram_total"):
                vram = "VRAM %s / %s GB" % (fmt_num(snap.get("vram_used")),
                                            fmt_num(snap.get("vram_total")))
            y = self._draw_secondary(painter, y, vram, name)
            y += GAP
        y = self._draw_ram(painter, y)
        y += GAP
        if self._snapshot.get("disks"):
            y = self._draw_disks(painter, y)
        hints = snap.get("hints") or []
        if hints:
            y = self._draw_footer(painter, y, hints)
        painter.end()

    ACCENT = "#22D3EE"

    # All text is drawn inside [EDGE_PAD, width - EDGE_PAD].
    def _left(self):
        return EDGE_PAD

    def _right(self):
        return self.width() - EDGE_PAD

    def _label_font(self):
        font = qt_font(9)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        return font

    def _text(self, painter, x, baseline, text, font, color, align_right=False,
              max_width=None):
        """Draw one line; right-aligned text ends at x. Elides to max_width."""
        painter.setFont(font)
        painter.setPen(QColor(color) if isinstance(color, str) else color)
        metrics = QtGui.QFontMetrics(font)
        if max_width is not None:
            text = metrics.elidedText(text, Qt.ElideRight, int(max_width))
        width = metrics.horizontalAdvance(text)
        left = int(x - width) if align_right else int(x)
        # Dark halo (all 8 neighbours + a drop shadow) keeps text readable
        # over any wallpaper with a light tint.
        pen = painter.pen()
        painter.setPen(QColor(0, 0, 0, 90))
        for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
            painter.drawText(left + dx, int(baseline) + dy, text)
        painter.setPen(QColor(0, 0, 0, 140))
        painter.drawText(left + 1, int(baseline) + 2, text)
        painter.setPen(pen)
        painter.drawText(left, int(baseline), text)
        return width

    def _pill(self, painter, rect, text, active, color=None, zone=None):
        """Rounded button; registers rect as a clickable zone."""
        tint = QColor(color or self.ACCENT)
        fill = QColor(tint)
        fill.setAlpha(70 if active else 0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill if active else QColor(255, 255, 255, 16))
        radius = rect.height() / 2
        painter.drawRoundedRect(rect, radius, radius)
        border = QColor(tint)
        border.setAlpha(220 if active else 90)
        painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)
        font = qt_font(8, True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
        metrics = QtGui.QFontMetrics(font)
        width = metrics.horizontalAdvance(text)
        baseline = rect.top() + (rect.height() + metrics.ascent() - metrics.descent()) / 2
        self._text(painter, rect.center().x() - width / 2, baseline, text, font,
                   "#FFFFFF" if active else C_TEXT2)
        if zone:
            self._zones.append((QRectF(rect), zone))

    def _draw_tabs(self, painter, y):
        tab = self.config.get("tab")
        left = self._left()
        self._pill(painter, QRectF(left, y, 54, TAB_H - 2), "LIVE", tab not in ("log", "procs"),
                   zone="tab_live")
        self._pill(painter, QRectF(left + 60, y, 54, TAB_H - 2), "LOG", tab == "log",
                   zone="tab_log")
        self._pill(painter, QRectF(left + 120, y, 62, TAB_H - 2), "PROCS", tab == "procs",
                   zone="tab_procs")
        return y + TAB_H

    def _draw_log(self, painter, y):
        recording = bool(self.config.get("log_recording"))
        left, right = self._left(), self._right()
        inner = right - left
        # Buttons share the row and shrink with the card: the open button
        # keeps its text width, the record button takes the rest and drops
        # to a short label when the long one would not fit.
        pill_font = qt_font(8, True)
        pill_font.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
        pill_metrics = QtGui.QFontMetrics(pill_font)
        open_w = min(92, pill_metrics.horizontalAdvance("OPEN FILE") + 22)
        record_w = min(168, inner - open_w - 8)
        record_text = "■  STOP RECORDING" if recording else "●  START RECORDING"
        if pill_metrics.horizontalAdvance(record_text) + 18 > record_w:
            record_text = "■  STOP" if recording else "●  RECORD"
        self._pill(painter, QRectF(left, y, record_w, 26), record_text,
                   recording, "#EF4444" if recording else self.ACCENT, zone="record")
        self._pill(painter, QRectF(right - open_w, y, open_w, 26), "OPEN FILE", False,
                   zone="open_log")
        y += 26 + 6
        if recording:
            last = float(self.config.get("log_last") or 0.0)
            due = datetime.datetime.fromtimestamp(max(last + LOG_INTERVAL_S, time.time()))
            status = "Saving every 30 min · next at %s" % due.strftime("%H:%M")
        else:
            status = "Press record to save stats every 30 min"
        y = self._draw_secondary(painter, y, status)
        y += GAP - 2

        # columns: time | CPU % | CPU temp | GPU % | GPU temp | RAM %
        # Widths come from the font so nothing overlaps at any card width.
        # Value columns are right-aligned and spread over the space left of
        # the time column; when even the minimum does not fit, the date is
        # dropped from the time stamp.
        font = qt_font(9)
        metrics = QtGui.QFontMetrics(font)
        label_font = self._label_font()
        label_metrics = QtGui.QFontMetrics(label_font)
        value_w = max(metrics.horizontalAdvance("100%"), metrics.horizontalAdvance("100°C"),
                      label_metrics.horizontalAdvance("CPU"),
                      label_metrics.horizontalAdvance("GPU"))
        min_gap = 6
        stamp_fmt = "%d/%m  %H:%M"
        time_w = metrics.horizontalAdvance("00/00  00:00")
        if time_w + 5 * (value_w + min_gap) > inner:
            stamp_fmt = "%H:%M"
            time_w = metrics.horizontalAdvance("00:00")
        # Right edges of the five value columns, evenly spaced, RAM at the edge.
        span = inner - time_w
        step = max(value_w + min_gap, span / 5.0)
        cols = [right - step * (4 - i) for i in range(5)]
        self._text(painter, left, y + 10, "TIME", label_font, C_LABEL)
        for x, name in zip(cols, ("CPU", "°C", "GPU", "°C", "RAM")):
            self._text(painter, x, y + 10, name, label_font, C_LABEL, align_right=True)
        y += LABEL_H + 2

        bottom = self.height() - EDGE_PAD + 4
        rows = list(reversed(self._log_rows))[:max(0, int((bottom - y) // LOG_ROW_H))]
        if not rows:
            self._text(painter, left, y + 13, "No entries yet", qt_font(9), C_TEXT2)
            return y + LOG_ROW_H
        for index, row in enumerate(rows):
            if index % 2 == 0:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 10))
                painter.drawRoundedRect(QRectF(left - 4, y + 1, right - left + 8,
                                               LOG_ROW_H - 2), 3, 3)
            stamp = row.get("time") or ""
            try:
                stamp = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M").strftime(stamp_fmt)
            except ValueError:
                pass
            self._text(painter, left, y + 13, stamp, font, C_VALUE)
            values = (
                (fmt_percent(row.get("cpu_load")), level_color(row.get("cpu_load"), 60.0, 85.0)),
                (fmt_temp(row.get("cpu_temp")), level_color(row.get("cpu_temp"), 70.0, 85.0, temp=True)),
                (fmt_percent(row.get("gpu_load")), level_color(row.get("gpu_load"), 60.0, 85.0)),
                (fmt_temp(row.get("gpu_temp")), level_color(row.get("gpu_temp"), 70.0, 83.0, temp=True)),
                (fmt_percent(row.get("ram_pct")), level_color(row.get("ram_pct"), 70.0, 90.0)),
            )
            for x, (text, color) in zip(cols, values):
                if text == DASH or text.startswith(DASH):
                    color = C_TEXT2
                self._text(painter, x, y + 13, text, font, color, align_right=True)
            y += LOG_ROW_H
        return y

    def _draw_gear(self, painter, cx, cy, radius, color):
        """Small gear glyph drawn with QPainter (no font or image needed)."""
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, 1.4))
        painter.drawEllipse(QtCore.QPointF(cx, cy), radius * 0.55, radius * 0.55)
        painter.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.FlatCap))
        for step in range(8):
            angle = step * math.pi / 4.0
            painter.drawLine(QtCore.QPointF(cx + math.cos(angle) * radius * 0.75,
                                            cy + math.sin(angle) * radius * 0.75),
                             QtCore.QPointF(cx + math.cos(angle) * radius,
                                            cy + math.sin(angle) * radius))
        painter.restore()

    def _draw_header(self, painter, y):
        host = self._snapshot.get("host") or platform.node() or socket.gethostname()
        clock = datetime.datetime.now().strftime("%H:%M")
        right = self._right()
        clock_w = self._text(painter, right, y + 13, clock, qt_font(9), C_TEXT2,
                             align_right=True)
        # Gear: opens the Settings window.
        gear_cx = right - clock_w - 14
        self._draw_gear(painter, gear_cx, y + 9.5, 6.0, QColor(C_LABEL))
        self._zones.append((QRectF(gear_cx - 9, y, 18, HEADER_H), "settings"))
        limit = gear_cx - 10

        host_w = self._text(painter, self._left(), y + 13, host, qt_font(10, True), C_VALUE,
                            max_width=max(40, (limit - self._left()) * 0.55))
        cur_x = self._left() + host_w + 14

        # Optional status feed indicator: only when the user picked a file.
        feed_ok = self._snapshot.get("feed_active")
        feed_path = str(self.config.get("feed_path") or "").strip()
        if feed_path and feed_ok is not None:
            label = (str(self.config.get("feed_label") or "").strip() or "FEED")[:12]
            dot_color = QColor("#34D399") if feed_ok else QColor("#EF4444")
            dot_cx = cur_x + 5
            dot_cy = y + 9.5
            painter.setPen(Qt.NoPen)
            glow = QColor(dot_color)
            glow.setAlpha(75)
            painter.setBrush(glow)
            painter.drawEllipse(QtCore.QPointF(dot_cx, dot_cy), 5.5, 5.5)
            painter.setBrush(dot_color)
            painter.drawEllipse(QtCore.QPointF(dot_cx, dot_cy), 3.2, 3.2)
            text_x = cur_x + 13
            text_w = self._text(painter, text_x, y + 13, label, qt_font(8, True),
                                dot_color if feed_ok else C_TEXT2)
            self._zones.append((QRectF(cur_x - 3, y, text_w + 18, HEADER_H), "open_feed"))
            cur_x = text_x + text_w + 12

        # Recording status indicator & toggle button
        if self.config.get("log_recording"):
            rec_color = QColor("#EF4444")
            rec_cx = cur_x + 4
            rec_cy = y + 9.5
            painter.setPen(Qt.NoPen)
            painter.setBrush(rec_color)
            painter.drawEllipse(QtCore.QPointF(rec_cx, rec_cy), 3.2, 3.2)

            rec_text_x = cur_x + 10
            rec_w = self._text(painter, rec_text_x, y + 13, "REC", qt_font(8, True), rec_color)
            self._zones.append((QRectF(cur_x - 2, y, rec_w + 15, HEADER_H), "record"))

        return y + HEADER_H

    def _draw_metric(self, painter, y, label, value_text, load, temp_text,
                     temp_value, temp_warn, temp_crit):
        self._text(painter, self._left(), y + 10, label, self._label_font(), C_LABEL)
        y += LABEL_H
        color = level_color(load, 60.0, 85.0) if load is not None else C_TEXT2
        self._text(painter, self._left(), y + 21, value_text, qt_font(16, True), color)
        temp_color = (level_color(temp_value, temp_warn, temp_crit, temp=True)
                      if temp_value is not None else C_TEXT2)
        self._text(painter, self._right(), y + 21, temp_text, qt_font(13, True),
                   temp_color, align_right=True)
        return y + VALUE_ROW_H

    def _draw_sparkline(self, painter, y, samples):
        top = y + 4
        spark_h = self._spark_h()
        height = spark_h - 6
        left, right = self._left(), self._right()
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.drawLine(left, top + height, right, top + height)
        samples = list(samples)[-60:]
        if len(samples) >= 2:
            step = (right - left) / 59.0
            start = right - step * (len(samples) - 1)
            path = QPainterPath()
            fill = QPainterPath()
            fill.moveTo(start, top + height)
            for index, value in enumerate(samples):
                px = start + index * step
                py = top + height - (max(0.0, min(100.0, value)) / 100.0) * height
                if index == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
                fill.lineTo(px, py)
            fill.lineTo(right, top + height)
            fill.closeSubpath()
            area = QColor(self.ACCENT)
            area.setAlpha(40)
            painter.setPen(Qt.NoPen)
            painter.setBrush(area)
            painter.drawPath(fill)
            painter.setPen(QPen(QColor(self.ACCENT), 1.4))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        return y + spark_h

    def _draw_secondary(self, painter, y, text, right_text=""):
        right_w = 0
        if right_text:
            right_w = self._text(painter, self._right(), y + 12, right_text, qt_font(9),
                                 C_TEXT2, align_right=True,
                                 max_width=(self._right() - self._left()) * 0.55)
        self._text(painter, self._left(), y + 12, text, qt_font(9), C_TEXT2,
                   max_width=self._right() - self._left() - right_w - 10)
        return y + SUB_H

    def _draw_bar(self, painter, y, fraction, value, warn, crit):
        left, right = self._left(), self._right()
        width = right - left
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 18))
        painter.drawRoundedRect(QtCore.QRectF(left, y, width, 6), 3, 3)
        fill = max(0.0, min(1.0, fraction))
        if fill > 0:
            painter.setBrush(level_color(value if value is not None else fill * 100.0,
                                         warn, crit))
            painter.drawRoundedRect(QtCore.QRectF(left, y, max(6.0, width * fill), 6), 3, 3)
        return y + BAR_H

    def _draw_ram(self, painter, y):
        snap = self._snapshot
        pct = snap.get("ram_pct")
        self._text(painter, self._left(), y + 10, "RAM", self._label_font(), C_LABEL)
        y += LABEL_H
        text = DASH
        if snap.get("ram_total"):
            text = "%s / %s GB" % (fmt_num(snap.get("ram_used")),
                                   fmt_num(snap.get("ram_total")))
        self._text(painter, self._left(), y + 16, text, qt_font(11, True), C_VALUE)
        self._text(painter, self._right(), y + 16, fmt_percent(pct), qt_font(11, True),
                   level_color(pct, 70.0, 90.0) if pct is not None else C_TEXT2,
                   align_right=True)
        y += RAM_ROW_H
        return self._draw_bar(painter, y, (pct or 0.0) / 100.0, pct, 70.0, 90.0)

    def _draw_disks(self, painter, y):
        self._text(painter, self._left(), y + 10, "DISKS", self._label_font(), C_LABEL)
        y += LABEL_H + 2
        show_io = bool(self.config.get("show_disk_io"))
        for row in self._snapshot.get("disks") or []:
            if row.get("total_gb"):
                zone_h = DISK_ROW_H + BAR_H + (DISK_SPARK_H if show_io else 0)
                self._disk_zones.append((QRectF(0, y, self.width(), zone_h), row["mount"]))
            io_w = 0
            if show_io and (row.get("read_mbps") is not None
                            or row.get("write_mbps") is not None):
                io = "R %s  W %s" % (fmt_num(row.get("read_mbps")),
                                     fmt_num(row.get("write_mbps")))
                io_w = self._text(painter, self._right(), y + 12, io + " MB/s", qt_font(9),
                                  C_TEXT2, align_right=True)
            if row.get("total_gb"):
                line = "%s   %s GB free of %s GB" % (row["mount"],
                                                     fmt_num(row.get("free_gb"), 0),
                                                     fmt_num(row.get("total_gb"), 0))
            else:
                line = row.get("mount") or DASH
            self._text(painter, self._left(), y + 12, line, qt_font(9), C_TEXT2,
                       max_width=self._right() - self._left() - io_w - 10)
            y += DISK_ROW_H
            pct = row.get("used_pct")
            if pct is not None:
                y = self._draw_bar(painter, y, pct / 100.0, pct, 80.0, 92.0)
            else:
                y += BAR_H
            if show_io:
                y = self._draw_disk_graph(painter, y,
                                          self._disk_history.get(row.get("mount"), []))
        return y

    def _draw_disk_graph(self, painter, y, samples):
        """Read/write MB/s over the last 60 ticks, CPU/GPU sparkline style.

        Write is the filled cyan area, read is a thin violet line. The scale
        follows the busiest moment on screen (never below DISK_SCALE_MIN), and
        that peak is printed small at the top right.
        """
        top = y + 2
        height = DISK_SPARK_H - 6
        left, right = self._left(), self._right()
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.drawLine(left, top + height, right, top + height)
        samples = list(samples)[-60:]
        if len(samples) < 2:
            return y + DISK_SPARK_H
        peak = max(max(r, w) for r, w in samples)
        scale = max(DISK_SCALE_MIN, peak)
        step = (right - left) / 59.0
        start = right - step * (len(samples) - 1)

        def line(index):
            path = QPainterPath()
            for i, sample in enumerate(samples):
                px = start + i * step
                py = top + height - min(1.0, sample[index] / scale) * height
                if i == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
            return path

        write = line(1)
        fill = QPainterPath(write)
        fill.lineTo(right, top + height)
        fill.lineTo(start, top + height)
        fill.closeSubpath()
        area = QColor(self.ACCENT)
        area.setAlpha(40)
        painter.setPen(Qt.NoPen)
        painter.setBrush(area)
        painter.drawPath(fill)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(self.ACCENT), 1.4))
        painter.drawPath(write)
        painter.setPen(QPen(QColor(C_READ), 1.1))
        painter.drawPath(line(0))
        if peak >= 0.1:
            self._text(painter, right, top + 7, "peak %s MB/s" % fmt_num(peak, 0 if peak >= 10 else 1),
                       qt_font(7), C_TEXT2, align_right=True)
        return y + DISK_SPARK_H

    def _draw_footer(self, painter, y, hints):
        text = "  \u00b7  ".join(h for h in hints if h)
        self._text(painter, self._left(), y + 12, text, qt_font(8), "#F59E0B",
                   max_width=self._right() - self._left())
        return y + FOOTER_H

    # -- pinning ------------------------------------------------------------
    def apply_extended_style(self, hwnd):
        if not IS_WINDOWS:
            return
        style = get_window_long(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if move_mode(self.config):
            style &= ~WS_EX_TRANSPARENT
            style &= ~WS_EX_LAYERED
        else:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        if not set_window_long(hwnd, GWL_EXSTYLE, style):
            log_line("SetWindowLongW(exstyle) failed")
        if style & WS_EX_LAYERED:
            # A layered window is never drawn until its layered attributes are
            # set. Without this the pinned card is invisible, Qt never sees it as
            # exposed, and every update() is dropped. Alpha 255 = fully opaque;
            # the card's own opacity comes from the overlay in paintEvent.
            try:
                if not user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA):
                    log_line("SetLayeredWindowAttributes failed (error %d)"
                             % ctypes.GetLastError())
            except Exception as exc:
                log_line("SetLayeredWindowAttributes failed: %s" % exc)

    def pin_to_desktop(self):
        """Pin to the desktop layer and place the card at the saved position."""
        if not IS_WINDOWS:
            return False
        hwnd = int(self.winId())
        if self._pinned and get_parent(hwnd) == self._parent_hwnd and is_window(hwnd):
            return True
        if not pin_to_desktop(hwnd):
            return False
        self._parent_hwnd = get_parent(hwnd)
        self._pinned = True
        self.apply_extended_style(hwnd)
        self.place_pinned(force=True)
        return True

    def place_pinned(self, force=False):
        """Put the pinned card at its saved spot, in WorkerW child pixels.

        Qt does not know the window was re-parented, so any Qt geometry change
        (setFixedSize, setGeometry) re-applies Qt's idea of a *screen* position
        to what is now a child window, shifting it by the virtual-screen origin.
        So Qt is never asked to move a pinned card; this is called after every
        resize and from the 5 s heartbeat, and fixes any drift.
        """
        if not IS_WINDOWS or not self._pinned:
            return
        hwnd = int(self.winId())
        child_x, child_y = self.pin_position()
        dpr = self.devicePixelRatio()
        want = (child_x, child_y, int(self._w * dpr), int(self._height * dpr))
        if not force:
            current = child_rect(hwnd)
            if current == want:
                return
            log_line("pinned card drifted to %s, moving back to %s" % (current, want))
        if not set_window_position_physical(hwnd, *want):
            log_line("could not position the pinned card")
        self.apply_round_region(hwnd, want[2], want[3])

    def apply_round_region(self, hwnd, width, height):
        """Clip the window to a rounded rectangle so the corners are truly cut.

        The region is set only when the size changes (Windows owns the region
        handle once SetWindowRgn succeeds, so it is never freed here).
        """
        if not IS_WINDOWS or getattr(self, "_region_size", None) == (width, height):
            return
        try:
            diameter = int(2 * (CORNER_RADIUS + GLOW) * self.devicePixelRatio())
            region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1,
                                                            diameter, diameter)
            if region and user32.SetWindowRgn(hwnd, region, True):
                self._region_size = (width, height)
            else:
                log_line("SetWindowRgn failed")
                if region:
                    ctypes.windll.gdi32.DeleteObject(region)
        except Exception as exc:
            log_line("rounded window region failed: %s" % exc)

    def unpin_from_desktop(self):
        """Release the card from the desktop layer so it can be dragged."""
        if not IS_WINDOWS:
            return False
        hwnd = int(self.winId())
        if not unpin_from_desktop(hwnd):
            return False
        self._parent_hwnd = 0
        self._pinned = False
        self.apply_extended_style(hwnd)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.show()
        self.move(self._pos[0], self._pos[1])
        self._region_size = None   # setWindowFlags recreated the native window
        dpr = self.devicePixelRatio()
        self.apply_round_region(int(self.winId()), int(self._w * dpr),
                                int(self._height * dpr))
        log_line("unpinned so the card can be moved")
        return True

    def repin_if_needed(self):
        """Every 5 s: Explorer restarts replace the desktop layer."""
        if not IS_WINDOWS or move_mode(self.config) or not self._pinned:
            return
        hwnd = int(self.winId())
        parent = get_parent(hwnd)
        if not is_window(hwnd) or not parent or parent != self._parent_hwnd:
            log_line("desktop layer changed (parent %s), re-pinning" % parent)
            self._pinned = False
            self._parent_hwnd = 0
            self.pin_to_desktop()
        else:
            self.place_pinned()

    # -- interaction ----------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            mount = self.disk_at(event.position().x(), event.position().y())
            if mount:
                self.show_disk_menu(mount)
            elif IS_LINUX:
                self.show_card_menu()      # fallback for desktops without a tray
            return
        if not (move_mode(self.config) or IS_LINUX):
            return          # Windows locked: the desktop hook delivers clicks
        if event.button() == Qt.LeftButton:
            self._drag_delta = None
            self._dragged = False
            self._press = (event.globalPosition().x(), event.globalPosition().y(),
                           event.position().x(), event.position().y())
            self._grab = (event.globalPosition().x() - self._pos[0],
                          event.globalPosition().y() - self._pos[1])

    def mouseMoveEvent(self, event):
        if getattr(self, "_grab", None) is None:
            return
        if IS_LINUX:
            # Linux: drag from anywhere on the card, locked or not. A press that
            # has not travelled more than 4 px is still a click on a button.
            if not self.config.get("drag_on_desktop", True) and not move_mode(self.config):
                return
            press = self._press
            if press is not None and (abs(event.globalPosition().x() - press[0])
                                      + abs(event.globalPosition().y() - press[1])) <= 4:
                return
            if wayland_session():
                # Wayland ignores QWidget.move(): ask the compositor to move the
                # window. It sends no release event to us afterwards.
                self._press = None
                self._grab = None
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
                return
            self._dragged = True
        elif not move_mode(self.config):
            return
        x = int(event.globalPosition().x() - self._grab[0])
        y = int(event.globalPosition().y() - self._grab[1])
        if (x, y) == self._pos:
            return
        self._pos = (x, y)
        self.move(x, y)
        self.refresh_blur(force=True)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._grab = None
            press, self._press = self._press, None
            if press is not None and (abs(event.globalPosition().x() - press[0])
                                      + abs(event.globalPosition().y() - press[1])) <= 4:
                zone = self.zone_at(press[2], press[3])
                if zone:
                    self.handle_zone(zone)
            if IS_LINUX and not move_mode(self.config) and not self._dragged:
                return          # locked: a click never moved the card, nothing to save
            self.config["x"], self.config["y"] = self._pos
            save_config(self.config)

    def show_card_menu(self):
        """Right-click menu on the card (Linux): Settings and Quit, so the app stays
        usable on a desktop with no system tray."""
        menu = QtWidgets.QMenu()
        settings_action = menu.addAction("Settings...")
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        chosen = menu.exec(QtGui.QCursor.pos())
        if chosen is settings_action:
            QTimer.singleShot(0, self.open_settings)
        elif chosen is quit_action:
            QtWidgets.QApplication.instance().quit()

    # -- buttons on the card (tabs, record, open file) -------------------------
    def zone_at(self, x, y):
        """Name of the button under this widget-local logical point, or None."""
        point = QtCore.QPointF(x, y)
        for rect, name in self._zones:
            if rect.adjusted(-3, -3, 3, 3).contains(point):
                return name
        return None

    def handle_zone(self, name):
        log_line("card button: %s" % name)
        if name in ("tab_live", "tab_log", "tab_procs"):
            self.config["tab"] = name[4:]
            save_config(self.config)
            self.sync_procs_timer()
        elif name.startswith("kill:"):
            self.kill_clicked(name[5:])
        elif name == "record":
            self.toggle_recording()
        elif name == "open_log":
            open_stats_log()
        elif name == "open_feed":
            open_status_feed(self.config)
        elif name == "settings":
            QTimer.singleShot(0, self.open_settings)
        self.update()

    # -- PROCS tab -------------------------------------------------------------
    def sync_procs_timer(self):
        """Poll processes only while the PROCS tab is showing."""
        if self.config.get("tab") == "procs":
            if not self._procs_timer.isActive():
                self.refresh_procs()
                self._procs_timer.start()
        else:
            self._procs_timer.stop()
            self._kill_armed = None

    def refresh_procs(self):
        if psutil is None:
            return
        groups = {}
        own = (os.getpid(), os.getppid())   # this widget (and its venv launcher)
        for proc in psutil.process_iter(["name", "memory_info"]):
            try:
                info = proc.info
                name = info.get("name") or ""
                mem = info.get("memory_info")
                if not name or mem is None or proc.pid in (0, 4) or proc.pid in own:
                    continue
                group = groups.setdefault(name.lower(), {"name": name, "count": 0,
                                                         "rss": 0, "pids": []})
                group["count"] += 1
                group["rss"] += mem.rss
                group["pids"].append(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        self._procs = sorted(groups.values(), key=lambda g: g["rss"], reverse=True)[:40]
        self._procs_time = time.monotonic()
        if self._kill_armed and time.monotonic() - self._kill_armed[1] > 4.0:
            self._kill_armed = None
        self.update()

    def kill_clicked(self, key):
        """First click arms the END button (shows SURE?), a second within 4 s ends it."""
        now = time.monotonic()
        if not (self._kill_armed and self._kill_armed[0] == key
                and now - self._kill_armed[1] <= 4.0):
            self._kill_armed = (key, now)
            QTimer.singleShot(4100, self.update)
            return
        self._kill_armed = None
        group = next((g for g in self._procs if g["name"].lower() == key), None)
        if group is None or key in PROTECTED_PROCS:
            return
        ended, denied = 0, 0
        procs = []
        for pid in group["pids"]:
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                procs.append(proc)
                ended += 1
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                denied += 1
        if denied and not ended:
            note = "%s: access denied (runs as admin)" % group["name"]
        elif denied:
            note = "%s: ended %d, %d denied" % (group["name"], ended, denied)
        else:
            note = "Ended %s (%d)" % (group["name"], ended)
        self._kill_note = (note, now)
        log_line("PROCS tab: %s" % note)
        QTimer.singleShot(800, self.refresh_procs)
        QTimer.singleShot(5100, self.update)

    def _draw_procs(self, painter, y):
        left, right = self._left(), self._right()
        if psutil is None:
            self._text(painter, left, y + 13, "Install psutil to list processes",
                       qt_font(9), C_TEXT2)
            return y
        memory = psutil.virtual_memory()
        status = "RAM %s / %s GB used · top by memory" % (
            fmt_num(memory.used / 2 ** 30), fmt_num(memory.total / 2 ** 30))
        if self._kill_note and time.monotonic() - self._kill_note[1] < 5.0:
            status = self._kill_note[0]
        y = self._draw_secondary(painter, y, status)
        y += GAP - 2

        font = qt_font(9)
        metrics = QtGui.QFontMetrics(font)
        label_font = self._label_font()
        btn_font = qt_font(7, True)
        btn_w = QtGui.QFontMetrics(btn_font).horizontalAdvance("SURE?") + 14
        ram_right = right - btn_w - 8
        ram_w = metrics.horizontalAdvance("00.0 GB")
        self._text(painter, left, y + 10, "PROCESS", label_font, C_LABEL)
        self._text(painter, ram_right, y + 10, "RAM", label_font, C_LABEL, align_right=True)
        y += LABEL_H + 2

        row_h = LOG_ROW_H + 2
        bottom = self.height() - EDGE_PAD + 4
        rows = self._procs[:max(0, int((bottom - y) // row_h))]
        if not rows:
            self._text(painter, left, y + 13, "Reading processes…", font, C_TEXT2)
            return y + row_h
        armed = self._kill_armed[0] if (self._kill_armed and
                                        time.monotonic() - self._kill_armed[1] <= 4.0) else None
        for index, group in enumerate(rows):
            if index % 2 == 0:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 10))
                painter.drawRoundedRect(QRectF(left - 4, y + 1, right - left + 8,
                                               row_h - 2), 3, 3)
            gb = group["rss"] / 2 ** 30
            ram_text = ("%.1f GB" % gb) if gb >= 1 else ("%d MB" % (group["rss"] // 2 ** 20))
            color = level_color(gb * 1024, 1024.0, 3072.0)   # amber >1 GB, red >3 GB
            self._text(painter, ram_right, y + 14, ram_text, font, color, align_right=True)
            name = group["name"]
            if name.lower().endswith(".exe"):
                name = name[:-4]
            if group["count"] > 1:
                name = "%s  ×%d" % (name, group["count"])
            self._text(painter, left, y + 14, name, font, C_VALUE,
                       max_width=ram_right - ram_w - left - 6)
            key = group["name"].lower()
            if key in PROTECTED_PROCS:
                y += row_h                 # Windows core process: no END button
                continue
            is_armed = armed == key
            rect = QRectF(right - btn_w, y + 2, btn_w, row_h - 4)
            tint = QColor("#EF4444")
            painter.setPen(QPen(QColor(tint.red(), tint.green(), tint.blue(),
                                       230 if is_armed else 110), 1))
            fill = QColor(tint)
            fill.setAlpha(110 if is_armed else 0)
            painter.setBrush(fill if is_armed else QColor(255, 255, 255, 12))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            label = "SURE?" if is_armed else "END"
            bm = QtGui.QFontMetrics(btn_font)
            self._text(painter, rect.center().x() - bm.horizontalAdvance(label) / 2,
                       rect.top() + (rect.height() + bm.ascent() - bm.descent()) / 2,
                       label, btn_font, "#FFFFFF" if is_armed else "#FCA5A5")
            self._zones.append((rect, "kill:" + key))
            y += row_h
        return y

    def toggle_recording(self, enabled=None):
        enabled = (not self.config.get("log_recording")) if enabled is None else bool(enabled)
        self.config["log_recording"] = enabled
        if enabled:
            self.config["log_last"] = 0.0      # first row straight away
            save_config(self.config)
            self.log_tick()
        else:
            save_config(self.config)
        log_line("stats recording %s" % ("started" if enabled else "stopped"))
        self.update()

    def record_now(self):
        if not self._snapshot:
            return False
        row = append_stats_log(self._snapshot)
        if row is None:
            return False
        self._log_rows.append(row)
        del self._log_rows[:-200]
        self.config["log_last"] = time.time()
        save_config(self.config)
        self.update()
        return True

    def log_tick(self):
        """Called every 15 s: save a row when 30 minutes have passed."""
        if not self.config.get("log_recording") or not self._snapshot:
            return
        if time.time() - float(self.config.get("log_last") or 0.0) >= LOG_INTERVAL_S - 5:
            self.record_now()

    # -- drag on the desktop (DesktopDragHook) ---------------------------------
    def desktop_hook_allowed(self):
        """Pinned on the wallpaper: the hook delivers clicks (and drags)."""
        return IS_WINDOWS and self._pinned and not move_mode(self.config)

    def desktop_drag_allowed(self):
        return (self.desktop_hook_allowed()
                and bool(self.config.get("drag_on_desktop", True)))

    def drag_to_physical(self, screen_x, screen_y):
        """Move the pinned card so its top-left is at this physical screen point."""
        vx, vy = self.virtual_origin()
        dpr = self.devicePixelRatio()
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        width, height = int(self._w * dpr), int(self._height * dpr)
        screen_x = max(vx, min(vx + vw - width, int(screen_x)))
        screen_y = max(vy, min(vy + vh - height, int(screen_y)))
        pos = (int(round(screen_x / dpr)), int(round(screen_y / dpr)))
        if pos != self._pos:
            self._pos = pos
            self.place_pinned(force=True)

    def finish_drag(self):
        self.config["x"], self.config["y"] = self._pos
        save_config(self.config)
        log_line("card dragged to %s" % (self._pos,))
        self._blurred = None
        self.refresh_blur(force=True)
        self.update()

    # -- snapshots -------------------------------------------------------------
    def on_snapshot(self, snapshot):
        had_footer = bool(self._snapshot.get("hints"))
        had_gpu = self._show_gpu()
        if not self._snapshot:
            log_line("first metrics snapshot received")
        self._snapshot = snapshot
        for key, history in (("cpu_load", self._cpu_history),
                             ("gpu_load", self._gpu_history)):
            if snapshot.get(key) is not None:
                history.append(float(snapshot[key]))
                del history[:-60]
        for row in snapshot.get("disks") or []:
            if row.get("read_mbps") is None and row.get("write_mbps") is None:
                continue
            history = self._disk_history.setdefault(row.get("mount"), [])
            history.append((float(row.get("read_mbps") or 0.0),
                            float(row.get("write_mbps") or 0.0)))
            del history[:-60]
        disk_count = len(snapshot.get("disks") or [])
        if getattr(self, "_disk_alert", None) and snapshot.get("disks"):
            self._disk_alert.configure(self.config.get("disk_alert_enabled"),
                                       self.config.get("disk_alert_gb"))
            self._disk_alert.check(snapshot.get("disks") or [],
                                   tray_icon=getattr(self, "tray", None))
        footer_changed = bool(snapshot.get("hints")) != had_footer
        if (disk_count != self._disk_count) or footer_changed or self._show_gpu() != had_gpu:
            self._disk_count = disk_count
            self._min_h = self._compute_height()
            self.apply_size()
        self.update()

    # -- Settings window -------------------------------------------------------
    def open_settings(self):
        """Show the Settings window (one at a time) and apply what it returns."""
        if self._settings_open:
            return
        self._settings_open = True
        try:
            import settings_dialog
            dialog = settings_dialog.SettingsDialog(
                self.config, self._snapshot.get("all_drives") or detect_fixed_drives(),
                read_bg_file())
            if dialog.exec() == QtWidgets.QDialog.Accepted:
                self.apply_settings(dialog.config_updates(), dialog.background_updates())
        except Exception as exc:
            log_line("settings window failed: %s" % exc)
        finally:
            self._settings_open = False

    def apply_settings(self, config_updates, background_updates):
        """Store new settings, push them to the worker, redraw."""
        self.config.update(config_updates)
        if background_updates:
            save_custom_bg_settings(background_updates)
            if "tint_opacity" in background_updates:
                self.config["opacity"] = background_updates["tint_opacity"]
        save_config(self.config)
        if self.worker is not None:
            self.worker.configure(self.config)
        self._min_h = self._compute_height()
        self.apply_size()
        self.refresh_blur(force=True)
        self.update()

    def clock_tick(self):
        minute = datetime.datetime.now().strftime("%H:%M")
        if minute != self._last_minute:
            self._last_minute = minute
            self.update()


class DesktopDragHook:
    """Click-and-drag the pinned card from anywhere on it (Windows).

    The pinned card sits under the desktop icon layer, so the icon list view
    gets every click and the card never sees one. A low-level mouse hook
    watches the left button instead: a press inside the card, where nothing
    but the desktop is on top, starts a drag (the press is swallowed so the
    desktop does not start a selection box); moves follow the cursor; the
    release saves the new spot. Presses anywhere else pass through untouched.
    """

    WH_MOUSE_LL = 14
    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long),
                    ("mouseData", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                    ("time", ctypes.c_uint32), ("extra", ctypes.c_size_t)]

    def __init__(self, widget):
        self.widget = widget
        self._hook = None
        self._grab = None          # (dx, dy) physical offset of the cursor in the card
        self._press = None         # physical press point
        self._moved = False        # moved > 4 px since the press = a drag, not a click
        self._zone = None          # button under the press (tabs, record, open file)
        self._rs = None            # edge resize: (edges, original physical rect)
        self._rdisk = None         # disk row under a right-button press
        self._failed = False
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                             ctypes.c_size_t, ctypes.c_ssize_t)
        self._u.SetWindowsHookExW.argtypes = [ctypes.c_int, self._proc_type,
                                              ctypes.c_void_p, ctypes.c_uint32]
        self._u.SetWindowsHookExW.restype = ctypes.c_void_p
        self._u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                           ctypes.c_size_t, ctypes.c_ssize_t]
        self._u.CallNextHookEx.restype = ctypes.c_ssize_t
        self._u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        self._u.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
        self._u.WindowFromPoint.restype = ctypes.c_void_p
        self._u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self._u.GetAncestor.restype = ctypes.c_void_p
        self._u.GetWindowRect.argtypes = [ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.wintypes.RECT)]
        self._callback = self._proc_type(self._proc)   # keep a reference

    def start(self):
        """Arm the hook only while the pointer is over the visible card.

        A system-wide mouse hook in Python would sit in front of every mouse
        event (games included) and Windows silently drops hooks that answer
        slowly. So a 10 Hz cursor check installs it on hover and removes it
        when the pointer leaves; while a game or any window covers the card,
        no hook exists at all.
        """
        if not IS_WINDOWS:
            return
        self._u.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
        self._timer = QTimer()
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        log_line("desktop drag enabled (hook armed on hover)")

    def _tick(self):
        if self._grab is not None or self._rs is not None:
            return
        point = ctypes.wintypes.POINT()
        over = (self.widget.desktop_hook_allowed()
                and self._u.GetCursorPos(ctypes.byref(point))
                and self._hit(point.x, point.y))
        if over and not self._hook:
            self.install()
        elif not over and self._hook:
            self.uninstall()

    def install(self):
        if not IS_WINDOWS or self._hook:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p   # 64-bit handle
        module = kernel32.GetModuleHandleW(None)
        self._hook = self._u.SetWindowsHookExW(self.WH_MOUSE_LL, self._callback,
                                               module, 0)
        if not self._hook and not self._failed:
            self._failed = True
            log_line("desktop drag hook failed (error %d)" % ctypes.get_last_error())

    def uninstall(self):
        if self._hook:
            self._u.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def stop(self):
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        self.uninstall()

    def _card_rect(self):
        rect = ctypes.wintypes.RECT()
        if not self._u.GetWindowRect(int(self.widget.winId()), ctypes.byref(rect)):
            return None
        return rect

    def _hit(self, x, y):
        """True when (x, y) is on the card and only the desktop is above it."""
        rect = self._card_rect()
        if rect is None or not (rect.left <= x < rect.right and rect.top <= y < rect.bottom):
            return False
        card = int(self.widget.winId())
        under = self._u.WindowFromPoint(ctypes.wintypes.POINT(x, y)) or 0
        if under == card:
            return True
        desktop_root = self._u.GetAncestor(card, GA_ROOT) or 0
        return bool(under) and (self._u.GetAncestor(under, GA_ROOT) or 0) == desktop_root

    def _proc(self, code, wparam, lparam):
        try:
            if code == 0 and self.widget.desktop_hook_allowed():
                info = self.MSLLHOOKSTRUCT.from_address(lparam)
                if wparam == self.WM_LBUTTONDOWN and DEBUG_LOG:
                    log_line("drag hook: left press at %d,%d hit=%s"
                             % (info.x, info.y, self._hit(info.x, info.y)))
                if wparam == self.WM_RBUTTONDOWN and self._hit(info.x, info.y):
                    rect = self._card_rect()
                    dpr = self.widget.devicePixelRatio() or 1.0
                    mount = self.widget.disk_at((info.x - rect.left) / dpr,
                                                (info.y - rect.top) / dpr)
                    if mount:
                        self._rdisk = mount
                        return 1
                if wparam == self.WM_RBUTTONUP and self._rdisk:
                    mount, self._rdisk = self._rdisk, None
                    QTimer.singleShot(0, lambda: self.widget.show_disk_menu(mount))
                    return 1
                if wparam == self.WM_LBUTTONDOWN and self._hit(info.x, info.y):
                    rect = self._card_rect()
                    dpr = self.widget.devicePixelRatio() or 1.0
                    edges = self.widget.edges_at(info.x - rect.left, info.y - rect.top,
                                                 rect.right - rect.left,
                                                 rect.bottom - rect.top)
                    if edges:
                        self._rs = (edges, (rect.left, rect.top, rect.right, rect.bottom))
                        self._press = (info.x, info.y)
                        return 1
                    zone = self.widget.zone_at((info.x - rect.left) / dpr,
                                               (info.y - rect.top) / dpr)
                    if zone or self.widget.desktop_drag_allowed():
                        self._grab = (info.x - rect.left, info.y - rect.top)
                        self._press = (info.x, info.y)
                        self._moved = False
                        self._zone = zone
                        return 1
                if self._rs is not None:
                    if wparam == self.WM_MOUSEMOVE:
                        edges, (left, top, right, bottom) = self._rs
                        dx, dy = info.x - self._press[0], info.y - self._press[1]
                        if "L" in edges:
                            left += dx
                        if "R" in edges:
                            right += dx
                        if "T" in edges:
                            top += dy
                        if "B" in edges:
                            bottom += dy
                        self.widget.queue_resize(left, top, right, bottom, edges)
                    elif wparam == self.WM_LBUTTONUP:
                        self._rs = None
                        QTimer.singleShot(0, self.widget.end_resize)
                        return 1
                elif self._grab is not None:
                    if wparam == self.WM_MOUSEMOVE:
                        if not self._moved and (abs(info.x - self._press[0])
                                                + abs(info.y - self._press[1])) > 4:
                            self._moved = True
                        if self._moved and self.widget.desktop_drag_allowed():
                            self.widget.drag_to_physical(info.x - self._grab[0],
                                                         info.y - self._grab[1])
                    elif wparam == self.WM_LBUTTONUP:
                        self._grab = None
                        # Work happens outside the hook: hooks must return fast.
                        if self._moved:
                            if self.widget.desktop_drag_allowed():
                                QTimer.singleShot(0, self.widget.finish_drag)
                        elif self._zone:
                            zone = self._zone
                            QTimer.singleShot(0, lambda: self.widget.handle_zone(zone))
                        return 1
            elif self._grab is not None or self._rs is not None:
                if self._rs is not None:
                    QTimer.singleShot(0, self.widget.end_resize)
                self._grab = None
                self._rs = None
        except Exception as exc:
            if not self._failed:
                self._failed = True
                log_line("desktop drag hook error: %s" % exc)
        return self._u.CallNextHookEx(None, code, wparam, lparam)


def make_tray_icon():
    """Draw a 32x32 tray glyph with QPainter - no image files."""
    image = QtGui.QImage(32, 32, QtGui.QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setBrush(QColor("#101218"))
    painter.setPen(QPen(QColor("#22D3EE"), 1.5))
    painter.drawRoundedRect(QRectF(2, 2, 28, 28), 6, 6)
    painter.setPen(QPen(QColor("#22D3EE"), 2))
    points = [(6, 20), (10, 12), (14, 22), (18, 9), (22, 17), (26, 13)]
    path = QPainterPath()
    path.moveTo(points[0][0], points[0][1])
    for point in points[1:]:
        path.lineTo(point[0], point[1])
    painter.drawPath(path)
    painter.setPen(QPen(QColor("#34D399"), 2))
    painter.drawLine(6, 26, 26, 26)
    painter.end()
    return QtGui.QPixmap.fromImage(image)


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command() -> str:
    """Command line stored in the Run value: the exe when frozen, else pythonw + script."""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return '"%s" "%s"' % (pythonw, os.path.abspath(__file__))


def autostart_file_path() -> Path:
    """Freedesktop autostart entry: $XDG_CONFIG_HOME/autostart/desktopwidgets-desktop.desktop."""
    return config_home() / "autostart" / AUTOSTART_FILE_NAME


def desktop_entry_quote(argument: str) -> str:
    """Quote one argument for the Exec= line of a .desktop file."""
    escaped = (argument.replace("\\", "\\\\").replace('"', '\\"')
               .replace("`", "\\`").replace("$", "\\$"))
    return '"%s"' % escaped


def linux_exec_line() -> str:
    """Absolute command of the running app: the frozen binary, else interpreter + script."""
    if getattr(sys, "frozen", False):
        parts = [sys.executable]
    else:
        parts = [sys.executable, os.path.abspath(__file__)]
    return " ".join(desktop_entry_quote(part) for part in parts)


def autostart_entry_text() -> str:
    """Contents of the autostart .desktop file."""
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Desktop Monitor",
        "Comment=Desktop Monitor card (CPU, GPU, RAM, disks)",
        "Exec=" + linux_exec_line(),
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "",
    ])


def set_autostart_linux(enabled) -> bool:
    """Write or remove the Freedesktop autostart entry."""
    target = autostart_file_path()
    try:
        if enabled:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(autostart_entry_text(), encoding="utf-8")
            os.replace(tmp, target)
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        log_line("autostart %s: %s" % ("enabled" if enabled else "removed", target.name))
        return True
    except Exception as exc:
        log_line("autostart update failed: %s" % exc)
        return False


def set_autostart(enabled):
    """Write or remove the DesktopWidgets.DesktopMonitor value in the HKCU Run key
    (Windows) or the autostart .desktop file (Linux)."""
    if IS_LINUX:
        return set_autostart_linux(enabled)
    if not IS_WINDOWS or winreg is None:
        log_line("autostart is only supported on Windows")
        return False
    value = autostart_command()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, value)
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        log_line("autostart %s: %s" % ("enabled" if enabled else "removed", value))
        return True
    except Exception as exc:
        log_line("autostart update failed: %s" % exc)
        return False


def open_status_feed(config):
    """Open the file the user picked as the status feed, if there is one."""
    path = str(config.get("feed_path") or "").strip()
    try:
        if path and os.path.isfile(path):
            if IS_WINDOWS:
                os.startfile(path)
            elif IS_LINUX:
                subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        log_line("could not open the status feed file: %s" % exc)


def build_tray(widget):
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log_line("no system tray available; controls are unavailable")
        return None
    icon = QSystemTrayIcon(make_tray_icon())
    icon.setToolTip("Desktop Monitor")
    widget.tray = icon
    menu = QtWidgets.QMenu()

    settings_action = menu.addAction("Settings...")
    settings_action.triggered.connect(lambda: widget.open_settings())
    feed_action = menu.addAction("Open status feed")
    feed_action.triggered.connect(lambda: open_status_feed(widget.config))
    feed_action.setVisible(bool(str(widget.config.get("feed_path") or "").strip()))
    # Only offered while a feed file is set (re-checked every time the menu opens).
    menu.aboutToShow.connect(lambda: feed_action.setVisible(
        bool(str(widget.config.get("feed_path") or "").strip())))
    menu.addSeparator()

    move_action = menu.addAction("Move widget")
    move_action.triggered.connect(lambda: set_move_mode(widget, True))
    lock_action = menu.addAction("Lock position")
    lock_action.triggered.connect(lambda: set_move_mode(widget, False))
    menu.addSeparator()

    opacity_menu = menu.addMenu("Opacity / Dark Tint")
    for percent in (20, 35, 50, 65, 80):
        action = opacity_menu.addAction("%d%%" % percent)
        action.triggered.connect(lambda _a=None, p=percent: set_opacity(widget, p))

    bg_menu = menu.addMenu("Background & Blur")
    for label, radius in (
        ("No Blur (0px - Crisp)", 0.0),
        ("Light Blur (3px)", 3.0),
        ("Soft Blur (6px)", 6.0),
        ("Medium Blur (12px)", 12.0),
        ("Heavy Frosted Glass (22px)", 22.0),
    ):
        act_blur = bg_menu.addAction(label)
        act_blur.triggered.connect(lambda _a=None, r=radius: set_custom_bg_blur(widget, r))
    bg_menu.addSeparator()
    act_choose_bg = bg_menu.addAction("Choose Background Image...")
    act_choose_bg.triggered.connect(lambda: choose_custom_bg_image(widget))
    act_reset_bg = bg_menu.addAction("Reset to Desktop Wallpaper")
    act_reset_bg.triggered.connect(lambda: reset_custom_bg_image(widget))
    menu.addSeparator()

    record_action = menu.addAction("Record stats every 30 min")
    record_action.setCheckable(True)
    record_action.setChecked(bool(widget.config.get("log_recording")))
    record_action.toggled.connect(   # only act on a real change (aboutToShow syncs it)
        lambda on: bool(on) != bool(widget.config.get("log_recording"))
        and widget.toggle_recording(bool(on)))
    menu.aboutToShow.connect(
        lambda: record_action.setChecked(bool(widget.config.get("log_recording"))))
    log_action = menu.addAction("Open stats log")
    log_action.triggered.connect(open_stats_log)
    menu.addSeparator()

    drag_action = menu.addAction("Drag with mouse")
    drag_action.setCheckable(True)
    drag_action.setChecked(bool(widget.config.get("drag_on_desktop", True)))
    drag_action.toggled.connect(lambda on: toggle_flag(widget, "drag_on_desktop", bool(on)))

    io_action = menu.addAction("Show disk I/O")
    io_action.setCheckable(True)
    io_action.setChecked(bool(widget.config.get("show_disk_io")))
    io_action.toggled.connect(lambda on: toggle_flag(widget, "show_disk_io", bool(on)))

    start_action = menu.addAction("Start when I sign in" if IS_LINUX
                                  else "Start with Windows")
    start_action.setCheckable(True)
    start_action.setChecked(bool(widget.config.get("autostart")))
    start_action.toggled.connect(lambda on: set_autostart_flag(widget, bool(on)))
    menu.addSeparator()
    quit_action = menu.addAction("Quit")
    quit_action.triggered.connect(lambda: QtWidgets.QApplication.instance().quit())

    if hasattr(icon, "setMenu"):
        icon.setMenu(menu)
    else:
        # PySide6 6.12 renamed the tray menu setter.
        icon.setContextMenu(menu)
    icon.show()
    return icon


def set_move_mode(widget, enabled):
    """Move mode: the card is detached from the wallpaper and draggable."""
    widget.config["locked"] = not bool(enabled)
    if IS_LINUX:
        save_config(widget.config)
        widget.apply_linux_window_flags()
        widget.update()
        return
    if enabled:
        widget.unpin_from_desktop()
    else:
        save_config(widget.config)
        widget.pin_to_desktop()
    widget.apply_extended_style(int(widget.winId()))
    widget.refresh_blur(force=True)
    widget.update()


def set_opacity(widget, percent):
    widget.config["opacity"] = percent / 100.0
    save_config(widget.config)
    if widget._custom_bg is not None:
        save_custom_bg_settings({"tint_opacity": percent / 100.0})
        widget.refresh_blur(force=True)
    widget.update()


def set_custom_bg_blur(widget, radius: float):
    save_custom_bg_settings({"blur_radius": float(radius)})
    widget.refresh_blur(force=True)
    widget.update()


def choose_custom_bg_image(widget):
    import settings_dialog
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        None,
        "Choose Widget Background Image",
        settings_dialog.pictures_dir(),
        "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
    )
    if path and os.path.isfile(path):
        save_custom_bg_settings({"image_path": path})
        widget.refresh_blur(force=True)
        widget.update()


def reset_custom_bg_image(widget):
    save_custom_bg_settings({"image_path": ""})
    widget.refresh_blur(force=True)
    widget.update()


def toggle_flag(widget, key, value):
    widget.config[key] = value
    save_config(widget.config)
    if key == "show_disk_io":          # disk graphs add/remove height
        widget._min_h = widget._compute_height()
        widget.apply_size()
    widget.update()


def set_autostart_flag(widget, value):
    widget.config["autostart"] = bool(value)
    set_autostart(bool(value))
    save_config(widget.config)


def normalise_config(config):
    """Fill in missing keys and coerce every value to the type the code expects."""
    config.setdefault("x", None)
    config.setdefault("y", 24)
    config["opacity"] = opacity_fraction(config)
    config["show_disk_io"] = bool(config.get("show_disk_io", False))
    config["drag_on_desktop"] = bool(config.get("drag_on_desktop", True))
    config["locked"] = bool(config.get("locked", True))
    config["autostart"] = bool(config.get("autostart", False))
    config["tab"] = config.get("tab") if config.get("tab") in ("log", "procs") else "live"
    config["log_recording"] = bool(config.get("log_recording", False))
    try:
        config["log_last"] = float(config.get("log_last") or 0.0)
    except (TypeError, ValueError):
        config["log_last"] = 0.0
    # Settings window values
    config["feed_label"] = str(config.get("feed_label") or "FEED").strip()[:12] or "FEED"
    config["feed_path"] = str(config.get("feed_path") or "").strip()
    try:
        config["feed_minutes"] = max(1, min(1440, int(config.get("feed_minutes") or 10)))
    except (TypeError, ValueError):
        config["feed_minutes"] = 10
    hidden = config.get("hidden_drives")
    config["hidden_drives"] = [str(m) for m in hidden] if isinstance(hidden, list) else []
    config["disk_alert_enabled"] = bool(config.get("disk_alert_enabled", False))
    try:
        config["disk_alert_gb"] = max(0.5, float(config.get("disk_alert_gb") or 10.0))
    except (TypeError, ValueError):
        config["disk_alert_gb"] = 10.0
    return config


class LinuxInstanceLock:
    """Holds the flock on the lock file for as long as the app runs."""

    def __init__(self, handle) -> None:
        self._handle = handle

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass


def lock_file_path() -> Path:
    """$XDG_RUNTIME_DIR/desktopwidgets-desktop.lock, else the data folder."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / LOCK_FILE_NAME
    return APP_DIR / LOCK_FILE_NAME


def acquire_linux_lock(path=None):
    """Take the single-instance lock (fcntl.flock). None if another copy holds it."""
    import fcntl
    target = Path(path) if path is not None else lock_file_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = open(target, "a+")
    except OSError as exc:
        log_line("lock file %s could not be opened: %s" % (target.name, exc))
        return LinuxInstanceLock(None)        # cannot lock: run anyway
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return LinuxInstanceLock(handle)


def acquire_singleton():
    """Return a lock/server object if this is the only instance, else None."""
    if IS_LINUX:
        return acquire_linux_lock()
    name = SINGLE_INSTANCE_NAME
    socket_client = QLocalSocket()
    socket_client.connectToServer(name)
    if socket_client.waitForConnected(500):
        socket_client.write(b"duplicate launch")
        socket_client.flush()
        socket_client.close()
        return None
    server = QLocalServer()
    if not server.listen(name):
        QLocalServer.removeServer(name)
        if not server.listen(name):
            log_line("singleton server could not start: %s" % server.errorString())
            return None
    return server


def run_selftest():
    """No-GUI smoke test: pin probe, worker snapshot, and a rendered card PNG."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if IS_WINDOWS and os.environ["QT_QPA_PLATFORM"] == "offscreen":
        # The offscreen platform loads no system fonts on Windows, so every
        # glyph would render as a box in the test PNG.
        os.environ.setdefault("QT_QPA_FONTDIR",
                              os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    setup_logging()
    print("config: %s" % CONFIG_PATH)
    print("machine: %s" % (platform.node() or socket.gethostname()))
    if IS_WINDOWS:
        progman = user32.FindWindowW("Progman", None)
        workerw = find_workerw()
        print("Progman hwnd: %s   WorkerW hwnd: %s" % (progman, workerw))
    config = load_config()
    normalise_config(config)
    # Demo status feed (in memory only): the log file is always fresh, so the dot is green.
    config["feed_path"] = str(LOG_PATH)
    config["feed_label"] = "FEED"
    worker = MetricsWorker(1000)
    worker.configure(config)
    worker.start()
    snapshot = {}
    for _ in range(3):
        snapshot = worker.poll()
        time.sleep(0.6)  # disk I/O rates need > 0.5 s between ticks
    worker.stop()
    shutdown_nvml()
    print(json.dumps({k: v for k, v in snapshot.items() if k != "time"}, indent=2))
    app = QtWidgets.QApplication(sys.argv)
    widget = MonitorWidget(config)
    widget._snapshot = snapshot
    widget._disk_count = len(snapshot.get("disks") or [])
    # Sample history so the sparklines have something to draw in the PNG.
    widget._cpu_history = [(snapshot.get("cpu_load") or 0.0) * (0.6 + 0.4 * ((i * 7) % 5) / 4.0)
                           for i in range(40)]
    widget._gpu_history = [(snapshot.get("gpu_load") or 0.0) * (0.5 + 0.5 * ((i * 3) % 4) / 3.0)
                           for i in range(40)]
    widget._disk_history = {
        row.get("mount"): [(30.0 * ((i * 5) % 7) / 6.0, 900.0 if 25 <= i <= 34 else 2.0 * (i % 3))
                           for i in range(40)]
        for row in snapshot.get("disks") or []}
    widget.config["show_disk_io"] = True   # in memory only, never saved
    widget._min_h = widget._compute_height()
    widget.apply_size()
    widget.show()
    app.processEvents()
    out = os.path.join(os.path.dirname(CONFIG_PATH), "selftest_card.png")
    pixmap = widget.grab()
    if pixmap.isNull():
        print("selftest: grab() returned an empty pixmap")
        return 1
    pixmap.save(out)
    print("rendered card saved to %s (%dx%d px)" % (out, pixmap.width(), pixmap.height()))
    widget.config["tab"] = "log"          # in memory only, never saved
    widget.update()
    app.processEvents()
    out_log = os.path.join(os.path.dirname(CONFIG_PATH), "selftest_card_log.png")
    widget.grab().save(out_log)
    print("LOG tab saved to %s (%d rows in %s)" % (out_log, len(widget._log_rows),
                                                  STATS_LOG_PATH))
    widget.config["tab"] = "procs"        # in memory only, never saved
    widget.refresh_procs()
    app.processEvents()
    out_procs = os.path.join(os.path.dirname(CONFIG_PATH), "selftest_card_procs.png")
    widget.grab().save(out_procs)
    print("PROCS tab saved to %s (%d process groups)" % (out_procs, len(widget._procs)))
    # Narrowest allowed width: every tab must still lay out without overlap.
    widget._w = MIN_W
    widget.apply_size()
    for tab in ("log", "procs"):
        widget.config["tab"] = tab
        widget.update()
        app.processEvents()
        path = os.path.join(os.path.dirname(CONFIG_PATH), "selftest_card_%s_min.png" % tab)
        widget.grab().save(path)
        print("%s tab at %d px saved to %s" % (tab.upper(), MIN_W, path))
    return 0


def main():
    if "--selftest" in sys.argv:
        return run_selftest()
    setup_logging()
    server = acquire_singleton()
    if server is None:
        print("Desktop Monitor is already running")
        return 0
    set_process_dpi_awareness()
    app = QtWidgets.QApplication(sys.argv)
    config = load_config()
    normalise_config(config)

    widget = MonitorWidget(config)
    worker = MetricsWorker(2000)
    worker.configure(config)
    widget.worker = worker
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.start)
    worker.snapshot.connect(widget.on_snapshot)
    worker.stop_requested.connect(worker.stop)
    thread.start()

    widget.show()
    widget.pin_to_desktop()
    if "--settings" in sys.argv:
        # Desktops without a tray: open Settings straight away.
        QTimer.singleShot(400, widget.open_settings)

    tray = build_tray(widget)
    drag_hook = DesktopDragHook(widget) if IS_WINDOWS else None
    if drag_hook is not None:
        drag_hook.start()

    heartbeat = QTimer(app)
    heartbeat.setInterval(5000)
    heartbeat.timeout.connect(widget.repin_if_needed)
    heartbeat.start()

    clock = QTimer(app)
    clock.setInterval(1000)
    clock.timeout.connect(widget.clock_tick)
    clock.start()

    stats_log = QTimer(app)          # LOG tab: saves a row every 30 min while recording
    stats_log.setInterval(15000)
    stats_log.timeout.connect(widget.log_tick)
    stats_log.start()
    widget.sync_procs_timer()        # PROCS tab polls only while it is open

    app.exec()
    if drag_hook is not None:
        drag_hook.stop()
    worker.stop_requested.emit()
    thread.quit()
    if not thread.wait(3000):
        log_line("worker thread did not stop within 3 s")
    shutdown_nvml()
    if tray is not None:
        tray.hide()
    server.close()
    log_line("clean shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
