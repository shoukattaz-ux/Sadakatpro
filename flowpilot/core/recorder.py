"""Recorder: turns the user's actions in a real browser profile into editable steps.

The JavaScript in ``recorder.js`` is injected with ``add_init_script`` (new documents)
and ``evaluate`` (already open pages) and reports events through ``expose_binding``.
Each event becomes a step that is appended live to the editor. Typing is reported once
per field; password fields are recorded as ``{{account.password}}``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from flowpilot.core.selectors import candidates_from_descriptor, convert_devtools_selector
from flowpilot.core.session import BrowserSession
from flowpilot.core.steps import StepBase, new_step

RECORDER_JS = (Path(__file__).parent / "recorder.js").read_text("utf-8")
BINDING = "__flowpilotRecord"
NAV_AFTER_ACTION_S = 2.5  # a navigation this soon after a click/Enter is caused by it -> not recorded

#: called with (step, replace_last)
StepCallback = Callable[[StepBase, bool], None]


def _selectors(desc: dict[str, Any]) -> tuple[str, list[str]]:
    cands = candidates_from_descriptor(desc, max_candidates=4)
    return (cands[0] if cands else ""), cands[1:]


def event_to_step(event: dict[str, Any], last: StepBase | None = None) -> tuple[StepBase | None, bool]:
    """Convert one recorder event into ``(step, replace_last)``. Pure function (tested)."""
    kind = event.get("kind")
    desc = event.get("desc") or {}
    selector, fallbacks = _selectors(desc) if desc else ("", [])
    common = {"selector": selector, "fallbacks": fallbacks}
    if kind == "click":
        if event.get("button") == 2:
            return new_step("right_click", **common), False
        return new_step("click", **common), False
    if kind == "dblclick":
        # The browser also sent a click just before: turn that into the double click.
        if last is not None and last.type == "click" and last.selector == selector:
            return new_step("double_click", **common), True
        return new_step("double_click", **common), False
    if kind == "right_click":
        return new_step("right_click", **common), False
    if kind == "type":
        value = str(event.get("value", ""))
        is_password = desc.get("inputType") == "password"
        text = "{{account.password}}" if is_password else value
        step = new_step("type_text", text=text, sensitive=is_password, **common)
        if last is not None and last.type == "type_text" and last.selector == selector:
            return step, True
        return step, False
    if kind == "press":
        key = str(event.get("key", "Enter"))
        if desc:
            # Enter in a field we just typed into -> simply tick "press Enter" on that step.
            if key == "Enter" and last is not None and last.type == "type_text" and last.selector == selector:
                merged = last.model_copy(update={"press_enter": True})
                return merged, True
            return new_step("press_key", key=key, **common), False
        return new_step("press_key", key=key), False
    if kind == "select":
        label = str(event.get("label") or "")
        if label:
            return new_step("select_option", by="label", value=label, **common), False
        return new_step("select_option", by="value", value=str(event.get("value", "")), **common), False
    if kind == "check":
        return new_step("check" if event.get("checked") else "uncheck", **common), False
    if kind == "upload":
        names = [str(n) for n in event.get("names") or []]
        return new_step("upload_file", files=names, note="Recorded file names only — set the full paths.",
                        **common), False
    return None, False


def cleanup_steps(steps: list[StepBase]) -> list[StepBase]:
    """Tidy a recording: drop repeated clicks, merge typing into one step per field, drop a
    click right before typing into the same field, and repeated Open URL steps."""
    out: list[StepBase] = []
    for step in steps:
        prev = out[-1] if out else None
        if prev is not None:
            same_target = bool(step.selector) and prev.selector == step.selector
            if step.type == prev.type == "click" and same_target:
                continue
            if step.type == prev.type == "type_text" and same_target:
                out[-1] = step
                continue
            if prev.type == "click" and step.type in ("type_text", "check", "uncheck", "select_option") and same_target:
                out[-1] = step
                continue
            if step.type == prev.type == "open_url" and getattr(step, "url", None) == getattr(prev, "url", None):
                continue
        out.append(step)
    return out


class Recorder:
    """Records actions in an open :class:`BrowserSession`."""

    def __init__(self, session: BrowserSession, on_step: StepCallback,
                 on_status: Callable[[str], None] | None = None) -> None:
        self.session = session
        self.on_step = on_step
        self.on_status = on_status or (lambda msg: None)
        self.recording = False
        self.paused = False
        self.last_step: StepBase | None = None
        self._last_action = 0.0
        self._last_url = ""
        self._pick_future: asyncio.Future[dict[str, Any]] | None = None
        self._watched_pages: set[int] = set()

    # -- setup ------------------------------------------------------------------------
    async def install(self) -> None:
        """Expose the binding and inject the script (once per browser context)."""
        ctx = self.session.context
        if not getattr(ctx, "_flowpilot_recorder", False):
            await ctx.expose_binding(BINDING, self._binding)
            await ctx.add_init_script(RECORDER_JS)
            ctx._flowpilot_recorder = True  # noqa: SLF001 - marker on the Playwright object
        ctx._flowpilot_active_recorder = self  # noqa: SLF001
        for page in ctx.pages:
            await self._prepare_page(page)
        ctx.on("page", lambda p: asyncio.ensure_future(self._prepare_page(p)))

    async def _prepare_page(self, page: Any) -> None:
        if id(page) not in self._watched_pages:
            self._watched_pages.add(id(page))
            page.on("framenavigated", lambda frame, p=page: self._on_navigated(p, frame))
        try:
            await page.evaluate(RECORDER_JS)
            await page.evaluate(f"() => {{ window.__fpPaused = {json.dumps(not self.recording or self.paused)}; }}")
        except Exception as exc:  # page navigating / closed
            logger.debug("recorder inject skipped: {}", exc)

    @staticmethod
    async def _binding(source: dict[str, Any], event: dict[str, Any]) -> None:
        page = source.get("page")
        ctx = page.context if page is not None else None
        recorder: Recorder | None = getattr(ctx, "_flowpilot_active_recorder", None)
        if recorder is not None:
            recorder.handle_event(event, page)

    # -- recording ------------------------------------------------------------------------
    async def start(self, add_open_url: bool = True) -> None:
        self.recording = True
        self.paused = False
        await self.install()
        page = self.session.page
        if add_open_url and page is not None and page.url and not page.url.startswith(("about:", "chrome:")):
            self._emit(new_step("open_url", url=page.url))
            self._last_url = page.url
        await self._set_paused_flag()
        self.on_status("Recording… use the browser normally.")

    async def pause(self) -> None:
        self.paused = True
        await self._set_paused_flag()
        self.on_status("Recording paused.")

    async def resume(self) -> None:
        self.paused = False
        await self._set_paused_flag()
        self.on_status("Recording…")

    async def stop(self) -> None:
        self.recording = False
        await self._set_paused_flag()
        if self._pick_future and not self._pick_future.done():
            self._pick_future.cancel()
        self.on_status("Recording stopped.")

    async def _set_paused_flag(self) -> None:
        flag = json.dumps(not self.recording or self.paused)
        for page in list(self.session.context.pages if self.session.context else []):
            try:
                await page.evaluate(f"() => {{ window.__fpPaused = {flag}; }}")
            except Exception:
                pass

    def _emit(self, step: StepBase, replace_last: bool = False) -> None:
        self.last_step = step
        self.on_step(step, replace_last)

    def handle_event(self, event: dict[str, Any], page: Any = None) -> None:
        if event.get("kind") == "pick":
            if self._pick_future and not self._pick_future.done():
                self._pick_future.set_result(event.get("desc") or {})
            return
        if not self.recording or self.paused:
            return
        if page is not None and self.session.page is not page:
            # The user is working in another tab: record a tab switch first.
            self.session.page = page
            self._emit(new_step("switch_tab", by="url", value=page.url.split("?")[0][:120]))
        step, replace = event_to_step(event, self.last_step)
        if step is None:
            return
        self._last_action = time.monotonic()
        self._emit(step, replace)

    def _on_navigated(self, page: Any, frame: Any) -> None:
        if not self.recording or self.paused or frame != page.main_frame:
            return
        url = frame.url
        if not url or url.startswith(("about:", "chrome:", "chrome-error:")) or url == self._last_url:
            return
        self._last_url = url
        if time.monotonic() - self._last_action < NAV_AFTER_ACTION_S:
            return  # caused by the click/Enter we just recorded
        self._emit(new_step("open_url", url=url))

    # -- pick element -----------------------------------------------------------------------
    async def pick_element(self, timeout_s: float = 120) -> tuple[str, list[str]] | None:
        """Let the user click an element (highlighted on hover). Returns (selector, fallbacks)."""
        await self.install()
        loop = asyncio.get_running_loop()
        self._pick_future = loop.create_future()
        page = self.session.page
        await page.bring_to_front()
        for p in self.session.context.pages:
            try:
                await p.evaluate("() => window.__fpSetPick && window.__fpSetPick(true)")
            except Exception:
                pass
        self.on_status("Click the element in the browser…")
        try:
            desc = await asyncio.wait_for(self._pick_future, timeout_s)
        except (TimeoutError, asyncio.CancelledError):
            return None
        finally:
            for p in self.session.context.pages:
                try:
                    await p.evaluate("() => window.__fpSetPick && window.__fpSetPick(false)")
                except Exception:
                    pass
        cands = candidates_from_descriptor(desc, max_candidates=4)
        if not cands:
            return None
        try:  # confirm what was picked
            await page.locator(cands[0]).first.evaluate(
                "el => { const o = el.style.outline; el.style.outline = '3px solid #6BCB8B';"
                " setTimeout(() => el.style.outline = o, 1200); }")
        except Exception:
            pass
        return cands[0], cands[1:]


# --------------------------------------------------------------------------- DevTools import

def import_devtools_recording(data: dict[str, Any] | str) -> tuple[str, list[StepBase]]:
    """Convert a Chrome DevTools Recorder JSON export into ``(title, steps)``."""
    if isinstance(data, str):
        data = json.loads(data)
    title = str(data.get("title") or "Imported recording")
    steps: list[StepBase] = []
    for raw in data.get("steps") or []:
        kind = raw.get("type")
        sels = [s for s in (convert_devtools_selector(x) for x in raw.get("selectors") or []) if s]
        common: dict[str, Any] = {"selector": sels[0], "fallbacks": sels[1:4]} if sels else {}
        if kind == "navigate":
            steps.append(new_step("open_url", url=raw.get("url", "")))
        elif kind == "click" and common:
            button = raw.get("button")
            steps.append(new_step("right_click" if button == "secondary" else "click", **common))
        elif kind == "doubleClick" and common:
            steps.append(new_step("double_click", **common))
        elif kind == "hover" and common:
            steps.append(new_step("hover", **common))
        elif kind == "change" and common:
            steps.append(new_step("type_text", text=str(raw.get("value", "")), **common))
        elif kind == "keyDown":
            key = str(raw.get("key", ""))
            if key and key not in ("Shift", "Control", "Alt", "Meta"):
                steps.append(new_step("press_key", key=key))
        elif kind == "scroll":
            if common:
                steps.append(new_step("scroll", mode="to_element", **common))
            else:
                steps.append(new_step("scroll", mode="by_amount", amount=int(raw.get("y", 0) or 0)))
        elif kind == "waitForElement" and common:
            steps.append(new_step("wait_for_element", **common))
        elif kind == "close":
            steps.append(new_step("close_tab"))
        elif kind in ("setViewport", "keyUp", "emulateNetworkConditions"):
            continue
        else:
            steps.append(new_step("log", message=f"Imported step '{kind}' is not supported and was skipped.",
                                  level="warning", enabled=False))
    return title, cleanup_steps(steps)
