"""Octo Browser adapter.

* Local client API (default ``http://localhost:58888``): ``POST /api/profiles/start``
  with ``{"uuid": ..., "debug_port": true}`` returns ``ws_endpoint``.
* Profiles are listed from the cloud API ``https://app.octobrowser.net/api/v2/automation/profiles``
  with the header ``X-Octo-Api-Token``.
Docs: https://documenter.getpostman.com/view/1801428/UVC6i6eA
"""

from __future__ import annotations

from typing import Any

from flowpilot.adapters.base import BaseHttpAdapter, ExtraField, HttpClient, RemoteProfile

# --- endpoints --------------------------------------------------------------------------
CLOUD_URL = "https://app.octobrowser.net"
EP_START = "/api/profiles/start"  # local, POST {"uuid", "headless", "debug_port"}
EP_STOP = "/api/profiles/stop"  # local, POST {"uuid"}
EP_ACTIVE = "/api/profiles/active"  # local, GET
EP_LIST = "/api/v2/automation/profiles"  # cloud, ?page=N&page_len=100
TOKEN_HEADER = "X-Octo-Api-Token"


class OctoAdapter(BaseHttpAdapter):
    display_name = "Octo Browser"
    default_base_url = "http://localhost:58888"
    api_key_help = "API token from Octo Browser → Settings → Additional → API token."
    extra_fields = [ExtraField("cloud_url", "Cloud API URL", "Used to list profiles", CLOUD_URL)]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cloud = HttpClient(self.extra.get("cloud_url") or CLOUD_URL, headers=self._auth_headers(),
                                transport=self._transport)

    def _auth_headers(self) -> dict[str, str]:
        return {TOKEN_HEADER: self.api_key} if self.api_key else {}

    async def aclose(self) -> None:
        await super().aclose()
        await self.cloud.aclose()

    async def list_profiles(self) -> list[RemoteProfile]:
        profiles: list[RemoteProfile] = []
        page = 0
        while True:
            data = await self.cloud.request("GET", EP_LIST, params={"page": page, "page_len": 100,
                                                                    "fields": "title,tags,description"})
            items = data.get("data", []) if isinstance(data, dict) else []
            for it in items:
                profiles.append(
                    RemoteProfile(
                        id=str(it.get("uuid", "")),
                        name=str(it.get("title") or it.get("uuid")),
                        tags=[str(t) for t in it.get("tags") or []],
                        notes=str(it.get("description") or ""),
                    )
                )
            if len(items) < 100:
                return profiles
            page += 1

    async def start_profile(self, profile_id: str) -> str:
        data = await self.http.request(
            "POST", EP_START, json={"uuid": profile_id, "headless": False, "debug_port": True}, timeout=120
        )
        ws = data.get("ws_endpoint") if isinstance(data, dict) else None
        self._expect(bool(ws), "Octo Browser did not return a debugging address.", data)
        return str(ws)

    async def stop_profile(self, profile_id: str) -> None:
        await self.http.request("POST", EP_STOP, json={"uuid": profile_id})

    async def is_running(self, profile_id: str) -> bool:
        data = await self.http.request("GET", EP_ACTIVE)
        items: list[Any] = data if isinstance(data, list) else []
        return any(isinstance(i, dict) and i.get("uuid") == profile_id for i in items)
