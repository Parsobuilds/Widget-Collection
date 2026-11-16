"""
Tests for goals_manager.py
==========================
Every test uses temporary files only: the real Goals.json, the real To Do List
and the real backups in %APPDATA% are never touched (see sandbox_env.py).
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

import sandbox_env  # noqa: F401,E402  (must come first: redirects APPDATA to a temp folder)
import goals_manager
from goals_manager import (
    Goal, GoalsManager, Step, is_stale, next_step, progress, todays_focus,
)
from todo_manager import TodoListManager

TODAY = datetime.date(2026, 11, 13)  # a Friday


class TempDirTestCase(unittest.TestCase):
    """Gives each test a private temp folder with manager objects on it."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_dir, True)
        self.goals_path = os.path.join(self.tmp_dir, "Goals.json")
        self.backup_path = os.path.join(self.tmp_dir, "backup", "Goals.json.bak")
        self.todo_path = os.path.join(self.tmp_dir, "To Do List.txt")
        self.todo_backup = os.path.join(self.tmp_dir, "backup", "todo.bak")
        self.mgr = GoalsManager(self.goals_path, self.backup_path)
        self.mgr.today_func = lambda: TODAY
        self.todo = TodoListManager(self.todo_path, self.todo_backup)

    def make_goal(self, title: str = "Learn to stream", **kwargs) -> Goal:
        return self.mgr.add_goal(title, **kwargs)


class GoalPriorityTests(TempDirTestCase):
    def test_finish_preserves_history_and_undo(self) -> None:
        self.mgr.add_goal("Done", goal_id="done")
        self.mgr.add_step("done", "Finish it")
        self.mgr.set_step_done("done", 1)
        self.mgr.finish_goal("done")
        goal = self.mgr.load_goals()[0]
        self.assertEqual(goal.status, "done")
        self.assertTrue(goal.steps[0].done)
        self.assertTrue(self.mgr.undo_last_change())
        self.assertEqual(self.mgr.load_goals()[0].status, "active")

    def test_unfinished_or_empty_goal_cannot_be_removed(self) -> None:
        self.mgr.add_goal("Work", goal_id="work")
        with self.assertRaises(ValueError):
            self.mgr.finish_goal("work")
        self.mgr.add_step("work", "Not done")
        with self.assertRaises(ValueError):
            self.mgr.finish_goal("work")

    def test_reorder_persists_and_appends_concurrent_additions(self) -> None:
        for key in ("first", "second", "new"):
            self.mgr.add_goal(key, goal_id=key)
            self.mgr.add_step(key, "Do something")
        old_touched = {g.id: g.last_touched for g in self.mgr.load_goals()}
        self.mgr.reorder_goals(["second", "first"])
        ordered = sorted(self.mgr.load_goals(), key=lambda g: g.priority)
        self.assertEqual([g.id for g in ordered], ["second", "first", "new"])
        self.assertEqual([g.priority for g in ordered], [1, 2, 3])
        self.assertEqual({g.id: g.last_touched for g in ordered}, old_touched)
        self.assertEqual(todays_focus(ordered)[0].id, "second")

    def test_bad_orders_do_not_write(self) -> None:
        self.mgr.add_goal("First", goal_id="first")
        before = Path(self.goals_path).read_bytes()
        for order in (None, "first", ["first", "first"], ["missing"], [1]):
            with self.assertRaises(ValueError):
                self.mgr.reorder_goals(order)
            self.assertEqual(Path(self.goals_path).read_bytes(), before)


class GoalAndStepTests(TempDirTestCase):
    def test_load_missing_file_does_not_create_it(self) -> None:
        self.assertEqual(self.mgr.load_goals(), [])
        self.assertFalse(os.path.exists(self.goals_path))

    def test_add_goal_slug_and_uniqueness(self) -> None:
        first = self.make_goal("Build Streaming into a Regular Hobby!")
        second = self.make_goal("Build Streaming into a Regular Hobby!")
        third = self.make_goal("Build Streaming into a Regular Hobby!")
        self.assertEqual(first.id, "build-streaming-into-a-regular")
        self.assertLessEqual(len(first.id), 30)
        self.assertEqual(second.id, first.id + "-2")
        self.assertEqual(third.id, first.id + "-3")
        self.assertEqual(first.created, "2026-11-13")
        self.assertEqual(first.time_bound.start, "2026-11-13")

    def test_add_and_insert_step(self) -> None:
        goal = self.make_goal("Fitness")
        self.mgr.add_step(goal.id, "one")
        self.mgr.add_step(goal.id, "three")
        self.mgr.add_step(goal.id, "two", position=2)
        texts = [s.text for s in self.mgr.get_goal(goal.id).steps]
        self.assertEqual(texts, ["one", "two", "three"])

    def test_done_and_undone(self) -> None:
        goal = self.make_goal("Fitness")
        self.mgr.add_step(goal.id, "run")
        self.mgr.set_step_stuck(goal.id, 1)
        step = self.mgr.set_step_done(goal.id, 1, learned="start slow")
        self.assertTrue(step.done)
        self.assertFalse(step.stuck)  # done clears stuck
        self.assertEqual(step.done_date, "2026-11-13")
        self.assertEqual(step.learned, "start slow")
        step = self.mgr.set_step_done(goal.id, 1, done=False)
        self.assertFalse(step.done)
        self.assertIsNone(step.done_date)

    def test_stuck_and_bad_numbers(self) -> None:
        goal = self.make_goal("Fitness")
        self.mgr.add_step(goal.id, "run")
        self.assertTrue(self.mgr.set_step_stuck(goal.id, 1).stuck)
        self.assertFalse(self.mgr.set_step_stuck(goal.id, 1, stuck=False).stuck)
        with self.assertRaises(IndexError):
            self.mgr.set_step_done(goal.id, 5)
        with self.assertRaises(KeyError):
            self.mgr.add_step("nope", "x")

    def test_split_step_in_place(self) -> None:
        goal = self.make_goal("Fitness")
        for text in ("a", "big", "z"):
            self.mgr.add_step(goal.id, text)
        self.mgr.split_step(goal.id, 2, ["b1", "b2"])
        texts = [s.text for s in self.mgr.get_goal(goal.id).steps]
        self.assertEqual(texts, ["a", "b1", "b2", "z"])

    def test_mutation_updates_last_touched(self) -> None:
        goal = self.make_goal("Fitness")
        self.mgr.today_func = lambda: TODAY + datetime.timedelta(days=3)
        self.mgr.add_step(goal.id, "run")
        self.assertEqual(self.mgr.get_goal(goal.id).last_touched, "2026-11-16")


class HelperTests(unittest.TestCase):
    def test_next_step_and_progress(self) -> None:
        goal = Goal(id="g", title="G", steps=[Step("a", done=True), Step("b"), Step("c")])
        number, step = next_step(goal)
        self.assertEqual((number, step.text), (2, "b"))
        self.assertEqual(progress(goal), (1, 3))
        goal.steps = [Step("a", done=True)]
        self.assertIsNone(next_step(goal))

    def test_todays_focus_priority_and_status(self) -> None:
        low = Goal(id="low", title="Low", priority=3, created="2026-01-01", steps=[Step("l")])
        high = Goal(id="high", title="High", priority=1, created="2026-02-01", steps=[Step("h")])
        paused = Goal(id="p", title="P", priority=1, status="paused", created="2025-01-01", steps=[Step("p")])
        finished = Goal(id="f", title="F", priority=1, created="2025-01-01", steps=[Step("f", done=True)])
        goal, number, step = todays_focus([low, paused, finished, high])
        self.assertEqual((goal.id, number, step.text), ("high", 1, "h"))
        goal, _, _ = todays_focus([low, paused, finished])
        self.assertEqual(goal.id, "low")
        self.assertIsNone(todays_focus([paused, finished]))

    def test_is_stale_boundary(self) -> None:
        four = Goal(id="g", title="G", last_touched="2026-11-09")
        five = Goal(id="g", title="G", last_touched="2026-11-08")
        self.assertFalse(is_stale(four, TODAY))
        self.assertTrue(is_stale(five, TODAY))

    def test_from_dict_tolerates_missing_keys(self) -> None:
        goal = Goal.from_dict({"title": "Bare"})
        self.assertEqual(goal.id, "bare")
        self.assertEqual(goal.status, "active")
        self.assertEqual(goal.steps, [])
        self.assertEqual(goal.time_bound.review_dates, [])
        goal = Goal.from_dict({"id": "x", "steps": [{"text": "s"}, "junk"], "priority": "bad"})
        self.assertEqual(len(goal.steps), 1)
        self.assertEqual(goal.priority, 3)
        self.assertFalse(goal.steps[0].done)


class TargetLogAndFieldTests(TempDirTestCase):
    def test_log_requires_existing_target(self) -> None:
        goal = self.make_goal("Stream")
        with self.assertRaises(ValueError):
            self.mgr.log_entry(goal.id, "streams", 1)
        self.mgr.add_target(goal.id, "streams", 2, "streams", "week")
        entry = self.mgr.log_entry(goal.id, "streams", 1, note="first")
        self.assertEqual(entry.date, "2026-11-13")
        self.assertEqual(len(self.mgr.get_goal(goal.id).log), 1)
        with self.assertRaises(ValueError):
            self.mgr.log_entry(goal.id, "streams", 1, date="13/11/2026")

    def test_set_field_validation(self) -> None:
        goal = self.make_goal("Stream")
        self.mgr.set_field(goal.id, "status", "paused")
        self.mgr.set_field(goal.id, "priority", "1")
        self.mgr.set_field(goal.id, "end", "2027-02-13")
        self.mgr.set_field(goal.id, "review-dates", "2026-12-11, 2027-01-11")
        self.mgr.set_field(goal.id, "time-note", "be kind")
        saved = self.mgr.get_goal(goal.id)
        self.assertEqual((saved.status, saved.priority), ("paused", 1))
        self.assertEqual(saved.time_bound.review_dates, ["2026-12-11", "2027-01-11"])
        for field_name, value in (("status", "bogus"), ("priority", "high"),
                                  ("end", "soon"), ("colour", "red"),
                                  ("review-dates", "2026-12-11,xx")):
            with self.assertRaises(ValueError, msg=field_name):
                self.mgr.set_field(goal.id, field_name, value)


class FileSafetyTests(TempDirTestCase):
    def test_backup_and_undo(self) -> None:
        goal = self.make_goal("Fitness")
        self.mgr.add_step(goal.id, "run")
        self.assertTrue(os.path.exists(self.backup_path))
        self.assertTrue(self.mgr.undo_last_change())
        self.assertEqual(self.mgr.get_goal(goal.id).steps, [])

    def test_undo_without_backup(self) -> None:
        self.assertFalse(self.mgr.undo_last_change())

    def test_atomic_write_leaves_valid_json(self) -> None:
        goal = self.make_goal("Fitness")
        self.mgr.add_step(goal.id, "run")
        with open(self.goals_path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["goals"][0]["steps"][0]["text"], "run")
        self.assertFalse(os.path.exists(self.goals_path + ".tmp"))


class TasksAndReviewTests(TempDirTestCase):
    def test_send_step_to_tasks(self) -> None:
        goal = self.make_goal("Stream more")
        self.mgr.add_step(goal.id, "Set up OBS")
        self.mgr.add_step(goal.id, "Go live")
        self.mgr.set_step_done(goal.id, 1)
        item = self.mgr.send_step_to_tasks(goal.id, None, self.todo)
        self.assertEqual(item.text, "Go live [goal: Stream more]")
        items, _ = self.todo.read_items()
        self.assertEqual([i.text for i in items], ["Go live [goal: Stream more]"])
        # The step is copied, not ticked.
        self.assertFalse(self.mgr.get_goal(goal.id).steps[1].done)
        self.mgr.send_step_to_tasks(goal.id, 1, self.todo)
        self.assertEqual(len(self.todo.read_items()[0]), 2)

    def test_review_text_contents(self) -> None:
        goal = self.make_goal("Stream more", end="2026-12-13", review_dates=["2026-12-01"], start="2026-10-16")
        for text in ("Set up OBS", "Go live", "Tell friends"):
            self.mgr.add_step(goal.id, text)
        self.mgr.set_step_done(goal.id, 1)
        self.mgr.set_step_stuck(goal.id, 3)
        self.mgr.add_target(goal.id, "streams", 2, "streams", "week")
        # TODAY is Friday 2026-11-13: Monday is 11-09. 11-08 is last week.
        self.mgr.log_entry(goal.id, "streams", 1, date="2026-11-10")
        self.mgr.log_entry(goal.id, "streams", 1, date="2026-11-12")
        self.mgr.log_entry(goal.id, "streams", 1, date="2026-11-08")
        text = self.mgr.review_text(goal.id, TODAY)
        self.assertIn("Steps done: 1/3", text)
        self.assertIn("Next step: 2. Go live", text)
        self.assertIn("Stuck: 3. Tell friends", text)
        self.assertIn("Last touched: 0 days ago", text)
        self.assertIn("Days left: 30", text)
        self.assertIn("Next review: 2026-12-01", text)
        self.assertIn("streams: 2 of 2 streams this week", text)
        self.assertIn("average 0.8 per week", text)  # 3 streams / 4 weeks
        self.assertIn("Stream more", self.mgr.review_text(None, TODAY))


class StepNoteTests(TempDirTestCase):
    """Notes are the 'what I did' lines the user writes on a step from the widget."""

    def setUp(self) -> None:
        super().setUp()
        self.goal = self.make_goal("Learn Python")
        self.mgr.add_step(self.goal.id, "Watch lesson one")

    def test_note_is_dated_stripped_and_survives_reload(self) -> None:
        note = self.mgr.add_step_note(self.goal.id, 1, "  Watched lesson one  ")
        self.assertEqual(note.date, "2026-11-13")
        self.assertEqual(note.text, "Watched lesson one")
        # Re-read from disk: the note must come back through to_dict/from_dict.
        step = self.mgr.get_goal(self.goal.id).steps[0]
        self.assertEqual([(n.date, n.text) for n in step.notes],
                         [("2026-11-13", "Watched lesson one")])

    def test_empty_note_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.mgr.add_step_note(self.goal.id, 1, "   \n ")

    def test_notes_travel_with_split_step(self) -> None:
        self.mgr.add_step_note(self.goal.id, 1, "Started it")
        self.mgr.split_step(self.goal.id, 1, ["Part A", "Part B"])
        steps = self.mgr.get_goal(self.goal.id).steps
        self.assertEqual([s.text for s in steps], ["Part A", "Part B"])
        # The old log moves to the first smaller step instead of being lost.
        self.assertEqual([n.text for n in steps[0].notes], ["Started it"])
        self.assertEqual(steps[1].notes, [])

    def test_update_step_log_saves_note_and_tick_once(self) -> None:
        self.mgr.update_step_log(self.goal.id, 1, "Wrote hello.py", True)
        step = self.mgr.get_goal(self.goal.id).steps[0]
        self.assertTrue(step.done)
        self.assertEqual(step.done_date, "2026-11-13")
        self.assertEqual([n.text for n in step.notes], ["Wrote hello.py"])
        # One save means one backup, so a single undo takes back BOTH changes.
        self.assertTrue(self.mgr.undo_last_change())
        step = self.mgr.get_goal(self.goal.id).steps[0]
        self.assertFalse(step.done)
        self.assertEqual(step.notes, [])

    def test_update_step_log_can_leave_done_alone(self) -> None:
        self.mgr.update_step_log(self.goal.id, 1, note_text="Just a note")
        step = self.mgr.get_goal(self.goal.id).steps[0]
        self.assertFalse(step.done)
        self.assertEqual([n.text for n in step.notes], ["Just a note"])

    def test_file_written_before_notes_existed_still_loads(self) -> None:
        # An older file may lack the "notes" key until the first note is added.
        with open(self.goals_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        del payload["goals"][0]["steps"][0]["notes"]
        with open(self.goals_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        step = self.mgr.get_goal(self.goal.id).steps[0]
        self.assertEqual(step.notes, [])


class GoalsTextLogTests(TempDirTestCase):
    """Every save rewrites 'Goals Log.txt' so the user has a readable copy."""

    def setUp(self) -> None:
        super().setUp()
        self.log_path = os.path.join(self.tmp_dir, "Goals Log.txt")

    def read_log(self) -> str:
        with open(self.log_path, encoding="utf-8") as handle:
            return handle.read()

    def test_log_lives_beside_the_json_file(self) -> None:
        self.assertEqual(self.mgr.text_log_path, self.log_path)

    def test_save_writes_goals_steps_and_header(self) -> None:
        goal = self.make_goal("Learn Python")
        self.mgr.add_step(goal.id, "Watch lesson one")
        text = self.read_log()
        self.assertIn("GOALS - STEPS AND LOGS", text)
        self.assertIn("Learn Python", text)
        self.assertIn("1. [ ] Watch lesson one", text)

    def test_log_is_rebuilt_not_appended(self) -> None:
        goal = self.make_goal("Learn Python")
        self.mgr.add_step(goal.id, "Watch lesson one")
        self.mgr.add_step(goal.id, "Write hello.py")
        self.assertEqual(self.read_log().count("Learn Python"), 1)

    def test_done_date_and_notes_appear(self) -> None:
        goal = self.make_goal("Learn Python")
        self.mgr.add_step(goal.id, "Watch lesson one")
        self.mgr.add_step_note(goal.id, 1, "Wrote hello.py")
        self.mgr.set_step_done(goal.id, 1)
        text = self.read_log()
        self.assertIn("[X] Watch lesson one  (done 2026-11-13)", text)
        self.assertIn("2026-11-13: Wrote hello.py", text)

    def test_undo_puts_the_log_back_in_step(self) -> None:
        goal = self.make_goal("Learn Python")
        self.mgr.add_step(goal.id, "Watch lesson one")
        self.mgr.add_step_note(goal.id, 1, "Wrote hello.py")
        self.assertIn("Wrote hello.py", self.read_log())
        self.assertTrue(self.mgr.undo_last_change())
        self.assertNotIn("Wrote hello.py", self.read_log())

    def test_unwritable_log_never_loses_the_save(self) -> None:
        # Put a *folder* where the log file should go, so writing it always fails.
        os.mkdir(self.log_path)
        goal = self.make_goal("Learn Python")
        self.mgr.add_step(goal.id, "Still saved")
        self.assertEqual([s.text for s in self.mgr.get_goal(goal.id).steps], ["Still saved"])


class CommandLineTests(TempDirTestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, APPDATA=self.tmp_dir, PYTHONIOENCODING="utf-8")
        command = [sys.executable, os.path.join(CURRENT_DIR, "goals_manager.py"),
                   "--file", self.goals_path, "--todo-file", self.todo_path, *args]
        return subprocess.run(command, capture_output=True, text=True,
                              encoding="utf-8", env=env, timeout=60)

    def test_cli_smoke(self) -> None:
        result = self.run_cli("add-goal", "Learn to stream", "--priority", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("learn-to-stream", result.stdout)
        self.run_cli("add-step", "learn-to-stream", "Install OBS")
        self.run_cli("add-step", "learn-to-stream", "Go live")
        self.run_cli("add-target", "learn-to-stream", "streams", "2", "streams", "week")
        self.assertEqual(self.run_cli("log", "learn-to-stream", "streams", "1").returncode, 0)
        done = self.run_cli("done-step", "learn-to-stream", "1", "--learned", "easy")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("learn-to-stream: 2. Go live", self.run_cli("next").stdout)
        self.assertIn("1/2 steps", self.run_cli("list").stdout)
        shown = self.run_cli("show", "learn-to-stream").stdout
        self.assertIn("[X] Install OBS", shown)
        self.assertIn("learned: easy", shown)
        self.assertIn("Steps done: 1/2", self.run_cli("review").stdout)
        sent = self.run_cli("send-to-tasks", "learn-to-stream")
        self.assertEqual(sent.returncode, 0, sent.stderr)
        items, _ = TodoListManager(self.todo_path, self.todo_backup).read_items()
        self.assertEqual(items[0].text, "Go live [goal: Learn to stream]")
        self.assertEqual(self.run_cli("undo").returncode, 0)
        # A custom --file / --todo-file backs up beside itself, never into
        # the app's data folder in %APPDATA%, where the real backups live.
        self.assertTrue(os.path.exists(self.goals_path + ".bak"))
        self.assertTrue(os.path.exists(self.todo_path + ".bak"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp_dir, "DesktopWidgets", "TodoWidget", "Goals.json.bak")))

    def test_cli_note_step_and_export(self) -> None:
        self.run_cli("add-goal", "Learn to stream")
        self.run_cli("add-step", "learn-to-stream", "Install OBS")
        noted = self.run_cli("note-step", "learn-to-stream", "1", "Watched lesson one")
        self.assertEqual(noted.returncode, 0, noted.stderr)
        self.assertIn("Watched lesson one", noted.stdout)
        self.assertIn("Watched lesson one", self.run_cli("show", "learn-to-stream").stdout)
        log_path = os.path.join(self.tmp_dir, "Goals Log.txt")
        self.assertTrue(os.path.exists(log_path))
        exported = self.run_cli("export")
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn("Goals Log.txt", exported.stdout)
        empty = self.run_cli("note-step", "learn-to-stream", "1", "   ")
        self.assertEqual(empty.returncode, 1)

    def test_cli_export_reports_a_locked_log(self) -> None:
        self.run_cli("add-goal", "Learn to stream")
        log_path = os.path.join(self.tmp_dir, "Goals Log.txt")
        os.remove(log_path)  # add-goal already wrote one
        os.mkdir(log_path)   # a folder cannot be written as a file
        result = self.run_cli("export")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error:", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_errors_are_friendly(self) -> None:
        result = self.run_cli("add-step", "missing", "text")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error:", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
