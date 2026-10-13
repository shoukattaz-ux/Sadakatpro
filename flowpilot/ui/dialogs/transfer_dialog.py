"""Export everything to one file / import it on another PC."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from flowpilot.services.portable import EXTENSION, MIN_PASSWORD, PortableError, export_all, import_all, read_manifest
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import info, show_error
from flowpilot.ui.theme.palette import ThemeManager
from flowpilot.ui.widgets.clay import ClayButton, muted_label, title_label

FILE_FILTER = f"FlowPilot export (*{EXTENSION})"


def _password_box(placeholder: str) -> QLineEdit:
    box = QLineEdit()
    box.setEchoMode(QLineEdit.EchoMode.Password)
    box.setPlaceholderText(placeholder)
    return box


def _error_label() -> QLabel:
    label = QLabel("")
    label.setWordWrap(True)
    label.setProperty("role", "error")
    return label


def apply_imported(app: AppContext) -> None:
    """Make the running app show the imported data — no restart needed."""
    s = app.settings
    ThemeManager.instance().apply(s.theme, s.font_size)
    app.services.scheduler.reload()
    for what in ("connections", "groups", "accounts", "flows", "schedules", "runs", "settings", "imported"):
        app.bridge.data_changed.emit(what)


class ExportDialog(QDialog):
    """Choose a password (+ optional run history) and save one ``.flowpilot`` file."""

    def __init__(self, app: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.saved_path: Path | None = None
        self.setWindowTitle("Export all data")
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(title_label("Export everything to one file", "subtitle"))
        root.addWidget(muted_label(
            "The file holds all your flows, accounts (with their passwords and variables), groups, browser "
            "connections (with API keys), schedules and settings. Copy it to another PC and use Import there "
            "— everything comes back.\n\nBecause it contains your passwords, the file is locked with a password "
            "you choose now. You need the same password to import it. Keep it safe; it cannot be recovered."))
        form = QFormLayout()
        self.pw1 = _password_box(f"at least {MIN_PASSWORD} characters")
        self.pw2 = _password_box("type it again")
        form.addRow("File password", self.pw1)
        form.addRow("Repeat password", self.pw2)
        root.addLayout(form)
        self.history = QCheckBox("Also include run history (results of past runs, without screenshots)")
        root.addWidget(self.history)
        self.error = _error_label()
        root.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = ClayButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = ClayButton("Export…", "download", "primary")
        self.ok.clicked.connect(self._export)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        root.addLayout(buttons)

    def _export(self) -> None:
        pw = self.pw1.text()
        if len(pw) < MIN_PASSWORD:
            self.error.setText(f"Choose a password with at least {MIN_PASSWORD} characters.")
            return
        if pw != self.pw2.text():
            self.error.setText("The two passwords are not the same.")
            return
        default = Path.home() / f"FlowPilot-export-{datetime.now():%Y-%m-%d}{EXTENSION}"
        target, _ = QFileDialog.getSaveFileName(self, "Save export", str(default), FILE_FILTER)
        if not target:
            return
        svc = self.app.services
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            summary = export_all(svc.db, svc.secrets, self.app.settings, Path(target), pw, self.history.isChecked())
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            show_error(self, "Export failed", exc)
            return
        QApplication.restoreOverrideCursor()
        self.saved_path = summary.path
        c = summary.counts
        text = (f"Saved to:\n{summary.path}\n\n{c.get('flows', 0)} flow(s), {c.get('accounts', 0)} account(s), "
                f"{c.get('groups', 0)} group(s), {c.get('connections', 0)} connection(s), "
                f"{c.get('schedules', 0)} schedule(s) and all settings.")
        if summary.warnings:
            text += "\n\nNote:\n• " + "\n• ".join(summary.warnings)
        info(self, "Export finished", text)
        self.accept()


class ImportDialog(QDialog):
    """Pick a ``.flowpilot`` file, enter its password and replace or merge."""

    def __init__(self, app: AppContext, parent: QWidget | None = None, path: Path | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.imported = False
        self.setWindowTitle("Import data")
        self.setMinimumWidth(540)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(title_label("Import from another PC", "subtitle"))
        root.addWidget(muted_label("Choose a file made with “Export all data” and type the password that was used "
                                   "to export it."))
        pick = QHBoxLayout()
        self.file = QLineEdit()
        self.file.setPlaceholderText(f"…{EXTENSION} file")
        self.file.textChanged.connect(self._describe)
        browse = ClayButton("Choose…", "folder", compact=True)
        browse.clicked.connect(self._browse)
        pick.addWidget(self.file, 1)
        pick.addWidget(browse)
        root.addLayout(pick)
        self.about = muted_label("")
        root.addWidget(self.about)
        form = QFormLayout()
        self.pw = _password_box("password of the export")
        form.addRow("File password", self.pw)
        root.addLayout(form)
        self.replace = QRadioButton("Replace everything here with the file (recommended for a new PC)")
        self.merge = QRadioButton("Add to what is already here (keeps current data and settings)")
        self.replace.setChecked(True)
        root.addWidget(self.replace)
        root.addWidget(self.merge)
        root.addWidget(muted_label("A safety backup of the current data is made first (Settings → Data → Restore "
                                   "can bring it back)."))
        self.error = _error_label()
        root.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = ClayButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = ClayButton("Import", "upload", "primary")
        self.ok.clicked.connect(self._import)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        root.addLayout(buttons)
        if path is not None:
            self.file.setText(str(path))

    def _browse(self) -> None:
        src, _ = QFileDialog.getOpenFileName(self, "Choose a FlowPilot export", str(Path.home()),
                                             f"{FILE_FILTER};;All files (*)")
        if src:
            self.file.setText(src)
            self.pw.setFocus()

    def _describe(self, text: str) -> None:
        self.error.setText("")
        path = Path(text.strip())
        if not text.strip() or not path.is_file():
            self.about.setText("")
            return
        try:
            m = read_manifest(path)
        except PortableError as exc:
            self.about.setText("")
            self.error.setText(str(exc))
            return
        c = m.get("counts", {})
        self.about.setText(f"Made {m.get('created', '?').replace('T', ' ')} with FlowPilot {m.get('app_version', '?')}"
                           f": {c.get('flows', 0)} flow(s), {c.get('accounts', 0)} account(s), "
                           f"{c.get('connections', 0)} connection(s), {c.get('schedules', 0)} schedule(s).")

    def _import(self) -> None:
        path = Path(self.file.text().strip())
        if not path.is_file():
            self.error.setText("Choose the export file first.")
            return
        if not self.pw.text():
            self.error.setText("Type the password of the export.")
            return
        svc = self.app.services
        if svc.engine.is_busy():
            self.error.setText("Runs are in progress. Stop them first, then import.")
            return
        mode = "replace" if self.replace.isChecked() else "merge"
        try:
            result = import_all(svc.paths, svc.db, svc.secrets, svc.settings, svc.repos, path, self.pw.text(), mode)
        except PortableError as exc:
            self.error.setText(str(exc))
            return
        except Exception as exc:
            show_error(self, "Import failed", exc)
            return
        self.imported = True
        apply_imported(self.app)
        text = ("All data was restored: " if mode == "replace" else "Added: ") + result.describe() + "."
        if mode == "replace":
            text += "\nSettings were restored too."
        if result.warnings:
            text += "\n\nNote:\n• " + "\n• ".join(result.warnings)
        info(self, "Import finished", text)
        self.accept()
