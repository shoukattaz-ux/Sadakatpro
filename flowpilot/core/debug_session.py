"""A browser profile kept open while editing a flow (recorder, Pick element, test steps)."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from flowpilot.config import SettingsStore
from flowpilot.core.adapter_pool import AdapterPool
from flowpilot.core.context import RunControl, StepContext
from flowpilot.core.errors import ValidationProblem
from flowpilot.core.humanize import Humanizer
from flowpilot.core.recorder import Recorder
from flowpilot.core.runner import FlowRunner, RunOutcome, StepEvent
from flowpilot.core.session import BrowserSession, PlaywrightHolder
from flowpilot.core.steps import StepBase
from flowpilot.core.variables import Variables
from flowpilot.storage.models import Account
from flowpilot.storage.repositories import Repositories


class DebugSession:
    def __init__(self, repos: Repositories, adapters: AdapterPool, playwright: PlaywrightHolder,
                 settings: SettingsStore, exports_dir: Any, artifacts_dir: Any) -> None:
        self.repos = repos
        self.adapters = adapters
        self.playwright = playwright
        self.settings_store = settings
        self.exports_dir = exports_dir
        self.artifacts_dir = artifacts_dir
        self.account: Account | None = None
        self.session: BrowserSession | None = None
        self.recorder: Recorder | None = None
        self.variables = Variables()
        self.control = RunControl()

    @property
    def is_open(self) -> bool:
        return self.session is not None and not self.session.closed and not self.session.disconnected

    async def open(self, account: Account) -> None:
        if self.is_open and self.account is not None and self.account.id == account.id:
            return
        await self.close()
        conn = self.repos.connections.get(account.connection_id)
        if conn is None:
            raise ValidationProblem(f"Account “{account.name}” has no browser connection.")
        s = self.settings_store.settings
        session = BrowserSession(self.adapters.get(conn), account.remote_profile_id, self.playwright,
                                 auto_switch_tabs=s.auto_switch_new_tab, dialog_action=s.dialog_action)
        await session.open()
        self.session = session
        self.account = account
        self.variables = Variables(account=self.repos.accounts.variables(account) | {
            "name": account.name, "profile_id": account.remote_profile_id})

    def recorder_for(self, on_step: Callable[[StepBase, bool], None],
                     on_status: Callable[[str], None] | None = None) -> Recorder:
        assert self.session is not None
        self.recorder = Recorder(self.session, on_step, on_status)
        return self.recorder

    def _context(self) -> StepContext:
        assert self.session is not None and self.account is not None
        s = self.settings_store.settings
        self.control = RunControl()
        ctx = StepContext(
            settings=s, variables=self.variables, control=self.control,
            humanizer=Humanizer(s.humanize, sleep=self.control.sleep, rng=random.Random()),
            page=self.session.page, browser_context=self.session.context, browser=self.session.browser,
            test_mode=True, account_name=self.account.name, flow_name="(test)",
            artifacts_dir=self.artifacts_dir, exports_dir=self.exports_dir,
        )
        session = self.session

        async def close_cb() -> None:
            await session.close(stop_profile=True)

        ctx.close_browser_cb = close_cb
        return ctx

    async def run(self, steps: list[StepBase], start: int = 0, stop_at: int | None = None,
                  on_event: Callable[[StepEvent], None] | None = None,
                  log: Callable[[str, str], None] | None = None) -> RunOutcome:
        """Run steps on the open page. ``stop_at`` = last index to run (inclusive)."""
        if not self.is_open:
            raise ValidationProblem("Open a test profile first.")
        ctx = self._context()
        if log:
            ctx.log = log
        runner = FlowRunner(steps, ctx, on_event)
        try:
            return await runner.run(start=start, stop_at=stop_at)
        finally:
            if self.session is not None and ctx.page is not None:
                self.session.page = ctx.page

    def stop(self) -> None:
        self.control.stop()

    async def close(self) -> None:
        if self.recorder is not None:
            try:
                await self.recorder.stop()
            except Exception:
                pass
            self.recorder = None
        if self.session is not None:
            await self.session.close(stop_profile=True)
        self.session = None
        self.account = None
