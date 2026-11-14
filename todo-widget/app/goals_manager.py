"""
SMART Goals Manager
===================

A Python-first utility that stores the user's SMART goals in one JSON file
('%APPDATA%\\DesktopWidgets\\TodoWidget\\Goals.json') and gives both the command line and the
desktop widget a safe way to read and change it.

SMART means each goal is Specific, Measurable, Achievable, Relevant and
Time-bound. A goal also has small *steps* (a checklist), numeric *targets*
(for example "2 streams per week") and a *log* of progress against those
targets.

How the file is laid out
------------------------
The JSON file looks like this::

    {"version": 1, "goals": [ {"id": "streaming", "title": "...", ...} ]}

How the pieces fit together
---------------------------
1. Dataclasses (Step, Target, LogEntry, TimeBound, Goal) describe the data.
   Each can turn itself into a plain dict (``to_dict``) and back
   (``from_dict``). ``from_dict`` tolerates missing keys so a hand edit to the
   JSON cannot crash the widget.
2. GoalsManager reads and writes the file. Every change is saved with a
   backup first (so ``undo`` works) and an atomic write (so the widget never
   sees a half-written file).
3. Small pure helper functions (next_step, todays_focus, is_stale, progress)
   do not touch the disk. The widget imports these directly.
4. ``main()`` is the command line, built with argparse subcommands.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# The to-do manager lives in this same folder; make sure we can import it
# whether we are run as a script or imported from elsewhere.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Default file paths live in the per-user data folder (see paths.py).
import paths  # noqa: E402  (needs CURRENT_DIR on sys.path first, see above)

DEFAULT_GOALS_PATH = paths.goals_file()
BACKUP_GOALS_PATH = paths.goals_backup_file()

# A goal not touched for this many days is "stale" (the widget turns it amber).
STALE_AFTER_DAYS = 5

FILE_VERSION = 1
VALID_STATUSES = ("active", "paused", "done")
MAX_SLUG_LENGTH = 30


# ---------------------------------------------------------------------------
# Small helpers for reading loosely-typed JSON values
# ---------------------------------------------------------------------------

def _as_str(value: Any, default: str = "") -> str:
    """Return value as text, or default when it is missing (None)."""
    return default if value is None else str(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    """Return value as a float, or default if it cannot be converted."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    """Return value as an int, or default if it cannot be converted."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
    """Return only the dict entries of a list (ignores junk from hand edits)."""
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def parse_date(text: Any) -> Optional[datetime.date]:
    """Parse 'YYYY-MM-DD' into a date, or return None if it is not valid."""
    try:
        return datetime.date.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def require_date(text: str, label: str) -> str:
    """Validate a 'YYYY-MM-DD' string and return it, or raise ValueError."""
    if parse_date(text) is None:
        raise ValueError(f"{label} must look like YYYY-MM-DD (got '{text}').")
    return str(text)


def format_number(number: float) -> str:
    """Show 2.0 as '2' and 2.5 as '2.5' so output reads naturally."""
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StepNote:
    """One dated diary line on a step: what the user did towards it that day."""

    date: str
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"date": self.date, "text": self.text}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepNote":
        return cls(date=_as_str(data.get("date")), text=_as_str(data.get("text")))


@dataclass
class Step:
    """One small, concrete action on the way to a goal."""

    text: str
    done: bool = False
    done_date: Optional[str] = None
    stuck: bool = False
    learned: str = ""
    # field(default_factory=list) gives every Step its OWN empty list. Writing
    # `notes = []` would make all steps share one list, a classic Python trap.
    notes: List[StepNote] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "done": self.done,
            "done_date": self.done_date,
            "stuck": self.stuck,
            "learned": self.learned,
            "notes": [note.to_dict() for note in self.notes],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Step":
        return cls(
            text=_as_str(data.get("text")),
            done=bool(data.get("done", False)),
            done_date=data.get("done_date"),
            stuck=bool(data.get("stuck", False)),
            learned=_as_str(data.get("learned")),
            # Older files have no "notes" key; _as_dict_list turns that into [].
            notes=[StepNote.from_dict(item) for item in _as_dict_list(data.get("notes"))],
        )


@dataclass
class Target:
    """A measurable amount to hit each period, e.g. 2 streams per week."""

    name: str
    target: float = 0.0
    unit: str = ""
    period: str = "week"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "target": self.target,
            "unit": self.unit,
            "period": self.period,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Target":
        return cls(
            name=_as_str(data.get("name")),
            target=_as_float(data.get("target")),
            unit=_as_str(data.get("unit")),
            period=_as_str(data.get("period"), "week"),
        )


@dataclass
class LogEntry:
    """One progress record against a target."""

    date: str
    target: str
    value: float = 0.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "target": self.target,
            "value": self.value,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LogEntry":
        return cls(
            date=_as_str(data.get("date")),
            target=_as_str(data.get("target")),
            value=_as_float(data.get("value")),
            note=_as_str(data.get("note")),
        )


@dataclass
class TimeBound:
    """The 'T' in SMART: when the goal starts, ends and is reviewed."""

    start: str = ""
    end: str = ""
    review_dates: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "review_dates": list(self.review_dates),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TimeBound":
        data = data if isinstance(data, dict) else {}
        dates = data.get("review_dates")
        return cls(
            start=_as_str(data.get("start")),
            end=_as_str(data.get("end")),
            review_dates=[str(d) for d in dates] if isinstance(dates, list) else [],
            note=_as_str(data.get("note")),
        )


@dataclass
class Goal:
    """A whole SMART goal with its steps, targets and progress log."""

    id: str
    title: str
    status: str = "active"
    priority: int = 3
    created: str = ""
    last_touched: str = ""
    why: str = ""
    specific: str = ""
    measurable: str = ""
    achievable: str = ""
    relevant: str = ""
    time_bound: TimeBound = field(default_factory=TimeBound)
    steps: List[Step] = field(default_factory=list)
    targets: List[Target] = field(default_factory=list)
    log: List[LogEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "created": self.created,
            "last_touched": self.last_touched,
            "why": self.why,
            "specific": self.specific,
            "measurable": self.measurable,
            "achievable": self.achievable,
            "relevant": self.relevant,
            "time_bound": self.time_bound.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "targets": [target.to_dict() for target in self.targets],
            "log": [entry.to_dict() for entry in self.log],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        title = _as_str(data.get("title"), "Untitled goal")
        created = _as_str(data.get("created"))
        return cls(
            id=_as_str(data.get("id")) or slugify(title),
            title=title,
            status=_as_str(data.get("status"), "active"),
            priority=_as_int(data.get("priority"), 3),
            created=created,
            last_touched=_as_str(data.get("last_touched")) or created,
            why=_as_str(data.get("why")),
            specific=_as_str(data.get("specific")),
            measurable=_as_str(data.get("measurable")),
            achievable=_as_str(data.get("achievable")),
            relevant=_as_str(data.get("relevant")),
            time_bound=TimeBound.from_dict(data.get("time_bound")),
            steps=[Step.from_dict(d) for d in _as_dict_list(data.get("steps"))],
            targets=[Target.from_dict(d) for d in _as_dict_list(data.get("targets"))],
            log=[LogEntry.from_dict(d) for d in _as_dict_list(data.get("log"))],
        )


# ---------------------------------------------------------------------------
# Pure helper functions (no disk access; the widget imports these)
# ---------------------------------------------------------------------------

def slugify(title: str) -> str:
    """Turn a title into a short id: lowercase words joined by '-'."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    slug = "-".join(words)[:MAX_SLUG_LENGTH].strip("-")
    return slug or "goal"


def next_step(goal: Goal) -> Optional[Tuple[int, Step]]:
    """Return (1-based number, step) for the first step not done, or None."""
    for number, step in enumerate(goal.steps, start=1):
        if not step.done:
            return number, step
    return None


def progress(goal: Goal) -> Tuple[int, int]:
    """Return (steps done, total steps)."""
    done_count = sum(1 for step in goal.steps if step.done)
    return done_count, len(goal.steps)


def days_since_touched(goal: Goal, today: Optional[datetime.date] = None) -> Optional[int]:
    """Days since the goal was last touched, or None if the date is unknown."""
    today = today or datetime.date.today()
    touched = parse_date(goal.last_touched)
    return None if touched is None else (today - touched).days


def is_stale(
    goal: Goal,
    today: Optional[datetime.date] = None,
    days: int = STALE_AFTER_DAYS,
) -> bool:
    """True when the goal was last touched at least `days` days ago."""
    age = days_since_touched(goal, today)
    return age is not None and age >= days


def todays_focus(goals: List[Goal]) -> Optional[Tuple[Goal, int, Step]]:
    """
    Pick the single most important thing to do next.

    Only active goals count. They are ordered by priority (1 = most
    important), then by creation date; the first with an unfinished step wins.
    """
    active = [goal for goal in goals if goal.status == "active"]
    active.sort(key=lambda goal: (goal.priority, goal.created))
    for goal in active:
        upcoming = next_step(goal)
        if upcoming is not None:
            number, step = upcoming
            return goal, number, step
    return None


def _week_bounds(today: datetime.date) -> Tuple[datetime.date, datetime.date]:
    """Return the Monday and Sunday of the week containing `today`."""
    monday = today - datetime.timedelta(days=today.weekday())
    return monday, monday + datetime.timedelta(days=6)


def _in_period(entry_date: datetime.date, period: str, today: datetime.date) -> bool:
    """Is this log date inside the target's current period?"""
    if period == "week":
        monday, sunday = _week_bounds(today)
        return monday <= entry_date <= sunday
    if period == "month":
        return (entry_date.year, entry_date.month) == (today.year, today.month)
    return True  # any other period means "all time"


# ---------------------------------------------------------------------------
# The manager (reads and writes Goals.json)
# ---------------------------------------------------------------------------

def _apply_done(step: Step, done: bool, today_text: str) -> None:
    """Set a step's done flag and date, and clear 'stuck' when it is ticked.

    Kept in one place so `done-step` and the widget's log dialog can never
    drift apart in how they treat the date and the stuck flag.
    """
    step.done = done
    step.done_date = today_text if done else None
    if done:
        step.stuck = False


class GoalsManager:
    """Handles loading, changing and safely saving the goals file."""

    def __init__(
        self,
        file_path: str = DEFAULT_GOALS_PATH,
        backup_path: str = BACKUP_GOALS_PATH,
    ) -> None:
        self.file_path = file_path
        self.backup_path = backup_path
        # Replaceable clock so tests can pretend it is another day.
        self.today_func: Callable[[], datetime.date] = datetime.date.today

    # -- file handling ------------------------------------------------------

    def load_goals(self) -> List[Goal]:
        """Read all goals. A missing or empty file gives []; never creates it."""
        if not os.path.exists(self.file_path):
            return []
        with open(self.file_path, "r", encoding="utf-8") as file_handle:
            text = file_handle.read()
        if not text.strip():
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"Goals file is not valid JSON ({error}).") from error
        raw_goals = data.get("goals") if isinstance(data, dict) else data
        return [Goal.from_dict(item) for item in _as_dict_list(raw_goals)]

    def create_backup(self) -> None:
        """Copy the current file aside so 'undo' can bring it back."""
        if os.path.exists(self.file_path):
            os.makedirs(os.path.dirname(os.path.abspath(self.backup_path)), exist_ok=True)
            shutil.copy2(self.file_path, self.backup_path)

    def save_goals(self, goals: List[Goal]) -> None:
        """
        Back up, then write atomically.

        The backup protects against a bad edit. Writing to a temporary file and
        then os.replace()-ing it means the widget, which polls this file, can
        never read a half-written file: it sees the old or the new one.
        """
        self.create_backup()
        os.makedirs(os.path.dirname(os.path.abspath(self.file_path)), exist_ok=True)
        payload = {"version": FILE_VERSION, "goals": [goal.to_dict() for goal in goals]}
        temp_path = self.file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, indent=2, ensure_ascii=False)
        os.replace(temp_path, self.file_path)
        self.write_text_log(goals)

    def undo_last_change(self) -> bool:
        """Restore the file from the backup. False if there is no backup."""
        if not os.path.exists(self.backup_path):
            return False
        shutil.copy2(self.backup_path, self.file_path)
        self.write_text_log(self.load_goals())  # keep the .txt matching the JSON
        return True

    # -- the readable text copy ---------------------------------------------

    @property
    def text_log_path(self) -> str:
        """The .txt copy lives beside the JSON: Goals.json -> 'Goals Log.txt'.

        Working it out from file_path (not a fixed path) means a test that uses
        a temporary Goals.json gets its own temporary log, never the real one.
        """
        folder = os.path.dirname(os.path.abspath(self.file_path))
        stem = os.path.splitext(os.path.basename(self.file_path))[0]
        return os.path.join(folder, f"{stem} Log.txt")

    def write_text_log(self, goals: List[Goal], strict: bool = False) -> None:
        """Rewrite 'Goals Log.txt': every goal, step and note in plain text.

        JSON is for programs; this file is for a person to read, search or print.
        It is rebuilt from scratch after every save, so it can never drift
        out of step with Goals.json. A failure here (say the file is open
        and locked) must never lose a normal save, so errors are swallowed.
        `strict=True` is for the explicit `export` command, where the user asked
        for the file and should hear about it if it could not be written.
        """
        today = self.today_func()
        lines = [
            "GOALS - STEPS AND LOGS",
            f"Written automatically on {today.isoformat()} from {os.path.basename(self.file_path)}.",
            "Do not edit this file: it is rebuilt every time a goal changes.",
            "",
        ]
        ordered = sorted(goals, key=lambda goal: (goal.status != "active", goal.priority, goal.created))
        for goal in ordered:
            lines.append("=" * 72)
            lines.append(_show_goal(goal, today, log_limit=None))
            lines.append("")
        if not goals:
            lines.append("(no goals yet)")
        temp_path = self.text_log_path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file_handle:
                file_handle.write("\n".join(lines) + "\n")
            os.replace(temp_path, self.text_log_path)
        except OSError:
            # Do not leave a stray "Goals Log.txt.tmp" behind after a failed write.
            try:
                os.remove(temp_path)
            except OSError:
                pass
            if strict:
                raise
            # A locked or unwritable readable copy must never fail the save itself.

    # -- lookups ------------------------------------------------------------

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Return the goal with this id, or None."""
        for goal in self.load_goals():
            if goal.id == goal_id:
                return goal
        return None

    def _find(self, goals: List[Goal], goal_id: str) -> Goal:
        """Find a goal in an already-loaded list or raise a friendly KeyError."""
        for goal in goals:
            if goal.id == goal_id:
                return goal
        known = ", ".join(goal.id for goal in goals) or "none yet"
        raise KeyError(f"No goal with id '{goal_id}'. Known goals: {known}.")

    @staticmethod
    def _check_step_number(goal: Goal, step_number: int) -> Step:
        """Return step N (1-based) or raise a friendly IndexError."""
        if not 1 <= step_number <= len(goal.steps):
            raise IndexError(
                f"Step {step_number} does not exist in '{goal.id}' "
                f"(it has {len(goal.steps)} steps)."
            )
        return goal.steps[step_number - 1]

    def _today_text(self) -> str:
        return self.today_func().isoformat()

    def _commit(self, goals: List[Goal], goal: Goal) -> None:
        """Mark the goal as touched today and save once."""
        goal.last_touched = self._today_text()
        self.save_goals(goals)

    # -- changing goals -----------------------------------------------------

    def _unique_id(self, base_id: str, goals: List[Goal]) -> str:
        """Append -2, -3 ... until the id is not already used."""
        taken = {goal.id for goal in goals}
        candidate, counter = base_id, 2
        while candidate in taken:
            candidate = f"{base_id}-{counter}"
            counter += 1
        return candidate

    def add_goal(
        self,
        title: str,
        goal_id: Optional[str] = None,
        why: str = "",
        specific: str = "",
        measurable: str = "",
        achievable: str = "",
        relevant: str = "",
        start: Optional[str] = None,
        end: Optional[str] = None,
        review_dates: Optional[List[str]] = None,
        time_note: str = "",
        priority: int = 3,
    ) -> Goal:
        """Create a new goal (id defaults to a slug of the title)."""
        if not title.strip():
            raise ValueError("A goal needs a title.")
        today = self._today_text()
        for label, value in (("start", start), ("end", end)):
            if value:
                require_date(value, label)
        dates = [require_date(d, "review date") for d in (review_dates or [])]
        goals = self.load_goals()
        goal = Goal(
            id=self._unique_id(goal_id or slugify(title), goals),
            title=title.strip(),
            priority=priority,
            created=today,
            last_touched=today,
            why=why,
            specific=specific,
            measurable=measurable,
            achievable=achievable,
            relevant=relevant,
            time_bound=TimeBound(start or today, end or "", dates, time_note),
        )
        goals.append(goal)
        self.save_goals(goals)
        return goal

    def add_step(self, goal_id: str, text: str, position: Optional[int] = None) -> Step:
        """Add a step at the end, or at 1-based `position`."""
        if not text.strip():
            raise ValueError("A step needs some text.")
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        step = Step(text=text.strip())
        if position is None:
            goal.steps.append(step)
        else:
            if not 1 <= position <= len(goal.steps) + 1:
                raise IndexError(
                    f"Position {position} is out of range (1 to {len(goal.steps) + 1})."
                )
            goal.steps.insert(position - 1, step)
        self._commit(goals, goal)
        return step

    def set_step_done(
        self,
        goal_id: str,
        step_number: int,
        done: bool = True,
        learned: Optional[str] = None,
    ) -> Step:
        """Tick (or untick) a step. Ticking clears 'stuck' and stamps the date."""
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        step = self._check_step_number(goal, step_number)
        _apply_done(step, done, self._today_text())
        if learned is not None:
            step.learned = learned
        self._commit(goals, goal)
        return step

    def update_step_log(
        self,
        goal_id: str,
        step_number: int,
        note_text: str = "",
        done: Optional[bool] = None,
    ) -> Step:
        """Add a note and/or change the done tick in ONE save.

        Two separate saves would leave two undo backups, so `undo` could take
        back the tick but never the note. One save means one backup and one
        clean undo. `done=None` means "leave the tick as it is".
        """
        note_text = note_text.strip()
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        step = self._check_step_number(goal, step_number)
        if note_text:
            step.notes.append(StepNote(date=self._today_text(), text=note_text))
        if done is not None and done != step.done:
            _apply_done(step, done, self._today_text())
        self._commit(goals, goal)
        return step

    def add_step_note(self, goal_id: str, step_number: int, text: str) -> StepNote:
        """Add a dated 'what I did' note to a step (the step stays as it is)."""
        if not text.strip():
            raise ValueError("A note needs some text.")
        step = self.update_step_log(goal_id, step_number, note_text=text)
        return step.notes[-1]

    def set_step_stuck(self, goal_id: str, step_number: int, stuck: bool = True) -> Step:
        """Flag a step as stuck (or clear the flag)."""
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        step = self._check_step_number(goal, step_number)
        step.stuck = stuck
        self._commit(goals, goal)
        return step

    def split_step(self, goal_id: str, step_number: int, new_texts: List[str]) -> List[Step]:
        """Replace one step, in place, with several smaller steps."""
        texts = [text.strip() for text in new_texts if text.strip()]
        if not texts:
            raise ValueError("Give at least one smaller step to split into.")
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        old_step = self._check_step_number(goal, step_number)
        new_steps = [Step(text=text) for text in texts]
        # Notes already written on the big step move onto the first small one,
        # so splitting a step never throws away the user's log.
        new_steps[0].notes = old_step.notes
        goal.steps[step_number - 1:step_number] = new_steps
        self._commit(goals, goal)
        return new_steps

    def add_target(
        self, goal_id: str, name: str, target: float, unit: str, period: str
    ) -> Target:
        """Add a measurable target (period: week, month or anything else = all time)."""
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        if any(existing.name == name for existing in goal.targets):
            raise ValueError(f"Goal '{goal_id}' already has a target named '{name}'.")
        new_target = Target(name=name, target=float(target), unit=unit, period=period)
        goal.targets.append(new_target)
        self._commit(goals, goal)
        return new_target

    def set_field(self, goal_id: str, field_name: str, value: str) -> Goal:
        """Change one field by name; raises ValueError for a bad field or value."""
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        key = field_name.strip().lower().replace("_", "-")
        text_fields = ("title", "why", "specific", "measurable", "achievable", "relevant")
        if key in text_fields:
            if key == "title" and not str(value).strip():
                raise ValueError("The title cannot be empty.")
            setattr(goal, key, str(value))
        elif key == "status":
            if value not in VALID_STATUSES:
                raise ValueError(f"status must be one of: {', '.join(VALID_STATUSES)}.")
            goal.status = value
        elif key == "priority":
            try:
                goal.priority = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"priority must be a whole number (got '{value}').") from None
        elif key in ("start", "end"):
            setattr(goal.time_bound, key, require_date(value, key))
        elif key == "review-dates":
            parts = [part.strip() for part in str(value).split(",") if part.strip()]
            goal.time_bound.review_dates = [require_date(p, "review date") for p in parts]
        elif key == "time-note":
            goal.time_bound.note = str(value)
        else:
            raise ValueError(
                f"Unknown field '{field_name}'. Use: title, why, status, priority, "
                "specific, measurable, achievable, relevant, start, end, "
                "review-dates, time-note."
            )
        self._commit(goals, goal)
        return goal

    def log_entry(
        self,
        goal_id: str,
        target_name: str,
        value: float,
        date: Optional[str] = None,
        note: str = "",
    ) -> LogEntry:
        """Record progress against an existing target."""
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        if not any(target.name == target_name for target in goal.targets):
            names = ", ".join(target.name for target in goal.targets) or "none yet"
            raise ValueError(
                f"Goal '{goal_id}' has no target named '{target_name}'. Targets: {names}."
            )
        entry_date = require_date(date, "date") if date else self._today_text()
        entry = LogEntry(date=entry_date, target=target_name, value=float(value), note=note)
        goal.log.append(entry)
        self._commit(goals, goal)
        return entry

    def send_step_to_tasks(
        self,
        goal_id: str,
        step_number: Optional[int] = None,
        todo_manager: Optional[Any] = None,
    ) -> Any:
        """
        Copy a step onto the daily to-do list as "<step> [goal: <title>]".

        step_number None means "the next step". The step is NOT ticked: it is
        only copied, so the goal stays honest until the work is really done.
        """
        goals = self.load_goals()
        goal = self._find(goals, goal_id)
        if step_number is None:
            upcoming = next_step(goal)
            if upcoming is None:
                raise ValueError(f"All steps of '{goal_id}' are already done.")
            step_number = upcoming[0]
        step = self._check_step_number(goal, step_number)
        if todo_manager is None:
            from todo_manager import TodoListManager  # imported late: only needed here
            todo_manager = TodoListManager()
        item = todo_manager.add_item(f"{step.text} [goal: {goal.title}]")
        self._commit(goals, goal)
        return item

    # -- reporting ----------------------------------------------------------

    def review_text(
        self, goal_id: Optional[str] = None, today: Optional[datetime.date] = None
    ) -> str:
        """Build a plain-text progress review for one goal, or all active goals."""
        today = today or self.today_func()
        goals = self.load_goals()
        if goal_id is not None:
            chosen = [self._find(goals, goal_id)]
        else:
            chosen = sorted(
                (g for g in goals if g.status == "active"),
                key=lambda g: (g.priority, g.created),
            )
        if not chosen:
            return "No active goals to review."
        return "\n\n".join(_review_goal(goal, today) for goal in chosen)


# ---------------------------------------------------------------------------
# Text builders (kept outside the class: they only format data)
# ---------------------------------------------------------------------------

def _plural_days(count: int) -> str:
    return f"{count} day" + ("" if abs(count) == 1 else "s")


def _target_period_total(goal: Goal, target: Target, today: datetime.date) -> float:
    """Sum the log values for one target inside its current period."""
    total = 0.0
    for entry in goal.log:
        entry_date = parse_date(entry.date)
        if entry.target == target.name and entry_date and _in_period(entry_date, target.period, today):
            total += entry.value
    return total


def _target_average_per_week(goal: Goal, target: Target, today: datetime.date) -> float:
    """All-time logged total divided by weeks since start (minimum one week)."""
    start = parse_date(goal.time_bound.start) or parse_date(goal.created) or today
    weeks = max((today - start).days, 7) / 7
    total = sum(e.value for e in goal.log if e.target == target.name)
    return total / weeks


def _review_target_line(goal: Goal, target: Target, today: datetime.date) -> str:
    period_total = _target_period_total(goal, target, today)
    period_label = {"week": "this week", "month": "this month"}.get(target.period, "in total")
    average = _target_average_per_week(goal, target, today)
    return (
        f"    {target.name}: {format_number(period_total)} of "
        f"{format_number(target.target)} {target.unit} {period_label}; "
        f"average {average:.1f} per week since start"
    )


def _review_goal(goal: Goal, today: datetime.date) -> str:
    done_count, total = progress(goal)
    lines = [f"{goal.title} ({goal.id}) [{goal.status}, priority {goal.priority}]"]
    lines.append(f"  Steps done: {done_count}/{total}")
    upcoming = next_step(goal)
    lines.append(
        f"  Next step: {upcoming[0]}. {upcoming[1].text}" if upcoming else "  Next step: all steps done"
    )
    for number, step in enumerate(goal.steps, start=1):
        if step.stuck and not step.done:
            lines.append(f"  Stuck: {number}. {step.text}")
    age = days_since_touched(goal, today)
    if age is not None:
        lines.append(f"  Last touched: {_plural_days(age)} ago")
    end = parse_date(goal.time_bound.end)
    if end is not None:
        left = (end - today).days
        lines.append(f"  Days left: {left}" if left >= 0 else f"  Days left: overdue by {_plural_days(-left)}")
    upcoming_reviews = sorted(d for d in goal.time_bound.review_dates if (parse_date(d) or datetime.date.min) >= today)
    if upcoming_reviews:
        lines.append(f"  Next review: {upcoming_reviews[0]}")
    if goal.targets:
        lines.append("  Targets:")
        lines.extend(_review_target_line(goal, target, today) for target in goal.targets)
    return "\n".join(lines)


def _show_goal(goal: Goal, today: datetime.date, log_limit: Optional[int] = 10) -> str:
    """Everything about one goal, for `show` and for 'Goals Log.txt'.

    log_limit=None prints the whole target log instead of the last 10 entries.
    """
    done_count, total = progress(goal)
    time_bound = goal.time_bound
    lines = [
        f"{goal.title} ({goal.id})",
        f"Status: {goal.status}   Priority: {goal.priority}   Steps: {done_count}/{total}",
        f"Created: {goal.created}   Last touched: {goal.last_touched}",
        f"Why: {goal.why or '-'}",
        "",
        "SMART:",
        f"  Specific (what exactly):      {goal.specific or '-'}",
        f"  Measurable (how I know):      {goal.measurable or '-'}",
        f"  Achievable (why I can):       {goal.achievable or '-'}",
        f"  Relevant (why it matters):    {goal.relevant or '-'}",
        f"  Time-bound: {time_bound.start or '?'} to {time_bound.end or '?'}"
        + (f"; reviews {', '.join(time_bound.review_dates)}" if time_bound.review_dates else "")
        + (f"; {time_bound.note}" if time_bound.note else ""),
        "",
        "Steps:",
    ]
    for number, step in enumerate(goal.steps, start=1):
        marker = "[X]" if step.done else ("[stuck]" if step.stuck else "[ ]")
        done_note = f"  (done {step.done_date})" if step.done and step.done_date else ""
        lines.append(f"  {number}. {marker} {step.text}{done_note}")
        if step.learned:
            lines.append(f"       learned: {step.learned}")
        for note in step.notes:
            lines.append(f"       {note.date}: {note.text}")
    if not goal.steps:
        lines.append("  (no steps yet)")
    lines.append("")
    lines.append("Targets:")
    for target in goal.targets:
        lines.append(f"  {target.name}: {format_number(target.target)} {target.unit} per {target.period}")
    if not goal.targets:
        lines.append("  (none yet)")
    lines.append("")
    entries = goal.log if log_limit is None else goal.log[-log_limit:]
    lines.append("Target log:" if log_limit is None else f"Recent log (last {log_limit}):")
    for entry in entries:
        note = f" - {entry.note}" if entry.note else ""
        lines.append(f"  {entry.date}  {entry.target}: {format_number(entry.value)}{note}")
    if not goal.log:
        lines.append("  (nothing logged yet)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Define every command. Global options go BEFORE the command name."""
    parser = argparse.ArgumentParser(description="Manage SMART goals.")
    parser.add_argument("--file", default=DEFAULT_GOALS_PATH, help="Goals.json path")
    parser.add_argument("--todo-file", default=None, help="To Do List.txt path")
    sub = parser.add_subparsers(dest="command", required=True)

    add_goal = sub.add_parser("add-goal", help="Create a new goal")
    add_goal.add_argument("title")
    add_goal.add_argument("--id")
    for name in ("why", "specific", "measurable", "achievable", "relevant"):
        add_goal.add_argument(f"--{name}", default="")
    add_goal.add_argument("--start")
    add_goal.add_argument("--end")
    add_goal.add_argument("--review-dates", default="", help="comma list: d1,d2")
    add_goal.add_argument("--time-note", default="")
    add_goal.add_argument("--priority", type=int, default=3)

    add_step = sub.add_parser("add-step", help="Add a step to a goal")
    add_step.add_argument("goal_id")
    add_step.add_argument("text")
    add_step.add_argument("--at", type=int, help="insert at this step number")

    done = sub.add_parser("done-step", help="Tick a step")
    done.add_argument("goal_id")
    done.add_argument("number", type=int)
    done.add_argument("--learned", default=None)
    done.add_argument("--undone", action="store_true", help="untick instead")

    stuck = sub.add_parser("stuck-step", help="Flag a step as stuck")
    stuck.add_argument("goal_id")
    stuck.add_argument("number", type=int)
    stuck.add_argument("--clear", action="store_true")

    note = sub.add_parser("note-step", help="Add a dated 'what I did' note to a step")
    note.add_argument("goal_id")
    note.add_argument("number", type=int)
    note.add_argument("text")

    sub.add_parser("export", help="Rewrite 'Goals Log.txt' now")

    split = sub.add_parser("split-step", help="Replace a step with smaller ones")
    split.add_argument("goal_id")
    split.add_argument("number", type=int)
    split.add_argument("texts", nargs="+")

    target = sub.add_parser("add-target", help="Add a measurable target")
    target.add_argument("goal_id")
    target.add_argument("name")
    target.add_argument("target", type=float)
    target.add_argument("unit")
    target.add_argument("period")

    set_cmd = sub.add_parser("set", help="Change a goal field")
    set_cmd.add_argument("goal_id")
    set_cmd.add_argument("field")
    set_cmd.add_argument("value")

    sub.add_parser("next", help="Next step of every active goal")
    list_cmd = sub.add_parser("list", help="List goals")
    list_cmd.add_argument("--all", "-a", action="store_true")
    show = sub.add_parser("show", help="Show one goal in full")
    show.add_argument("goal_id")

    log = sub.add_parser("log", help="Log progress against a target")
    log.add_argument("goal_id")
    log.add_argument("target")
    log.add_argument("value", type=float)
    log.add_argument("--date")
    log.add_argument("--note", default="")

    review = sub.add_parser("review", help="Progress review")
    review.add_argument("goal_id", nargs="?")

    send = sub.add_parser("send-to-tasks", help="Copy a step to the to-do list")
    send.add_argument("goal_id")
    send.add_argument("number", type=int, nargs="?")

    sub.add_parser("undo", help="Undo the last change")
    return parser


def _cmd_list(manager: GoalsManager, show_all: bool) -> None:
    goals = sorted(manager.load_goals(), key=lambda g: (g.priority, g.created))
    shown = [g for g in goals if show_all or g.status == "active"]
    if not shown:
        print("No goals to show yet.")
        return
    today = manager.today_func()
    for goal in shown:
        done_count, total = progress(goal)
        line = f"{goal.id}  P{goal.priority}  {goal.title}  {done_count}/{total} steps"
        if show_all and goal.status != "active":
            line += f"  [{goal.status}]"
        if is_stale(goal, today):
            line += f"  (not touched for {days_since_touched(goal, today)} days)"
        print(line)


def _cmd_next(manager: GoalsManager) -> None:
    goals = sorted(manager.load_goals(), key=lambda g: (g.priority, g.created))
    for goal in (g for g in goals if g.status == "active"):
        upcoming = next_step(goal)
        print(f"{goal.id}: {upcoming[0]}. {upcoming[1].text}" if upcoming else f"{goal.id}: all steps done")


def _run_command(args: argparse.Namespace, manager: GoalsManager) -> None:
    """Carry out one parsed command and print a confirmation."""
    command = args.command
    if command == "add-goal":
        dates = [d.strip() for d in args.review_dates.split(",") if d.strip()]
        goal = manager.add_goal(
            args.title, args.id, args.why, args.specific, args.measurable,
            args.achievable, args.relevant, args.start, args.end, dates,
            args.time_note, args.priority,
        )
        print(f'Added goal "{goal.title}" with id {goal.id}.')
    elif command == "add-step":
        step = manager.add_step(args.goal_id, args.text, args.at)
        print(f'Added step to {args.goal_id}: "{step.text}"')
    elif command == "done-step":
        step = manager.set_step_done(args.goal_id, args.number, not args.undone, args.learned)
        verb = "Unticked" if args.undone else "Ticked off"
        print(f'{verb} step {args.number} of {args.goal_id}: "{step.text}"')
    elif command == "stuck-step":
        step = manager.set_step_stuck(args.goal_id, args.number, not args.clear)
        verb = "Cleared stuck flag on" if args.clear else "Marked as stuck:"
        print(f'{verb} step {args.number} of {args.goal_id}: "{step.text}"')
    elif command == "note-step":
        note = manager.add_step_note(args.goal_id, args.number, args.text)
        print(f"Noted on step {args.number} of {args.goal_id} ({note.date}): {note.text}")
    elif command == "export":
        # strict=True: the user asked for this file, so a locked log is an error here.
        manager.write_text_log(manager.load_goals(), strict=True)
        print(f"Wrote {manager.text_log_path}")
    elif command == "split-step":
        steps = manager.split_step(args.goal_id, args.number, args.texts)
        print(f"Split step {args.number} of {args.goal_id} into {len(steps)} smaller steps.")
    elif command == "add-target":
        new_target = manager.add_target(args.goal_id, args.name, args.target, args.unit, args.period)
        print(f"Added target to {args.goal_id}: {new_target.name} = "
              f"{format_number(new_target.target)} {new_target.unit} per {new_target.period}")
    elif command == "set":
        manager.set_field(args.goal_id, args.field, args.value)
        print(f"Set {args.field} of {args.goal_id}.")
    elif command == "next":
        _cmd_next(manager)
    elif command == "list":
        _cmd_list(manager, args.all)
    elif command == "show":
        goal = manager.get_goal(args.goal_id)
        if goal is None:
            raise KeyError(f"No goal with id '{args.goal_id}'.")
        print(_show_goal(goal, manager.today_func()))
    elif command == "log":
        entry = manager.log_entry(args.goal_id, args.target, args.value, args.date, args.note)
        print(f"Logged {format_number(entry.value)} for {entry.target} on {entry.date} ({args.goal_id}).")
    elif command == "review":
        print(manager.review_text(args.goal_id))
    elif command == "send-to-tasks":
        _send_to_tasks(args, manager)
    elif command == "undo":
        if manager.undo_last_change():
            print("Successfully undid the last change to the goals file.")
        else:
            print("No backup file found to undo from.")


def _send_to_tasks(args: argparse.Namespace, manager: GoalsManager) -> None:
    """Handle `send-to-tasks`, building a to-do manager for --todo-file."""
    from todo_manager import DEFAULT_TODO_PATH, TodoListManager
    todo_path = args.todo_file or DEFAULT_TODO_PATH
    todo_backup = backup_path_for(todo_path, DEFAULT_TODO_PATH, "To Do List.txt.bak")
    todo_manager = TodoListManager(file_path=todo_path, backup_path=todo_backup)
    item = manager.send_step_to_tasks(args.goal_id, args.number, todo_manager)
    print(f'Added to your to-do list: "{item.text}"')


def backup_path_for(file_path: str, default_path: str, backup_name: str) -> str:
    """Where to keep the undo backup for a data file.

    The real files back up into the app's data folder (worked out now, at call
    time, so a test can point APPDATA somewhere else). Any other file, such as a
    temporary test copy, backs up right beside itself as "<file>.bak". That way a
    test can never overwrite the backup that the user's own `undo` relies on.
    """
    if os.path.normcase(os.path.abspath(file_path)) != os.path.normcase(os.path.abspath(default_path)):
        return file_path + ".bak"
    return os.path.join(paths.data_dir(), backup_name)


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point. Returns 0 on success, 1 on a friendly error."""
    # The Windows console may be cp1252; utf-8 avoids crashes on odd characters.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    manager = GoalsManager(args.file, backup_path_for(args.file, DEFAULT_GOALS_PATH, "Goals.json.bak"))
    try:
        _run_command(args, manager)
    except KeyError as error:
        print(f"Error: {error.args[0]}")
        return 1
    except (ValueError, IndexError, OSError) as error:
        print(f"Error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
