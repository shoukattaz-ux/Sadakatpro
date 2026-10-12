"""Utility steps (screenshot, log, manual pause, JavaScript, notify) and ending/browser steps."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal

from pydantic import Field

from flowpilot.core.context import StepContext
from flowpilot.core.errors import FlowStopped
from flowpilot.core.steps.base import StepBase, register

CAT = "Utilities"
END = "Ending / Browser"


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_")[:60] or "shot"


@register
class ScreenshotStep(StepBase):
    type: Literal["screenshot"] = "screenshot"
    name: str = Field("", title="File name (optional)")
    full_page: bool = Field(False, title="Whole page (not only the visible part)")
    TITLE = "Take screenshot"
    CATEGORY = CAT
    ICON = "camera"

    def summary(self) -> str:
        return "Take a screenshot" + (" of the whole page" if self.full_page else "")

    async def run(self, ctx: StepContext) -> None:
        page = ctx.require_page()
        name = _safe(str(ctx.render(self.name)) or ctx.account_name)
        path = ctx.artifacts_dir / f"{datetime.now():%H%M%S}_{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=self.full_page)
        ctx.screenshots.append(str(path))
        if ctx.screenshot_cb:
            ctx.screenshot_cb(str(path))


@register
class LogMessageStep(StepBase):
    type: Literal["log"] = "log"
    message: str = Field("", title="Message", description="Variables are filled in, e.g. Price is {{price}}")
    level: Literal["info", "warning", "error"] = Field("info", title="Level")
    TITLE = "Log message"
    CATEGORY = CAT
    ICON = "message"

    def summary(self) -> str:
        return f"Write “{self.message}” to the log"

    async def run(self, ctx: StepContext) -> None:
        ctx.log(self.level, str(ctx.render(self.message)))


@register
class PauseManualStep(StepBase):
    type: Literal["pause_manual"] = "pause_manual"
    message: str = Field("Please finish this step in the browser, then press Continue.", title="Message to show")
    TITLE = "Pause for manual action"
    CATEGORY = CAT
    ICON = "hand"

    def summary(self) -> str:
        return f"Pause until you press Continue: “{self.message}”"

    async def run(self, ctx: StepContext) -> None:
        msg = str(ctx.render(self.message))
        if ctx.notify_cb:
            ctx.notify_cb(f"{ctx.account_name}: {msg}")
        ctx.log("warning", f"Waiting for you: {msg}")
        await ctx.control.wait_manual(msg)


@register
class RunJavaScriptStep(StepBase):
    type: Literal["run_js"] = "run_js"
    code: str = Field("return document.title;", title="JavaScript",
                      description="Runs inside the page. Use 'return' to give back a value.")
    variable: str = Field("", title="Save result into variable (optional)")
    TITLE = "Run JavaScript"
    CATEGORY = CAT
    ICON = "code"
    ADVANCED = True

    def summary(self) -> str:
        first = self.code.strip().splitlines()[0] if self.code.strip() else "…"
        return f"Run JavaScript: {first[:50]}"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.code.strip() else ["Enter the JavaScript code."])

    async def run(self, ctx: StepContext) -> None:
        code = str(ctx.render(self.code))
        result = await ctx.require_page().evaluate(f"async () => {{ {code}\n }}")
        if self.variable.strip():
            value = result if isinstance(result, str) else json.dumps(result)
            ctx.variables.set(self.variable, "" if result is None else value)


@register
class NotifyStep(StepBase):
    type: Literal["notify"] = "notify"
    message: str = Field("Done!", title="Message")
    sound: bool = Field(True, title="Play a sound")
    TITLE = "Play sound / notify"
    CATEGORY = CAT
    ICON = "bell"

    def summary(self) -> str:
        return f"Notify: “{self.message}”"

    async def run(self, ctx: StepContext) -> None:
        msg = str(ctx.render(self.message))
        if ctx.notify_cb:
            ctx.notify_cb(f"{ctx.account_name}: {msg}")
        if self.sound:
            try:
                from PySide6.QtWidgets import QApplication

                QApplication.beep()
            except Exception:
                pass


@register
class CloseBrowserStep(StepBase):
    type: Literal["close_browser"] = "close_browser"
    TITLE = "Close browser"
    CATEGORY = END
    ICON = "power"
    DESCRIPTION = "Close the browser and stop the profile in the anti-detect browser."

    def summary(self) -> str:
        return "Close the browser"

    async def run(self, ctx: StepContext) -> None:
        if ctx.browser_closed:
            return
        if ctx.close_browser_cb:
            await ctx.close_browser_cb()
        ctx.browser_closed = True


@register
class MarkDoneStep(StepBase):
    type: Literal["mark_done"] = "mark_done"
    message: str = Field("", title="Message (optional)")
    stop: bool = Field(True, title="Stop the flow here")
    TITLE = "Mark account as done"
    CATEGORY = END
    ICON = "check-circle"

    def summary(self) -> str:
        return "Mark this account as done" + (" and stop" if self.stop else "")

    async def run(self, ctx: StepContext) -> None:
        ctx.forced_status = "done"
        ctx.forced_message = str(ctx.render(self.message))
        if self.stop:
            raise FlowStopped(True, ctx.forced_message)


@register
class MarkFailedStep(StepBase):
    type: Literal["mark_failed"] = "mark_failed"
    message: str = Field("", title="Reason")
    stop: bool = Field(True, title="Stop the flow here")
    TITLE = "Mark account as failed"
    CATEGORY = END
    ICON = "x-circle"

    def summary(self) -> str:
        return "Mark this account as failed" + (f": {self.message}" if self.message else "")

    async def run(self, ctx: StepContext) -> None:
        ctx.forced_status = "failed"
        ctx.forced_message = str(ctx.render(self.message)) or "Marked as failed by the flow."
        if self.stop:
            raise FlowStopped(False, ctx.forced_message)
