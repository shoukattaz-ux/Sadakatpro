"""Anti-detect browser adapters and the factory that builds them from a saved connection."""

from __future__ import annotations

from typing import Any

from flowpilot.adapters.adspower import AdsPowerAdapter
from flowpilot.adapters.base import AdapterError, BrowserAdapter, ConnectionResult, ExtraField, RemoteProfile
from flowpilot.adapters.custom import CustomAdapter
from flowpilot.adapters.dolphin import DolphinAdapter
from flowpilot.adapters.gologin import GoLoginAdapter
from flowpilot.adapters.local import LocalChromeAdapter
from flowpilot.adapters.multilogin import MultiloginAdapter
from flowpilot.adapters.octo import OctoAdapter

#: connection ``type`` value -> adapter class
ADAPTERS: dict[str, Any] = {
    "adspower": AdsPowerAdapter,
    "gologin": GoLoginAdapter,
    "dolphin": DolphinAdapter,
    "multilogin": MultiloginAdapter,
    "octo": OctoAdapter,
    "custom": CustomAdapter,
    "local": LocalChromeAdapter,
}


def create_adapter(conn_type: str, base_url: str, api_key: str, extra: dict[str, Any] | None = None) -> BrowserAdapter:
    """Build an adapter instance; raises :class:`AdapterError` for an unknown type."""
    cls = ADAPTERS.get(conn_type)
    if cls is None:
        raise AdapterError(f"Unknown browser type '{conn_type}'")
    return cls(base_url=base_url, api_key=api_key, extra=extra or {})


__all__ = [
    "ADAPTERS",
    "AdapterError",
    "BrowserAdapter",
    "ConnectionResult",
    "ExtraField",
    "RemoteProfile",
    "create_adapter",
]
