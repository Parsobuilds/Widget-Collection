"""
Tests for To-Do List Widget & Frosted-Glass Card Theme
=====================================================
Verifies theme constants, glass math, manager operations, widget setup, the per-user
data paths, the first-run state, the shared-background fallback and window snapping.
Every test runs inside the temporary sandbox from sandbox_env.py.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Must come first: points APPDATA at a temporary folder and Qt at its offscreen platform.
import sandbox_env  # noqa: F401

from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QImage, QMouseEvent

import glass_theme
import paths
import snap_widgets
import todo_widget
from todo_manager import TodoListManager, TodoItem

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class GlassThemeTests(unittest.TestCase):
    """Verifies frosted glass calculations and color recipes."""

    def test_opacity_fraction_normalization(self) -> None:
        self.assertAlmostEqual(glass_theme.opacity_fraction(0.5), 0.5)
        self.assertAlmostEqual(glass_theme.opacity_fraction(50), 0.5)
        self.assertAlmostEqual(glass_theme.opacity_fraction("35"), 0.35)
        # Clamped bounds
        self.assertAlmostEqual(glass_theme.opacity_fraction(0.05), 0.2)
        self.assertAlmostEqual(glass_theme.opacity_fraction(150), 1.0)
        # Invalid fallback
        self.assertAlmostEqual(glass_theme.opacity_fraction("invalid"), glass_theme.DEFAULT_TINT_OPACITY)

    def test_card_body_rect_and_path(self) -> None:
        rect = glass_theme.card_body_rect(380, 520)
        self.assertEqual(rect.x(), glass_theme.GLOW + 0.5)
        self.assertEqual(rect.y(), glass_theme.GLOW + 0.5)
        self.assertEqual(rect.width(), 380 - 2 * glass_theme.GLOW - 1)
        self.assertEqual(rect.height(), 520 - 2 * glass_theme.GLOW - 1)

        path = glass_theme.card_path(380, 520)
        self.assertFalse(path.isEmpty())

    def test_tone_curve_lengths(self) -> None:
        self.assertEqual(len(glass_theme.TONE_CURVE), 256)
        self.assertGreater(glass_theme.TONE_CURVE[-1], 0)
        self.assertLessEqual(glass_theme.TONE_CURVE[-1], 255)


class TodoManagerTests(unittest.TestCase):
    """Verifies item reading, parsing, saving, and toggle actions."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8")
        self.tmp.write("TO DO LIST\n==========\n\n[ ] 1. Buy milk   (added 2026-11-12)\n\n\n[X] 2. Call dentist   (added 2026-11-10)\n\n\nNotes line\n")
        self.tmp.close()
        # Keep the backup beside the temp file, never in the app's data folder.
        self.mgr = TodoListManager(file_path=self.tmp.name, backup_path=self.tmp.name + ".bak")

    def tearDown(self) -> None:
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)
        if os.path.exists(self.mgr.backup_path):
            os.remove(self.mgr.backup_path)

    def test_read_items(self) -> None:
        items, trailing = self.mgr.read_items()
        self.assertEqual(len(items), 2)
        self.assertFalse(items[0].is_done)
        self.assertTrue(items[1].is_done)
        self.assertIn("Notes line", trailing)

    def test_add_and_renumber(self) -> None:
        self.mgr.add_item("Test new item")
        items, _ = self.mgr.read_items()
        self.assertEqual(len(items), 3)
        self.assertEqual(items[2].index, 3)
        self.assertIn("Test new item", items[2].text)

    def test_clear_done(self) -> None:
        self.mgr.clear_done()
        items, _ = self.mgr.read_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].index, 1)
        self.assertEqual(items[0].text, "Buy milk")


class TodoReorderTests(unittest.TestCase):
    """Verifies drag-to-reorder slot maths and renumbering."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8")
        self.tmp.close()
        # Keep the backup beside the temp file, never in the app's data folder.
        self.mgr = TodoListManager(file_path=self.tmp.name, backup_path=self.tmp.name + ".bak")
        self._write(["Alpha", "Bravo", "Charlie", "Delta"])

    def tearDown(self) -> None:
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)
        if os.path.exists(self.mgr.backup_path):
            os.remove(self.mgr.backup_path)

    def _write(self, texts: list[str]) -> None:
        """Replaces the file with the given task texts."""
        items = [TodoItem(index=i, text=text) for i, text in enumerate(texts, start=1)]
        self.mgr.save_items(items)

    def _texts(self) -> list[str]:
        items, _ = self.mgr.read_items()
        return [item.text for item in items]

    def test_drag_bottom_to_top(self) -> None:
        self.assertTrue(self.mgr.move_item(4, 0))
        self.assertEqual(self._texts(), ["Delta", "Alpha", "Bravo", "Charlie"])

    def test_drag_top_to_bottom(self) -> None:
        self.assertTrue(self.mgr.move_item(1, 4))
        self.assertEqual(self._texts(), ["Bravo", "Charlie", "Delta", "Alpha"])

    def test_drag_middle_down_uses_post_removal_slot(self) -> None:
        self.assertTrue(self.mgr.move_item(2, 3))
        self.assertEqual(self._texts(), ["Alpha", "Charlie", "Bravo", "Delta"])

    def test_renumbers_after_move(self) -> None:
        self.mgr.move_item(4, 1)
        items, _ = self.mgr.read_items()
        self.assertEqual([item.index for item in items], [1, 2, 3, 4])

    def test_drop_in_place_is_noop(self) -> None:
        before = self.mgr.read_items()
        self.assertFalse(self.mgr.move_item(2, 1))
        self.assertFalse(self.mgr.move_item(2, 2))
        after = self.mgr.read_items()
        self.assertEqual([i.text for i in before[0]], [i.text for i in after[0]])


class PathTests(unittest.TestCase):
    """Verifies the per-user data folders sit under %APPDATA%\\DesktopWidgets\\TodoWidget."""

    def test_data_paths_are_under_the_app_folder(self) -> None:
        appdata = os.environ["APPDATA"]
        expected = os.path.join(appdata, "DesktopWidgets", "TodoWidget")
        self.assertEqual(paths.data_dir(), expected)
        self.assertTrue(os.path.isdir(expected))
        self.assertEqual(paths.todo_file(), os.path.join(expected, "todo", "To Do List.txt"))
        self.assertTrue(os.path.isdir(os.path.dirname(paths.todo_file())))
        self.assertEqual(paths.goals_file(), os.path.join(expected, "Goals.json"))
        self.assertEqual(paths.backup_file(), os.path.join(expected, "To Do List.txt.bak"))
        self.assertEqual(paths.goals_backup_file(), os.path.join(expected, "Goals.json.bak"))
        self.assertEqual(paths.config_file(), os.path.join(expected, "widget_config.json"))
        self.assertEqual(
            paths.widget_background_file(), os.path.join(appdata, "DesktopWidgets", "widget_background.json")
        )

    def test_suite_names(self) -> None:
        self.assertEqual(paths.INSTANCE_KEY, "DesktopWidgets.TodoWidget")
        self.assertEqual(paths.RUN_VALUE_NAME, "DesktopWidgets.TodoWidget")
        self.assertEqual(todo_widget.WINDOW_TITLE, "To-Do Widget")


class FirstRunTests(unittest.TestCase):
    """A brand-new profile gets the example tasks and an empty Goals tab."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="todo_widget_firstrun_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.todo_path = os.path.join(self.tmp_dir, "todo", "To Do List.txt")
        self.goals_path = os.path.join(self.tmp_dir, "Goals.json")

    def test_seed_copies_the_example_list_once(self) -> None:
        todo_widget.seed_first_run_files(self.todo_path)
        items, trailing = TodoListManager(self.todo_path, self.todo_path + ".bak").read_items()
        self.assertEqual(len(items), 3)
        self.assertTrue(all(not item.is_done for item in items))
        self.assertEqual(trailing, [])
        # A second call must never overwrite what the user has since changed.
        TodoListManager(self.todo_path, self.todo_path + ".bak").add_item("My own task")
        todo_widget.seed_first_run_files(self.todo_path)
        items, _ = TodoListManager(self.todo_path, self.todo_path + ".bak").read_items()
        self.assertEqual(len(items), 4)

    def test_fresh_profile_has_empty_goals(self) -> None:
        todo_widget.seed_first_run_files(self.todo_path)
        widget = todo_widget.DesktopTodoWidget(
            self.todo_path, self.goals_path, os.path.join(self.tmp_dir, "widget_config.json")
        )
        self.addCleanup(widget.deleteLater)
        self.assertEqual(widget.goals_manager.load_goals(), [])
        self.assertFalse(os.path.exists(self.goals_path))
        self.assertEqual(widget.lbl_stats.text(), "3 open / 3 total")

    def test_selftest_writes_both_pictures(self) -> None:
        widget = todo_widget.DesktopTodoWidget(
            self.todo_path, self.goals_path, os.path.join(self.tmp_dir, "widget_config.json")
        )
        self.addCleanup(widget.deleteLater)
        self.assertEqual(todo_widget.run_selftest(widget), 0)
        self.assertTrue(os.path.getsize(os.path.join(paths.data_dir(), "selftest_card.png")) > 0)
        self.assertTrue(os.path.getsize(os.path.join(paths.data_dir(), "selftest_card_goals.png")) > 0)


class SharedBackgroundTests(unittest.TestCase):
    """The shared background file is read and written; the legacy file is only ever read."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="todo_widget_bg_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.new_path = os.path.join(self.tmp_dir, "DesktopWidgets", "widget_background.json")
        self.legacy_path = os.path.join(self.tmp_dir, "OldFolder", "widget_background.json")
        os.makedirs(os.path.dirname(self.legacy_path))
        # A real (tiny) picture, because the loader only accepts an existing image file.
        self.image_path = os.path.join(self.tmp_dir, "picture.png")
        image = QImage(4, 4, QImage.Format.Format_RGB32)
        image.fill(0x336699)
        self.assertTrue(image.save(self.image_path))
        with open(self.legacy_path, "w", encoding="utf-8") as handle:
            json.dump({"image_path": self.image_path, "blur_radius": 11.0}, handle)

    def test_legacy_file_is_read_when_the_new_one_is_missing(self) -> None:
        settings = glass_theme.load_widget_bg_config(self.new_path, self.legacy_path)
        self.assertIsNotNone(settings)
        self.assertEqual(settings.image_path, self.image_path)
        self.assertEqual(settings.blur_radius, 11.0)

    def test_saving_writes_only_the_new_file(self) -> None:
        with open(self.legacy_path, "rb") as handle:
            legacy_before = handle.read()
        data = glass_theme.save_widget_bg_config({"brightness": 0.7}, self.new_path, self.legacy_path)
        self.assertTrue(os.path.isfile(self.new_path))
        self.assertEqual(data["brightness"], 0.7)
        self.assertEqual(data["blur_radius"], 11.0)  # carried over from the legacy file
        with open(self.legacy_path, "rb") as handle:
            self.assertEqual(handle.read(), legacy_before)  # legacy file untouched

    def test_new_file_wins_once_it_exists(self) -> None:
        glass_theme.save_widget_bg_config({"blur_radius": 3.0}, self.new_path, self.legacy_path)
        settings = glass_theme.load_widget_bg_config(self.new_path, self.legacy_path)
        self.assertEqual(settings.blur_radius, 3.0)

    def test_no_files_means_no_custom_background(self) -> None:
        missing = os.path.join(self.tmp_dir, "nothing", "widget_background.json")
        self.assertIsNone(glass_theme.load_widget_bg_config(missing, missing + ".legacy"))

    def test_default_locations_live_in_the_sandbox(self) -> None:
        sandbox = os.environ["TODOWIDGET_TEST_SANDBOX"]
        self.assertTrue(glass_theme.WIDGET_BG_CONFIG_PATH.startswith(sandbox))
        self.assertTrue(glass_theme.LEGACY_BG_CONFIG_PATH.startswith(sandbox))
        self.assertNotEqual(glass_theme.WIDGET_BG_CONFIG_PATH, glass_theme.LEGACY_BG_CONFIG_PATH)


class SnapLayoutTests(unittest.TestCase):
    """The snap layout is plain arithmetic on the screen's work area."""

    def test_cards_line_up_from_the_bottom_right(self) -> None:
        area = (0, 0, 1920, 1040)
        positions = snap_widgets.plan_row(area, [(320, 448), (320, 448), (300, 400)])
        gap = snap_widgets.GAP
        self.assertEqual(positions[0], (1920 - 320, 1040 - 448))
        self.assertEqual(positions[1], (1920 - 320 - gap - 320, 1040 - 448))
        self.assertEqual(positions[2], (positions[1][0] - gap - 300, 1040 - 400))

    def test_negative_coordinate_screens_and_tiny_areas_stay_inside(self) -> None:
        area = (-1080, 100, 0, 700)
        positions = snap_widgets.plan_row(area, [(320, 448), (320, 448), (320, 448), (320, 448)])
        for x, y in positions:
            self.assertGreaterEqual(x, -1080)
            self.assertGreaterEqual(y, 100)
        self.assertEqual(positions[0][0], -320)


class TodoWidgetImportTests(unittest.TestCase):
    """Verifies that todo_widget loads cleanly and defines its classes."""

    def test_widget_classes_available(self) -> None:
        import todo_widget
        self.assertTrue(hasattr(todo_widget, "DesktopTodoWidget"))
        self.assertTrue(hasattr(todo_widget, "TaskItemWidget"))
        self.assertTrue(hasattr(todo_widget, "PillButton"))


class TodoWidgetResizeTests(unittest.TestCase):
    """Verifies edge/corner resizing, mouse grab behavior, and header window dragging."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="todo_widget_resize_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.todo_path = os.path.join(self.tmp_dir, "To Do List.txt")
        self.goals_path = os.path.join(self.tmp_dir, "Goals.json")
        self.config_path = os.path.join(self.tmp_dir, "widget_config.json")

        with open(self.todo_path, "w", encoding="utf-8") as f:
            f.write("TO DO LIST\n==========\n\n[ ] 1. Test task\n")
        with open(self.goals_path, "w", encoding="utf-8") as f:
            f.write("[]")

        self.widget = todo_widget.DesktopTodoWidget(self.todo_path, self.goals_path, self.config_path)
        self.addCleanup(self.widget.deleteLater)
        self.widget.setGeometry(100, 100, 400, 500)
        self.widget.show()
        APP.processEvents()

    def _drag(self, start_pos: QPoint, end_pos: QPoint) -> None:
        """Simulates a complete mouse drag from start_pos to end_pos in widget coordinates."""
        start_glob = self.widget.mapToGlobal(start_pos)
        end_glob = self.widget.mapToGlobal(end_pos)

        press = QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QPointF(start_pos),
            QPointF(start_glob),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.widget.mousePressEvent(press)

        move = QMouseEvent(
            QtCore.QEvent.Type.MouseMove,
            QPointF(end_pos),
            QPointF(end_glob),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.widget.mouseMoveEvent(move)

        release = QMouseEvent(
            QtCore.QEvent.Type.MouseButtonRelease,
            QPointF(end_pos),
            QPointF(end_glob),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.widget.mouseReleaseEvent(release)
        APP.processEvents()

    def test_resize_right_edge_expands_width(self) -> None:
        """Dragging the right edge outward expands width while keeping (x, y) fixed."""
        start_geo = self.widget.geometry()
        w, h = start_geo.width(), start_geo.height()
        # Drag right edge outward by 50 px
        self._drag(QPoint(w - 10, h // 2), QPoint(w + 40, h // 2))
        new_geo = self.widget.geometry()
        self.assertEqual(new_geo.x(), start_geo.x())
        self.assertEqual(new_geo.y(), start_geo.y())
        self.assertEqual(new_geo.width(), w + 50)
        self.assertEqual(new_geo.height(), h)

    def test_resize_left_edge_on_visible_border(self) -> None:
        """Grabbing the visible card border on the left (x=15) resizes width and moves x."""
        start_geo = self.widget.geometry()
        w, h = start_geo.width(), start_geo.height()
        # x = 15 sits between GLOW (8 px) and EDGE_PAD (22 px), right on the visible glass border
        self._drag(QPoint(15, h // 2), QPoint(-35, h // 2))
        new_geo = self.widget.geometry()
        # Right edge must stay anchored: x + width must equal original start_geo.x() + w (500)
        self.assertEqual(new_geo.x() + new_geo.width(), start_geo.x() + w)
        self.assertEqual(new_geo.x(), start_geo.x() - 50)
        self.assertEqual(new_geo.width(), w + 50)
        self.assertEqual(new_geo.y(), start_geo.y())
        self.assertEqual(new_geo.height(), h)

    def test_resize_bottom_edge_expands_height(self) -> None:
        """Dragging the bottom edge downward expands height while keeping (x, y) fixed."""
        start_geo = self.widget.geometry()
        w, h = start_geo.width(), start_geo.height()
        self._drag(QPoint(w // 2, h - 15), QPoint(w // 2, h + 35))
        new_geo = self.widget.geometry()
        self.assertEqual(new_geo.x(), start_geo.x())
        self.assertEqual(new_geo.y(), start_geo.y())
        self.assertEqual(new_geo.width(), w)
        self.assertEqual(new_geo.height(), h + 50)

    def test_resize_top_edge_on_visible_border(self) -> None:
        """Dragging the top visible card border (y <= 8) expands height while bottom edge stays anchored."""
        start_geo = self.widget.geometry()
        w, h = start_geo.width(), start_geo.height()
        # Top card edge sits at y = 6..8
        self._drag(QPoint(w // 2, 6), QPoint(w // 2, -44))
        new_geo = self.widget.geometry()
        # Bottom edge must stay anchored: y + height must equal original start_geo.y() + h (600)
        self.assertEqual(new_geo.y() + new_geo.height(), start_geo.y() + h)
        self.assertEqual(new_geo.y(), start_geo.y() - 50)
        self.assertEqual(new_geo.height(), h + 50)
        self.assertEqual(new_geo.x(), start_geo.x())
        self.assertEqual(new_geo.width(), w)

    def test_resize_bottom_right_corner_grip(self) -> None:
        """Dragging the painted bottom-right corner grip marks (w - 18, h - 18) resizes diagonally."""
        start_geo = self.widget.geometry()
        w, h = start_geo.width(), start_geo.height()
        # Coordinates of the visible diagonal grip tick marks painted in paintEvent
        rx = w - glass_theme.EDGE_PAD + 4  # w - 18
        ry = h - glass_theme.EDGE_PAD + 4  # h - 18
        self._drag(QPoint(rx, ry), QPoint(rx + 40, ry + 30))
        new_geo = self.widget.geometry()
        self.assertEqual(new_geo.x(), start_geo.x())
        self.assertEqual(new_geo.y(), start_geo.y())
        self.assertEqual(new_geo.width(), w + 40)
        self.assertEqual(new_geo.height(), h + 30)

    def test_header_drag_moves_window_without_resizing(self) -> None:
        """Dragging anywhere inside the header bar (above pills, between pills, or on title) moves window."""
        start_geo = self.widget.geometry()
        w, h = start_geo.width(), start_geo.height()
        # Dragging at y = 15 (above pills in the header) moves the window
        self._drag(QPoint(w // 2, 15), QPoint(w // 2 + 30, 15 + 20))
        geo1 = self.widget.geometry()
        self.assertEqual(geo1.x(), start_geo.x() + 30)
        self.assertEqual(geo1.y(), start_geo.y() + 20)
        self.assertEqual(geo1.width(), w)
        self.assertEqual(geo1.height(), h)

        # Dragging at y = 42 (between pills) moves the window
        self._drag(QPoint(w // 2, 42), QPoint(w // 2 - 20, 42 - 10))
        geo2 = self.widget.geometry()
        self.assertEqual(geo2.x(), geo1.x() - 20)
        self.assertEqual(geo2.y(), geo1.y() - 10)
        self.assertEqual(geo2.width(), w)
        self.assertEqual(geo2.height(), h)

        # Dragging at y = 60 (title row) moves the window
        self._drag(QPoint(w // 2, 60), QPoint(w // 2 + 10, 60 + 15))
        geo3 = self.widget.geometry()
        self.assertEqual(geo3.x(), geo2.x() + 10)
        self.assertEqual(geo3.y(), geo2.y() + 15)
        self.assertEqual(geo3.width(), w)
        self.assertEqual(geo3.height(), h)

    def test_left_border_near_header_resizes_rather_than_moves(self) -> None:
        """Grabbing the left border at header height (x=15, y=42) triggers resize, not window move."""
        start_geo = self.widget.geometry()
        w, h = start_geo.width(), start_geo.height()
        # x = 15 is on the left edge band; even though y = 42 is in the header y-range,
        # edge resizing must take precedence over window dragging.
        self._drag(QPoint(15, 42), QPoint(-35, 42))
        new_geo = self.widget.geometry()
        self.assertEqual(new_geo.x(), start_geo.x() - 50)
        self.assertEqual(new_geo.width(), w + 50)
        self.assertEqual(new_geo.y(), start_geo.y())
        self.assertEqual(new_geo.height(), h)

    def test_scrollbar_area_is_not_intercepted_by_resize(self) -> None:
        """Hit test on the scrollbar (at w - 25) must not claim right edge resize."""
        w, h = self.widget.width(), self.widget.height()
        # Scrollbar sits at [w - 27, w - 22]
        left, right, top, bottom = self.widget._hit_test(QPoint(w - 25, h // 2))
        self.assertFalse(right, "Scrollbar area at w - 25 must not be intercepted by resize hit test")

    def test_mouse_grab_and_release_during_resize(self) -> None:
        """Widget grabs the mouse when resize begins and releases it when mouse is released."""
        w, h = self.widget.width(), self.widget.height()
        press_pos = QPoint(w - 10, h // 2)
        press = QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QPointF(press_pos),
            QPointF(self.widget.mapToGlobal(press_pos)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.widget.mousePressEvent(press)
        self.assertIsNotNone(self.widget._resizing_edges)
        self.assertEqual(QtWidgets.QWidget.mouseGrabber(), self.widget)

        release = QMouseEvent(
            QtCore.QEvent.Type.MouseButtonRelease,
            QPointF(press_pos),
            QPointF(self.widget.mapToGlobal(press_pos)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.widget.mouseReleaseEvent(release)
        self.assertIsNone(self.widget._resizing_edges)
        self.assertIsNone(QtWidgets.QWidget.mouseGrabber())


if __name__ == "__main__":
    unittest.main()
