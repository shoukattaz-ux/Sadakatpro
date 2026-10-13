"""Start answers that only contain a process id: the debugging port is read from the process."""

from __future__ import annotations

import subprocess
import sys

import httpx
import pytest

from flowpilot.adapters import AdapterError
from flowpilot.adapters.custom import CustomAdapter
from flowpilot.adapters import cdp_locate
from flowpilot.adapters.cdp_locate import find_pid, scan_process
from tests.conftest import needs_browser

EXTRA = {"start_url": "/profiles/{id}/start", "start_method": "POST", "ws_path": "auto", "success_path": "auto",
         "list_url": "/profiles", "key_location": "none"}


def pid_api(pid: int) -> httpx.MockTransport:
    return httpx.MockTransport(lambda req: httpx.Response(200, json={"pid": pid}))


def test_find_pid():
    assert find_pid({"pid": 18708}) == 18708
    assert find_pid({"data": {"processId": "42"}}) == 42
    assert find_pid({"port": 1}) is None


@needs_browser
async def test_start_answer_with_only_a_pid(tmp_path):
    from flowpilot.adapters.local import find_browser

    proc = subprocess.Popen([find_browser(), "--headless=new", "--no-sandbox", "--remote-debugging-port=0", "--no-first-run",
                             f"--user-data-dir={tmp_path / 'profile'}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        adapter = CustomAdapter("http://127.0.0.1:9378", "", EXTRA, transport=pid_api(proc.pid))
        address = await adapter.start_profile("p1")
        assert address.startswith("ws://127.0.0.1:") and "/devtools/browser/" in address
        await adapter.aclose()
    finally:
        proc.kill()
        proc.wait()


async def test_pid_without_debugging_port_is_explained(monkeypatch):
    monkeypatch.setattr(cdp_locate, "PID_WAIT_S", 1.0)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert scan_process(proc.pid).found
        adapter = CustomAdapter("http://h", "", EXTRA, transport=pid_api(proc.pid))
        with pytest.raises(AdapterError, match="started without a debugging port"):
            await adapter.start_profile("p1")
    finally:
        proc.kill()
        proc.wait()
    gone = CustomAdapter("http://h", "", EXTRA, transport=pid_api(proc.pid))
    with pytest.raises(AdapterError, match="not running on this PC"):
        await gone.start_profile("p1")
