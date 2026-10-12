"""Run control (pause/stop) and the context object handed to every step."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from flowpilot.config import AppSettings
from flowpilot.core.errors import BrowserDisconnected, ElementNotFound, StopRequested
from flowpilot.core.humanize import Humanizer
from flowpilot.core.selectors import resolve_locator
from flowpilot.core.variables import Variables

if TYPE_CHECKING:
    from flowpilot.core.steps.base import StepBase


class RunControl:
    """Cooperative pause / stop switch.

    Steps call :meth:`checkpoint` between actions and use :meth:`sleep` for waits, so a
    pause or stop takes effect within a fraction of a second. A control may have a parent
    (the global batch control); pausing or stopping the parent affects all children.
    """

    def __init__(self, parent: RunControl | None = None) -> None:
        self.parent = parent
        self._running = asyncio.Event()
        self._running.set()
        self.stop_requested = False
        self.force = False
        self.manual_message = ""  # set while waiting for the user ("Pause for manual action")
        self._manual = asyncio.Event()
        self._manual.set()
        #: monotonic time of the last checkpoint; the engine's watchdog uses it to find stuck runs
        self.last_activity = time.monotonic()

    # -- state ------------------------------------------------------------------------
    @property
    def waiting_manual(self) -> bool:
        return not self._manual.is_set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set() or (self.parent is not None and self.parent.paused)

    @property
    def stopping(self) -> bool:
        return self.stop_requested or (self.parent is not None and self.parent.stopping)

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()
        self._manual.set()

    def stop(self, force: bool = False) -> None:
        self.stop_requested = True
        self.force = self.force or force
        self._running.set()
        self._manual.set()

    # -- used by steps -------------------------------------------------------------------
    async def checkpoint(self) -> None:
        """Raise :class:`StopRequested` if stopping; block while paused."""
        while True:
            self.last_activity = time.monotonic()
            if self.stopping:
                raise StopRequested()
            if not self.paused:
                return
            await asyncio.sleep(0.2)

    async def sleep(self, seconds: float) -> None:
        """Sleep in small slices, honouring pause (time does not count while paused) and stop."""
        loop = asyncio.get_running_loop()
        remaining = max(0.0, seconds)
        while remaining > 0:
            await self.checkpoint()
            chunk = min(0.2, remaining)
            start = loop.time()
            await asyncio.sleep(chunk)
            remaining -= loop.time() - start
        await self.checkpoint()

    async def wait_manual(self, message: str) -> None:
        """Block until the user presses Continue (resume) on the run card."""
        self.manual_message = message
        self._manual.clear()
        try:
            while not self._manual.is_set():
                self.last_activity = time.monotonic()
                if self.stopping:
                    raise StopRequested()
                await asyncio.sleep(0.2)
        finally:
            self.manual_message = ""


LogFn = Callable[[str, str], None]  # (level, message)


@dataclass
class StepContext:
    """Everything a step needs while it runs on one account."""

    settings: AppSettings
    variables: Variables
    control: RunControl
    humanizer: Humanizer
    page: Any = None
    browser_context: Any = None
    browser: Any = None
    test_mode: bool = False
    account_name: str = ""
    account_position: int = 0
    flow_name: str = ""
    artifacts_dir: Path = field(default_factory=lambda: Path("."))
    exports_dir: Path = field(default_factory=lambda: Path("."))
    log: LogFn = field(default=lambda level, msg: logger.log(level.upper(), msg))
    #: shared between all accounts of a batch (CSV cursors, ...)
    shared: dict[str, Any] = field(default_factory=dict)
    shared_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: values saved with 'Save value' steps
    results: dict[str, Any] = field(default_factory=dict)
    screenshots: list[str] = field(default_factory=list)
    #: set by 'Mark account as done/failed'
    forced_status: str | None = None
    forced_message: str = ""
    browser_closed: bool = False
    disconnected: bool = False
    close_browser_cb: Callable[[], Awaitable[None]] | None = None
    screenshot_cb: Callable[[str], None] | None = None
    notify_cb: Callable[[str], None] | None = None
    #: called with (step, HealResult) when a selector was self-healed
    heal_cb: Callable[[Any, Any], None] | None = None
    healed: list[tuple[Any, Any]] = field(default_factory=list)

    # -- defaults ------------------------------------------------------------------
    def timeout_for(self, step: StepBase) -> float:
        base = step.timeout if step.timeout is not None else self.settings.step_timeout_s
        return float(base) * (2 if self.test_mode else 1)

    def humanize_for(self, step: StepBase) -> bool:
        if step.humanize == "on":
            return True
        if step.humanize == "off":
            return False
        return self.settings.humanize.mouse_enabled

    def render(self, value: Any) -> Any:
        return self.variables.render(value)

    # -- page helpers ------------------------------------------------------------------
    def require_page(self) -> Any:
        if self.browser_closed:
            raise BrowserDisconnected("The browser was already closed by an earlier step.")
        if self.disconnected:
            raise BrowserDisconnected("The browser was closed or disconnected.")
        if self.page is None or self.page.is_closed():
            pages = [p for p in (self.browser_context.pages if self.browser_context else []) if not p.is_closed()]
            if not pages:
                raise BrowserDisconnected("There is no open tab in the browser.")
            self.page = pages[-1]
        return self.page

    async def locate(self, step: StepBase, state: str = "visible", selector: str | None = None,
                     fallbacks: list[str] | None = None) -> Any:
        """Find the element for ``step`` using its selector and fallbacks."""
        page = self.require_page()
        primary = selector if selector is not None else step.selector
        extra = fallbacks if fallbacks is not None else step.fallbacks
        candidates = [self.render(s) for s in [primary, *extra] if s]
        try:
            locator, _ = await resolve_locator(page, candidates, self.timeout_for(step), state=state,
                                               check=self.control.checkpoint)
        except ElementNotFound:
            # Self-healing: only for the step's own element, never for a second target
            # (e.g. drag & drop's drop zone) and never when variables changed the selector.
            if not (self.settings.self_heal and step.element_fp and selector is None):
                raise
            from flowpilot.core.healing import heal_locator

            healed = await heal_locator(page, step.element_fp)
            if healed is None:
                raise
            locator, result = healed
            self.healed.append((step, result))
            self.log("warning", f"Self-healed “{step.summary()}”: the element was found again as {result.selector} "
                                f"(confidence {result.score:g}). Review it in the Flows page.")
            if self.heal_cb:
                self.heal_cb(step, result)
        if self.test_mode:
            await self.highlight(locator)
        return locator

    async def highlight(self, locator: Any, color: str = "#7C6CF2") -> None:
        """Briefly outline an element (test runs) so the user sees what is being used."""
        try:
            await locator.evaluate(
                """(el, color) => {
                    const prev = el.style.outline, prevOff = el.style.outlineOffset;
                    el.style.outline = `3px solid ${color}`; el.style.outlineOffset = '2px';
                    setTimeout(() => { el.style.outline = prev; el.style.outlineOffset = prevOff; }, 900);
                }""",
                color,
            )
            await self.control.sleep(0.4)
        except Exception:
            pass

    async def pause_between(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.page is not None and not self.browser_closed and not self.disconnected:
            await self.humanizer.idle(self.page, seconds, self.control.sleep)
        else:
            await self.control.sleep(seconds)
