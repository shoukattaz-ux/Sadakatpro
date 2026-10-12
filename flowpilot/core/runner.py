"""FlowRunner: executes a list of steps on one page.

The flow is a flat list. :func:`compile_flow` matches the logic blocks and builds jump
tables; :class:`FlowRunner` then runs it with a program counter. This keeps the editor
simple (a list of cards) while supporting If/Else, loops, Go to and Try/On error.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from flowpilot.core.context import StepContext
from flowpilot.core.errors import (
    BrowserDisconnected,
    FlowError,
    FlowStopped,
    StepFailed,
    StopRequested,
    ValidationProblem,
    friendly_error,
    is_disconnect,
)
from flowpilot.core.steps.base import StepBase
from flowpilot.core.steps.logic import BreakLoopStep, GoToStep, LabelStep, StopFlowStep

_OPENERS = {"if": "end_if", "loop": "end_loop", "try": "end_try"}
_NAMES = {"if": "If", "loop": "Repeat/Loop", "try": "Try", "end_if": "End If", "end_loop": "End loop",
          "end_try": "End try", "else": "Else", "catch": "On error"}


@dataclass
class FlowProgram:
    """Jump tables for a flat step list."""

    steps: list[StepBase]
    block_end: dict[int, int] = field(default_factory=dict)  # opener -> its end marker
    middle: dict[int, int] = field(default_factory=dict)  # if -> else, try -> catch
    middle_end: dict[int, int] = field(default_factory=dict)  # else/catch -> end marker
    end_start: dict[int, int] = field(default_factory=dict)  # end marker -> opener
    parent_loop: dict[int, int] = field(default_factory=dict)  # index -> innermost loop opener
    labels: dict[str, int] = field(default_factory=dict)
    depth: list[int] = field(default_factory=list)


def _structure(steps: list[StepBase]) -> tuple[FlowProgram, dict[int, list[str]]]:
    """Match blocks; returns the program and structure problems by step index."""
    prog = FlowProgram(steps=steps)
    problems: dict[int, list[str]] = {}
    stack: list[int] = []
    for i, step in enumerate(steps):
        block = step.BLOCK
        prog.depth.append(len(stack) - (1 if block in ("else", "catch", "end_if", "end_loop", "end_try") else 0))
        loops = [s for s in stack if steps[s].BLOCK == "loop"]
        if loops:
            prog.parent_loop[i] = loops[-1]
        name = step.name if isinstance(step, LabelStep) else step.label
        if name and name.strip():
            if name.strip() in prog.labels:
                problems.setdefault(i, []).append(f"The label “{name}” is used twice.")
            prog.labels.setdefault(name.strip(), i)
        if block in _OPENERS:
            stack.append(i)
        elif block in ("else", "catch"):
            want = "if" if block == "else" else "try"
            if not stack or steps[stack[-1]].BLOCK != want:
                problems.setdefault(i, []).append(f"“{_NAMES[block]}” has no matching “{_NAMES[want]}” above it.")
            elif stack[-1] in prog.middle:
                problems.setdefault(i, []).append(f"This “{_NAMES[want]}” already has a “{_NAMES[block]}”.")
            else:
                prog.middle[stack[-1]] = i
        elif block in ("end_if", "end_loop", "end_try"):
            want = {"end_if": "if", "end_loop": "loop", "end_try": "try"}[block]
            if not stack or steps[stack[-1]].BLOCK != want:
                problems.setdefault(i, []).append(f"“{_NAMES[block]}” has no matching “{_NAMES[want]}” above it.")
            else:
                opener = stack.pop()
                prog.block_end[opener] = i
                prog.end_start[i] = opener
                if opener in prog.middle:
                    prog.middle_end[prog.middle[opener]] = i
        if isinstance(step, BreakLoopStep) and i not in prog.parent_loop:
            problems.setdefault(i, []).append("“Exit loop” must be inside a Repeat or Loop block.")
    for opener in stack:
        block = steps[opener].BLOCK or ""
        problems.setdefault(opener, []).append(f"“{_NAMES[block]}” is missing its “{_NAMES[_OPENERS[block]]}”.")
    for i, step in enumerate(steps):
        if isinstance(step, GoToStep) and step.goto_label.strip() and step.goto_label.strip() not in prog.labels:
            problems.setdefault(i, []).append(f"There is no label called “{step.goto_label}”.")
        if step.on_error == "goto" and step.on_error_label.strip() and step.on_error_label.strip() not in prog.labels:
            problems.setdefault(i, []).append(f"There is no label called “{step.on_error_label}”.")
    prog.depth = [max(0, d) for d in prog.depth]
    return prog, problems


def flow_problems(steps: list[StepBase]) -> dict[int, list[str]]:
    """All problems (structure + per step) by step index, for the editor."""
    _, problems = _structure(steps)
    for i, step in enumerate(steps):
        if step.enabled:
            issues = step.problems()
            if issues:
                problems.setdefault(i, []).extend(issues)
    return problems


def block_depths(steps: list[StepBase]) -> list[int]:
    """Indentation level of each step (for display)."""
    prog, _ = _structure(steps)
    return prog.depth


def compile_flow(steps: list[StepBase]) -> FlowProgram:
    """Validate and compile; raises :class:`ValidationProblem` with a friendly message."""
    prog, _ = _structure(steps)
    problems = flow_problems(steps)
    if problems:
        idx = min(problems)
        more = f" (and {len(problems) - 1} more step(s) with problems)" if len(problems) > 1 else ""
        raise ValidationProblem(f"Step {idx + 1} “{steps[idx].TITLE}”: {problems[idx][0]}{more}")
    return prog


@dataclass
class StepEvent:
    index: int
    step: StepBase
    status: str  # running / ok / failed / skipped / retry
    duration: float = 0.0
    error: str = ""
    detail: str = ""

    def to_timeline(self) -> dict[str, Any]:
        return {
            "index": self.index + 1,
            "title": self.step.TITLE,
            "summary": self.step.summary(),
            "status": self.status,
            "duration": round(self.duration, 2),
            "error": self.error,
            "detail": self.detail,
            "time": time.strftime("%H:%M:%S"),
        }


@dataclass
class _LoopState:
    items: list[Any]
    index: int = 0


@dataclass
class RunOutcome:
    success: bool
    message: str = ""
    close_browser: bool = False


class FlowRunner:
    """Runs a compiled flow on a :class:`StepContext`."""

    MAX_TOTAL_JUMPS = 10_000

    def __init__(self, steps: list[StepBase], ctx: StepContext,
                 on_event: Callable[[StepEvent], None] | None = None) -> None:
        self.steps = steps
        self.ctx = ctx
        self.on_event = on_event or (lambda e: None)
        self.program = compile_flow(steps)
        self._loops: dict[int, _LoopState] = {}
        self._tries: list[int] = []
        self._jumps = 0
        self._goto_counts: dict[int, int] = {}

    # -- events --------------------------------------------------------------------
    def _emit(self, index: int, status: str, duration: float = 0.0, error: str = "", detail: str = "") -> None:
        self.on_event(StepEvent(index, self.steps[index], status, duration, error, detail))

    # -- main loop -------------------------------------------------------------------
    async def run(self, start: int = 0, stop_at: int | None = None) -> RunOutcome:
        """Run from ``start``; if ``stop_at`` is given, stop after that index."""
        pc = start
        ctrl = self.ctx.control
        try:
            while pc < len(self.steps) and (stop_at is None or pc <= stop_at):
                await ctrl.checkpoint()
                pc = await self._step(pc)
        except FlowStopped as stop:
            return RunOutcome(stop.success, stop.message, stop.close_browser)
        if self.ctx.forced_status == "failed":
            return RunOutcome(False, self.ctx.forced_message or "Marked as failed.")
        return RunOutcome(True, self.ctx.forced_message)

    async def _step(self, pc: int) -> int:
        prog, step = self.program, self.steps[pc]
        block = step.BLOCK
        if block == "if":
            if not step.enabled:
                return prog.block_end[pc] + 1
            ok, result = await self._guarded(pc, step, lambda: step.evaluate(self.ctx))  # type: ignore[attr-defined]
            if not ok:
                return result
            if result:
                return pc + 1
            return (prog.middle[pc] + 1) if pc in prog.middle else prog.block_end[pc] + 1
        if block in ("else", "catch"):
            if block == "catch":
                self._pop_try(prog.end_start[prog.middle_end[pc]])
            return prog.middle_end[pc] + 1
        if block == "end_if":
            return pc + 1
        if block == "loop":
            return await self._loop_start(pc, step)
        if block == "end_loop":
            opener = prog.end_start[pc]
            if opener in self._loops:
                self._loops[opener].index += 1
            return opener
        if block == "try":
            if not step.enabled:
                return prog.block_end[pc] + 1
            self._tries.append(pc)
            return pc + 1
        if block == "end_try":
            self._pop_try(prog.end_start[pc])
            return pc + 1
        if isinstance(step, BreakLoopStep):
            if not step.enabled:
                return pc + 1
            opener = prog.parent_loop[pc]
            self._loops.pop(opener, None)
            self._emit(pc, "ok")
            return prog.block_end[opener] + 1
        if isinstance(step, GoToStep):
            if not step.enabled:
                return pc + 1
            count = self._goto_counts.get(pc, 0) + 1
            self._goto_counts[pc] = count
            if count > step.max_jumps:
                raise StepFailed(f"“Go to {step.goto_label}” ran more than {step.max_jumps} times; stopped to avoid an endless loop.")
            self._emit(pc, "ok")
            return self._jump(prog.labels[step.goto_label.strip()])
        if isinstance(step, StopFlowStep):
            if not step.enabled:
                return pc + 1
            self._emit(pc, "ok")
            raise FlowStopped(step.result == "success", str(self.ctx.render(step.message)))
        if isinstance(step, LabelStep):
            return pc + 1
        if not step.enabled:
            self._emit(pc, "skipped")
            return pc + 1
        ok, result = await self._guarded(pc, step, lambda: step.run(self.ctx))
        return pc + 1 if ok else result

    async def _loop_start(self, pc: int, step: StepBase) -> int:
        end = self.program.block_end[pc]
        if not step.enabled:
            return end + 1
        state = self._loops.get(pc)
        if state is None:
            ok, items = await self._guarded(pc, step, lambda: step.items(self.ctx))  # type: ignore[attr-defined]
            if not ok:
                return items
            state = self._loops[pc] = _LoopState(list(items))
        if state.index >= len(state.items):
            del self._loops[pc]
            return end + 1
        item = state.items[state.index]
        v = self.ctx.variables
        v.set("loop.index", state.index + 1)
        v.set("loop.count", len(state.items))
        if isinstance(item, dict):
            for col, value in item.items():
                if col:
                    v.set(f"row.{str(col).strip()}", value)
            v.set("loop.item", ", ".join(str(x) for x in item.values()))
        else:
            v.set("loop.item", item)
        return pc + 1

    def _pop_try(self, opener: int) -> None:
        if opener in self._tries:
            while self._tries and self._tries[-1] != opener:
                self._tries.pop()
            if self._tries:
                self._tries.pop()

    def _jump(self, target: int) -> int:
        """Jump to ``target``, discarding loop/try state of blocks we leave."""
        self._jumps += 1
        if self._jumps > self.MAX_TOTAL_JUMPS:
            raise StepFailed("Too many jumps; the flow seems to be stuck in an endless loop.")
        prog = self.program
        for opener in list(self._loops):
            if not (opener < target <= prog.block_end[opener]):
                del self._loops[opener]
        self._tries = [t for t in self._tries if t < target <= prog.block_end[t]]
        return target

    # -- execution with retries and error policy ------------------------------------------
    async def _guarded(self, pc: int, step: StepBase, action: Callable[[], Any]) -> tuple[bool, Any]:
        """Run ``action`` with delays, retries and the step's error policy.

        Returns ``(True, result)`` on success or ``(False, next_pc)`` when the error policy
        decided where to continue. Raises when the flow must end.
        """
        ctx = self.ctx
        settings = ctx.settings
        retries = step.retries if step.retries is not None else settings.step_retries
        retry_delay = step.retry_delay if step.retry_delay is not None else settings.step_retry_delay_s
        rng = ctx.humanizer.rng
        last_exc: BaseException | None = None
        for attempt in range(max(0, retries) + 1):
            started = time.monotonic()
            self._emit(pc, "running" if attempt == 0 else "retry", detail=f"attempt {attempt + 1}" if attempt else "")
            try:
                if not step.delay_before.is_zero:
                    await ctx.pause_between(rng.uniform(*sorted((step.delay_before.min, step.delay_before.max))))
                result = await action()
                if not step.delay_after.is_zero:
                    await ctx.pause_between(rng.uniform(*sorted((step.delay_after.min, step.delay_after.max))))
                if ctx.humanize_for(step) and step.BLOCK is None:
                    await ctx.control.sleep(ctx.humanizer.step_delay())
                if ctx.test_mode:
                    await ctx.control.sleep(0.4)
                self._emit(pc, "ok", time.monotonic() - started,
                           detail=("yes" if result else "no") if step.BLOCK == "if" else "")
                return True, result
            except (FlowStopped, StopRequested):
                raise
            except Exception as exc:  # noqa: BLE001 - every failure is reported to the user
                last_exc = exc
                if ctx.disconnected or is_disconnect(exc):
                    ctx.disconnected = True
                    self._emit(pc, "failed", time.monotonic() - started, "The browser was closed or disconnected.")
                    raise BrowserDisconnected("The browser was closed or disconnected.",
                                              detail=_detail(exc)) from exc
                if attempt < retries:
                    ctx.log("warning", f"Step {pc + 1} failed ({friendly_error(exc)}), retrying…")
                    await ctx.control.sleep(retry_delay)
                    continue
                self._emit(pc, "failed", time.monotonic() - started, friendly_error(exc), _detail(exc))
        assert last_exc is not None
        return False, self._apply_policy(pc, step, last_exc)

    def _apply_policy(self, pc: int, step: StepBase, exc: BaseException) -> int:
        message = friendly_error(exc)
        ctx = self.ctx
        if step.on_error == "skip":
            ctx.log("warning", f"Step {pc + 1} failed and was skipped: {message}")
            return pc + 1
        if step.on_error == "goto":
            ctx.log("warning", f"Step {pc + 1} failed, going to “{step.on_error_label}”: {message}")
            ctx.variables.set("error.message", message)
            return self._jump(self.program.labels[step.on_error_label.strip()])
        full = f"Step {pc + 1} “{step.summary()}”: {message}"
        if step.on_error == "fail_close":
            raise FlowStopped(False, full, close_browser=True)
        if self._tries:
            opener = self._tries.pop()
            ctx.variables.set("error.message", message)
            ctx.variables.set("error.step", pc + 1)
            ctx.log("warning", f"Step {pc + 1} failed, running the 'On error' steps: {message}")
            if opener in self.program.middle:
                return self._jump(self.program.middle[opener] + 1)
            return self._jump(self.program.block_end[opener] + 1)
        err = StepFailed(full, detail=_detail(exc))
        err.step_index = pc  # type: ignore[attr-defined]
        raise err


def _detail(exc: BaseException) -> str:
    if isinstance(exc, FlowError) and exc.detail:
        return exc.detail
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()[:4000]
