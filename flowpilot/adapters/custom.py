"""Custom adapter: support any anti-detect browser (including your own) without code.

Everything is configured in the connection editor:

* ``start_url`` / ``stop_url`` / ``list_url`` / ``status_url`` — paths or full URLs.
  ``{id}`` is replaced by the profile ID and ``{api_key}`` by the key.
* ``*_method`` — ``GET`` or ``POST``; ``*_body`` — optional JSON body template.
* ``key_location`` — ``header``, ``query`` or ``none``; ``key_name`` and ``key_prefix``
  (e.g. ``Authorization`` + ``Bearer ``).
* ``ws_path`` — JSON path to the CDP endpoint in the start answer (e.g. ``data.ws.puppeteer``).
  If the value is only a port number, ``ws://127.0.0.1:<port>`` is not guessed — instead the
  ``http://127.0.0.1:<port>`` address is used, which Playwright also accepts.
* ``list_items_path`` / ``list_id_path`` / ``list_name_path`` / ``list_group_path`` — where
  to find profiles in the list answer.
* ``success_path`` + ``success_value`` — optional check (e.g. ``code`` == ``0``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from loguru import logger

from flowpilot.adapters.base import (
    AdapterError,
    BaseHttpAdapter,
    ConnectionResult,
    ExtraField,
    HttpClient,
    RemoteProfile,
    json_path,
)
from flowpilot.adapters.cdp_locate import find_pid, locate_by_pid
from flowpilot.adapters.discovery import api_failure, find_cdp
from flowpilot.adapters.stealth import StealthAdapter, looks_like_stealth


class CustomAdapter(BaseHttpAdapter):
    display_name = "Custom"
    default_base_url = "http://127.0.0.1:50325"
    api_key_help = "Leave empty if your browser's API does not need a key."
    extra_fields = [
        ExtraField("start_url", "Start URL", "Path or full URL. {id} = profile ID", "/api/v1/browser/start?user_id={id}"),
        ExtraField("start_method", "Start method", "", "GET", choices=["GET", "POST"]),
        ExtraField("start_body", "Start body (JSON)", 'Optional, e.g. {"profileId": "{id}"}', ""),
        ExtraField("ws_path", "JSON path to WebSocket/CDP address",
                   "e.g. data.ws.puppeteer — or 'auto' to find it anywhere in the answer", "data.ws.puppeteer"),
        ExtraField("stop_url", "Stop URL", "Path or full URL. {id} = profile ID", "/api/v1/browser/stop?user_id={id}"),
        ExtraField("stop_method", "Stop method", "", "GET", choices=["GET", "POST"]),
        ExtraField("stop_body", "Stop body (JSON)", "Optional", ""),
        ExtraField("list_url", "List profiles URL", "Path or full URL", "/api/v1/user/list?page_size=100"),
        ExtraField("list_method", "List method", "", "GET", choices=["GET", "POST"]),
        ExtraField("list_body", "List body (JSON)", "Optional", ""),
        ExtraField("list_items_path", "JSON path to the profile list", "e.g. data.list", "data.list"),
        ExtraField("list_id_path", "Profile ID field", "Inside each item, e.g. user_id", "user_id"),
        ExtraField("list_name_path", "Profile name field", "Inside each item, e.g. name", "name"),
        ExtraField("list_group_path", "Profile group field", "Optional, e.g. group_name", "group_name"),
        ExtraField("status_url", "Is-running URL", "Optional. {id} = profile ID", ""),
        ExtraField("status_path", "JSON path to running state", "e.g. data.status", ""),
        ExtraField("status_running_value", "Value meaning 'running'", "e.g. Active", "Active"),
        ExtraField("key_location", "Where the API key goes", "", "header", choices=["header", "query", "none"]),
        ExtraField("key_name", "API key header/parameter name", "", "Authorization"),
        ExtraField("key_prefix", "API key prefix", "Text before the key, e.g. 'Bearer '", "Bearer "),
        ExtraField("success_path", "Success field (optional)", "e.g. code — or 'auto' to detect error answers", ""),
        ExtraField("success_value", "Success value", "e.g. 0", "0"),
        ExtraField("host", "Debug host", "Used when the answer contains only a port", "127.0.0.1"),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Called with updated settings (e.g. after switching to a known API) so they are saved.
        self.on_detected: Callable[[dict[str, Any]], None] | None = None
        #: A browser FlowPilot knows exactly (Stealth Browser) is driven by its own adapter, even when
        #: the connection was set up as Custom / auto-detect with other addresses.
        self._known: StealthAdapter | None = None
        if self.extra.get("flavor") == "stealth":
            self._known = StealthAdapter(self.base_url, self.api_key, transport=self._transport)

    def _settings_for_known(self) -> dict[str, Any]:
        return {**self.extra, "flavor": "stealth"}

    def _switch_to_known(self) -> None:
        """From now on use Stealth Browser's automation API (and remember it for this connection)."""
        self._known = StealthAdapter(self.base_url, self.api_key, transport=self._transport)
        self.extra = self._settings_for_known()
        if self.on_detected is not None:
            self.on_detected(dict(self.extra))

    def _make_client(self) -> HttpClient:
        headers: dict[str, str] = {}
        params: dict[str, str] = {}
        location = self.extra.get("key_location", "header")
        if self.api_key and location == "header":
            headers[self.extra.get("key_name") or "Authorization"] = f"{self.extra.get('key_prefix', '')}{self.api_key}"
        elif self.api_key and location == "query":
            params[self.extra.get("key_name") or "api_key"] = self.api_key
        return HttpClient(self.base_url, headers=headers, params=params, transport=self._transport, timeout=120)

    # -- helpers -----------------------------------------------------------------
    def _fill(self, template: str, profile_id: str = "") -> str:
        return template.replace("{id}", profile_id).replace("{api_key}", self.api_key)

    def _body(self, key: str, profile_id: str = "") -> Any:
        raw = (self.extra.get(key) or "").strip()
        if not raw:
            return None
        try:
            return json.loads(self._fill(raw, profile_id))
        except ValueError as exc:
            raise AdapterError(f"The '{key}' JSON in this connection is not valid: {exc}") from exc

    async def _call(self, prefix: str, profile_id: str = "") -> Any:
        url = self.extra.get(f"{prefix}_url") or ""
        if not url:
            raise AdapterError(f"This custom connection has no '{prefix}' URL configured.")
        method = self.extra.get(f"{prefix}_method") or "GET"
        data = await self.http.request(method, self._fill(url, profile_id), json=self._body(f"{prefix}_body", profile_id))
        self._check_success(data)
        return data

    def _check_success(self, data: Any) -> None:
        path = (self.extra.get("success_path") or "").strip()
        if not path:
            return
        if path == "auto":  # generic: {"success": false}, {"code": 5, "msg": ...}, {"error": ...}
            failure = api_failure(data)
            if failure:
                raise AdapterError(f"The browser API reported a failure: {failure[:300]}")
            return
        value = json_path(data, path)
        if str(value) != str(self.extra.get("success_value", "")):
            message = json_path(data, "msg") or json_path(data, "message") or json_path(data, "error") or data
            raise AdapterError(f"The browser API reported a failure: {str(message)[:300]}")

    # -- adapter API ---------------------------------------------------------------
    async def test_connection(self) -> ConnectionResult:
        if self._known is not None:
            return await self._known.test_connection()
        if not self.extra.get("list_url"):
            try:
                await self.http.request("GET", "/")
            except AdapterError as exc:
                return ConnectionResult(False, str(exc))
            return ConnectionResult(True, "The API address answered.")
        return await super().test_connection()

    async def list_profiles(self) -> list[RemoteProfile]:
        if self._known is not None:
            return await self._known.list_profiles()
        data = await self._call("list")
        items = json_path(data, self.extra.get("list_items_path") or "") if self.extra.get("list_items_path") else data
        if not isinstance(items, list):
            raise AdapterError("Could not find the profile list in the answer. Check 'JSON path to the profile list'.")
        result = []
        for it in items:
            pid = json_path(it, self.extra.get("list_id_path") or "id")
            if pid is None:
                continue
            name = json_path(it, self.extra.get("list_name_path") or "name") or pid
            group = json_path(it, self.extra.get("list_group_path") or "") if self.extra.get("list_group_path") else ""
            result.append(RemoteProfile(id=str(pid), name=str(name), group=str(group or "")))
        return result

    async def start_profile(self, profile_id: str) -> str:
        if self._known is not None:
            return await self._known.start_profile(profile_id)
        data = await self._call("start", profile_id)
        ws_path = (self.extra.get("ws_path") or "").strip()
        if ws_path in ("", "auto"):
            host = self.extra.get("host") or "127.0.0.1"
            found = find_cdp(data, host)
            if found:
                return found
            if await looks_like_stealth(self.base_url, self.api_key, self._transport):
                # This address opened the profile for normal use (e.g. Stealth Browser's own internal
                # route, which answers only {"pid": …}). Use its automation API instead: it closes the
                # profile and opens it again for automation.
                logger.info("Start answer had no debugging address; switching {} to Stealth Browser's "
                            "automation API", self.base_url)
                self._switch_to_known()
                assert self._known is not None
                return await self._known.start_profile(profile_id)
            pid = find_pid(data)
            if pid is None:
                self._expect(False, "The browser started the profile but FlowPilot could not find its debugging "
                             "address (ws://… or a port) in the answer.", data)
            return await self._address_from_process(pid, host)  # type: ignore[arg-type]
        value = json_path(data, ws_path)
        self._expect(value not in (None, ""), "Could not find the debugging address in the start answer. "
                     "Check 'JSON path to WebSocket/CDP address'.", data)
        text = str(value)
        if text.isdigit():
            return f"http://{self.extra.get('host') or '127.0.0.1'}:{text}"
        if text.startswith(("ws://", "wss://", "http://", "https://")):
            return text
        return f"ws://{text}"  # e.g. "127.0.0.1:9222/devtools/browser/..."

    async def _address_from_process(self, pid: int, host: str) -> str:
        """The start answer only had a process id: read the debugging port from that process."""
        address, scan = await locate_by_pid(pid, host)
        if address:
            return address
        if not scan.found:
            raise AdapterError(f"The browser started the profile as process {pid}, but that process is not running "
                               "on this PC, so FlowPilot cannot find its debugging port. If the browser runs on "
                               "another computer, its API must return the debugging address (ws://… or a port).")
        if scan.pipe_only:
            raise AdapterError(f"The profile is open (process {pid}), but your browser started it in a private "
                               "automation mode (--remote-debugging-pipe) that no other program can join. In the "
                               "browser's settings turn on “remote debugging” / “CDP port” / “automation port”, "
                               "then try again.")
        raise AdapterError(f"The profile is open (process {pid}), but it was started without a debugging port, so "
                           "FlowPilot cannot control it. In your anti-detect browser's settings turn on “remote "
                           "debugging” / “CDP port” / “automation” (the name differs per browser) and try again. "
                           "If you build this browser yourself: start Chrome with --remote-debugging-port=0 (or a "
                           "fixed port) and return the port or ws:// address in the start answer.")

    async def stop_profile(self, profile_id: str) -> None:
        if self._known is not None:
            return await self._known.stop_profile(profile_id)
        if self.extra.get("stop_url"):
            await self._call("stop", profile_id)

    async def is_running(self, profile_id: str) -> bool:
        if self._known is not None:
            return await self._known.is_running(profile_id)
        if not self.extra.get("status_url"):
            return False
        data = await self._call("status", profile_id)
        value = json_path(data, self.extra.get("status_path") or "")
        return str(value).lower() == str(self.extra.get("status_running_value", "")).lower()
