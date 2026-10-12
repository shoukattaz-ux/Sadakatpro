"""Qt signals for engine events. The engine runs on the same (qasync) loop as the UI, so
events are re-emitted directly; widgets only ever update through these signals."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from flowpilot.core.engine import EngineEvent, RunEngine


class EngineBridge(QObject):
    any_event = Signal(object)  # every EngineEvent
    run_started = Signal(object)
    run_finished = Signal(object)
    item_status = Signal(object)
    item_step = Signal(object)
    item_screenshot = Signal(object)
    item_log = Signal(object)
    warning = Signal(object)
    #: "accounts" / "flows" / "connections" / "groups" / "runs" / "schedules" / "settings"
    data_changed = Signal(str)

    _DISPATCH = ("run_started", "run_finished", "item_status", "item_step", "item_screenshot", "item_log", "warning")

    def __init__(self, engine: RunEngine) -> None:
        super().__init__()
        engine.subscribe(self._on_event)

    def _on_event(self, e: EngineEvent) -> None:
        self.any_event.emit(e)
        if e.kind in self._DISPATCH:
            getattr(self, e.kind).emit(e)
        if e.kind == "run_finished":
            self.data_changed.emit("runs")
            self.data_changed.emit("accounts")
