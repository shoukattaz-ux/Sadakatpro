"""Data & variable steps."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from flowpilot.core.context import StepContext
from flowpilot.core.errors import StepFailed
from flowpilot.core.steps.base import StepBase, register

CAT = "Data & variables"


def _var_problem(name: str) -> list[str]:
    return [] if name.strip() else ["Enter a variable name to store the value in."]


def load_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV file (auto-detects , ; or tab) into a list of row dicts."""
    if not path.exists():
        raise StepFailed(f"CSV file not found: {path}")
    raw = path.read_text("utf-8-sig", errors="replace")
    try:
        dialect: Any = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [dict(row) for row in csv.DictReader(raw.splitlines(), dialect=dialect)]


@register
class ExtractStep(StepBase):
    type: Literal["extract"] = "extract"
    attribute: str = Field("", title="What to read",
                           description="Empty = visible text; 'value' = input value; or an attribute such as href.")
    variable: str = Field("", title="Save into variable", description="Use it later as {{name}}.")
    regex: str = Field("", title="Keep only the part matching (advanced)", description="Optional regular expression.")
    USES_SELECTOR = True
    TITLE = "Extract text / attribute"
    CATEGORY = CAT
    ICON = "download"

    def summary(self) -> str:
        what = self.attribute or "text"
        return f"Read the {what} of {self.target()} into {{{{{self.variable or '?'}}}}}"

    def problems(self) -> list[str]:
        return super().problems() + _var_problem(self.variable)

    async def run(self, ctx: StepContext) -> None:
        loc = await ctx.locate(self, state="attached")
        timeout_ms = ctx.timeout_for(self) * 1000
        if not self.attribute:
            value = await loc.inner_text(timeout=timeout_ms)
        elif self.attribute == "value":
            value = await loc.input_value(timeout=timeout_ms)
        else:
            value = await loc.get_attribute(self.attribute, timeout=timeout_ms) or ""
        value = value.strip()
        if self.regex:
            m = re.search(self.regex, value)
            value = (m.group(1) if m and m.groups() else m.group(0)) if m else ""
        ctx.variables.set(self.variable, value)
        ctx.log("info", f"{self.variable} = {value[:200]}")


@register
class SetVariableStep(StepBase):
    type: Literal["set_variable"] = "set_variable"
    variable: str = Field("", title="Variable name")
    value: str = Field("", title="Value", description="Can contain other variables, e.g. {{account.username}}-1")
    TITLE = "Set variable"
    CATEGORY = CAT
    ICON = "variable"

    def summary(self) -> str:
        return f"Set {{{{{self.variable or '?'}}}}} to “{self.value}”"

    def problems(self) -> list[str]:
        return super().problems() + _var_problem(self.variable)

    async def run(self, ctx: StepContext) -> None:
        ctx.variables.set(self.variable, ctx.render(self.value))


@register
class RandomItemStep(StepBase):
    type: Literal["random_item"] = "random_item"
    items: list[str] = Field(default_factory=list, title="List of values", description="One per line.")
    variable: str = Field("", title="Save into variable")
    TITLE = "Random item from list"
    CATEGORY = CAT
    ICON = "dice"

    def summary(self) -> str:
        return f"Pick a random item ({len(self.items)} choices) into {{{{{self.variable or '?'}}}}}"

    def problems(self) -> list[str]:
        p = super().problems() + _var_problem(self.variable)
        return p + ([] if self.items else ["Add at least one value to the list."])

    async def run(self, ctx: StepContext) -> None:
        ctx.variables.set(self.variable, ctx.render(ctx.humanizer.rng.choice(self.items)))


@register
class RandomNumberStep(StepBase):
    type: Literal["random_number"] = "random_number"
    minimum: float = Field(1, title="From")
    maximum: float = Field(100, title="To")
    decimals: int = Field(0, ge=0, le=6, title="Decimals")
    variable: str = Field("", title="Save into variable")
    TITLE = "Random number"
    CATEGORY = CAT
    ICON = "hash"

    def summary(self) -> str:
        return f"Random number {self.minimum:g}–{self.maximum:g} into {{{{{self.variable or '?'}}}}}"

    def problems(self) -> list[str]:
        return super().problems() + _var_problem(self.variable)

    async def run(self, ctx: StepContext) -> None:
        lo, hi = sorted((self.minimum, self.maximum))
        rng = ctx.humanizer.rng
        value: Any = rng.randint(int(lo), int(hi)) if self.decimals == 0 else round(rng.uniform(lo, hi), self.decimals)
        ctx.variables.set(self.variable, str(value))


@register
class ReadCsvRowStep(StepBase):
    type: Literal["read_csv_row"] = "read_csv_row"
    file: str = Field("", title="CSV file", description="Columns become variables: {{row.column_name}}.")
    mode: Literal["next", "random", "same_as_account"] = Field(
        "next", title="Which row", description="next = each account gets the next row; "
                                               "same_as_account = row number = account position.")
    when_finished: Literal["start_over", "fail"] = Field("start_over", title="When all rows are used")
    TITLE = "Read next row from CSV"
    CATEGORY = CAT
    ICON = "table"

    def summary(self) -> str:
        return f"Read the {self.mode.replace('_', ' ')} row from {Path(self.file).name or '…'}"

    def problems(self) -> list[str]:
        return super().problems() + ([] if self.file.strip() else ["Choose a CSV file."])

    async def run(self, ctx: StepContext) -> None:
        path = Path(str(ctx.render(self.file)))
        key = f"csv:{path.resolve()}"
        async with ctx.shared_lock:
            cache = ctx.shared.setdefault(key, {"rows": None, "cursor": 0})
            if cache["rows"] is None:
                cache["rows"] = load_csv(path)
            rows: list[dict[str, str]] = cache["rows"]
            if not rows:
                raise StepFailed(f"The CSV file {path.name} has no rows.")
            if self.mode == "random":
                row = ctx.humanizer.rng.choice(rows)
            else:
                idx = ctx.account_position if self.mode == "same_as_account" else cache["cursor"]
                if idx >= len(rows):
                    if self.when_finished == "fail":
                        raise StepFailed(f"All {len(rows)} rows of {path.name} are already used.")
                    idx %= len(rows)
                row = rows[idx]
                if self.mode == "next":
                    cache["cursor"] = idx + 1
        for col, value in row.items():
            if col:
                ctx.variables.set(f"row.{col.strip()}", value)


@register
class SaveValueStep(StepBase):
    type: Literal["save_value"] = "save_value"
    values: list[str] = Field(default_factory=lambda: ["value = {{value}}"], title="Values to save",
                              description="One per line as  column = text, e.g.  price = {{price}}")
    file: str = Field("", title="CSV file (optional)", description="Empty = save in the run results only.")
    TITLE = "Save value to results / CSV"
    CATEGORY = CAT
    ICON = "save"

    def summary(self) -> str:
        cols = ", ".join(v.split("=", 1)[0].strip() for v in self.values if "=" in v) or "…"
        return f"Save {cols}" + (f" to {Path(self.file).name}" if self.file else " to results")

    def problems(self) -> list[str]:
        bad = [v for v in self.values if v.strip() and "=" not in v]
        p = super().problems()
        if not self.values:
            p.append("Add at least one 'column = value' line.")
        if bad:
            p.append(f"Each line must look like  column = value  (problem: “{bad[0]}”).")
        return p

    async def run(self, ctx: StepContext) -> None:
        row: dict[str, Any] = {}
        for line in self.values:
            if "=" in line:
                col, _, template = line.partition("=")
                row[col.strip()] = ctx.render(template.strip())
        ctx.results.update(row)
        if not self.file.strip():
            return
        path = Path(str(ctx.render(self.file)))
        if not path.is_absolute():
            path = ctx.exports_dir / path
        full = {"account": ctx.account_name, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **row}
        async with ctx.shared_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            exists = path.exists() and path.stat().st_size > 0
            with path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(full.keys()), extrasaction="ignore")
                if not exists:
                    writer.writeheader()
                writer.writerow(full)


@register
class GetUrlStep(StepBase):
    type: Literal["get_url"] = "get_url"
    variable: str = Field("current_url", title="Save into variable")
    TITLE = "Get current URL"
    CATEGORY = CAT
    ICON = "link-2"

    def summary(self) -> str:
        return f"Save the current address into {{{{{self.variable or '?'}}}}}"

    def problems(self) -> list[str]:
        return super().problems() + _var_problem(self.variable)

    async def run(self, ctx: StepContext) -> None:
        ctx.variables.set(self.variable, ctx.require_page().url)
