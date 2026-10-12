"""GoLogin adapter.

* Profiles are listed from the GoLogin **cloud API** (``https://api.gologin.com``) with the
  API token from GoLogin → Settings → API.
* Profiles are started/stopped through the **desktop app's local API**
  (default ``http://localhost:36912``), which returns a ``wsUrl`` for CDP.
Docs: https://gologin.com/docs/api-reference/introduction
"""

from __future__ import annotations

from typing import Any

from flowpilot.adapters.base import BaseHttpAdapter, ExtraField, HttpClient, RemoteProfile

# --- endpoints --------------------------------------------------------------------------
CLOUD_URL = "https://api.gologin.com"
EP_LIST = "/browser/v2"  # cloud, ?page=N
EP_START = "/browser/start-profile"  # local, POST {"profileId", "sync"}
EP_STOP = "/browser/stop-profile"  # local, POST {"profileId"}
WS_KEY = "wsUrl"


class GoLoginAdapter(BaseHttpAdapter):
    display_name = "GoLogin"
    default_base_url = "http://localhost:36912"
    api_key_help = "Your GoLogin API token (GoLogin → Settings → API). Needed to list profiles."
    extra_fields = [ExtraField("cloud_url", "Cloud API URL", "Used to list profiles", CLOUD_URL)]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cloud = HttpClient(self.extra.get("cloud_url") or CLOUD_URL, headers=self._auth_headers(),
                                transport=self._transport)

    async def aclose(self) -> None:
        await super().aclose()
        await self.cloud.aclose()

    async def list_profiles(self) -> list[RemoteProfile]:
        profiles: list[RemoteProfile] = []
        page = 1
        while True:
            data = await self.cloud.request("GET", EP_LIST, params={"page": page})
            items = data.get("profiles", []) if isinstance(data, dict) else []
            for it in items:
                folders = it.get("folders") or []
                profiles.append(
                    RemoteProfile(
                        id=str(it.get("id", "")),
                        name=str(it.get("name") or it.get("id")),
                        group=str(folders[0]) if folders else "",
                        tags=[str(t.get("title", t)) if isinstance(t, dict) else str(t) for t in it.get("tags") or []],
                        notes=str(it.get("notes") or ""),
                    )
                )
            total = data.get("allProfilesCount", 0) if isinstance(data, dict) else 0
            if not items or len(profiles) >= total:
                return profiles
            page += 1

    async def start_profile(self, profile_id: str) -> str:
        data = await self.http.request("POST", EP_START, json={"profileId": profile_id, "sync": True}, timeout=120)
        ws = data.get(WS_KEY) if isinstance(data, dict) else None
        self._expect(bool(ws), "GoLogin did not return a debugging address. Is the GoLogin app running?", data)
        return str(ws)

    async def stop_profile(self, profile_id: str) -> None:
        await self.http.request("POST", EP_STOP, json={"profileId": profile_id})
