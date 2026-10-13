"""Stealth Browser: a fake app that follows its documented API and really launches Chromium.

Covers: start must send {"automation": true}; endpoints come from data.automation; 409 for a
profile opened by hand (stop, start again); 423 while locked; wrong key; ids with spaces; an
existing "Any browser (auto-detect)" connection switching to the Stealth API by itself.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import pytest

from flowpilot.adapters import AdapterError, create_adapter
from flowpilot.adapters.stealth import FIREFOX_PREFIX, endpoint_from
from flowpilot.core.engine import RunOptions, TargetSpec
from flowpilot.core.steps import dump_steps, new_step
from flowpilot.storage.models import Connection
from tests.conftest import needs_browser

KEY = "hy3sobPeT6FVq4zSGk5H8MpkDnUmdOc"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeStealth(BaseHTTPRequestHandler):
    profiles = {"p-kok": "kok", "p-shop": "Shop US"}
    running: dict[str, dict] = {}  # id -> {"proc", "dir", "automation": dict | None}
    locked = False
    calls: list[tuple[str, str, object]] = []
    executable = ""

    def log_message(self, *args):  # noqa: D401
        pass

    def _send(self, code: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self, data: object) -> None:
        self._send(200, {"ok": True, "data": data})

    def _err(self, status: int, code: str, message: str) -> None:
        self._send(status, {"ok": False, "error": {"code": code, "message": message}})

    def _launch(self, automation: bool) -> dict:
        port = _free_port()
        user_dir = tempfile.mkdtemp(prefix="stealth-")
        args = [self.executable, "--headless=new", "--no-sandbox", f"--user-data-dir={user_dir}", "about:blank"]
        if automation:
            args.insert(1, f"--remote-debugging-port={port}")
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        info = {"proc": proc, "dir": user_dir, "automation": None}
        if automation:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                        ws = json.loads(r.read())["webSocketDebuggerUrl"]
                        break
                except Exception:
                    time.sleep(0.1)
            info["automation"] = {"selenium_url": f"http://127.0.0.1:{port}", "selenium_options": "ChromeOptions",
                                  "playwright_endpoint": ws, "playwright_connect": "chromium.connect_over_cdp",
                                  "playwright_version": None, "cdp_url": ws, "debugger_address": f"127.0.0.1:{port}"}
        return info

    def _stop(self, pid: str) -> None:
        info = self.running.pop(pid, None)
        if info:
            info["proc"].terminate()
            info["proc"].wait(10)
            shutil.rmtree(info["dir"], ignore_errors=True)

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.calls.append((method, path, body))
        if not path.startswith("/api/v1/"):
            return self._err(404, "not_found", "Not found")
        auth = self.headers.get("Authorization") == f"Bearer {KEY}" or self.headers.get("X-API-Key") == KEY
        if not auth:
            return self._err(401, "unauthorized", "Wrong API key")
        if self.locked:
            return self._err(423, "locked", "Stealth Browser is locked: enter the code first")
        parts = [unquote(p) for p in path[len("/api/v1/"):].split("/")]
        if parts == ["status"]:
            return self._ok({"version": "2.4.0", "playwright_version": "1.62.0",
                             "engines": ["fingerprint-chromium", "camoufox"]})
        if parts == ["profiles"] and method == "GET":
            return self._ok([{"id": k, "name": v, "engine": "fingerprint-chromium", "tags": ["us"], "notes": ""}
                             for k, v in self.profiles.items()])
        if len(parts) != 3 or parts[0] != "profiles":
            return self._err(404, "not_found", "Not found")
        ref, action = parts[1], parts[2]
        pid = ref if ref in self.profiles else next((k for k, v in self.profiles.items() if v == ref), None)
        if pid is None:
            return self._err(404, "not_found", f"No profile “{ref}”")
        if action == "start" and method == "POST":
            automation = bool((body or {}).get("automation"))
            if pid in self.running:
                if self.running[pid]["automation"] is None:
                    return self._err(409, "conflict", "The profile is already open without automation")
                return self._err(409, "conflict", "The profile is already open")
            self.running[pid] = self._launch(automation)
            if not automation:  # what FlowPilot used to get: only the process id
                return self._ok({"pid": self.running[pid]["proc"].pid})
            return self._ok({"pid": self.running[pid]["proc"].pid, "automation": self.running[pid]["automation"]})
        if action == "stop" and method == "POST":
            if pid not in self.running:
                return self._err(409, "conflict", "The profile is not open")
            self._stop(pid)
            return self._ok({"stopped": True})
        if action == "status" and method == "GET":
            info = self.running.get(pid)
            return self._ok({"running": info is not None, "automation": info and info["automation"],
                             "last_error": None})
        return self._err(404, "not_found", "Not found")

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")


@pytest.fixture()
def fake_stealth():
    from flowpilot.adapters.local import find_browser

    FakeStealth.executable = find_browser()
    FakeStealth.running, FakeStealth.calls, FakeStealth.locked = {}, [], False
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeStealth)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    for pid in list(FakeStealth.running):
        FakeStealth.running[pid]["proc"].kill()
        FakeStealth.running.pop(pid)
    server.shutdown()


def test_endpoint_from_answer():
    auto = {"playwright_endpoint": "ws://127.0.0.1:1/a", "playwright_connect": "chromium.connect_over_cdp"}
    assert endpoint_from(auto) == "ws://127.0.0.1:1/a"
    assert endpoint_from({**auto, "playwright_connect": "firefox.connect"}) == FIREFOX_PREFIX + "ws://127.0.0.1:1/a"
    assert endpoint_from({"debugger_address": "127.0.0.1:9"}) == "http://127.0.0.1:9"


@needs_browser
async def test_stealth_adapter_lifecycle(fake_stealth, monkeypatch):
    import flowpilot.adapters.stealth as stealth

    a = create_adapter("stealth", fake_stealth, KEY)
    result = await a.test_connection()
    assert result.ok and "2.4.0" in result.message and "2 profile" in result.message, result.message
    profiles = await a.list_profiles()
    assert [(p.id, p.name, p.tags) for p in profiles] == [("p-kok", "kok", ["us"]), ("p-shop", "Shop US", ["us"])]

    endpoint = await a.start_profile("Shop US")  # a name with a space works too
    assert endpoint.startswith("ws://127.0.0.1:")
    assert ("POST", "/api/v1/profiles/Shop%20US/start", {"automation": True}) in FakeStealth.calls
    assert await a.is_running("p-shop")
    await a.stop_profile("p-shop")
    assert not await a.is_running("p-shop")
    await a.stop_profile("p-shop")  # stopping a closed profile is fine

    # opened by hand without automation → 409 → FlowPilot closes it and opens it for automation
    FakeStealth.running["p-kok"] = FakeStealth._launch(FakeStealth, automation=False)
    endpoint = await a.start_profile("p-kok")
    assert endpoint.startswith("ws://") and FakeStealth.running["p-kok"]["automation"]
    # already open for automation (e.g. an earlier run) → attach to it
    assert await a.start_profile("p-kok") == endpoint
    await a.stop_profile("p-kok")

    # locked app and wrong key: plain-language errors
    monkeypatch.setattr(stealth, "LOCK_WAIT_S", 0.0)
    FakeStealth.locked = True
    with pytest.raises(AdapterError, match="locked"):
        await a.start_profile("p-kok")
    FakeStealth.locked = False
    bad = await create_adapter("stealth", fake_stealth, "wrong").test_connection()
    assert not bad.ok and "API key" in bad.message
    with pytest.raises(AdapterError, match="No profile"):
        await a.start_profile("nope")


@needs_browser
async def test_auto_detect_connection_switches_to_stealth_and_runs_a_flow(services, site_url, fake_stealth):
    """The user's existing 'Any browser (auto-detect)' connection, saved by v0.2.3, now just works."""
    old = {"detected": "2", "list_url": "/api/v1/profiles", "list_items_path": "data", "list_id_path": "id",
           "list_name_path": "name", "start_url": "/api/v1/profiles/{id}/start", "start_method": "POST",
           "ws_path": "auto", "success_path": "auto", "key_location": "header", "key_name": "Authorization",
           "key_prefix": "Bearer "}
    conn = services.repos.connections.save(Connection(type="auto", name="stealth", base_url=fake_stealth,
                                                      extra_json=json.dumps(old)), api_key=KEY)
    from flowpilot.services.profile_sync import sync_profiles

    adapter = services.adapters.get(conn)
    assert (await sync_profiles(services.repos, adapter, conn)).created == 2
    saved = services.repos.connections.extra(services.repos.connections.get(conn.id))
    assert saved == {"detected": "3", "flavor": "stealth"}  # remembered for next time
    services.settings.settings.resource_guard = False
    services.settings.settings.humanize.step_delay_min = services.settings.settings.humanize.step_delay_max = 0
    steps = [new_step("open_url", url=f"{site_url}/welcome.html?email=kok@stealth.dev"),
             new_step("extract", selector="#who", variable="who"),
             new_step("save_value", values=["who = {{who}}"]),
             new_step("close_tab"),  # the only tab: must not close the profile
             new_step("close_browser")]
    flow = services.repos.flows.create("Stealth", json.dumps(dump_steps(steps)))
    kok = next(a for a in services.repos.accounts.list() if a.name == "kok")
    run_id = await services.engine.start(flow, TargetSpec(mode="accounts", account_ids=[kok.id]),
                                         RunOptions(launch_delay_min=0, launch_delay_max=0))
    while run_id in services.engine.runs:
        await asyncio.sleep(0.2)
    item = services.repos.runs.items(run_id)[0]
    assert item.status == "done", item.error
    assert json.loads(item.results_json)["who"] == "kok@stealth.dev"
    starts = [b for m, p, b in FakeStealth.calls if p.endswith("/start")]
    assert starts and all(b == {"automation": True} for b in starts)
    assert FakeStealth.running == {}  # stopped through the API at the end
