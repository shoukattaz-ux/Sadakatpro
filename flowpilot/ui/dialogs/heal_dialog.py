"""Review self-healed selectors and save the ones you trust."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from flowpilot.storage.models import SelectorHeal
from flowpilot.ui.widgets.clay import ClayButton, muted_label, title_label


class HealDialog(QDialog):
    """Lists pending heal suggestions with checkboxes. ``accepted_ids`` / ``dismissed_ids`` after exec."""

    def __init__(self, heals: list[SelectorHeal], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.heals = heals
        self.accepted_ids: list[int] = []
        self.dismissed_ids: list[int] = []
        self.setWindowTitle("Self-healed selectors")
        self.resize(760, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(title_label("Elements FlowPilot found again", "subtitle"))
        root.addWidget(muted_label("These steps could not find their element during a run, so FlowPilot found it "
                                   "again by its text, role and position and continued. Tick the ones that are "
                                   "right to save the new selector (the old one is kept as a backup)."))
        self.list = QListWidget()
        for h in heals:
            times = f" · seen {h.times}×" if h.times > 1 else ""
            item = QListWidgetItem(f"{h.step_summary}\n   was: {h.old_selector or '—'}\n   now: {h.new_selector}"
                                   f"   (confidence {h.score:g}{times}, {h.account_name})")
            item.setData(Qt.ItemDataRole.UserRole, h.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            try:
                fallbacks = json.loads(h.payload_json or "{}").get("fallbacks", [])
            except ValueError:
                fallbacks = []
            item.setToolTip("Backups: " + ", ".join(fallbacks) if fallbacks else h.new_selector)
            self.list.addItem(item)
        root.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        dismiss = ClayButton("Dismiss all", "x", compact=True)
        dismiss.clicked.connect(self._dismiss_all)
        buttons.addWidget(dismiss)
        buttons.addStretch(1)
        later = ClayButton("Later")
        later.clicked.connect(self.reject)
        save = ClayButton("Save ticked", "check", "primary")
        save.clicked.connect(self._save)
        buttons.addWidget(later)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def _ids(self, checked: bool) -> list[int]:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == state]

    def _save(self) -> None:
        self.accepted_ids = self._ids(True)
        self.dismissed_ids = self._ids(False)
        self.accept()

    def _dismiss_all(self) -> None:
        self.accepted_ids = []
        self.dismissed_ids = self._ids(True) + self._ids(False)
        self.accept()
