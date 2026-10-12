"""Service container: builds and owns every long-lived object (no Qt dependency)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flowpilot.config import AppPaths, SettingsStore, default_data_dir
from flowpilot.core.adapter_pool import AdapterPool
from flowpilot.core.engine import RunEngine
from flowpilot.core.session import PlaywrightHolder
from flowpilot.security import SecretBox
from flowpilot.services.notifications import Notifier
from flowpilot.services.scheduler import SchedulerService
from flowpilot.storage.db import Database
from flowpilot.storage.repositories import Repositories


@dataclass
class Services:
    paths: AppPaths
    settings: SettingsStore
    db: Database
    secrets: SecretBox
    repos: Repositories
    adapters: AdapterPool
    playwright: PlaywrightHolder
    engine: RunEngine
    scheduler: SchedulerService
    notifier: Notifier

    @classmethod
    def create(cls, data_dir: Path | None = None) -> Services:
        paths = AppPaths(data_dir or default_data_dir()).ensure()
        settings = SettingsStore(paths.settings_file)
        db = Database(paths.db_file)
        secrets = SecretBox(paths.root)
        repos = Repositories(db, secrets)
        adapters = AdapterPool(repos.connections)
        playwright = PlaywrightHolder()
        engine = RunEngine(repos, settings, paths, adapters, playwright)
        scheduler = SchedulerService(repos, engine)
        notifier = Notifier(settings)
        notifier.attach(engine)
        return cls(paths, settings, db, secrets, repos, adapters, playwright, engine, scheduler, notifier)

    async def aclose(self) -> None:
        self.scheduler.shutdown()
        await self.engine.shutdown()
        await self.adapters.aclose()
        self.db.dispose()
