"""Runs & Logs: history, per-account timelines, screenshots, traces and exports."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
)

from flowpilot.services.reporting import export_items
from flowpilot.storage.models import Run, RunItem
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import confirm, info, show_error
from flowpilot.ui.pages.base import Page
from flowpilot.ui.theme.palette import STATUS_COLORS, pal
from flowpilot.ui.widgets.clay import ClayButton, ClayCard, EmptyState, title_label


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds or 0)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _status_item(status: str) -> QTableWidgetItem:
    it = QTableWidgetItem(status.title())
    it.setForeground(pal().c(STATUS_COLORS.get(status, "muted")))
    return it


class RunsPage(Page):
    title = "Runs & Logs"
    subtitle = "What happened in every run — step by step, with screenshots of problems."
    icon = "list"

    def __init__(self, app: AppContext) -> None:
        super().__init__(app)
        self.runs: list[Run] = []
        self.items: list[RunItem] = []
        refresh = ClayButton("Refresh", "refresh", compact=True)
        refresh.clicked.connect(self.reload)
        export = ClayButton("Export", "download", "secondary", compact=True, tooltip="Export the selected run "
                            "to CSV or Excel")
        export.clicked.connect(self.export)
        delete = ClayButton("Delete", "trash", "danger", compact=True)
        delete.clicked.connect(self.delete)
        for b in (refresh, export, delete):
            self.actions.addWidget(b)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        self.content.addWidget(vsplit, 1)

        top = ClayCard(padding=10)
        frow = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search flows, accounts or errors…")
        self.search.textChanged.connect(self._fill_runs)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All runs", "")
        for s in ("done", "failed", "stopped", "running"):
            self.status_filter.addItem(s.title(), s)
        self.status_filter.currentIndexChanged.connect(self._fill_runs)
        frow.addWidget(self.search, 1)
        frow.addWidget(self.status_filter)
        top.body.addLayout(frow)
        self.runs_table = self._table(["Date", "Flow", "Accounts", "Succeeded", "Failed", "Duration", "Status", "Source"])
        self.runs_table.itemSelectionChanged.connect(self._run_selected)
        top.body.addWidget(self.runs_table, 1)
        self.empty = EmptyState("list", "No runs yet", "Start a flow on the Dashboard. Every run is listed here.")
        top.body.addWidget(self.empty)
        vsplit.addWidget(top)

        bottom = QSplitter()
        icard = ClayCard(padding=10)
        icard.body.addWidget(title_label("Accounts in this run", "subtitle"))
        self.items_table = self._table(["Account", "Group", "Status", "What happened", "Duration", "Tries"])
        self.items_table.itemSelectionChanged.connect(self._item_selected)
        icard.body.addWidget(self.items_table, 1)
        bottom.addWidget(icard)

        dcard = ClayCard(padding=10)
        self.detail_title = title_label("Select an account", "subtitle")
        dcard.body.addWidget(self.detail_title)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setProperty("role", "error")
        dcard.body.addWidget(self.error)
        self.timeline = QTreeWidget()
        self.timeline.setHeaderLabels(["#", "Step", "Result", "Time"])
        self.timeline.setRootIsDecorated(False)
        self.timeline.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        dcard.body.addWidget(self.timeline, 1)
        self.tech_btn = ClayButton("Technical details", "code", "ghost", compact=True)
        self.tech_btn.setCheckable(True)
        self.tech = QPlainTextEdit()
        self.tech.setReadOnly(True)
        self.tech.setMaximumHeight(120)
        self.tech.hide()
        self.tech_btn.toggled.connect(self.tech.setVisible)
        dcard.body.addWidget(self.tech_btn, 0, Qt.AlignmentFlag.AlignLeft)
        dcard.body.addWidget(self.tech)
        self.shot = QLabel()
        self.shot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shot.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shot.mousePressEvent = lambda _e: self._open(self._current_item_attr("screenshot_path"))  # type: ignore[assignment]
        dcard.body.addWidget(self.shot)
        brow = QHBoxLayout()
        self.open_shot = ClayButton("Screenshot", "image", compact=True)
        self.open_shot.clicked.connect(lambda: self._open(self._current_item_attr("screenshot_path")))
        self.open_html = ClayButton("Page HTML", "file", compact=True)
        self.open_html.clicked.connect(lambda: self._open(self._current_item_attr("html_path")))
        self.open_trace = ClayButton("Trace", "history", compact=True, tooltip="Open the Playwright trace viewer")
        self.open_trace.clicked.connect(self._open_trace)
        self.open_folder = ClayButton("Folder", "folder", compact=True)
        self.open_folder.clicked.connect(self._open_folder)
        for b in (self.open_shot, self.open_html, self.open_trace, self.open_folder):
            brow.addWidget(b)
        brow.addStretch(1)
        dcard.body.addLayout(brow)
        bottom.addWidget(dcard)
        bottom.setSizes([520, 520])
        vsplit.addWidget(bottom)
        vsplit.setSizes([300, 460])
        app.bridge.run_started.connect(lambda *_: self.reload())
        app.bridge.run_finished.connect(lambda *_: self.reload())
        self.reload()

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        return t

    # ------------------------------------------------------------------ data
    def reload(self) -> None:
        self.runs = self.app.repos.runs.list()
        self._fill_runs()

    def on_show(self) -> None:
        self.reload()

    def on_data_changed(self, what: str) -> None:
        if what == "runs":
            self.reload()

    def _fill_runs(self) -> None:
        text = self.search.text().lower().strip()
        status = self.status_filter.currentData()
        selected = self._selected_run()
        rows = []
        for r in self.runs:
            if status and r.status != status:
                continue
            if text and text not in r.flow_name.lower():
                # also search account names and errors of the run
                items = self.app.repos.runs.items(r.id)
                if not any(text in (it.account_name + " " + it.error).lower() for it in items):
                    continue
            rows.append(r)
        self.runs_table.setRowCount(len(rows))
        self.runs_table.blockSignals(True)
        self.runs_table.clearSelection()
        for i, r in enumerate(rows):
            dur = ((r.finished_at - r.started_at).total_seconds() if r.finished_at and r.started_at else 0)
            values = [r.started_at.strftime("%Y-%m-%d %H:%M"), r.flow_name, str(r.total), str(r.succeeded),
                      str(r.failed), _fmt_duration(dur), None, r.source]
            for c, v in enumerate(values):
                item = _status_item(r.status) if v is None else QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, r.id)
                self.runs_table.setItem(i, c, item)
            if selected is not None and r.id == selected.id:
                self.runs_table.selectRow(i)
        self.runs_table.blockSignals(False)
        self.empty.setVisible(not self.runs)
        self.runs_table.setVisible(bool(self.runs))
        self.runs_table.blockSignals(True)
        if self.runs_table.rowCount() and not self.runs_table.selectedItems():
            self.runs_table.selectRow(0)
        self.runs_table.blockSignals(False)
        self._run_selected()  # always refresh the accounts of the selected run

    def _selected_run(self) -> Run | None:
        rows = self.runs_table.selectionModel().selectedRows() if self.runs_table.selectionModel() else []
        if not rows:
            return None
        run_id = self.runs_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((r for r in self.runs if r.id == run_id), None)

    def _run_selected(self) -> None:
        run = self._selected_run()
        self.items = self.app.repos.runs.items(run.id) if run else []
        text = self.search.text().lower().strip()
        shown = [it for it in self.items if not text or text in (it.account_name + " " + it.error).lower()
                 or (run is not None and text in run.flow_name.lower())]
        self.items_table.setRowCount(len(shown))
        for i, it in enumerate(shown):
            values = [it.account_name, it.group_name, None, it.error or ("OK" if it.status == "done" else ""),
                      _fmt_duration(it.duration_s), str(it.attempts)]
            for c, v in enumerate(values):
                cell = _status_item(it.status) if v is None else QTableWidgetItem(v)
                cell.setData(Qt.ItemDataRole.UserRole, it.id)
                if c == 3 and it.error:
                    cell.setToolTip(it.error)
                self.items_table.setItem(i, c, cell)
        if shown:
            self.items_table.selectRow(0)
        else:
            self._show_item(None)

    def _current_item(self) -> RunItem | None:
        rows = self.items_table.selectionModel().selectedRows() if self.items_table.selectionModel() else []
        if not rows:
            return None
        item_id = self.items_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((it for it in self.items if it.id == item_id), None)

    def _current_item_attr(self, attr: str) -> str:
        it = self._current_item()
        return getattr(it, attr, "") if it else ""

    def _item_selected(self) -> None:
        self._show_item(self._current_item())

    def _show_item(self, it: RunItem | None) -> None:
        self.timeline.clear()
        self.shot.clear()
        self.shot.setFixedHeight(0)
        if it is None:
            self.detail_title.setText("Select an account")
            self.error.setText("")
            self.tech.setPlainText("")
            for b in (self.open_shot, self.open_html, self.open_trace):
                b.setEnabled(False)
            return
        self.detail_title.setText(f"{it.account_name} — {it.status.title()}")
        self.error.setText(it.error)
        self.error.setVisible(bool(it.error))
        try:
            timeline = json.loads(it.timeline_json or "[]")
        except ValueError:
            timeline = []
        p = pal()
        for ev in timeline:
            row = QTreeWidgetItem([str(ev.get("index", "")), ev.get("summary") or ev.get("title", ""),
                                   ev.get("status", ""), f"{ev.get('duration', 0)} s"])
            row.setForeground(2, p.c(STATUS_COLORS.get(ev.get("status", ""), "muted")))
            if ev.get("error"):
                row.setToolTip(1, ev["error"])
                child_text = ev["error"]
                row.setText(1, f"{row.text(1)}  —  {child_text}")
            self.timeline.addTopLevelItem(row)
        try:
            results = json.loads(it.results_json or "{}")
        except ValueError:
            results = {}
        details = it.error_detail or ""
        if results:
            details += ("\n\n" if details else "") + "Saved values:\n" + json.dumps(results, indent=2, ensure_ascii=False)
        self.tech.setPlainText(details or "No technical details.")
        if it.screenshot_path and Path(it.screenshot_path).exists():
            pm = QPixmap(it.screenshot_path)
            if not pm.isNull():
                scaled = pm.scaledToHeight(200, Qt.TransformationMode.SmoothTransformation)
                self.shot.setFixedHeight(scaled.height())
                self.shot.setPixmap(scaled)
        self.open_shot.setEnabled(bool(it.screenshot_path))
        self.open_html.setEnabled(bool(it.html_path))
        self.open_trace.setEnabled(bool(it.trace_path))

    # ------------------------------------------------------------------ actions
    def _open(self, path: str) -> None:
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif path:
            info(self, "File not found", "The file was deleted (maybe by the clean-up).")

    def _open_folder(self) -> None:
        path = self._current_item_attr("screenshot_path")
        folder = Path(path).parent if path else self.app.services.paths.screenshots
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _open_trace(self) -> None:
        path = self._current_item_attr("trace_path")
        if not path or not Path(path).exists():
            info(self, "No trace", "Turn on 'Record a Playwright trace' in Settings → Defaults to get traces.")
            return
        try:
            subprocess.Popen([sys.executable, "-m", "playwright", "show-trace", path])
        except OSError as exc:
            show_error(self, "Could not open the trace viewer", exc)

    def export(self) -> None:
        run = self._selected_run()
        if run is None:
            info(self, "Export", "Select a run first.")
            return
        default = str(self.app.services.paths.exports / f"run_{run.id}_{run.flow_name}.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "Export results", default, "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        try:
            export_items(self.app.repos.runs.items(run.id), Path(path))
        except Exception as exc:  # noqa: BLE001
            show_error(self, "Export failed", exc)
            return
        info(self, "Exported", f"Saved to:\n{path}")

    def delete(self) -> None:
        run = self._selected_run()
        if run is None:
            return
        if run.id in self.app.services.engine.runs:
            info(self, "Run in progress", "Stop the run before deleting it.")
            return
        if confirm(self, "Delete run", f"Delete run #{run.id} ({run.flow_name}) from the history?", "Delete",
                   danger=True):
            self.app.repos.runs.delete([run.id])
            self.reload()
