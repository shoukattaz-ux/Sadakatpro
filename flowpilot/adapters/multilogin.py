"""Multilogin X adapter.

* The launcher (default ``https://launcher.mlx.yt:45001``) starts/stops profiles. With
  ``automation_type=playwright`` it returns ``{"data": {"port": "12345"}}``; Playwright
  connects with ``connect_over_cdp("http://127.0.0.1:12345")``.
* Profiles are listed via ``POST https://api.multilogin.com/profile/search``.
* Authentication: a Bearer token (Multilogin → Settings → API, or ``/user/signin``).
Docs: https://documenter.getpostman.com/view/28533318/2s946h9Cv9
"""

from __future__ import annotations

from typing import Any

from flowpilot.adapters.base import BaseHttpAdapter, ExtraField, HttpClient, RemoteProfile

# --- endpoints --------------------------------------------------------------------------
CLOUD_URL = "https://api.multilogin.com"
EP_START = "/api/v2/profile/f/{folder}/p/{id}/start"  # launcher, ?automation_type=playwright
EP_STOP = "/api/v1/profile/stop/p/{id}"  # launcher
EP_STATUS = "/api/v1/profile/statuses"  # launcher
EP_LIST = "/profile/search"  # cloud, POST


class MultiloginAdapter(BaseHttpAdapter):
    display_name = "Multilogin X"
    default_base_url = "https://launcher.mlx.yt:45001"
    api_key_help = "Bearer token from Multilogin X (Settings → API → generate token)."
    extra_fields = [
        ExtraField("folder_id", "Folder ID", "The Multilogin folder that contains your profiles", ""),
        ExtraField("cloud_url", "Cloud API URL", "Used to list profiles", CLOUD_URL),
        ExtraField("host", "Debug host", "Host used to build the CDP address", "127.0.0.1"),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cloud = HttpClient(self.extra.get("cloud_url") or CLOUD_URL, headers=self._auth_headers(),
                                transport=self._transport)

    def _make_client(self) -> HttpClient:
        # The launcher uses a certificate for launcher.mlx.yt that resolves to 127.0.0.1.
        return HttpClient(self.base_url, headers=self._auth_headers(), transport=self._transport, timeout=120)

    async def aclose(self) -> None:
        await super().aclose()
        await self.cloud.aclose()

    async def list_profiles(self) -> list[RemoteProfile]:
        profiles: list[RemoteProfile] = []
        offset, limit = 0, 100
        while True:
            body = {"is_removed": False, "limit": limit, "offset": offset, "search_text": "",
                    "storage_type": "all", "order_by": "created_at", "sort": "asc"}
            if self.extra.get("folder_id"):
                body["folder_id"] = self.extra["folder_id"]
            data = await self.cloud.request("POST", EP_LIST, json=body)
            items = ((data or {}).get("data") or {}).get("profiles") or [] if isinstance(data, dict) else []
            for it in items:
                profiles.append(
                    RemoteProfile(
                        id=str(it.get("id", "")),
                        name=str(it.get("name") or it.get("id")),
                        group=str(it.get("folder_id") or ""),
                        tags=[str(t) for t in it.get("tags") or []],
                        notes=str(it.get("notes") or ""),
                    )
                )
            if len(items) < limit:
                return profiles
            offset += limit

    async def start_profile(self, profile_id: str) -> str:
        folder = self.extra.get("folder_id") or ""
        self._expect(bool(folder), "Multilogin needs a Folder ID. Add it in Settings → Connections.")
        data = await self.http.request(
            "GET", EP_START.format(folder=folder, id=profile_id), params={"automation_type": "playwright"}
        )
        port = ((data or {}).get("data") or {}).get("port") if isinstance(data, dict) else None
        self._expect(bool(port), "Multilogin did not return a debugging port.", data)
        return f"http://{self.extra.get('host') or '127.0.0.1'}:{port}"

    async def stop_profile(self, profile_id: str) -> None:
        await self.http.request("GET", EP_STOP.format(id=profile_id))

    async def is_running(self, profile_id: str) -> bool:
        data = await self.http.request("GET", EP_STATUS)
        states = ((data or {}).get("data") or {}).get("states") or {} if isinstance(data, dict) else {}
        return profile_id in states
