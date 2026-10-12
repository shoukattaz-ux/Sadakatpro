"""Navigation steps: open URL, back/forward/reload, tabs, wait for load."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from flowpilot.core.context import StepContext
from flowpilot.core.errors import StepFailed
from flowpilot.core.steps.base import StepBase, register

WaitUntil = Literal["load", "domcontentloaded", "networkidle", "commit"]
CAT = "Navigation"


def _normalize_url(url: str) -> str:
    url = url.strip()
    if url and "://" not in url and not url.startswith(("about:", "data:", "file:", "chrome:")):
        url = "https://" + url
    return url


@register
class OpenUrlStep(StepBase):
    type: Literal["open_url"] = "open_url"
    url: str = Field("", title="Web address", description="For example https://example.com. Variables like {{account.email}} work.")
    wait_until: WaitUntil = Field("load", title="Wait until", description="load = whole page loaded; domcontentloaded = faster.")
    new_tab: bool = Field(False, title="Open in a new tab")
    TITLE = "Open URL"
    CATEGORY = CAT
    ICON = "globe"
    DESCRIPTION = "Go to a web address."

    def summary(self) -> str:
        return f"Open {self.url or '…'}" + (" in a new tab" if self.new_tab else "")

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.url.strip() else ["Enter the web address to open."])

    async def run(self, ctx: StepContext) -> None:
        url = _normalize_url(ctx.render(self.url))
        if self.new_tab:
            ctx.require_page()
            ctx.page = await ctx.browser_context.new_page()
        page = ctx.require_page()
        await page.goto(url, wait_until=self.wait_until, timeout=ctx.timeout_for(self) * 1000 * 2)


class _HistoryStep(StepBase):
    wait_until: WaitUntil = Field("load", title="Wait until")
    CATEGORY = CAT

    async def run(self, ctx: StepContext) -> None:
        page = ctx.require_page()
        method = getattr(page, {"go_back": "go_back", "go_forward": "go_forward", "reload": "reload"}[self.type])
        await method(wait_until=self.wait_until, timeout=ctx.timeout_for(self) * 1000 * 2)


@register
class GoBackStep(_HistoryStep):
    type: Literal["go_back"] = "go_back"
    TITLE = "Go back"
    ICON = "arrow-left"

    def summary(self) -> str:
        return "Go back to the previous page"


@register
class GoForwardStep(_HistoryStep):
    type: Literal["go_forward"] = "go_forward"
    TITLE = "Go forward"
    ICON = "arrow-right"

    def summary(self) -> str:
        return "Go forward"


@register
class ReloadStep(_HistoryStep):
    type: Literal["reload"] = "reload"
    TITLE = "Reload page"
    ICON = "refresh"

    def summary(self) -> str:
        return "Reload the page"


@register
class NewTabStep(StepBase):
    type: Literal["new_tab"] = "new_tab"
    url: str = Field("", title="Web address (optional)")
    TITLE = "Open new tab"
    CATEGORY = CAT
    ICON = "plus-square"

    def summary(self) -> str:
        return f"Open a new tab{' with ' + self.url if self.url else ''}"

    async def run(self, ctx: StepContext) -> None:
        ctx.require_page()
        ctx.page = await ctx.browser_context.new_page()
        if self.url.strip():
            await ctx.page.goto(_normalize_url(ctx.render(self.url)), timeout=ctx.timeout_for(self) * 2000)


@register
class SwitchTabStep(StepBase):
    type: Literal["switch_tab"] = "switch_tab"
    by: Literal["index", "title", "url", "newest"] = Field("newest", title="Find tab by",
                                                             description="index: 1 = first tab; title/url: 'contains' text.")
    value: str = Field("", title="Index or text")
    TITLE = "Switch to tab"
    CATEGORY = CAT
    ICON = "layers"

    def summary(self) -> str:
        if self.by == "newest":
            return "Switch to the newest tab"
        if self.by == "index":
            return f"Switch to tab #{self.value or '?'}"
        return f"Switch to the tab whose {self.by} contains “{self.value}”"

    def problems(self) -> list[str]:
        p = super().problems()
        if self.by != "newest" and not self.value.strip():
            p.append("Enter the tab number or text to look for.")
        return p

    async def run(self, ctx: StepContext) -> None:
        ctx.require_page()
        loop_deadline = ctx.timeout_for(self)
        value = str(ctx.render(self.value)).strip()
        waited = 0.0
        while True:
            pages = [p for p in ctx.browser_context.pages if not p.is_closed()]
            found = await self._find(pages, value)
            if found is not None:
                ctx.page = found
                await found.bring_to_front()
                return
            if waited >= loop_deadline:
                raise StepFailed(f"No tab found ({self.summary().lower()}).")
            await ctx.control.sleep(0.5)
            waited += 0.5

    async def _find(self, pages: list[Any], value: str) -> Any:
        if not pages:
            return None
        if self.by == "newest":
            return pages[-1]
        if self.by == "index":
            try:
                idx = int(value)
            except ValueError as exc:
                raise StepFailed("The tab index must be a number (1 = first tab).") from exc
            idx = idx - 1 if idx > 0 else idx
            return pages[idx] if -len(pages) <= idx < len(pages) else None
        for p in pages:
            text = p.url if self.by == "url" else await p.title()
            if value.lower() in text.lower():
                return p
        return None


@register
class CloseTabStep(StepBase):
    type: Literal["close_tab"] = "close_tab"
    TITLE = "Close tab"
    CATEGORY = CAT
    ICON = "x-square"

    def summary(self) -> str:
        return "Close the current tab"

    async def run(self, ctx: StepContext) -> None:
        page = ctx.require_page()
        await page.close()
        pages = [p for p in ctx.browser_context.pages if not p.is_closed()]
        ctx.page = pages[-1] if pages else None
        if ctx.page is not None:
            await ctx.page.bring_to_front()


@register
class WaitForLoadStep(StepBase):
    type: Literal["wait_for_load"] = "wait_for_load"
    state: Literal["load", "domcontentloaded", "networkidle"] = Field("load", title="Wait until")
    TITLE = "Wait for page load"
    CATEGORY = CAT
    ICON = "loader"

    def summary(self) -> str:
        return "Wait for the page to finish loading"

    async def run(self, ctx: StepContext) -> None:
        await ctx.require_page().wait_for_load_state(self.state, timeout=ctx.timeout_for(self) * 1000)
