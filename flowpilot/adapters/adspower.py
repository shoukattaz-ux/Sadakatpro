"""AdsPower local API adapter.

Docs: https://localapi-doc-en.adspower.com/ (Local API v1).
The local API listens on ``http://local.adspower.net:50325`` (or ``http://127.0.0.1:50325``).
When "API security verification" is enabled in AdsPower, the API key is sent as a Bearer
token. Responses look like ``{"code": 0, "msg": "success", "data": {...}}``.
AdsPower rate-limits the local API (about 1 request/second), so throttled calls are retried.
"""

from __future__ import annotations

import asyncio
from typing import Any

from flowpilot.adapters.base import AdapterError, BaseHttpAdapter, ConnectionResult, ExtraField, RemoteProfile

# --- endpoints (single place to fix if the vendor changes them) --------------------------
EP_STATUS = "/status"
EP_START = "/api/v1/browser/start"  # ?user_id=...
EP_STOP = "/api/v1/browser/stop"  # ?user_id=...
EP_ACTIVE = "/api/v1/browser/active"  # ?user_id=...
EP_LIST = "/api/v1/user/list"  # ?page=1&page_size=100
WS_PATH = "data.ws.puppeteer"
PAGE_SIZE = 100
RATE_LIMIT_RETRIES = 5


class AdsPowerAdapter(BaseHttpAdapter):
    display_name = "AdsPower"
    default_base_url = "http://local.adspower.net:50325"
    api_key_help = "Optional. Only needed when 'API security verification' is turned on in AdsPower → API."
    extra_fields = [
        ExtraField("open_tabs", "Open saved tabs on start", "1 = open the profile's start tabs, 0 = only a blank tab", "0"),
        ExtraField("launch_args", "Extra launch args (JSON list)", 'Example: ["--window-size=1280,800"]', ""),
    ]

    async def _call(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Call the API and unwrap ``data``; retries AdsPower's 'too many requests' answer."""
        for attempt in range(RATE_LIMIT_RETRIES):
            payload = await self.http.request("GET", path, params=params)
            if not isinstance(payload, dict):
                raise AdapterError(f"AdsPower answered with unexpected data: {str(payload)[:200]}")
            if payload.get("code") == 0:
                return payload.get("data")
            msg = str(payload.get("msg", ""))
            if "too many" in msg.lower() and attempt < RATE_LIMIT_RETRIES - 1:
                await asyncio.sleep(1.1 * (attempt + 1))
                continue
            raise AdapterError(f"AdsPower: {msg or 'unknown error'}")
        raise AdapterError("AdsPower is busy (rate limit). Try a longer launch delay.")

    async def test_connection(self) -> ConnectionResult:
        try:
            await self.http.request("GET", EP_STATUS)
        except AdapterError as exc:
            return ConnectionResult(False, str(exc))
        return await super().test_connection()

    async def list_profiles(self) -> list[RemoteProfile]:
        profiles: list[RemoteProfile] = []
        page = 1
        while True:
            data = await self._call(EP_LIST, {"page": page, "page_size": PAGE_SIZE})
            items = (data or {}).get("list") or []
            for it in items:
                profiles.append(
                    RemoteProfile(
                        id=str(it.get("user_id", "")),
                        name=str(it.get("name") or it.get("serial_number") or it.get("user_id")),
                        group=str(it.get("group_name") or ""),
                        notes=str(it.get("remark") or ""),
                    )
                )
            if len(items) < PAGE_SIZE:
                return profiles
            page += 1
            await asyncio.sleep(1.0)  # respect the local API rate limit

    async def start_profile(self, profile_id: str) -> str:
        params: dict[str, Any] = {"user_id": profile_id, "open_tabs": self.extra.get("open_tabs") or "0"}
        if self.extra.get("launch_args"):
            params["launch_args"] = self.extra["launch_args"]
        data = await self._call(EP_START, params)
        ws = (((data or {}).get("ws") or {}).get("puppeteer")) if isinstance(data, dict) else None
        self._expect(bool(ws), "AdsPower started the profile but did not return a debugging address.", data)
        return str(ws)

    async def stop_profile(self, profile_id: str) -> None:
        await self._call(EP_STOP, {"user_id": profile_id})

    async def is_running(self, profile_id: str) -> bool:
        data = await self._call(EP_ACTIVE, {"user_id": profile_id})
        return isinstance(data, dict) and str(data.get("status", "")).lower() == "active"
