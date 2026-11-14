"""Settings window for Usage Monitor: add, edit, test, switch off and remove providers.

The window has two pages inside a stacked widget:

* the list page shows every provider you have set up;
* the form page adds a new provider or edits an existing one.

All network work (the Test button) runs on a worker thread, so the window never
freezes. Secret values (API keys) go straight to the system key store (Windows
Credential Manager, or the Secret Service on Linux) through ``secrets_store`` and
are never written to the config file, a label or a log. When no key store is
available the form says so in plain English and refuses to save a key.

The three helper modules are looked up through module-level names (``registry``,
``provider_config`` and ``secrets_store``) so tests can swap in fakes.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The helper modules may be missing while they are being written; keep importing.
try:
    from providers import registry  # type: ignore
except Exception:  # pragma: no cover - depends on the other modules
    registry = None  # type: ignore
try:
    import provider_config  # type: ignore
except Exception:  # pragma: no cover
    provider_config = None  # type: ignore
try:
    import secrets_store  # type: ignore
except Exception:  # pragma: no cover
    secrets_store = None  # type: ignore

# ---------------------------------------------------------------- palette (matches the card)
C_CARD = "#101218"
C_PANEL = "#171A22"
C_FIELD = "#0B0D12"
C_BORDER = "#2A3040"
C_ACCENT = "#22D3EE"
C_GREEN = "#34D399"
C_AMBER = "#F59E0B"
C_RED = "#EF4444"
C_LABEL = "#8A93A6"
C_VALUE = "#E8ECF2"

STYLE = f"""
QDialog {{ background: {C_CARD}; }}
QLabel {{ color: {C_VALUE}; font-size: 13px; }}
QLabel[role="muted"] {{ color: {C_LABEL}; font-size: 12px; }}
QLabel[role="title"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="heading"] {{ color: {C_ACCENT}; font-size: 12px; font-weight: 600; }}
QLabel[role="ok"] {{ color: {C_GREEN}; }}
QLabel[role="warn"] {{ color: {C_AMBER}; }}
QLabel[role="bad"] {{ color: {C_RED}; }}
QFrame#row {{ background: {C_PANEL}; border: 1px solid {C_BORDER}; border-radius: 10px; }}
QLineEdit, QComboBox {{
    background: {C_FIELD}; color: {C_VALUE}; border: 1px solid {C_BORDER};
    border-radius: 6px; padding: 6px 8px; font-size: 13px; min-height: 18px;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {C_ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {C_FIELD}; color: {C_VALUE}; selection-background-color: {C_BORDER};
}}
QPushButton {{
    background: {C_PANEL}; color: {C_VALUE}; border: 1px solid {C_BORDER};
    border-radius: 6px; padding: 6px 14px; font-size: 13px;
}}
QPushButton:hover {{ border-color: {C_ACCENT}; }}
QPushButton:disabled {{ color: {C_LABEL}; }}
QPushButton[primary="true"] {{
    background: {C_ACCENT}; color: #06222A; border: none; font-weight: 600;
}}
QPushButton[primary="true"]:disabled {{ background: {C_BORDER}; color: {C_LABEL}; }}
QCheckBox {{ color: {C_VALUE}; }}
QCheckBox::indicator {{ width: 34px; height: 18px; border-radius: 9px;
    background: {C_BORDER}; }}
QCheckBox::indicator:checked {{ background: {C_ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""

# (category id, heading shown to the user), in display order.
CATEGORY_HEADINGS: list[tuple[str, str]] = [
    ("signin", "Signed in on this PC"),
    ("api_key", "API key"),
    ("budget", "Spending budgets"),
    ("local", "Local"),
    ("custom", "Custom"),
]

def _store_name() -> str:
    """Where keys are kept on this computer, for sentences shown to the user."""
    return str(getattr(secrets_store, "STORE_NAME", "") or "the system key store")


def _key_store_problem() -> str:
    """Plain-English reason keys cannot be saved here, or "" when they can."""
    check = getattr(secrets_store, "key_store_problem", None)
    if check is None:
        return ""
    try:
        return str(check() or "")
    except Exception:  # noqa: BLE001 - a broken check must not break the form
        return ""


SECRET_PLACEHOLDER = f"Saved in {_store_name()} (leave blank to keep)"
EMPTY_TEXT = "No providers yet. Press Add a provider to start."
NOT_TESTED = "Not tested yet"
TEST_ID = "test0000"  # temporary id used for an unsaved Test


def _secret_error_type() -> type:
    """Return SecretStoreError from the credential module, or plain Exception."""
    return getattr(secrets_store, "SecretStoreError", Exception)


def _set_role(label: QtWidgets.QLabel, role: str) -> None:
    """Switch a label's colour role (muted, ok, warn, bad) and re-apply the style."""
    label.setProperty("role", role)
    label.style().unpolish(label)
    label.style().polish(label)


def _label(text: str = "", role: str = "", wrap: bool = True) -> QtWidgets.QLabel:
    """Create a styled QLabel."""
    lab = QtWidgets.QLabel(text)
    lab.setWordWrap(wrap)
    if role:
        lab.setProperty("role", role)
    return lab


def _button(text: str, primary: bool = False) -> QtWidgets.QPushButton:
    """Create a button that never reacts to Enter (so Enter cannot save by accident)."""
    btn = QtWidgets.QPushButton(text)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    if primary:
        btn.setProperty("primary", True)
    btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    return btn


def summarise_result(result: Any, max_lines: int = 4) -> str:
    """Short plain-English summary of a TestResult: the message plus up to 4 readings."""
    lines: list[str] = []
    message = getattr(result, "message", "") or ""
    if message:
        lines.append(message)
    if getattr(result, "ok", False):
        for row in list(getattr(result, "rows", []) or [])[:max_lines]:
            name = getattr(row, "model_label", "") or getattr(row, "provider_label", "")
            pct = getattr(row, "percent_used", None)
            bal = getattr(row, "credit_balance_usd", None)
            if pct is not None:
                lines.append(f"{name}: {pct:.0f}% used")
            elif bal is not None:
                lines.append(f"{name}: ${bal:,.2f} left")
            elif name:
                lines.append(str(name))
    return "\n".join(lines[: max_lines + 1])


# ---------------------------------------------------------------- worker thread for Test
class _TestSignals(QtCore.QObject):
    """Carries the Test result back to the UI thread."""

    finished = QtCore.Signal(object)


class _TestTask(QtCore.QRunnable):
    """Runs ``registry.test_instance`` off the UI thread."""

    def __init__(self, instance: Any, secret: str | None, signals: _TestSignals) -> None:
        super().__init__()
        self.instance = instance
        self.secret = secret
        self.signals = signals

    def run(self) -> None:
        try:
            result = registry.test_instance(self.instance, secret_override=self.secret)
        except Exception:  # never leak details that could contain a key
            result = _FallbackResult(False, "The test could not be completed.")
        self.signals.finished.emit(result)


class _FallbackResult:
    """Minimal TestResult stand-in used only if the test call itself crashes."""

    def __init__(self, ok: bool, message: str) -> None:
        self.ok = ok
        self.message = message
        self.rows: list[Any] = []


# ---------------------------------------------------------------- the window
class SettingsWindow(QtWidgets.QDialog):
    """List, add, edit, test, switch and remove usage providers."""

    providers_changed = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None,
                 config_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Usage Monitor settings")
        self.setMinimumSize(560, 520)
        self.resize(600, 640)
        self.setStyleSheet(STYLE)
        self._config_path = config_path
        self._config: dict[str, Any] = {}
        self._status: dict[str, str] = {}        # instance_id -> last Test summary
        self._status_ok: dict[str, bool] = {}
        self._editing_id: str | None = None      # None = adding
        self._kind: Any = None
        self._inputs: dict[str, QtWidgets.QWidget] = {}
        self._last_test: tuple[bool, str] | None = None
        self._testing = False
        self._pool = QtCore.QThreadPool(self)
        self._signals = _TestSignals(self)
        self._signals.finished.connect(self._on_test_finished)

        self._stack = QtWidgets.QStackedWidget()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.addWidget(self._stack)
        self._stack.addWidget(self._build_list_page())
        self._stack.addWidget(self._build_form_page())

        self.reload()

    # ------------------------------------------------------------ config helpers
    def _load(self) -> dict[str, Any]:
        if provider_config is None:
            return {"version": 2, "poll_minutes": 5, "providers": []}
        return provider_config.load_config(self._config_path)

    def _save(self) -> None:
        provider_config.save_config(self._config, self._config_path)

    def _save_or_warn(self) -> bool:
        """Save the config and tell the user when it could not be saved.

        The config can be a file of a newer version of the app, and that file
        must not be overwritten; the user has to know that nothing was saved.
        """
        try:
            self._save()
            return True
        except Exception as exc:  # noqa: BLE001 - every problem is shown to the user
            QtWidgets.QMessageBox.warning(
                self, "Settings",
                str(exc) or "The settings could not be saved.")
            return False

    def reload(self) -> None:
        """Re-read the config from disk and redraw the list."""
        self._config = self._load()
        self._refresh_list()
        self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------ list page
    def _build_list_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(_label("Your providers", "title", wrap=False))
        lay.addWidget(_label("Choose which services Usage Monitor shows on the card.", "muted"))

        self._list_host = QtWidgets.QWidget()
        self._list_layout = QtWidgets.QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_host)
        lay.addWidget(scroll, 1)

        foot = QtWidgets.QHBoxLayout()
        self._add_btn = _button("Add a provider", primary=True)
        self._add_btn.clicked.connect(self.start_add)
        close_btn = _button("Close")
        close_btn.clicked.connect(self.close)
        foot.addWidget(self._add_btn)
        foot.addStretch(1)
        foot.addWidget(close_btn)
        lay.addLayout(foot)
        return page

    def _kind_name(self, kind_id: str) -> str:
        kind = registry.get_kind(kind_id) if registry is not None else None
        return getattr(kind, "display_name", "") or kind_id

    def _refresh_list(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        instances = provider_config.list_instances(self._config) if provider_config else []
        if not instances:
            self._empty_label = _label(EMPTY_TEXT, "muted")
            self._empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._list_layout.addWidget(self._empty_label)
        for inst in instances:
            self._list_layout.addWidget(self._make_row(inst))
        self._list_layout.addStretch(1)

    def _make_row(self, inst: Any) -> QtWidgets.QFrame:
        row = QtWidgets.QFrame()
        row.setObjectName("row")
        grid = QtWidgets.QGridLayout(row)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(10)

        name = _label(inst.label, wrap=False)
        name.setStyleSheet("font-weight: 600; font-size: 14px;")
        name.setObjectName("rowLabel")
        kind = _label(self._kind_name(inst.kind), "muted", wrap=False)
        status = _label(self._status.get(inst.instance_id, NOT_TESTED), "muted")
        status.setObjectName("rowStatus")
        if inst.instance_id in self._status:
            _set_role(status, "ok" if self._status_ok.get(inst.instance_id) else "bad")

        toggle = QtWidgets.QCheckBox("On")
        toggle.setChecked(bool(inst.enabled))
        toggle.setObjectName("rowToggle")
        toggle.setToolTip("Show this provider on the card")
        toggle.toggled.connect(lambda on, i=inst.instance_id: self.toggle_instance(i, on))
        edit = _button("Edit")
        edit.setObjectName("rowEdit")
        edit.clicked.connect(lambda _=False, i=inst.instance_id: self.start_edit(i))
        remove = _button("Remove")
        remove.setObjectName("rowRemove")
        remove.clicked.connect(lambda _=False, i=inst.instance_id: self.remove_instance(i))

        grid.addWidget(name, 0, 0)
        grid.addWidget(kind, 1, 0)
        grid.addWidget(status, 2, 0, 1, 4)
        grid.addWidget(toggle, 0, 1, 2, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        grid.addWidget(edit, 0, 2, 2, 1)
        grid.addWidget(remove, 0, 3, 2, 1)
        grid.setColumnStretch(0, 1)
        return row

    # ------------------------------------------------------------ list actions
    def toggle_instance(self, instance_id: str, enabled: bool) -> None:
        """Switch a provider on or off and save at once."""
        provider_config.set_enabled(self._config, instance_id, enabled)
        if not self._save_or_warn():
            return
        self.providers_changed.emit()

    def remove_instance(self, instance_id: str) -> None:
        """Ask, then remove a provider and its saved key."""
        inst = self._find(instance_id)
        label = inst.label if inst else "this provider"
        answer = QtWidgets.QMessageBox.question(
            self, "Remove provider",
            f"Remove {label}? Its saved key is deleted from {_store_name()}.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No)
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        key_problem = ""
        try:
            provider_config.remove_instance(self._config, instance_id)
        except _secret_error_type() as exc:
            # The provider is gone from the config, but its key could not be deleted.
            key_problem = str(exc)
        self._status.pop(instance_id, None)
        self._status_ok.pop(instance_id, None)
        self._save_or_warn()
        if key_problem:
            QtWidgets.QMessageBox.warning(
                self, "Remove provider",
                "The provider was removed, but its saved key could not be deleted. " + key_problem)
        self._refresh_list()
        self.providers_changed.emit()

    def _find(self, instance_id: str) -> Any:
        for inst in provider_config.list_instances(self._config):
            if inst.instance_id == instance_id:
                return inst
        return None

    # ------------------------------------------------------------ form page
    def _build_form_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._form_title = _label("Add a provider", "title", wrap=False)
        lay.addWidget(self._form_title)

        self._picker_caption = _label("What do you want to add?", "muted")
        lay.addWidget(self._picker_caption)
        self._picker = QtWidgets.QComboBox()
        self._picker.currentIndexChanged.connect(self._on_pick)
        lay.addWidget(self._picker)

        self._form_host = QtWidgets.QWidget()
        self._form_layout = QtWidgets.QVBoxLayout(self._form_host)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setSpacing(6)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form_host)
        lay.addWidget(scroll, 1)

        self._message = _label("", "muted")
        self._message.setObjectName("formMessage")
        lay.addWidget(self._message)

        foot = QtWidgets.QHBoxLayout()
        self._detect_btn = _button("Detect")
        self._detect_btn.clicked.connect(self._on_detect)
        self._test_btn = _button("Test")
        self._test_btn.clicked.connect(self._on_test)
        self._save_btn = _button("Save", primary=True)
        self._save_btn.clicked.connect(self._on_save)
        back = _button("Back")
        back.clicked.connect(self._back_to_list)
        foot.addWidget(self._detect_btn)
        foot.addWidget(self._test_btn)
        foot.addStretch(1)
        foot.addWidget(back)
        foot.addWidget(self._save_btn)
        lay.addLayout(foot)
        return page

    def _fill_picker(self, selected: str | None = None) -> None:
        """Fill the picker with the catalogue, grouped by category with headings."""
        model = QtGui.QStandardItemModel(self._picker)
        kinds = list(registry.catalogue()) if registry is not None else []
        known = {c for c, _ in CATEGORY_HEADINGS}
        groups = list(CATEGORY_HEADINGS)
        # Any category we do not know about still shows up, under "Other".
        if any(k.category not in known for k in kinds):
            groups.append(("", "Other"))
        select_row = -1
        first_row = -1
        for cat, heading in groups:
            members = [k for k in kinds if (k.category == cat if cat else k.category not in known)]
            if not members:
                continue
            head = QtGui.QStandardItem(heading.upper())
            head.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            head.setForeground(QtGui.QColor(C_ACCENT))
            model.appendRow(head)
            for kind in members:
                item = QtGui.QStandardItem("    " + kind.display_name)
                item.setData(kind.kind, QtCore.Qt.ItemDataRole.UserRole)
                model.appendRow(item)
                if first_row < 0:
                    first_row = model.rowCount() - 1
                if selected and kind.kind == selected:
                    select_row = model.rowCount() - 1
        self._picker.blockSignals(True)
        self._picker.setModel(model)
        self._picker.blockSignals(False)
        row = select_row if select_row >= 0 else first_row
        if row >= 0:
            self._picker.setCurrentIndex(row)
        self._on_pick(row)

    def start_add(self) -> None:
        """Open the form to add a new provider."""
        self._editing_id = None
        self._form_title.setText("Add a provider")
        self._picker.setVisible(True)
        self._picker_caption.setVisible(True)
        self._fill_picker()
        self._stack.setCurrentIndex(1)

    def start_edit(self, instance_id: str) -> None:
        """Open the form pre-filled with an existing provider."""
        inst = self._find(instance_id)
        if inst is None:
            return
        self._editing_id = instance_id
        self._form_title.setText("Edit provider")
        self._picker.setVisible(False)
        self._picker_caption.setVisible(False)
        self._fill_picker(selected=inst.kind)
        self._build_form(registry.get_kind(inst.kind), inst)
        self._stack.setCurrentIndex(1)

    def _back_to_list(self) -> None:
        self._stack.setCurrentIndex(0)

    def _on_pick(self, row: int) -> None:
        if self._editing_id is not None:
            return
        kind_id = self._picker.itemData(row, QtCore.Qt.ItemDataRole.UserRole) if row >= 0 else None
        kind = registry.get_kind(kind_id) if (registry and kind_id) else None
        self._build_form(kind, None)

    def _clear_form(self) -> None:
        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._inputs = {}
        self._last_test = None

    def _build_form(self, kind: Any, inst: Any) -> None:
        """Draw the description, notes, label and one input per field of ``kind``."""
        self._clear_form()
        self._kind = kind
        self._message.setText("")
        if kind is None:
            self._detect_btn.setVisible(False)
            self._test_btn.setEnabled(False)
            self._save_btn.setEnabled(False)
            return
        self._test_btn.setEnabled(not self._testing)
        self._save_btn.setEnabled(True)
        lay = self._form_layout

        lay.addWidget(_label(kind.description))
        if any(spec.kind == "secret" for spec in kind.fields):
            problem = _key_store_problem()
            if problem:
                # No key store: say so, and refuse to save (a key is never put in a file).
                warning = _label(problem, "bad")
                warning.setObjectName("keyStoreProblem")
                lay.addWidget(warning)
                if any(spec.kind == "secret" and spec.required for spec in kind.fields):
                    self._save_btn.setEnabled(False)  # a key is needed and cannot be kept
        if kind.stability == "unofficial":
            lay.addWidget(_label("Unofficial: " + (kind.stability_note or ""), "warn"))
        elif kind.stability == "manual":
            lay.addWidget(_label(
                "Manual: you type these numbers yourself; nothing is fetched from the service.",
                "muted"))
        if kind.docs_url:
            link = QtWidgets.QLabel(f'<a href="{kind.docs_url}" style="color:{C_ACCENT}">'
                                    "Where to get this</a>")
            link.setOpenExternalLinks(True)
            link.setObjectName("docsLink")
            lay.addWidget(link)

        lay.addSpacing(6)
        lay.addWidget(_label("Name on the card", "muted"))
        label_edit = QtWidgets.QLineEdit(inst.label if inst else kind.default_label())
        label_edit.setObjectName("field_label")
        self._inputs["__label__"] = label_edit
        lay.addWidget(label_edit)

        settings = dict(inst.settings) if inst else {}
        for spec in kind.fields:
            lay.addWidget(_label(spec.label + ("" if spec.required else " (optional)"), "muted"))
            lay.addWidget(self._make_input(spec, settings, editing=inst is not None))
            if spec.help:
                lay.addWidget(_label(spec.help, "muted"))
        lay.addStretch(1)

        self._detect_btn.setVisible(bool(kind.supports_detect))

    def _make_input(self, spec: Any, settings: dict[str, Any], editing: bool) -> QtWidgets.QWidget:
        """Create the input widget for one FieldSpec and remember it in ``self._inputs``."""
        value = settings.get(spec.key, spec.default)
        value = "" if value is None else str(value)
        kind = spec.kind
        host = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(host)
        h.setContentsMargins(0, 0, 0, 0)
        if kind == "choice":
            widget: QtWidgets.QWidget = QtWidgets.QComboBox()
            widget.addItems(list(spec.choices))
            if value in spec.choices:
                widget.setCurrentText(value)
            h.addWidget(widget, 1)
        else:
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText(spec.placeholder)
            widget = edit
            if kind == "secret":
                edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
                if editing:
                    edit.setPlaceholderText(SECRET_PLACEHOLDER)
                h.addWidget(edit, 1)
                show = _button("Show")
                show.setCheckable(True)
                show.toggled.connect(lambda on, e=edit, b=show: (
                    e.setEchoMode(QtWidgets.QLineEdit.EchoMode.Normal if on
                                  else QtWidgets.QLineEdit.EchoMode.Password),
                    b.setText("Hide" if on else "Show")))
                h.addWidget(show)
            else:
                edit.setText(value)
                if kind == "number":
                    validator = QtGui.QDoubleValidator(edit)
                    validator.setNotation(QtGui.QDoubleValidator.Notation.StandardNotation)
                    edit.setValidator(validator)
                h.addWidget(edit, 1)
                if kind == "path":
                    browse = _button("Browse")
                    browse.clicked.connect(lambda _=False, e=edit: self._browse(e))
                    h.addWidget(browse)
        widget.setObjectName("field_" + spec.key)
        self._inputs[spec.key] = widget
        return host

    def _browse(self, edit: QtWidgets.QLineEdit) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose a file", edit.text())
        if path:
            edit.setText(path)

    # ------------------------------------------------------------ form helpers
    def _say(self, text: str, role: str = "muted") -> None:
        self._message.setText(text)
        _set_role(self._message, role)

    @staticmethod
    def _text_of(widget: QtWidgets.QWidget) -> str:
        if isinstance(widget, QtWidgets.QComboBox):
            return widget.currentText()
        return widget.text() if isinstance(widget, QtWidgets.QLineEdit) else ""

    def _collect(self) -> tuple[str, dict[str, str], dict[str, str]]:
        """Return (label, non-secret settings, secrets) as typed in the form."""
        label = self._text_of(self._inputs["__label__"]).strip()
        settings: dict[str, str] = {}
        secrets: dict[str, str] = {}
        for spec in self._kind.fields:
            raw = self._text_of(self._inputs[spec.key])
            if spec.kind == "secret":
                secrets[spec.key] = raw
            elif spec.key == "key_prefix":
                settings[spec.key] = raw.lstrip()
            else:
                settings[spec.key] = raw.strip()
        return label, settings, secrets

    def _has_saved_secret(self) -> bool:
        if self._editing_id is None or secrets_store is None:
            return False
        try:
            return bool(secrets_store.has_secret(self._editing_id))
        except Exception:
            return False

    def _validate(self, label: str, settings: dict[str, str], secrets: dict[str, str]) -> str:
        """Return a plain-English problem, or an empty string when the form is fine."""
        if not label:
            return "Please give this provider a name."
        for spec in self._kind.fields:
            if not spec.required:
                continue
            if spec.kind == "secret":
                typed = secrets.get(spec.key, "").strip()
                if not typed and not (self._editing_id and self._has_saved_secret()):
                    return f"Please fill in: {spec.label}."
            elif not settings.get(spec.key):
                return f"Please fill in: {spec.label}."
        for spec in self._kind.fields:
            if spec.kind == "number" and settings.get(spec.key):
                try:
                    float(settings[spec.key].replace(",", "."))
                except ValueError:
                    return f"{spec.label} must be a number."
        return ""

    def _make_instance(self, instance_id: str, label: str, settings: dict[str, str]) -> Any:
        enabled = True
        if self._editing_id:
            old = self._find(self._editing_id)
            enabled = bool(old.enabled) if old else True
        return provider_config.ProviderInstance(
            instance_id=instance_id, kind=self._kind.kind, label=label,
            enabled=enabled, settings=dict(settings))

    # ------------------------------------------------------------ Detect
    def _on_detect(self) -> None:
        if self._kind is None:
            return
        try:
            result = self._kind.detect()
        except Exception:
            self._say("Could not look for it on this PC.", "warn")
            return
        if getattr(result, "found", False):
            target = None
            for spec in self._kind.fields:
                if spec.kind == "path" or spec.key == "credentials_path":
                    target = self._inputs.get(spec.key)
                    break
            if isinstance(target, QtWidgets.QLineEdit) and result.path:
                target.setText(result.path)
            self._say(result.message or "Found it.", "ok")
        else:
            self._say(getattr(result, "message", "") or "Not found on this PC.", "warn")

    # ------------------------------------------------------------ Test
    def _on_test(self) -> None:
        if self._kind is None or self._testing:
            return
        label, settings, secrets = self._collect()
        problem = self._validate(label, settings, secrets)
        if problem:
            self._say(problem, "bad")
            return
        typed = next((v.strip() for v in secrets.values() if v.strip()), None)
        instance_id = self._editing_id or TEST_ID
        instance = self._make_instance(instance_id, label, settings)
        self._testing = True
        self._test_btn.setEnabled(False)
        self._test_btn.setText("Testing...")
        self._say("Testing...", "muted")
        self._pool.start(_TestTask(instance, typed, self._signals))

    def _on_test_finished(self, result: Any) -> None:
        self._testing = False
        self._test_btn.setEnabled(True)
        self._test_btn.setText("Test")
        ok = bool(getattr(result, "ok", False))
        text = summarise_result(result) if ok else (
            getattr(result, "message", "") or "The test did not work.")
        self._last_test = (ok, text)
        self._say(text, "ok" if ok else "bad")
        if self._editing_id:
            self._status[self._editing_id] = text.splitlines()[0] if text else ""
            self._status_ok[self._editing_id] = ok

    # ------------------------------------------------------------ Save
    def _on_save(self) -> None:
        if self._kind is None:
            return
        label, settings, secrets = self._collect()
        problem = self._validate(label, settings, secrets)
        if problem:
            self._say(problem, "bad")
            return
        editing = self._editing_id is not None
        instance_id = self._editing_id or provider_config.new_instance_id(self._config)
        instance = self._make_instance(instance_id, label, settings)
        previous_config = copy.deepcopy(self._config)
        key_written = False
        try:
            for value in secrets.values():
                if value.strip():  # blank on edit means "keep the existing key"
                    secrets_store.set_secret(instance_id, value.strip())
                    key_written = True
        except _secret_error_type() as exc:
            self._say(str(exc) or "The key could not be saved.", "bad")
            return
        try:
            if editing:
                provider_config.update_instance(self._config, instance)
            else:
                provider_config.add_instance(self._config, instance)
            self._save()
        except Exception:
            # The config could not be saved, so the provider is not in it. A
            # key that was just written would then be an orphan: no config
            # would ever mention it, so neither Remove nor --purge-secrets
            # could ever delete it. Put it back the way it was.
            self._config = previous_config
            if key_written:
                try:
                    secrets_store.delete_secret(instance_id)
                except Exception:  # noqa: BLE001 - the key stays, the save failed anyway
                    pass
            self._say("The settings could not be saved.", "bad")
            return
        if self._last_test:
            self._status[instance_id] = self._last_test[1].splitlines()[0]
            self._status_ok[instance_id] = self._last_test[0]
        self._editing_id = None
        self._refresh_list()
        self._stack.setCurrentIndex(0)
        self.providers_changed.emit()

    # ------------------------------------------------------------ keyboard
    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        """Esc closes the window; Enter does nothing (it must never save by accident)."""
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)


_shared_window: SettingsWindow | None = None


def open_settings(parent: QtWidgets.QWidget | None = None) -> SettingsWindow:
    """Show the single shared settings window (create once, then raise it)."""
    global _shared_window
    if _shared_window is None:
        _shared_window = SettingsWindow(parent)
        _shared_window.destroyed.connect(lambda *_: _clear_shared())
    else:
        _shared_window.reload()
    _shared_window.show()
    _shared_window.raise_()
    _shared_window.activateWindow()
    return _shared_window


def _clear_shared() -> None:
    global _shared_window
    _shared_window = None
