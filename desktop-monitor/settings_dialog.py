"""settings_dialog.py - the Settings window of Desktop Monitor.

The dialog does not touch any file itself. The card gives it the current
settings, and after OK it reads back two dicts:

* config_updates()     - keys for the app's config.json
* background_updates() - keys for the shared widget_background.json
                         (only the ones the user actually changed)
"""

from __future__ import annotations

import os

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QStandardPaths

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp)"


def pictures_dir() -> str:
    """The user's Pictures folder (where the image picker opens)."""
    folder = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
    return folder if folder and os.path.isdir(folder) else os.path.expanduser("~")


def _norm(mount: str) -> str:
    return str(mount or "").upper().rstrip("\\/")


class SettingsDialog(QtWidgets.QDialog):
    """Status feed, drives, disk alerts and background image."""

    def __init__(self, config: dict, drives: list, background: dict | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Desktop Monitor Settings")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._config = dict(config)
        self._drives = list(drives)
        self._bg = dict(background or {})
        self._image_path = str(self._bg.get("image_path") or "")
        self._start_image = self._image_path
        self._start_blur = self._number(self._bg.get("blur_radius"), 6.0)
        self._start_tint = self._number(self._bg.get("tint_opacity"),
                                        self._number(config.get("opacity"), 0.5))

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._build_feed_box())
        layout.addWidget(self._build_drives_box())
        layout.addWidget(self._build_alert_box())
        layout.addWidget(self._build_background_box())
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _number(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # -- status feed ----------------------------------------------------------
    def _build_feed_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Status feed indicator (optional)")
        form = QtWidgets.QFormLayout(box)
        note = QtWidgets.QLabel(
            "Shows a dot in the card header while a file is being updated, for example "
            "the log of a program you want to watch. Leave the file empty to hide it.")
        note.setWordWrap(True)
        form.addRow(note)
        self.feed_label = QtWidgets.QLineEdit(str(self._config.get("feed_label") or "FEED"))
        self.feed_label.setMaxLength(12)
        form.addRow("Label", self.feed_label)
        row = QtWidgets.QHBoxLayout()
        self.feed_path = QtWidgets.QLineEdit(str(self._config.get("feed_path") or ""))
        self.feed_path.setPlaceholderText("No file chosen")
        browse = QtWidgets.QPushButton("Browse...")
        browse.clicked.connect(self._browse_feed)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self.feed_path.clear)
        row.addWidget(self.feed_path, 1)
        row.addWidget(browse)
        row.addWidget(clear)
        form.addRow("File", row)
        self.feed_minutes = QtWidgets.QSpinBox()
        self.feed_minutes.setRange(1, 1440)
        self.feed_minutes.setSuffix(" minutes")
        self.feed_minutes.setValue(int(self._number(self._config.get("feed_minutes"), 10)))
        form.addRow("Green if changed within", self.feed_minutes)
        return box

    def _browse_feed(self) -> None:
        start = self.feed_path.text().strip() or os.path.expanduser("~")
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose the status feed file", start)
        if path:
            self.feed_path.setText(path)

    # -- drives ---------------------------------------------------------------
    def _build_drives_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Drives to show")
        column = QtWidgets.QVBoxLayout(box)
        hidden = {_norm(m) for m in (self._config.get("hidden_drives") or [])}
        self.drive_checks: dict[str, QtWidgets.QCheckBox] = {}
        if not self._drives:
            column.addWidget(QtWidgets.QLabel("No drives were detected."))
        for mount in self._drives:
            check = QtWidgets.QCheckBox(str(mount))
            check.setChecked(_norm(mount) not in hidden)
            self.drive_checks[mount] = check
            column.addWidget(check)
        return box

    # -- disk alerts ----------------------------------------------------------
    def _build_alert_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Low disk space notification")
        form = QtWidgets.QFormLayout(box)
        self.alert_enabled = QtWidgets.QCheckBox("Notify me when a drive is running low")
        self.alert_enabled.setChecked(bool(self._config.get("disk_alert_enabled")))
        form.addRow(self.alert_enabled)
        self.alert_gb = QtWidgets.QDoubleSpinBox()
        self.alert_gb.setRange(0.5, 100000.0)
        self.alert_gb.setDecimals(1)
        self.alert_gb.setSuffix(" GB free")
        self.alert_gb.setValue(self._number(self._config.get("disk_alert_gb"), 10.0))
        form.addRow("Alert below", self.alert_gb)
        return box

    # -- background -----------------------------------------------------------
    def _build_background_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Background image (shared by all Desktop Widgets)")
        form = QtWidgets.QFormLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.image_label = QtWidgets.QLabel()
        self.image_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        choose = QtWidgets.QPushButton("Choose...")
        choose.clicked.connect(self._choose_image)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self._clear_image)
        row.addWidget(self.image_label, 1)
        row.addWidget(choose)
        row.addWidget(clear)
        form.addRow("Image", row)
        self.blur_slider, self.blur_value = self._slider(0, 60, int(round(self._start_blur)), " px")
        form.addRow("Blur", self._with_value(self.blur_slider, self.blur_value))
        self.tint_slider, self.tint_value = self._slider(
            0, 95, int(round(self._start_tint * 100)), " %")
        form.addRow("Dark tint", self._with_value(self.tint_slider, self.tint_value))
        self._show_image()
        return box

    def _slider(self, low: int, high: int, value: int, suffix: str):
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(low, high)
        slider.setValue(max(low, min(high, value)))
        label = QtWidgets.QLabel()
        label.setMinimumWidth(48)
        slider.valueChanged.connect(lambda v, lab=label, suf=suffix: lab.setText("%d%s" % (v, suf)))
        label.setText("%d%s" % (slider.value(), suffix))
        return slider, label

    @staticmethod
    def _with_value(slider, label) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(slider, 1)
        row.addWidget(label)
        return holder

    def _show_image(self) -> None:
        self.image_label.setText(os.path.basename(self._image_path) if self._image_path
                                 else "None (uses the desktop wallpaper)")
        self.image_label.setToolTip(self._image_path)

    def _choose_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose Widget Background Image", pictures_dir(), IMAGE_FILTER)
        if path and os.path.isfile(path):
            self._image_path = path
            self._show_image()

    def _clear_image(self) -> None:
        self._image_path = ""
        self._show_image()

    # -- results --------------------------------------------------------------
    def config_updates(self) -> dict:
        """Values for config.json."""
        hidden = [mount for mount, check in self.drive_checks.items() if not check.isChecked()]
        return {
            "feed_label": self.feed_label.text().strip()[:12] or "FEED",
            "feed_path": self.feed_path.text().strip(),
            "feed_minutes": int(self.feed_minutes.value()),
            "hidden_drives": hidden,
            "disk_alert_enabled": self.alert_enabled.isChecked(),
            "disk_alert_gb": float(self.alert_gb.value()),
        }

    def background_updates(self) -> dict:
        """Keys of widget_background.json the user changed (empty dict = leave the file alone)."""
        updates: dict = {}
        if self._image_path != self._start_image:
            updates["image_path"] = self._image_path
        if self.blur_slider.value() != int(round(self._start_blur)):
            updates["blur_radius"] = float(self.blur_slider.value())
        if self.tint_slider.value() != int(round(self._start_tint * 100)):
            updates["tint_opacity"] = round(self.tint_slider.value() / 100.0, 2)
        return updates
