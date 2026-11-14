"""
Silent Launcher for the Desktop To-Do Widget
============================================

Runs todo_widget.py using pythonw.exe without opening a command prompt window.
Double-click this file, or put a shortcut to it in the Startup folder.
"""

from __future__ import annotations

import os
import subprocess
import sys

widget_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "todo_widget.py")

pythonw_candidates = [
    os.path.join(os.path.dirname(sys.executable), "pythonw.exe"),
    "pythonw.exe",
    "pythonw",
]

pythonw_exe = "pythonw"
for cand in pythonw_candidates:
    if os.path.exists(cand):
        pythonw_exe = cand
        break

creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
subprocess.Popen([pythonw_exe, widget_script] + sys.argv[1:], creationflags=creation_flags)
