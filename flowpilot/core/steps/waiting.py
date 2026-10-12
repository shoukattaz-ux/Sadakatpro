"""Waiting steps."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from flowpilot.core.context import StepContext
from flowpilot.core.errors import StepFailed
from flowpilot.core.steps.base import StepBase, register

CAT = "Waiting"


@register
class WaitStep(StepBase):
    type: Literal["wait"] = "wait"
    seconds: float = Field(2.0, ge=0, title="Seconds")
    TITLE = "Wait"
    CATEGORY = CAT
    ICON = "clock"

    def summary(self) -> str:
        return f"Wait {self.seconds:g} seconds"

    async def run(self, ctx: StepContext) -> None:
        await ctx.pause_between(self.seconds)


@register
class WaitRandomStep(StepBase):
    type: Literal["wait_random"] = "wait_random"
    min_seconds: float = Field(1.0, ge=0, title="Minimum seconds")
    max_seconds: float = Field(3.0, ge=0, title="Maximum seconds")
    TITLE = "Wait random time"
    CATEGORY = CAT
    ICON = "shuffle"

    def summary(self) -> str:
        return f"Wait {self.min_seconds:g}–{self.max_seconds:g} seconds"

    async def run(self, ctx: StepContext) -> None:
        lo, hi = sorted((self.min_seconds, self.max_seconds))
        await ctx.pause_between(ctx.humanizer.rng.uniform(lo, hi))


@register
class WaitForElementStep(StepBase):
    type: Literal["wait_for_element"] = "wait_for_element"
    state: Literal["visible", "hidden"] = Field("visible", title="Wait until the element is")
    USES_SELECTOR = True
    TITLE = "Wait for element"
    CATEGORY = CAT
    ICON = "eye"

    def summary(self) -> str:
        return f"Wait until {self.target()} is {self.state}"

    async def run(self, ctx: StepContext) -> None:
        if self.state == "visible":
            await ctx.locate(self)
            return
        page = ctx.require_page()
        candidates = [ctx.render(s) for s in [self.selector, *self.fallbacks] if s]
        loop_time = 0.0
        while loop_time <= ctx.timeout_for(self):
            visible = False
            for sel in candidates:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        visible = True
                        break
                except Exception:
                    pass
            if not visible:
                return
            await ctx.control.sleep(0.25)
            loop_time += 0.25
        raise StepFailed(f"{self.target().capitalize()} was still visible after {ctx.timeout_for(self):g} seconds.")


@register
class WaitForUrlStep(StepBase):
    type: Literal["wait_for_url"] = "wait_for_url"
    contains: str = Field("", title="Address contains")
    regex: bool = Field(False, title="Treat as regular expression (advanced)")
    TITLE = "Wait for URL"
    CATEGORY = CAT
    ICON = "link"

    def summary(self) -> str:
        return f"Wait until the address contains “{self.contains}”"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.contains.strip() else ["Enter the text the address should contain."])

    async def run(self, ctx: StepContext) -> None:
        needle = str(ctx.render(self.contains))
        waited = 0.0
        while True:
            url = ctx.require_page().url
            if (re.search(needle, url) if self.regex else needle.lower() in url.lower()):
                return
            if waited >= ctx.timeout_for(self):
                raise StepFailed(f"The page address never contained “{needle}” (it is {url}).")
            await ctx.control.sleep(0.25)
            waited += 0.25


@register
class WaitForTextStep(StepBase):
    type: Literal["wait_for_text"] = "wait_for_text"
    text: str = Field("", title="Text")
    TITLE = "Wait for text on page"
    CATEGORY = CAT
    ICON = "type"

    def summary(self) -> str:
        return f"Wait until the page shows “{self.text}”"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.text.strip() else ["Enter the text to wait for."])

    async def run(self, ctx: StepContext) -> None:
        needle = str(ctx.render(self.text))
        waited = 0.0
        while True:
            body = await ctx.require_page().evaluate("() => document.body ? document.body.innerText : ''")
            if needle.lower() in str(body).lower():
                return
            if waited >= ctx.timeout_for(self):
                raise StepFailed(f"The text “{needle}” did not appear within {ctx.timeout_for(self):g} seconds.")
            await ctx.control.sleep(0.4)
            waited += 0.4


@register
class WaitForNetworkIdleStep(StepBase):
    type: Literal["wait_for_network_idle"] = "wait_for_network_idle"
    TITLE = "Wait for network idle"
    CATEGORY = CAT
    ICON = "wifi"

    def summary(self) -> str:
        return "Wait until the page stops loading data"

    async def run(self, ctx: StepContext) -> None:
        await ctx.require_page().wait_for_load_state("networkidle", timeout=ctx.timeout_for(self) * 1000)
