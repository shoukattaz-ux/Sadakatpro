"""Everything a page needs: services, the engine bridge and the editor's debug session."""

from __future__ import annotations

from dataclasses import dataclass

from flowpilot.config import AppSettings
from flowpilot.core.debug_session import DebugSession
from flowpilot.services.container import Services
from flowpilot.ui.bridge import EngineBridge


@dataclass
class AppContext:
    services: Services
    bridge: EngineBridge
    debug: DebugSession

    @classmethod
    def create(cls, services: Services) -> AppContext:
        debug = DebugSession(services.repos, services.adapters, services.playwright, services.settings,
                             services.paths.exports, services.paths.screenshots / "test-runs")
        return cls(services, EngineBridge(services.engine), debug)

    @property
    def settings(self) -> AppSettings:
        return self.services.settings.settings

    def save_settings(self) -> None:
        self.services.settings.save()
        self.bridge.data_changed.emit("settings")

    @property
    def repos(self):  # noqa: ANN201
        return self.services.repos
