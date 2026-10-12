"""Searchable 'Add step' picker grouped by category."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from flowpilot.core.steps import CATEGORY_ORDER, StepRegistry
from flowpilot.ui.icons import has_icon, icon
from flowpilot.ui.theme.palette import pal
from flowpilot.ui.widgets.clay import ClayButton, muted_label, title_label
from flowpilot.ui.widgets.step_list import category_color

#: types hidden from the picker because they are inserted automatically with their block
AUTO_TYPES = {"else", "end_if", "end_loop", "catch", "end_try"}


class StepPicker(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add a step")
        self.resize(520, 620)
        self.chosen: str | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.addWidget(title_label("Add a step", "subtitle"))
        root.addWidget(muted_label("Type to search, then double-click a step (or press Enter)."))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: click, type, wait, loop…")
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.itemActivated.connect(self._accept_item)
        self.tree.itemDoubleClicked.connect(self._accept_item)
        root.addWidget(self.tree, 1)
        p = pal()
        cats = StepRegistry.by_category()
        for cat in CATEGORY_ORDER:
            parent_item = QTreeWidgetItem([cat])
            f = parent_item.font(0)
            f.setBold(True)
            parent_item.setFont(0, f)
            parent_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(parent_item)
            for cls in cats.get(cat, []):
                type_name = cls.model_fields["type"].default
                if type_name in AUTO_TYPES:
                    continue
                label = cls.TITLE + ("  (Advanced)" if cls.ADVANCED else "")
                it = QTreeWidgetItem([label])
                it.setIcon(0, icon(cls.ICON if has_icon(cls.ICON) else "circle", category_color(p, cat), 18))
                it.setData(0, Qt.ItemDataRole.UserRole, type_name)
                it.setToolTip(0, cls.DESCRIPTION or cls.TITLE)
                parent_item.addChild(it)
            parent_item.setExpanded(True)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = ClayButton("Cancel")
        cancel.clicked.connect(self.reject)
        add = ClayButton("Add", "plus", "primary")
        add.clicked.connect(lambda: self._accept_item(self.tree.currentItem()))
        buttons.addWidget(cancel)
        buttons.addWidget(add)
        root.addLayout(buttons)
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        text = text.lower().strip()
        first = None
        for i in range(self.tree.topLevelItemCount()):
            cat = self.tree.topLevelItem(i)
            visible = 0
            for j in range(cat.childCount()):
                child = cat.child(j)
                match = not text or text in child.text(0).lower() or text in str(child.data(0, Qt.ItemDataRole.UserRole))
                child.setHidden(not match)
                if match:
                    visible += 1
                    first = first or child
            cat.setHidden(visible == 0)
        if first is not None and text:
            self.tree.setCurrentItem(first)

    def _accept_item(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        type_name = item.data(0, Qt.ItemDataRole.UserRole)
        if type_name:
            self.chosen = str(type_name)
            self.accept()

    def keyPressEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept_item(self.tree.currentItem())
            return
        if e.key() == Qt.Key.Key_Down and self.search.hasFocus():
            self.tree.setFocus()
        super().keyPressEvent(e)
