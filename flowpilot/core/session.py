"""BrowserSession: open a profile through its adapter and attach Playwright over CDP.

Used by the run engine, the recorder and the editor's test buttons. :meth:`close` is safe
to call many times and always tries both steps (disconnect Playwright, stop the profile).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger

from flowpilot.adapters.base import AdapterError, BrowserAdapter
from flowpilot.adapters.stealth import FIREFOX_PREFIX

PlaywrightGetter = Callable[[], Any]


class PlaywrightHolder:
    """Starts the Playwright driver once and shares it."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._cm: Any = None
        self._lock = asyncio.Lock()

    async def get(self) -> Any:
        async with self._lock:
            if self._pw is None:
                from playwright.async_api import async_playwright

                self._cm = async_playwright()
                self._pw = await self._cm.start()
            return self._pw

    async def stop(self) -> None:
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as exc:  # pragma: no cover
                logger.debug("Playwright stop failed: {}", exc)
            self._pw = None


class BrowserSession:
    """One open profile with a Playwright connection."""

    def __init__(self, adapter: BrowserAdapter, profile_id: str, playwright: PlaywrightHolder,
                 auto_switch_tabs: bool = True, dialog_action: str = "accept") -> None:
        self.adapter = adapter
        self.profile_id = profile_id
        self._pw = playwright
        self.auto_switch_tabs = auto_switch_tabs
        self.dialog_action = dialog_action
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.started = False
        self.closed = False
        self.disconnected = False
        self.on_disconnect: Callable[[], None] | None = None
        self.on_new_page: Callable[[Any], None] | None = None

    async def open(self, connect_timeout_s: float = 30.0) -> Any:
        """Start the profile and connect. Returns the active page."""
        endpoint = await self.adapter.start_profile(self.profile_id)
        self.started = True
        pw = await self._pw.get()
        last_exc: Exception | None = None
        firefox = endpoint.startswith(FIREFOX_PREFIX)  # e.g. Stealth Browser's Camoufox profiles
        for _ in range(3):  # the browser can take a moment before accepting CDP connections
            try:
                if firefox:
                    self.browser = await pw.firefox.connect(endpoint[len(FIREFOX_PREFIX):],
                                                            timeout=connect_timeout_s * 1000)
                else:
                    self.browser = await pw.chromium.connect_over_cdp(endpoint, timeout=connect_timeout_s * 1000)
                break
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(1.5)
        if self.browser is None:
            await self.close()
            raise AdapterError(f"Could not attach to the browser profile ({str(last_exc).splitlines()[0][:150]}).")
        self.browser.on("disconnected", lambda *_: self._handle_disconnect())
        self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        self.context.on("page", self._handle_new_page)
        pages = [p for p in self.context.pages if not p.is_closed()]
        # Anti-detect browsers sometimes open extension/background pages first; prefer a normal tab.
        normal = [p for p in pages if not p.url.startswith(("chrome-extension://", "devtools://"))]
        self.page = normal[0] if normal else (pages[0] if pages else await self.context.new_page())
        for p in pages:
            self._attach_dialog_handler(p)
        return self.page

    def _handle_disconnect(self) -> None:
        self.disconnected = True
        if self.on_disconnect and not self.closed:
            self.on_disconnect()

    def _attach_dialog_handler(self, page: Any) -> None:
        async def on_dialog(dialog: Any) -> None:
            try:
                if self.dialog_action == "dismiss":
                    await dialog.dismiss()
                else:
                    await dialog.accept()
            except Exception:
                pass

        page.on("dialog", on_dialog)

    def _handle_new_page(self, page: Any) -> None:
        self._attach_dialog_handler(page)
        if self.auto_switch_tabs:
            self.page = page
        if self.on_new_page:
            self.on_new_page(page)

    async def bring_to_front(self) -> None:
        if self.page is not None and not self.page.is_closed():
            await self.page.bring_to_front()

    async def close(self, stop_profile: bool = True) -> None:
        """Disconnect Playwright and (optionally) stop the profile. Never raises."""
        if self.closed:
            return
        self.closed = True
        if self.browser is not None:
            try:
                await asyncio.wait_for(self.browser.close(), timeout=10)
            except Exception as exc:
                logger.debug("Closing CDP connection failed: {}", exc)
        if stop_profile and self.started:
            for attempt in range(3):
                try:
                    await asyncio.wait_for(self.adapter.stop_profile(self.profile_id), timeout=30)
                    break
                except Exception as exc:
                    logger.warning("Stopping profile {} failed (attempt {}): {}", self.profile_id, attempt + 1, exc)
                    await asyncio.sleep(1.0)
