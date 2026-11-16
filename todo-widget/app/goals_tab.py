"""
Goals Tab for the Desktop To-Do Widget
======================================

This file builds everything you see on the "Goals" tab of the to-do widget.
The main window (todo_widget.py) only creates one GoalsPanel and shows it when
the "Goals" pill is pressed; all the goal drawing and clicking lives here.

How the pieces fit together (top to bottom on screen):

    GoalsPanel                the whole tab
      header                  "Goals" title + "1 active · 2/9 steps"
      focus box               TODAY'S FOCUS: the next step of the top goal
      scroll area
        GoalCard              one per goal (title, x/y steps, progress bar,
          ProgressBar           next step, "To Tasks" button)
          StepRow  (open card only)   tick a step, open its log, or flag it "stuck"
          SMART view (open card only) the five SMART fields in plain words
      footer                  hint text, or "Sent to Tasks" confirmation
    StepLogDialog             pops up when a step's text is clicked: past notes,
                              a box for "what I did", and "mark as done"

Data flow:
- All reading and writing of Goals.json (in the widget's data folder) goes through
  GoalsManager (goals_manager.py). This file never edits the JSON itself, so
  the rules (backups, undo, "last touched" dates) live in one place.
- Like the Tasks tab, the panel checks the file's "modified time" every
  1.5 seconds. If another program or a text editor changed the file, the
  cards are rebuilt, so edits show up within 2 seconds.
- "To Tasks" copies a step onto the normal to-do list through the same
  TodoListManager the Tasks tab uses, so the Tasks tab picks it up by itself.
- Every save also rewrites Goals Log.txt beside it, a plain-text copy
  of all goals, steps and notes (GoalsManager.write_text_log does this).
"""

from __future__ import annotations

import datetime
import html
import os

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QRect, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen

import glass_theme
from goals_manager import Goal, GoalsManager, Step, is_stale, next_step, progress, todays_focus
from todo_manager import TodoListManager

# How often (milliseconds) to check Goals.json for outside edits. Same as Tasks.
POLL_INTERVAL_MS = 1500

# How long a "Sent to Tasks" message stays in the footer before the hint returns.
STATUS_MESSAGE_MS = 4000

FOOTER_HINT = "Click a goal to open it · click a step to log it"

# Style of the "add a goal / step" line (same look as the Tasks tab's input).
INPUT_STYLE = """
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
"""

# The log dialog is a normal Qt dialog, so it is styled with a stylesheet
# (Qt's version of CSS) in the same dark navy and cyan as the glass widget.
DIALOG_STYLE = """
    QDialog { background: #0E1726; border: 1px solid rgba(103, 232, 249, 0.45); }
    QLabel { color: #E2E8F0; background: transparent; }
    QPlainTextEdit {
        background: rgba(255, 255, 255, 0.06); color: #F4F7FB;
        border: 1px solid rgba(103, 232, 249, 0.35); border-radius: 6px; padding: 4px;
        selection-background-color: #0E7490;
    }
    QPlainTextEdit:focus { border: 1px solid #22D3EE; }
    QCheckBox { color: #E2E8F0; spacing: 6px; }
    QPushButton {
        background: rgba(255, 255, 255, 0.08); color: #E2E8F0;
        border: 1px solid rgba(255, 255, 255, 0.25); border-radius: 10px; padding: 4px 14px;
    }
    QPushButton:hover { border: 1px solid #22D3EE; }
    QPushButton#saveButton { background: rgba(34, 211, 238, 0.25); border: 1px solid #22D3EE; }
"""

# The scroll bar look is copied from the Tasks tab so both tabs match.
SCROLL_AREA_STYLE = """
    QScrollArea { background: transparent; border: none; }
    QScrollBar:vertical { background: transparent; width: 5px; margin: 0; }
    QScrollBar::handle:vertical {
        background: rgba(103, 232, 249, 0.35); min-height: 24px; border-radius: 2px;
    }
    QScrollBar::handle:vertical:hover { background: #22D3EE; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: none; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""

# Plain-words names for the five SMART fields, shown in the SMART view.
SMART_LABELS = [
    ("specific", "Specific", "what exactly I'll do"),
    ("measurable", "Measurable", "how I'll know it's working"),
    ("achievable", "Achievable", "why I can do it"),
    ("relevant", "Relevant", "why it matters to me"),
]


# ----------------------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------------------
def make_label(text: str, size: float, color: str, bold: bool = False, wrap: bool = True) -> QtWidgets.QLabel:
    """Creates a transparent QLabel in the glass theme font.

    Every label on this tab is made here, so changing the look is a one-line edit.
    """
    label = QtWidgets.QLabel(text)
    label.setFont(glass_theme.qt_font(size, bold=bold))
    label.setStyleSheet(f"color: {color}; background: transparent;")
    label.setWordWrap(wrap)
    if wrap:
        label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
    return label


def pretty_date(iso_date: str | None) -> str:
    """Turns '2027-02-13' into '13 Feb 2027'. Bad or empty dates come back unchanged."""
    if not iso_date:
        return "?"
    try:
        parsed = datetime.date.fromisoformat(iso_date)
    except ValueError:
        return iso_date
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


def days_since(iso_date: str | None, today: datetime.date) -> int:
    """How many whole days ago a date was (0 if unknown)."""
    try:
        return (today - datetime.date.fromisoformat(iso_date or "")).days
    except ValueError:
        return 0


# ----------------------------------------------------------------------------------------
# Progress bar (a thin rounded bar, cyan to green; amber when the goal is stale)
# ----------------------------------------------------------------------------------------
class ProgressBar(QtWidgets.QWidget):
    """A 6 px tall bar showing the fraction of steps done."""

    def __init__(self, fraction: float, stale: bool, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.fraction = max(0.0, min(1.0, fraction))
        self.stale = stale
        self.setFixedHeight(6)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        groove = QRectF(0, 0, self.width(), self.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 22))
        painter.drawRoundedRect(groove, 3, 3)

        if self.fraction > 0:
            filled = QRectF(0, 0, max(6.0, self.width() * self.fraction), self.height())
            gradient = QtGui.QLinearGradient(filled.topLeft(), filled.topRight())
            if self.stale:
                gradient.setColorAt(0.0, QColor(glass_theme.AMBER))
                gradient.setColorAt(1.0, QColor("#FCD34D"))
            else:
                gradient.setColorAt(0.0, QColor(glass_theme.ACCENT))
                gradient.setColorAt(1.0, QColor(glass_theme.EMERALD))
            painter.setBrush(gradient)
            painter.drawRoundedRect(filled, 3, 3)
        painter.end()


# ----------------------------------------------------------------------------------------
# One step inside an open goal card
# ----------------------------------------------------------------------------------------
class StepRow(QtWidgets.QWidget):
    """A step with a tick circle on the left and a 'stuck' flag on the right.

    Three click zones:
    - the tick circle on the left ticks or unticks the step,
    - the step text opens the log dialog ("what I did"),
    - the flag on the right marks the step as stuck (too big or blocked), which
      a flag you can use to remember which steps need splitting into smaller ones.
    """

    toggled = Signal(int)        # 1-based step number
    stuck_toggled = Signal(int)  # 1-based step number
    opened = Signal(int)         # 1-based step number

    FLAG_WIDTH = 42
    TICK_ZONE_WIDTH = 26  # the circle plus a little slack, so it is easy to hit

    def __init__(
        self,
        number: int,
        step: Step,
        is_next: bool,
        content_width: int,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.number = number
        self.step = step
        self.is_next = is_next
        self._hovered = False
        self._flag_hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip("Circle: tick off · Text: log what you did · 'stuck': too big")
        self.update_width(content_width)

    def update_width(self, width: int) -> None:
        """Works out how tall the row must be for its text to wrap at this width."""
        self._font = glass_theme.qt_font(9, strike_out=self.step.done)
        count = len(self.step.notes)
        # A small "(2 notes)" tag shows at a glance which steps have a log.
        notes_tag = f"  ({count} note{'s' if count != 1 else ''})" if count else ""
        self._label = f"{self.number}. {self.step.text}{notes_tag}"
        self._text_width = max(80, width - 30 - self.FLAG_WIDTH - 8)
        metrics = QFontMetrics(self._font)
        bounds = metrics.boundingRect(QRect(0, 0, self._text_width, 9999), Qt.TextFlag.TextWordWrap, self._label)
        self._text_height = bounds.height()
        self.setFixedHeight(max(28, self._text_height + 12))
        self.update()

    def _flag_rect(self) -> QRectF:
        return QRectF(self.width() - self.FLAG_WIDTH - 4, (self.height() - 18) / 2, self.FLAG_WIDTH, 18)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = False
        self._flag_hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        over_flag = self._flag_rect().contains(event.position())
        if over_flag != self._flag_hovered:
            self._flag_hovered = over_flag
            self.update()
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        # Accepting the press stops the window from treating it as a window drag.
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.rect().contains(event.position().toPoint()):
            return
        if self._flag_rect().contains(event.position()):
            self.stuck_toggled.emit(self.number)
        elif event.position().x() <= self.TICK_ZONE_WIDTH:
            self.toggled.emit(self.number)
        else:
            self.opened.emit(self.number)
        event.accept()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self._paint_background(painter)
        self._paint_tick(painter)
        self._paint_text(painter)
        self._paint_flag(painter)
        painter.end()

    def _paint_background(self, painter: QPainter) -> None:
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if self.is_next:
            # The next step gets a faint cyan wash so the eye lands on it first.
            painter.setBrush(QColor(34, 211, 238, 26 if self._hovered else 16))
            painter.setPen(QPen(QColor(103, 232, 249, 90), 1.0))
        else:
            painter.setBrush(QColor(255, 255, 255, 14 if self._hovered else 5))
            painter.setPen(QPen(QColor(255, 255, 255, 14), 1.0))
        painter.drawRoundedRect(rect, 6, 6)

    def _paint_tick(self, painter: QPainter) -> None:
        box = QRectF(7, (self.height() - 14) / 2, 14, 14)
        if self.step.done:
            painter.setPen(QPen(QColor(glass_theme.EMERALD), 1.4))
            painter.setBrush(QColor(52, 211, 153, 50))
            painter.drawEllipse(box)
            # Draw the tick as two short lines so it never depends on a font glyph.
            pen = QPen(QColor(glass_theme.EMERALD), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QtCore.QPointF(box.left() + 3.5, box.center().y()),
                             QtCore.QPointF(box.left() + 6, box.bottom() - 4))
            painter.drawLine(QtCore.QPointF(box.left() + 6, box.bottom() - 4),
                             QtCore.QPointF(box.right() - 3, box.top() + 4))
        else:
            ring = QColor(glass_theme.EDGE if self._hovered else glass_theme.C_LABEL)
            ring.setAlpha(220 if self._hovered else 130)
            painter.setPen(QPen(ring, 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(box)

    def _paint_text(self, painter: QPainter) -> None:
        painter.setFont(self._font)
        if self.step.done:
            color = glass_theme.C_FAINT
        elif self.step.stuck:
            color = glass_theme.AMBER
        else:
            color = glass_theme.C_VALUE
        area = QRectF(28, (self.height() - self._text_height) / 2, self._text_width, self._text_height + 4)
        painter.setPen(QColor(0, 0, 0, 170))
        painter.drawText(area.translated(0, 1), Qt.TextFlag.TextWordWrap, self._label)
        painter.setPen(QColor(color))
        painter.drawText(area, Qt.TextFlag.TextWordWrap, self._label)

    def _paint_flag(self, painter: QPainter) -> None:
        if self.step.done:
            return  # a finished step can't be stuck, so no flag
        rect = self._flag_rect()
        if self.step.stuck:
            fill, border, text = QColor(245, 158, 11, 60), QColor(glass_theme.AMBER), "#FDE68A"
        elif self._flag_hovered:
            fill, border, text = QColor(245, 158, 11, 30), QColor(245, 158, 11, 160), "#FDE68A"
        else:
            fill, border, text = QColor(255, 255, 255, 8), QColor(255, 255, 255, 40), glass_theme.C_FAINT
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 9, 9)
        font = glass_theme.qt_font(7.5, bold=True)
        painter.setFont(font)
        painter.setPen(QColor(text))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "stuck")


# ----------------------------------------------------------------------------------------
# One goal card (compact when closed, steps + SMART view when open)
# ----------------------------------------------------------------------------------------
GOAL_MIME_TYPE = "application/x-desktop-widgets-goal"


class GoalDragHandle(QtWidgets.QPushButton):
    """Only the handle starts a drag, leaving steps and card clicks usable."""

    drag_active = Signal(bool)

    def __init__(self, goal_id: str, parent: QtWidgets.QWidget) -> None:
        super().__init__("≡", parent)
        self.goal_id = goal_id
        self.start_position: QtCore.QPoint | None = None
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to change goal priority")
        self.setAccessibleName("Drag goal to change priority")
        self.setStyleSheet("QPushButton { color: #94a3b8; background: transparent; border: 0; }")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.start_position is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self.start_position).manhattanLength() < QtWidgets.QApplication.startDragDistance():
            return
        self.start_position = None
        self.setDown(False)
        mime = QtCore.QMimeData()
        mime.setData(GOAL_MIME_TYPE, self.goal_id.encode("utf-8"))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        self.drag_active.emit(True)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.drag_active.emit(False)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self.start_position = None
        super().mouseReleaseEvent(event)


class GoalDropArea(QtWidgets.QWidget):
    """Receive drops over cards or gaps, with an insertion line and edge scroll."""

    def __init__(self, panel: "GoalsPanel") -> None:
        super().__init__()
        self.panel = panel
        self.line_y: int | None = None
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(GOAL_MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if not event.mimeData().hasFormat(GOAL_MIME_TYPE):
            return
        y = event.position().y()
        cards = self.panel.cards
        self.line_y = next((card.y() for card in cards
                            if y < card.y() + card.height() / 2),
                           cards[-1].geometry().bottom() + 3 if cards else 2)
        point = self.mapTo(self.panel.scroll.viewport(), event.position().toPoint())
        bar = self.panel.scroll.verticalScrollBar()
        if point.y() < 32:
            bar.setValue(bar.value() - 18)
        elif point.y() > self.panel.scroll.viewport().height() - 32:
            bar.setValue(bar.value() + 18)
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self.line_y = None
        self.update()
        event.accept()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if not event.mimeData().hasFormat(GOAL_MIME_TYPE):
            return
        goal_id = bytes(event.mimeData().data(GOAL_MIME_TYPE)).decode("utf-8")
        order = [card.goal.id for card in self.panel.cards if card.goal.id != goal_id]
        before = next((card.goal.id for card in self.panel.cards
                       if card.goal.id != goal_id and
                       event.position().y() < card.y() + card.height() / 2), None)
        index = order.index(before) if before else len(order)
        order.insert(index, goal_id)
        self.line_y = None
        self.update()
        # Rebuild after QDrag exits so its source widget is still alive.
        QTimer.singleShot(0, lambda: self.panel.save_goal_order(order))
        event.acceptProposedAction()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        if self.line_y is not None:
            painter = QtGui.QPainter(self)
            painter.setPen(QtGui.QPen(QtGui.QColor(glass_theme.ACCENT), 2))
            painter.drawLine(0, max(0, self.line_y - 2), self.width(), max(0, self.line_y - 2))


class GoalCard(QtWidgets.QWidget):
    """A rounded card for one goal. Click the card to open or close it."""

    card_clicked = Signal(str)          # goal id
    send_clicked = Signal(str, int)     # goal id, step number
    step_toggled = Signal(str, int)     # goal id, step number
    step_stuck = Signal(str, int)       # goal id, step number
    step_opened = Signal(str, int)      # goal id, step number (opens the log dialog)
    smart_toggled = Signal(str)         # goal id
    finish_clicked = Signal(str)
    drag_active = Signal(bool)

    def __init__(
        self,
        goal: Goal,
        expanded: bool,
        smart_open: bool,
        content_width: int,
        today: datetime.date,
        pill_class: type,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.goal = goal
        # The pill button class comes from todo_widget.py, passed in rather than
        # imported, so this file and todo_widget.py don't import each other.
        self.pill_class = pill_class
        self.expanded = expanded
        self.stale = goal.status == "active" and is_stale(goal, today=today)
        self.step_rows: list[StepRow] = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to close" if expanded else "Click to see the steps")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 9)
        layout.setSpacing(5)
        self._build_summary(layout, today)
        if expanded:
            self._build_steps(layout, content_width)
            self._build_smart(layout, smart_open)

    # -- building -------------------------------------------------------------------------
    def _build_summary(self, layout: QtWidgets.QVBoxLayout, today: datetime.date) -> None:
        """Title, x/y count, progress bar, next step and the 'To Tasks' button."""
        done_count, total = progress(self.goal)
        paused = "  (paused)" if self.goal.status == "paused" else ""

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(6)
        self.drag_handle = GoalDragHandle(self.goal.id, self)
        self.drag_handle.drag_active.connect(self.drag_active.emit)
        top_row.addWidget(self.drag_handle)
        top_row.addWidget(make_label(self.goal.title + paused, 10, glass_theme.C_VALUE, bold=True), stretch=1)
        count_color = glass_theme.EMERALD if total and done_count == total else glass_theme.C_LABEL
        top_row.addWidget(make_label(f"{done_count}/{total}", 9, count_color, bold=True, wrap=False),
                          alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top_row)

        layout.addWidget(ProgressBar(done_count / total if total else 0.0, self.stale))

        upcoming = next_step(self.goal)
        next_row = QtWidgets.QHBoxLayout()
        next_row.setSpacing(6)
        if upcoming is None:
            next_text = "All steps done - add the next one below" if total else "No steps yet - open this goal and add one below"
            next_row.addWidget(make_label(next_text, 8.5, glass_theme.C_FAINT), stretch=1)
            if total and done_count == total:
                self.finish_button = self._small_pill("Remove finished goal")
                self.finish_button.setToolTip("Hide this finished goal; keep its history and backup")
                self.finish_button.clicked.connect(lambda: self.finish_clicked.emit(self.goal.id))
                next_row.addWidget(self.finish_button)
        else:
            number, step = upcoming
            next_row.addWidget(make_label(f"Next: {step.text}", 8.5, glass_theme.C_TEXT2), stretch=1)
            send_button = self._small_pill("To Tasks")
            send_button.setToolTip("Copy this step onto your Tasks list")
            send_button.clicked.connect(lambda: self.send_clicked.emit(self.goal.id, number))
            next_row.addWidget(send_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(next_row)

        if self.stale:
            idle_days = days_since(self.goal.last_touched, today)
            layout.addWidget(make_label(f"Not touched for {idle_days} days - one small step today?",
                                        8, glass_theme.AMBER))

    def _build_steps(self, layout: QtWidgets.QVBoxLayout, content_width: int) -> None:
        """One StepRow per step. The first unfinished one is highlighted."""
        upcoming = next_step(self.goal)
        next_number = upcoming[0] if upcoming else -1
        steps_box = QtWidgets.QVBoxLayout()
        steps_box.setContentsMargins(0, 4, 0, 0)
        steps_box.setSpacing(3)
        for number, step in enumerate(self.goal.steps, start=1):
            row = StepRow(number, step, number == next_number, self._row_width(content_width))
            row.toggled.connect(lambda n: self.step_toggled.emit(self.goal.id, n))
            row.stuck_toggled.connect(lambda n: self.step_stuck.emit(self.goal.id, n))
            row.opened.connect(lambda n: self.step_opened.emit(self.goal.id, n))
            self.step_rows.append(row)
            steps_box.addWidget(row)
        layout.addLayout(steps_box)

    def _build_smart(self, layout: QtWidgets.QVBoxLayout, smart_open: bool) -> None:
        """A 'SMART' pill that shows or hides the five SMART fields."""
        smart_row = QtWidgets.QHBoxLayout()
        smart_row.setContentsMargins(0, 3, 0, 0)
        button = self._small_pill("Hide SMART" if smart_open else "SMART plan", active=smart_open)
        button.clicked.connect(lambda: self.smart_toggled.emit(self.goal.id))
        smart_row.addWidget(button)
        smart_row.addStretch()
        layout.addLayout(smart_row)
        if smart_open:
            smart_label = make_label(smart_html(self.goal), 8.5, glass_theme.C_TEXT2)
            smart_label.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(smart_label)

    def _small_pill(self, text: str, active: bool = False) -> QtWidgets.QPushButton:
        return self.pill_class(text, active=active, color=glass_theme.ACCENT, h=20)

    @staticmethod
    def _row_width(content_width: int) -> int:
        """Card padding (10 px each side) is taken off the panel width for step rows."""
        return content_width - 20

    def update_width(self, content_width: int) -> None:
        for row in self.step_rows:
            row.update_width(self._row_width(content_width))

    # -- clicking -------------------------------------------------------------------------
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        event.accept()  # stop the click from starting a window drag

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.card_clicked.emit(self.goal.id)
        event.accept()

    # -- drawing --------------------------------------------------------------------------
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if self.stale:
            # Amber = "this goal has been quiet for 5+ days".
            fill, border = QColor(245, 158, 11, 22), QColor(245, 158, 11, 170)
        elif self.expanded:
            fill, border = QColor(34, 211, 238, 14), QColor(103, 232, 249, 120)
        else:
            fill, border = QColor(255, 255, 255, 9), QColor(255, 255, 255, 26)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 9, 9)
        painter.end()


def smart_html(goal: Goal) -> str:
    """The SMART view as simple HTML (bold coloured headings, plain text answers)."""
    heading = f"color:{glass_theme.EDGE}; font-weight:600;"
    hint = f"color:{glass_theme.C_FAINT};"
    parts: list[str] = []
    if goal.why:
        parts.append(f"<span style='{heading}'>Why</span> {html.escape(goal.why)}")
    for field_name, title, plain_words in SMART_LABELS:
        value = getattr(goal, field_name, "") or "(not set yet)"
        parts.append(f"<span style='{heading}'>{title}</span> <span style='{hint}'>{plain_words}</span><br>"
                     f"{html.escape(value)}")
    parts.append(f"<span style='{heading}'>Time-bound</span> <span style='{hint}'>by when</span><br>"
                 f"{html.escape(time_bound_text(goal))}")
    return "<br><br>".join(parts)


def time_bound_text(goal: Goal) -> str:
    """'13 Nov 2026 to 13 Feb 2027. Reviews: 11 Dec 2026, ... Note'."""
    time_bound = goal.time_bound
    text = f"{pretty_date(time_bound.start)} to {pretty_date(time_bound.end)}."
    if time_bound.review_dates:
        text += " Reviews: " + ", ".join(pretty_date(day) for day in time_bound.review_dates) + "."
    if time_bound.note:
        text += " " + time_bound.note
    return text


# ----------------------------------------------------------------------------------------
# The whole Goals tab
# ----------------------------------------------------------------------------------------
class StepLogDialog(QtWidgets.QDialog):
    """The pop-up for one step: earlier notes, a 'what I did' box, and a done tick.

    It only collects what the user types. Saving is done by GoalsPanel.save_step_log,
    so the dialog stays simple and the saving can be tested without clicking.
    """

    def __init__(
        self,
        goal: Goal,
        step_number: int,
        log_path: str,
        parent: QtWidgets.QWidget | None = None,
        initial_text: str = "",
    ) -> None:
        """`initial_text` puts words back in the box after a failed save."""
        super().__init__(parent)
        self.step = goal.steps[step_number - 1]
        self.log_path = log_path
        self.setWindowTitle(f"Step {step_number} log")
        # The widget sits always-on-top, so its dialog must too or it hides behind.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet(DIALOG_STYLE)
        self.setMinimumWidth(340)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(make_label(goal.title.upper(), 7.5, glass_theme.ACCENT, bold=True))
        layout.addWidget(make_label(f"{step_number}. {self.step.text}", 10.5, "#F4F7FB", bold=True))
        layout.addWidget(self._build_history())

        layout.addWidget(make_label("What I did today", 8.5, glass_theme.C_LABEL, bold=True))
        self.note_edit = QtWidgets.QPlainTextEdit()
        self.note_edit.setPlaceholderText("e.g. Watched the first lesson, wrote hello.py")
        self.note_edit.setFont(glass_theme.qt_font(9.5))
        self.note_edit.setFixedHeight(90)
        layout.addWidget(self.note_edit)

        self.done_box = QtWidgets.QCheckBox("This step is done")
        self.done_box.setFont(glass_theme.qt_font(9))
        self.done_box.setChecked(self.step.done)
        layout.addWidget(self.done_box)
        layout.addLayout(self._build_buttons())
        if initial_text:
            # Left over from a save that failed: give the user their words back.
            self.note_edit.setPlainText(initial_text)
            self.note_edit.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        self.note_edit.setFocus()

    def _build_history(self) -> QtWidgets.QWidget:
        """Earlier notes, newest last, in a small scroll box (read-only)."""
        if not self.step.notes:
            return make_label("No notes yet - this will be the first.", 8.5, glass_theme.C_FAINT)
        rows = "".join(
            f"<p style='margin:0 0 4px 0'><b style='color:#67E8F9'>{html.escape(pretty_date(note.date))}</b>"
            f" &nbsp;{html.escape(note.text)}</p>"
            for note in self.step.notes
        )
        history = make_label(rows, 8.5, glass_theme.C_TEXT2)
        history.setTextFormat(Qt.TextFormat.RichText)
        history.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setStyleSheet(SCROLL_AREA_STYLE)
        scroll.setWidget(history)
        scroll.setMaximumHeight(120)
        return scroll

    def _build_buttons(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        open_button = QtWidgets.QPushButton("Open log file")
        open_button.setToolTip(self.log_path)
        open_button.clicked.connect(self._open_log_file)
        row.addWidget(open_button)
        row.addStretch()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QtWidgets.QPushButton("Save")
        save.setObjectName("saveButton")  # the stylesheet gives this one the cyan look
        save.setDefault(True)
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        # Ctrl+Enter saves, since a plain Enter adds a new line in the note box.
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self, activated=self.accept)
        return row

    def _open_log_file(self) -> None:
        """Opens 'Goals Log.txt' in Notepad (or whatever opens .txt files)."""
        if os.path.exists(self.log_path):
            os.startfile(self.log_path)  # type: ignore[attr-defined]  # Windows only

    def note_text(self) -> str:
        return self.note_edit.toPlainText().strip()

    def mark_done(self) -> bool:
        return self.done_box.isChecked()


class GoalsPanel(QtWidgets.QWidget):
    """The Goals tab: focus line, goal cards, and live refresh from Goals.json."""

    def __init__(
        self,
        goals_manager: GoalsManager,
        todo_manager: TodoListManager,
        pill_class: type,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.goals_manager = goals_manager
        self.todo_manager = todo_manager
        self.pill_class = pill_class
        self.expanded_goal_id: str | None = None  # only one card is open at a time
        self.smart_open = False
        self.last_mtime = 0.0
        self.cards: list[GoalCard] = []
        self._focus_goal_id: str | None = None
        self.drag_in_progress = False
        self._build_ui()
        self.refresh()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.poll_file_changes)
        self._poll_timer.start()

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self._set_footer(FOOTER_HINT, glass_theme.C_FAINT))

    # -- layout ---------------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 4, 0, 2)
        lbl_goals_title = make_label("Goals", 13, "#F4F7FB", bold=True, wrap=False)
        lbl_goals_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header.addWidget(lbl_goals_title)
        header.addStretch()
        self.lbl_stats = make_label("", 8, glass_theme.EMERALD, bold=True, wrap=False)
        self.lbl_stats.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header.addWidget(self.lbl_stats)
        layout.addLayout(header)

        layout.addWidget(self._build_focus_box())

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(SCROLL_AREA_STYLE)
        self.card_container = GoalDropArea(self)
        self.card_container.setStyleSheet("background: transparent;")
        self.card_layout = QtWidgets.QVBoxLayout(self.card_container)
        self.card_layout.setContentsMargins(0, 2, 6, 2)
        self.card_layout.setSpacing(6)
        self.scroll.setWidget(self.card_container)
        layout.addWidget(self.scroll, stretch=1)

        # One line for typing: it adds a new goal, or a step when a goal card is open.
        self.input_edit = QtWidgets.QLineEdit()
        self.input_edit.setFont(glass_theme.qt_font(9))
        self.input_edit.setFixedHeight(28)
        self.input_edit.setStyleSheet(INPUT_STYLE)
        self.input_edit.returnPressed.connect(self.add_from_input)
        layout.addWidget(self.input_edit)
        self._update_input_placeholder()

        self.lbl_footer = make_label(FOOTER_HINT, 8, glass_theme.C_FAINT, wrap=False)
        self.lbl_footer.setContentsMargins(4, 2, 0, 0)
        layout.addWidget(self.lbl_footer)

    def _build_focus_box(self) -> QtWidgets.QFrame:
        """The 'TODAY'S FOCUS' box. Clicking it opens that goal's card."""
        self.focus_box = QtWidgets.QFrame()
        self.focus_box.setObjectName("focusBox")
        self.focus_box.setStyleSheet(
            "#focusBox { background: rgba(34, 211, 238, 0.08);"
            " border: 1px solid rgba(103, 232, 249, 0.45); border-radius: 9px; }"
        )
        self.focus_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.focus_box.mouseReleaseEvent = self._on_focus_clicked  # type: ignore[method-assign]
        self.focus_box.mousePressEvent = lambda event: event.accept()  # type: ignore[method-assign]
        box_layout = QtWidgets.QVBoxLayout(self.focus_box)
        box_layout.setContentsMargins(10, 6, 10, 7)
        box_layout.setSpacing(2)
        self.lbl_focus_title = make_label("TODAY'S FOCUS", 7.5, glass_theme.ACCENT, bold=True, wrap=False)
        self.lbl_focus_step = make_label("", 10, glass_theme.C_VALUE, bold=True)
        box_layout.addWidget(self.lbl_focus_title)
        box_layout.addWidget(self.lbl_focus_step)
        return self.focus_box

    def content_width(self) -> int:
        """Usable width for cards (the scroll bar and its gap take about 10 px)."""
        return max(200, self.width() - 10)

    # -- reading the file -----------------------------------------------------------------
    def load_visible_goals(self) -> list[Goal]:
        """Active goals first (by priority), then paused ones. Finished goals are hidden."""
        goals = self.goals_manager.load_goals()
        shown = [goal for goal in goals if goal.status != "done"]
        return sorted(shown, key=lambda goal: (goal.priority, goal.created, goal.id))

    def refresh(self) -> None:
        """Re-reads Goals.json and rebuilds the focus line and every card."""
        if os.path.exists(self.goals_manager.file_path):
            self.last_mtime = os.path.getmtime(self.goals_manager.file_path)
        try:
            goals = self.load_visible_goals()
        except (OSError, ValueError) as error:
            # A half-typed hand edit can make the JSON invalid. Say so, don't crash.
            self._show_error(f"Could not read Goals.json: {error}")
            return
        scroll_position = self.scroll.verticalScrollBar().value()
        self._update_header(goals)
        self._update_focus(goals)
        self._rebuild_cards(goals)
        # Rebuilding the cards would jump the list to the top; put it back.
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_position))

    def poll_file_changes(self) -> None:
        """Called every 1.5 s: refresh only if the file's modified time moved on."""
        if self.drag_in_progress:
            return
        try:
            path = self.goals_manager.file_path
            if os.path.exists(path) and os.path.getmtime(path) > self.last_mtime:
                self.refresh()
        except OSError:
            pass  # the file may be mid-save; the next tick will catch it

    def _update_header(self, goals: list[Goal]) -> None:
        active = [goal for goal in goals if goal.status == "active"]
        done_steps = sum(progress(goal)[0] for goal in active)
        all_steps = sum(progress(goal)[1] for goal in active)
        self.lbl_stats.setText(f"{len(active)} active · {done_steps}/{all_steps} steps")

    def _update_focus(self, goals: list[Goal]) -> None:
        focus = todays_focus(goals)
        if focus is None:
            self._focus_goal_id = None
            self.lbl_focus_step.setText("Nothing planned yet - add a goal below")
            return
        goal, number, step = focus
        self._focus_goal_id = goal.id
        self.lbl_focus_title.setText(f"TODAY'S FOCUS · {goal.title.upper()}")
        self.lbl_focus_step.setText(f"{number}. {step.text}")

    def _rebuild_cards(self, goals: list[Goal]) -> None:
        self._clear_cards()
        if not goals:
            empty = make_label("No goals yet.\nType a title below and press Enter.", 10, glass_theme.C_LABEL)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.card_layout.addWidget(empty)
        today = datetime.date.today()
        for goal in goals:
            card = GoalCard(goal, goal.id == self.expanded_goal_id, self.smart_open,
                            self.content_width(), today, self.pill_class)
            card.card_clicked.connect(self.toggle_card)
            card.send_clicked.connect(self.send_step_to_tasks)
            card.step_toggled.connect(self.toggle_step)
            card.step_stuck.connect(self.toggle_stuck)
            card.step_opened.connect(self.open_step_log)
            card.smart_toggled.connect(self.toggle_smart)
            card.finish_clicked.connect(self.finish_goal)
            card.drag_active.connect(self._set_drag_active)
            self.cards.append(card)
            self.card_layout.addWidget(card)
        self.card_layout.addStretch()

    def _set_drag_active(self, active: bool) -> None:
        self.drag_in_progress = active


    def finish_goal(self, goal_id: str) -> None:
        try:
            self.goals_manager.finish_goal(goal_id)
        except (OSError, ValueError, KeyError) as error:
            self._show_error(str(error))
            return
        if self.expanded_goal_id == goal_id:
            self.expanded_goal_id = None
        self.refresh()
        self._set_footer("Finished goal removed. History kept in Goals Log.txt.", glass_theme.EMERALD)


    def save_goal_order(self, goal_ids: list[str]) -> None:
        if self.drag_in_progress:
            QTimer.singleShot(50, lambda: self.save_goal_order(goal_ids))
            return
        try:
            self.goals_manager.reorder_goals(goal_ids)
        except (OSError, ValueError) as error:
            self._show_error(str(error))
            return
        self.refresh()
        self._set_footer("Goal priorities saved.", glass_theme.EMERALD)


    def _clear_cards(self) -> None:
        self.cards = []
        while self.card_layout.count():
            child = self.card_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _show_error(self, message: str) -> None:
        self._clear_cards()
        self.card_layout.addWidget(make_label(message, 9, glass_theme.RED_SOFT))
        self.card_layout.addStretch()

    def _set_footer(self, text: str, color: str) -> None:
        self.lbl_footer.setText(text)
        self.lbl_footer.setStyleSheet(f"color: {color}; background: transparent;")

    def reflow(self) -> None:
        """Called when the window is resized so step text re-wraps."""
        for card in self.cards:
            card.update_width(self.content_width())

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self.reflow()
        super().resizeEvent(event)

    # -- clicks ---------------------------------------------------------------------------
    def _update_input_placeholder(self) -> None:
        """Tells the user what pressing Enter will add: a goal, or a step to the open goal."""
        if self.expanded_goal_id is None:
            self.input_edit.setPlaceholderText("Add a new goal... (press Enter)")
        else:
            self.input_edit.setPlaceholderText("Add a step to the open goal... (press Enter)")

    def add_from_input(self) -> None:
        """Adds what was typed as a new goal, or as a step when a goal card is open."""
        text = self.input_edit.text().strip()
        if not text:
            return
        try:
            if self.expanded_goal_id is None:
                goal = self.goals_manager.add_goal(text)
                self.expanded_goal_id = goal.id  # open it so the first step can be typed next
                self.smart_open = False
                self._flash("Goal added · now type its first step", glass_theme.EMERALD)
            else:
                self.goals_manager.add_step(self.expanded_goal_id, text)
                self._flash("Step added", glass_theme.EMERALD)
        except (KeyError, ValueError, OSError) as error:
            self._flash(f"Could not add: {error}", glass_theme.RED_SOFT)
            return
        self._update_input_placeholder()
        self.input_edit.clear()
        self.refresh()

    def toggle_card(self, goal_id: str) -> None:
        """Opens a card (closing any other) or closes it if it was already open."""
        if self.expanded_goal_id == goal_id:
            self.expanded_goal_id = None
        else:
            self.expanded_goal_id = goal_id
            self.smart_open = False  # a newly opened card starts on its steps
        self._update_input_placeholder()
        self.refresh()

    def toggle_smart(self, goal_id: str) -> None:
        self.smart_open = not self.smart_open
        self.refresh()

    def _on_focus_clicked(self, event: QtGui.QMouseEvent) -> None:
        if self._focus_goal_id and self.expanded_goal_id != self._focus_goal_id:
            self.toggle_card(self._focus_goal_id)
        event.accept()

    def _find_step(self, goal_id: str, step_number: int) -> Step | None:
        goal = self.goals_manager.get_goal(goal_id)
        if goal is None or not 1 <= step_number <= len(goal.steps):
            return None
        return goal.steps[step_number - 1]

    def toggle_step(self, goal_id: str, step_number: int) -> None:
        """Ticks a step, or unticks it if it was already done."""
        step = self._find_step(goal_id, step_number)
        if step is None:
            return  # the file changed under us; the next poll redraws it
        self.goals_manager.set_step_done(goal_id, step_number, done=not step.done)
        self.refresh()

    def toggle_stuck(self, goal_id: str, step_number: int) -> None:
        """Flags a step as stuck (or clears the flag)."""
        step = self._find_step(goal_id, step_number)
        if step is None:
            return
        self.goals_manager.set_step_stuck(goal_id, step_number, stuck=not step.stuck)
        if not step.stuck:
            self._flash(f"Marked stuck: step {step_number} may need splitting into smaller steps", glass_theme.AMBER)
        self.refresh()

    def open_step_log(self, goal_id: str, step_number: int) -> None:
        """Shows the log dialog for a step, and saves what the user typed if they press Save.

        If the save fails (locked file, a hand edit that broke the JSON), the
        dialog reopens with his words still in the box rather than losing them.
        """
        typed_text = ""
        while True:
            try:
                goal = self.goals_manager.get_goal(goal_id)
            except (OSError, ValueError) as error:
                self._flash(f"Could not read Goals.json: {error}", glass_theme.RED_SOFT)
                self.refresh()
                return
            if goal is None or not 1 <= step_number <= len(goal.steps):
                self._flash("That step has gone - the list changed.", glass_theme.AMBER)
                self.refresh()
                return
            dialog = StepLogDialog(goal, step_number, self.goals_manager.text_log_path,
                                   self.window(), initial_text=typed_text)
            # exec() waits here until the dialog closes, then says how it was closed.
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            typed_text = dialog.note_text()
            try:
                self.save_step_log(goal_id, step_number, typed_text, dialog.mark_done(),
                                   expected_text=dialog.step.text)
            except (OSError, ValueError, KeyError) as error:
                # Reopen with the words still there; the dialog shows which step
                # it is, so the user can check it is still the right one.
                self._flash(f"Not saved: {error}", glass_theme.RED_SOFT)
                continue
            return

    def save_step_log(
        self,
        goal_id: str,
        step_number: int,
        note_text: str,
        mark_done: bool,
        expected_text: str | None = None,
    ) -> None:
        """Saves the note and the done tick together, in one write.

        `expected_text` is the step text the dialog showed. If it no longer
        matches, an outside edit moved the steps and saving by number would
        land on the wrong step, so we refuse instead of writing.
        """
        step = self._find_step(goal_id, step_number)
        if step is None:
            raise ValueError("that step has gone - the list changed")
        if expected_text is not None and step.text != expected_text:
            self.refresh()
            raise ValueError("the list changed while the dialog was open - check the step text")
        note_text = note_text.strip()
        done_change = None if mark_done == step.done else mark_done
        if not note_text and done_change is None:
            return  # nothing typed and nothing switched: leave the file alone
        # One call, one save, one backup: a single undo takes both back together.
        self.goals_manager.update_step_log(goal_id, step_number, note_text, done_change)
        self._flash("Saved · also written to Goals Log.txt", glass_theme.EMERALD)
        self.refresh()

    def send_step_to_tasks(self, goal_id: str, step_number: int) -> None:
        """Copies a step onto the normal Tasks list (the step stays open here)."""
        try:
            item = self.goals_manager.send_step_to_tasks(goal_id, step_number, todo_manager=self.todo_manager)
        except (KeyError, IndexError, OSError) as error:
            self._flash(f"Could not send: {error}", glass_theme.RED_SOFT)
            return
        self._flash(f"Sent to Tasks as #{item.index}: {item.text}", glass_theme.EMERALD)

    def _flash(self, text: str, color: str) -> None:
        """Shows a message in the footer for a few seconds, then the hint returns."""
        self._set_footer(text, color)
        self._status_timer.start(STATUS_MESSAGE_MS)
