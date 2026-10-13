"""A fake 'unknown anti-detect browser' on a real HTTP port that really launches Chromium.

FlowPilot is given only the base URL and the API key ("Any browser (auto-detect)") and must
detect the API, list profiles, start one, run a flow in it and stop it.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from flowpilot.core.engine import RunOptions, TargetSpec
from flowpilot.core.steps import dump_steps, new_step
from flowpilot.storage.models import Connection
from tests.conftest import needs_browser

KEY = "stealth-key-123"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StealthBrowserAPI(BaseHTTPRequestHandler):
    """Shape of a typical home-made browser API: Bearer key, /api/profiles, /api/profiles/:id/start."""

    profiles = {"stealth-1": "Main account", "stealth-2": "Second account"}
    procs: dict[str, tuple[subprocess.Popen, str]] = {}
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

    def _route(self, method: str) -> None:
        path = self.path.split("?")[0]
        m = re.fullmatch(r"/api/profiles/([\w-]+)/(start|stop)", path)
        if path != "/api/profiles" and not m:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<pre>Cannot {method} {path}</pre>".encode())
            return
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._send(401, {"error": "invalid api key"})
        if path == "/api/profiles":
            if method != "GET":
                return self._send(404, {"error": "not found"})
            return self._send(200, {"profiles": [{"id": k, "name": v} for k, v in self.profiles.items()]})
        pid, action = m.group(1), m.group(2)
        if method != "POST":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(f"Cannot {method} {path}".encode())
            return
        if pid not in self.profiles:
            return self._send(404, {"error": f"profile {pid} not found"})
        if action == "start":
            port = _free_port()
            user_dir = tempfile.mkdtemp(prefix="stealth-")
            proc = subprocess.Popen([self.executable, "--headless=new", "--no-sandbox", f"--remote-debugging-port={port}",
                                     f"--user-data-dir={user_dir}", "about:blank"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                        ws = json.loads(r.read())["webSocketDebuggerUrl"]
                        break
                except Exception:
                    time.sleep(0.1)
            self.procs[pid] = (proc, user_dir)
            return self._send(200, {"status": "started", "browser": {"websocket": ws, "port": port}})
        proc, user_dir = self.procs.pop(pid, (None, ""))
        if proc is not None:
            proc.terminate()
            proc.wait(10)
            shutil.rmtree(user_dir, ignore_errors=True)
        return self._send(200, {"status": "stopped"})

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")


@needs_browser
async def test_auto_detect_runs_a_flow_on_an_unknown_browser(services, site_url):
    from flowpilot.adapters.local import find_browser

    StealthBrowserAPI.executable = find_browser()
    server = ThreadingHTTPServer(("127.0.0.1", 0), StealthBrowserAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        conn = services.repos.connections.save(Connection(type="auto", name="Stealth Browser", base_url=base),
                                               api_key=KEY)
        adapter = services.adapters.get(conn)
        result = await adapter.test_connection()
        assert result.ok, result.message
        assert "found 2 profile" in result.message
        assert StealthBrowserAPI.procs == {}  # detection never started a real profile

        from flowpilot.services.profile_sync import sync_profiles

        synced = await sync_profiles(services.repos, adapter, conn)
        assert synced.created == 2
        services.settings.settings.resource_guard = False
        services.settings.settings.humanize.step_delay_min = services.settings.settings.humanize.step_delay_max = 0
        steps = [new_step("open_url", url=f"{site_url}/welcome.html?email=auto@detect.dev"),
                 new_step("extract", selector="#who", variable="who"),
                 new_step("save_value", values=["who = {{who}}"]),
                 new_step("close_browser")]
        flow = services.repos.flows.create("Auto", json.dumps(dump_steps(steps)))
        target = TargetSpec(mode="accounts", account_ids=[a.id for a in services.repos.accounts.list()
                                                           if a.remote_profile_id == "stealth-1"])
        run_id = await services.engine.start(flow, target, RunOptions(launch_delay_min=0, launch_delay_max=0))
        while run_id in services.engine.runs:
            await asyncio.sleep(0.2)
        item = services.repos.runs.items(run_id)[0]
        assert item.status == "done", item.error
        assert json.loads(item.results_json)["who"] == "auto@detect.dev"
        assert StealthBrowserAPI.procs == {}  # Close browser stopped the profile through the API
    finally:
        for proc, _ in StealthBrowserAPI.procs.values():
            proc.kill()
        server.shutdown()


