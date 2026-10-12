"""Dashboard: choose a flow and accounts, set concurrency and delays, start, watch live."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from flowpilot.core.engine import EngineEvent, RunOptions, TargetSpec
from flowpilot.services.licensing import check_license
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import confirm, info, run_async
from flowpilot.ui.dialogs.simple_dialogs import AccountChooser
from flowpilot.ui.pages.accounts_page import color_icon
from flowpilot.ui.pages.base import Page
from flowpilot.ui.widgets.clay import (
    ClayButton,
    ClayCard,
    ClaySpinBox,
    ClayToggle,
    EmptyState,
    FlowLayout,
    HelpIcon,
    muted_label,
    title_label,
)
from flowpilot.ui.widgets.run_card import RunCard


class _Stat(QWidget):
    def __init__(self, label: str, color_role: str = "") -> None:
        super().__init__()
        self.setObjectName("Transparent")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(0)
        self.value = QLabel("0")
        f = self.value.font()
        f.setPointSizeF(f.pointSizeF() + 8)
        f.setBold(True)
        self.value.setFont(f)
        if color_role:
            self.value.setProperty("role", color_role)
        lay.addWidget(self.value)
        lay.addWidget(muted_label(label, wrap=False))

    def set(self, text: str) -> None:
        self.value.setText(text)


class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Run a flow on many browser profiles — safely, one group after another."
    icon = "dashboard"

    def __init__(self, app: AppContext) -> None:
        super().__init__(app)
        self.cards: dict[int, RunCard] = {}
        self.active_runs: dict[int, dict[str, Any]] = {}
        self.target_accounts: list[int] = []
        s = app.settings
        opts = RunOptions.from_dict(s.last_run_options or {})
        target = TargetSpec.from_dict(s.last_target or {})
        self.target_accounts = list(target.account_ids)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("Transparent")
        page = QVBoxLayout(host)
        page.setContentsMargins(0, 0, 4, 0)
        scroll.setWidget(host)
        self.content.addWidget(scroll, 1)

        # ---------------------------------------------------------------- setup card
        setup = ClayCard()
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        setup.body.addLayout(grid)

        grid.addWidget(self._label("Flow", "Which automation to run."), 0, 0)
        self.flow_box = QComboBox()
        self.flow_box.setMinimumWidth(220)
        grid.addWidget(self.flow_box, 0, 1)

        grid.addWidget(self._label("Run on", "All enabled accounts, only some groups, or hand-picked accounts."), 1, 0)
        trow = QHBoxLayout()
        self.target_box = QComboBox()
        self.target_box.addItem("All accounts", "all")
        self.target_box.addItem("Selected groups", "groups")
        self.target_box.addItem("Selected accounts", "accounts")
        self.target_box.setCurrentIndex(max(0, self.target_box.findData(target.mode)))
        self.choose_btn = ClayButton("Choose accounts…", "users", compact=True)
        self.choose_btn.clicked.connect(self._choose_accounts)
        self.target_info = muted_label("", wrap=False)
        trow.addWidget(self.target_box)
        trow.addWidget(self.choose_btn)
        trow.addWidget(self.target_info)
        trow.addStretch(1)
        grid.addLayout(trow, 1, 1)

        grid.addWidget(self._label("Browsers at once", "How many profiles run at the same time."), 2, 0)
        self.concurrency = ClaySpinBox(opts.concurrency, 1, 100, 1, 0)
        grid.addWidget(self.concurrency, 2, 1)

        grid.addWidget(self._label("Delay between browser launches",
                                   "After one browser starts, wait a random time in this range before starting "
                                   "the next one."), 3, 0)
        drow = QHBoxLayout()
        self.launch_min = ClaySpinBox(opts.launch_delay_min, 0, 3600, 1, 0, " s")
        self.launch_max = ClaySpinBox(opts.launch_delay_max, 0, 3600, 1, 0, " s")
        drow.addWidget(self.launch_min)
        drow.addWidget(QLabel("to"))
        drow.addWidget(self.launch_max)
        drow.addStretch(1)
        grid.addLayout(drow, 3, 1)

        grid.addWidget(self._label("Delay between groups", "When a group is finished, wait this long before "
                                   "the next group starts."), 4, 0)
        grow = QHBoxLayout()
        self.group_unit = QComboBox()
        self.group_unit.addItem("seconds", 1)
        self.group_unit.addItem("minutes", 60)
        unit = 60 if opts.group_delay_max >= 120 and opts.group_delay_max % 60 == 0 else 1
        self.group_unit.setCurrentIndex(1 if unit == 60 else 0)
        self.group_min = ClaySpinBox(opts.group_delay_min / unit, 0, 100000, 1, 0)
        self.group_max = ClaySpinBox(opts.group_delay_max / unit, 0, 100000, 1, 0)
        grow.addWidget(self.group_min)
        grow.addWidget(QLabel("to"))
        grow.addWidget(self.group_max)
        grow.addWidget(self.group_unit)
        grow.addStretch(1)
        grid.addLayout(grow, 4, 1)

        grid.addWidget(self._label("Test run", "Runs only the first account, slowly, and highlights each element "
                                   "it uses. Great before a big batch."), 5, 0)
        mrow = QHBoxLayout()
        self.test_mode = ClayToggle(opts.test_mode)
        mrow.addWidget(self.test_mode)
        self.mode_label = muted_label("", wrap=False)
        mrow.addWidget(self.mode_label)
        mrow.addStretch(1)
        self.test_mode.toggled.connect(self._update_mode_label)
        grid.addLayout(mrow, 5, 1)

        # group order
        gcol = QVBoxLayout()
        gcol.addWidget(self._label("Group order", "Drag to choose which group runs first. Group 2 starts only "
                                   "after group 1 has finished. Tick groups for 'Selected groups'."))
        self.group_list = QListWidget()
        self.group_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.group_list.setMaximumHeight(200)
        self.group_list.setMinimumWidth(220)
        gcol.addWidget(self.group_list)
        grid.addLayout(gcol, 0, 2, 6, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        buttons = QHBoxLayout()
        self.start_btn = ClayButton("Start", "play", "primary")
        self.start_btn.clicked.connect(self.start)
        self.pause_btn = ClayButton("Pause all", "pause")
        self.pause_btn.clicked.connect(self.app.services.engine.pause_all)
        self.resume_btn = ClayButton("Resume", "play", "success")
        self.resume_btn.clicked.connect(self.app.services.engine.resume_all)
        self.stop_btn = ClayButton("Stop all", "stop", "warning", tooltip="Finish the current step, then close "
                                   "every browser cleanly")
        self.stop_btn.clicked.connect(self.stop_all)
        self.force_btn = ClayButton("Force stop", "x-circle", "danger", tooltip="Stop immediately")
        self.force_btn.clicked.connect(lambda: self.stop_all(force=True))
        for b in (self.start_btn, self.pause_btn, self.resume_btn, self.stop_btn, self.force_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        clear = ClayButton("Clear finished", "trash", "ghost", compact=True)
        clear.clicked.connect(self.clear_finished)
        buttons.addWidget(clear)
        setup.body.addLayout(buttons)
        page.addWidget(setup)

        # ---------------------------------------------------------------- summary
        summary = ClayCard(padding=8)
        srow = QHBoxLayout()
        self.stat_total = _Stat("Total")
        self.stat_running = _Stat("Running")
        self.stat_queued = _Stat("Queued")
        self.stat_ok = _Stat("Succeeded", "success")
        self.stat_failed = _Stat("Failed", "error")
        self.stat_eta = _Stat("Time left")
        for st in (self.stat_total, self.stat_running, self.stat_queued, self.stat_ok, self.stat_failed, self.stat_eta):
            srow.addWidget(st)
        srow.addStretch(1)
        self.status_text = muted_label("")
        srow.addWidget(self.status_text, 2)
        summary.body.addLayout(srow)
        page.addWidget(summary)

        # ---------------------------------------------------------------- cards
        self.cards_host = QWidget()
        self.cards_host.setObjectName("Transparent")
        self.cards_layout = FlowLayout(self.cards_host, spacing=4)
        page.addWidget(self.cards_host)
        self.empty = EmptyState("zap", "Nothing running", "Choose a flow and press Start. Each browser will "
                                "appear here with its progress.")
        page.addWidget(self.empty)
        page.addStretch(1)

        # signals
        b = app.bridge
        b.run_started.connect(self._on_run_started)
        b.item_status.connect(self._on_item_status)
        b.item_step.connect(self._on_item_step)
        b.item_screenshot.connect(self._on_screenshot)
        b.item_log.connect(self._on_log)
        b.run_finished.connect(self._on_run_finished)
        b.warning.connect(lambda e: self.status_text.setText("⚠ " + e.data.get("message", "")))
        b.any_event.connect(self._on_any)
        self.target_box.currentIndexChanged.connect(self._target_changed)
        self.group_list.itemChanged.connect(lambda *_: self._update_target_info())
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.reload()
        self._target_changed()
        self._update_mode_label()
        self._update_summary()

    @staticmethod
    def _label(text: str, help_text: str) -> QWidget:
        w = QWidget()
        w.setObjectName("Transparent")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(text)
        f = lab.font()
        f.setWeight(f.Weight.DemiBold)
        lab.setFont(f)
        lay.addWidget(lab)
        lay.addWidget(HelpIcon(help_text))
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------------ data
    def reload(self) -> None:
        s = self.app.settings
        current = self.flow_box.currentData() or s.last_flow_id
        self.flow_box.blockSignals(True)
        self.flow_box.clear()
        for f in self.app.repos.flows.list():
            self.flow_box.addItem(f.name, f.id)
        if current is not None:
            self.flow_box.setCurrentIndex(max(0, self.flow_box.findData(current)))
        self.flow_box.blockSignals(False)
        target = TargetSpec.from_dict(s.last_target or {})
        checked = {self.group_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.group_list.count())
                   if self.group_list.item(i).checkState() == Qt.CheckState.Checked} or set(target.group_ids)
        order = [self.group_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.group_list.count())] \
            or target.group_order
        groups = self.app.repos.groups.list()
        pos = {gid: i for i, gid in enumerate(order)}
        groups.sort(key=lambda g: (pos.get(g.id, 10_000 + g.order), g.id))
        counts: dict[int | None, int] = {}
        for a in self.app.repos.accounts.list(only_enabled=True):
            counts[a.group_id] = counts.get(a.group_id, 0) + 1
        self.group_list.blockSignals(True)
        self.group_list.clear()
        for g in groups:
            it = QListWidgetItem(color_icon(g.color), f"{g.name}  ({counts.get(g.id, 0)})")
            it.setData(Qt.ItemDataRole.UserRole, g.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            it.setCheckState(Qt.CheckState.Checked if (g.id in checked or not checked) else Qt.CheckState.Unchecked)
            self.group_list.addItem(it)
        self.group_list.blockSignals(False)
        self._update_target_info()

    def on_data_changed(self, what: str) -> None:
        if what in ("flows", "groups", "accounts"):
            self.reload()

    def on_show(self) -> None:
        self.reload()

    def _target_changed(self) -> None:
        mode = self.target_box.currentData()
        self.choose_btn.setVisible(mode == "accounts")
        self._update_target_info()

    def _update_target_info(self) -> None:
        mode = self.target_box.currentData()
        if mode == "accounts":
            self.target_info.setText(f"{len(self.target_accounts)} account(s) chosen")
        elif mode == "groups":
            n = sum(1 for i in range(self.group_list.count())
                    if self.group_list.item(i).checkState() == Qt.CheckState.Checked)
            self.target_info.setText(f"{n} group(s) ticked")
        else:
            n = len(self.app.repos.accounts.list(only_enabled=True))
            self.target_info.setText(f"{n} enabled account(s)")

    def _update_mode_label(self) -> None:
        self.mode_label.setText("Test run: first account only, slow, highlights elements" if self.test_mode.isChecked()
                                else "Normal run")

    def _choose_accounts(self) -> None:
        groups = {g.id: g.name for g in self.app.repos.groups.list()}
        dlg = AccountChooser(self.app.repos.accounts.list(only_enabled=True), self.target_accounts, self, groups)
        if dlg.exec():
            self.target_accounts = dlg.chosen()
            self._update_target_info()

    # ------------------------------------------------------------------ run control
    def _options(self) -> tuple[TargetSpec, RunOptions]:
        unit = self.group_unit.currentData()
        order = [self.group_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.group_list.count())]
        ticked = [self.group_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.group_list.count())
                  if self.group_list.item(i).checkState() == Qt.CheckState.Checked]
        target = TargetSpec(mode=self.target_box.currentData(), group_ids=ticked, account_ids=self.target_accounts,
                            group_order=order)
        lo, hi = sorted((self.launch_min.value(), self.launch_max.value()))
        glo, ghi = sorted((self.group_min.value() * unit, self.group_max.value() * unit))
        opts = RunOptions(concurrency=self.concurrency.int_value(), launch_delay_min=lo, launch_delay_max=hi,
                          group_delay_min=glo, group_delay_max=ghi, test_mode=self.test_mode.isChecked())
        return target, opts

    def start(self) -> None:
        flow = self.app.repos.flows.get(self.flow_box.currentData())
        if flow is None:
            info(self, "Choose a flow", "Create a flow on the Flows page first.")
            return
        target, opts = self._options()
        lic = check_license()
        if lic.max_concurrent_browsers and opts.concurrency > lic.max_concurrent_browsers:
            info(self, "License limit", f"Your plan allows {lic.max_concurrent_browsers} browsers at once.")
            return
        if opts.concurrency >= 10 and not opts.test_mode and not confirm(
                self, "Many browsers", f"Run {opts.concurrency} browsers at the same time? Make sure your PC "
                "has enough memory.", "Start"):
            return
        s = self.app.settings
        s.last_flow_id = flow.id
        s.last_run_options = asdict(opts)
        s.last_target = asdict(target)
        self.app.save_settings()
        if self.app.debug.is_open and not confirm(self, "Test browser is open", "The test browser from the Flows page "
                                                  "is still open. Close it first?", "Close and start"):
            return

        async def go() -> int:
            if self.app.debug.is_open:
                await self.app.debug.close()
            return await self.app.services.engine.start(flow, target, opts)

        run_async(go(), lambda run_id: self.status_text.setText(f"Run #{run_id} started."), parent=self,
                  error_title="Could not start")

    def stop_all(self, force: bool = False) -> None:
        if not self.app.services.engine.is_busy():
            return
        text = ("Stop every browser immediately? Steps are interrupted." if force else
                "Stop all runs? Each browser finishes its current step and is then closed.")
        if confirm(self, "Force stop" if force else "Stop all", text, "Stop", danger=True):
            self.app.services.engine.stop_all(force=force)
            self.status_text.setText("Stopping…")

    def clear_finished(self) -> None:
        for item_id in [i for i, c in self.cards.items() if c.status in ("done", "failed", "stopped", "skipped")]:
            card = self.cards.pop(item_id)
            self.cards_layout.removeWidget(card)
            card.deleteLater()
        self._update_summary()

    # ------------------------------------------------------------------ engine events
    def _on_any(self, e: EngineEvent) -> None:
        if e.kind == "group_waiting":
            self.status_text.setText(f"Waiting {e.data.get('seconds')}s before group “{e.data.get('group')}”…")
        elif e.kind == "group_started":
            self.status_text.setText(f"Group “{e.data.get('group')}” started.")
        elif e.kind == "run_paused":
            self.status_text.setText("Paused. Press Resume to continue.")
        elif e.kind == "run_resumed":
            self.status_text.setText("Running.")

    def _on_run_started(self, e: EngineEvent) -> None:
        total_steps = int(e.data.get("steps", 1))
        self.active_runs[e.run_id] = {"concurrency": int(e.data.get("concurrency", 1)), "total_steps": total_steps}
        for it in e.data.get("items", []):
            card = RunCard(it["item_id"], it["account"], it["group"], total_steps)
            card.pause_clicked.connect(self.app.services.engine.pause_item)
            card.resume_clicked.connect(self.app.services.engine.resume_item)
            card.stop_clicked.connect(self.app.services.engine.stop_item)
            card.logs_clicked.connect(self._show_logs)
            card.front_clicked.connect(lambda item_id: run_async(self.app.services.engine.bring_to_front(item_id)))
            self.cards[it["item_id"]] = card
            self.cards_layout.addWidget(card)
        self.status_text.setText(f"Run #{e.run_id}: {e.data.get('flow_name')} on {e.data.get('total')} account(s).")
        self._update_summary()

    def _on_item_status(self, e: EngineEvent) -> None:
        card = self.cards.get(e.item_id or -1)
        if card is not None:
            card.set_status(e.data.get("status", ""), e.data.get("message", ""), e.data.get("manual", ""))
        self._update_summary()

    def _on_item_step(self, e: EngineEvent) -> None:
        card = self.cards.get(e.item_id or -1)
        if card is not None:
            card.set_step(e.data.get("index", 0), e.data.get("total", 1), e.data.get("title", ""))

    def _on_screenshot(self, e: EngineEvent) -> None:
        card = self.cards.get(e.item_id or -1)
        if card is not None:
            card.set_thumbnail(e.data.get("path", ""))

    def _on_log(self, e: EngineEvent) -> None:
        card = self.cards.get(e.item_id or -1)
        if card is not None:
            card.add_log(f"{e.data.get('time', '')}  {e.data.get('level', '').upper():7} {e.data.get('message', '')}")

    def _on_run_finished(self, e: EngineEvent) -> None:
        self.active_runs.pop(e.run_id, None)
        d = e.data
        self.status_text.setText(f"Run #{e.run_id} {d.get('status')}: {d.get('succeeded')}/{d.get('total')} "
                                 f"succeeded, {d.get('failed')} failed.")
        self._update_summary()

    def _show_logs(self, item_id: int) -> None:
        card = self.cards.get(item_id)
        if card is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Log — {card.name.text()}")
        dlg.resize(640, 420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(title_label(card.name.text(), "subtitle"))
        text = QPlainTextEdit("\n".join(card.logs) or "No log lines yet.")
        text.setReadOnly(True)
        lay.addWidget(text)
        if card.thumb_path:
            open_btn = ClayButton("Open screenshot", "image", compact=True)
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(card.thumb_path)))
            lay.addWidget(open_btn)
        dlg.exec()

    # ------------------------------------------------------------------ summary
    def _tick(self) -> None:
        for c in self.cards.values():
            if c.status in ("starting", "running", "waiting"):
                c.tick()
        self._update_summary()

    def _update_summary(self) -> None:
        cards = list(self.cards.values())
        counts: dict[str, int] = {}
        for c in cards:
            counts[c.status] = counts.get(c.status, 0) + 1
        running = counts.get("running", 0) + counts.get("starting", 0) + counts.get("waiting", 0)
        queued = counts.get("queued", 0)
        done = counts.get("done", 0)
        failed = counts.get("failed", 0)
        self.stat_total.set(str(len(cards)))
        self.stat_running.set(str(running))
        self.stat_queued.set(str(queued))
        self.stat_ok.set(str(done))
        self.stat_failed.set(str(failed))
        finished = [c.duration() for c in cards if c.status in ("done", "failed") and c.duration() > 0]
        remaining = running + queued
        if finished and remaining:
            conc = max(1, max((r["concurrency"] for r in self.active_runs.values()), default=1))
            eta = sum(finished) / len(finished) * remaining / conc
            m, s = divmod(int(eta), 60)
            h, m = divmod(m, 60)
            self.stat_eta.set(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
        else:
            self.stat_eta.set("—")
        busy = self.app.services.engine.is_busy()
        self.empty.setVisible(not cards)
        self.pause_btn.setEnabled(busy)
        self.resume_btn.setEnabled(busy)
        self.stop_btn.setEnabled(busy)
        self.force_btn.setEnabled(busy)
