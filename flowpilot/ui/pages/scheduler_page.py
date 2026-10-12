"""Scheduler: run flows automatically at set times."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QDateTime, Qt, QTime
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from flowpilot.core.engine import RunOptions, TargetSpec
from flowpilot.services.scheduler import KINDS, describe_spec, parse_spec
from flowpilot.storage.models import Schedule
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import confirm, info, run_async
from flowpilot.ui.dialogs.simple_dialogs import AccountChooser, ListDialog
from flowpilot.ui.pages.base import Page
from flowpilot.ui.widgets.clay import ClayButton, ClayCard, ClaySpinBox, ClayToggle, EmptyState, muted_label, title_label


class ScheduleDialog(QDialog):
    def __init__(self, app: AppContext, schedule: Schedule | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.schedule = schedule
        spec = parse_spec(schedule) if schedule else {"kind": "daily", "at": "09:00"}
        target = TargetSpec.from_dict(json.loads(schedule.target_json or "{}")) if schedule else TargetSpec()
        opts = RunOptions.from_dict(spec.get("options") or {})
        self.account_ids = list(target.account_ids)
        self.setWindowTitle("Schedule")
        self.resize(560, 600)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(title_label("Schedule a flow", "subtitle"))
        root.addWidget(muted_label("FlowPilot must be running (it can stay in the tray) for schedules to start."))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.name = QLineEdit(schedule.name if schedule else "")
        self.name.setPlaceholderText("e.g. Morning check")
        self.flow = QComboBox()
        for f in app.repos.flows.list():
            self.flow.addItem(f.name, f.id)
        if schedule:
            self.flow.setCurrentIndex(max(0, self.flow.findData(schedule.flow_id)))
        self.kind = QComboBox()
        for k, label in KINDS.items():
            self.kind.addItem(label, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(spec.get("kind", "daily"))))
        self.when = QDateTimeEdit()
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        at_once = spec.get("at") if spec.get("kind") == "once" else None
        self.when.setDateTime(QDateTime.fromString(at_once, "yyyy-MM-ddTHH:mm") if at_once else
                              QDateTime.currentDateTime().addSecs(3600))
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        at = spec.get("at") if spec.get("kind") in ("daily", "weekdays") else "09:00"
        self.time.setTime(QTime.fromString(at or "09:00", "HH:mm"))
        self.hours = ClaySpinBox(float(spec.get("hours", 4)), 0.5, 168, 0.5, 1, " h")
        self.jitter = ClaySpinBox(float(spec.get("jitter_minutes", 0)), 0, 720, 5, 0, " min")
        self.target = QComboBox()
        self.target.addItem("All accounts", "all")
        self.target.addItem("Selected groups", "groups")
        self.target.addItem("Selected accounts", "accounts")
        self.target.setCurrentIndex(max(0, self.target.findData(target.mode)))
        self.group_ids = list(target.group_ids)
        self.group_btn = ClayButton("Choose groups…", "folder", compact=True)
        self.group_btn.clicked.connect(self._choose_groups)
        self.acc_btn = ClayButton("Choose accounts…", "users", compact=True)
        self.acc_btn.clicked.connect(self._choose_accounts)
        trow = QHBoxLayout()
        trow.addWidget(self.target)
        trow.addWidget(self.group_btn)
        trow.addWidget(self.acc_btn)
        self.concurrency = ClaySpinBox(opts.concurrency, 1, 100)
        self.launch_min = ClaySpinBox(opts.launch_delay_min, 0, 3600, 1, 0, " s")
        self.launch_max = ClaySpinBox(opts.launch_delay_max, 0, 3600, 1, 0, " s")
        drow = QHBoxLayout()
        drow.addWidget(self.launch_min)
        drow.addWidget(muted_label("to", wrap=False))
        drow.addWidget(self.launch_max)
        self.enabled = ClayToggle(schedule.enabled if schedule else True)
        form.addRow("Name", self.name)
        form.addRow("Flow", self.flow)
        form.addRow("Repeat", self.kind)
        form.addRow("Date and time", self.when)
        form.addRow("Time of day", self.time)
        form.addRow("Every", self.hours)
        form.addRow("Random start delay (up to)", self.jitter)
        form.addRow("Run on", trow)
        form.addRow("Browsers at once", self.concurrency)
        form.addRow("Delay between launches", drow)
        form.addRow("Enabled", self.enabled)
        self.form = form
        root.addLayout(form)
        root.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = ClayButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = ClayButton("Save", "check", "primary")
        save.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)
        self.kind.currentIndexChanged.connect(self._update_visibility)
        self.target.currentIndexChanged.connect(self._update_visibility)
        self._update_visibility()

    def _row_visible(self, widget: QWidget, visible: bool) -> None:
        self.form.setRowVisible(widget, visible)

    def _update_visibility(self) -> None:
        k = self.kind.currentData()
        self._row_visible(self.when, k == "once")
        self._row_visible(self.time, k in ("daily", "weekdays"))
        self._row_visible(self.hours, k == "interval")
        self.group_btn.setVisible(self.target.currentData() == "groups")
        self.acc_btn.setVisible(self.target.currentData() == "accounts")

    def _choose_accounts(self) -> None:
        dlg = AccountChooser(self.app.repos.accounts.list(only_enabled=True), self.account_ids, self)
        if dlg.exec():
            self.account_ids = dlg.chosen()

    def _choose_groups(self) -> None:
        dlg = ListDialog("Choose groups", "Groups run in the order of the Accounts page.", self)
        for g in self.app.repos.groups.list():
            it = QListWidgetItem(g.name)
            it.setData(Qt.ItemDataRole.UserRole, g.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if g.id in self.group_ids else Qt.CheckState.Unchecked)
            dlg.list.addItem(it)
        if dlg.exec():
            self.group_ids = [dlg.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg.list.count())
                              if dlg.list.item(i).checkState() == Qt.CheckState.Checked]

    def save(self) -> None:
        if self.flow.currentData() is None:
            info(self, "No flow", "Create a flow first.")
            return
        k = self.kind.currentData()
        spec: dict[str, Any] = {"kind": k, "jitter_minutes": self.jitter.int_value()}
        if k == "once":
            when = self.when.dateTime().toPython()
            if when < datetime.now() - timedelta(minutes=1):
                info(self, "Time in the past", "Choose a date and time in the future.")
                return
            spec["at"] = when.strftime("%Y-%m-%dT%H:%M")
        elif k in ("daily", "weekdays"):
            spec["at"] = self.time.time().toString("HH:mm")
        else:
            spec["hours"] = self.hours.value()
        lo, hi = sorted((self.launch_min.value(), self.launch_max.value()))
        spec["options"] = asdict(RunOptions(concurrency=self.concurrency.int_value(), launch_delay_min=lo,
                                            launch_delay_max=hi))
        target = TargetSpec(mode=self.target.currentData(), group_ids=self.group_ids, account_ids=self.account_ids)
        sched = self.schedule or Schedule()
        sched.name = self.name.text().strip() or self.flow.currentText()
        sched.flow_id = self.flow.currentData()
        sched.cron_or_interval = json.dumps(spec)
        sched.target_json = json.dumps(asdict(target))
        sched.enabled = self.enabled.isChecked()
        self.schedule = self.app.repos.schedules.save(sched)
        self.accept()


class SchedulerPage(Page):
    title = "Scheduler"
    subtitle = "Run flows automatically — once, every day, on weekdays or every few hours."
    icon = "calendar"

    def __init__(self, app: AppContext) -> None:
        super().__init__(app)
        add = ClayButton("New schedule", "plus", "primary")
        add.clicked.connect(lambda: self.edit(None))
        self.actions.addWidget(add)
        card = ClayCard(padding=10)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["On", "Name", "Flow", "When", "Next run", "Last result", ""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda idx: self.edit(self._schedule_at(idx.row())))
        card.body.addWidget(self.table, 1)
        self.empty = EmptyState("calendar", "No schedules", "Create a schedule to run a flow automatically.",
                                "New schedule")
        assert self.empty.button is not None
        self.empty.button.clicked.connect(lambda: self.edit(None))
        card.body.addWidget(self.empty)
        self.content.addWidget(card, 1)
        self.schedules: list[Schedule] = []
        app.bridge.run_finished.connect(lambda *_: self.reload())
        self.reload()

    def reload(self) -> None:
        self.schedules = self.app.repos.schedules.list()
        flows = {f.id: f.name for f in self.app.repos.flows.list()}
        self.table.setRowCount(len(self.schedules))
        for i, s in enumerate(self.schedules):
            toggle = ClayToggle(s.enabled)
            toggle.toggled.connect(lambda on, sid=s.id: self._set_enabled(sid, on))
            self.table.setCellWidget(i, 0, toggle)
            nxt = self.app.services.scheduler.next_run(s.id) or s.next_run
            for c, v in enumerate([s.name, flows.get(s.flow_id, "(deleted)"), describe_spec(parse_spec(s)),
                                   nxt.strftime("%Y-%m-%d %H:%M") if nxt and s.enabled else "—",
                                   s.last_result or "—"], start=1):
                self.table.setItem(i, c, QTableWidgetItem(v))
            btns = QWidget()
            btns.setObjectName("Transparent")
            lay = QHBoxLayout(btns)
            lay.setContentsMargins(0, 0, 0, 0)
            run = ClayButton("Run now", "play", "secondary", compact=True)
            run.clicked.connect(lambda _=False, sid=s.id: self.run_now(sid))
            edit = ClayButton("", "edit", compact=True, tooltip="Edit")
            edit.clicked.connect(lambda _=False, sc=s: self.edit(sc))
            delete = ClayButton("", "trash", "danger", compact=True, tooltip="Delete")
            delete.clicked.connect(lambda _=False, sc=s: self.delete(sc))
            for b in (run, edit, delete):
                lay.addWidget(b)
            self.table.setCellWidget(i, 6, btns)
        self.empty.setVisible(not self.schedules)
        self.table.setVisible(bool(self.schedules))

    def _schedule_at(self, row: int) -> Schedule | None:
        return self.schedules[row] if 0 <= row < len(self.schedules) else None

    def _set_enabled(self, schedule_id: int, on: bool) -> None:
        s = self.app.repos.schedules.get(schedule_id)
        if s is not None:
            s.enabled = on
            self.app.repos.schedules.save(s)
            self.app.services.scheduler.reload()
            self.reload()

    def edit(self, schedule: Schedule | None) -> None:
        if not self.app.repos.flows.list():
            info(self, "No flows", "Create a flow first on the Flows page.")
            return
        if ScheduleDialog(self.app, schedule, self).exec():
            self.app.services.scheduler.reload()
            self.reload()

    def delete(self, schedule: Schedule) -> None:
        if confirm(self, "Delete schedule", f"Delete “{schedule.name}”?", "Delete", danger=True):
            self.app.repos.schedules.delete(schedule.id)
            self.app.services.scheduler.reload()
            self.reload()

    def run_now(self, schedule_id: int) -> None:
        run_async(self.app.services.scheduler.run_now(schedule_id), lambda _: self.reload(), parent=self)

    def on_show(self) -> None:
        self.reload()

    def on_data_changed(self, what: str) -> None:
        if what in ("schedules", "flows"):
            self.app.services.scheduler.reload()
            self.reload()
