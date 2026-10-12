"""Offscreen UI smoke tests: pages, dialogs and the step form for every step type."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from flowpilot.core.steps import StepRegistry, dump_steps, new_step  # noqa: E402
from flowpilot.storage.models import Account, Connection  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    from flowpilot.ui.theme.palette import ThemeManager

    ThemeManager.instance().apply("light")
    yield app


@pytest.fixture()
def ui(qapp, services):
    from flowpilot.ui.app_context import AppContext

    services.settings.settings.setup_completed = True
    conn = services.repos.connections.save(Connection(type="adspower", name="Ads", base_url="http://x"), api_key="k")
    g = services.repos.groups.create("G1")
    for i in range(3):
        services.repos.accounts.save(Account(name=f"A{i}", connection_id=conn.id, remote_profile_id=f"r{i}",
                                             group_id=g.id), variables={"email": f"{i}@x.y"})
    steps = [new_step("open_url", url="https://x"), new_step("if_element", selector="#a"), new_step("else"),
             new_step("end_if"), new_step("click")]
    services.repos.flows.create("Flow 1", json.dumps(dump_steps(steps)))
    return AppContext.create(services)


def test_main_window_pages(ui, qapp):
    from flowpilot.ui.main_window import MainWindow
    from flowpilot.ui.theme.palette import ThemeManager

    w = MainWindow(ui)
    w.show()
    for i in range(len(w.pages)):
        w.show_page(i)
        qapp.processEvents()
    w.toggle_theme()
    qapp.processEvents()
    assert ThemeManager.instance().palette.name == "dark"
    w.toggle_theme()
    w._really_quit = True
    w.hide()


@pytest.mark.parametrize("type_name", sorted(StepRegistry.all()))
def test_step_form_for_every_step(qapp, type_name):
    from flowpilot.ui.widgets.step_form import StepForm

    form = StepForm()
    changed = []
    form.changed.connect(changed.append)
    step = new_step(type_name)
    form.set_context(["lbl"], ["account.email", "price"])
    form.set_step(step, step.problems())
    qapp.processEvents()
    assert form.header.text().startswith(step.TITLE)
    form._set("note", "hello", now=True)  # every step has a note
    if step.BLOCK not in ("else", "end_if", "end_loop", "end_try", "catch"):
        assert changed and changed[-1].note == "hello"


def test_flows_page_editing(ui, qapp):
    from flowpilot.ui.pages.flows_page import FlowsPage

    page = FlowsPage(ui)
    page.reload_flows()
    assert page.flow is not None and len(page.doc.steps) == 5
    assert 4 in page.doc.problems()  # click without selector
    page.steps_view.setCurrentRow(0)
    page.add_step("wait")
    assert page.doc.steps[1].type == "wait"
    page.steps_view.clearSelection()
    page.steps_view.item(1).setSelected(True)
    page.duplicate_steps()
    assert [s.type for s in page.doc.steps][:3] == ["open_url", "wait", "wait"]
    page.delete_steps()
    page.undo()
    page.redo()
    page.toggle_enabled()
    page.save()
    stored = json.loads(ui.repos.flows.get(page.flow.id).steps_json)
    assert len(stored) == len(page.doc.steps)
    page.save_version()
    assert ui.repos.flows.versions(page.flow.id)


def test_dashboard_options_and_cards(ui, qapp):
    from flowpilot.core.engine import EngineEvent
    from flowpilot.ui.pages.dashboard_page import DashboardPage

    page = DashboardPage(ui)
    page.concurrency.setValue(3)
    page.launch_min.setValue(10)
    page.launch_max.setValue(20)
    page.group_unit.setCurrentIndex(1)
    page.group_min.setValue(1)
    page.group_max.setValue(2)
    target, opts = page._options()
    assert (opts.concurrency, opts.launch_delay_min, opts.launch_delay_max) == (3, 10, 20)
    assert (opts.group_delay_min, opts.group_delay_max) == (60, 120)
    assert target.mode == "all" and target.group_order
    ui.bridge.run_started.emit(EngineEvent("run_started", 1, data={
        "steps": 4, "concurrency": 2, "items": [{"item_id": 7, "account": "A0", "group": "G1"}]}))
    ui.bridge.item_status.emit(EngineEvent("item_status", 1, 7, {"status": "running"}))
    ui.bridge.item_step.emit(EngineEvent("item_step", 1, 7, {"index": 1, "total": 4, "title": "Click"}))
    ui.bridge.item_status.emit(EngineEvent("item_status", 1, 7, {"status": "waiting", "manual": "solve captcha"}))
    card = page.cards[7]
    assert card.continue_btn.isVisibleTo(card) and "captcha" in card.manual.text()
    ui.bridge.item_status.emit(EngineEvent("item_status", 1, 7, {"status": "done"}))
    assert page.stat_ok.value.text() == "1"
    page.clear_finished()
    assert not page.cards


def test_dialogs_construct(ui, qapp, tmp_path):
    from flowpilot.adapters import ADAPTERS
    from flowpilot.ui.dialogs.account_dialog import AccountDialog
    from flowpilot.ui.dialogs.connection_dialog import ConnectionDialog
    from flowpilot.ui.dialogs.import_wizard import ImportWizard, guess_target, read_table
    from flowpilot.ui.dialogs.setup_wizard import SetupWizard
    from flowpilot.ui.dialogs.simple_dialogs import AccountChooser, TemplateDialog
    from flowpilot.ui.dialogs.step_picker import StepPicker
    from flowpilot.ui.pages.scheduler_page import ScheduleDialog

    dlg = ConnectionDialog(ui)
    for i in range(len(ADAPTERS)):
        dlg.type_box.setCurrentIndex(i)
    dlg.type_box.setCurrentIndex(dlg.type_box.findData("custom"))
    conn, key = dlg.values()
    assert conn.type == "custom" and "start_url" in json.loads(conn.extra_json)
    existing = ui.repos.connections.list()[0]
    assert ConnectionDialog(ui, existing).api_key.text() == "k"

    acc = ui.repos.accounts.list()[0]
    ad = AccountDialog(ui, acc)
    assert ad.vars.values() == {"email": "0@x.y"}
    ad.vars.add_row("password", "s3cret")
    assert "•" in ad.vars.item(1, 1).text()
    ad.vars.set_show(True)
    assert ad.vars.item(1, 1).text() == "s3cret"
    ad.save()
    assert ui.repos.accounts.variables(ui.repos.accounts.get(acc.id))["password"] == "s3cret"

    picker = StepPicker()
    picker.search.setText("wait for")
    assert picker.tree.currentItem() is not None
    TemplateDialog()
    AccountChooser(ui.repos.accounts.list(), [], None)
    SetupWizard(ui)
    ScheduleDialog(ui)
    csv_file = tmp_path / "a.csv"
    csv_file.write_text("name;profile_id;group;email\nX;p1;New;x@y.z\n", "utf-8")
    headers, rows = read_table(csv_file)
    assert headers == ["name", "profile_id", "group", "email"] and rows == [["X", "p1", "New", "x@y.z"]]
    assert [guess_target(h) for h in headers] == ["name", "remote_profile_id", "group", "variable"]
    wiz = ImportWizard(ui)
    wiz.headers, wiz.rows = headers, rows
    wiz.map_page.initializePage()
    created, updated = wiz.do_import()
    assert (created, updated) == (1, 0)
    imported = ui.repos.accounts.find_by_remote(existing.id, "p1")
    assert imported.name == "X" and ui.repos.accounts.variables(imported) == {"email": "x@y.z"}


def test_settings_and_scheduler_pages(ui, qapp):
    from flowpilot.ui.pages.scheduler_page import SchedulerPage
    from flowpilot.ui.pages.settings_page import SettingsPage

    page = SettingsPage(ui)
    assert page.tabs.count() == 7
    sp = SchedulerPage(ui)
    sp.reload()
