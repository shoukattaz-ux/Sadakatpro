"""Keeps one adapter instance per saved connection (some adapters hold state, e.g. the
Local Chrome adapter tracks its browser processes)."""

from __future__ import annotations

import asyncio
import json

from loguru import logger

from flowpilot.adapters import BrowserAdapter, create_adapter
from flowpilot.storage.models import Connection
from flowpilot.storage.repositories import ConnectionRepo


class AdapterPool:
    def __init__(self, connections: ConnectionRepo) -> None:
        self.connections = connections
        self._adapters: dict[int, tuple[str, BrowserAdapter]] = {}

    @staticmethod
    def _fingerprint(conn: Connection) -> str:
        return f"{conn.type}|{conn.base_url}|{conn.api_key_encrypted}|{conn.extra_json}"

    def get(self, conn: Connection) -> BrowserAdapter:
        """Return the adapter for ``conn``, rebuilding it if the connection was edited."""
        fp = self._fingerprint(conn)
        cached = self._adapters.get(conn.id)
        if cached and cached[0] == fp:
            return cached[1]
        adapter = create_adapter(conn.type, conn.base_url, self.connections.api_key(conn), self.connections.extra(conn))
        if cached:  # connection was edited: release the old client in the background
            try:
                asyncio.get_running_loop().create_task(cached[1].aclose())
            except RuntimeError:
                pass
        self._adapters[conn.id] = (fp, adapter)
        if hasattr(adapter, "on_detected"):
            adapter.on_detected = lambda settings, cid=conn.id: self._store_detected(cid, settings)
        return adapter

    def _store_detected(self, conn_id: int, settings: dict) -> None:
        """Save endpoints found by an auto-detect connection, keeping the live adapter."""
        conn = self.connections.get(conn_id)
        cached = self._adapters.get(conn_id)
        if conn is None or not settings:
            return
        conn.extra_json = json.dumps(settings)
        conn = self.connections.save(conn)
        if cached:
            self._adapters[conn_id] = (self._fingerprint(conn), cached[1])

    def build_unpooled(self, conn: Connection) -> BrowserAdapter:
        """A fresh adapter (e.g. for 'Test connection' with unsaved values)."""
        return create_adapter(conn.type, conn.base_url, self.connections.api_key(conn), self.connections.extra(conn))

    async def aclose(self) -> None:
        for _, adapter in self._adapters.values():
            try:
                await adapter.aclose()
            except Exception as exc:  # pragma: no cover
                logger.debug("adapter close failed: {}", exc)
        self._adapters.clear()
