"""Small dialogs: flow templates, version history, account chooser."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from flowpilot.core.templates import TEMPLATES
from flowpilot.storage.models import Account, FlowVersion
from flowpilot.ui.widgets.clay import ClayButton, muted_label, title_label


class ListDialog(QDialog):
    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None, multi: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.addWidget(title_label(title, "subtitle"))
        if subtitle:
            root.addWidget(muted_label(subtitle))
        self.extra = QVBoxLayout()
        root.addLayout(self.extra)
        self.list = QListWidget()
        if multi:
            self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        root.addWidget(self.list, 1)
        self.buttons = QHBoxLayout()
        self.buttons.addStretch(1)
        cancel = ClayButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = ClayButton("OK", "check", "primary")
        self.ok.clicked.connect(self.accept)
        self.buttons.addWidget(cancel)
        self.buttons.addWidget(self.ok)
        root.addLayout(self.buttons)


class TemplateDialog(ListDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("New flow", "Start from a template or from scratch.", parent)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Flow name")
        self.extra.addWidget(self.name)
        for name, (desc, _) in TEMPLATES.items():
            it = QListWidgetItem(f"{name}\n{desc}")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self.ok.setText("Create")

    def result_values(self) -> tuple[str, str]:
        item = self.list.currentItem()
        template = item.data(Qt.ItemDataRole.UserRole) if item else "Empty flow"
        name = self.name.text().strip() or (template if template != "Empty flow" else "New flow")
        return name, template


class VersionsDialog(ListDialog):
    def __init__(self, versions: list[FlowVersion], parent: QWidget | None = None) -> None:
        super().__init__("Version history", "The last 10 saved versions. Restoring can be undone with Ctrl+Z.", parent)
        for v in versions:
            n = len(json.loads(v.steps_json or "[]"))
            it = QListWidgetItem(f"Version {v.version} — {v.saved_at:%Y-%m-%d %H:%M} — {n} step(s)")
            it.setData(Qt.ItemDataRole.UserRole, v.steps_json)
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self.ok.setText("Restore")

    def chosen_json(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class AccountChooser(ListDialog):
    def __init__(self, accounts: list[Account], selected: list[int], parent: QWidget | None = None,
                 group_names: dict[int, str] | None = None) -> None:
        super().__init__("Choose accounts", "Tick the accounts to run.", parent)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self._filter)
        self.extra.addWidget(self.search)
        row = QHBoxLayout()
        all_btn = ClayButton("Select all", compact=True)
        none_btn = ClayButton("None", compact=True)
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        row.addStretch(1)
        self.extra.addLayout(row)
        for a in accounts:
            group = (group_names or {}).get(a.group_id or -1, "")
            it = QListWidgetItem(f"{a.name}" + (f"   · {group}" if group else ""))
            it.setData(Qt.ItemDataRole.UserRole, a.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if a.id in selected else Qt.CheckState.Unchecked)
            self.list.addItem(it)

    def _filter(self, text: str) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(text.lower() not in it.text().lower())

    def _set_all(self, on: bool) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def chosen(self) -> list[int]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]
