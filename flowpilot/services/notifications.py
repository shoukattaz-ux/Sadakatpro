"""Desktop and Telegram notifications for finished batches and manual-action requests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
from loguru import logger

from flowpilot.config import SettingsStore
from flowpilot.core.engine import EngineEvent, RunEngine

DesktopFn = Callable[[str, str], None]  # (title, message)


class Notifier:
    def __init__(self, settings: SettingsStore, desktop: DesktopFn | None = None) -> None:
        self.settings = settings
        self.desktop = desktop

    def attach(self, engine: RunEngine) -> None:
        engine.subscribe(self._on_event)

    def _on_event(self, event: EngineEvent) -> None:
        if event.kind == "run_finished":
            d = event.data
            ok, failed, total = d.get("succeeded", 0), d.get("failed", 0), d.get("total", 0)
            status = d.get("status", "done")
            title = "FlowPilot: batch finished" if status == "done" and not failed else "FlowPilot: batch finished with problems"
            if status == "stopped":
                title = "FlowPilot: batch stopped"
            msg = f"{d.get('flow_name', 'Flow')}: {ok}/{total} succeeded, {failed} failed."
            self.notify(title, msg)
        elif event.kind == "notify":
            self.notify("FlowPilot", str(event.data.get("message", "")))

    def notify(self, title: str, message: str) -> None:
        s = self.settings.settings
        if s.notify_desktop and self.desktop is not None:
            try:
                self.desktop(title, message)
            except Exception as exc:  # pragma: no cover
                logger.debug("desktop notification failed: {}", exc)
        if s.telegram_bot_token and s.telegram_chat_id:
            try:
                asyncio.get_running_loop().create_task(self.send_telegram(f"{title}\n{message}"))
            except RuntimeError:
                pass

    async def send_telegram(self, text: str, token: str | None = None, chat_id: str | None = None) -> bool:
        s = self.settings.settings
        token = token or s.telegram_bot_token
        chat_id = chat_id or s.telegram_chat_id
        if not token or not chat_id:
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                         json={"chat_id": chat_id, "text": text})
            if resp.status_code != 200:
                logger.warning("Telegram answered {}: {}", resp.status_code, resp.text[:200])
            return resp.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("Telegram notification failed: {}", exc)
            return False
