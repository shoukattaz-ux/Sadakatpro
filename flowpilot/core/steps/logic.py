"""Logic steps: conditions, loops, labels/go to, stop, try / on error.

These steps are *markers* in the flat step list. The runner (``core/runner.py``) matches
the blocks (``If … Else … End If``, ``Repeat/Loop … End loop``, ``Try … On error … End try``)
and moves its program counter; the steps here only evaluate conditions and produce items.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from flowpilot.core.context import StepContext
from flowpilot.core.steps.base import StepBase, register
from flowpilot.core.steps.data import load_csv

CAT = "Logic"


class _Marker(StepBase):
    CATEGORY = CAT


class _IfBase(_Marker):
    negate: bool = Field(False, title="Reverse (if NOT …)")
    BLOCK = "if"
    ICON = "git-branch"

    async def check(self, ctx: StepContext) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    async def evaluate(self, ctx: StepContext) -> bool:
        result = await self.check(ctx)
        return (not result) if self.negate else result

    def _not(self) -> str:
        return " NOT" if self.negate else ""


@register
class IfElementStep(_IfBase):
    type: Literal["if_element"] = "if_element"
    state: Literal["exists", "visible"] = Field("visible", title="Condition")
    wait_seconds: float = Field(2.0, ge=0, title="Look for up to (seconds)")
    USES_SELECTOR = True
    TITLE = "If element exists"

    def summary(self) -> str:
        return f"If {self.target()} does{self._not()} {'exist' if self.state == 'exists' else 'show'}"

    async def check(self, ctx: StepContext) -> bool:
        from flowpilot.core.errors import ElementNotFound
        from flowpilot.core.selectors import resolve_locator

        candidates = [ctx.render(s) for s in [self.selector, *self.fallbacks] if s]
        try:
            await resolve_locator(ctx.require_page(), candidates, self.wait_seconds,
                                  state="visible" if self.state == "visible" else "attached",
                                  check=ctx.control.checkpoint)
            return True
        except ElementNotFound:
            return False


@register
class IfTextStep(_IfBase):
    type: Literal["if_text"] = "if_text"
    text: str = Field("", title="Text")
    USES_SELECTOR = True
    SELECTOR_REQUIRED = False
    TITLE = "If text contains"

    def summary(self) -> str:
        where = self.target() if self.selector else "the page"
        return f"If {where} does{self._not()} contain “{self.text}”"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.text.strip() else ["Enter the text to look for."])

    async def check(self, ctx: StepContext) -> bool:
        needle = str(ctx.render(self.text)).lower()
        page = ctx.require_page()
        if self.selector:
            loc = page.locator(ctx.render(self.selector)).first
            if await loc.count() == 0:
                return False
            haystack = await loc.inner_text(timeout=ctx.timeout_for(self) * 1000)
        else:
            haystack = await page.evaluate("() => document.body ? document.body.innerText : ''")
        return needle in str(haystack).lower()


Operator = Literal["equals", "not_equals", "contains", "not_contains", "greater", "less", "is_empty", "not_empty"]


def compare(left: Any, operator: str, right: Any) -> bool:
    """Compare two values as numbers when both look numeric, otherwise as text."""
    ls, rs = str(left if left is not None else ""), str(right if right is not None else "")
    if operator == "is_empty":
        return ls.strip() == ""
    if operator == "not_empty":
        return ls.strip() != ""
    if operator in ("contains", "not_contains"):
        found = rs.lower() in ls.lower()
        return found if operator == "contains" else not found
    try:
        lf, rf = float(ls.replace(",", ".")), float(rs.replace(",", "."))
        numeric = True
    except ValueError:
        numeric = False
    if operator == "equals":
        return lf == rf if numeric else ls.strip() == rs.strip()
    if operator == "not_equals":
        return lf != rf if numeric else ls.strip() != rs.strip()
    if operator == "greater":
        return lf > rf if numeric else ls > rs
    if operator == "less":
        return lf < rf if numeric else ls < rs
    raise ValueError(f"Unknown operator {operator}")


@register
class IfVariableStep(_IfBase):
    type: Literal["if_variable"] = "if_variable"
    variable: str = Field("", title="Variable", description="e.g. price or account.country")
    operator: Operator = Field("equals", title="Is")
    value: str = Field("", title="Value")
    TITLE = "If variable equals"

    def summary(self) -> str:
        op = self.operator.replace("_", " ")
        tail = "" if self.operator in ("is_empty", "not_empty") else f" “{self.value}”"
        return f"If {{{{{self.variable or '?'}}}}}{self._not()} {op}{tail}"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.variable.strip() else ["Enter the variable to check."])

    async def check(self, ctx: StepContext) -> bool:
        name = self.variable.strip().strip("{}").strip()
        return compare(ctx.variables.get(name, ""), self.operator, ctx.render(self.value))


@register
class ElseStep(_Marker):
    type: Literal["else"] = "else"
    TITLE = "Else"
    ICON = "corner-down-right"
    BLOCK = "else"

    def summary(self) -> str:
        return "Otherwise"


@register
class EndIfStep(_Marker):
    type: Literal["end_if"] = "end_if"
    TITLE = "End If"
    ICON = "corner-left-up"
    BLOCK = "end_if"

    def summary(self) -> str:
        return "End of If"


@register
class RepeatStep(_Marker):
    type: Literal["repeat"] = "repeat"
    times: int = Field(3, ge=0, le=100000, title="Times")
    BLOCK = "loop"
    TITLE = "Repeat N times"
    ICON = "repeat"

    def summary(self) -> str:
        return f"Repeat {self.times} times"

    async def items(self, ctx: StepContext) -> list[Any]:
        return list(range(1, self.times + 1))


@register
class LoopListStep(_Marker):
    type: Literal["loop_list"] = "loop_list"
    source: Literal["list", "csv", "variable"] = Field("list", title="Loop over")
    items_list: list[str] = Field(default_factory=list, title="Items (one per line)")
    file: str = Field("", title="CSV file", description="Each row's columns become {{row.column}}.")
    variable: str = Field("", title="Variable with items (one per line or comma separated)")
    BLOCK = "loop"
    TITLE = "Loop over list / CSV rows"
    ICON = "list-ordered"

    def summary(self) -> str:
        if self.source == "csv":
            return f"For each row in {Path(self.file).name or '…'}"
        if self.source == "variable":
            return f"For each item in {{{{{self.variable or '?'}}}}}"
        return f"For each of {len(self.items_list)} items"

    def problems(self) -> list[str]:
        p = super().problems()
        if self.source == "csv" and not self.file.strip():
            p.append("Choose a CSV file.")
        if self.source == "variable" and not self.variable.strip():
            p.append("Enter the variable that holds the list.")
        return p

    async def items(self, ctx: StepContext) -> list[Any]:
        if self.source == "csv":
            return list(load_csv(Path(str(ctx.render(self.file)))))
        if self.source == "variable":
            raw = ctx.variables.get(self.variable.strip().strip("{}").strip(), "")
            if isinstance(raw, list):
                return raw
            text = str(raw)
            parts = text.splitlines() if "\n" in text else text.split(",")
            return [p.strip() for p in parts if p.strip()]
        return [ctx.render(i) for i in self.items_list]


@register
class EndLoopStep(_Marker):
    type: Literal["end_loop"] = "end_loop"
    TITLE = "End loop"
    ICON = "corner-left-up"
    BLOCK = "end_loop"

    def summary(self) -> str:
        return "End of loop"


@register
class BreakLoopStep(_Marker):
    type: Literal["break_loop"] = "break_loop"
    TITLE = "Exit loop"
    ICON = "log-out"

    def summary(self) -> str:
        return "Leave the loop now"


@register
class LabelStep(_Marker):
    type: Literal["label_marker"] = "label_marker"
    name: str = Field("", title="Label name")
    TITLE = "Label"
    ICON = "bookmark"

    def summary(self) -> str:
        return f"Label “{self.name or '?'}”"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.name.strip() else ["Give the label a name."])


@register
class GoToStep(_Marker):
    type: Literal["goto"] = "goto"
    goto_label: str = Field("", title="Go to label")
    max_jumps: int = Field(100, ge=1, title="Maximum jumps", description="Safety limit against endless loops.")
    TITLE = "Go to label"
    ICON = "corner-up-left"

    def summary(self) -> str:
        return f"Go to “{self.goto_label or '?'}”"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.goto_label.strip() else ["Choose the label to jump to."])


@register
class StopFlowStep(_Marker):
    type: Literal["stop_flow"] = "stop_flow"
    result: Literal["success", "fail"] = Field("success", title="End as")
    message: str = Field("", title="Message")
    TITLE = "Stop flow"
    ICON = "octagon"

    def summary(self) -> str:
        return f"Stop here ({'success' if self.result == 'success' else 'failed'})"


@register
class TryStep(_Marker):
    type: Literal["try"] = "try"
    BLOCK = "try"
    TITLE = "Try"
    ICON = "shield"

    def summary(self) -> str:
        return "Try the following steps"


@register
class CatchStep(_Marker):
    type: Literal["catch"] = "catch"
    BLOCK = "catch"
    TITLE = "On error"
    ICON = "alert-triangle"

    def summary(self) -> str:
        return "If any of them failed: ({{error.message}} has the reason)"


@register
class EndTryStep(_Marker):
    type: Literal["end_try"] = "end_try"
    BLOCK = "end_try"
    TITLE = "End try"
    ICON = "corner-left-up"

    def summary(self) -> str:
        return "End of Try"
