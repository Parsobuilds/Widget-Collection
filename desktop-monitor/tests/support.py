"""Shared test setup: offscreen Qt and a throw-away APPDATA, before desktop_monitor is imported."""

import os
import sys
import tempfile

PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
TEMP_APPDATA = tempfile.mkdtemp(prefix="dm_test_appdata_")
os.environ["APPDATA"] = TEMP_APPDATA            # Windows data folder root
os.environ["XDG_CONFIG_HOME"] = TEMP_APPDATA    # Linux data folder root


def import_monitor():
    import desktop_monitor
    return desktop_monitor


def qapp():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
