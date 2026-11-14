"""
Test sandbox: points the user-profile folders at a temporary directory
=======================================================================

Import this module FIRST in every test file (before ``paths``, ``todo_widget`` and so on).
It sets ``APPDATA``, ``LOCALAPPDATA``, ``USERPROFILE``, ``HOME``, ``XDG_CONFIG_HOME`` and ``XDG_RUNTIME_DIR`` to a throw-away folder and tells Qt
to run offscreen, so no test can ever read or change the real user's files. The folder is
deleted when the test run ends.

It is only used by the tests and is not part of the shipped program.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

# "offscreen" lets Qt build widgets without drawing them on a real screen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Several test files import this module; the first one creates the sandbox, the rest reuse it.
if "TODOWIDGET_TEST_SANDBOX" not in os.environ:
    SANDBOX = tempfile.mkdtemp(prefix="todowidget_sandbox_")
    os.environ["TODOWIDGET_TEST_SANDBOX"] = SANDBOX
    os.environ["APPDATA"] = os.path.join(SANDBOX, "Roaming")
    os.environ["LOCALAPPDATA"] = os.path.join(SANDBOX, "Local")
    os.environ["USERPROFILE"] = os.path.join(SANDBOX, "Profile")
    # The same idea for Linux: home, config and runtime folders all live inside the sandbox.
    os.environ["HOME"] = os.environ["USERPROFILE"]
    os.environ["XDG_CONFIG_HOME"] = os.path.join(SANDBOX, "config")
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(SANDBOX, "runtime")
    for _folder in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"):
        os.makedirs(os.environ[_folder], exist_ok=True)
    atexit.register(shutil.rmtree, SANDBOX, True)
else:
    SANDBOX = os.environ["TODOWIDGET_TEST_SANDBOX"]
