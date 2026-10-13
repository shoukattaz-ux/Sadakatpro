"""Export everything on one "PC" and import it on another (different data folder + key)."""

from __future__ import annotations

import json
import zipfile

import pytest

from flowpilot.config import AppPaths, SettingsStore
from flowpilot.security import SecretBox
from flowpilot.services.portable import PortableError, export_all, import_all, read_manifest
from flowpilot.storage.db import Database
from flowpilot.storage.models import Account, Connection, Run, RunItem, Schedule
from flowpilot.storage.repositories import Repositories


class PC:
    """A complete, separate FlowPilot data folder with its own encryption key."""

    def __init__(self, root):
        self.paths = AppPaths(root).ensure()
        self.db = Database(self.paths.db_file)
        self.secrets = SecretBox(self.paths.root, use_dpapi=False)
        self.repos = Repositories(self.db, self.secrets)
        self.store = SettingsStore(self.paths.settings_file)

    def export(self, path, password="pass1234", history=False):
        return export_all(self.db, self.secrets, self.store.settings, path, password, history)

    def import_(self, path, password="pass1234", mode="replace"):
        return import_all(self.paths, self.db, self.secrets, self.store, self.repos, path, password, mode)

    def close(self):
        self.db.dispose()


@pytest.fixture()
def pcs(tmp_path):
    a, b = PC(tmp_path / "old-pc"), PC(tmp_path / "new-pc")
    yield a, b
    a.close()
    b.close()


def fill(pc: PC) -> None:
    r = pc.repos
    conn = r.connections.save(Connection(type="auto", name="Stealth", base_url="http://127.0.0.1:9378",
                                         extra_json='{"detected": "1"}'), api_key="SECRET-KEY")
    g = r.groups.create("Shops", "#ff0000")
    r.accounts.save(Account(connection_id=conn.id, remote_profile_id="p1", name="Shop 1", group_id=g.id,
                            tags="a,b", notes="vip"), variables={"password": "hunter2", "email": "x@y.z"})
    r.accounts.save(Account(connection_id=conn.id, remote_profile_id="p2", name="Shop 2", enabled=False))
    flow = r.flows.create("Daily login", json.dumps([{"type": "goto", "url": "https://example.com"}]), "desc")
    flow.steps_json = json.dumps([{"type": "goto", "url": "https://example.org"}])
    r.flows.save(flow)
    r.schedules.save(Schedule(name="Every day", flow_id=flow.id, target_json=json.dumps(
        {"mode": "groups", "group_ids": [g.id]}), cron_or_interval='{"kind": "daily"}'))
    run, _ = r.runs.create_run(Run(flow_id=flow.id, flow_name=flow.name, status="done", total=1),
                               [RunItem(account_name="Shop 1", status="success", screenshot_path="C:/x.png")])
    s = pc.store.settings
    s.theme = "dark"
    s.humanize.mouse_speed = 1.7
    s.telegram_bot_token = "tg"
    s.ai_api_key_encrypted = pc.secrets.encrypt("sk-ai")
    pc.store.save()


def test_round_trip_to_another_pc(pcs, tmp_path):
    old, new = pcs
    fill(old)
    summary = old.export(tmp_path / "my-data")
    assert summary.path.name == "my-data.flowpilot"
    assert summary.counts["accounts"] == 2 and "runs" not in summary.counts
    # nothing secret is readable in the file
    raw = summary.path.read_bytes()
    with zipfile.ZipFile(summary.path) as zf:
        raw += zf.read("data.enc")
    assert b"SECRET-KEY" not in raw and b"hunter2" not in raw and b"Daily login" not in raw
    assert read_manifest(summary.path)["counts"]["flows"] == 1

    result = new.import_(summary.path)
    assert result.backup and result.backup.exists()
    r = new.repos
    conn = r.connections.list()[0]
    assert conn.api_key_encrypted != old.repos.connections.list()[0].api_key_encrypted  # re-encrypted
    assert r.connections.api_key(conn) == "SECRET-KEY" and r.connections.extra(conn) == {"detected": "1"}
    accs = {a.name: a for a in r.accounts.list()}
    assert r.accounts.variables(accs["Shop 1"]) == {"password": "hunter2", "email": "x@y.z"}
    assert accs["Shop 1"].group_id == r.groups.list()[0].id and accs["Shop 1"].notes == "vip"
    assert not accs["Shop 2"].enabled
    flow = r.flows.list()[0]
    assert "example.org" in flow.steps_json and len(r.flows.versions(flow.id)) == len(old.repos.flows.versions(1))
    assert r.schedules.list()[0].flow_id == flow.id
    s = new.store.settings
    assert (s.theme, s.humanize.mouse_speed, s.telegram_bot_token, s.setup_completed) == ("dark", 1.7, "tg", True)
    assert new.secrets.decrypt(s.ai_api_key_encrypted) == "sk-ai"
    assert SettingsStore(new.paths.settings_file).settings.theme == "dark"  # saved to disk


def test_history_and_replace_twice(pcs, tmp_path):
    old, new = pcs
    fill(old)
    path = old.export(tmp_path / "x.flowpilot", history=True).path
    new.import_(path)
    new.import_(path)  # importing again does not duplicate anything
    assert len(new.repos.accounts.list()) == 2 and len(new.repos.flows.list()) == 1
    items = new.repos.runs.items(new.repos.runs.list()[0].id)
    assert items[0].account_name == "Shop 1" and items[0].screenshot_path == ""


def test_wrong_password_and_bad_files(pcs, tmp_path):
    old, new = pcs
    fill(old)
    path = old.export(tmp_path / "x").path
    with pytest.raises(PortableError, match="Wrong password"):
        new.import_(path, "nope-nope")
    assert new.repos.accounts.list() == []
    with pytest.raises(PortableError, match="at least"):
        old.export(tmp_path / "y", password="1")
    junk = tmp_path / "junk.flowpilot"
    junk.write_text("hello")
    with pytest.raises(PortableError, match="not a FlowPilot export"):
        new.import_(junk)


def test_merge_keeps_existing_and_remaps_ids(pcs, tmp_path):
    old, new = pcs
    fill(old)
    path = old.export(tmp_path / "x").path
    # the new PC already has its own data, with ids that collide
    mine = new.repos.groups.create("Mine")
    new.repos.accounts.save(Account(name="Local acc", group_id=mine.id))
    new.repos.flows.create("Daily login", "[]")
    new.store.settings.theme = "light"
    new.store.save()

    result = new.import_(path, mode="merge")
    assert result.counts["accounts"] == 2 and result.counts["flows"] == 1
    r = new.repos
    names = sorted(a.name for a in r.accounts.list())
    assert names == ["Local acc", "Shop 1", "Shop 2"]
    flows = {f.name: f for f in r.flows.list()}
    assert set(flows) == {"Daily login", "Daily login (imported)"}
    shops = next(g for g in r.groups.list() if g.name == "Shops")
    sched = r.schedules.list()[0]
    assert sched.flow_id == flows["Daily login (imported)"].id
    assert json.loads(sched.target_json)["group_ids"] == [shops.id]
    shop1 = next(a for a in r.accounts.list() if a.name == "Shop 1")
    assert shop1.group_id == shops.id and r.accounts.variables(shop1)["password"] == "hunter2"
    assert new.store.settings.theme == "light"  # merge keeps this PC's settings
    # merging the same file again adds nothing
    again = new.import_(path, mode="merge")
    assert again.counts == {"connections": 0, "groups": 0, "accounts": 0, "flows": 0, "schedules": 0}
