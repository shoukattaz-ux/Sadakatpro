"""Find the debugging (CDP) address of a browser the anti-detect app started.

Most APIs return the address in their start answer. Some only return the process id
(``{"pid": 18708}``). Then the address is read from the browser process itself:

* its command line: ``--remote-debugging-port=9222``;
* with ``--remote-debugging-port=0`` Chrome picks a port and writes it to
  ``<user data dir>/DevToolsActivePort``;
* otherwise the TCP ports the process listens on.

Every candidate is confirmed with ``GET /json/version`` before it is used, so a wrong guess
(a proxy port, …) is never handed to Playwright.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

#: How long to wait for a just-started browser to open its debugging port.
PID_WAIT_S = 20.0
PID_KEYS = ("pid", "process_id", "processId", "browser_pid", "browserPid", "chrome_pid")


def find_pid(data: Any, depth: int = 0) -> int | None:
    """A process id anywhere in a JSON answer."""
    if depth > 4:
        return None
    if isinstance(data, dict):
        for key, value in data.items():
            if key in PID_KEYS or key.lower() in {k.lower() for k in PID_KEYS}:
                if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
                    return int(value)
        for value in data.values():
            found = find_pid(value, depth + 1)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = find_pid(value, depth + 1)
            if found:
                return found
    return None


@dataclass
class ProcessScan:
    found: bool = False
    ports: list[int] = field(default_factory=list)
    pipe_only: bool = False
    debugging_flag: bool = False


def scan_process(pid: int) -> ProcessScan:
    """Ports that the browser started as ``pid`` (or its child processes) may debug on."""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return ProcessScan()
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ProcessScan()
    scan = ProcessScan(found=True)
    ports: list[int] = []
    for proc in procs:
        try:
            cmd = proc.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            cmd = []
        user_data = ""
        for arg in cmd:
            m = re.match(r"--remote-debugging-port=(\d+)", arg)
            if m:
                scan.debugging_flag = True
                if int(m.group(1)) > 0:
                    ports.append(int(m.group(1)))
            elif arg.startswith("--user-data-dir="):
                user_data = arg.split("=", 1)[1].strip('"')
            elif arg == "--remote-debugging-pipe":
                scan.pipe_only = True
        if user_data:
            active = Path(user_data) / "DevToolsActivePort"
            try:
                first = active.read_text("utf-8").splitlines()[0].strip()
                if first.isdigit():
                    ports.append(int(first))
            except (OSError, IndexError):
                pass
    for proc in procs:  # finally anything the browser listens on locally
        try:
            conns = proc.net_connections(kind="tcp") if hasattr(proc, "net_connections") else proc.connections("tcp")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        for c in conns:
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.ip in ("127.0.0.1", "0.0.0.0", "::1", "::"):
                ports.append(int(c.laddr.port))
    seen: set[int] = set()
    scan.ports = [p for p in ports if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
    if scan.ports:
        scan.pipe_only = False
    return scan


async def confirm_cdp(port: int, host: str = "127.0.0.1", transport: httpx.AsyncBaseTransport | None = None) -> str:
    """The browser WebSocket address if a DevTools endpoint answers on ``port``, else ''."""
    try:
        async with httpx.AsyncClient(timeout=2.0, transport=transport, trust_env=False) as client:
            resp = await client.get(f"http://{host}:{port}/json/version")
        if resp.status_code == 200:
            ws = str(resp.json().get("webSocketDebuggerUrl") or "")
            return ws or f"http://{host}:{port}"
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return ""


async def locate_by_pid(pid: int, host: str = "127.0.0.1", wait_s: float | None = None) -> tuple[str, ProcessScan]:
    """Wait for the browser ``pid`` to open its debugging port; returns (address or '', last scan)."""
    wait_s = PID_WAIT_S if wait_s is None else wait_s
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    scan = ProcessScan()
    while True:
        scan = await asyncio.to_thread(scan_process, pid)
        for port in scan.ports:
            address = await confirm_cdp(port, host)
            if address:
                logger.info("Found the debugging address of process {} on port {}", pid, port)
                return address, scan
        if loop.time() >= deadline or (not scan.found and loop.time() > deadline - wait_s + min(3.0, wait_s)):
            return "", scan
        await asyncio.sleep(0.5)
