"""Add / edit one account (browser profile) and its variables."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from flowpilot.storage.models import Account
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import run_async
from flowpilot.ui.widgets.clay import ClayButton, ClayToggle, muted_label, title_label

SECRET_HINTS = ("pass", "secret", "token", "pin", "otp", "key")


def is_secret_key(key: str) -> bool:
    return any(h in key.lower() for h in SECRET_HINTS)


class VariablesTable(QTableWidget):
    """Key/value editor. Values of secret-looking keys are masked until 'Show' is on."""

    def __init__(self, variables: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Name (use as {{account.name}})", "Value"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.show_secrets = False
        self._values: list[str] = []
        for k, v in variables.items():
            self.add_row(k, v)
        self.itemChanged.connect(self._on_changed)

    def add_row(self, key: str = "", value: str = "") -> None:
        self.blockSignals(True)
        r = self.rowCount()
        self.insertRow(r)
        self._values.append(value)
        self.setItem(r, 0, QTableWidgetItem(key))
        self.setItem(r, 1, QTableWidgetItem(self._display(key, value)))
        self.blockSignals(False)

    def _display(self, key: str, value: str) -> str:
        return "•" * min(12, len(value)) if is_secret_key(key) and not self.show_secrets and value else value

    def _on_changed(self, item: QTableWidgetItem) -> None:
        r = item.row()
        if item.column() == 1:
            text = item.text()
            if not (is_secret_key(self.item(r, 0).text()) and not self.show_secrets and set(text) == {"•"}):
                self._values[r] = text
        self.refresh()

    def refresh(self) -> None:
        self.blockSignals(True)
        for r in range(self.rowCount()):
            key = self.item(r, 0).text() if self.item(r, 0) else ""
            self.item(r, 1).setText(self._display(key, self._values[r]))
        self.blockSignals(False)

    def set_show(self, show: bool) -> None:
        self.show_secrets = show
        self.refresh()

    def remove_selected(self) -> None:
        for r in sorted({i.row() for i in self.selectedIndexes()}, reverse=True):
            self.removeRow(r)
            del self._values[r]

    def values(self) -> dict[str, str]:
        out = {}
        for r in range(self.rowCount()):
            key = (self.item(r, 0).text() if self.item(r, 0) else "").strip()
            if key:
                out[key.removeprefix("account.")] = self._values[r]
        return out


class AccountDialog(QDialog):
    def __init__(self, app: AppContext, account: Account | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.account = account
        self.setWindowTitle("Account")
        self.resize(640, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(title_label("Account (browser profile)", "subtitle"))
        form = QFormLayout()
        self.name = QLineEdit(account.name if account else "")
        self.conn = QComboBox()
        for c in app.repos.connections.list():
            self.conn.addItem(c.name, c.id)
        if account and account.connection_id:
            self.conn.setCurrentIndex(max(0, self.conn.findData(account.connection_id)))
        self.profile = QLineEdit(account.remote_profile_id if account else "")
        self.profile.setPlaceholderText("The profile ID in your anti-detect browser")
        pick = ClayButton("Choose…", "list", compact=True, tooltip="Load the profile list from the browser")
        pick.clicked.connect(self._choose_profile)
        prow = QHBoxLayout()
        prow.addWidget(self.profile, 1)
        prow.addWidget(pick)
        self.group = QComboBox()
        self.group.addItem("No group", None)
        for g in app.repos.groups.list():
            self.group.addItem(g.name, g.id)
        if account and account.group_id:
            self.group.setCurrentIndex(max(0, self.group.findData(account.group_id)))
        self.tags = QLineEdit(account.tags if account else "")
        self.tags.setPlaceholderText("comma separated, e.g. us, warm-up")
        self.notes = QPlainTextEdit(account.notes if account else "")
        self.notes.setMaximumHeight(70)
        self.enabled = ClayToggle(account.enabled if account else True)
        form.addRow("Name", self.name)
        form.addRow("Connection", self.conn)
        form.addRow("Profile ID", prow)
        form.addRow("Group", self.group)
        form.addRow("Tags", self.tags)
        form.addRow("Notes", self.notes)
        form.addRow("Enabled", self.enabled)
        root.addLayout(form)
        root.addWidget(title_label("Variables", "subtitle"))
        root.addWidget(muted_label("Values your flows can use, e.g. email, password, username, search_keyword. "
                                   "Passwords are stored encrypted."))
        self.vars = VariablesTable(app.repos.accounts.variables(account) if account else
                                   {"email": "", "password": ""})
        root.addWidget(self.vars, 1)
        vrow = QHBoxLayout()
        add = ClayButton("Add variable", "plus", compact=True)
        add.clicked.connect(lambda: self.vars.add_row())
        rem = ClayButton("Remove", "trash", compact=True)
        rem.clicked.connect(self.vars.remove_selected)
        show = ClayButton("Show secrets", "eye", compact=True)
        show.setCheckable(True)
        show.toggled.connect(self.vars.set_show)
        vrow.addWidget(add)
        vrow.addWidget(rem)
        vrow.addWidget(show)
        vrow.addStretch(1)
        root.addLayout(vrow)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = ClayButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = ClayButton("Save", "check", "primary")
        save.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def _choose_profile(self) -> None:
        conn = self.app.repos.connections.get(self.conn.currentData())
        if conn is None:
            return
        adapter = self.app.services.adapters.get(conn)

        def show(profiles: list[Any]) -> None:
            menu = QMenu(self)
            for p in profiles[:300]:
                act = menu.addAction(f"{p.name}  ({p.id})")
                act.triggered.connect(lambda _=False, pr=p: (self.profile.setText(pr.id),
                                                              self.name.setText(self.name.text() or pr.name)))
            if not profiles:
                menu.addAction("No profiles found").setEnabled(False)
            menu.exec(self.profile.mapToGlobal(self.profile.rect().bottomLeft()))

        run_async(adapter.list_profiles(), show, parent=self, error_title="Could not load profiles")

    def save(self) -> None:
        acc = self.account or Account()
        acc.name = self.name.text().strip() or self.profile.text().strip() or "Account"
        acc.connection_id = self.conn.currentData()
        acc.remote_profile_id = self.profile.text().strip()
        acc.group_id = self.group.currentData()
        acc.tags = ", ".join(t.strip() for t in self.tags.text().split(",") if t.strip())
        acc.notes = self.notes.toPlainText()
        acc.enabled = self.enabled.isChecked()
        self.account = self.app.repos.accounts.save(acc, variables=self.vars.values())
        self.app.bridge.data_changed.emit("accounts")
        self.accept()
