"""Database, repositories, secrets, settings, variables, humanize maths and services."""

from __future__ import annotations

import json
import random
import zipfile
from datetime import datetime, timedelta

import pytest

from flowpilot.config import AppSettings, HumanizeSettings, SettingsStore, apply_humanize_preset
from flowpilot.core.errors import friendly_error
from flowpilot.core.humanize import bezier_path, plan_scroll, plan_typing, random_point_in_box
from flowpilot.core.variables import Variables, find_variables
from flowpilot.security import SecretBox, mask
from flowpilot.services.backup import create_backup, restore_backup
from flowpilot.services.reporting import apply_retention, export_items
from flowpilot.services.scheduler import build_trigger, describe_spec
from flowpilot.storage.models import Account, Connection, Run, RunItem


def test_secretbox_roundtrip(tmp_path):
    box = SecretBox(tmp_path, use_dpapi=False)
    enc = box.encrypt("p@ss")
    assert enc.startswith("fernet:") and "p@ss" not in enc
    assert box.decrypt(enc) == "p@ss"
    assert SecretBox(tmp_path, use_dpapi=False).decrypt(enc) == "p@ss"  # key persisted
    assert box.encrypt("") == "" and box.decrypt("") == ""
    assert mask("secret") == "sec••••"


def test_settings_store_roundtrip(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.settings.step_timeout_s = 33
    store.save()
    assert SettingsStore(tmp_path / "s.json").settings.step_timeout_s == 33
    (tmp_path / "bad.json").write_text("{not json")
    assert SettingsStore(tmp_path / "bad.json").settings == AppSettings()
    h = apply_humanize_preset(HumanizeSettings(), "careful")
    assert h.preset == "careful" and h.typing_min_ms > HumanizeSettings().typing_min_ms


def test_repositories(services):
    r = services.repos
    conn = r.connections.save(Connection(type="adspower", name="Ads", base_url="http://x"), api_key="K123")
    assert conn.api_key_encrypted and "K123" not in conn.api_key_encrypted
    assert r.connections.api_key(conn) == "K123"
    g1, g2 = r.groups.create("First"), r.groups.create("Second")
    r.groups.reorder([g2.id, g1.id])
    assert [g.name for g in r.groups.list()] == ["Second", "First"]
    acc = r.accounts.save(Account(name="A", connection_id=conn.id, remote_profile_id="u1", group_id=g1.id),
                          variables={"password": "hunter2"})
    assert "hunter2" not in acc.variables_encrypted_json
    assert r.accounts.variables(acc) == {"password": "hunter2"}
    r.accounts.move_to_group([acc.id], g2.id)
    r.accounts.set_enabled([acc.id], False)
    acc = r.accounts.get(acc.id)
    assert acc.group_id == g2.id and not acc.enabled
    assert r.accounts.find_by_remote(conn.id, "u1").id == acc.id

    flow = r.flows.create("F", "[]")
    for i in range(13):
        flow.steps_json = json.dumps([{"type": "wait", "seconds": i}])
        flow = r.flows.save(flow)
    assert flow.version == 14
    versions = r.flows.versions(flow.id)
    assert len(versions) == 10 and versions[0].version == 13

    run, items = r.runs.create_run(Run(flow_id=flow.id, flow_name="F", total=1),
                                   [RunItem(account_id=acc.id, account_name="A")])
    assert items[0].run_id == run.id
    r.runs.update_item(items[0].id, status="done", finished_at=datetime.now())
    assert r.runs.succeeded_today([acc.id], flow.id) == {acc.id}
    assert r.runs.interrupted()[0].id == run.id


def test_variables():
    v = Variables(account={"email": "a@b.c"}, flow={"x": 1})
    assert v.render("Hi {{account.email}} {{ x }} {{missing}}") == "Hi a@b.c 1 {{missing}}"
    assert v.render("{{missing}}", keep_unknown=False) == ""
    v.set("loop.item", "z")
    v.set("{{price}}", "9")
    assert v.get("loop.item") == "z" and v.get("price") == "9"
    with pytest.raises(ValueError):
        v.set("account.email", "x")
    assert len(v.render("{{uuid}}")) == 36
    assert find_variables("{{a}} and {{account.b}}") == ["a", "account.b"]


def test_humanize_maths():
    rng = random.Random(3)
    path = bezier_path((0, 0), (300, 200), rng)
    assert path[-1] == pytest.approx((300, 200))
    assert len(path) >= 8
    box = {"x": 100, "y": 100, "width": 50, "height": 20}
    for _ in range(200):
        x, y = random_point_in_box(box, rng)
        assert 100 + 50 * 0.15 <= x <= 150 - 50 * 0.15 and 100 + 20 * 0.15 <= y <= 120 - 20 * 0.15
    s = HumanizeSettings(typo_chance=0.5)
    actions = plan_typing("hello world", s, rng)
    typed = []
    for a in actions:
        if a.key == "Backspace":
            typed.pop()
        else:
            typed.append(a.key)
    assert "".join(typed) == "hello world"
    assert any(a.key == "Backspace" for a in actions)
    steps = plan_scroll(1000, HumanizeSettings(scroll_back_chance=0.3), rng)
    assert sum(steps) == pytest.approx(1000)


def test_friendly_errors():
    assert friendly_error(Exception("Timeout 15000ms exceeded.")) == "The page took too long to respond."
    assert "address could not be found" in friendly_error(Exception("net::ERR_NAME_NOT_RESOLVED at https://x"))
    assert friendly_error(Exception("Target page, context or browser has been closed")).startswith("The browser was closed")
    assert friendly_error(Exception("weird")).startswith("Something went wrong")


def test_scheduler_triggers():
    rng = random.Random(1)
    when = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    t = build_trigger({"kind": "once", "at": when.isoformat(), "jitter_minutes": 10}, rng)
    assert when <= t.run_date.replace(tzinfo=None) <= when + timedelta(minutes=10)
    assert "mon-fri" in str(build_trigger({"kind": "weekdays", "at": "08:30"}))
    assert build_trigger({"kind": "interval", "hours": 2}).interval == timedelta(hours=2)
    with pytest.raises(ValueError):
        build_trigger({"kind": "interval", "hours": 0})
    assert describe_spec({"kind": "daily", "at": "09:00", "jitter_minutes": 5}) == "Every day at 09:00 (+0–5 min random)"


async def test_scheduler_service_fires(services, monkeypatch):
    from flowpilot.storage.models import Schedule

    flow = services.repos.flows.create("F", json.dumps([{"type": "wait", "seconds": 0}]))
    sched = services.repos.schedules.save(Schedule(name="s", flow_id=flow.id, cron_or_interval=json.dumps(
        {"kind": "daily", "at": "09:00"}), target_json=json.dumps({"mode": "all"})))
    services.scheduler.start()
    try:
        assert services.repos.schedules.get(sched.id).next_run is not None
        run_id = await services.scheduler.run_now(sched.id)
        assert run_id is None  # no accounts -> could not start, reported in last_result
        assert "No enabled accounts" in services.repos.schedules.get(sched.id).last_result
    finally:
        services.scheduler.shutdown()


def test_backup_restore_and_export(services, tmp_path):
    services.repos.flows.create("Keep me", json.dumps([{"type": "wait", "seconds": 1}]))
    services.settings.settings.step_timeout_s = 42
    services.settings.save()
    archive = create_backup(services.paths, services.repos)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert "flowpilot.db" in names and any(n.startswith("flows/") for n in names)
    services.repos.flows.create("After backup")
    services.db.dispose()
    restore_backup(services.paths, archive)
    from flowpilot.storage.db import Database
    from flowpilot.storage.repositories import Repositories

    db = Database(services.paths.db_file)
    try:
        assert [f.name for f in Repositories(db, services.secrets).flows.list()] == ["Keep me"]
    finally:
        db.dispose()
    with pytest.raises(ValueError):
        bad = tmp_path / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("x.txt", "x")
        restore_backup(services.paths, bad)

    items = [RunItem(run_id=1, account_name="A", status="done", results_json=json.dumps({"price": "9"}))]
    csv_path = export_items(items, tmp_path / "out.csv")
    assert "result.price" in csv_path.read_text("utf-8-sig")
    xlsx = export_items(items, tmp_path / "out.xlsx")
    assert xlsx.stat().st_size > 0


def test_retention(services):
    old = datetime.now() - timedelta(days=40)
    shot = services.paths.screenshots / "old.png"
    shot.write_bytes(b"x")
    run, _ = services.repos.runs.create_run(Run(flow_name="F", started_at=old),
                                            [RunItem(account_name="A", screenshot_path=str(shot))])
    services.repos.runs.create_run(Run(flow_name="New"), [RunItem(account_name="B")])
    assert apply_retention(services.repos, services.paths, 30) == 1
    assert not shot.exists()
    assert [r.flow_name for r in services.repos.runs.list()] == ["New"]


async def test_profile_sync(services):
    from flowpilot.adapters.base import RemoteProfile
    from flowpilot.services.profile_sync import sync_profiles

    class FakeAdapter:
        def __init__(self, profiles):
            self.profiles = profiles

        async def list_profiles(self):
            return self.profiles

    conn = services.repos.connections.save(Connection(type="custom", name="C"))
    fake = FakeAdapter([RemoteProfile("1", "One", group="G1"), RemoteProfile("2", "Two")])
    res = await sync_profiles(services.repos, fake, conn)
    assert (res.created, res.updated, res.total) == (2, 0, 2)
    assert [g.name for g in services.repos.groups.list()] == ["G1"]
    fake.profiles = [RemoteProfile("1", "One renamed"), RemoteProfile("3", "Three")]
    res = await sync_profiles(services.repos, fake, conn)
    assert (res.created, res.updated) == (1, 1)
    assert sorted(a.name for a in services.repos.accounts.list()) == ["One renamed", "Three", "Two"]


def test_flow_snapshot(services):
    r = services.repos.flows
    flow = r.create("F", "[]")
    assert r.snapshot(flow.id)
    assert not r.snapshot(flow.id)  # unchanged
    flow = r.get(flow.id)
    flow.steps_json = '[{"type": "wait"}]'
    r.save(flow, snapshot=False)
    assert r.snapshot(flow.id)
    assert [v.steps_json for v in r.versions(flow.id)] == ['[{"type": "wait"}]', "[]"]
