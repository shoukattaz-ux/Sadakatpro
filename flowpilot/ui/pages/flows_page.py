"""Flows: flow list, step editor (drag & drop, undo/redo), properties, recorder and tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QSplitter,
)

from flowpilot.core.errors import ValidationProblem
from flowpilot.core.flow_document import FlowDocument
from flowpilot.core.flow_io import export_flow, import_flow
from flowpilot.core.recorder import cleanup_steps
from flowpilot.core.runner import StepEvent, compile_flow
from flowpilot.core.steps import StepBase, dump_steps, new_step, parse_steps
from flowpilot.core.templates import TEMPLATES
from flowpilot.storage.models import Flow
from flowpilot.ui.app_context import AppContext
from flowpilot.ui.async_utils import confirm, info, run_async, show_error
from flowpilot.ui.dialogs.recorder_toolbar import RecorderToolbar
from flowpilot.ui.dialogs.simple_dialogs import TemplateDialog, VersionsDialog
from flowpilot.ui.dialogs.step_picker import StepPicker
from flowpilot.ui.pages.base import Page
from flowpilot.ui.widgets.clay import ClayButton, ClayCard, EmptyState, IconButton, title_label
from flowpilot.ui.widgets.step_form import StepForm
from flowpilot.ui.widgets.step_list import StepListWidget


class FlowsPage(Page):
    title = "Flows"
    subtitle = "Build automations step by step — or record them by using the browser."
    icon = "flow"

    def __init__(self, app: AppContext) -> None:
        super().__init__(app)
        self.flow: Flow | None = None
        self.doc = FlowDocument()
        self.doc.on_change = self._on_doc_change
        self.recording_from = -1
        self.toolbar_win: RecorderToolbar | None = None
        self._running = False
        self._account_vars: list[str] = []
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(800)
        self._autosave.timeout.connect(self.save)

        # header actions: test profile selection
        self.actions.addWidget(QLabel("Test profile"))
        self.profile_box = QComboBox()
        self.profile_box.setMinimumWidth(180)
        self.profile_box.setToolTip("The account used for recording, Pick element and test runs.")
        self.actions.addWidget(self.profile_box)
        self.open_btn = ClayButton("Open", "monitor", compact=True, tooltip="Open the test profile's browser")
        self.open_btn.clicked.connect(self.open_profile)
        self.close_btn = ClayButton("Close", "power", compact=True, tooltip="Close the test browser")
        self.close_btn.clicked.connect(self.close_profile)
        self.actions.addWidget(self.open_btn)
        self.actions.addWidget(self.close_btn)

        splitter = QSplitter()
        self.content.addWidget(splitter, 1)

        # ---- flow list
        left = ClayCard(padding=10)
        left.setMinimumWidth(250)
        left.setMaximumWidth(300)
        left.body.addWidget(title_label("Your flows", "subtitle"))
        self.flow_list = QListWidget()
        self.flow_list.currentItemChanged.connect(self._flow_selected)
        self.flow_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.flow_list.customContextMenuRequested.connect(self._flow_menu)
        left.body.addWidget(self.flow_list, 1)
        new_btn = ClayButton("New flow", "plus", "primary")
        new_btn.clicked.connect(self.new_flow)
        left.body.addWidget(new_btn)
        row = QHBoxLayout()
        for icon, tip, slot in (("copy", "Duplicate", self.duplicate_flow), ("edit", "Rename", self.rename_flow),
                                ("upload", "Import .json (FlowPilot or Chrome DevTools recording)", self.import_flow),
                                ("download", "Export as .json", self.export_flow),
                                ("history", "Version history", self.show_versions),
                                ("trash", "Delete", self.delete_flow)):
            b = IconButton(icon, tip, "danger" if icon == "trash" else "neutral", size=26)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.setSpacing(0)
        row.addStretch(1)
        left.body.addLayout(row)
        splitter.addWidget(left)

        # ---- step editor
        center = ClayCard(padding=10)
        center.setMinimumWidth(520)
        self.flow_title = title_label("No flow selected", "subtitle")
        center.body.addWidget(self.flow_title)
        tb = QHBoxLayout()
        tb.setSpacing(0)
        self.add_btn = ClayButton("Add step", "plus", "primary", compact=True)
        self.add_btn.clicked.connect(lambda: self.add_step())
        self.record_btn = ClayButton("Record", "record", "danger", compact=True,
                                     tooltip="Open the test profile and turn your actions into steps")
        self.record_btn.clicked.connect(self.toggle_recording)
        tb.addWidget(self.add_btn)
        tb.addWidget(self.record_btn)
        for icon, tip, slot in (("undo", "Undo (Ctrl+Z)", self.undo), ("redo", "Redo (Ctrl+Y)", self.redo),
                                ("copy", "Duplicate step (Ctrl+D)", self.duplicate_steps),
                                ("eye", "Enable / disable step", self.toggle_enabled),
                                ("wand", "Clean up (remove duplicate clicks, merge typing)", self.cleanup),
                                ("trash", "Delete step (Del)", self.delete_steps)):
            b = IconButton(icon, tip, size=30)
            b.clicked.connect(slot)
            tb.addWidget(b)
        tb.addStretch(1)
        center.body.addLayout(tb)
        self.banner = QLabel("")
        self.banner.setProperty("role", "error")
        self.banner.setWordWrap(True)
        center.body.addWidget(self.banner)
        self.steps_view = StepListWidget()
        self.steps_view.itemSelectionChanged.connect(self._selection_changed)
        # apply after the view finished its drop (the list is rebuilt on change)
        self.steps_view.reordered.connect(lambda ids: QTimer.singleShot(0, lambda: self.doc.reorder(ids)))
        self.steps_view.collapse_toggled.connect(self._toggle_collapse)
        self.steps_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.steps_view.customContextMenuRequested.connect(self._step_menu)
        center.body.addWidget(self.steps_view, 1)
        self.empty = EmptyState("record", "No steps yet",
                                "Click Record to create your first steps by using the browser, or add steps by hand.",
                                "Add step")
        assert self.empty.button is not None
        self.empty.button.clicked.connect(lambda: self.add_step())
        center.body.addWidget(self.empty, 1)
        run_row = QHBoxLayout()
        self.run_here_btn = ClayButton("From here", "play", "secondary", compact=True,
                                       tooltip="Run from the selected step to the end")
        self.run_here_btn.clicked.connect(self.run_from_here)
        self.run_one_btn = ClayButton("This step", "pointer", compact=True, tooltip="Run only the selected step")
        self.run_one_btn.clicked.connect(self.run_this_step)
        self.run_all_btn = ClayButton("Test flow", "zap", "primary", compact=True,
                                      tooltip="Run the whole flow on the test profile (F5)")
        self.run_all_btn.clicked.connect(self.run_whole)
        self.stop_btn = ClayButton("Stop", "stop", "danger", compact=True)
        self.stop_btn.clicked.connect(self.stop_test)
        self.stop_btn.setEnabled(False)
        for b in (self.run_all_btn, self.run_here_btn, self.run_one_btn, self.stop_btn):
            run_row.addWidget(b)
        run_row.addStretch(1)
        center.body.addLayout(run_row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setPlaceholderText("Test results appear here.")
        center.body.addWidget(self.log)
        splitter.addWidget(center)

        # ---- properties
        right = ClayCard(padding=12)
        right.setMinimumWidth(320)
        self.form = StepForm()
        self.form.changed.connect(self._form_changed)
        self.form.pick_requested.connect(self.pick_element)
        right.body.addWidget(self.form)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 640, 360])

        # shortcuts
        for keys, slot in ((QKeySequence.StandardKey.Save, self.save_version), (QKeySequence.StandardKey.Undo, self.undo),
                           (QKeySequence.StandardKey.Redo, self.redo), ("Ctrl+Y", self.redo),
                           ("Ctrl+D", self.duplicate_steps), ("F5", self.run_whole), ("Ctrl+N", self.add_step)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.steps_view)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self.delete_steps)

        app.bridge.data_changed.connect(lambda what: self._reload_profiles() if what in ("accounts",) else None)
        self.reload_flows()
        self._reload_profiles()
        self._update_buttons()

    # ================================================================== flows
    def reload_flows(self, select_id: int | None = None) -> None:
        current = select_id if select_id is not None else (self.flow.id if self.flow else None)
        self.flow_list.blockSignals(True)
        self.flow_list.clear()
        for f in self.app.repos.flows.list():
            n = len(json.loads(f.steps_json or "[]"))
            it = QListWidgetItem(f"{f.name}\n{n} step(s) · v{f.version}")
            it.setData(Qt.ItemDataRole.UserRole, f.id)
            self.flow_list.addItem(it)
        self.flow_list.blockSignals(False)
        target = None
        for i in range(self.flow_list.count()):
            if self.flow_list.item(i).data(Qt.ItemDataRole.UserRole) == current:
                target = i
        if target is None and self.flow_list.count():
            target = 0
        if target is not None:
            self.flow_list.setCurrentRow(target)
        else:
            self._load(None)

    def _flow_selected(self, item: QListWidgetItem | None, _prev: Any = None) -> None:
        self._commit_current()
        flow = self.app.repos.flows.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self._load(flow)

    def _commit_current(self) -> None:
        """Save pending edits and keep a version of the flow we are leaving."""
        if self.flow is not None and self.doc.dirty:
            self.save()
            self.app.repos.flows.snapshot(self.flow.id)

    def _load(self, flow: Flow | None) -> None:
        self.flow = flow
        if flow is None:
            self.doc.reset([])
            self.flow_title.setText("No flow selected")
        else:
            try:
                steps = parse_steps(json.loads(flow.steps_json or "[]"))
            except ValueError as exc:
                show_error(self, "Flow could not be opened", exc)
                steps = []
            self.doc.reset(steps)
            self.flow_title.setText(flow.name)
        self.steps_view.clear_marks()
        self._refresh(keep=[0] if self.doc.steps else [])
        self._update_buttons()

    def save(self) -> None:
        """Autosave (no version snapshot)."""
        self._autosave.stop()
        if self.flow is None or not self.doc.dirty:
            return
        self.flow.steps_json = self.doc.to_json()
        self.flow = self.app.repos.flows.save(self.flow, snapshot=False)
        self.doc.dirty = False
        self.app.bridge.data_changed.emit("flows")
        self._update_flow_item()

    def save_version(self) -> None:
        """Ctrl+S: save and keep a version in the history."""
        if self.flow is None:
            return
        self.doc.dirty = True
        self.save()
        if self.app.repos.flows.snapshot(self.flow.id):
            self.flow = self.app.repos.flows.get(self.flow.id)
            self._update_flow_item()
        self.log.appendPlainText("Saved.")

    def _update_flow_item(self) -> None:
        item = self.flow_list.currentItem()
        if item is not None and self.flow is not None:
            item.setText(f"{self.flow.name}\n{len(self.doc.steps)} step(s) · v{self.flow.version}")

    def new_flow(self) -> None:
        dlg = TemplateDialog(self)
        if not dlg.exec():
            return
        name, template = dlg.result_values()
        steps = TEMPLATES[template][1]()
        flow = self.app.repos.flows.create(name, json.dumps(dump_steps(steps)), TEMPLATES[template][0])
        self.reload_flows(flow.id)
        self.app.bridge.data_changed.emit("flows")

    def duplicate_flow(self) -> None:
        if self.flow is None:
            return
        self.save()
        copy = self.app.repos.flows.create(f"{self.flow.name} (copy)", self.flow.steps_json, self.flow.description)
        self.reload_flows(copy.id)
        self.app.bridge.data_changed.emit("flows")

    def rename_flow(self) -> None:
        if self.flow is None:
            return
        name, ok = QInputDialog.getText(self, "Rename flow", "Name:", text=self.flow.name)
        if ok and name.strip():
            self.flow.name = name.strip()
            self.flow = self.app.repos.flows.save(self.flow, snapshot=False)
            self.flow_title.setText(self.flow.name)
            self.reload_flows(self.flow.id)
            self.app.bridge.data_changed.emit("flows")

    def delete_flow(self) -> None:
        if self.flow is None:
            return
        if confirm(self, "Delete flow", f"Delete “{self.flow.name}” and its history? Schedules using it are removed too.",
                   "Delete", danger=True):
            self.app.repos.flows.delete(self.flow.id)
            self.flow = None
            self.doc.reset([])
            self.reload_flows()
            self.app.bridge.data_changed.emit("flows")
            self.app.bridge.data_changed.emit("schedules")

    def import_flow(self) -> None:
        file, _ = QFileDialog.getOpenFileName(self, "Import flow", "", "Flow / recording (*.json)")
        if not file:
            return
        try:
            name, desc, steps = import_flow(Path(file).read_text("utf-8"))
        except (OSError, ValueError) as exc:
            show_error(self, "Import failed", exc)
            return
        flow = self.app.repos.flows.create(name, json.dumps(dump_steps(steps)), desc)
        self.reload_flows(flow.id)
        self.app.bridge.data_changed.emit("flows")
        info(self, "Imported", f"“{name}” with {len(steps)} step(s) was imported.")

    def export_flow(self) -> None:
        if self.flow is None:
            return
        self.save()
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in self.flow.name)
        file, _ = QFileDialog.getSaveFileName(self, "Export flow", str(self.app.services.paths.exports / f"{safe}.json"),
                                              "Flow (*.json)")
        if file:
            Path(file).write_text(export_flow(self.flow.name, self.flow.description, self.doc.steps), "utf-8")
            self.log.appendPlainText(f"Exported to {file}")

    def show_versions(self) -> None:
        if self.flow is None:
            return
        self.save()
        versions = self.app.repos.flows.versions(self.flow.id)
        if not versions:
            info(self, "Version history", "No earlier versions yet. Press Ctrl+S to keep a version.")
            return
        dlg = VersionsDialog(versions, self)
        if dlg.exec() and dlg.chosen_json():
            steps = parse_steps(json.loads(dlg.chosen_json() or "[]"))
            self.doc._snapshot()  # noqa: SLF001 - make the restore undoable
            self.doc.steps = steps
            self.doc._changed()  # noqa: SLF001

    def _flow_menu(self, pos: Any) -> None:
        menu = QMenu(self)
        menu.addAction("Rename…").triggered.connect(self.rename_flow)
        menu.addAction("Duplicate").triggered.connect(self.duplicate_flow)
        menu.addAction("Export…").triggered.connect(self.export_flow)
        menu.addAction("Version history…").triggered.connect(self.show_versions)
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(self.delete_flow)
        menu.exec(self.flow_list.viewport().mapToGlobal(pos))

    # ================================================================== steps
    def _on_doc_change(self) -> None:
        self._refresh()
        if self.flow is not None:
            self._autosave.start()

    def _refresh(self, keep: list[int] | None = None) -> None:
        problems = self.doc.problems()
        self.steps_view.set_steps(self.doc.steps, self.doc.depths(), problems, self.doc.hidden_rows(), keep)
        has_steps = bool(self.doc.steps)
        self.steps_view.setVisible(has_steps)
        self.empty.setVisible(not has_steps and self.flow is not None)
        n = len(problems)
        self.banner.setText(f"⚠ {n} step(s) need attention (shown in red)." if n else "")
        self.banner.setVisible(bool(n))
        self._update_buttons()

    def _selected(self) -> list[int]:
        return self.steps_view.selected_rows()

    def _selection_changed(self) -> None:
        rows = self._selected()
        if len(rows) == 1:
            idx = rows[0]
            self.form.set_context(self.doc.labels(), self._account_vars + self.doc.defined_variables())
            self.form.set_step(self.doc.steps[idx], self.doc.problems().get(idx, []))
        else:
            self.form.set_step(None)
        self._update_buttons()

    def _form_changed(self, step: StepBase) -> None:
        idx = self.doc.index_of(step.id)
        if idx < 0:
            return
        self.doc.update(idx, step)
        self.form.show_problems(self.doc.problems().get(idx, []))

    def add_step(self, type_name: str | None = None) -> None:
        if self.flow is None:
            info(self, "Choose a flow", "Create or select a flow first.")
            return
        if type_name is None:
            picker = StepPicker(self)
            if not picker.exec() or not picker.chosen:
                return
            type_name = picker.chosen
        rows = self._selected()
        at = (max(rows) + 1) if rows else len(self.doc.steps)
        new_rows = self.doc.insert(at, type_name)
        self._refresh(keep=[new_rows[0]])

    def delete_steps(self) -> None:
        rows = self._selected()
        if rows:
            self.doc.delete(rows)
            self._refresh(keep=[min(rows[0], len(self.doc.steps) - 1)] if self.doc.steps else [])

    def duplicate_steps(self) -> None:
        rows = self._selected()
        if rows:
            new = self.doc.duplicate(rows)
            self._refresh(keep=new)

    def toggle_enabled(self) -> None:
        rows = self._selected()
        if rows:
            enable = not all(self.doc.steps[r].enabled for r in rows)
            self.doc.set_enabled(rows, enable)
            self._refresh(keep=rows)

    def undo(self) -> None:
        if self.doc.undo():
            self._refresh()

    def redo(self) -> None:
        if self.doc.redo():
            self._refresh()

    def cleanup(self) -> None:
        if not self.doc.steps:
            return
        before = len(self.doc.steps)
        cleaned = cleanup_steps(self.doc.steps)
        if len(cleaned) == before:
            self.log.appendPlainText("Clean up: nothing to tidy.")
            return
        self.doc._snapshot()  # noqa: SLF001 - single undo entry
        self.doc.steps = cleaned
        self.doc._changed()  # noqa: SLF001
        self.log.appendPlainText(f"Clean up: removed {before - len(cleaned)} duplicate step(s).")

    def _toggle_collapse(self, row: int) -> None:
        self.doc.toggle_collapsed(row)

    def _step_menu(self, pos: Any) -> None:
        rows = self._selected()
        menu = QMenu(self)
        menu.addAction("Add step after…").triggered.connect(lambda: self.add_step())
        if rows:
            menu.addAction("Duplicate").triggered.connect(self.duplicate_steps)
            menu.addAction("Enable / disable").triggered.connect(self.toggle_enabled)
            menu.addAction("Run from here").triggered.connect(self.run_from_here)
            menu.addAction("Run this step only").triggered.connect(self.run_this_step)
            step = self.doc.steps[rows[0]]
            if len(rows) == 1 and step.type == "type_text" and "{{" not in getattr(step, "text", ""):
                menu.addAction("Turn this text into a variable…").triggered.connect(lambda: self._to_variable(rows[0]))
            if len(rows) == 1 and step.USES_SELECTOR:
                menu.addAction("Pick element…").triggered.connect(lambda: self.pick_element("selector"))
            menu.addSeparator()
            menu.addAction("Delete").triggered.connect(self.delete_steps)
        menu.addSeparator()
        menu.addAction("Undo").triggered.connect(self.undo)
        menu.addAction("Redo").triggered.connect(self.redo)
        menu.exec(self.steps_view.viewport().mapToGlobal(pos))

    def _to_variable(self, row: int) -> None:
        step = self.doc.steps[row]
        guess = "email" if "@" in getattr(step, "text", "") else "username"
        name, ok = QInputDialog.getText(self, "Turn into a variable",
                                        "Variable name (each account gets its own value on the Accounts page):",
                                        text=guess)
        if not ok or not name.strip():
            return
        key = name.strip().removeprefix("account.").strip("{} ")
        self.doc.update(row, step.model_copy(update={"text": "{{account." + key + "}}"}))
        self._refresh(keep=[row])
        info(self, "Variable created", f"The step now types {{{{account.{key}}}}}.\n"
             f"Fill in “{key}” for each account on the Accounts page (Edit / variables).")

    # ================================================================== test profile
    def _reload_profiles(self) -> None:
        current = self.profile_box.currentData()
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        keys: set[str] = set()
        for a in self.app.repos.accounts.list():
            if a.connection_id is not None:
                self.profile_box.addItem(a.name, a.id)
        for a in self.app.repos.accounts.list()[:200]:
            keys.update(self.app.repos.accounts.variables(a).keys())
        self._account_vars = [f"account.{k}" for k in sorted(keys)] + ["account.name", "account.profile_id"]
        if current is not None:
            self.profile_box.setCurrentIndex(max(0, self.profile_box.findData(current)))
        self.profile_box.blockSignals(False)

    def _test_account(self) -> Any:
        acc = self.app.repos.accounts.get(self.profile_box.currentData())
        if acc is None:
            info(self, "Choose a test profile", "Add an account on the Accounts page first, then choose it here.")
        return acc

    def open_profile(self, then: Any = None) -> None:
        acc = self._test_account()
        if acc is None:
            return
        self.log.appendPlainText(f"Opening {acc.name}…")
        self.open_btn.setEnabled(False)

        def done(_: Any) -> None:
            self.open_btn.setEnabled(True)
            self.log.appendPlainText(f"{acc.name} is open.")
            self._update_buttons()
            if callable(then):
                then()

        def failed(exc: BaseException) -> None:
            self.open_btn.setEnabled(True)
            show_error(self, "Could not open the profile", exc)

        run_async(self.app.debug.open(acc), done, failed)

    def close_profile(self) -> None:
        if self.toolbar_win is not None:
            self.stop_recording()
        run_async(self.app.debug.close(), lambda _: (self.log.appendPlainText("Test browser closed."),
                                                     self._update_buttons()))

    def _with_profile(self, action: Any) -> None:
        """Run ``action`` once the test profile is open (opening it if needed)."""
        acc = self._test_account()
        if acc is None:
            return
        if self.app.debug.is_open and self.app.debug.account is not None and self.app.debug.account.id == acc.id:
            action()
        else:
            self.open_profile(then=action)

    # ================================================================== recorder
    def toggle_recording(self) -> None:
        if self.toolbar_win is not None:
            self.stop_recording()
            return
        if self.flow is None:
            info(self, "Choose a flow", "Create or select a flow to record into.")
            return
        self._with_profile(self._start_recording)

    def _start_recording(self) -> None:
        self.recording_from = len(self.doc.steps)
        rec = self.app.debug.recorder_for(self._recorded_step, lambda msg: self.toolbar_win and
                                          self.toolbar_win.set_status(msg))
        tb = RecorderToolbar()
        tb.pause_toggled.connect(lambda paused: run_async(rec.pause() if paused else rec.resume()))
        tb.stop_clicked.connect(self.stop_recording)
        tb.add_wait.connect(lambda: self._recorded_step(new_step("wait_random", min_seconds=1, max_seconds=3), False))
        tb.add_screenshot.connect(lambda: self._recorded_step(new_step("screenshot"), False))
        tb.add_close.connect(lambda: self._recorded_step(new_step("close_browser"), False))
        self.toolbar_win = tb
        geo = self.window().geometry()
        tb.adjustSize()
        tb.move(geo.right() - tb.width() - 40, geo.top() + 60)
        tb.show()
        self.record_btn.setText("Stop recording")
        run_async(rec.start(add_open_url=not self.doc.steps), parent=self)

    def _recorded_step(self, step: StepBase, replace_last: bool) -> None:
        idx = self.doc.append_step(step, replace_last and len(self.doc.steps) > self.recording_from)
        self._refresh(keep=[idx])
        if self.toolbar_win is not None:
            self.toolbar_win.set_count(len(self.doc.steps) - self.recording_from)

    def stop_recording(self) -> None:
        rec = self.app.debug.recorder
        if rec is not None:
            run_async(rec.stop())
        if self.toolbar_win is not None:
            self.toolbar_win.close()
            self.toolbar_win.deleteLater()
            self.toolbar_win = None
        self.record_btn.setText("Record")
        recorded = len(self.doc.steps) - max(0, self.recording_from)
        if self.recording_from >= 0 and recorded > 1:
            head, tail = self.doc.steps[:self.recording_from], self.doc.steps[self.recording_from:]
            cleaned = cleanup_steps(tail)
            if len(cleaned) < len(tail) and confirm(
                    self, "Clean up recording", f"Remove {len(tail) - len(cleaned)} duplicate click(s)/typing step(s) "
                    "from the recording?", "Clean up"):
                self.doc._snapshot()  # noqa: SLF001
                self.doc.steps = head + cleaned
                self.doc._changed()  # noqa: SLF001
        self.recording_from = -1
        self.save()

    # ================================================================== pick element
    def pick_element(self, field: str = "selector") -> None:
        rows = self._selected()
        if len(rows) != 1:
            info(self, "Pick element", "Select one step first.")
            return
        step_id = self.doc.steps[rows[0]].id

        def go() -> None:
            debug = self.app.debug
            rec = debug.recorder or debug.recorder_for(lambda *_: None)
            self.log.appendPlainText("Click the element in the browser window…")

            def done(result: Any) -> None:
                if not result:
                    self.log.appendPlainText("Pick cancelled.")
                    return
                selector, fallbacks, element_desc = result
                idx = self.doc.index_of(step_id)
                if idx < 0:
                    return
                update: dict[str, Any] = {field: selector}
                if field == "selector":
                    update["fallbacks"] = fallbacks
                    update["element_desc"] = element_desc
                self.doc.update(idx, self.doc.steps[idx].model_copy(update=update))
                self._refresh(keep=[idx])
                self.log.appendPlainText(f"Picked: {selector}")
                self.window().raise_()
                self.window().activateWindow()

            run_async(rec.pick_element(), done, parent=self)

        self._with_profile(go)

    # ================================================================== test runs
    def _log_line(self, level: str, message: str) -> None:
        self.log.appendPlainText(("⚠ " if level in ("warning", "error") else "") + message)

    def _on_test_event(self, e: StepEvent) -> None:
        if e.status in ("running", "retry"):
            self.steps_view.mark(e.index, "running")
        elif e.status == "ok":
            self.steps_view.mark(e.index, "ok")
        elif e.status == "failed":
            self.steps_view.mark(e.index, "failed")
            self.log.appendPlainText(f"✖ Step {e.index + 1}: {e.error}")
        elif e.status == "skipped":
            self.steps_view.mark(e.index, "")

    def _run(self, start: int, stop_at: int | None, steps: list[StepBase] | None = None) -> None:
        if self._running:
            return
        steps = steps if steps is not None else self.doc.steps
        try:
            compile_flow(steps)
        except ValidationProblem as exc:
            show_error(self, "Fix the flow first", exc)
            return

        def go() -> None:
            self._running = True
            self.steps_view.clear_marks()
            self._update_buttons()
            self.log.appendPlainText(f"▶ Running step {start + 1}" + (f" to {stop_at + 1}" if stop_at is not None
                                                                      else " to the end") + "…")

            def done(outcome: Any) -> None:
                self._running = False
                self._update_buttons()
                self.log.appendPlainText(("✔ Finished" if outcome.success else "✖ Ended as failed") +
                                         (f": {outcome.message}" if outcome.message else "."))

            def failed(exc: BaseException) -> None:
                self._running = False
                self._update_buttons()
                from flowpilot.core.errors import StopRequested, friendly_error

                self.log.appendPlainText("■ Stopped." if isinstance(exc, StopRequested) else f"✖ {friendly_error(exc)}")

            run_async(self.app.debug.run(steps, start, stop_at, self._on_test_event, self._log_line), done, failed)

        self._with_profile(go)

    def run_whole(self) -> None:
        self.save()
        self._run(0, None)

    def run_from_here(self) -> None:
        rows = self._selected()
        self._run(rows[0] if rows else 0, None)

    def run_this_step(self) -> None:
        rows = self._selected()
        if len(rows) != 1:
            info(self, "Run this step", "Select one step.")
            return
        step = self.doc.steps[rows[0]]
        if step.BLOCK is not None:
            info(self, "Run this step", "Logic blocks can't run alone. Use 'Run from here'.")
            return
        problems = step.problems()
        if problems:
            show_error(self, "Fix the step first", problems[0])
            return
        single = [step.model_copy(update={"enabled": True})]
        self._run(0, 0, steps=single)

    def stop_test(self) -> None:
        self.app.debug.stop()

    def _update_buttons(self) -> None:
        has_flow = self.flow is not None
        rows = self._selected()
        self.add_btn.setEnabled(has_flow and not self._running)
        self.record_btn.setEnabled(has_flow and not self._running)
        for b in (self.run_all_btn, self.run_here_btn):
            b.setEnabled(has_flow and bool(self.doc.steps) and not self._running)
        self.run_one_btn.setEnabled(len(rows) == 1 and not self._running)
        self.stop_btn.setEnabled(self._running)
        self.close_btn.setEnabled(self.app.debug.is_open)

    def on_show(self) -> None:
        self._reload_profiles()

    def on_data_changed(self, what: str) -> None:
        if what == "flows-external":
            self.reload_flows()
