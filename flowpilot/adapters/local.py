"""Local Chrome adapter — runs plain Chrome/Chromium profiles on this PC.

Not an anti-detect browser, but handy to try FlowPilot without one, to build flows,
and for the automated tests. Each profile is a folder inside ``profiles_dir``; starting a
profile launches the browser with ``--remote-debugging-port`` and returns its CDP address.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from flowpilot.adapters.base import AdapterError, ConnectionResult, ExtraField, RemoteProfile


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_browser() -> str:
    """Best guess at a Chrome/Chromium executable on this machine."""
    candidates: list[str] = []
    if sys.platform == "win32":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
                candidates.append(str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    elif sys.platform == "darwin":
        candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    pw_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if pw_root and Path(pw_root).exists():
        for p in sorted(Path(pw_root).glob("chromium-*/chrome-linux*/chrome")):
            candidates.append(str(p))
        candidates.append(str(Path(pw_root) / "chromium"))
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return ""


class LocalChromeAdapter:
    display_name = "Local Chrome (testing)"
    default_base_url = ""
    api_key_help = "Not used."
    extra_fields = [
        ExtraField("executable", "Browser executable", "Leave empty to auto-detect Chrome/Chromium/Edge", ""),
        ExtraField("profiles_dir", "Profiles folder", "Each sub-folder is one profile", ""),
        ExtraField("headless", "Headless", "1 = hidden browser windows", "0"),
    ]

    def __init__(self, base_url: str = "", api_key: str = "", extra: dict[str, Any] | None = None, **_: Any) -> None:
        self.extra = {f.key: f.default for f in self.extra_fields} | (extra or {})
        from flowpilot.config import default_data_dir

        self.profiles_dir = Path(self.extra.get("profiles_dir") or default_data_dir() / "local_profiles")
        self._procs: dict[str, subprocess.Popen[bytes]] = {}

    def _executable(self) -> str:
        exe = self.extra.get("executable") or find_browser()
        if not exe or not Path(exe).exists():
            raise AdapterError("No Chrome/Chromium found. Set 'Browser executable' in the connection settings.")
        return exe

    async def test_connection(self) -> ConnectionResult:
        try:
            exe = self._executable()
            profiles = await self.list_profiles()
        except AdapterError as exc:
            return ConnectionResult(False, str(exc))
        return ConnectionResult(True, f"Browser found: {exe}. {len(profiles)} profile(s).")

    def _check_folder(self) -> None:
        if (self.profiles_dir / "Local State").exists():
            raise AdapterError(
                f"“{self.profiles_dir}” is Chrome's own data folder (its sub-folders such as Crashpad are not "
                "profiles, and Chrome does not allow automation of this folder). In Settings → Connections, edit "
                "this connection and leave “Profiles folder” empty (FlowPilot then keeps its own profiles) or "
                "choose an empty folder.")

    async def list_profiles(self) -> list[RemoteProfile]:
        self._check_folder()
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        if not any(self.profiles_dir.iterdir()):
            for i in range(1, 4):
                (self.profiles_dir / f"Profile {i}").mkdir()
        return [RemoteProfile(id=p.name, name=p.name) for p in sorted(self.profiles_dir.iterdir()) if p.is_dir()]

    async def start_profile(self, profile_id: str) -> str:
        if profile_id in self._procs and self._procs[profile_id].poll() is None:
            raise AdapterError(f"Profile '{profile_id}' is already open.")
        self._check_folder()
        port = _free_port()
        user_dir = self.profiles_dir / profile_id
        user_dir.mkdir(parents=True, exist_ok=True)
        args = [self._executable(), f"--remote-debugging-port={port}", f"--user-data-dir={user_dir}",
                "--no-first-run", "--no-default-browser-check", "about:blank"]
        if str(self.extra.get("headless", "0")) in ("1", "true", "yes"):
            args.insert(1, "--headless=new")
        if sys.platform.startswith("linux") and os.geteuid() == 0:
            args.insert(1, "--no-sandbox")
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._procs[profile_id] = proc
        async with httpx.AsyncClient(timeout=2.0) as client:
            for _ in range(100):
                if proc.poll() is not None:
                    raise AdapterError("The browser closed right after starting.")
                try:
                    resp = await client.get(f"http://127.0.0.1:{port}/json/version")
                    return str(resp.json()["webSocketDebuggerUrl"])
                except (httpx.HTTPError, KeyError, ValueError):
                    await asyncio.sleep(0.2)
        await self.stop_profile(profile_id)
        raise AdapterError("The browser did not open its debugging port in time.")

    async def stop_profile(self, profile_id: str) -> None:
        proc = self._procs.pop(profile_id, None)
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        for _ in range(50):
            if proc.poll() is not None:
                return
            await asyncio.sleep(0.1)
        proc.kill()

    async def is_running(self, profile_id: str) -> bool:
        proc = self._procs.get(profile_id)
        return proc is not None and proc.poll() is None

    async def aclose(self) -> None:
        for pid in list(self._procs):
            await self.stop_profile(pid)
