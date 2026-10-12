"""Import/export of flows as JSON files (FlowPilot format or Chrome DevTools Recorder)."""

from __future__ import annotations

import json
from typing import Any

from flowpilot import __version__
from flowpilot.core.recorder import import_devtools_recording
from flowpilot.core.steps import StepBase, dump_steps, parse_steps

FORMAT = "flowpilot-flow"


def export_flow(name: str, description: str, steps: list[StepBase]) -> str:
    return json.dumps({"format": FORMAT, "app_version": __version__, "name": name, "description": description,
                       "steps": dump_steps(steps)}, indent=2, ensure_ascii=False)


def is_devtools_recording(data: Any) -> bool:
    if not isinstance(data, dict) or data.get("format") == FORMAT:
        return False
    steps = data.get("steps")
    return isinstance(steps, list) and any(isinstance(s, dict) and s.get("type") in
                                           ("navigate", "setViewport", "click", "change", "keyDown") and
                                           ("selectors" in s or "url" in s or "width" in s or "key" in s)
                                           for s in steps)


def import_flow(text: str) -> tuple[str, str, list[StepBase]]:
    """Parse a file's text into ``(name, description, steps)``. Raises ValueError on bad input."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"The file is not valid JSON: {exc}") from exc
    if is_devtools_recording(data):
        title, steps = import_devtools_recording(data)
        return title, "Imported from Chrome DevTools Recorder", steps
    if isinstance(data, list):  # a bare list of steps
        return "Imported flow", "", parse_steps(data)
    if isinstance(data, dict) and isinstance(data.get("steps"), list):
        return str(data.get("name") or "Imported flow"), str(data.get("description") or ""), parse_steps(data["steps"])
    raise ValueError("This file does not contain a FlowPilot flow or a DevTools recording.")
