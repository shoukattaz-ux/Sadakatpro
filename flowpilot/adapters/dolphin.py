"""Dolphin {anty} adapter.

* Local API (default ``http://localhost:3001/v1.0``) starts/stops profiles. Start with
  ``?automation=1`` returns ``{"success": true, "automation": {"port": 1234, "wsEndpoint": "/devtools/browser/..."}}``.
* The local API must be authorised once per app start with ``POST /auth/login-with-token``.
* Profiles are listed from the cloud API ``https://dolphin-anty-api.com/browser_profiles``.
Docs: https://help.dolphin-anty.com/en/articles/7127390-basic-automation-dolphin-anty
"""

from __future__ import annotations

from typing import Any

from flowpilot.adapters.base import AdapterError, BaseHttpAdapter, ExtraField, HttpClient, RemoteProfile

# --- endpoints --------------------------------------------------------------------------
CLOUD_URL = "https://dolphin-anty-api.com"
EP_LOGIN = "/auth/login-with-token"  # local, POST {"token": ...}
EP_START = "/browser_profiles/{id}/start"  # local, ?automation=1
EP_STOP = "/browser_profiles/{id}/stop"  # local
EP_LIST = "/browser_profiles"  # cloud, ?limit=50&page=N


class DolphinAdapter(BaseHttpAdapter):
    display_name = "Dolphin Anty"
    default_base_url = "http://localhost:3001/v1.0"
    api_key_help = "API token from Dolphin Anty → Profile → API."
    extra_fields = [
        ExtraField("cloud_url", "Cloud API URL", "Used to list profiles", CLOUD_URL),
        ExtraField("host", "Debug host", "Host used to build the CDP address", "127.0.0.1"),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cloud = HttpClient(self.extra.get("cloud_url") or CLOUD_URL, headers=self._auth_headers(),
                                transport=self._transport)
        self._logged_in = False

    async def aclose(self) -> None:
        await super().aclose()
        await self.cloud.aclose()

    async def _login(self) -> None:
        if self._logged_in or not self.api_key:
            return
        data = await self.http.request("POST", EP_LOGIN, json={"token": self.api_key})
        if isinstance(data, dict) and data.get("success") is False:
            raise AdapterError("Dolphin Anty rejected the API token.")
        self._logged_in = True

    async def list_profiles(self) -> list[RemoteProfile]:
        profiles: list[RemoteProfile] = []
        page = 1
        while True:
            data = await self.cloud.request("GET", EP_LIST, params={"limit": 50, "page": page})
            items = data.get("data", []) if isinstance(data, dict) else []
            for it in items:
                profiles.append(
                    RemoteProfile(
                        id=str(it.get("id", "")),
                        name=str(it.get("name") or it.get("id")),
                        tags=[str(t) for t in it.get("tags") or []],
                        notes=str((it.get("notes") or {}).get("content") or "") if isinstance(it.get("notes"), dict) else "",
                    )
                )
            last_page = int(data.get("last_page", page)) if isinstance(data, dict) else page
            if not items or page >= last_page:
                return profiles
            page += 1

    async def start_profile(self, profile_id: str) -> str:
        await self._login()
        data = await self.http.request("GET", EP_START.format(id=profile_id), params={"automation": 1}, timeout=120)
        auto = data.get("automation") if isinstance(data, dict) else None
        self._expect(isinstance(auto, dict) and bool(auto.get("port")),
                     "Dolphin Anty did not return automation details.", data)
        host = self.extra.get("host") or "127.0.0.1"
        return f"ws://{host}:{auto['port']}{auto.get('wsEndpoint', '')}"  # type: ignore[index]

    async def stop_profile(self, profile_id: str) -> None:
        await self._login()
        await self.http.request("GET", EP_STOP.format(id=profile_id))
