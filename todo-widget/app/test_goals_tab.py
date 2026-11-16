"""
Tests for the Goals tab of the desktop to-do widget
===================================================

These tests build the real widget with Qt's "offscreen" platform (no window
appears) and point it at temporary copies of every file, so the real
To Do List.txt, Goals.json and widget_config.json are never touched.

Run from the repo root:
    python -m unittest discover -s app -p "test_*.py" -v
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

import sandbox_env  # noqa: E402,F401  (must come first: redirects APPDATA to a temp folder)
from PySide6 import QtWidgets  # noqa: E402

import todo_widget  # noqa: E402
import goals_tab  # noqa: E402
from goals_manager import GoalsManager  # noqa: E402
from todo_manager import TodoListManager  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class GoalsTabTests(unittest.TestCase):
    """Drives the Goals tab the way a click would, then checks the files."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="goals_tab_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.todo_path = os.path.join(self.tmp_dir, "To Do List.txt")
        self.goals_path = os.path.join(self.tmp_dir, "Goals.json")
        self.config_path = os.path.join(self.tmp_dir, "widget_config.json")

        goals = GoalsManager(self.goals_path, self.goals_path + ".bak")
        goals.add_goal("Build streaming into a regular hobby", goal_id="streaming", priority=1,
                       specific="Stream games I enjoy on a schedule", end="2027-02-13")
        for text in ("Decide the platform", "Pick two stream days", "Do the first stream"):
            goals.add_step("streaming", text)
        goals.add_goal("Tidy the garage", goal_id="garage", priority=2)
        goals.add_step("garage", "Clear one shelf")

        self.widget = todo_widget.DesktopTodoWidget(self.todo_path, self.goals_path, self.config_path)
        self.addCleanup(self.widget.deleteLater)
        self.panel = self.widget.goals_panel

    def _read_goals(self) -> dict:
        with open(self.goals_path, encoding="utf-8") as handle:
            return {goal["id"]: goal for goal in json.load(handle)["goals"]}

    def test_finished_button_hides_card_but_keeps_record(self) -> None:
        manager = self.panel.goals_manager
        manager.set_step_done("garage", 1)
        self.panel.refresh()
        card = next(card for card in self.panel.cards if card.goal.id == "garage")
        card.finish_button.click()
        self.assertEqual([card.goal.id for card in self.panel.cards], ["streaming"])
        self.assertEqual(self._read_goals()["garage"]["status"], "done")


    def test_drag_drop_updates_priorities_and_focus(self) -> None:
        from PySide6 import QtCore, QtGui
        self.widget.show_tab("goals")
        self.widget.show()
        APP.processEvents()
        mime = QtCore.QMimeData()
        mime.setData(goals_tab.GOAL_MIME_TYPE, b"garage")
        drop = QtGui.QDropEvent(QtCore.QPointF(5, 0), QtCore.Qt.DropAction.MoveAction,
                               mime, QtCore.Qt.MouseButton.LeftButton,
                               QtCore.Qt.KeyboardModifier.NoModifier)
        self.panel.card_container.dropEvent(drop)
        APP.processEvents()
        self.assertTrue(drop.isAccepted())
        self.assertEqual([card.goal.id for card in self.panel.cards], ["garage", "streaming"])
        self.assertEqual(self._read_goals()["garage"]["priority"], 1)
        self.assertIn("Clear one shelf", self.panel.lbl_focus_step.text())
        self.widget.hide()


    def test_tab_switch_is_remembered(self) -> None:
        self.widget.show_tab("goals")
        self.assertEqual(self.widget.pages.currentWidget(), self.panel)
        self.assertTrue(self.widget.btn_goals.isActive())
        self.assertFalse(self.widget.btn_tasks.isActive())
        with open(self.config_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["tab"], "goals")
        # A fresh widget opens on the tab that was open last time.
        reopened = todo_widget.DesktopTodoWidget(self.todo_path, self.goals_path, self.config_path)
        self.addCleanup(reopened.deleteLater)
        self.assertEqual(reopened.current_tab, "goals")

    def test_focus_is_next_step_of_top_goal(self) -> None:
        self.assertEqual(self.panel.lbl_focus_step.text(), "1. Decide the platform")
        self.assertEqual([card.goal.id for card in self.panel.cards], ["streaming", "garage"])

    def test_only_one_card_open(self) -> None:
        self.panel.toggle_card("streaming")
        self.panel.toggle_card("garage")
        open_cards = [card.goal.id for card in self.panel.cards if card.expanded]
        self.assertEqual(open_cards, ["garage"])
        self.panel.toggle_card("garage")
        self.assertFalse(any(card.expanded for card in self.panel.cards))

    def test_tick_step_writes_file_and_moves_focus(self) -> None:
        self.panel.toggle_card("streaming")
        self.panel.toggle_step("streaming", 1)
        steps = self._read_goals()["streaming"]["steps"]
        self.assertTrue(steps[0]["done"])
        self.assertEqual(self.panel.lbl_focus_step.text(), "2. Pick two stream days")
        self.panel.toggle_step("streaming", 1)  # a second click unticks it
        self.assertFalse(self._read_goals()["streaming"]["steps"][0]["done"])

    def test_stuck_flag(self) -> None:
        self.panel.toggle_stuck("streaming", 2)
        self.assertTrue(self._read_goals()["streaming"]["steps"][1]["stuck"])
        self.assertIn("stuck", self.panel.lbl_footer.text())

    def test_send_to_tasks_uses_the_temp_list(self) -> None:
        self.panel.send_step_to_tasks("streaming", 1)
        items, _ = TodoListManager(self.todo_path, self.todo_path + ".bak").read_items()
        self.assertEqual(items[-1].text, "Decide the platform [goal: Build streaming into a regular hobby]")
        self.assertIn("Sent to Tasks", self.panel.lbl_footer.text())
        # The step is copied, not ticked.
        self.assertFalse(self._read_goals()["streaming"]["steps"][0]["done"])

    def test_stale_goal_card_is_amber(self) -> None:
        with open(self.goals_path, encoding="utf-8") as handle:
            data = json.load(handle)
        old_day = (datetime.date.today() - datetime.timedelta(days=6)).isoformat()
        data["goals"][1]["last_touched"] = old_day
        with open(self.goals_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        self.panel.refresh()
        stale = {card.goal.id: card.stale for card in self.panel.cards}
        self.assertEqual(stale, {"streaming": False, "garage": True})

    def test_outside_edit_is_picked_up_by_polling(self) -> None:
        outside = GoalsManager(self.goals_path, self.goals_path + ".bak")
        outside.set_step_done("streaming", 1)
        # Make sure the modified time really moves on, even on a coarse clock.
        later = os.path.getmtime(self.goals_path) + 5
        os.utime(self.goals_path, (later, later))
        self.panel.poll_file_changes()
        self.assertEqual(self.panel.lbl_focus_step.text(), "2. Pick two stream days")

    def test_bad_json_shows_message_instead_of_crashing(self) -> None:
        with open(self.goals_path, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.panel.refresh()
        self.assertEqual(self.panel.cards, [])

    def test_clicking_step_text_opens_log_not_tick(self) -> None:
        # The row is built on its own, not inside the panel: a real click on the
        # text would call the panel's open_step_log, whose dialog.exec() waits in
        # a modal loop for a person to close it. Here we only check the zones.
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from goals_manager import Step

        row = goals_tab.StepRow(1, Step(text="Do the first thing"), True, 300)
        self.addCleanup(row.deleteLater)
        seen: list = []
        row.opened.connect(seen.append)
        row.toggled.connect(lambda number: seen.append(("tick", number)))
        row.stuck_toggled.connect(lambda number: seen.append(("stuck", number)))

        def click(x: float) -> None:
            row.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, QPointF(x, 6),
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ))

        click(row.width() / 2)  # on the text: opens the log, does not tick
        self.assertEqual(seen, [1])
        seen.clear()
        click(8)  # on the tick circle: ticks the step, does not open the log
        self.assertEqual(seen, [("tick", 1)])

    def test_open_step_log_saves_when_dialog_accepted(self) -> None:
        # Swap the real dialog for a stand-in so the modal exec() never runs.
        original = goals_tab.StepLogDialog

        class FakeDialog:
            def __init__(self, goal, step_number, *args, **kwargs) -> None:
                self.step = goal.steps[step_number - 1]  # like the real dialog

            def exec(self):
                return goals_tab.QtWidgets.QDialog.DialogCode.Accepted

            def note_text(self) -> str:
                return "Typed in the dialog"

            def mark_done(self) -> bool:
                return False

        goals_tab.StepLogDialog = FakeDialog
        self.addCleanup(setattr, goals_tab, "StepLogDialog", original)

        self.panel.open_step_log("streaming", 1)
        notes = self._read_goals()["streaming"]["steps"][0]["notes"]
        self.assertEqual([note["text"] for note in notes], ["Typed in the dialog"])

    def test_open_step_log_ignores_cancel(self) -> None:
        original = goals_tab.StepLogDialog

        class CancelledDialog:
            def __init__(self, goal, step_number, *args, **kwargs) -> None:
                self.step = goal.steps[step_number - 1]

            def exec(self):
                return goals_tab.QtWidgets.QDialog.DialogCode.Rejected

            def note_text(self) -> str:
                return "Should never be saved"

            def mark_done(self) -> bool:
                return True

        goals_tab.StepLogDialog = CancelledDialog
        self.addCleanup(setattr, goals_tab, "StepLogDialog", original)

        self.panel.open_step_log("streaming", 1)
        step = self._read_goals()["streaming"]["steps"][0]
        self.assertEqual(step["notes"], [])
        self.assertFalse(step["done"])

    def test_save_step_log_adds_note_and_writes_text_log(self) -> None:
        self.panel.save_step_log("streaming", 1, "  Decided on Twitch  ", False)
        step = self._read_goals()["streaming"]["steps"][0]
        self.assertEqual([note["text"] for note in step["notes"]], ["Decided on Twitch"])
        self.assertEqual(step["notes"][0]["date"], datetime.date.today().isoformat())
        with open(os.path.join(self.tmp_dir, "Goals Log.txt"), encoding="utf-8") as handle:
            self.assertIn("Decided on Twitch", handle.read())
        self.assertIn("Saved", self.panel.lbl_footer.text())

    def test_save_step_log_can_tick_done_at_the_same_time(self) -> None:
        self.panel.save_step_log("streaming", 1, "", True)
        self.assertTrue(self._read_goals()["streaming"]["steps"][0]["done"])
        self.assertEqual(self.panel.lbl_focus_step.text(), "2. Pick two stream days")

    def test_saving_nothing_changes_nothing(self) -> None:
        self.panel.save_step_log("streaming", 1, "   ", False)
        self.assertEqual(self._read_goals()["streaming"]["steps"][0]["notes"], [])
        self.assertEqual(self.panel.lbl_footer.text(), goals_tab.FOOTER_HINT)

    def test_dialog_shows_history_and_returns_what_was_typed(self) -> None:
        self.panel.save_step_log("streaming", 1, "First log", False)
        goal = self.panel.goals_manager.get_goal("streaming")
        dialog = goals_tab.StepLogDialog(goal, 1, self.panel.goals_manager.text_log_path)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.mark_done())  # starts unticked for an open step
        dialog.note_edit.setPlainText("  Second log  ")
        self.assertEqual(dialog.note_text(), "Second log")
        dialog.done_box.setChecked(True)
        self.assertTrue(dialog.mark_done())
        # Words left over from a failed save come back in the box.
        prefilled = goals_tab.StepLogDialog(goal, 1, self.panel.goals_manager.text_log_path,
                                            initial_text="Kept words")
        self.addCleanup(prefilled.deleteLater)
        self.assertEqual(prefilled.note_text(), "Kept words")

    def test_note_and_tick_are_taken_back_by_one_undo(self) -> None:
        self.panel.save_step_log("streaming", 1, "Decided on Twitch", True)
        step = self._read_goals()["streaming"]["steps"][0]
        self.assertTrue(step["done"])
        self.assertEqual([note["text"] for note in step["notes"]], ["Decided on Twitch"])
        # The note and the tick were saved together, so one undo takes both back.
        self.assertTrue(self.panel.goals_manager.undo_last_change())
        step = self._read_goals()["streaming"]["steps"][0]
        self.assertFalse(step["done"])
        self.assertEqual(step["notes"], [])

    def test_save_refuses_when_the_steps_moved_underneath(self) -> None:
        outside = GoalsManager(self.goals_path, self.goals_path + ".bak")
        outside.add_step("streaming", "A new first step", position=1)
        with self.assertRaises(ValueError):
            self.panel.save_step_log("streaming", 1, "Should not land", False,
                                     expected_text="Decide the platform")
        step = self._read_goals()["streaming"]["steps"][0]
        self.assertEqual(step["text"], "A new first step")
        self.assertEqual(step["notes"], [])

    def test_tasks_tab_unchanged(self) -> None:
        self.widget.input_edit.setText("Buy milk")
        self.widget._on_add_task()
        items, _ = self.widget.manager.read_items()
        self.assertEqual([item.text for item in items], ["Buy milk"])
        self.assertEqual(self.widget.lbl_stats.text(), "1 open / 1 total")


class GoalsTabAddTests(unittest.TestCase):
    """The input line at the bottom of the Goals tab adds goals and steps."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="goals_tab_add_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.goals_path = os.path.join(self.tmp_dir, "Goals.json")
        self.widget = todo_widget.DesktopTodoWidget(
            os.path.join(self.tmp_dir, "To Do List.txt"),
            self.goals_path,
            os.path.join(self.tmp_dir, "widget_config.json"),
        )
        self.addCleanup(self.widget.deleteLater)
        self.panel = self.widget.goals_panel

    def test_empty_state_is_friendly(self) -> None:
        self.assertEqual(self.panel.goals_manager.load_goals(), [])
        self.assertEqual(self.panel.lbl_stats.text(), "0 active · 0/0 steps")
        texts = [self.panel.card_layout.itemAt(i).widget().text()
                 for i in range(self.panel.card_layout.count())
                 if self.panel.card_layout.itemAt(i).widget() is not None]
        self.assertTrue(any("No goals yet." in text for text in texts))
        self.assertFalse(os.path.exists(self.goals_path))  # nothing is written until a goal exists

    def test_typing_a_title_adds_a_goal_then_steps(self) -> None:
        self.panel.input_edit.setText("Learn to cook")
        self.panel.add_from_input()
        goals = self.panel.goals_manager.load_goals()
        self.assertEqual([goal.title for goal in goals], ["Learn to cook"])
        self.assertEqual(self.panel.expanded_goal_id, goals[0].id)  # the new goal opens
        self.panel.input_edit.setText("Buy a pan")
        self.panel.add_from_input()
        goals = self.panel.goals_manager.load_goals()
        self.assertEqual([step.text for step in goals[0].steps], ["Buy a pan"])
        self.assertEqual(self.panel.lbl_focus_step.text(), "1. Buy a pan")

    def test_blank_input_adds_nothing(self) -> None:
        self.panel.input_edit.setText("   ")
        self.panel.add_from_input()
        self.assertEqual(self.panel.goals_manager.load_goals(), [])


if __name__ == "__main__":
    unittest.main()
