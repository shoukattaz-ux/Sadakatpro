"""FlowDocument: the editable list of steps with undo/redo (no Qt; the editor wraps it)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from flowpilot.core.runner import block_depths, flow_problems
from flowpilot.core.steps import StepBase, dump_steps, new_step, parse_steps
from flowpilot.core.steps.base import new_step_id

MAX_UNDO = 200

#: block opener -> markers inserted together with it
BLOCK_COMPANIONS = {
    "if": ["else", "end_if"],
    "loop": ["end_loop"],
    "try": ["catch", "end_try"],
}


class FlowDocument:
    """Steps plus undo/redo history. Every mutating method records one undo entry."""

    def __init__(self, steps: list[StepBase] | None = None) -> None:
        self.steps: list[StepBase] = list(steps or [])
        self._undo: list[str] = []
        self._redo: list[str] = []
        self.dirty = False
        self.on_change: Callable[[], None] | None = None

    # -- serialisation -------------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(dump_steps(self.steps))

    @classmethod
    def from_json(cls, text: str) -> FlowDocument:
        return cls(parse_steps(json.loads(text or "[]")))

    # -- history ---------------------------------------------------------------------
    def _snapshot(self) -> None:
        self._undo.append(self.to_json())
        if len(self._undo) > MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()

    def _changed(self) -> None:
        self.dirty = True
        if self.on_change:
            self.on_change()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.to_json())
        self.steps = parse_steps(json.loads(self._undo.pop()))
        self._changed()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.to_json())
        self.steps = parse_steps(json.loads(self._redo.pop()))
        self._changed()
        return True

    def reset(self, steps: list[StepBase]) -> None:
        """Load new steps and clear history (e.g. when switching flows)."""
        self.steps = list(steps)
        self._undo.clear()
        self._redo.clear()
        self.dirty = False

    # -- queries -------------------------------------------------------------------------
    def index_of(self, step_id: str) -> int:
        for i, s in enumerate(self.steps):
            if s.id == step_id:
                return i
        return -1

    def problems(self) -> dict[int, list[str]]:
        return flow_problems(self.steps)

    def depths(self) -> list[int]:
        return block_depths(self.steps)

    def labels(self) -> list[str]:
        names = []
        for s in self.steps:
            name = getattr(s, "name", "") if s.type == "label_marker" else s.label
            if name and name.strip():
                names.append(name.strip())
        return names

    def defined_variables(self) -> list[str]:
        """Variable names the flow sets (Set variable, Extract, Random …, Get URL, Run JS)."""
        names: list[str] = []
        for s in self.steps:
            name = str(getattr(s, "variable", "") or "").strip().strip("{}").strip()
            if name and s.type != "if_variable" and s.type != "loop_list" and name not in names:
                names.append(name)
        return names

    def block_range(self, index: int) -> tuple[int, int]:
        """For a block opener, the index range it spans (inclusive); otherwise (index, index)."""
        step = self.steps[index]
        if step.BLOCK not in BLOCK_COMPANIONS:
            return index, index
        depth = 0
        for j in range(index, len(self.steps)):
            b = self.steps[j].BLOCK
            if b in BLOCK_COMPANIONS:
                depth += 1
            elif b in ("end_if", "end_loop", "end_try"):
                depth -= 1
                if depth == 0:
                    return index, j
        return index, index

    # -- edits ---------------------------------------------------------------------------
    def insert(self, index: int, type_name: str, with_block: bool = True, **values: Any) -> list[int]:
        """Insert a new step (and its End/Else markers for block steps). Returns new indexes."""
        self._snapshot()
        step = new_step(type_name, **values)
        added = [step]
        if with_block and step.BLOCK in BLOCK_COMPANIONS:
            added += [new_step(t) for t in _companion_types(step)]
        index = max(0, min(index, len(self.steps)))
        self.steps[index:index] = added
        self._changed()
        return list(range(index, index + len(added)))

    def insert_steps(self, index: int, steps: list[StepBase]) -> list[int]:
        self._snapshot()
        index = max(0, min(index, len(self.steps)))
        self.steps[index:index] = steps
        self._changed()
        return list(range(index, index + len(steps)))

    def append_step(self, step: StepBase, replace_last: bool = False) -> int:
        """Used by the recorder (one undo entry per recorded action)."""
        self._snapshot()
        if replace_last and self.steps:
            self.steps[-1] = step
        else:
            self.steps.append(step)
        self._changed()
        return len(self.steps) - 1

    def update(self, index: int, step: StepBase, record: bool = True) -> None:
        if record:
            self._snapshot()
        self.steps[index] = step
        self._changed()

    def delete(self, indexes: list[int]) -> None:
        if not indexes:
            return
        self._snapshot()
        for i in sorted(set(indexes), reverse=True):
            if 0 <= i < len(self.steps):
                del self.steps[i]
        self._changed()

    def duplicate(self, indexes: list[int]) -> list[int]:
        """Copy the selected steps (blocks are copied whole) right after the last one."""
        if not indexes:
            return []
        self._snapshot()
        expanded: list[int] = []
        for i in sorted(set(indexes)):
            lo, hi = self.block_range(i)
            for j in range(lo, hi + 1):
                if j not in expanded:
                    expanded.append(j)
        copies = [s.model_copy(update={"id": new_step_id()}, deep=True) for s in (self.steps[j] for j in expanded)]
        at = max(expanded) + 1
        self.steps[at:at] = copies
        self._changed()
        return list(range(at, at + len(copies)))

    def move(self, indexes: list[int], target: int) -> list[int]:
        """Move the selected steps so they start at ``target`` (index in the list before the move)."""
        idx = sorted(set(i for i in indexes if 0 <= i < len(self.steps)))
        if not idx:
            return []
        self._snapshot()
        moving = [self.steps[i] for i in idx]
        before = sum(1 for i in idx if i < target)
        for i in reversed(idx):
            del self.steps[i]
        at = max(0, min(target - before, len(self.steps)))
        self.steps[at:at] = moving
        self._changed()
        return list(range(at, at + len(moving)))

    def reorder(self, ids: list[str]) -> None:
        """Apply an order coming from a drag & drop view (list of step ids)."""
        by_id = {s.id: s for s in self.steps}
        if sorted(ids) != sorted(by_id):
            return
        if ids == [s.id for s in self.steps]:
            return
        self._snapshot()
        self.steps = [by_id[i] for i in ids]
        self._changed()

    def set_enabled(self, indexes: list[int], enabled: bool) -> None:
        self._snapshot()
        for i in indexes:
            self.steps[i] = self.steps[i].model_copy(update={"enabled": enabled})
        self._changed()

    def toggle_collapsed(self, index: int) -> None:
        s = self.steps[index]
        self.steps[index] = s.model_copy(update={"collapsed": not s.collapsed})
        self._changed()

    def hidden_rows(self) -> set[int]:
        """Rows inside collapsed blocks (the end marker stays visible)."""
        hidden: set[int] = set()
        for i, s in enumerate(self.steps):
            if s.collapsed and s.BLOCK in BLOCK_COMPANIONS:
                lo, hi = self.block_range(i)
                hidden.update(range(lo + 1, hi))
        return hidden


def _companion_types(step: StepBase) -> list[str]:
    return list(BLOCK_COMPANIONS.get(step.BLOCK or "", []))
