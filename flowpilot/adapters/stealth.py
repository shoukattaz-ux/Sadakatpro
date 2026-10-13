"""Stealth Browser — local API on ``http://127.0.0.1:9378`` (``/api/v1``).

* Every answer is ``{"ok": true, "data": …}`` or ``{"ok": false, "error": {"code", "message"}}``;
  ``error.message`` is written for people and is shown as is.
* ``POST /profiles/{id}/start`` with ``{"automation": true}`` opens the profile *for automation*
  and returns ``data.automation`` with the endpoints (they change on every start). Without that
  flag the profile opens for normal use and cannot be controlled.
* The first start on a PC may download the browser engine, so the start call may take minutes.
* HTTP 423 = the app is locked (enter the access code), 409 on start = the profile is already
  open (by hand without automation: stop it, then start again).
* Firefox (Camoufox) profiles are attached with ``firefox.connect`` and need the same Playwright
  version as the app; Chrome profiles use ``connect_over_cdp``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from flowpilot.adapters.base import AdapterError, BaseHttpAdapter, ConnectionResult, ExtraField, RemoteProfile
from flowpilot.adapters.discovery import find_list

#: Prefix that tells :class:`~flowpilot.core.session.BrowserSession` to use ``firefox.connect``.
FIREFOX_PREFIX = "firefox+"
START_TIMEOUT_S = 15 * 60
LOCK_WAIT_S = 60.0


class StealthError(AdapterError):
    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(t) for t in value if str(t).strip()]
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return []


def endpoint_from(automation: dict[str, Any]) -> str:
    """The address FlowPilot attaches to, from a ``data.automation`` answer."""
    endpoint = str(automation.get("playwright_endpoint") or automation.get("cdp_url") or "")
    if not endpoint and automation.get("debugger_address"):
        endpoint = f"http://{automation['debugger_address']}"
    if not endpoint:
        return ""
    if str(automation.get("playwright_connect", "")).startswith("firefox"):
        return FIREFOX_PREFIX + endpoint
    return endpoint


class StealthAdapter(BaseHttpAdapter):
    display_name = "Stealth Browser"
    default_base_url = "http://127.0.0.1:9378"
    api_key_help = "Stealth Browser → Settings → API Connection: copy the API Base URL and the API Key."
    extra_fields: list[ExtraField] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.app_info: dict[str, Any] = {}

    def _make_client(self) -> Any:  # requests go through _call (own error handling)
        return _NoClient()

    async def aclose(self) -> None:
        return None

    # ------------------------------------------------------------------ requests
    async def _call(self, method: str, path: str, body: Any = None, timeout: float = 60.0,
                    wait_if_locked: bool = True) -> Any:
        url = f"{self.base_url}/api/v1{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        loop = asyncio.get_running_loop()
        give_up = loop.time() + (LOCK_WAIT_S if wait_if_locked else 0)
        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self._transport, trust_env=False) as client:
                    resp = await client.request(method, url, json=body, headers=headers)
            except httpx.ConnectError as exc:
                raise StealthError(f"Stealth Browser is not running — nothing answers at {self.base_url}. "
                                   "Open Stealth Browser and try again.") from exc
            except httpx.TimeoutException as exc:
                raise StealthError("Stealth Browser did not answer in time. If it is downloading a browser "
                                   "engine (first start), wait until that finishes and try again.") from exc
            except httpx.HTTPError as exc:
                raise StealthError(f"Could not talk to Stealth Browser: {exc}") from exc
            if resp.status_code == 423:
                if loop.time() < give_up:
                    logger.info("Stealth Browser is locked; waiting for the access code…")
                    await asyncio.sleep(5)
                    continue
                raise StealthError("Stealth Browser is locked. Enter your access code in Stealth Browser, "
                                   "then try again.", 423, "locked")
            try:
                data = resp.json() if resp.content else {}
            except ValueError:
                data = {}
            if resp.status_code < 400 and (not isinstance(data, dict) or data.get("ok", True)):
                return data.get("data", data) if isinstance(data, dict) else data
            err = data.get("error") if isinstance(data, dict) else None
            message = (err.get("message") if isinstance(err, dict) else err) or resp.text[:300]
            code = err.get("code", "") if isinstance(err, dict) else ""
            if resp.status_code == 401:
                message = "Stealth Browser refused the API key. Copy it again from Stealth Browser → Settings → " \
                          "API Connection."
            raise StealthError(str(message or f"HTTP {resp.status_code}"), resp.status_code, str(code))

    @staticmethod
    def _pid(profile_id: str) -> str:
        return quote(str(profile_id), safe="")

    # ------------------------------------------------------------------ adapter API
    async def test_connection(self) -> ConnectionResult:
        try:
            self.app_info = await self._call("GET", "/status", wait_if_locked=False) or {}
            profiles = await self.list_profiles()
        except AdapterError as exc:
            return ConnectionResult(False, str(exc))
        version = self.app_info.get("version") or self.app_info.get("app_version") or ""
        return ConnectionResult(True, f"Connected to Stealth Browser{(' ' + str(version)) if version else ''}. "
                                      f"Found {len(profiles)} profile(s).")

    async def list_profiles(self) -> list[RemoteProfile]:
        data = await self._call("GET", "/profiles")
        located = find_list(data)
        items = located[1] if located else []
        out = []
        for p in items:
            pid = str(p.get("id") or p.get("name") or "")
            if not pid:
                continue
            engine = str(p.get("engine") or "")
            notes = str(p.get("notes") or "")
            out.append(RemoteProfile(id=pid, name=str(p.get("name") or pid), group=str(p.get("group") or ""),
                                     tags=_tags(p.get("tags")),
                                     notes=notes or ("Firefox (Camoufox)" if engine == "camoufox" else "")))
        return out

    async def _start_once(self, profile_id: str) -> dict[str, Any]:
        data = await self._call("POST", f"/profiles/{self._pid(profile_id)}/start", {"automation": True},
                                timeout=START_TIMEOUT_S)
        return data if isinstance(data, dict) else {}

    async def start_profile(self, profile_id: str) -> str:
        try:
            data = await self._start_once(profile_id)
        except StealthError as exc:
            if exc.status != 409:
                raise
            data = await self._recover_open_profile(profile_id, str(exc))
        automation = data.get("automation") if isinstance(data.get("automation"), dict) else data
        endpoint = endpoint_from(automation)
        if not endpoint:
            raise AdapterError("Stealth Browser opened the profile but did not return automation endpoints. "
                               "Update Stealth Browser to a version with automation support.")
        self._check_playwright(automation)
        return endpoint

    async def _recover_open_profile(self, profile_id: str, reason: str) -> dict[str, Any]:
        """409 on start: reuse an automation session, or close a hand-opened profile and start again."""
        status = await self._status(profile_id)
        automation = status.get("automation")
        if status.get("running") and isinstance(automation, dict) and endpoint_from(automation):
            return status  # already open for automation (e.g. by an earlier run): just attach
        logger.info("Profile {} is open without automation ({}); restarting it", profile_id, reason)
        await self.stop_profile(profile_id)
        for _ in range(60):
            if not (await self._status(profile_id)).get("running"):
                break
            await asyncio.sleep(0.5)
        return await self._start_once(profile_id)

    async def _status(self, profile_id: str) -> dict[str, Any]:
        data = await self._call("GET", f"/profiles/{self._pid(profile_id)}/status")
        return data if isinstance(data, dict) else {}

    async def stop_profile(self, profile_id: str) -> None:
        try:
            await self._call("POST", f"/profiles/{self._pid(profile_id)}/stop", timeout=120)
        except StealthError as exc:
            if exc.status not in (404, 409):  # already closed
                raise

    async def is_running(self, profile_id: str) -> bool:
        try:
            return bool((await self._status(profile_id)).get("running"))
        except AdapterError:
            return False

    @staticmethod
    def _check_playwright(automation: dict[str, Any]) -> None:
        if not str(automation.get("playwright_connect", "")).startswith("firefox"):
            return  # Chrome profiles accept any recent Playwright
        wanted = str(automation.get("playwright_version") or "")
        try:
            from importlib.metadata import version

            have = version("playwright")
        except Exception:  # pragma: no cover
            return
        if wanted and wanted.split(".")[:2] != have.split(".")[:2]:
            raise AdapterError(f"This is a Firefox (Camoufox) profile. It needs Playwright {wanted}, but this "
                               f"FlowPilot has {have}. Update FlowPilot, or use a Chrome profile.")


def _is_envelope(data: Any) -> bool:
    """Stealth Browser's documented answer shape: {"ok": true, "data": …} / {"ok": false, "error": {…}}."""
    return isinstance(data, dict) and isinstance(data.get("ok"), bool) and (
        "data" in data or isinstance(data.get("error"), dict))


async def looks_like_stealth(base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """True when ``base_url`` serves Stealth Browser's automation API (``/api/v1`` with its
    ``{"ok": …}`` answers). Works when the key is wrong (401) or the app is locked (423) too."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0, transport=transport, trust_env=False) as client:
            for path in ("/api/v1/status", "/api/v1/profiles"):
                resp = await client.get(base + path, headers=headers)
                try:
                    data = resp.json()
                except ValueError:
                    continue
                if _is_envelope(data):
                    return True
    except httpx.HTTPError:
        return False
    return False


class _NoClient:
    async def aclose(self) -> None:
        return None
