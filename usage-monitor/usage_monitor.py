"""usage_monitor.py: AI usage and credits desktop widget for Windows and Linux.

What it does
------------
A compact, dark, frosted-glass desktop card pinned above the wallpaper (WorkerW)
and below normal windows, matching Desktop Monitor and the To-Do Widget. It shows
usage limits (progress bar, percent used, time until reset) and credit balances
for the providers the user adds in the Settings window (tray menu "Settings...",
right-click menu on Linux, or the gear on the card). A first run has no
providers and shows an "Add a provider" button.

On Linux the card is a normal frameless window (no WorkerW pinning), the tray
icon is optional (GNOME may not have one: the gear and the right-click menu
still work) and "Start when I sign in" writes an autostart ``.desktop`` file.

Tabs
----
1. ALL: one section per provider, one row per limit the provider reports.
2. FAVOURITES: only starred rows, in the user's own order.
3. CREDITS: money balances and budgets.

Run
---
    python usage_monitor.py             # normal launch
    pythonw usage_monitor.py            # background launch without console
    python usage_monitor.py --selftest  # fetch rows, print table, save PNG
    python usage_monitor.py --settings  # launch and open the Settings window
"""

from __future__ import annotations

import ctypes
import datetime
import io
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, QThread, QTimer, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QSystemTrayIcon
except ImportError:
    sys.stderr.write("PySide6 is required: pip install PySide6\n")
    raise SystemExit(1)

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    Image = None
    ImageDraw = None
    ImageFilter = None

# Windows-only modules: imported only on Windows so Linux never touches them.
if sys.platform == "win32":
    import ctypes.wintypes

    try:
        import winreg
    except ImportError:
        winreg = None
else:
    winreg = None

# Make the flat local imports (cache, models, providers...) work from any folder.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import provider_config
import secrets_store
from cache import UsageCache
from models import (
    CreditRow,
    UsageRow,
    build_credit_rows,
    format_reset_countdown,
)
from providers import registry

IS_WINDOWS = sys.platform.startswith("win")
if IS_WINDOWS:
    user32 = ctypes.windll.user32
else:
    user32 = None

# ---------------------------------------------------------------- palette & metrics
C_CARD = "#101218"
C_OVERLAY = "#0B0D12"
C_ACCENT = "#22D3EE"
C_EMERALD = "#34D399"
C_AMBER = "#F59E0B"
C_RED = "#EF4444"
C_LABEL = "#8A93A6"
C_VALUE = "#E8ECF2"
C_TEXT2 = "#AEB8CC"

GLOW = 7
EDGE_PAD = GLOW + 15
CORNER_RADIUS = 14
CARD_WIDTH = 320
MIN_W = 280
MAX_W = 640
MIN_H = 380
DEFAULT_H = 448
MAX_H = 1100
RESIZE_BAND = 12

TAB_H = 22
HEADER_H = 18
SECTION_H = 16
MODEL_ROW_H = 27
CREDIT_CARD_H = 68
GAP = 8

SINGLE_INSTANCE_KEY = "DesktopWidgets.UsageMonitor"
RUN_VALUE_NAME = "DesktopWidgets.UsageMonitor"
# Linux names (see docs/LINUX_PORT.md): autostart entry and single-instance lock.
AUTOSTART_FILE_NAME = "desktopwidgets-usage.desktop"
LOCK_FILE_NAME = "desktopwidgets-usage.lock"
LAUNCHER_NAME = "desktopwidgets-usage"
LOG_MAX_BYTES = 512 * 1024


def appdata_root() -> Path:
    """Folder that holds ``DesktopWidgets``: %APPDATA% on Windows, $XDG_CONFIG_HOME on Linux."""
    return provider_config.config_root()


def app_dir() -> Path:
    """Return <config root>/DesktopWidgets/UsageMonitor, creating it on demand."""
    return provider_config.data_dir()


APP_DIR = app_dir()
# Window position, size, tab and favourites. Provider choices live in config.json,
# which belongs to provider_config / the Settings window, so the two never clash.
STATE_PATH = APP_DIR / "widget_state.json"
LOG_PATH = APP_DIR / "usage_monitor.log"
BG_CONFIG_PATH = appdata_root() / "DesktopWidgets" / "widget_background.json"
# Read-only fallback: the shared background file of earlier widget versions.
LEGACY_BG_CONFIG_PATH = appdata_root() / "TomTodoList" / "widget_background.json"


def background_config_path() -> Path:
    """The shared background file (Windows: or the legacy one when only that exists).

    Linux has no legacy file, so it only ever uses ``widget_background.json``.
    """
    if not IS_WINDOWS:
        return BG_CONFIG_PATH
    if BG_CONFIG_PATH.is_file() or not LEGACY_BG_CONFIG_PATH.is_file():
        return BG_CONFIG_PATH
    return LEGACY_BG_CONFIG_PATH


def log_line(message: str) -> None:
    """Append one timestamped diagnostic line to usage_monitor.log.

    The log rotates at about 512 KB (one old copy is kept). The text is run
    through ``secrets_store.redact`` first, so a key can never reach the log
    file, not even when a caller passes one by accident.
    """
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        safe = secrets_store.redact(str(message))
    except Exception:  # noqa: BLE001 - a broken redaction must not leak the text
        safe = "<the message could not be written>"
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH.with_suffix(".log.1"))
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {safe}\n")
    except Exception:
        pass


def bar_color(percent_used: float | None) -> QColor:
    """Return bar colour by percent used: emerald <60, amber 60..85, red >85."""
    if percent_used is None:
        return QColor(C_LABEL)
    if percent_used < 60.0:
        return QColor(C_EMERALD)
    if percent_used <= 85.0:
        return QColor(C_AMBER)
    return QColor(C_RED)


def font_family_available(family: str) -> bool:
    """True when Qt can really find ``family`` (a clean machine or the offscreen
    platform may lack it; asking for a missing family can draw empty boxes)."""
    from PySide6.QtGui import QFontDatabase

    return family in QFontDatabase.families()


def qt_font(size_pt: int, bold: bool = False, letter_spacing: float = 0.0) -> QFont:
    """Return Segoe UI on Windows (matches the other widgets), else the system font."""
    font = QFont("Segoe UI") if IS_WINDOWS else QFont()
    font.setPointSize(size_pt)
    font.setBold(bold)
    if letter_spacing:
        font.setLetterSpacing(QFont.AbsoluteSpacing, letter_spacing)
    return font


def draw_gear_icon(painter: QPainter, center: QPointF, radius: float, color: QColor) -> None:
    """Paint a small settings gear (a ring with eight teeth) centred on ``center``.

    Used where there is no icon font (Linux). It is a painted path, so it looks
    the same on every computer.
    """
    body = QPainterPath()
    body.addEllipse(QPointF(0.0, 0.0), radius * 0.72, radius * 0.72)
    tooth_w = radius * 0.4
    for index in range(8):
        tooth = QPainterPath()
        tooth.addRoundedRect(QRectF(-tooth_w / 2, -radius, tooth_w, radius * 0.55), 0.6, 0.6)
        turn = QtGui.QTransform()
        turn.rotate(index * 45.0)
        body = body.united(turn.map(tooth))
    hole = QPainterPath()
    hole.addEllipse(QPointF(0.0, 0.0), radius * 0.3, radius * 0.3)
    gear = body.subtracted(hole)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.translate(center)
    painter.drawPath(gear)
    painter.restore()


# ---------------------------------------------------------------- configuration
DEFAULT_CONFIG: dict = {
    "x": None,
    "y": None,
    "w": CARD_WIDTH,
    "h": DEFAULT_H,
    "opacity": 75,  # 60, 75, or 90 (%)
    "locked": True,
    "autostart": False,
    "drag_on_desktop": True,
    "tab": None,  # None = auto (FAVOURITES if favourites non-empty, else ALL)
    "favourites": [],  # list of "provider_id:model_id" strings in the user's order
}


def compute_non_overlapping_default_pos(
    width: int = CARD_WIDTH,
    height: int = DEFAULT_H,
) -> tuple[int, int]:
    """Place the card beside the To-Do Widget and Desktop Monitor if they are saved.

    Lines the widgets up side by side at the same Y coordinate without
    overlapping, staying on the screen the other widgets are on.
    """
    suite = appdata_root() / "DesktopWidgets"
    todo_cfg_path = suite / "TodoWidget" / "widget_config.json"
    dm_cfg_path = suite / "DesktopMonitor" / "config.json"

    occupied: list[tuple[int, int, int, int]] = []
    anchor_y: int | None = None

    for path, default_w, default_h in (
        (todo_cfg_path, 320, 448),
        (dm_cfg_path, 320, 448),
    ):
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(raw, dict) and raw.get("x") is not None and raw.get("y") is not None:
                    ox = int(raw["x"])
                    oy = int(raw["y"])
                    ow = int(raw.get("w") or default_w)
                    oh = int(raw.get("h") or default_h)
                    occupied.append((ox, oy, ow, oh))
                    if anchor_y is None:
                        anchor_y = oy
            except Exception:
                pass

    screen = QtWidgets.QApplication.primaryScreen() if QtWidgets.QApplication.instance() else None
    if occupied and anchor_y is not None:
        min_x = min(r[0] for r in occupied)
        max_right = max(r[0] + r[2] for r in occupied)
        candidate_left = min_x - width
        if screen is not None:
            near = QtGui.QGuiApplication.screenAt(QPoint(min_x, anchor_y)) or screen
            if candidate_left >= near.availableGeometry().left():
                return candidate_left, anchor_y
        return max_right, anchor_y

    if screen is not None:
        geo = screen.availableGeometry()
        return geo.right() - width - 24, geo.top() + 24
    return 24, 24


def load_config() -> dict:
    """Load the card's own state (widget_state.json) or initialise defaults."""
    cfg = dict(DEFAULT_CONFIG)
    if STATE_PATH.exists():
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                for k in DEFAULT_CONFIG:
                    if k in raw:
                        cfg[k] = raw[k]
        except Exception as exc:
            log_line(f"config load failed: {exc}")

    if cfg.get("x") is None or cfg.get("y") is None:
        dx, dy = compute_non_overlapping_default_pos(
            int(cfg.get("w") or CARD_WIDTH),
            int(cfg.get("h") or MIN_H),
        )
        cfg["x"] = dx
        cfg["y"] = dy

    if not isinstance(cfg.get("favourites"), list):
        cfg["favourites"] = []

    return cfg


def save_config(config: dict) -> None:
    """Atomically write the card's state to widget_state.json."""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except Exception as exc:
        log_line(f"config save failed: {exc}")


def move_mode(config: dict) -> bool:
    """True when the card is unlocked for top-level dragging."""
    return not bool(config.get("locked", True))


def opacity_fraction(config: dict) -> float:
    """Convert opacity setting (60, 75, 90) into a 0.0..1.0 alpha fraction."""
    raw = config.get("opacity", 75)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.75
    if val > 1.0:
        val /= 100.0
    return min(0.95, max(0.25, val))


# ---------------------------------------------------------------- Win32 plumbing
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

GA_PARENT = 1
GA_ROOT = 2


def set_process_dpi_awareness() -> None:
    """Enable per-monitor DPI awareness v2 before creating QApplication."""
    if not IS_WINDOWS:
        return
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def get_window_long(hwnd: int, index: int) -> int:
    try:
        return int(user32.GetWindowLongW(hwnd, index)) & 0xFFFFFFFF
    except Exception:
        return 0


def set_window_long(hwnd: int, index: int, value: int) -> bool:
    try:
        return int(user32.SetWindowLongW(hwnd, index, value & 0xFFFFFFFF)) != 0
    except Exception:
        return False


def modify_style(hwnd: int, add: int, remove: int, extended: bool = False) -> None:
    index = GWL_EXSTYLE if extended else GWL_STYLE
    current = get_window_long(hwnd, index)
    set_window_long(hwnd, index, (current & ~remove) | add)
    user32.SetWindowPos(
        hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
    )


def is_window(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(user32.IsWindow(hwnd))
    except Exception:
        return False


def get_parent(hwnd: int) -> int:
    if not IS_WINDOWS or not hwnd:
        return 0
    try:
        parent = int(user32.GetAncestor(hwnd, GA_PARENT) or 0)
        if not parent:
            parent = int(user32.GetParent(hwnd) or 0)
        return parent
    except Exception:
        return 0


def find_workerw() -> int:
    """Find the desktop WorkerW window behind the desktop icons."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return 0
    result = ctypes.wintypes.DWORD(0)
    user32.SendMessageTimeoutW(
        progman, 0x052C, 0xD, 0x1, SMTO_NORMAL, 1000, ctypes.byref(result)
    )
    child = user32.FindWindowExW(progman, None, "WorkerW", None)
    if child:
        return int(child)

    found = [None]

    def _enum(hwnd, _data):
        defview = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if defview:
            sibling = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if sibling:
                found[0] = int(sibling)
        return True

    try:
        cb = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )(_enum)
        user32.EnumWindows(cb, 0)
    except Exception:
        pass
    return int(found[0] or progman)


def pin_to_desktop(hwnd: int) -> bool:
    """Attach the widget window to the desktop WorkerW layer."""
    if not IS_WINDOWS or not is_window(hwnd):
        return False
    target = find_workerw()
    if not target:
        return False
    try:
        if not user32.SetParent(hwnd, target):
            return False
    except Exception:
        return False
    modify_style(hwnd, WS_CHILD, WS_POPUP | WS_CAPTION, extended=False)
    modify_style(hwnd, WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, 0, extended=True)
    user32.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )
    return True


def unpin_from_desktop(hwnd: int) -> bool:
    """Detach the widget from WorkerW for top-level dragging."""
    if not IS_WINDOWS or not is_window(hwnd):
        return False
    try:
        user32.SetParent(hwnd, None)
    except Exception:
        return False
    modify_style(hwnd, WS_POPUP, WS_CHILD, extended=False)
    modify_style(hwnd, 0, WS_EX_TRANSPARENT, extended=True)
    user32.SetWindowPos(
        hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED
    )
    return True


def child_rect(hwnd: int):
    if not IS_WINDOWS or not is_window(hwnd):
        return None
    try:
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        parent = get_parent(hwnd)
        origin = ctypes.wintypes.POINT(0, 0)
        if parent and not user32.ClientToScreen(parent, ctypes.byref(origin)):
            return None
        return (
            rect.left - origin.x,
            rect.top - origin.y,
            rect.right - rect.left,
            rect.bottom - rect.top,
        )
    except Exception:
        return None


def set_window_position_physical(
    hwnd: int, px: int, py: int, pw: int, ph: int
) -> bool:
    if not IS_WINDOWS or not is_window(hwnd):
        return False
    try:
        return bool(
            user32.SetWindowPos(
                hwnd, 0, int(px), int(py), int(pw), int(ph), SWP_NOZORDER | SWP_NOACTIVATE
            )
        )
    except Exception:
        return False


# ---------------------------------------------------------------- wallpaper blur
def wallpaper_settings():
    if not IS_WINDOWS:
        return None, None, None, 0
    buf = ctypes.create_unicode_buffer(260)
    try:
        ok = user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 260, buf, 0)
    except Exception:
        return None, None, None, 0
    if not ok or not buf.value:
        return None, None, None, 0
    try:
        mtime = os.path.getmtime(buf.value)
    except OSError:
        mtime = 0
    style, tile = 10, 0
    if winreg is not None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
            style = int(winreg.QueryValue(key, "WallpaperStyle"))
            tile = int(winreg.QueryValue(key, "TileWallpaper"))
            winreg.CloseKey(key)
        except Exception:
            style, tile = 10, 0
    return buf.value, style, tile, mtime


def scaled_wallpaper(image, width: int, height: int, style: int, tile: int):
    if tile:
        canvas = Image.new("RGB", (width, height))
        for x in range(0, width, image.width):
            for y in range(0, height, image.height):
                canvas.paste(image, (x, y))
        return canvas
    if style == 10:
        scale = max(width / image.width, height / image.height)
        scaled = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.LANCZOS,
        )
        left = (scaled.width - width) // 2
        top = (scaled.height - height) // 2
        return scaled.crop((left, top, left + width, top + height))
    if style == 2:
        return image.resize((max(1, width), max(1, height)), Image.LANCZOS)
    return None


def custom_bg_settings():
    """Read the shared background image settings, or None when none is set."""
    bg_path = background_config_path()
    if not bg_path.is_file():
        return None
    try:
        cfg_mtime = bg_path.stat().st_mtime
        raw = json.loads(bg_path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            return None
        image_path = str(raw.get("image_path") or "").strip()
        if not image_path or not os.path.isfile(image_path):
            return None
        img_mtime = os.path.getmtime(image_path)
        blur_radius = max(0.0, min(60.0, float(raw.get("blur_radius", 6.0))))
        tint_opacity = max(0.0, min(0.95, float(raw.get("tint_opacity", 0.35))))
        brightness = max(0.1, min(1.5, float(raw.get("brightness", 0.85))))
        saturation = max(0.0, min(1.5, float(raw.get("saturation", 0.90))))
        fit_mode = str(raw.get("fit_mode") or "per_widget").strip().lower()
        return (
            image_path,
            img_mtime,
            cfg_mtime,
            blur_radius,
            tint_opacity,
            brightness,
            saturation,
            fit_mode,
        )
    except Exception:
        return None


def build_blur_rect(monitor_rect, card_rect, settings, dpr: float, custom_bg=None):
    """Build frosted-glass background QPixmap matching Desktop Monitor."""
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

    crop = None
    if path and crop_w >= card_w // 2 and crop_h >= card_h // 2:
        try:
            image = Image.open(path).convert("RGB")
            fitted = scaled_wallpaper(image, screen_w, screen_h, style, tile)
            if fitted is not None:
                crop = fitted.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
        except Exception:
            crop = None

    if crop is None:
        crop = Image.new("RGB", (card_w, card_h), (16, 18, 24))

    inset = int(GLOW * dpr)
    if crop.size[0] < 2 * inset + 4 or crop.size[1] < 2 * inset + 4:
        return None

    blurred = None
    if custom_bg is not None:
        c_path, _im, _cm, c_blur, _c_tint, c_bright, c_sat, c_fit = custom_bg
        try:
            from PIL import ImageEnhance

            c_img = Image.open(c_path).convert("RGB")
            if c_fit == "desktop":
                c_fitted = scaled_wallpaper(c_img, screen_w, screen_h, 10, 0)
                c_crop = (
                    c_fitted.crop(
                        (crop_x, crop_y, crop_x + crop.size[0], crop_y + crop.size[1])
                    )
                    if c_fitted
                    else None
                )
            else:
                c_crop = scaled_wallpaper(c_img, crop.size[0], crop.size[1], 10, 0)
            if c_crop is not None:
                if c_blur > 0.1:
                    c_crop = c_crop.filter(
                        ImageFilter.GaussianBlur(
                            radius=max(0.5, min(80.0, c_blur * dpr))
                        )
                    )
                if abs(c_bright - 1.0) > 0.01:
                    c_crop = ImageEnhance.Brightness(c_crop).enhance(c_bright)
                if abs(c_sat - 1.0) > 0.01:
                    c_crop = ImageEnhance.Color(c_crop).enhance(c_sat)
                blurred = c_crop
        except Exception:
            blurred = None

    if blurred is None:
        radius = max(18, min(40, int(18 * dpr)))
        blurred = crop.filter(ImageFilter.GaussianBlur(radius=radius))
        curve = [int(130 * (1 - math.exp(-i / 110.0))) for i in range(256)]
        blurred = blurred.point(curve * 3)
        blurred = Image.blend(blurred, blurred.convert("L").convert("RGB"), 0.3)

    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (inset, inset, crop.size[0] - 1 - inset, crop.size[1] - 1 - inset),
        radius=int(CORNER_RADIUS * dpr),
        fill=255,
    )
    composite = crop.copy()
    composite.paste(blurred, (0, 0), mask)

    buffer = io.BytesIO()
    composite.save(buffer, format="PNG")
    qimg = QImage.fromData(buffer.getvalue())
    if qimg.isNull():
        return None
    result = QPixmap.fromImage(qimg)
    result.setDevicePixelRatio(dpr)
    return result


# ---------------------------------------------------------------- collector orchestration
def collect_rows(
    cache: UsageCache | None = None,
    force: bool = False,
) -> tuple[list[UsageRow], bool, str]:
    """Read every enabled provider from config.json through the registry.

    Returns ``(rows, any_stale, "HH:MM")``. With no providers configured the
    list is empty and the card shows its "Add a provider" message. The registry
    never raises; a failing provider becomes one error row.
    """
    config = provider_config.load_config()
    return registry.collect_enabled(config, cache=cache, force=force)


def provider_count() -> int:
    """Number of providers in config.json (enabled or not)."""
    try:
        return len(provider_config.list_instances(provider_config.load_config()))
    except Exception:
        return 0


class CollectorWorker(QtCore.QObject):
    """Background worker (own QThread) that polls the providers every few minutes.

    Every network call happens here, never on the UI thread. Other threads ask
    for a refresh with ``QMetaObject.invokeMethod(worker, "force_poll", Qt.QueuedConnection)``
    or by connecting a signal to ``force_poll``.
    """

    snapshot_ready = QtCore.Signal(list, bool, str)

    def __init__(self, interval_ms: int = 300_000) -> None:
        super().__init__()
        self.interval_ms = interval_ms
        self.cache = UsageCache()
        self._timer: QTimer | None = None

    @QtCore.Slot()
    def start(self) -> None:
        self.poll_once(force=False)
        self._timer = QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(lambda: self.poll_once(force=False))
        self._timer.start()

    @QtCore.Slot()
    def force_poll(self) -> None:
        """Poll now, skipping each provider's minimum interval (after Settings changes)."""
        self.poll_once(force=True)

    def poll_once(self, force: bool = False) -> None:
        try:
            rows, any_stale, stamp = collect_rows(cache=self.cache, force=force)
            keep = {r.provider_id for r in rows}
            try:
                self.cache.prune(keep)
            except Exception:
                pass
            self.snapshot_ready.emit(rows, any_stale, stamp)
        except Exception as exc:
            log_line(f"provider poll failed: {type(exc).__name__}")

    @QtCore.Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()


# ---------------------------------------------------------------- widget UI
class UsageMonitorWidget(QtWidgets.QWidget):
    """Frameless frosted-glass Usage Monitor card pinned to the desktop layer."""

    refresh_requested = QtCore.Signal()
    settings_requested = QtCore.Signal()

    def __init__(self, config: dict, initial_rows: list[UsageRow] | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Usage Monitor")
        self.config = config
        self._rows: list[UsageRow] = list(initial_rows or [])
        self._provider_count: int = provider_count()
        self._credits: list[CreditRow] = build_credit_rows(self._rows)
        self._any_stale: bool = False
        self._last_refresh_str: str = datetime.datetime.now().strftime("%H:%M")
        self._scroll_y: int = 0
        self._max_scroll_y: int = 0

        self._blurred: QPixmap | None = None
        self._blur_key = None
        self._wallpaper_settings = wallpaper_settings()
        self._custom_bg = custom_bg_settings()
        self._last_wallpaper_check = 0.0
        self._last_custom_bg_check = 0.0
        self._pinned = False
        self._parent_hwnd = 0
        self._zones: list[tuple[QRectF, str]] = []
        self._press = None
        self._grab = None
        self._resizing = False
        self._pending_resize = None
        # Menu shown on right-click (set by main(); used on Linux, where there may be no tray).
        self.context_menu: QtWidgets.QMenu | None = None

        self._resize_timer = QTimer(self)
        self._resize_timer.setInterval(16)
        self._resize_timer.timeout.connect(self._apply_pending_resize)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        if not IS_WINDOWS:
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._w = max(MIN_W, min(MAX_W, int(config.get("w") or CARD_WIDTH)))
        self._height = max(MIN_H, min(MAX_H, int(config.get("h") or DEFAULT_H)))
        self.setFixedSize(self._w, self._height)
        self._pos = (int(config["x"]), int(config["y"]))
        self.setGeometry(self._pos[0], self._pos[1], self._w, self._height)

    # -- active tab & stars ---------------------------------------------------
    def active_tab(self) -> str:
        """Return active tab ('all', 'favourites', or 'credits').

        Once anything is starred, FAVOURITES is the default tab unless the user
        explicitly clicked another tab in this session.
        """
        explicit = self.config.get("tab")
        if explicit in ("all", "favourites", "credits"):
            return explicit
        favs = self.config.get("favourites") or []
        return "favourites" if favs else "all"

    def is_starred(self, row: UsageRow) -> bool:
        favs = self.config.get("favourites") or []
        return row.star_key in favs

    def toggle_star(self, star_key: str) -> None:
        favs = list(self.config.get("favourites") or [])
        if star_key in favs:
            favs.remove(star_key)
        else:
            favs.append(star_key)
        self.config["favourites"] = favs
        save_config(self.config)
        self.update()

    def move_favourite(self, star_key: str, delta: int) -> None:
        """Move a starred row up (-1) or down (+1) in the user's own order."""
        favs = list(self.config.get("favourites") or [])
        if star_key not in favs:
            return
        idx = favs.index(star_key)
        new_idx = max(0, min(len(favs) - 1, idx + delta))
        if new_idx != idx:
            favs.pop(idx)
            favs.insert(new_idx, star_key)
            self.config["favourites"] = favs
            save_config(self.config)
            self.update()

    @QtCore.Slot(list, bool, str)
    def on_snapshot(self, rows: list[UsageRow], any_stale: bool, stamp: str) -> None:
        self._rows = rows
        self._provider_count = provider_count()
        self._credits = build_credit_rows(rows)
        self._any_stale = any_stale
        self._last_refresh_str = stamp
        self.update()

    # -- coordinates & pinning ------------------------------------------------
    def virtual_origin(self) -> tuple[int, int]:
        if not IS_WINDOWS:
            return 0, 0
        try:
            return (
                int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
                int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
            )
        except Exception:
            return 0, 0

    def card_screen(self):
        center = QPoint(
            int(self._pos[0] + self._w / 2), int(self._pos[1] + self._height / 2)
        )
        return (
            QtGui.QGuiApplication.screenAt(center)
            or QtWidgets.QApplication.primaryScreen()
        )

    def monitor_origin(self) -> tuple[int, int]:
        screen = self.card_screen()
        return screen.geometry().x(), screen.geometry().y()

    def pin_position(self) -> tuple[int, int]:
        ox, oy = self.virtual_origin()
        dpr = self.devicePixelRatio()
        return (int(self._pos[0] * dpr) - ox, int(self._pos[1] * dpr) - oy)

    def apply_extended_style(self, hwnd: int) -> None:
        style = get_window_long(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if move_mode(self.config):
            style &= ~WS_EX_TRANSPARENT
            style &= ~WS_EX_LAYERED
        else:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        set_window_long(hwnd, GWL_EXSTYLE, style)
        if style & WS_EX_LAYERED:
            try:
                user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
            except Exception:
                pass

    def apply_round_region(self, hwnd: int, width: int, height: int) -> None:
        if not IS_WINDOWS or getattr(self, "_region_size", None) == (width, height):
            return
        try:
            diameter = int(2 * (CORNER_RADIUS + GLOW) * self.devicePixelRatio())
            region = ctypes.windll.gdi32.CreateRoundRectRgn(
                0, 0, width + 1, height + 1, diameter, diameter
            )
            if region and user32.SetWindowRgn(hwnd, region, True):
                self._region_size = (width, height)
            elif region:
                ctypes.windll.gdi32.DeleteObject(region)
        except Exception:
            pass

    def pin_to_desktop(self) -> bool:
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

    def place_pinned(self, force: bool = False) -> None:
        if not IS_WINDOWS or not self._pinned:
            return
        hwnd = int(self.winId())
        cx, cy = self.pin_position()
        dpr = self.devicePixelRatio()
        want = (cx, cy, int(self._w * dpr), int(self._height * dpr))
        if not force and child_rect(hwnd) == want:
            return
        set_window_position_physical(hwnd, *want)
        self.apply_round_region(hwnd, want[2], want[3])

    def unpin_from_desktop(self) -> bool:
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
        self._region_size = None
        dpr = self.devicePixelRatio()
        self.apply_round_region(
            int(self.winId()), int(self._w * dpr), int(self._height * dpr)
        )
        return True

    def repin_if_needed(self) -> None:
        if not IS_WINDOWS or move_mode(self.config) or not self._pinned:
            return
        hwnd = int(self.winId())
        parent = get_parent(hwnd)
        if not is_window(hwnd) or not parent or parent != self._parent_hwnd:
            self._pinned = False
            self._parent_hwnd = 0
            self.pin_to_desktop()
        else:
            self.place_pinned()

    # -- resize & drag --------------------------------------------------------
    def edges_at(self, x: float, y: float, width: float, height: float) -> str:
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

    def queue_resize(self, left: int, top: int, right: int, bottom: int, edges: str) -> None:
        self._pending_resize = (left, top, right, bottom, edges)
        if not self._resizing:
            self._resizing = True
            self._resize_timer.start()

    def _apply_pending_resize(self) -> None:
        pending, self._pending_resize = self._pending_resize, None
        if pending is not None:
            self.resize_physical(*pending)

    def resize_physical(self, left: int, top: int, right: int, bottom: int, edges: str) -> None:
        dpr = self.devicePixelRatio() or 1.0
        min_w, max_w = MIN_W * dpr, MAX_W * dpr
        min_h, max_h = MIN_H * dpr, MAX_H * dpr
        if "L" in edges:
            left = max(right - max_w, min(left, right - min_w))
        elif "R" in edges:
            right = min(left + max_w, max(right, left + min_w))
        if "T" in edges:
            top = max(bottom - max_h, min(top, bottom - min_h))
        elif "B" in edges:
            bottom = min(top + max_h, max(bottom, top + min_h))
        w = max(MIN_W, min(MAX_W, int(round((right - left) / dpr))))
        h = max(MIN_H, min(MAX_H, int(round((bottom - top) / dpr))))
        x = int(round(right / dpr)) - w if "L" in edges else int(round(left / dpr))
        y = int(round(bottom / dpr)) - h if "T" in edges else int(round(top / dpr))
        if (w, h, (x, y)) == (self._w, self._height, self._pos):
            return
        self._w, self._height, self._pos = w, h, (x, y)
        self.setFixedSize(self._w, self._height)
        self.place_pinned(force=True)
        self.repaint()

    def end_resize(self) -> None:
        self._resize_timer.stop()
        self._apply_pending_resize()
        self._resizing = False
        self.config["w"], self.config["h"] = self._w, self._height
        self.config["x"], self.config["y"] = self._pos
        save_config(self.config)
        self._blurred = None
        self.refresh_blur(force=True)
        self.update()

    def desktop_hook_allowed(self) -> bool:
        return IS_WINDOWS and self._pinned and not move_mode(self.config)

    def desktop_drag_allowed(self) -> bool:
        return self.desktop_hook_allowed() and bool(
            self.config.get("drag_on_desktop", True)
        )

    def drag_to_physical(self, screen_x: int, screen_y: int) -> None:
        vx, vy = self.virtual_origin()
        dpr = self.devicePixelRatio()
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        w, h = int(self._w * dpr), int(self._height * dpr)
        screen_x = max(vx, min(vx + vw - w, int(screen_x)))
        screen_y = max(vy, min(vy + vh - h, int(screen_y)))
        pos = (int(round(screen_x / dpr)), int(round(screen_y / dpr)))
        if pos != self._pos:
            self._pos = pos
            self.place_pinned(force=True)

    def finish_drag(self) -> None:
        self.config["x"], self.config["y"] = self._pos
        save_config(self.config)
        self._blurred = None
        self.refresh_blur(force=True)
        self.update()

    def scroll_by(self, delta_px: int) -> None:
        new_scroll = max(0, min(self._max_scroll_y, self._scroll_y + delta_px))
        if new_scroll != self._scroll_y:
            self._scroll_y = new_scroll
            self.update()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        steps = event.angleDelta().y()
        if steps != 0:
            self.scroll_by(48 if steps < 0 else -48)
            event.accept()

    # -- mouse interaction in unlocked Move mode ------------------------------
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press = (
                event.globalPosition().x(),
                event.globalPosition().y(),
                event.position().x(),
                event.position().y(),
            )
            if move_mode(self.config):
                self._grab = (
                    event.globalPosition().x() - self._pos[0],
                    event.globalPosition().y() - self._pos[1],
                )

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not move_mode(self.config) or self._grab is None:
            return
        x = int(event.globalPosition().x() - self._grab[0])
        y = int(event.globalPosition().y() - self._grab[1])
        if (x, y) != self._pos:
            self._pos = (x, y)
            self.move(x, y)
            self.refresh_blur(force=True)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._grab = None
            press, self._press = self._press, None
            if (
                press is not None
                and abs(event.globalPosition().x() - press[0])
                + abs(event.globalPosition().y() - press[1])
                <= 4
            ):
                zone = self.zone_at(press[2], press[3])
                if zone:
                    self.handle_zone(zone)
            if move_mode(self.config):
                self.config["x"], self.config["y"] = self._pos
                save_config(self.config)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        """Right-click on the card shows the same menu as the tray icon (Linux only)."""
        if not IS_WINDOWS and self.context_menu is not None:
            self.context_menu.popup(event.globalPos())
            event.accept()

    def zone_at(self, x: float, y: float) -> str | None:
        pt = QPointF(x, y)
        for rect, name in self._zones:
            if rect.adjusted(-2, -2, 2, 2).contains(pt):
                return name
        return None

    def handle_zone(self, name: str) -> None:
        if name in ("tab_all", "tab_favourites", "tab_credits"):
            self.config["tab"] = name[4:]
            self._scroll_y = 0
            save_config(self.config)
            self.update()
        elif name.startswith("star:"):
            self.toggle_star(name[5:])
        elif name.startswith("up:"):
            self.move_favourite(name[3:], -1)
        elif name.startswith("down:"):
            self.move_favourite(name[5:], 1)
        elif name == "scroll_up":
            self.scroll_by(-120)
        elif name == "scroll_down":
            self.scroll_by(120)
        elif name == "refresh":
            self.refresh_requested.emit()
        elif name == "settings":
            self.settings_requested.emit()

    # -- blur & painting ------------------------------------------------------
    def refresh_blur(self, force: bool = False) -> QPixmap | None:
        if Image is None:
            return None
        now = time.monotonic()
        if now - self._last_wallpaper_check > 30.0:
            self._last_wallpaper_check = now
            ws = wallpaper_settings()
            if ws != self._wallpaper_settings:
                self._wallpaper_settings = ws
                force = True
        if now - self._last_custom_bg_check > 1.5 or force:
            self._last_custom_bg_check = now
            cbg = custom_bg_settings()
            if cbg != self._custom_bg:
                self._custom_bg = cbg
                force = True
        dpr = self.devicePixelRatio()
        key = (
            self._pos[0],
            self._pos[1],
            self._w,
            self._height,
            self._wallpaper_settings,
            self._custom_bg,
            dpr,
        )
        if self._blurred is not None and key == self._blur_key and not force:
            return self._blurred
        if self._resizing and self._blurred is not None:
            return self._blurred

        px = int(self._pos[0] * dpr) - self.monitor_origin()[0] * dpr
        py = int(self._pos[1] * dpr) - self.monitor_origin()[1] * dpr
        screen = self.card_screen()
        mrect = (
            screen.geometry().x() * dpr,
            screen.geometry().y() * dpr,
            screen.geometry().width() * dpr,
            screen.geometry().height() * dpr,
        )
        self._blurred = build_blur_rect(
            mrect,
            (px, py, int(self._w * dpr), int(self._height * dpr)),
            self._wallpaper_settings,
            dpr,
            custom_bg=self._custom_bg,
        )
        self._blur_key = key
        return self._blurred

    def _left(self) -> int:
        return EDGE_PAD

    def _right(self) -> int:
        return self.width() - EDGE_PAD

    def _text(
        self,
        painter: QPainter,
        x: float,
        baseline: float,
        text: str,
        font: QFont,
        color: str | QColor,
        align_right: bool = False,
        max_width: float | None = None,
    ) -> int:
        painter.setFont(font)
        qcolor = QColor(color) if isinstance(color, str) else color
        metrics = QtGui.QFontMetrics(font)
        if max_width is not None:
            text = metrics.elidedText(text, Qt.ElideRight, max(10, int(max_width)))
        width = metrics.horizontalAdvance(text)
        left = int(x - width) if align_right else int(x)

        # Crisp dark halo for legibility over frosted wallpaper
        painter.setPen(QColor(0, 0, 0, 95))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(left + dx, int(baseline) + dy, text)
        painter.setPen(qcolor)
        painter.drawText(left, int(baseline), text)
        return width

    def _pill(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        active: bool,
        color: str | None = None,
        zone: str | None = None,
    ) -> None:
        tint = QColor(color or C_ACCENT)
        fill = QColor(tint)
        fill.setAlpha(70 if active else 0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill if active else QColor(255, 255, 255, 16))
        radius = rect.height() / 2.0
        painter.drawRoundedRect(rect, radius, radius)

        border = QColor(tint)
        border.setAlpha(220 if active else 90)
        painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)

        font = qt_font(8, True, 0.8)
        metrics = QtGui.QFontMetrics(font)
        w = metrics.horizontalAdvance(text)
        baseline = rect.top() + (rect.height() + metrics.ascent() - metrics.descent()) / 2
        self._text(
            painter,
            rect.center().x() - w / 2,
            baseline,
            text,
            font,
            "#FFFFFF" if active else C_TEXT2,
        )
        if zone:
            self._zones.append((QRectF(rect), zone))

    def _draw_bar(
        self,
        painter: QPainter,
        x: float,
        y: float,
        width: float,
        percent_used: float | None,
    ) -> None:
        """Draw a thin rounded bar on a faint track, coloured by percent_used."""
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 22))
        painter.drawRoundedRect(QRectF(x, y, width, 5.0), 2.5, 2.5)
        if percent_used is not None and percent_used > 0:
            fill = max(0.0, min(1.0, percent_used / 100.0))
            painter.setBrush(bar_color(percent_used))
            painter.drawRoundedRect(
                QRectF(x, y, max(4.0, width * fill), 5.0), 2.5, 2.5
            )

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        body = QRectF(
            GLOW + 0.5,
            GLOW + 0.5,
            self.width() - 2 * GLOW - 1,
            self.height() - 2 * GLOW - 1,
        )
        path = QPainterPath()
        path.addRoundedRect(body, CORNER_RADIUS, CORNER_RADIUS)

        bg = self.refresh_blur()
        if bg is not None:
            if self._resizing:
                painter.drawPixmap(QRect(0, 0, self.width(), self.height()), bg)
            else:
                painter.drawPixmap(0, 0, bg)
        else:
            painter.fillRect(QRect(0, 0, self.width(), self.height()), QColor(C_CARD))

        overlay = QColor(C_OVERLAY)
        eff_opacity = (
            self._custom_bg[4]
            if self._custom_bg is not None
            else opacity_fraction(self.config)
        )
        overlay.setAlpha(int(255 * eff_opacity))
        painter.setBrush(overlay)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        # Cyan outer glow + crisp border matching Desktop Monitor
        accent = QColor(C_ACCENT)
        painter.setBrush(Qt.NoBrush)
        for stroke_w, alpha in ((14, 10), (10, 18), (7, 30), (4.5, 55), (2.5, 95)):
            glow = QColor(accent)
            glow.setAlpha(alpha if not move_mode(self.config) else min(255, alpha * 2))
            painter.setPen(QPen(glow, stroke_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)
        edge = QColor("#67E8F9")
        edge.setAlpha(235)
        painter.setPen(QPen(edge, 1.3))
        painter.drawPath(path)

        self._zones = []
        y = float(EDGE_PAD)
        y = self._draw_tabs(painter, y)
        y += 5
        content_top = y + HEADER_H + 6
        self._update_scroll_bounds(content_top)
        y = self._draw_header(painter, y)
        y += 6

        tab = self.active_tab()
        if tab == "favourites":
            self._draw_favourites_tab(painter, y)
        elif tab == "credits":
            self._draw_credits_tab(painter, y)
        else:
            self._draw_all_tab(painter, y)

        painter.end()

    def _update_scroll_bounds(self, y_top: float) -> None:
        bottom_limit = float(self.height() - EDGE_PAD + 4)
        viewport_h = max(60.0, bottom_limit - y_top)
        tab = self.active_tab()
        total_h = 0.0
        if tab == "favourites":
            fav_keys = list(self.config.get("favourites") or [])
            by_key = {r.star_key: r for r in self._rows}
            starred_rows = [by_key[k] for k in fav_keys if k in by_key]
            if starred_rows:
                total_h = SECTION_H + 2 + len(starred_rows) * MODEL_ROW_H + 4
        elif tab == "credits":
            total_h = len(self._credits) * (SECTION_H + 50 + GAP)
        else:
            grouped: dict[str, list[UsageRow]] = {}
            for r in self._rows:
                grouped.setdefault(r.provider_id, []).append(r)
            for p_rows in grouped.values():
                total_h += SECTION_H + len(p_rows) * MODEL_ROW_H + 4
        self._max_scroll_y = max(0, int(total_h - viewport_h + 8))
        self._scroll_y = max(0, min(self._scroll_y, self._max_scroll_y))

    def _draw_tabs(self, painter: QPainter, y: float) -> float:
        tab = self.active_tab()
        left = float(self._left())
        fav_count = len(self.config.get("favourites") or [])
        fav_label = f"FAV ({fav_count})" if fav_count else "FAVOURITES"
        fav_w = 68.0 if fav_count else 82.0
        self._pill(
            painter, QRectF(left, y, 46, TAB_H - 2), "ALL", tab == "all", zone="tab_all"
        )
        self._pill(
            painter,
            QRectF(left + 52, y, fav_w, TAB_H - 2),
            fav_label,
            tab == "favourites",
            zone="tab_favourites",
        )
        self._pill(
            painter,
            QRectF(left + 52 + fav_w + 6, y, 64, TAB_H - 2),
            "CREDITS",
            tab == "credits",
            zone="tab_credits",
        )
        return y + TAB_H

    def _draw_header(self, painter: QPainter, y: float) -> float:
        left, right = float(self._left()), float(self._right())
        title_w = self._text(
            painter, left, y + 13, "AI USAGE", qt_font(10, True), C_VALUE
        )

        # Status dot: small amber dot (#F59E0B) when any source is stale, else emerald
        dot_x = left + title_w + 10
        dot_y = y + 9.0
        dot_color = QColor(C_AMBER if self._any_stale else C_EMERALD)
        painter.setPen(Qt.NoPen)
        glow = QColor(dot_color)
        glow.setAlpha(75)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(dot_x, dot_y), 5.0, 5.0)
        painter.setBrush(dot_color)
        painter.drawEllipse(QPointF(dot_x, dot_y), 3.0, 3.0)

        if self._any_stale:
            self._text(painter, dot_x + 7, y + 12, "STALE", qt_font(7, True), C_AMBER)

        # Gear (opens Settings) at the far right, then the clickable refresh time
        # "Settings" glyph from the Windows icon font (monochrome, unlike the emoji gear)
        if IS_WINDOWS and font_family_available("Segoe MDL2 Assets"):
            gear_font = QFont("Segoe MDL2 Assets")
            gear_font.setPixelSize(13)
            gear_w = self._text(
                painter, right, y + 14, "\ue713", gear_font, C_LABEL, align_right=True
            )
        else:
            # No Segoe icon font (Linux, or a machine without it): paint the gear as a shape.
            gear_w = 13.0
            draw_gear_icon(painter, QPointF(right - gear_w / 2, y + 8.0), 6.5, QColor(C_LABEL))
        self._zones.append((QRectF(right - gear_w - 4, y - 1, gear_w + 8, HEADER_H + 2), "settings"))
        stamp_right = right - gear_w - 10
        stamp_text = f"↻ {self._last_refresh_str}"
        stamp_w = self._text(
            painter, stamp_right, y + 13, stamp_text, qt_font(8), C_LABEL, align_right=True
        )
        self._zones.append((QRectF(stamp_right - stamp_w - 6, y, stamp_w + 8, HEADER_H), "refresh"))
        rw = (right - stamp_right) + stamp_w

        # Compact scroll pills in the header when any tab overflows
        if self._max_scroll_y > 0:
            btn_right = right - rw - 12
            dn_rect = QRectF(btn_right - 18, y + 1, 18, HEADER_H - 2)
            up_rect = QRectF(btn_right - 39, y + 1, 18, HEADER_H - 2)
            self._pill(painter, up_rect, "▲", self._scroll_y > 0, zone="scroll_up")
            self._pill(
                painter,
                dn_rect,
                "▼",
                self._scroll_y < self._max_scroll_y,
                zone="scroll_down",
            )
        return y + HEADER_H

    def _draw_model_row(
        self,
        painter: QPainter,
        y: float,
        row: UsageRow,
        show_reorder: bool = False,
    ) -> float:
        """Render a single model row: name, bar, percent used (5h & 7d), reset time, and star."""
        left, right = float(self._left()), float(self._right())
        starred = self.is_starred(row)

        # Star button at row end
        star_x = right - 13
        star_rect = QRectF(star_x - 2, y, 17, MODEL_ROW_H - 2)
        self._text(
            painter,
            right,
            y + 12,
            "★" if starred else "☆",
            qt_font(10, True),
            C_AMBER if starred else C_LABEL,
            align_right=True,
        )
        self._zones.append((star_rect, f"star:{row.star_key}"))

        content_right = star_x - 4
        if show_reorder:
            # Small up / down buttons on the FAVOURITES tab to reorder starred rows
            up_rect = QRectF(content_right - 26, y + 1, 12, 14)
            dn_rect = QRectF(content_right - 13, y + 1, 12, 14)
            self._text(painter, up_rect.left() + 2, y + 11, "↑", qt_font(8, True), C_LABEL)
            self._text(painter, dn_rect.left() + 2, y + 11, "↓", qt_font(8, True), C_LABEL)
            self._zones.append((up_rect, f"up:{row.star_key}"))
            self._zones.append((dn_rect, f"down:{row.star_key}"))
            content_right -= 30

        # Right side of top line: explicit 5h + 7d (weekly) limits & reset time
        pct = row.percent_used
        if pct is not None and row.status not in ("no_data", "error"):
            pct_str = f"{pct:.0f}%"
            pct_col = bar_color(pct)
        else:
            pct_str = "no data"
            pct_col = QColor(C_LABEL)

        reset_note = row.display_reset_Or_reason()
        if row.status == "error":
            right_label = row.reason or "Could not read usage"
            pct_col = QColor(C_RED)
            pct = None
        elif reset_note and reset_note != "no data" and pct is None:
            right_label = reset_note
        elif reset_note and reset_note != "no data":
            # Compact 'resets in 4h 21m' to '4h 21m' when both 5h and 7d limits are shown
            compact_note = (
                reset_note.replace("resets in ", "↻ ")
                if row.secondary_label
                else reset_note
            )
            right_label = f"{pct_str} · {compact_note}".strip()
        else:
            right_label = pct_str

        name_font = qt_font(8, True)
        right_font = qt_font(7, False)
        total_w = content_right - left
        right_metrics = QtGui.QFontMetrics(right_font)
        desired_rw = right_metrics.horizontalAdvance(right_label)
        max_rw = max(total_w * 0.68, total_w - 76.0)
        rw = self._text(
            painter,
            content_right,
            y + 11,
            right_label,
            right_font,
            pct_col if (pct is not None or row.status == "error") else C_LABEL,
            align_right=True,
            max_width=min(max_rw, max(desired_rw + 2, 128.0)),
        )

        # Left side of top line: model name
        self._text(
            painter,
            left,
            y + 11,
            row.model_label,
            name_font,
            C_VALUE,
            max_width=max(44.0, content_right - left - rw - 6),
        )

        # Bottom line of row: thin rounded progress bar across content width
        self._draw_bar(painter, left, y + 16, content_right - left, pct)
        return y + MODEL_ROW_H

    def _draw_empty_state(self, painter: QPainter, y_top: float) -> bool:
        """Draw the first-run message and button. Returns True when it was drawn."""
        if self._rows:
            return False
        left, right = float(self._left()), float(self._right())
        if self._provider_count == 0:
            title = "No providers yet"
            lines = ("Add the AI services and budgets you", "want to keep an eye on.")
        else:
            title = "All providers are turned off"
            lines = ("Turn one on in Settings to see", "its usage here.")
        self._text(painter, left, y_top + 24, title, qt_font(10, True), C_VALUE)
        self._text(painter, left, y_top + 44, lines[0], qt_font(8), C_LABEL)
        self._text(painter, left, y_top + 60, lines[1], qt_font(8), C_LABEL)
        button = QRectF(left, y_top + 76, min(150.0, right - left), 26)
        painter.setPen(Qt.NoPen)
        fill = QColor(C_ACCENT)
        fill.setAlpha(60)
        painter.setBrush(fill)
        painter.drawRoundedRect(button, 8, 8)
        painter.setPen(QPen(QColor(C_ACCENT), 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(button, 8, 8)
        label = "Add a provider" if self._provider_count == 0 else "Open Settings"
        font = qt_font(9, True)
        text_w = QtGui.QFontMetrics(font).horizontalAdvance(label)
        self._text(
            painter, button.center().x() - text_w / 2, button.top() + 17, label, font, "#FFFFFF"
        )
        self._zones.append((button, "settings"))
        return True

    def _draw_all_tab(self, painter: QPainter, y_top: float) -> None:
        left = float(self._left())
        bottom_limit = float(self.height() - EDGE_PAD + 4)
        viewport_h = max(60.0, bottom_limit - y_top)
        if self._draw_empty_state(painter, y_top):
            return

        # Group rows by provider instance, keeping the order of config.json
        grouped: dict[str, list[UsageRow]] = {}
        for r in self._rows:
            grouped.setdefault(r.provider_id, []).append(r)

        painter.save()
        painter.setClipRect(QRectF(0, y_top, self.width(), viewport_h))

        cur_y = y_top - self._scroll_y
        label_font = qt_font(8, True, 1.0)
        for p_rows in grouped.values():
            provider_label = p_rows[0].provider_label
            if cur_y + SECTION_H >= y_top - 20 and cur_y <= bottom_limit:
                self._text(painter, left, cur_y + 11, provider_label, label_font, C_LABEL)
            cur_y += SECTION_H
            for row in p_rows:
                if cur_y + MODEL_ROW_H >= y_top and cur_y <= bottom_limit:
                    self._draw_model_row(painter, cur_y, row, show_reorder=False)
                cur_y += MODEL_ROW_H
            cur_y += 4

        painter.restore()

    def _draw_favourites_tab(self, painter: QPainter, y_top: float) -> None:
        left = float(self._left())
        fav_keys = list(self.config.get("favourites") or [])
        by_key = {r.star_key: r for r in self._rows}
        starred_rows = [by_key[k] for k in fav_keys if k in by_key]

        if self._draw_empty_state(painter, y_top):
            return
        if not starred_rows:
            self._text(
                painter,
                left,
                y_top + 22,
                "No favourites starred yet.",
                qt_font(9, True),
                C_VALUE,
            )
            self._text(
                painter,
                left,
                y_top + 42,
                "Click ☆ on any row in the ALL tab to pin",
                qt_font(8),
                C_LABEL,
            )
            self._text(
                painter,
                left,
                y_top + 58,
                "your go-to limits here in your own order.",
                qt_font(8),
                C_LABEL,
            )
            return

        bottom_limit = float(self.height() - EDGE_PAD + 4)
        viewport_h = max(60.0, bottom_limit - y_top)
        painter.save()
        painter.setClipRect(QRectF(0, y_top, self.width(), viewport_h))

        cur_y = y_top - self._scroll_y
        label_font = qt_font(8, True, 1.0)
        if cur_y + SECTION_H >= y_top - 20 and cur_y <= bottom_limit:
            self._text(
                painter,
                left,
                cur_y + 11,
                "STARRED MODELS (YOUR ORDER)",
                label_font,
                C_LABEL,
            )
        cur_y += SECTION_H + 2
        for row in starred_rows:
            if cur_y + MODEL_ROW_H >= y_top and cur_y <= bottom_limit:
                tag_row = UsageRow.from_dict(row.to_dict())
                tag_row.model_label = f"{row.provider_label[:6]} · {row.model_label}"
                self._draw_model_row(painter, cur_y, tag_row, show_reorder=True)
            cur_y += MODEL_ROW_H

        painter.restore()

    def _draw_credits_tab(self, painter: QPainter, y_top: float) -> None:
        left, right = float(self._left()), float(self._right())
        width = right - left
        bottom_limit = float(self.height() - EDGE_PAD + 4)
        viewport_h = max(60.0, bottom_limit - y_top)
        label_font = qt_font(8, True, 1.0)

        if self._draw_empty_state(painter, y_top):
            return
        if not self._credits:
            self._text(painter, left, y_top + 22, "No balances yet.", qt_font(9, True), C_VALUE)
            self._text(painter, left, y_top + 42, "Providers with credit, spend or a budget",
                       qt_font(8), C_LABEL)
            self._text(painter, left, y_top + 58, "show up here.", qt_font(8), C_LABEL)
            return
        # Budgets use the user's own currency; their row already has the text.
        budget_text = {
            r.provider_id: r.secondary_label
            for r in self._rows
            if r.provider_kind in ("manual_budget", "modal_budget") and r.secondary_label
        }

        painter.save()
        painter.setClipRect(QRectF(0, y_top, self.width(), viewport_h))
        y = y_top - self._scroll_y

        for credit in self._credits:
            self._text(
                painter, left, y + 11, credit.provider_label, label_font, C_LABEL
            )
            if credit.reset_text:
                self._text(
                    painter,
                    right,
                    y + 11,
                    credit.reset_text,
                    qt_font(8),
                    C_LABEL,
                    align_right=True,
                )
            y += SECTION_H

            # Main balance / spend headline
            if credit.provider_id in budget_text and credit.balance_usd is not None:
                headline = budget_text[credit.provider_id]
                head_color = bar_color(credit.percent_used) if credit.percent_used is not None else C_VALUE
            elif credit.balance_usd is not None:
                headline = f"${credit.balance_usd:.2f} left"
                head_color = (
                    C_EMERALD
                    if credit.balance_usd > 2.0
                    else (C_AMBER if credit.balance_usd > 0.5 else C_RED)
                )
            elif credit.percent_used is not None:
                headline = f"{credit.percent_used:.0f}% daily cap"
                head_color = bar_color(credit.percent_used)
            else:
                headline = "no data"
                head_color = C_LABEL

            self._text(painter, left, y + 16, headline, qt_font(13, True), head_color)
            if (credit.spent_usd is not None and credit.spent_usd > 0
                    and credit.provider_id not in budget_text):
                self._text(
                    painter,
                    right,
                    y + 16,
                    f"${credit.spent_usd:.2f} spent",
                    qt_font(9, True),
                    C_VALUE,
                    align_right=True,
                )
            y += 22

            self._draw_bar(painter, left, y, width, credit.percent_used)
            y += 10

            self._text(
                painter,
                left,
                y + 10,
                credit.detail_text,
                qt_font(8),
                C_TEXT2,
                max_width=width,
            )
            y += 18 + GAP

        painter.restore()


# ---------------------------------------------------------------- desktop mouse hook
class DesktopDragHook:
    """Low-level mouse hook so the WorkerW-pinned card supports clicks, drags & wheel."""

    WH_MOUSE_LL = 14
    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_MOUSEWHEEL = 0x020A

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_long),
            ("y", ctypes.c_long),
            ("mouseData", ctypes.c_uint32),
            ("flags", ctypes.c_uint32),
            ("time", ctypes.c_uint32),
            ("extra", ctypes.c_size_t),
        ]

    def __init__(self, widget: UsageMonitorWidget) -> None:
        self.widget = widget
        self._hook = None
        self._grab = None
        self._press = None
        self._moved = False
        self._zone = None
        self._rs = None
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
        )
        self._u.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            self._proc_type,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._u.SetWindowsHookExW.restype = ctypes.c_void_p
        self._u.CallNextHookEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        self._u.CallNextHookEx.restype = ctypes.c_ssize_t
        self._u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        self._u.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
        self._u.WindowFromPoint.restype = ctypes.c_void_p
        self._u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self._u.GetAncestor.restype = ctypes.c_void_p
        self._u.GetWindowRect.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.wintypes.RECT),
        ]
        self._callback = self._proc_type(self._proc)

    def start(self) -> None:
        if not IS_WINDOWS:
            return
        self._u.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
        self._timer = QTimer()
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        if self._grab is not None or self._rs is not None:
            return
        pt = ctypes.wintypes.POINT()
        over = (
            self.widget.desktop_hook_allowed()
            and self._u.GetCursorPos(ctypes.byref(pt))
            and self._hit(pt.x, pt.y)
        )
        if over and not self._hook:
            self.install()
        elif not over and self._hook:
            self.uninstall()

    def install(self) -> None:
        if not IS_WINDOWS or self._hook:
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        k32.GetModuleHandleW.restype = ctypes.c_void_p
        mod = k32.GetModuleHandleW(None)
        self._hook = self._u.SetWindowsHookExW(self.WH_MOUSE_LL, self._callback, mod, 0)

    def uninstall(self) -> None:
        if self._hook:
            self._u.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def stop(self) -> None:
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        self.uninstall()

    def _card_rect(self):
        rect = ctypes.wintypes.RECT()
        if not self._u.GetWindowRect(int(self.widget.winId()), ctypes.byref(rect)):
            return None
        return rect

    def _hit(self, x: int, y: int) -> bool:
        rect = self._card_rect()
        if rect is None or not (
            rect.left <= x < rect.right and rect.top <= y < rect.bottom
        ):
            return False
        card = int(self.widget.winId())
        under = self._u.WindowFromPoint(ctypes.wintypes.POINT(x, y)) or 0
        if under == card:
            return True
        desktop_root = self._u.GetAncestor(card, GA_ROOT) or 0
        return bool(under) and (self._u.GetAncestor(under, GA_ROOT) or 0) == desktop_root

    def _proc(self, code: int, wparam: int, lparam: int) -> int:
        try:
            if code == 0 and self.widget.desktop_hook_allowed():
                info = self.MSLLHOOKSTRUCT.from_address(lparam)
                if wparam == self.WM_MOUSEWHEEL and self._hit(info.x, info.y):
                    delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                    if delta != 0:
                        step = 54 if delta < 0 else -54
                        QTimer.singleShot(0, lambda s=step: self.widget.scroll_by(s))
                        return 1
                if wparam == self.WM_LBUTTONDOWN and self._hit(info.x, info.y):
                    rect = self._card_rect()
                    dpr = self.widget.devicePixelRatio() or 1.0
                    edges = self.widget.edges_at(
                        info.x - rect.left,
                        info.y - rect.top,
                        rect.right - rect.left,
                        rect.bottom - rect.top,
                    )
                    if edges:
                        self._rs = (
                            edges,
                            (rect.left, rect.top, rect.right, rect.bottom),
                        )
                        self._press = (info.x, info.y)
                        return 1
                    zone = self.widget.zone_at(
                        (info.x - rect.left) / dpr, (info.y - rect.top) / dpr
                    )
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
                        if (
                            not self._moved
                            and abs(info.x - self._press[0])
                            + abs(info.y - self._press[1])
                            > 4
                        ):
                            self._moved = True
                        if self._moved and self.widget.desktop_drag_allowed():
                            self.widget.drag_to_physical(
                                info.x - self._grab[0], info.y - self._grab[1]
                            )
                    elif wparam == self.WM_LBUTTONUP:
                        self._grab = None
                        if self._moved:
                            if self.widget.desktop_drag_allowed():
                                QTimer.singleShot(0, self.widget.finish_drag)
                        elif self._zone:
                            zone = self._zone
                            QTimer.singleShot(0, lambda z=zone: self.widget.handle_zone(z))
                        return 1
        except Exception:
            pass
        return self._u.CallNextHookEx(None, code, wparam, lparam)


# ---------------------------------------------------------------- tray & startup
def make_tray_icon() -> QPixmap:
    """Draw a 32x32 cyan/emerald gauge glyph for the system tray."""
    img = QImage(32, 32, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(C_CARD))
    p.setPen(QPen(QColor(C_ACCENT), 1.5))
    p.drawRoundedRect(QRectF(2, 2, 28, 28), 6, 6)

    # Three usage bars inside the tray icon
    for idx, (frac, col) in enumerate(
        ((0.45, C_EMERALD), (0.72, C_AMBER), (0.90, C_RED))
    ):
        by = 8 + idx * 6
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(6, by, 20, 3.5), 1.5, 1.5)
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(6, by, 20 * frac, 3.5), 1.5, 1.5)
    p.end()
    return QPixmap.fromImage(img)


# ---------------------------------------------------------------- autostart (sign-in)
def desktop_exec_quote(argument: str) -> str:
    """Quote one command-line argument for the ``Exec=`` line of a .desktop file.

    Plain words are left alone. Anything with a space or a special character is
    put in double quotes with the characters the Desktop Entry rules name
    escaped, and ``%`` doubled (``%`` starts a field code in an Exec line).
    """
    argument = argument.replace("%", "%%")
    if argument and not any(ch in argument for ch in " \t\n\"'\\><~|&;$*?#()`"):
        return argument
    for special in ("\\", '"', "`", "$"):
        argument = argument.replace(special, "\\" + special)
    # The Exec value is itself a string value, so backslashes are doubled once more.
    argument = argument.replace("\\", "\\\\")
    return f'"{argument}"'


def autostart_command() -> str:
    """The command run at sign-in.

    Windows: the exe when frozen, else pythonw + script. Linux: the
    ``desktopwidgets-usage`` launcher when it is installed, the frozen binary,
    or this Python plus this script.
    """
    if IS_WINDOWS:
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        return f'"{pythonw}" "{os.path.abspath(__file__)}"'
    if getattr(sys, "frozen", False):
        return desktop_exec_quote(sys.executable)
    launcher = shutil.which(LAUNCHER_NAME)
    if launcher:
        return desktop_exec_quote(launcher)
    return f"{desktop_exec_quote(sys.executable)} {desktop_exec_quote(os.path.abspath(__file__))}"


def linux_autostart_path() -> Path:
    """``$XDG_CONFIG_HOME/autostart/desktopwidgets-usage.desktop``."""
    return provider_config.config_root() / "autostart" / AUTOSTART_FILE_NAME


def linux_autostart_text() -> str:
    """The text of the autostart ``.desktop`` file (no secrets, no personal data)."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Usage Monitor\n"
        "Comment=AI usage and credits desktop widget\n"
        f"Exec={autostart_command()}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def linux_autostart_enabled() -> bool:
    """True when the autostart file exists and is not switched off inside."""
    path = linux_autostart_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lowered = text.lower().replace(" ", "")
    return "hidden=true" not in lowered and "x-gnome-autostart-enabled=false" not in lowered


def set_linux_autostart(enabled: bool) -> bool:
    """Write or delete the autostart file. Returns True on success."""
    path = linux_autostart_path()
    try:
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(linux_autostart_text(), encoding="utf-8")
            os.replace(tmp, path)
        else:
            path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log_line(f"autostart update failed: {type(exc).__name__}")
        return False


def autostart_enabled() -> bool:
    """True when the widget starts at sign-in (HKCU Run value, or the Linux .desktop file)."""
    if not IS_WINDOWS:
        return linux_autostart_enabled()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
        ) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """Turn "start at sign-in" on or off (HKCU Run value, or the Linux .desktop file)."""
    if not IS_WINDOWS:
        return set_linux_autostart(enabled)
    if winreg is None:
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as exc:
        log_line(f"autostart update failed: {type(exc).__name__}")
        return False


def show_settings(widget: "UsageMonitorWidget", worker: "CollectorWorker"):
    """Open the shared Settings window; any provider change triggers a fresh poll."""
    import settings_window

    window = settings_window.open_settings(None)
    if not getattr(window, "_usage_monitor_wired", False):
        def _changed() -> None:
            widget._provider_count = provider_count()
            widget.update()
            QtCore.QMetaObject.invokeMethod(worker, "force_poll", Qt.QueuedConnection)

        window.providers_changed.connect(_changed)
        window._usage_monitor_wired = True
    return window


AUTOSTART_LABEL = "Start with Windows" if IS_WINDOWS else "Start when I sign in"


def build_menu(
    widget: UsageMonitorWidget,
    worker: CollectorWorker,
    stop_callback: Callable[[], None],
) -> QtWidgets.QMenu:
    """Build the menu: Settings / Move / Lock / Refresh / Opacity / autostart / Quit.

    The tray icon shows it, and on Linux a right-click on the card shows it too
    (some desktops have no tray).
    """
    menu = QtWidgets.QMenu()

    settings_action = menu.addAction("Settings...")
    settings_action.triggered.connect(lambda: show_settings(widget, worker))
    menu.addSeparator()

    move_action = menu.addAction("Move widget")
    move_action.setCheckable(True)
    move_action.setChecked(move_mode(widget.config))

    lock_action = menu.addAction("Lock position (pin to desktop)")
    lock_action.setCheckable(True)
    lock_action.setChecked(bool(widget.config.get("locked", True)))

    def sync_lock(locked: bool) -> None:
        widget.config["locked"] = locked
        save_config(widget.config)
        move_action.setChecked(not locked)
        lock_action.setChecked(locked)
        if locked:
            widget.setWindowFlags(
                Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
            )
            widget.show()
            widget._region_size = None
            widget.pin_to_desktop()
        else:
            widget.unpin_from_desktop()
        widget.update()

    move_action.triggered.connect(lambda checked: sync_lock(not checked))
    lock_action.triggered.connect(lambda checked: sync_lock(checked))

    refresh_action = menu.addAction("Refresh usage now")
    refresh_action.triggered.connect(
        lambda: QtCore.QMetaObject.invokeMethod(worker, "force_poll", Qt.QueuedConnection)
    )

    menu.addSeparator()
    opacity_menu = menu.addMenu("Opacity")
    opacity_group = QtGui.QActionGroup(opacity_menu)
    opacity_group.setExclusive(True)
    current_op = int(widget.config.get("opacity", 75))
    for pct in (60, 75, 90):
        act = opacity_menu.addAction(f"{pct}%")
        act.setCheckable(True)
        act.setChecked(abs(current_op - pct) <= 7)

        def _set_op(_checked: bool = False, value: int = pct) -> None:
            widget.config["opacity"] = value
            save_config(widget.config)
            widget.update()

        act.triggered.connect(_set_op)
        opacity_group.addAction(act)

    menu.addSeparator()
    autostart_action = menu.addAction(AUTOSTART_LABEL)
    autostart_action.setCheckable(True)
    autostart_action.setChecked(autostart_enabled())

    def _toggle_autostart(checked: bool) -> None:
        if set_autostart(checked):
            widget.config["autostart"] = checked
            save_config(widget.config)
        autostart_action.setChecked(autostart_enabled())

    autostart_action.triggered.connect(_toggle_autostart)

    menu.addSeparator()
    quit_action = menu.addAction("Quit")
    quit_action.triggered.connect(stop_callback)
    return menu


def build_tray(
    app: QtWidgets.QApplication,
    widget: UsageMonitorWidget,
    worker: CollectorWorker,
    stop_callback: Callable[[], None],
    menu: QtWidgets.QMenu | None = None,
) -> QSystemTrayIcon:
    """Build the tray icon and give it the menu from ``build_menu``."""
    tray = QSystemTrayIcon(make_tray_icon(), app)
    tray.setToolTip("Usage Monitor")
    menu = menu or build_menu(widget, worker, stop_callback)
    tray.setContextMenu(menu)
    tray._menu = menu  # the tray does not own the menu: keep it alive
    tray.show()
    return tray


# ---------------------------------------------------------------- selftest & main
def run_selftest() -> int:
    """Fetch rows, print every provider/model row, and save a picture of the card."""
    if IS_WINDOWS and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # The offscreen platform loads no system fonts on Windows, so every glyph
        # would render as a box in the test PNG (same fix as Desktop Monitor).
        os.environ.setdefault("QT_QPA_FONTDIR",
                              os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    set_process_dpi_awareness()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    config = load_config()
    rows, any_stale, stamp = collect_rows(force=True)
    credits = build_credit_rows(rows)

    print(f"=== Usage Monitor Selftest ({stamp}, stale={any_stale}) ===")
    for r in rows:
        pct_str = f"{r.percent_used:5.1f}%" if r.percent_used is not None else "no data"
        note = r.display_reset_Or_reason()
        print(
            f"[{r.provider_label:<16}] {r.model_label:<32} | {pct_str:>7} | {r.status:<7} | {note}"
        )
    if not rows:
        print("No providers configured (first run): the card shows 'Add a provider'.")

    print("\n=== Credits Tab ===")
    for c in credits:
        bal_str = f"${c.balance_usd:.2f}" if c.balance_usd is not None else "n/a"
        spent_str = f"${c.spent_usd:.2f}" if c.spent_usd is not None else "n/a"
        print(
            f"[{c.provider_label:<20}] bal={bal_str:<8} spent={spent_str:<8} | {c.detail_text}"
        )

    widget = UsageMonitorWidget(config, initial_rows=rows)
    widget.on_snapshot(rows, any_stale, stamp)
    pixmap = widget.grab()
    out_path = APP_DIR / "selftest_card.png"
    pixmap.save(str(out_path), "PNG")
    print(f"\nSaved card image to: {out_path}")
    return 0


def lock_file_path() -> Path:
    """``$XDG_RUNTIME_DIR/desktopwidgets-usage.lock`` (the config folder when there is no runtime dir)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime and os.path.isabs(runtime) and os.path.isdir(runtime):
        return Path(runtime) / LOCK_FILE_NAME
    return provider_config.data_dir() / LOCK_FILE_NAME


def acquire_instance_lock(path: Path | None = None):
    """Take the Linux single-instance lock. Returns the open file, or None if another copy holds it.

    The lock lasts as long as the returned file stays open, and the system
    drops it by itself if the app crashes, so there is never a stale lock.
    """
    import fcntl  # Linux/macOS only, so it is imported here and never on Windows

    target = Path(path) if path is not None else lock_file_path()
    handle = open(target, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def ensure_single_instance():
    """Ensure only one instance of Usage Monitor runs at a time.

    Windows: a local socket server. Linux: an ``flock`` lock file. Returns an
    object that must be kept alive for the whole run, or None for a duplicate.
    """
    if not IS_WINDOWS:
        try:
            return acquire_instance_lock()
        except OSError as exc:
            log_line(f"single-instance lock failed: {type(exc).__name__}")
            return None
    probe = QLocalSocket()
    probe.connectToServer(SINGLE_INSTANCE_KEY)
    if probe.waitForConnected(250):
        probe.disconnectFromServer()
        return None
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    server = QLocalServer()
    if not server.listen(SINGLE_INSTANCE_KEY):
        return None
    return server


def purge_secrets() -> int:
    """Delete every API key this app saved in the key store (used on uninstall).

    The ids in ``config.json`` are not enough: the config can be missing or
    broken, and then it does not mention the ids the key store really holds.
    So the key store itself is asked which entries it has for our service and
    every one of them is deleted.

    Returns the number of keys that were really removed, or -1 when the app
    cannot promise that nothing is left behind. The caller turns that into a
    failing exit code, so a purge that did not work never looks like a
    success.
    """
    removed = 0
    confirmed = True
    if not secrets_store.no_key_store():
        # The ids the config knows about (on Windows this is the only list there is).
        config_ids: list[str] = []
        try:
            cfg = provider_config.load_config()
            config_ids = [inst.instance_id for inst in provider_config.list_instances(cfg)]
        except Exception:  # noqa: BLE001
            confirmed = False

        # The ids the key store really holds for our service.
        listed = secrets_store.list_service_instance_ids()
        if listed is None:
            if not IS_WINDOWS:
                # The store could not be listed, so we cannot promise that
                # nothing is left behind.
                confirmed = False
        else:
            config_ids = list(dict.fromkeys(config_ids + listed))

        for instance_id in config_ids:
            try:
                if secrets_store.delete_secret(instance_id):
                    removed += 1
            except Exception:  # noqa: BLE001 - one failure must not stop the rest
                confirmed = False

    if confirmed:
        print(f"Removed {removed} saved provider key(s) from the key store.")
    else:
        print(
            "Could not confirm that every provider key was removed; "
            "delete them in your key store (Passwords and Keys)."
        )
    return removed if confirmed else -1


def main() -> int:
    if "--selftest" in sys.argv:
        return run_selftest()
    if "--purge-secrets" in sys.argv:
        removed = purge_secrets()
        # A negative number means "not sure": the exit code must not be 0 then.
        return 0 if removed >= 0 else 1

    set_process_dpi_awareness()
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    instance_server = ensure_single_instance()
    if instance_server is None:
        log_line("Usage Monitor is already running; exiting duplicate instance.")
        return 0

    config = load_config()
    save_config(config)
    # The first poll runs on the worker thread so a slow provider never freezes startup.
    widget = UsageMonitorWidget(config, initial_rows=[])
    widget.show()

    repin_timer = QTimer()
    drag_hook = None
    if IS_WINDOWS:
        # Desktop pinning and the click-through mouse hook only exist on Windows.
        if not move_mode(config):
            QTimer.singleShot(150, widget.pin_to_desktop)
        repin_timer.setInterval(5000)
        repin_timer.timeout.connect(widget.repin_if_needed)
        repin_timer.start()
        drag_hook = DesktopDragHook(widget)
        drag_hook.start()

    thread = QThread()
    worker = CollectorWorker(interval_ms=300_000)
    worker.moveToThread(thread)
    thread.started.connect(worker.start)
    worker.snapshot_ready.connect(widget.on_snapshot)
    widget.refresh_requested.connect(worker.force_poll)  # queued: runs on the worker thread
    widget.settings_requested.connect(lambda: show_settings(widget, worker))
    thread.start()

    tray = None

    def shutdown() -> None:
        if drag_hook is not None:
            drag_hook.stop()
        repin_timer.stop()
        QtCore.QMetaObject.invokeMethod(
            worker, "stop", Qt.BlockingQueuedConnection
        )
        thread.quit()
        thread.wait(2000)
        if tray is not None:
            tray.hide()
        widget.close()
        app.quit()

    menu = build_menu(widget, worker, shutdown)
    widget.context_menu = menu  # right-click on the card (used on Linux)
    if IS_WINDOWS or QSystemTrayIcon.isSystemTrayAvailable():
        tray = build_tray(app, widget, worker, shutdown, menu)
    else:
        # GNOME on Wayland has no tray by default: the gear and right-click menu still work.
        log_line("No system tray on this desktop; use the gear or right-click the card.")
    app._instance_server = instance_server  # keep reference alive
    app._tray = tray
    app._menu = menu
    if "--settings" in sys.argv:
        QTimer.singleShot(200, lambda: show_settings(widget, worker))
    log_line(f"Usage Monitor started at {widget._pos} ({widget._w}x{widget._height})")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
