"""Interaction steps: click, type, keys, dropdowns, checkboxes, upload, scroll, drag & drop."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from flowpilot.core.context import StepContext
from flowpilot.core.errors import StepFailed
from flowpilot.core.steps.base import StepBase, register

CAT = "Interaction"


class _ElementStep(StepBase):
    USES_SELECTOR = True
    CATEGORY = CAT


@register
class ClickStep(_ElementStep):
    type: Literal["click"] = "click"
    TITLE = "Click"
    ICON = "pointer"
    DESCRIPTION = "Click a button, link or any element."
    BUTTON: ClassVar[str] = "left"
    CLICK_COUNT: ClassVar[int] = 1

    def summary(self) -> str:
        return f"Click {self.target()}"

    async def run(self, ctx: StepContext) -> None:
        loc = await ctx.locate(self)
        page = ctx.require_page()
        timeout_ms = ctx.timeout_for(self) * 1000
        if ctx.humanize_for(self):
            await ctx.humanizer.click(page, loc, button=self.BUTTON, click_count=self.CLICK_COUNT, timeout_ms=timeout_ms)
        else:
            await loc.click(button=self.BUTTON, click_count=self.CLICK_COUNT, timeout=timeout_ms)


@register
class DoubleClickStep(ClickStep):
    type: Literal["double_click"] = "double_click"  # type: ignore[assignment]
    TITLE = "Double click"
    ICON = "mouse-pointer-2"
    CLICK_COUNT = 2

    def summary(self) -> str:
        return f"Double-click {self.target()}"


@register
class RightClickStep(ClickStep):
    type: Literal["right_click"] = "right_click"  # type: ignore[assignment]
    TITLE = "Right click"
    ICON = "mouse-right"
    BUTTON = "right"

    def summary(self) -> str:
        return f"Right-click {self.target()}"


@register
class HoverStep(_ElementStep):
    type: Literal["hover"] = "hover"
    TITLE = "Hover"
    ICON = "move"

    def summary(self) -> str:
        return f"Move the mouse over {self.target()}"

    async def run(self, ctx: StepContext) -> None:
        loc = await ctx.locate(self)
        if ctx.humanize_for(self):
            await ctx.humanizer.hover(ctx.require_page(), loc)
        else:
            await loc.hover(timeout=ctx.timeout_for(self) * 1000)


@register
class TypeTextStep(_ElementStep):
    type: Literal["type_text"] = "type_text"
    text: str = Field("", title="Text to type", description="Use variables like {{account.email}}.")
    clear_first: bool = Field(True, title="Clear the field first")
    press_enter: bool = Field(False, title="Press Enter afterwards")
    sensitive: bool = Field(False, title="Hide the text in logs", description="Turn on for passwords.")
    TITLE = "Type text"
    ICON = "keyboard"

    def summary(self) -> str:
        shown = "••••••" if self.sensitive and "{{" not in self.text else self.text
        return f"Type “{shown}” into {self.target()}"

    async def run(self, ctx: StepContext) -> None:
        loc = await ctx.locate(self)
        page = ctx.require_page()
        text = str(ctx.render(self.text))
        timeout_ms = ctx.timeout_for(self) * 1000
        if ctx.humanize_for(self):
            await ctx.humanizer.click(page, loc, timeout_ms=timeout_ms)
            if self.clear_first:
                await loc.fill("", timeout=timeout_ms)
            await ctx.humanizer.type_text(page, text)
        else:
            if self.clear_first:
                await loc.fill(text, timeout=timeout_ms)
            else:
                await loc.press_sequentially(text, timeout=timeout_ms)
        if self.press_enter:
            await ctx.control.checkpoint()
            await loc.press("Enter", timeout=timeout_ms)


@register
class ClearFieldStep(_ElementStep):
    type: Literal["clear_field"] = "clear_field"
    TITLE = "Clear field"
    ICON = "eraser"

    def summary(self) -> str:
        return f"Clear {self.target()}"

    async def run(self, ctx: StepContext) -> None:
        loc = await ctx.locate(self)
        await loc.fill("", timeout=ctx.timeout_for(self) * 1000)


@register
class PressKeyStep(_ElementStep):
    type: Literal["press_key"] = "press_key"
    key: str = Field("Enter", title="Key or combination",
                     description="Examples: Enter, Tab, Escape, ArrowDown, Control+A, Shift+Tab.")
    times: int = Field(1, ge=1, le=100, title="How many times")
    SELECTOR_REQUIRED = False
    TITLE = "Press key"
    ICON = "command"

    def summary(self) -> str:
        times = f" {self.times}×" if self.times > 1 else ""
        where = f" in {self.target()}" if self.selector else ""
        return f"Press {self.key}{times}{where}"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.key.strip() else ["Enter the key to press."])

    async def run(self, ctx: StepContext) -> None:
        key = str(ctx.render(self.key)).strip()
        if self.selector:
            loc = await ctx.locate(self)
            for _ in range(self.times):
                await loc.press(key, timeout=ctx.timeout_for(self) * 1000)
                await ctx.control.sleep(ctx.humanizer.rng.uniform(0.05, 0.15) if ctx.humanize_for(self) else 0)
            return
        page = ctx.require_page()
        for _ in range(self.times):
            await page.keyboard.press(key)
            await ctx.control.sleep(ctx.humanizer.rng.uniform(0.05, 0.15) if ctx.humanize_for(self) else 0)


@register
class SelectOptionStep(_ElementStep):
    type: Literal["select_option"] = "select_option"
    by: Literal["label", "value", "index"] = Field("label", title="Choose option by",
                                                   description="label = the visible text.")
    value: str = Field("", title="Option")
    TITLE = "Select dropdown option"
    ICON = "list"

    def summary(self) -> str:
        return f"Choose “{self.value}” in {self.target()}"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.value.strip() else ["Enter which option to choose."])

    async def run(self, ctx: StepContext) -> None:
        loc = await ctx.locate(self)
        value = str(ctx.render(self.value))
        timeout_ms = ctx.timeout_for(self) * 1000
        if self.by == "index":
            await loc.select_option(index=int(value), timeout=timeout_ms)
        elif self.by == "value":
            await loc.select_option(value=value, timeout=timeout_ms)
        else:
            await loc.select_option(label=value, timeout=timeout_ms)


@register
class CheckStep(_ElementStep):
    type: Literal["check"] = "check"
    TITLE = "Check"
    ICON = "check-square"
    CHECKED: ClassVar[bool] = True

    def summary(self) -> str:
        return f"{'Tick' if self.CHECKED else 'Untick'} {self.target()}"

    async def run(self, ctx: StepContext) -> None:
        loc = await ctx.locate(self, state="attached")
        timeout_ms = ctx.timeout_for(self) * 1000
        if await loc.is_checked() == self.CHECKED:
            return
        if ctx.humanize_for(self) and await loc.is_visible():
            await ctx.humanizer.click(ctx.require_page(), loc, timeout_ms=timeout_ms)
            if await loc.is_checked() == self.CHECKED:
                return
        await loc.set_checked(self.CHECKED, timeout=timeout_ms, force=True)


@register
class UncheckStep(CheckStep):
    type: Literal["uncheck"] = "uncheck"  # type: ignore[assignment]
    TITLE = "Uncheck"
    ICON = "square"
    CHECKED = False


@register
class UploadFileStep(_ElementStep):
    type: Literal["upload_file"] = "upload_file"
    files: list[str] = Field(default_factory=list, title="Files", description="Full paths, one per line.")
    TITLE = "Upload file"
    ICON = "upload"

    def summary(self) -> str:
        names = ", ".join(Path(f).name for f in self.files) or "…"
        return f"Upload {names}"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.files else ["Choose at least one file."])

    async def run(self, ctx: StepContext) -> None:
        paths = [str(ctx.render(f)).strip() for f in self.files if f.strip()]
        missing = [p for p in paths if not Path(p).exists()]
        if missing:
            raise StepFailed(f"File not found: {missing[0]}")
        loc = await ctx.locate(self, state="attached")
        await loc.set_input_files(paths, timeout=ctx.timeout_for(self) * 1000)


@register
class ScrollStep(StepBase):
    type: Literal["scroll"] = "scroll"
    mode: Literal["by_amount", "to_element", "to_bottom", "to_top"] = Field("by_amount", title="Scroll")
    amount: int = Field(600, title="Pixels (positive = down)", description="Used with 'by_amount'.")
    USES_SELECTOR = True
    SELECTOR_REQUIRED = False
    TITLE = "Scroll"
    CATEGORY = CAT
    ICON = "mouse"

    def summary(self) -> str:
        return {
            "by_amount": f"Scroll {'down' if self.amount >= 0 else 'up'} {abs(self.amount)} px",
            "to_element": f"Scroll to {self.target()}",
            "to_bottom": "Scroll to the bottom of the page",
            "to_top": "Scroll to the top of the page",
        }[self.mode]

    def problems(self) -> list[str]:
        p = [x for x in super().problems() if "element" not in x.lower()]
        if self.mode == "to_element" and not self.selector.strip():
            p.append("Choose the element to scroll to.")
        return p

    async def run(self, ctx: StepContext) -> None:
        page = ctx.require_page()
        human = ctx.humanize_for(self)
        if self.mode == "to_element":
            loc = await ctx.locate(self, state="attached")
            if human:
                await ctx.humanizer.scroll_into_view(page, loc)
            else:
                await loc.scroll_into_view_if_needed(timeout=ctx.timeout_for(self) * 1000)
            return
        if self.mode == "by_amount":
            distance = float(self.amount)
        else:
            metrics = await page.evaluate("() => [window.scrollY, document.documentElement.scrollHeight, window.innerHeight]")
            y, height, view = metrics
            distance = (height - view - y) if self.mode == "to_bottom" else -y
        if human:
            await ctx.humanizer.scroll_by(page, distance)
        else:
            await page.mouse.wheel(0, distance)
        await ctx.control.sleep(0.2)


@register
class DragDropStep(_ElementStep):
    type: Literal["drag_drop"] = "drag_drop"
    target_selector: str = Field("", title="Drop onto", description="The element to drop onto.")
    TITLE = "Drag and drop"
    ICON = "grab"

    def summary(self) -> str:
        from flowpilot.core.selectors import describe_selector

        return f"Drag {self.target()} onto {describe_selector(self.target_selector) if self.target_selector else '…'}"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.target_selector.strip() else ["Choose where to drop."])

    async def run(self, ctx: StepContext) -> None:
        src = await ctx.locate(self)
        dst = await ctx.locate(self, selector=self.target_selector, fallbacks=[])
        page = ctx.require_page()
        if ctx.humanize_for(self):
            await ctx.humanizer.hover(page, src)
            await page.mouse.down()
            await ctx.humanizer.hover(page, dst)
            await page.mouse.up()
        else:
            await src.drag_to(dst, timeout=ctx.timeout_for(self) * 1000)


@register
class FocusStep(_ElementStep):
    type: Literal["focus"] = "focus"
    TITLE = "Focus"
    ICON = "crosshair"

    def summary(self) -> str:
        return f"Focus {self.target()}"

    async def run(self, ctx: StepContext) -> None:
        loc = await ctx.locate(self, state="attached")
        await loc.focus(timeout=ctx.timeout_for(self) * 1000)
