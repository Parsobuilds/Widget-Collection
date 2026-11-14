"""
Desktop To-Do List Widget
=========================

A sleek, modern frosted-glass desktop widget built in Python with PySide6, with a task
list and a Goals tab.

The to-do list is the plain-text file '%APPDATA%\\DesktopWidgets\\TodoWidget\\todo\\To Do List.txt',
read and written through TodoListManager. The goals live beside it in 'Goals.json'.

Key Features & Educational Highlights:
- Frosted glass effect using WallpaperGlass and paint_card from glass_theme.py.
- Smooth anti-aliased Segoe UI Variable typography with soft drop shadows (no crunchy halos).
- Full 8-direction edge & corner window resizing with dynamic task text reflow.
- Drag-to-reorder: grab any task row and drag it up or down; a cyan insertion
  line shows the exact slot, the list renumbers itself, and the file is rewritten
  in the new order. Edge auto-scroll keeps long lists draggable.
- System Tray Icon with quick actions (Show/Hide, Notepad, Snap, Pin, Opacity,
  Start with Windows, Quit).
- Sleek pill buttons (Tasks, Goals, Notepad, Pin, Close, Add, Clear).
- Multi-monitor aware: accurately positions across negative coordinate displays.
- Persistent state: remembers screen position, size, tab and pin status in
  '%APPDATA%\\DesktopWidgets\\TodoWidget\\widget_config.json'.
- File polling: automatically refreshes when tasks are updated externally via
  Notepad or another program.
- Global snap hotkey: Ctrl+F12 snaps the To-Do card to the bottom-right corner of its screen
  and lines up any running Usage Monitor / Desktop Monitor cards beside it.
- Goals tab: the "Goals" pill swaps the task list for SMART goals ('Goals.json').
  All of that tab's code lives in goals_tab.py; this file only creates the panel and
  switches between the two tabs.
- Test mode: --todo-file, --goals-file and --config point the widget at other
  files (with backups beside them) so it can be tried without touching the real data.
- --selftest builds the widget without showing it, saves pictures of both tabs into the
  data folder and exits (used to check an installed copy).
- Only one copy runs at a time: starting a second one just focuses the first.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

# Guard against None std streams under pythonw or DETACHED_PROCESS on Windows
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

# Make sure the modules next to this file can be imported however the app is started.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QRect, QSize, Signal, QTimer
from PySide6.QtGui import (
    QPainter,
    QColor,
    QFont,
    QPen,
    QBrush,
    QCursor,
    QFontMetrics,
    QPainterPath,
    QKeyEvent,
    QMouseEvent,
)

import glass_theme
import paths
import setup_startup
from todo_manager import TodoListManager, TodoItem, DEFAULT_TODO_PATH
from goals_manager import GoalsManager, DEFAULT_GOALS_PATH, backup_path_for
from goals_tab import GoalsPanel

# Config path for persistent widget state (in the per-user data folder).
CONFIG_DIR = paths.data_dir()
CONFIG_PATH = paths.config_file()

# Defaults for a first run. The window position is not listed here: when no position has
# been saved, the widget opens in the bottom-right corner of the primary screen.
DEFAULT_CONFIG = {
    "w": 320,
    "h": 448,
    "always_on_top": True,
    "opacity": glass_theme.DEFAULT_TINT_OPACITY,
    "tab": "tasks",  # which tab was open last time: "tasks" or "goals"
}

TAB_NAMES = ("tasks", "goals")

# Edge grab margins in pixels:
# The window is frameless and translucent with a soft outer glow. The visible card
# border is painted at glass_theme.GLOW (8 px) inside the window boundary.
# Card content (pills, task list, goals) starts at glass_theme.EDGE_PAD (22 px).
#
# - Left edge grab: x <= 18 (covers glow 0-8 and outer border 8-18, clear of content at x >= 22).
# - Right edge grab: x >= w - 18 (covers visible border at w - 8, safely outside the 5px
#   task-list scrollbar which sits at [w - 27, w - 22]).
# - Bottom edge grab: y >= h - 20 (covers the bottom card border at h - 8, below footer text).
# - Top edge grab: y <= 8 (the very top card boundary; clicks at y > 8 in the header
#   drag the window instead of resizing height).
# - Corner grip: x >= w - 26 and y >= h - 26 (covers the 3 diagonal tick marks drawn at w - 18, h - 18).
# - Header drag: y <= 75 (entire top header area including empty space and title row).
RESIZE_MARGIN_LEFT = 18
RESIZE_MARGIN_RIGHT = 18
RESIZE_MARGIN_BOTTOM = 20
RESIZE_MARGIN_TOP = 8
CORNER_GRIP_MARGIN = 26
HEADER_DRAG_MAX_Y = 75

# Backward compatibility alias for any caller expecting RESIZE_MARGIN
RESIZE_MARGIN = RESIZE_MARGIN_LEFT
VERTICAL_RESIZE_MARGIN = RESIZE_MARGIN_BOTTOM

# Window title (also what the snap helper looks for) and the names shared by the whole suite.
WINDOW_TITLE = "To-Do Widget"

# Window size constraints in logical pixels
MIN_WIDTH, MIN_HEIGHT = 320, 380
MAX_WIDTH, MAX_HEIGHT = 750, 1100


# ----------------------------------------------------------------------------------------
# Start with Windows (HKCU Run entry), first-run sample list, single instance
# ----------------------------------------------------------------------------------------
RUN_KEY_PATH = r"Software" + chr(92) + r"Microsoft" + chr(92) + r"Windows" + chr(92) + r"CurrentVersion" + chr(92) + r"Run"


def startup_command() -> str:
    """The command line stored in the Run entry: the exe itself when frozen, else pythonw."""
    if paths.is_frozen():
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = "pythonw.exe"
    return f'"{pythonw}" "{os.path.abspath(__file__)}"'


def is_startup_enabled() -> bool:
    """True when the widget starts at sign-in (Run value on Windows, autostart file on Linux)."""
    if paths.is_linux():
        return setup_startup.linux_autostart_enabled()
    try:
        import winreg  # imported here so the module also loads on non-Windows systems

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, paths.RUN_VALUE_NAME)
        return True
    except (ImportError, OSError):
        return False


def set_startup_enabled(enabled: bool) -> bool:
    """Writes or deletes the Run value / autostart file. Returns True when the change succeeded."""
    if paths.is_linux():
        return setup_startup.linux_set_autostart(enabled)
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            if enabled:
                winreg.SetValueEx(key, paths.RUN_VALUE_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, paths.RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except (ImportError, OSError):
        return False


def seed_first_run_files(todo_path: str) -> None:
    """On a first run, copies the bundled example list so the widget is not blank.

    Only does anything when the user's list file does not exist yet, so it never
    overwrites real data.
    """
    if os.path.exists(todo_path):
        return
    sample = paths.resource_path("todo", "To Do List.txt")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(todo_path)), exist_ok=True)
        if os.path.isfile(sample):
            shutil.copyfile(sample, todo_path)
    except OSError:
        pass


_single_instance_mutex = None  # kept alive for the whole run so Windows holds the mutex
_single_instance_lock = None  # Linux: the open lock file; the lock lasts as long as it stays open


def acquire_linux_lock() -> bool:
    """Takes the Linux single-instance lock. Returns False when another copy already runs.

    ``fcntl.flock`` locks the file for as long as the process keeps it open, and the system
    drops the lock by itself if the program crashes, so a stale lock can never block a restart.
    """
    global _single_instance_lock
    import fcntl  # Linux only, so it is imported here

    try:
        handle = open(paths.lock_file(), "a+")
    except OSError:
        return True  # cannot even create the lock file: run anyway rather than refuse to start
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _single_instance_lock = handle
    return True


def _focus_existing_widget(title: str) -> None:
    """Brings the already-running widget window to the front (Windows only)."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    found: list[int] = []

    def visit(hwnd, _extra) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == title:
                found.append(hwnd)
                return False
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    for hwnd in found:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


def run_selftest(widget: "DesktopTodoWidget") -> int:
    """Saves a picture of each tab into the data folder without showing a window."""
    app = QtWidgets.QApplication.instance()
    widget.show_tab("tasks", save=False)
    app.processEvents()
    tasks_png = os.path.join(paths.data_dir(), "selftest_card.png")
    goals_png = os.path.join(paths.data_dir(), "selftest_card_goals.png")
    if not widget.grab().save(tasks_png):
        print("Could not save " + tasks_png)
        return 1
    widget.show_tab("goals", save=False)
    app.processEvents()
    if not widget.grab().save(goals_png):
        print("Could not save " + goals_png)
        return 1
    print(tasks_png)
    print(goals_png)
    return 0


# ----------------------------------------------------------------------------------------
# Tray Icon Generator
# ----------------------------------------------------------------------------------------
def make_todo_tray_icon() -> QtGui.QIcon:
    """Draws a crisp 32x32 vector tray icon with cyan border and emerald checkmark."""
    # Check if custom ico exists
    ico_path = paths.resource_path("todo_icon.ico")
    if os.path.exists(ico_path):
        return QtGui.QIcon(ico_path)

    image = QtGui.QImage(32, 32, QtGui.QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Dark rounded square
    painter.setBrush(QColor("#101218"))
    painter.setPen(QPen(QColor(glass_theme.ACCENT), 1.5))
    painter.drawRoundedRect(QRectF(2, 2, 28, 28), 6, 6)

    # Checkmark in emerald
    painter.setPen(QPen(QColor(glass_theme.EMERALD), 2.2))
    path = QPainterPath()
    path.moveTo(7, 16)
    path.lineTo(12, 21)
    path.lineTo(25, 9)
    painter.drawPath(path)

    # Checklist line in muted grey
    painter.setPen(QPen(QColor(glass_theme.C_LABEL), 1.8))
    painter.drawLine(8, 25, 24, 25)
    painter.end()
    return QtGui.QIcon(QtGui.QPixmap.fromImage(image))


# ----------------------------------------------------------------------------------------
# Smooth Pill Button Widget (Title Case, normal kerning, clean hover)
# ----------------------------------------------------------------------------------------
class PillButton(QtWidgets.QPushButton):
    """Pill-shaped push button with smooth hover glow and natural font kerning."""

    def __init__(
        self,
        text: str,
        active: bool = False,
        color: str = glass_theme.ACCENT,
        h: int = 22,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._active = active
        self._color = color
        self._hovered = False
        self._h = h
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(h)
        # Use clean font with normal letter spacing so text looks natural
        font = glass_theme.qt_font(8.5, bold=True)
        self.setFont(font)
        metrics = QFontMetrics(font)
        self.setFixedWidth(metrics.horizontalAdvance(text) + 20)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def setActive(self, active: bool) -> None:
        self._active = active
        self.update()

    def isActive(self) -> bool:
        return self._active

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = rect.height() / 2
        tint = QColor(self._color)

        if self._active:
            fill = QColor(tint)
            fill.setAlpha(95 if self._hovered else 70)
            border = QColor(tint)
            border.setAlpha(220)
            text_color = "#FFFFFF"
        else:
            fill = QColor(255, 255, 255, 26 if self._hovered else 14)
            border = QColor(tint if self._hovered else "#67E8F9")
            border.setAlpha(150 if self._hovered else 65)
            text_color = "#FFFFFF" if self._hovered else glass_theme.C_TEXT2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)

        metrics = self.fontMetrics()
        baseline = glass_theme.centred_baseline(rect.top(), rect.height(), metrics)
        tw = metrics.horizontalAdvance(self.text())
        x = int(rect.center().x() - tw / 2)

        if paths.is_linux() and self.text() == "✕":
            # Paint the cross as two lines: no font has to supply the glyph.
            glass_theme.draw_cross(painter, rect.center(), 8, QColor(text_color))
            painter.end()
            return

        # Soft 1px text shadow + crisp text
        painter.setPen(QColor(0, 0, 0, 160))
        painter.drawText(x, int(baseline) + 1, self.text())
        painter.setPen(QColor(text_color))
        painter.drawText(x, int(baseline), self.text())
        painter.end()


# ----------------------------------------------------------------------------------------
# Task Item Row Widget (Clean, silky-smooth typography, card outline)
# ----------------------------------------------------------------------------------------
class TaskItemWidget(QtWidgets.QWidget):
    """Interactive task row with circular checkbox, smooth text with soft shadow, and delete.

    The row doubles as a drag handle: press and drag it upwards or downwards to
    reposition it, proving a before/after drop marker. A short press without
    movement still toggles the task, and the ✕ still deletes it.
    """

    toggled = Signal(int)
    deleted = Signal(int)
    drop_committed = Signal(int)  # target insertion slot (0-based)

    def __init__(
        self,
        item: TodoItem,
        position: int = 0,
        content_width: int = 330,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self.position = position  # 0-based slot in the visible list
        self.owner: DesktopTodoWidget | None = None
        self._hovered = False
        self._del_hovered = False
        self._press_global: QPoint = QPoint()
        self._press_local: QPoint = QPoint()
        self._may_drag = False
        self._press_on_delete = False
        self._drag_source_active = False
        self._drop_marker = 0  # 0 = none, 1 = insert above, 2 = insert below
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAcceptDrops(True)
        self.setToolTip("Click to tick off · Drag up or down to reorder")
        self._calc_size(content_width)

    def _calc_size(self, width: int) -> None:
        self._text_font = glass_theme.qt_font(9.5, strike_out=self.item.is_done)
        metrics = QFontMetrics(self._text_font)
        self._task_label = f"{self.item.index}. {self.item.text}"
        self._text_max_w = max(100, width - 66)
        dummy_rect = QRect(0, 0, int(self._text_max_w), 9999)
        self._bounds = metrics.boundingRect(dummy_rect, Qt.TextFlag.TextWordWrap, self._task_label)
        row_h = max(36, self._bounds.height() + 16)
        self.setFixedHeight(row_h)

    def update_width(self, width: int) -> None:
        """Dynamically recalculates text wrap height on window resize."""
        self._calc_size(width)
        self.update()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = False
        self._del_hovered = False
        self.update()
        super().leaveEvent(event)

    def _delete_rect(self) -> QRectF:
        """Hover / click target for the ✕ delete button."""
        return QRectF(self.width() - 28, (self.height() - 22) / 2, 22, 22)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        is_del = self._delete_rect().contains(event.position())
        if is_del != self._del_hovered:
            self._del_hovered = is_del
            self.setCursor(Qt.CursorShape.PointingHandCursor if is_del else Qt.CursorShape.OpenHandCursor)
            self.update()

        # Start a drag once the pointer has travelled past the system threshold
        if self._may_drag and event.buttons() & Qt.MouseButton.LeftButton:
            travelled = (event.globalPosition().toPoint() - self._press_global).manhattanLength()
            if travelled >= QtWidgets.QApplication.startDragDistance():
                self._may_drag = False
                self._start_drag()
                return

        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_on_delete = self._delete_rect().contains(event.position())
            self._press_global = event.globalPosition().toPoint()
            self._press_local = event.position().toPoint()
            self._may_drag = not self._press_on_delete
            # Accepting gives this row the implicit mouse grab, so moves and the
            # release keep coming here instead of bubbling to the window.
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            still_inside = self.rect().contains(event.position().toPoint())
            if self._press_on_delete:
                if self._delete_rect().contains(event.position()):
                    self.deleted.emit(self.item.index)
            elif self._may_drag and still_inside:
                # A plain click (no drag) toggles the checkbox
                self.toggled.emit(self.item.index)
            self._may_drag = False
            self._press_on_delete = False
        event.accept()

    # ------------------------------------------------------------------------------------
    # Drag source
    # ------------------------------------------------------------------------------------
    def _start_drag(self) -> None:
        """Lifts this row into a translucent drag pixmap."""
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        mime.setText(str(self.position))
        drag.setMimeData(mime)

        pixmap = self.grab()
        veil = QPainter(pixmap)
        veil.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        veil.fillRect(pixmap.rect(), QColor(255, 255, 255, 205))
        veil.end()
        drag.setPixmap(pixmap)
        drag.setHotSpot(self._press_local)

        self._drag_source_active = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()
        if self.owner is not None:
            self.owner.begin_drag(self)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._drag_source_active = False
            self.unsetCursor()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            if self.owner is not None:
                self.owner.end_drag()

    # ------------------------------------------------------------------------------------
    # Drop target
    # ------------------------------------------------------------------------------------
    def _preview_at(self, y: float, owner: DesktopTodoWidget) -> None:
        """Shows the insertion line above or below depending on the pointer half."""
        before = y < self.height() / 2
        self._drop_marker = 1 if before else 2
        self.update()
        owner.update_autoscroll(self.mapToGlobal(QPoint(0, int(y))))

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasText() and self.owner is not None:
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasText() and self.owner is not None:
            self.owner.show_row_marker(self, event.position().y() < self.height() / 2)
            self._preview_at(event.position().y(), self.owner)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self._drop_marker = 0
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if not event.mimeData().hasText():
            return
        slot = self.position if event.position().y() < self.height() / 2 else self.position + 1
        event.acceptProposedAction()
        self.drop_committed.emit(slot)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self._drag_source_active:
            painter.setOpacity(0.4)

        # Subtle card row background
        card_rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        if self._hovered:
            bg = QColor(34, 211, 238, 18)
            border = QColor(103, 232, 249, 70)
        else:
            bg = QColor(255, 255, 255, 8)
            border = QColor(255, 255, 255, 12)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(card_rect, 7, 7)

        # Checkbox Circle
        box_y = (self.height() - 18) / 2
        box_rect = QRectF(8, box_y, 18, 18)
        if self.item.is_done:
            painter.setPen(QPen(QColor(glass_theme.EMERALD), 1.5))
            painter.setBrush(QColor(52, 211, 153, 50))
            painter.drawEllipse(box_rect)
            font = glass_theme.qt_font(9, bold=True)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            base = glass_theme.centred_baseline(box_rect.top(), box_rect.height(), metrics)
            painter.setPen(QColor(glass_theme.EMERALD))
            if paths.is_linux():
                glass_theme.draw_check(painter, box_rect.center(), 9, QColor(glass_theme.EMERALD))
            else:
                painter.drawText(
                    int(box_rect.center().x() - metrics.horizontalAdvance("✔") / 2),
                    int(base),
                    "✔",
                )
        else:
            ring_color = QColor(glass_theme.EDGE if self._hovered else glass_theme.C_LABEL)
            ring_color.setAlpha(220 if self._hovered else 130)
            painter.setPen(QPen(ring_color, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(box_rect)

        # Task Text (smooth, anti-aliased with 1px soft drop shadow - NO crunchy halo)
        painter.setFont(self._text_font)
        text_color = glass_theme.C_FAINT if self.item.is_done else glass_theme.C_VALUE
        text_y = (self.height() - self._bounds.height()) / 2
        draw_rect = QRectF(34, text_y, self._text_max_w, self._bounds.height() + 4)

        # Soft 1px shadow below
        painter.setPen(QColor(0, 0, 0, 180))
        painter.drawText(draw_rect.translated(0, 1), Qt.TextFlag.TextWordWrap, self._task_label)
        # Crisp foreground text
        painter.setPen(QColor(text_color))
        painter.drawText(draw_rect, Qt.TextFlag.TextWordWrap, self._task_label)

        # Delete icon (✕)
        del_font = glass_theme.qt_font(9, bold=True)
        painter.setFont(del_font)
        del_metrics = QFontMetrics(del_font)
        del_base = glass_theme.centred_baseline(0, self.height(), del_metrics)
        del_color = glass_theme.RED if self._del_hovered else (glass_theme.C_LABEL if self._hovered else "#5A6578")
        painter.setPen(QColor(del_color))
        if paths.is_linux():
            glass_theme.draw_cross(painter, QPointF(self.width() - 15, self.height() / 2), 8, QColor(del_color))
        else:
            painter.drawText(self.width() - 20, int(del_base), "✕")

        # Cyan insertion marker while another row hovers this one
        if self._drop_marker:
            painter.setOpacity(1.0)
            y = 0.5 if self._drop_marker == 1 else self.height() - 1.5
            pen = QPen(QColor(glass_theme.ACCENT), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(7, y), QPointF(self.width() - 7, y))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(glass_theme.ACCENT))
            painter.drawEllipse(QPointF(5.5, y), 2.4, 2.4)
            painter.drawEllipse(QPointF(self.width() - 5.5, y), 2.4, 2.4)

        painter.end()


# ----------------------------------------------------------------------------------------
# Task List Container (accepts drops in the gaps and empty space below the rows)
# ----------------------------------------------------------------------------------------
class TaskListContainer(QtWidgets.QWidget):
    """Transparent list body that routes drag-and-drop to the owning widget."""

    def __init__(self, owner: "DesktopTodoWidget", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.owner = owner
        self._end_marker = False
        self.setAcceptDrops(True)
        self.setStyleSheet("background: transparent;")

    def rows(self) -> list[TaskItemWidget]:
        """Returns task rows in visual (layout) order."""
        found: list[TaskItemWidget] = []
        layout = self.layout()
        if layout is None:
            return found
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget() if layout.itemAt(i) else None
            if isinstance(widget, TaskItemWidget):
                found.append(widget)
        return found

    def slot_at(self, y: float) -> int:
        """Maps a local y coordinate to an insertion slot (0..row count)."""
        rows = self.rows()
        for row in rows:
            if y < row.geometry().center().y():
                return row.position
        return len(rows)

    def set_end_marker(self, active: bool) -> None:
        if active != self._end_marker:
            self._end_marker = active
            self.update()

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if not event.mimeData().hasText():
            return
        self.owner.show_slot_marker(self.slot_at(event.position().y()))
        self.owner.update_autoscroll(self.mapToGlobal(event.position().toPoint()))
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self.owner.clear_markers()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if not event.mimeData().hasText():
            return
        event.acceptProposedAction()
        self.owner.commit_reorder(self.slot_at(event.position().y()))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if not self._end_marker:
            return
        rows = self.rows()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (rows[-1].geometry().bottom() + 4) if rows else 6
        pen = QPen(QColor(glass_theme.ACCENT), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(7, y), QPointF(self.width() - 7, y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(glass_theme.ACCENT))
        painter.drawEllipse(QPointF(5.5, y), 2.4, 2.4)
        painter.drawEllipse(QPointF(self.width() - 5.5, y), 2.4, 2.4)
        painter.end()


# ----------------------------------------------------------------------------------------
# Main To-Do List Widget Window
# ----------------------------------------------------------------------------------------
class DesktopTodoWidget(QtWidgets.QWidget):
    """Frosted-glass desktop to-do widget with live sync, tray icon, and edge resizing."""

    def __init__(
        self,
        todo_path: str = DEFAULT_TODO_PATH,
        goals_path: str = DEFAULT_GOALS_PATH,
        config_path: str = CONFIG_PATH,
    ) -> None:
        super().__init__()

        # The real files back up into the data folder; any other file
        # (a test copy) backs up beside itself, so testing never spoils undo.
        self.manager = TodoListManager(
            file_path=todo_path,
            backup_path=backup_path_for(todo_path, DEFAULT_TODO_PATH, "To Do List.txt.bak"),
        )
        self.goals_manager = GoalsManager(
            goals_path, backup_path_for(goals_path, DEFAULT_GOALS_PATH, "Goals.json.bak")
        )
        self.config_path = config_path
        self.glass = glass_theme.WallpaperGlass()
        self.config = self._load_config()
        self.tint_opacity = float(self.config.get("opacity", glass_theme.DEFAULT_TINT_OPACITY))
        self.last_mtime: float = 0.0
        self.is_always_on_top: bool = self.config.get("always_on_top", True)
        self._drag_pos: QPoint | None = None
        self._resizing_edges: tuple[bool, bool, bool, bool] | None = None
        self._resize_start_pos: QPoint = QPoint()
        self._resize_start_geo: QRect = QRect()
        self._drag_from_index: int | None = None  # file item number being dragged
        self._autoscroll_dir: int = 0

        # Frameless, translucent desktop tool window (hidden from Windows taskbar, tray-only)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.is_always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setWindowTitle(WINDOW_TITLE)

        # Set taskbar / window icon
        self.tray = None  # set properly by _build_tray_icon
        self.tray_menu = None
        self.tray_icon_obj = make_todo_tray_icon()
        self.setWindowIcon(self.tray_icon_obj)

        self._init_ui()
        self._build_tray_icon()
        self.refresh_list()

        # Size & Position (applied after layout and widgets are built)
        w = int(self.config.get("w", DEFAULT_CONFIG["w"]))
        h = int(self.config.get("h", DEFAULT_CONFIG["h"]))
        w = max(MIN_WIDTH, min(MAX_WIDTH, w))
        h = max(MIN_HEIGHT, min(MAX_HEIGHT, h))
        x, y = self._initial_position(w, h)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.setMaximumSize(MAX_WIDTH, MAX_HEIGHT)
        self.resize(w, h)
        self.move(x, y)
        self._reflow_task_widths()

        # Polling timer for external edits (every 1.5 seconds)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1500)
        self._poll_timer.timeout.connect(self._poll_file_changes)
        self._poll_timer.start()

        # Save debounce timer
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_config)

        # Edge auto-scroll while dragging a task near the top or bottom
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(30)
        self._scroll_timer.timeout.connect(self._autoscroll_tick)

    def _initial_position(self, w: int, h: int) -> tuple[int, int]:
        """Saved position if it is still on a screen, else the primary screen's bottom-right."""
        screen = QtGui.QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1040)
        default_x = area.right() - w - 16
        default_y = area.bottom() - h - 16
        try:
            x = int(self.config["x"])
            y = int(self.config["y"])
        except (KeyError, TypeError, ValueError):
            return default_x, default_y
        # A monitor may have been unplugged since the position was saved.
        probe = QPoint(x + 40, y + 40)
        if not any(sc.availableGeometry().contains(probe) for sc in QtGui.QGuiApplication.screens()):
            return default_x, default_y
        return x, y

    def _load_config(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as file_handle:
                    data = json.load(file_handle)
                    if isinstance(data, dict):
                        merged = dict(DEFAULT_CONFIG)
                        merged.update(data)
                        return merged
            except Exception:
                pass
        return dict(DEFAULT_CONFIG)

    def _save_config(self) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)
            self.config["x"] = self.x()
            self.config["y"] = self.y()
            self.config["w"] = self.width()
            self.config["h"] = self.height()
            self.config["always_on_top"] = self.is_always_on_top
            self.config["opacity"] = self.tint_opacity
            self.config["tab"] = self.current_tab
            with open(self.config_path, "w", encoding="utf-8") as file_handle:
                json.dump(self.config, file_handle, indent=2)
        except Exception:
            pass

    def _schedule_save_config(self) -> None:
        self._save_timer.start()

    def _init_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        pad = glass_theme.EDGE_PAD
        root_layout.setContentsMargins(pad, pad, pad, pad)
        root_layout.setSpacing(6)

        # 1. Top pill strip / actions (Title Case, normal kerning, clean look)
        pill_row = QtWidgets.QHBoxLayout()
        pill_row.setContentsMargins(0, 0, 0, 0)
        pill_row.setSpacing(6)

        # Tab pills: each one shows its own page of the stacked area below.
        self.btn_tasks = PillButton("Tasks", active=True, h=22)
        self.btn_tasks.clicked.connect(lambda: self.show_tab("tasks"))
        self.btn_goals = PillButton("Goals", active=False, h=22)
        self.btn_goals.clicked.connect(lambda: self.show_tab("goals"))
        self.btn_notepad = PillButton("Editor" if paths.is_linux() else "Notepad", active=False, h=22)
        self.btn_notepad.clicked.connect(self.manager.open_in_notepad)

        self.btn_pin = PillButton("Pin", active=self.is_always_on_top, h=22)
        self.btn_pin.clicked.connect(self._toggle_always_on_top)

        self.btn_close = PillButton("✕", active=False, color=glass_theme.RED, h=22)
        self.btn_close.setFixedWidth(28)
        self.btn_close.clicked.connect(self._close_clicked)  # Hide to tray (quit when no tray)

        pill_row.addWidget(self.btn_tasks)
        pill_row.addWidget(self.btn_goals)
        pill_row.addStretch()
        pill_row.addWidget(self.btn_notepad)
        pill_row.addWidget(self.btn_pin)
        pill_row.addWidget(self.btn_close)
        root_layout.addLayout(pill_row)

        # Everything below the pills is one of two "pages" in a QStackedWidget,
        # which shows exactly one child at a time: page 0 = Tasks, page 1 = Goals.
        self.tasks_page = QtWidgets.QWidget()
        tasks_layout = QtWidgets.QVBoxLayout(self.tasks_page)
        tasks_layout.setContentsMargins(0, 0, 0, 0)
        tasks_layout.setSpacing(6)
        self.goals_panel = GoalsPanel(self.goals_manager, self.manager, PillButton)
        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self.tasks_page)
        self.pages.addWidget(self.goals_panel)
        root_layout.addWidget(self.pages, stretch=1)

        # 2. Section Header: Title & Counts
        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 4, 0, 2)

        lbl_title = QtWidgets.QLabel("To-Do List")
        lbl_title.setFont(glass_theme.qt_font(13, bold=True))
        lbl_title.setStyleSheet("color: #F4F7FB; background: transparent;")
        lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.lbl_stats = QtWidgets.QLabel("Loading tasks...")
        self.lbl_stats.setFont(glass_theme.label_font())
        self.lbl_stats.setStyleSheet(f"color: {glass_theme.EMERALD}; background: transparent;")
        self.lbl_stats.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_stats.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        header_row.addWidget(lbl_title)
        header_row.addStretch()
        header_row.addWidget(self.lbl_stats)
        tasks_layout.addLayout(header_row)

        # 3. Task List Scroll Area (Transparent groove, sleek cyan handle)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 5px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(103, 232, 249, 0.35);
                min-height: 24px;
                border-radius: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background: #22D3EE;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)

        self.task_container = TaskListContainer(self)
        self.task_layout = QtWidgets.QVBoxLayout(self.task_container)
        self.task_layout.setContentsMargins(0, 2, 4, 2)
        self.task_layout.setSpacing(5)
        self.scroll.setWidget(self.task_container)
        tasks_layout.addWidget(self.scroll, stretch=1)

        # 4. Add Task Input Row
        input_row = QtWidgets.QHBoxLayout()
        input_row.setContentsMargins(0, 4, 0, 2)
        input_row.setSpacing(6)

        self.input_edit = QtWidgets.QLineEdit()
        self.input_edit.setPlaceholderText("Add a new task... (press Enter)")
        self.input_edit.setFont(glass_theme.qt_font(9))
        self.input_edit.setFixedHeight(28)
        self.input_edit.setStyleSheet("""
            QLineEdit {
                background-color: rgba(11, 13, 18, 0.7);
                color: #F4F7FB;
                border: 1px solid rgba(103, 232, 249, 0.35);
                border-radius: 14px;
                padding-left: 12px;
                padding-right: 12px;
            }
            QLineEdit:focus {
                border: 1.5px solid #22D3EE;
                background-color: rgba(16, 18, 24, 0.85);
            }
        """)
        self.input_edit.returnPressed.connect(self._on_add_task)

        self.btn_add = PillButton("+ Add", active=True, color=glass_theme.ACCENT, h=28)
        self.btn_add.clicked.connect(self._on_add_task)

        input_row.addWidget(self.input_edit, stretch=1)
        input_row.addWidget(self.btn_add)
        tasks_layout.addLayout(input_row)

        # 5. Footer Stats & Clear Completed
        footer_row = QtWidgets.QHBoxLayout()
        footer_row.setContentsMargins(4, 2, 0, 0)

        lbl_hint = QtWidgets.QLabel("Drag to reorder" if paths.is_linux() else "Drag to reorder · Ctrl+F12 snap")
        lbl_hint.setFont(glass_theme.qt_font(8))
        lbl_hint.setStyleSheet(f"color: {glass_theme.C_FAINT}; background: transparent;")
        lbl_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.btn_clear = PillButton("Clear Done", active=False, color=glass_theme.C_LABEL, h=20)
        self.btn_clear.clicked.connect(self._on_clear_done)

        footer_row.addWidget(lbl_hint)
        footer_row.addStretch()
        footer_row.addWidget(self.btn_clear)
        tasks_layout.addLayout(footer_row)

        self.current_tab = "tasks"
        saved_tab = self.config.get("tab", "tasks")
        self.show_tab(saved_tab if saved_tab in TAB_NAMES else "tasks", save=False)

    def show_tab(self, tab_name: str, save: bool = True) -> None:
        """Switches between the Tasks and Goals pages and lights up the right pill."""
        self.current_tab = tab_name
        self.pages.setCurrentIndex(TAB_NAMES.index(tab_name))
        self.btn_tasks.setActive(tab_name == "tasks")
        self.btn_goals.setActive(tab_name == "goals")
        if tab_name == "goals":
            self.goals_panel.poll_file_changes()  # catch up straight away
        if save:
            self._save_config()  # remember the tab for next launch

    # ------------------------------------------------------------------------------------
    # System Tray Icon & Menu
    # ------------------------------------------------------------------------------------
    def _build_tray_icon(self) -> None:
        """Builds the action menu, and shows it from a system tray icon when one exists.

        The menu is always built. Without a tray (GNOME needs an extension for one) it is
        reached by right-clicking the header of the card instead, so nothing is lost.
        """
        self.tray = None
        self.tray_menu = None
        has_tray = QtWidgets.QSystemTrayIcon.isSystemTrayAvailable()
        if has_tray:
            self.tray = QtWidgets.QSystemTrayIcon(self.tray_icon_obj, self)
            self.tray.setToolTip(WINDOW_TITLE)

        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #101218;
                color: #F4F7FB;
                border: 1px solid #22D3EE;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: rgba(34, 211, 238, 0.25);
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(103, 232, 249, 0.2);
                margin: 4px 6px;
            }
        """)

        if has_tray:  # hiding is only safe when a tray icon can bring the card back
            act_toggle = menu.addAction("Show / Hide To-Do List")
            act_toggle.triggered.connect(self._toggle_visibility)

        act_notepad = menu.addAction("Open in Text Editor" if paths.is_linux() else "Open in Notepad")
        act_notepad.triggered.connect(self.manager.open_in_notepad)

        if not paths.is_linux():  # nothing can move windows on Wayland
            act_snap = menu.addAction("Snap to Screen Corner (Ctrl+F12)")
            act_snap.triggered.connect(self._on_snap_hotkey)

        menu.addSeparator()

        self.act_startup = menu.addAction("Start when I sign in" if paths.is_linux() else "Start with Windows")
        self.act_startup.setCheckable(True)
        self.act_startup.setChecked(is_startup_enabled())
        self.act_startup.toggled.connect(self._toggle_startup)

        self.act_top = menu.addAction("Always On Top")
        self.act_top.setCheckable(True)
        self.act_top.setChecked(self.is_always_on_top)
        self.act_top.toggled.connect(self._toggle_always_on_top)

        opacity_menu = menu.addMenu("Opacity / Dark Tint")
        for percent in (20, 35, 50, 65, 80, 100):
            act_op = opacity_menu.addAction(f"{percent}%")
            act_op.triggered.connect(lambda _=None, p=percent: self._set_opacity(p))

        bg_menu = menu.addMenu("Background & Blur")
        for label, radius in (
            ("No Blur (0px · Crisp)", 0.0),
            ("Light Blur (3px)", 3.0),
            ("Soft Blur (6px)", 6.0),
            ("Medium Blur (12px)", 12.0),
            ("Heavy Frosted Glass (22px)", 22.0),
        ):
            act_blur = bg_menu.addAction(label)
            act_blur.triggered.connect(lambda _=None, r=radius: self._set_bg_blur(r))
        bg_menu.addSeparator()
        act_choose_bg = bg_menu.addAction("Choose Background Image...")
        act_choose_bg.triggered.connect(self._choose_bg_image)
        act_reset_bg = bg_menu.addAction("Reset to Desktop Wallpaper")
        act_reset_bg.triggered.connect(self._reset_bg_image)

        menu.addSeparator()

        act_clear = menu.addAction("Clear Completed Tasks")
        act_clear.triggered.connect(self._on_clear_done)

        menu.addSeparator()

        act_quit = menu.addAction("Quit To-Do Widget")
        act_quit.triggered.connect(QtWidgets.QApplication.instance().quit)

        self.tray_menu = menu
        if self.tray is not None:
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.show()

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        """Right-click on the header shows the same menu as the tray icon."""
        if self.tray_menu is not None and event.pos().y() <= HEADER_DRAG_MAX_Y:
            self.tray_menu.exec(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def _close_clicked(self) -> None:
        """The red cross: hide to the tray, or quit when there is no tray to restore from."""
        if self.tray is not None:
            self.hide()
        else:
            QtWidgets.QApplication.instance().quit()

    def _toggle_startup(self, enabled: bool) -> None:
        """Adds or removes the widget's HKCU Run entry, then shows what really happened."""
        set_startup_enabled(enabled)
        self.act_startup.blockSignals(True)
        self.act_startup.setChecked(is_startup_enabled())
        self.act_startup.blockSignals(False)

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visibility()

    def _set_opacity(self, percent: int) -> None:
        self.tint_opacity = percent / 100.0
        if self.glass.custom_settings is not None:
            glass_theme.save_widget_bg_config({"tint_opacity": self.tint_opacity})
            self.glass.refresh()
        self.update()
        self._schedule_save_config()

    def _set_bg_blur(self, radius: float) -> None:
        glass_theme.save_widget_bg_config({"blur_radius": float(radius)})
        self.glass.refresh()
        self.update()

    def _choose_bg_image(self) -> None:
        # Open the picker in the user's Pictures folder.
        start_dir = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.PicturesLocation
        )
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Choose Widget Background Image",
            start_dir,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if path and os.path.isfile(path):
            glass_theme.save_widget_bg_config({"image_path": path})
            self.glass.refresh()
            self.update()

    def _reset_bg_image(self) -> None:
        glass_theme.save_widget_bg_config({"image_path": ""})
        self.glass.refresh()
        self.update()

    # ------------------------------------------------------------------------------------
    # Window Painting (Frosted Glass, Glow, and Resize Grip)
    # ------------------------------------------------------------------------------------
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = glass_theme.card_path(self.width(), self.height())
        custom_bg = self.glass.custom_settings
        effective_tint = custom_bg.tint_opacity if custom_bg is not None else self.tint_opacity
        glass_theme.paint_card(
            painter,
            path,
            self.glass,
            self.mapToGlobal(QPoint(0, 0)),
            effective_tint,
        )

        # Subtle corner resize grip (3 diagonal tick marks at bottom right)
        painter.setPen(QPen(QColor(103, 232, 249, 100), 1.2))
        rx = self.width() - glass_theme.EDGE_PAD + 4
        ry = self.height() - glass_theme.EDGE_PAD + 4
        painter.drawLine(rx - 3, ry, rx, ry - 3)
        painter.drawLine(rx - 7, ry, rx, ry - 7)
        painter.drawLine(rx - 11, ry, rx, ry - 11)

        painter.end()

    # ------------------------------------------------------------------------------------
    # Edge & Corner Resizing + Window Dragging
    # ------------------------------------------------------------------------------------
    def _hit_test(self, pos: QPoint) -> tuple[bool, bool, bool, bool]:
        """Determines which edges (left, right, top, bottom) are under pos."""
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()

        # Dedicated bottom-right corner grip (covers painted diagonal tick marks)
        if x >= w - CORNER_GRIP_MARGIN and y >= h - CORNER_GRIP_MARGIN:
            return False, True, False, True

        left = x <= RESIZE_MARGIN_LEFT
        right = x >= w - RESIZE_MARGIN_RIGHT
        top = y <= RESIZE_MARGIN_TOP
        bottom = y >= h - RESIZE_MARGIN_BOTTOM

        # Top corners: grabbing near top on left or right activates diagonal resize
        if (left or right) and y <= RESIZE_MARGIN_LEFT:
            top = True

        return left, right, top, bottom

    def _update_cursor_for_edges(self, edges: tuple[bool, bool, bool, bool]) -> None:
        left, right, top, bottom = edges
        if (left and top) or (right and bottom):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif (right and top) or (left and bottom):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif left or right:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif top or bottom:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._hit_test(event.position().toPoint())
            if any(edges):
                self._resizing_edges = edges
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geo = self.geometry()
                if self.mouseGrabber() != self:
                    self.grabMouse()
                event.accept()
                return
            # On Linux (Wayland) a program may not place its own window, but it may ask the
            # compositor to run the move. If that is refused we fall back to the plain drag.
            if paths.is_linux():
                handle = self.windowHandle()
                if handle is not None and handle.startSystemMove():
                    event.accept()
                    return
            # Dragging anywhere on the card background (header, footer, or empty space) moves window
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            if self.mouseGrabber() != self:
                self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # Handle live edge / corner resizing
        if self._resizing_edges is not None:
            cur_pos = event.globalPosition().toPoint()
            dx = cur_pos.x() - self._resize_start_pos.x()
            dy = cur_pos.y() - self._resize_start_pos.y()

            start_x = self._resize_start_geo.x()
            start_y = self._resize_start_geo.y()
            start_w = self._resize_start_geo.width()
            start_h = self._resize_start_geo.height()

            new_x = start_x
            new_y = start_y
            new_w = start_w
            new_h = start_h

            left, right, top, bottom = self._resizing_edges

            # When dragging the left edge, the right edge stays anchored at start_x + start_w.
            # When dragging the right edge, the left edge stays anchored at start_x.
            if left:
                new_w = max(MIN_WIDTH, min(MAX_WIDTH, start_w - dx))
                new_x = start_x + (start_w - new_w)
            elif right:
                new_w = max(MIN_WIDTH, min(MAX_WIDTH, start_w + dx))

            # When dragging the top edge, the bottom edge stays anchored at start_y + start_h.
            # When dragging the bottom edge, the top edge stays anchored at start_y.
            if top:
                new_h = max(MIN_HEIGHT, min(MAX_HEIGHT, start_h - dy))
                new_y = start_y + (start_h - new_h)
            elif bottom:
                new_h = max(MIN_HEIGHT, min(MAX_HEIGHT, start_h + dy))

            self.setGeometry(new_x, new_y, new_w, new_h)
            self._reflow_task_widths()
            self._schedule_save_config()
            event.accept()
            return

        # Handle window dragging across monitors
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._schedule_save_config()
            event.accept()
            return

        # Update cursor on hover near borders
        edges = self._hit_test(event.position().toPoint())
        self._update_cursor_for_edges(edges)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resizing_edges is not None:
            self._resizing_edges = None
            self.unsetCursor()
            self._schedule_save_config()
            event.accept()
        if self._drag_pos is not None:
            self._drag_pos = None
            event.accept()
        # Release the mouse grab when the button is released
        if self.mouseGrabber() == self:
            self.releaseMouse()
        super().mouseReleaseEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        # Clean up any active drag or resize if the window gets hidden
        if self._resizing_edges is not None or self._drag_pos is not None:
            self._resizing_edges = None
            self._drag_pos = None
            if self.mouseGrabber() == self:
                self.releaseMouse()
            self.unsetCursor()
        super().hideEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._reflow_task_widths()
        super().resizeEvent(event)

    def _reflow_task_widths(self) -> None:
        """Dynamically updates word wrap widths for all visible task cards and goals cards."""
        content_w = max(200, self.width() - 2 * glass_theme.EDGE_PAD - 14)
        for i in range(self.task_layout.count()):
            item = self.task_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), TaskItemWidget):
                item.widget().update_width(content_w)
        if hasattr(self, "goals_panel") and self.goals_panel is not None:
            self.goals_panel.reflow()

    # ------------------------------------------------------------------------------------
    # Keyboard Hotkeys
    # ------------------------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F12 and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._on_snap_hotkey()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_snap_hotkey(self) -> None:
        """Snaps this card to the screen corner and lines up any companion widgets beside it."""
        try:
            from snap_widgets import snap_widget_to_edge
            snap_widget_to_edge(save_position=False)
            self.raise_()
            self._schedule_save_config()
        except Exception:
            pass

    # ------------------------------------------------------------------------------------
    # Always On Top Toggle
    # ------------------------------------------------------------------------------------
    def _toggle_always_on_top(self) -> None:
        self.is_always_on_top = not self.is_always_on_top
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.is_always_on_top)
        self.show()
        self.btn_pin.setActive(self.is_always_on_top)
        if hasattr(self, "act_top"):
            self.act_top.setChecked(self.is_always_on_top)
        self._save_config()

    # ------------------------------------------------------------------------------------
    # Task Operations & Sync
    # ------------------------------------------------------------------------------------
    def refresh_list(self) -> None:
        """Reads tasks from file and updates the visual cards."""
        while self.task_layout.count() > 0:
            child = self.task_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        items, trailing = self.manager.read_items()
        if os.path.exists(self.manager.file_path):
            self.last_mtime = os.path.getmtime(self.manager.file_path)

        open_count = sum(1 for it in items if not it.is_done)
        self.lbl_stats.setText(f"{open_count} open / {len(items)} total")
        self.lbl_stats.setStyleSheet(
            f"color: {glass_theme.EMERALD if open_count > 0 else glass_theme.C_LABEL}; background: transparent;"
        )

        content_w = max(200, self.width() - 2 * glass_theme.EDGE_PAD - 14)

        if not items and not trailing:
            empty_lbl = QtWidgets.QLabel("No tasks right now!\nType below to add one.")
            empty_lbl.setFont(glass_theme.qt_font(10))
            empty_lbl.setStyleSheet(f"color: {glass_theme.C_LABEL}; background: transparent;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.task_layout.addWidget(empty_lbl)
            self.task_layout.addStretch()
            return

        for position, item in enumerate(items):
            row = TaskItemWidget(item, position=position, content_width=content_w)
            row.owner = self
            row.toggled.connect(self._on_toggle_item)
            row.deleted.connect(self._on_delete_item)
            row.drop_committed.connect(self.commit_reorder)
            self.task_layout.addWidget(row)

        # Trailing draft notes
        if trailing:
            notes_header = QtWidgets.QLabel("DRAFT NOTES")
            notes_header.setFont(glass_theme.label_font())
            notes_header.setStyleSheet(f"color: {glass_theme.C_LABEL}; background: transparent; padding-top: 8px;")
            self.task_layout.addWidget(notes_header)

            for note in trailing:
                note_lbl = QtWidgets.QLabel(f"• {note}")
                note_lbl.setFont(glass_theme.qt_font(9))
                note_lbl.setStyleSheet(f"color: {glass_theme.C_TEXT2}; background: transparent;")
                note_lbl.setWordWrap(True)
                self.task_layout.addWidget(note_lbl)

        self.task_layout.addStretch()

    # ------------------------------------------------------------------------------------
    # Drag-to-Reorder Controller
    # ------------------------------------------------------------------------------------
    def begin_drag(self, row: TaskItemWidget) -> None:
        """Remembers which task is being dragged and resets any old markers."""
        self._drag_from_index = row.item.index
        self.clear_markers()

    def end_drag(self) -> None:
        """Clears drag state once the drop (or cancel) has completed."""
        self._drag_from_index = None
        self._autoscroll_dir = 0
        self._scroll_timer.stop()
        self.clear_markers()

    def clear_markers(self) -> None:
        """Hides every insertion marker."""
        self.task_container.set_end_marker(False)
        for i in range(self.task_layout.count()):
            layout_item = self.task_layout.itemAt(i)
            widget = layout_item.widget() if layout_item else None
            if isinstance(widget, TaskItemWidget) and widget._drop_marker:
                widget._drop_marker = 0
                widget.update()

    def show_row_marker(self, row: TaskItemWidget, before: bool) -> None:
        """Puts the insertion line above or below a specific row."""
        self.clear_markers()
        row._drop_marker = 1 if before else 2
        row.update()

    def show_slot_marker(self, slot: int) -> None:
        """Puts the insertion line at a numeric insertion slot."""
        rows = self.task_container.rows()
        self.clear_markers()
        if slot >= len(rows):
            self.task_container.set_end_marker(True)
        else:
            rows[slot]._drop_marker = 1
            rows[slot].update()

    def commit_reorder(self, to_slot: int) -> None:
        """Applies a completed drop, renumbering the list when the order changed."""
        from_index = self._drag_from_index
        if from_index is None:
            return
        if self.manager.move_item(from_index, to_slot):
            self.refresh_list()
        self.clear_markers()

    def update_autoscroll(self, global_pos: QPoint) -> None:
        """Scrolls the list when the dragged row nears the top or bottom edge."""
        viewport = self.scroll.viewport()
        local_y = viewport.mapFromGlobal(global_pos).y()
        if local_y < 26:
            direction = -1
        elif local_y > viewport.height() - 26:
            direction = 1
        else:
            direction = 0

        self._autoscroll_dir = direction
        if direction and not self._scroll_timer.isActive():
            self._scroll_timer.start()
        elif not direction and self._scroll_timer.isActive():
            self._scroll_timer.stop()

    def _autoscroll_tick(self) -> None:
        if not self._autoscroll_dir:
            return
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + self._autoscroll_dir * 14)

    def _on_toggle_item(self, item_index: int) -> None:
        items, trailing = self.manager.read_items()
        for it in items:
            if it.index == item_index:
                it.is_done = not it.is_done
                break
        self.manager.save_items(items, trailing_lines=trailing)
        self.refresh_list()

    def _on_delete_item(self, item_index: int) -> None:
        self.manager.remove_item(item_index)
        self.refresh_list()

    def _on_add_task(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            return
        self.manager.add_item(text)
        self.input_edit.clear()
        self.refresh_list()

    def _on_clear_done(self) -> None:
        self.manager.clear_done()
        self.refresh_list()

    def _poll_file_changes(self) -> None:
        try:
            if os.path.exists(self.manager.file_path):
                current_mtime = os.path.getmtime(self.manager.file_path)
                if current_mtime > self.last_mtime:
                    self.last_mtime = current_mtime
                    self.refresh_list()
        except Exception:
            pass
        try:
            if self.glass.refresh():
                self.update()
        except Exception:
            pass

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._save_config()
        super().closeEvent(event)


# ----------------------------------------------------------------------------------------
# Main Entry Point
# ----------------------------------------------------------------------------------------
def parse_args(argv: list[str]) -> argparse.Namespace:
    """Optional file paths, so the widget can be tested on copies of the real files."""
    parser = argparse.ArgumentParser(description="Desktop to-do and goals widget")
    parser.add_argument("--todo-file", default=DEFAULT_TODO_PATH, help="To Do List.txt to show")
    parser.add_argument("--goals-file", default=DEFAULT_GOALS_PATH, help="Goals.json to show")
    parser.add_argument("--config", default=CONFIG_PATH, help="widget_config.json to use")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="build the widget without showing it, save selftest_card.png and exit",
    )
    return parser.parse_args(argv)


def main() -> None:
    global _single_instance_mutex
    args = parse_args(sys.argv[1:])

    if args.selftest and sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # The offscreen platform loads no system fonts on Windows, so every glyph
        # would render as a box in the test PNG (same fix as the other two widgets).
        os.environ.setdefault("QT_QPA_FONTDIR",
                              os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

    # Only one widget may run: a second launch just focuses the first window.
    # (The self-test builds its own hidden widget, so it skips this check.)
    if not args.selftest and paths.is_linux():
        if not acquire_linux_lock():
            return  # a copy is already running: exit quietly with code 0
    elif not args.selftest:
        _single_instance_mutex = ctypes.windll.kernel32.CreateMutexW(
            None, False, "Local" + chr(92) + paths.INSTANCE_KEY
        )
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            _focus_existing_widget(WINDOW_TITLE)
            return

    # Ensure thread is attached to the interactive user desktop (WinSta0\Default)
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            h_desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_desk:
                user32.SetThreadDesktop(h_desk)
        except Exception:
            pass

    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # First run: start with the bundled example tasks instead of a blank file.
    if args.todo_file == DEFAULT_TODO_PATH and not paths.is_linux():
        seed_first_run_files(args.todo_file)

    widget = DesktopTodoWidget(args.todo_file, args.goals_file, args.config)
    if args.selftest:
        sys.exit(run_selftest(widget))
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
