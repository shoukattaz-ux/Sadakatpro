"""Accounts: browser profiles, groups, variables, sync and import."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor, QDropEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSplitter,
    QTableView,
)

from flowpilot.services.profile_sync import sync_profiles
from flowpilot.storage.models import Account
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import confirm, info, run_async
from flowpilot.ui.dialogs.account_dialog import AccountDialog
from flowpilot.ui.dialogs.import_wizard import ImportWizard
from flowpilot.ui.pages.base import Page
from flowpilot.ui.theme.palette import STATUS_COLORS, pal
from flowpilot.ui.widgets.clay import ClayButton, ClayCard, ClayToggle, EmptyState, HelpIcon, muted_label, title_label

MIME_ACCOUNTS = "application/x-flowpilot-accounts"
ALL, NO_GROUP = -1, -2
GROUP_COLORS = ["#7C6CF2", "#5EC8D8", "#6BCB8B", "#F5B75C", "#F27C7C", "#C08CF2", "#5E9CF2", "#F28CC0"]

COLUMNS = ["", "Name", "Connection", "Profile ID", "Group", "Tags", "Last result", "Last run", "Notes"]


def color_icon(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


class AccountsModel(QAbstractTableModel):
    """Table of accounts; column 0 is the enabled checkbox."""

    def __init__(self, app: AppContext) -> None:
        super().__init__()
        self.app = app
        self.accounts: list[Account] = []
        self.conn_names: dict[int, str] = {}
        self.group_names: dict[int, str] = {}

    def reload(self) -> None:
        self.beginResetModel()
        self.accounts = self.app.repos.accounts.list()
        self.conn_names = {c.id: c.name for c in self.app.repos.connections.list()}
        self.group_names = {g.id: g.name for g in self.app.repos.groups.list()}
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.accounts)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        a = self.accounts[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if a.enabled else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return a.id
        if role == Qt.ItemDataRole.ToolTipRole and col == 0:
            return "Enabled: included in runs"
        if role == Qt.ItemDataRole.ForegroundRole:
            if not a.enabled:
                return QColor(pal().muted)
            if col == 6 and a.last_run_status:
                return QColor(getattr(pal(), STATUS_COLORS.get(a.last_run_status, "muted")))
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return [
                "", a.name, self.conn_names.get(a.connection_id or -1, "—"), a.remote_profile_id,
                self.group_names.get(a.group_id or -1, ""), a.tags, a.last_run_status.title(),
                a.last_run_at.strftime("%Y-%m-%d %H:%M") if a.last_run_at else "", a.notes.replace("\n", " ")[:80],
            ][col]
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if index.column() == 0:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def setData(self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            a = self.accounts[index.row()]
            a.enabled = Qt.CheckState(value) == Qt.CheckState.Checked
            self.app.repos.accounts.set_enabled([a.id], a.enabled)
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
            return True
        return False

    def mimeTypes(self) -> list[str]:  # noqa: N802
        return [MIME_ACCOUNTS]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:  # noqa: N802
        ids = sorted({self.accounts[i.row()].id for i in indexes if i.isValid()})
        mime = QMimeData()
        mime.setData(MIME_ACCOUNTS, json.dumps(ids).encode())
        return mime


class AccountFilter(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.text = ""
        self.group: int = ALL
        self.status = ""
        self.tag = ""

    def filterAcceptsRow(self, row: int, parent: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        model: AccountsModel = self.sourceModel()  # type: ignore[assignment]
        a = model.accounts[row]
        if self.group == NO_GROUP and a.group_id is not None and a.group_id in model.group_names:
            return False
        if self.group >= 0 and a.group_id != self.group:
            return False
        if self.status and a.last_run_status != self.status:
            return False
        if self.tag and self.tag.lower() not in [t.lower() for t in a.tag_list]:
            return False
        if self.text:
            hay = f"{a.name} {a.remote_profile_id} {a.tags} {a.notes}".lower()
            return all(part in hay for part in self.text.lower().split())
        return True


class GroupList(QListWidget):
    """Groups; drag to reorder; drop accounts on a group to move them there."""

    def __init__(self, page: AccountsPage) -> None:
        super().__init__()
        self.page = page
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.mimeData().hasFormat(MIME_ACCOUNTS):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.mimeData().hasFormat(MIME_ACCOUNTS):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        if e.mimeData().hasFormat(MIME_ACCOUNTS):
            item = self.itemAt(e.position().toPoint())
            if item is not None:
                gid = item.data(Qt.ItemDataRole.UserRole)
                ids = json.loads(bytes(e.mimeData().data(MIME_ACCOUNTS)).decode())
                self.page.move_accounts(ids, None if gid in (ALL, NO_GROUP) else gid)
            e.acceptProposedAction()
            return
        super().dropEvent(e)
        self.page.save_group_order()


class AccountsPage(Page):
    title = "Accounts"
    subtitle = "Your browser profiles. Group them, add their variables (email, password…), and run flows on them."
    icon = "users"
    ICONS_ONLY_BELOW = 1000

    def __init__(self, app: AppContext) -> None:
        super().__init__(app)
        self.model = AccountsModel(app)
        self.proxy = AccountFilter()
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        # actions
        self.conn_box = QComboBox()
        self.conn_box.setToolTip("Connection to sync profiles from")
        sync = ClayButton("Sync profiles", "refresh", "secondary", tooltip="Load profiles from the anti-detect browser")
        sync.clicked.connect(self.sync)
        add = ClayButton("Add", "plus", "primary")
        add.clicked.connect(lambda: self.edit(None))
        imp = ClayButton("Import", "upload", tooltip="Import from CSV or Excel")
        imp.clicked.connect(self.import_file)
        for w in (self.conn_box, sync, add, imp):
            self.actions.addWidget(w)

        splitter = QSplitter()
        self.content.addWidget(splitter, 1)

        # groups panel
        gcard = ClayCard(padding=10)
        gcard.setMinimumWidth(210)
        gcard.setMaximumWidth(300)
        gcard.body.addWidget(title_label("Groups", "subtitle"))
        gcard.body.addWidget(muted_label("Drag accounts here to move them. Drag groups to change their order."))
        self.groups = GroupList(self)
        self.groups.currentItemChanged.connect(self._group_selected)
        self.groups.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.groups.customContextMenuRequested.connect(self._group_menu)
        gcard.body.addWidget(self.groups, 1)
        grow = QHBoxLayout()
        gadd = ClayButton("New group", "plus", compact=True)
        gadd.clicked.connect(self.add_group)
        grow.addWidget(gadd)
        grow.addStretch(1)
        gcard.body.addLayout(grow)
        splitter.addWidget(gcard)

        # table panel
        tcard = ClayCard(padding=10)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, profile ID, tags, notes…")
        self.search.textChanged.connect(self._apply_filter)
        self.status_box = QComboBox()
        self.status_box.addItem("Any result", "")
        for s in ("done", "failed", "stopped"):
            self.status_box.addItem(s.title(), s)
        self.status_box.currentIndexChanged.connect(self._apply_filter)
        self.tag_box = QComboBox()
        self.tag_box.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.status_box)
        filters.addWidget(self.tag_box)
        tcard.body.addLayout(filters)

        bulk = QHBoxLayout()
        self.count_label = muted_label("")
        bulk.addWidget(self.count_label)
        bulk.addStretch(1)
        skip = ClayToggle(app.settings.skip_succeeded_today)
        skip.toggled.connect(lambda v: (setattr(app.settings, "skip_succeeded_today", v), app.save_settings()))
        bulk.addWidget(muted_label("Skip accounts that already succeeded today", wrap=False))
        bulk.addWidget(HelpIcon("When on, runs leave out accounts whose last run of the same flow today was successful."))
        bulk.addWidget(skip)
        tcard.body.addLayout(bulk)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        self.table.setColumnWidth(0, 36)
        self.table.setColumnWidth(1, 180)
        self.table.doubleClicked.connect(lambda idx: self.edit(self._account_at(idx)))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        tcard.body.addWidget(self.table, 1)

        brow = QHBoxLayout()
        bulk_buttons = []
        for text, icon, slot in (("Edit / variables", "edit", self.edit_selected),
                                 ("Move to group", "folder", self._move_menu),
                                 ("Enable", "check", lambda: self._set_enabled(True)),
                                 ("Disable", "x", lambda: self._set_enabled(False)),
                                 ("Delete", "trash", self.delete)):
            b = ClayButton(text, icon, "danger" if text == "Delete" else "neutral", compact=True)
            b.clicked.connect(slot)
            brow.addWidget(b)
            bulk_buttons.append(b)
        self.shrinkable(sync, imp, *bulk_buttons)
        brow.addStretch(1)
        tcard.body.addLayout(brow)
        self.empty = EmptyState("users", "No accounts yet",
                                "Click “Sync profiles” to load your browser profiles, or add/import them.",
                                "Add account")
        assert self.empty.button is not None
        self.empty.button.clicked.connect(lambda: self.edit(None))
        tcard.body.addWidget(self.empty)
        splitter.addWidget(tcard)
        splitter.setStretchFactor(1, 1)
        self.reload()

    # ------------------------------------------------------------------ data
    def reload(self) -> None:
        self.model.reload()
        self._reload_groups()
        self._reload_connections()
        tags = sorted({t for a in self.model.accounts for t in a.tag_list}, key=str.lower)
        current = self.tag_box.currentData()
        self.tag_box.blockSignals(True)
        self.tag_box.clear()
        self.tag_box.addItem("Any tag", "")
        for t in tags:
            self.tag_box.addItem(t, t)
        self.tag_box.setCurrentIndex(max(0, self.tag_box.findData(current)))
        self.tag_box.blockSignals(False)
        self._apply_filter()

    def _reload_connections(self) -> None:
        current = self.conn_box.currentData()
        self.conn_box.clear()
        for c in self.app.repos.connections.list():
            self.conn_box.addItem(c.name, c.id)
        if current is not None:
            self.conn_box.setCurrentIndex(max(0, self.conn_box.findData(current)))

    def _reload_groups(self) -> None:
        current = self.groups.currentItem().data(Qt.ItemDataRole.UserRole) if self.groups.currentItem() else ALL
        counts: dict[int | None, int] = {}
        for a in self.model.accounts:
            counts[a.group_id] = counts.get(a.group_id, 0) + 1
        self.groups.blockSignals(True)
        self.groups.clear()
        all_item = QListWidgetItem(f"All accounts  ({len(self.model.accounts)})")
        all_item.setData(Qt.ItemDataRole.UserRole, ALL)
        all_item.setFlags(all_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        self.groups.addItem(all_item)
        known = set()
        for g in self.app.repos.groups.list():
            known.add(g.id)
            it = QListWidgetItem(color_icon(g.color), f"{g.name}  ({counts.get(g.id, 0)})")
            it.setData(Qt.ItemDataRole.UserRole, g.id)
            self.groups.addItem(it)
        loose = sum(n for gid, n in counts.items() if gid not in known)
        no_item = QListWidgetItem(f"No group  ({loose})")
        no_item.setData(Qt.ItemDataRole.UserRole, NO_GROUP)
        no_item.setFlags(no_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        self.groups.addItem(no_item)
        for i in range(self.groups.count()):
            if self.groups.item(i).data(Qt.ItemDataRole.UserRole) == current:
                self.groups.setCurrentRow(i)
                break
        else:
            self.groups.setCurrentRow(0)
        self.groups.blockSignals(False)
        self.proxy.group = current if isinstance(current, int) else ALL

    def _apply_filter(self) -> None:
        modern = hasattr(self.proxy, "beginFilterChange")  # Qt >= 6.10; invalidateFilter() is deprecated there
        if modern:
            self.proxy.beginFilterChange()
        self.proxy.text = self.search.text()
        self.proxy.status = self.status_box.currentData() or ""
        self.proxy.tag = self.tag_box.currentData() or ""
        if modern:
            self.proxy.endFilterChange()
        else:
            self.proxy.invalidateFilter()
        shown, total = self.proxy.rowCount(), len(self.model.accounts)
        self.count_label.setText(f"{shown} of {total} account(s)")
        self.empty.setVisible(total == 0)
        self.table.setVisible(total > 0)

    def _group_selected(self, item: QListWidgetItem | None, _: Any = None) -> None:
        self.proxy.group = item.data(Qt.ItemDataRole.UserRole) if item else ALL
        self._apply_filter()

    def on_data_changed(self, what: str) -> None:
        if what in ("accounts", "groups", "connections"):
            self.reload()

    def on_show(self) -> None:
        self.reload()

    # ------------------------------------------------------------------ selection helpers
    def _account_at(self, proxy_index: QModelIndex) -> Account | None:
        if not proxy_index.isValid():
            return None
        return self.model.accounts[self.proxy.mapToSource(proxy_index).row()]

    def selected_ids(self) -> list[int]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.accounts[r].id for r in sorted(rows)]

    def _first_selected(self) -> Account | None:
        ids = self.selected_ids()
        return self.app.repos.accounts.get(ids[0]) if ids else None

    # ------------------------------------------------------------------ actions
    def edit(self, account: Account | None) -> None:
        if not self.app.repos.connections.list() and account is None:
            info(self, "Add a connection first", "Go to Settings → Connections and add your anti-detect browser.")
            return
        if AccountDialog(self.app, account, self).exec():
            self.reload()

    def edit_selected(self) -> None:
        account = self._first_selected()
        if account is None:
            info(self, "Edit account", "Select an account in the list first (or double-click it).")
            return
        self.edit(account)

    def sync(self) -> None:
        conn = self.app.repos.connections.get(self.conn_box.currentData())
        if conn is None:
            info(self, "No connection", "Add a connection in Settings → Connections first.")
            return
        adapter = self.app.services.adapters.get(conn)

        def done(res: Any) -> None:
            self.reload()
            self.app.bridge.data_changed.emit("groups")
            info(self, "Profiles synced", f"{res.total} profile(s) found: {res.created} new, {res.updated} updated.")

        run_async(sync_profiles(self.app.repos, adapter, conn), done, parent=self, error_title="Sync failed")

    def import_file(self) -> None:
        if not self.app.repos.connections.list():
            info(self, "Add a connection first", "Go to Settings → Connections and add your anti-detect browser.")
            return
        ImportWizard(self.app, self).exec()
        self.reload()

    def move_accounts(self, ids: list[int], group_id: int | None) -> None:
        self.app.repos.accounts.move_to_group(ids, group_id)
        self.reload()
        self.app.bridge.data_changed.emit("groups")

    def _move_menu(self) -> None:
        ids = self.selected_ids()
        if not ids:
            info(self, "Move to group", "Select one or more accounts first.")
            return
        menu = QMenu(self)
        for g in self.app.repos.groups.list():
            menu.addAction(color_icon(g.color), g.name).triggered.connect(
                lambda _=False, gid=g.id: self.move_accounts(ids, gid))
        menu.addSeparator()
        menu.addAction("No group").triggered.connect(lambda: self.move_accounts(ids, None))
        menu.addAction("New group…").triggered.connect(lambda: self._move_new_group(ids))
        menu.exec(self.cursor().pos())

    def _move_new_group(self, ids: list[int]) -> None:
        g = self.add_group()
        if g is not None:
            self.move_accounts(ids, g.id)

    def _set_enabled(self, enabled: bool) -> None:
        ids = self.selected_ids()
        if ids:
            self.app.repos.accounts.set_enabled(ids, enabled)
            self.reload()

    def delete(self) -> None:
        ids = self.selected_ids()
        if ids and confirm(self, "Delete accounts", f"Delete {len(ids)} account(s) from FlowPilot? "
                           "(The profiles in your anti-detect browser are not touched.)", "Delete", danger=True):
            self.app.repos.accounts.delete(ids)
            self.reload()
            self.app.bridge.data_changed.emit("accounts")

    def _table_menu(self, pos: Any) -> None:
        menu = QMenu(self)
        menu.addAction("Edit / variables").triggered.connect(self.edit_selected)
        menu.addAction("Move to group…").triggered.connect(self._move_menu)
        menu.addAction("Enable").triggered.connect(lambda: self._set_enabled(True))
        menu.addAction("Disable").triggered.connect(lambda: self._set_enabled(False))
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(self.delete)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ groups
    def add_group(self) -> Any:
        name, ok = QInputDialog.getText(self, "New group", "Group name:")
        if not ok or not name.strip():
            return None
        count = len(self.app.repos.groups.list())
        g = self.app.repos.groups.create(name.strip(), GROUP_COLORS[count % len(GROUP_COLORS)])
        self.reload()
        self.app.bridge.data_changed.emit("groups")
        return g

    def save_group_order(self) -> None:
        ids = [self.groups.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.groups.count())]
        self.app.repos.groups.reorder([i for i in ids if isinstance(i, int) and i >= 0])
        self.app.bridge.data_changed.emit("groups")
        self._reload_groups()

    def _group_menu(self, pos: Any) -> None:
        item = self.groups.itemAt(pos)
        if item is None:
            return
        gid = item.data(Qt.ItemDataRole.UserRole)
        if gid in (ALL, NO_GROUP):
            return
        group = self.app.repos.groups.get(gid)
        if group is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename…").triggered.connect(lambda: self._rename_group(group))
        menu.addAction("Colour…").triggered.connect(lambda: self._color_group(group))
        menu.addSeparator()
        menu.addAction("Delete group").triggered.connect(lambda: self._delete_group(group))
        menu.exec(self.groups.viewport().mapToGlobal(pos))

    def _rename_group(self, group: Any) -> None:
        name, ok = QInputDialog.getText(self, "Rename group", "Group name:", text=group.name)
        if ok and name.strip():
            group.name = name.strip()
            self.app.repos.groups.save(group)
            self.reload()
            self.app.bridge.data_changed.emit("groups")

    def _color_group(self, group: Any) -> None:
        color = QColorDialog.getColor(QColor(group.color), self, "Group colour")
        if color.isValid():
            group.color = color.name()
            self.app.repos.groups.save(group)
            self.reload()
            self.app.bridge.data_changed.emit("groups")

    def _delete_group(self, group: Any) -> None:
        if confirm(self, "Delete group", f"Delete the group “{group.name}”? Its accounts are kept (without a group).",
                   "Delete", danger=True):
            self.app.repos.groups.delete(group.id)
            self.reload()
            self.app.bridge.data_changed.emit("groups")
