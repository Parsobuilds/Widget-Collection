"""
To-Do List Manager
==================

A Python-first utility to manage the to-do list ('%APPDATA%\\DesktopWidgets\\TodoWidget\\todo\\To Do List.txt').
Designed to be clean, educational, PEP 8 compliant, and safe.

Features:
- Reads and updates the To Do List file with strict Windows CRLF endings.
- Maintains the widget's exact format: 2 blank lines between items, [ ] for open, [X] for done.
- Automatic timestamping (YYYY-MM-DD) on newly added items.
- Renumbers items automatically when items are removed or cleared.
- Automatic backup creation before modifying the list (for seamless undo).
- Launches or focuses Notepad to display updates.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
from typing import List, Tuple, Optional

import paths

# Default file paths live in the per-user data folder (see paths.py), so the app works
# from any install folder.
DEFAULT_TODO_PATH = paths.todo_file()
BACKUP_TODO_PATH = paths.backup_file()

HEADER_TEXT = "TO DO LIST\r\n==========\r\n"
ITEM_PATTERN = re.compile(r"^\[([ xX])\]\s*(\d+)\.\s*(.*?)(?:\s+\(added\s+(\d{4}-\d{2}-\d{2})\))?\s*$")


class TodoItem:
    """Represents a single task item in the to-do list."""

    def __init__(self, index: int, text: str, is_done: bool = False, added_date: Optional[str] = None) -> None:
        self.index = index
        self.text = text.strip()
        self.is_done = is_done
        self.added_date = added_date or datetime.date.today().isoformat()

    @property
    def status_marker(self) -> str:
        """Returns '[X]' if completed, otherwise '[ ]'."""
        return "[X]" if self.is_done else "[ ]"

    def format_line(self) -> str:
        """Returns the formatted line matching the standard layout."""
        return f"{self.status_marker} {self.index}. {self.text}   (added {self.added_date})"


class TodoListManager:
    """Handles reading, modifying, and writing the desktop to-do list."""

    def __init__(self, file_path: str = DEFAULT_TODO_PATH, backup_path: str = BACKUP_TODO_PATH) -> None:
        self.file_path = file_path
        self.backup_path = backup_path

    def ensure_file_exists(self) -> None:
        """Creates the to-do file (and its folder) with standard headers if it does not exist."""
        if not os.path.exists(self.file_path):
            os.makedirs(os.path.dirname(os.path.abspath(self.file_path)), exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8", newline="") as file_handle:
                file_handle.write(HEADER_TEXT + "\r\n")

    def create_backup(self) -> None:
        """Backs up the current to-do list before any modification."""
        if os.path.exists(self.file_path):
            os.makedirs(os.path.dirname(self.backup_path), exist_ok=True)
            shutil.copy2(self.file_path, self.backup_path)

    def undo_last_change(self) -> bool:
        """Restores the to-do list from the backup file."""
        if not os.path.exists(self.backup_path):
            return False
        shutil.copy2(self.backup_path, self.file_path)
        return True

    def read_items(self) -> Tuple[List[TodoItem], List[str]]:
        """
        Parses the to-do file into structured items and raw trailing notes.
        Returns a tuple of (items_list, trailing_unformatted_lines).
        """
        self.ensure_file_exists()

        items: List[TodoItem] = []
        trailing_lines: List[str] = []

        with open(self.file_path, "r", encoding="utf-8", errors="replace") as file_handle:
            lines = file_handle.read().splitlines()

        # Skip header lines ("TO DO LIST" and "==========")
        content_lines = []
        in_header = True
        for line in lines:
            stripped = line.strip()
            if in_header:
                if stripped in ("TO DO LIST", "==========") or stripped == "":
                    continue
                else:
                    in_header = False
            content_lines.append(line)

        # Parse items and keep track of unformatted text
        for line in content_lines:
            stripped = line.strip()
            if not stripped:
                continue

            match = ITEM_PATTERN.match(stripped)
            if match:
                marker, num_str, task_text, date_str = match.groups()
                is_done = (marker.upper() == "X")
                item_num = int(num_str)
                items.append(TodoItem(index=item_num, text=task_text, is_done=is_done, added_date=date_str))
            else:
                trailing_lines.append(stripped)

        return items, trailing_lines

    def save_items(self, items: List[TodoItem], trailing_lines: Optional[List[str]] = None) -> None:
        """
        Writes the list of items to the file using CRLF and the exact two-blank-lines spacing.
        """
        self.create_backup()

        output_parts: List[str] = ["TO DO LIST", "==========", ""]

        for idx, item in enumerate(items):
            output_parts.append(item.format_line())
            # Add two blank lines between items (except after the very last item if no trailing notes)
            if idx < len(items) - 1:
                output_parts.append("")
                output_parts.append("")

        if trailing_lines:
            if items:
                output_parts.append("")
                output_parts.append("")
            for note in trailing_lines:
                output_parts.append(note)

        # Build full content with Windows CRLF line endings
        content = "\r\n".join(output_parts) + "\r\n"

        with open(self.file_path, "w", encoding="utf-8", newline="") as file_handle:
            file_handle.write(content)

    def add_item(self, text: str) -> TodoItem:
        """Adds a new item to the end of the to-do list."""
        items, trailing = self.read_items()
        next_index = len(items) + 1
        new_item = TodoItem(index=next_index, text=text)
        items.append(new_item)
        self.save_items(items, trailing_lines=trailing)
        return new_item

    def mark_done(self, item_index: int) -> Optional[TodoItem]:
        """Marks an item as completed [X] by its displayed number."""
        items, trailing = self.read_items()
        target: Optional[TodoItem] = None
        for item in items:
            if item.index == item_index:
                item.is_done = True
                target = item
                break

        if target:
            self.save_items(items, trailing_lines=trailing)
        return target

    def remove_item(self, item_index: int) -> Optional[TodoItem]:
        """Removes an item and renumbers all subsequent items sequentially."""
        items, trailing = self.read_items()
        removed: Optional[TodoItem] = None
        remaining: List[TodoItem] = []

        for item in items:
            if item.index == item_index:
                removed = item
            else:
                remaining.append(item)

        if removed:
            # Renumber sequentially starting from 1
            for new_idx, item in enumerate(remaining, start=1):
                item.index = new_idx
            self.save_items(remaining, trailing_lines=trailing)

        return removed

    def move_item(self, item_index: int, to_position: int) -> bool:
        """
        Moves an item to a new position and renumbers the list.

        ``item_index`` is the displayed (1-based) item number being dragged.
        ``to_position`` is a 0-based *insertion slot* in the current list order:
        slot 0 means "before the first item", slot ``len(items)`` means "after
        the last item". Dropping an item back onto its own slot is a no-op and
        returns False without touching the file.
        """
        items, trailing = self.read_items()
        source_position = next(
            (pos for pos, item in enumerate(items) if item.index == item_index),
            None,
        )
        if source_position is None:
            return False

        # Slots source_position and source_position + 1 both mean "stays in place"
        if to_position in (source_position, source_position + 1):
            return False

        moved_item = items.pop(source_position)

        # The slot index shifts down by one once the dragged item is removed
        insert_at = to_position if to_position <= source_position else to_position - 1
        insert_at = max(0, min(insert_at, len(items)))
        items.insert(insert_at, moved_item)

        # Renumber sequentially so the displayed numbers stay 1..n
        for new_index, item in enumerate(items, start=1):
            item.index = new_index

        self.save_items(items, trailing_lines=trailing)
        return True

    def clear_done(self) -> int:
        """Removes all completed [X] items and renumbers the rest."""
        items, trailing = self.read_items()
        remaining: List[TodoItem] = [item for item in items if not item.is_done]
        cleared_count = len(items) - len(remaining)

        if cleared_count > 0:
            for new_idx, item in enumerate(remaining, start=1):
                item.index = new_idx
            self.save_items(remaining, trailing_lines=trailing)

        return cleared_count

    def convert_trailing_notes(self) -> int:
        """Converts any unformatted raw notes at the end of the file into numbered items."""
        items, trailing = self.read_items()
        if not trailing:
            return 0

        added_count = 0
        for note in trailing:
            items.append(TodoItem(index=len(items) + 1, text=note))
            added_count += 1

        self.save_items(items, trailing_lines=[])
        return added_count

    def open_in_notepad(self) -> None:
        """Brings Notepad to the front or opens the file if not already running."""
        try:
            os.startfile(self.file_path)
        except Exception:
            subprocess.Popen(["notepad.exe", self.file_path])


def main() -> int:
    """Command-line interface entry point."""
    parser = argparse.ArgumentParser(description="Manage the desktop to-do list.")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: add
    add_parser = subparsers.add_parser("add", help="Add a new item to the to-do list")
    add_parser.add_argument("item", nargs="+", help="The text description of the to-do item")
    add_parser.add_argument("--open", "-o", action="store_true", help="Also open/focus Notepad after adding")

    # Command: list / show
    list_parser = subparsers.add_parser("list", help="List items in the to-do list")
    list_parser.add_argument("--all", "-a", action="store_true", help="Show all items including completed ones")

    # Command: done
    done_parser = subparsers.add_parser("done", help="Mark an item as completed [X]")
    done_parser.add_argument("number", type=int, help="Item number to mark as done")
    done_parser.add_argument("--open", "-o", action="store_true", help="Also open/focus Notepad after completing")

    # Command: remove
    remove_parser = subparsers.add_parser("remove", help="Remove an item and renumber the list")
    remove_parser.add_argument("number", type=int, help="Item number to remove")
    remove_parser.add_argument("--open", "-o", action="store_true", help="Also open/focus Notepad after removing")

    # Command: clear-done
    clear_parser = subparsers.add_parser("clear-done", help="Remove all completed items and renumber")
    clear_parser.add_argument("--open", "-o", action="store_true", help="Also open/focus Notepad after clearing")

    # Command: undo
    undo_parser = subparsers.add_parser("undo", help="Undo the last change made by the manager")
    undo_parser.add_argument("--open", "-o", action="store_true", help="Also open/focus Notepad after undoing")

    # Command: format-notes
    format_parser = subparsers.add_parser("format-notes", help="Convert trailing unformatted draft notes to numbered tasks")
    format_parser.add_argument("--open", "-o", action="store_true", help="Also open/focus Notepad after formatting")

    # Command: open
    subparsers.add_parser("open", help="Open or focus the To Do List in Notepad")

    # Command: widget
    widget_parser = subparsers.add_parser("widget", help="Launch, restart, or snap the Desktop To-Do Widget")
    widget_parser.add_argument("--snap", "-s", action="store_true", help="Snap widget into position")

    args = parser.parse_args()
    manager = TodoListManager()

    if args.command == "add":
        item_text = " ".join(args.item)
        new_item = manager.add_item(item_text)
        items, _ = manager.read_items()
        open_count = sum(1 for item in items if not item.is_done)
        print(f'Added to your to-do list: "{new_item.text}"')
        print(f"Items currently open: {open_count}")
        if getattr(args, "open", False):
            manager.open_in_notepad()

    elif args.command == "list" or args.command is None:
        items, trailing = manager.read_items()
        if not items and not trailing:
            print("To-do list is currently empty.")
            return 0

        show_all = getattr(args, "all", False)
        open_items = [i for i in items if not i.is_done]
        displayed = items if show_all else open_items

        print(f"=== TO DO LIST ({len(open_items)} open, {len(items)} total) ===")
        for item in displayed:
            print(item.format_line())

        if trailing:
            print("\nDraft / unformatted notes at bottom:")
            for note in trailing:
                print(f"  * {note}")

    elif args.command == "done":
        target = manager.mark_done(args.number)
        if target:
            print(f"Ticked off item {args.number}: \"{target.text}\"")
            if getattr(args, "open", False):
                manager.open_in_notepad()
        else:
            print(f"Item number {args.number} not found.")

    elif args.command == "remove":
        removed = manager.remove_item(args.number)
        if removed:
            print(f"Removed item {args.number}: \"{removed.text}\" (list renumbered)")
            if getattr(args, "open", False):
                manager.open_in_notepad()
        else:
            print(f"Item number {args.number} not found.")

    elif args.command == "clear-done":
        cleared = manager.clear_done()
        print(f"Cleared {cleared} completed item(s).")
        if getattr(args, "open", False):
            manager.open_in_notepad()

    elif args.command == "undo":
        if manager.undo_last_change():
            print("Successfully undid the last change to the to-do list.")
            if getattr(args, "open", False):
                manager.open_in_notepad()
        else:
            print("No backup file found to undo from.")

    elif args.command == "format-notes":
        converted = manager.convert_trailing_notes()
        if converted:
            print(f"Converted {converted} draft note(s) into numbered to-do item(s).")
            if getattr(args, "open", False):
                manager.open_in_notepad()
        else:
            print("No draft notes found to convert.")

    elif args.command == "open":
        manager.open_in_notepad()
        print("Opened To Do List in Notepad.")

    elif args.command == "widget":
        if paths.is_frozen():
            # The CLI is compiled into the same .exe, so start a second copy of it.
            subprocess.Popen([sys.executable], cwd=paths.data_dir())
        else:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            widget_script = os.path.join(app_dir, "todo_widget.pyw")
            if not os.path.exists(widget_script):
                widget_script = os.path.join(app_dir, "todo_widget.py")
            pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
            subprocess.Popen([pythonw_exe, widget_script], cwd=app_dir)
        print("Desktop To-Do Widget launched.")

        if getattr(args, "snap", False):
            try:
                from snap_widgets import snap_widget_to_edge
                snap_widget_to_edge()
                print("Snapped widget to the screen edge.")
            except Exception as exc:
                print(f"Snap notice: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
