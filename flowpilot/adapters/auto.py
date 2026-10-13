"""'Any browser (auto-detect)': only the base URL and API key are needed.

On first use the API is discovered (see :mod:`flowpilot.adapters.discovery`) and the
result drives the regular :class:`CustomAdapter`. The detected settings are returned by
:meth:`detected_settings` so the connection editor can save them (no re-detection later).
"""

from __future__ import annotations

import asyncio
from typing import Any

from flowpilot.adapters.base import AdapterError, ConnectionResult, RemoteProfile
from flowpilot.adapters.custom import CustomAdapter
from flowpilot.adapters.discovery import discover


class AutoDetectAdapter(CustomAdapter):
    display_name = "Any browser (auto-detect)"
    default_base_url = "http://127.0.0.1:"
    api_key_help = "The API key shown in your browser's settings. FlowPilot finds the endpoints by itself."
    extra_fields: list = []  # nothing to fill in

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = asyncio.Lock()
        self.last_report = ""

    @property
    def is_detected(self) -> bool:
        return bool(self.extra.get("detected") and self.extra.get("start_url") and self.extra.get("list_url"))

    def detected_settings(self) -> dict[str, Any]:
        return dict(self.extra) if self.is_detected else {}

    async def ensure_detected(self, force: bool = False) -> None:
        async with self._lock:
            if self.is_detected and not force:
                return
            result = await discover(self.base_url, self.api_key, transport=self._transport)
            self.last_report = result.summary()
            if not result.ok:
                raise AdapterError("FlowPilot could not work out this browser's API automatically.\n"
                                   + result.summary()
                                   + "\nTip: if the browser shows its API documentation or endpoint list, choose "
                                     "the “Custom” type and enter the addresses there.")
            await self.http.aclose()
            self.extra.update(result.config, detected="1")
            self.http = self._make_client()

    async def test_connection(self) -> ConnectionResult:
        try:
            await self.ensure_detected(force=True)
            profiles = await super().list_profiles()
        except AdapterError as exc:
            return ConnectionResult(False, str(exc))
        return ConnectionResult(True, f"Connected — found {len(profiles)} profile(s).\n{self.last_report}")

    async def list_profiles(self) -> list[RemoteProfile]:
        await self.ensure_detected()
        return await super().list_profiles()

    async def start_profile(self, profile_id: str) -> str:
        await self.ensure_detected()
        return await super().start_profile(profile_id)

    async def stop_profile(self, profile_id: str) -> None:
        await self.ensure_detected()
        await super().stop_profile(profile_id)

    async def is_running(self, profile_id: str) -> bool:
        await self.ensure_detected()
        return await super().is_running(profile_id)
