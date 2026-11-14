"""
Snap the To-Do Widget (and its companions) next to each other
=============================================================

Used by ``todo_manager.py widget --snap`` and by the Ctrl+F12 hotkey inside the widget.

The To-Do card is snapped to the bottom-right corner of the screen it is on. If the optional
companion widgets are running (windows titled "Usage Monitor" and "Desktop Monitor"), they are
lined up in a row to its left, bottom-aligned, with a small gap between cards. Every position
is computed from the screen's work area (the screen minus the taskbar) and each window's own
size, so nothing depends on a particular monitor layout.

This works through the Win32 API, which is all a separate script can use to move another
program's window.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os

import paths

# Match the widget's per-monitor DPI awareness so SetWindowPos takes physical pixels.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOSIZE = 0x0001
MONITOR_DEFAULTTONEAREST = 2

TODO_TITLE = "To-Do Widget"
# Companion widgets, in the order they are laid out to the left of the To-Do card.
COMPANION_TITLES = ("Desktop Monitor", "Usage Monitor")
GAP = 8  # pixels between neighbouring cards


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _find_windows(titles: tuple[str, ...]) -> dict[str, int]:
    """Returns {title: window handle} for every visible window whose title matches exactly."""
    found: dict[str, int] = {}

    def visit(hwnd, _extra) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value in titles and buffer.value not in found:
                found[buffer.value] = hwnd
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return found


def _work_area(hwnd: int) -> wintypes.RECT:
    """Returns the usable area (taskbar excluded) of the monitor holding hwnd."""
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    return info.rcWork


def _window_size(hwnd: int) -> tuple[int, int]:
    """Returns (width, height) of a window in pixels."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def plan_row(area: tuple[int, int, int, int], sizes: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Works out where each card goes. Pure arithmetic, so it is easy to test.

    ``area`` is (left, top, right, bottom) of the work area. ``sizes`` lists (width, height)
    of the cards from the rightmost card to the leftmost. The cards sit in one row, flush
    with the bottom-right corner, GAP pixels apart, and never leave the left or top edge.
    Returns the (x, y) of each card in the same order.
    """
    left, top, right, bottom = area
    positions: list[tuple[int, int]] = []
    cursor = right
    for width, height in sizes:
        x = max(left, cursor - width)
        y = max(top, bottom - height)
        positions.append((x, y))
        cursor = x - GAP
    return positions


def _save_position(x: int, y: int) -> None:
    """Stores the snapped position so the widget opens there next time."""
    config_path = paths.config_file()
    data: dict = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as file_handle:
                loaded = json.load(file_handle)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
    data["x"] = x
    data["y"] = y
    with open(config_path, "w", encoding="utf-8") as file_handle:
        json.dump(data, file_handle, indent=2)


def snap_widget_to_edge(save_position: bool = True) -> bool:
    """Snaps the running To-Do card (plus any companions) into a row. Returns success.

    Pass ``save_position=False`` when the widget itself calls this: it saves its own
    position in its own coordinates a moment later.
    """
    windows = _find_windows((TODO_TITLE,) + COMPANION_TITLES)
    todo = windows.get(TODO_TITLE)
    if todo is None:
        return False

    area_rect = _work_area(todo)
    area = (area_rect.left, area_rect.top, area_rect.right, area_rect.bottom)
    ordered = [todo] + [windows[title] for title in COMPANION_TITLES if title in windows]
    positions = plan_row(area, [_window_size(hwnd) for hwnd in ordered])

    for hwnd, (x, y) in zip(ordered, positions):
        user32.SetWindowPos(hwnd, 0, x, y, 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
    if save_position:
        _save_position(*positions[0])
    return True


def main() -> None:
    if snap_widget_to_edge():
        print("Widgets snapped to the screen edge.")
    else:
        print("To-Do Widget is not running.")


if __name__ == "__main__":
    main()
