"""RunEngine: runs a flow on many accounts.

* Groups run **one after another** (with a random delay between groups).
* Inside a group, up to ``concurrency`` accounts run at the same time (``asyncio.Semaphore``).
* Browser launches are **staggered**: after a browser starts, the next one waits a random
  ``launch_delay`` (the "launch gate"), even when a slot is already free.
* Every account is isolated: its failure never affects the others, and its browser is
  always closed and its profile stopped in ``finally``.
* Pause/Resume/Stop work globally and per account (cooperative, see :class:`RunControl`);
  Force stop additionally cancels the tasks.
* A watchdog cancels accounts that show no activity for too long; each account also has
  a maximum run time.
* Run and item state is stored in the database so an interrupted batch can be resumed.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from flowpilot.adapters.base import AdapterError
from flowpilot.config import AppPaths, AppSettings, SettingsStore
from flowpilot.core.adapter_pool import AdapterPool
from flowpilot.core.context import RunControl, StepContext
from flowpilot.core.errors import (
    BrowserDisconnected,
    FlowError,
    StopRequested,
    ValidationProblem,
    friendly_error,
)
from flowpilot.core.humanize import Humanizer
from flowpilot.core.runner import FlowRunner, StepEvent, compile_flow
from flowpilot.core.session import BrowserSession, PlaywrightHolder
from flowpilot.core.steps import parse_steps
from flowpilot.core.steps.base import StepBase
from flowpilot.core.variables import Variables
from flowpilot.storage.models import Account, Flow, Run, RunItem, utcnow
from flowpilot.storage.repositories import Repositories

# Item statuses shown on the run cards
QUEUED, STARTING, RUNNING, WAITING, DONE, FAILED, STOPPED, SKIPPED = (
    "queued", "starting", "running", "waiting", "done", "failed", "stopped", "skipped")
FINAL_STATUSES = {DONE, FAILED, STOPPED, SKIPPED}


@dataclass
class RunOptions:
    """Dashboard settings for one batch."""

    concurrency: int = 1
    launch_delay_min: float = 3.0
    launch_delay_max: float = 8.0
    group_delay_min: float = 0.0
    group_delay_max: float = 0.0
    test_mode: bool = False
    close_browser_at_end: bool | None = None  # None = use Settings
    retry_failed: int | None = None  # None = use Settings
    skip_succeeded_today: bool | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunOptions:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class TargetSpec:
    """Which accounts to run. ``mode``: all / groups / accounts."""

    mode: str = "all"
    group_ids: list[int] = field(default_factory=list)
    account_ids: list[int] = field(default_factory=list)
    group_order: list[int] = field(default_factory=list)  # optional explicit group order

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetSpec:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class EngineEvent:
    """Sent to listeners (the UI bridge). ``kind`` values:

    run_started, run_finished, item_status, item_step, item_screenshot, item_log,
    warning, group_started, group_waiting
    """

    kind: str
    run_id: int
    item_id: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class _ItemState:
    item: RunItem
    account: Account
    control: RunControl
    task: asyncio.Task[Any] | None = None
    session: BrowserSession | None = None
    timeline: list[dict[str, Any]] = field(default_factory=list)
    kill_reason: str = ""  # set by the watchdog before cancelling


@dataclass
class _ActiveRun:
    run: Run
    flow: Flow
    steps: list[StepBase]
    options: RunOptions
    control: RunControl
    groups: list[tuple[str, list[_ItemState]]]
    shared: dict[str, Any] = field(default_factory=dict)
    shared_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_launch: float = 0.0
    launch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task[Any] | None = None
    started: float = field(default_factory=time.monotonic)

    @property
    def items(self) -> list[_ItemState]:
        return [s for _, states in self.groups for s in states]


Listener = Callable[[EngineEvent], None]


def resource_usage() -> tuple[float, float] | None:
    """(cpu %, ram %) or None when psutil is not installed."""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return None
    return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent


class RunEngine:
    """Owns all running batches."""

    WATCHDOG_INTERVAL_S = 10.0

    def __init__(self, repos: Repositories, settings: SettingsStore, paths: AppPaths, adapters: AdapterPool,
                 playwright: PlaywrightHolder | None = None, rng: random.Random | None = None) -> None:
        self.repos = repos
        self.settings_store = settings
        self.paths = paths
        self.adapters = adapters
        self.playwright = playwright or PlaywrightHolder()
        self.rng = rng or random.Random()
        self.listeners: list[Listener] = []
        self.runs: dict[int, _ActiveRun] = {}
        self._watchdog: asyncio.Task[Any] | None = None

    @property
    def settings(self) -> AppSettings:
        return self.settings_store.settings

    # ------------------------------------------------------------------ events
    def subscribe(self, listener: Listener) -> None:
        self.listeners.append(listener)

    def _emit(self, kind: str, run_id: int, item_id: int | None = None, **data: Any) -> None:
        event = EngineEvent(kind, run_id, item_id, data)
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception as exc:  # a broken listener must never break a run
                logger.exception("Engine listener failed: {}", exc)

    # ------------------------------------------------------------------ targets
    def resolve_targets(self, target: TargetSpec, flow_id: int | None,
                        skip_succeeded: bool = False) -> list[tuple[str, int | None, list[Account]]]:
        """Return ``[(group name, group id, accounts)]`` in run order. Disabled accounts are
        skipped; accounts without a group form a final "No group" batch."""
        groups = self.repos.groups.list()
        order = {gid: pos for pos, gid in enumerate(target.group_order)}
        groups.sort(key=lambda g: (order.get(g.id, 10_000 + g.order), g.id))
        accounts = self.repos.accounts.list(only_enabled=True)
        if target.mode == "groups":
            wanted = set(target.group_ids)
            accounts = [a for a in accounts if a.group_id in wanted]
        elif target.mode == "accounts":
            by_id = {a.id: a for a in accounts}
            accounts = [by_id[i] for i in target.account_ids if i in by_id]
        if skip_succeeded and accounts:
            done = self.repos.runs.succeeded_today([a.id for a in accounts], flow_id)
            accounts = [a for a in accounts if a.id not in done]
        result: list[tuple[str, int | None, list[Account]]] = []
        for g in groups:
            members = [a for a in accounts if a.group_id == g.id]
            if members:
                result.append((g.name, g.id, members))
        known = {g.id for g in groups}
        loose = [a for a in accounts if a.group_id not in known]
        if loose:
            result.append(("No group", None, loose))
        return result

    # ------------------------------------------------------------------ start / resume
    async def start(self, flow: Flow, target: TargetSpec, options: RunOptions, source: str = "manual") -> int:
        """Create a run and start it in the background; returns the run id.

        Raises :class:`ValidationProblem` if the flow is invalid or no account matches."""
        steps = parse_steps(json.loads(flow.steps_json or "[]"))
        if not steps:
            raise ValidationProblem("This flow has no steps yet.")
        compile_flow(steps)
        skip = self.settings.skip_succeeded_today if options.skip_succeeded_today is None else options.skip_succeeded_today
        groups = self.resolve_targets(target, flow.id, skip_succeeded=skip)
        if options.test_mode:
            first = next(((n, gid, accs[:1]) for n, gid, accs in groups if accs), None)
            groups = [first] if first else []
        if not groups:
            raise ValidationProblem("No enabled accounts match the selected target.")
        missing = [a.name for _, _, accs in groups for a in accs if a.connection_id is None]
        if missing:
            raise ValidationProblem(f"Account “{missing[0]}” has no browser connection. Set it on the Accounts page.")
        run = Run(flow_id=flow.id, flow_name=flow.name, status="running", source=source,
                  total=sum(len(a) for _, _, a in groups),
                  settings_json=json.dumps({"options": asdict(options), "target": asdict(target)}))
        items: list[RunItem] = []
        position = 0
        for name, _, accs in groups:
            for acc in accs:
                items.append(RunItem(account_id=acc.id, account_name=acc.name, group_name=name, position=position))
                position += 1
        run, items = self.repos.runs.create_run(run, items)
        return self._launch(run, flow, steps, options, groups, items)

    def _launch(self, run: Run, flow: Flow, steps: list[StepBase], options: RunOptions,
                groups: list[tuple[str, int | None, list[Account]]], items: list[RunItem]) -> int:
        control = RunControl()
        by_account: dict[int | None, list[RunItem]] = {}
        for it in items:
            by_account.setdefault(it.account_id, []).append(it)
        state_groups: list[tuple[str, list[_ItemState]]] = []
        for name, _, accs in groups:
            states = [_ItemState(by_account[a.id].pop(0), a, RunControl(parent=control)) for a in accs]
            state_groups.append((name, states))
        active = _ActiveRun(run, flow, steps, options, control, state_groups)
        self.runs[run.id] = active
        self._emit("run_started", run.id, flow_name=flow.name, total=run.total, test_mode=options.test_mode,
                   items=[{"item_id": s.item.id, "account": s.account.name, "group": name, "status": s.item.status}
                          for name, states in state_groups for s in states])
        active.task = asyncio.get_running_loop().create_task(self._run_batch(active))
        self._ensure_watchdog()
        return run.id

    def interrupted_runs(self) -> list[Run]:
        return [r for r in self.repos.runs.interrupted() if r.id not in self.runs]

    async def resume(self, run_id: int) -> int:
        """Continue a batch that was interrupted (app closed / crash): unfinished items run again."""
        run = self.repos.runs.get(run_id)
        if run is None:
            raise ValidationProblem("That run no longer exists.")
        flow = self.repos.flows.get(run.flow_id)
        if flow is None:
            raise ValidationProblem("The flow of that run was deleted.")
        cfg = json.loads(run.settings_json or "{}")
        options = RunOptions.from_dict(cfg.get("options", {}))
        steps = parse_steps(json.loads(flow.steps_json or "[]"))
        compile_flow(steps)
        pending = [it for it in self.repos.runs.items(run_id) if it.status not in FINAL_STATUSES]
        accounts = {a.id: a for a in self.repos.accounts.get_many([it.account_id for it in pending if it.account_id])}
        groups: list[tuple[str, int | None, list[Account]]] = []
        items: list[RunItem] = []
        for it in pending:
            acc = accounts.get(it.account_id or -1)
            if acc is None:
                self.repos.runs.update_item(it.id, status=SKIPPED, error="Account was deleted.")
                continue
            it.status = QUEUED
            self.repos.runs.update_item(it.id, status=QUEUED)
            if not groups or groups[-1][0] != it.group_name:
                groups.append((it.group_name, None, []))
            groups[-1][2].append(acc)
            items.append(it)
        if not items:
            self._finish_run_record(run, "done")
            raise ValidationProblem("Nothing left to run in that batch.")
        run.status = "running"
        self.repos.runs.save_run(run)
        return self._launch(run, flow, steps, options, groups, items)

    def discard_interrupted(self, run_id: int) -> None:
        run = self.repos.runs.get(run_id)
        if run:
            for it in self.repos.runs.items(run_id):
                if it.status not in FINAL_STATUSES:
                    self.repos.runs.update_item(it.id, status=STOPPED, error="The app was closed during the run.")
            self._finish_run_record(run, "stopped")

    # ------------------------------------------------------------------ controls
    def _active_item(self, item_id: int) -> tuple[_ActiveRun, _ItemState] | None:
        for active in self.runs.values():
            for st in active.items:
                if st.item.id == item_id:
                    return active, st
        return None

    def pause_all(self) -> None:
        for active in self.runs.values():
            active.control.pause()
            active.run.status = "paused"
            self.repos.runs.save_run(active.run)
            self._emit("run_paused", active.run.id)

    def resume_all(self) -> None:
        for active in self.runs.values():
            active.control.resume()
            for st in active.items:  # also release per-account pauses and manual waits
                st.control.resume()
            active.run.status = "running"
            self.repos.runs.save_run(active.run)
            self._emit("run_resumed", active.run.id)

    def stop_all(self, force: bool = False) -> None:
        for active in self.runs.values():
            self.stop_run(active.run.id, force)

    def stop_run(self, run_id: int, force: bool = False) -> None:
        active = self.runs.get(run_id)
        if active is None:
            return
        active.control.stop(force)
        for st in active.items:
            st.control.stop(force)
            if force and st.task is not None and not st.task.done():
                st.task.cancel()
        self._emit("run_stopping", run_id, force=force)

    def pause_item(self, item_id: int) -> None:
        found = self._active_item(item_id)
        if found:
            found[1].control.pause()
            self._set_status(found[0], found[1], WAITING, "Paused")

    def resume_item(self, item_id: int) -> None:
        found = self._active_item(item_id)
        if found:
            found[1].control.resume()
            if found[1].item.status == WAITING:
                self._set_status(found[0], found[1], RUNNING, "")

    def stop_item(self, item_id: int, force: bool = False) -> None:
        found = self._active_item(item_id)
        if found:
            st = found[1]
            st.control.stop(force)
            if force and st.task is not None and not st.task.done():
                st.task.cancel()

    async def bring_to_front(self, item_id: int) -> None:
        found = self._active_item(item_id)
        if found and found[1].session is not None:
            try:
                await found[1].session.bring_to_front()
            except Exception as exc:
                logger.debug("bring_to_front failed: {}", exc)

    def is_busy(self) -> bool:
        return any(not (a.task is None or a.task.done()) for a in self.runs.values())

    # ------------------------------------------------------------------ batch
    async def _run_batch(self, active: _ActiveRun) -> None:
        opts = active.options
        sem = asyncio.Semaphore(max(1, int(opts.concurrency)))
        status = "done"
        try:
            for g_index, (group_name, states) in enumerate(active.groups):
                if active.control.stopping:
                    break
                if g_index > 0:
                    delay = self._rand(opts.group_delay_min, opts.group_delay_max)
                    if delay > 0:
                        self._emit("group_waiting", active.run.id, group=group_name, seconds=round(delay, 1))
                        await active.control.sleep(delay)
                self._emit("group_started", active.run.id, group=group_name, index=g_index)
                active.next_launch = 0.0
                for st in states:
                    st.task = asyncio.get_running_loop().create_task(self._run_item(active, st, sem))
                await asyncio.gather(*(st.task for st in states if st.task), return_exceptions=True)
        except StopRequested:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Batch crashed: {}", exc)
            status = "failed"
        finally:
            for st in active.items:  # anything never started
                if st.item.status not in FINAL_STATUSES:
                    self._set_status(active, st, STOPPED, "Stopped before it started.")
            if active.control.stopping and status == "done":
                status = "stopped"
            run = active.run
            run.succeeded = sum(1 for st in active.items if st.item.status == DONE)
            run.failed = sum(1 for st in active.items if st.item.status == FAILED)
            self._finish_run_record(run, status)
            self.runs.pop(run.id, None)
            self._emit("run_finished", run.id, status=status, succeeded=run.succeeded, failed=run.failed,
                       total=run.total, flow_name=active.flow.name,
                       duration=round(time.monotonic() - active.started, 1))

    def _finish_run_record(self, run: Run, status: str) -> None:
        run.status = status
        run.finished_at = utcnow()
        self.repos.runs.save_run(run)

    def _rand(self, lo: float, hi: float) -> float:
        lo, hi = sorted((max(0.0, lo), max(0.0, hi)))
        return self.rng.uniform(lo, hi)

    async def _launch_gate(self, active: _ActiveRun, st: _ItemState) -> None:
        """Wait for our turn to launch a browser (staggered launches + resource guard)."""
        loop = asyncio.get_running_loop()
        async with active.launch_lock:
            wait = active.next_launch - loop.time()
            if wait > 0:
                self._set_status(active, st, QUEUED, f"Starts in {wait:.0f}s")
                await st.control.sleep(wait)
            await self._resource_guard(active, st)
            active.next_launch = loop.time() + self._rand(active.options.launch_delay_min,
                                                          active.options.launch_delay_max)

    async def _resource_guard(self, active: _ActiveRun, st: _ItemState) -> None:
        s = self.settings
        if not s.resource_guard:
            return
        for attempt in range(12):  # wait up to ~1 minute for the PC to calm down
            usage = resource_usage()
            if usage is None:
                return
            cpu, ram = usage
            if cpu < s.resource_guard_cpu and ram < s.resource_guard_ram:
                return
            if attempt == 0:
                self._emit("warning", active.run.id, st.item.id,
                           message=f"Your PC is busy (CPU {cpu:.0f}%, RAM {ram:.0f}%). Waiting before opening more browsers.")
            await st.control.sleep(5)

    async def _run_item(self, active: _ActiveRun, st: _ItemState, sem: asyncio.Semaphore) -> None:
        retries = self.settings.retry_failed_accounts if active.options.retry_failed is None else active.options.retry_failed
        async with sem:
            try:
                for attempt in range(max(0, retries) + 1):
                    if st.control.stopping:
                        raise StopRequested()
                    await self._launch_gate(active, st)
                    retry = await self._run_once(active, st, attempt)
                    if not retry or attempt >= retries:
                        break
                    self._log(active, st, "warning", f"Retrying the whole account (attempt {attempt + 2}).")
            except (StopRequested, asyncio.CancelledError):
                if st.item.status not in FINAL_STATUSES:
                    self._set_status(active, st, STOPPED, "Stopped by you.")
            except Exception as exc:  # pragma: no cover - defensive, should be handled in _run_once
                logger.exception("Account run crashed: {}", exc)
                self._set_status(active, st, FAILED, friendly_error(exc))

    async def _run_once(self, active: _ActiveRun, st: _ItemState, attempt: int) -> bool:
        """Run the flow once on one account. Returns True if a retry makes sense."""
        s = self.settings
        acc = st.account
        item = st.item
        conn = self.repos.connections.get(acc.connection_id)
        item.started_at = utcnow()
        item.attempts = attempt + 1
        item.error = item.error_detail = ""
        started = time.monotonic()
        artifacts = self.paths.screenshots / f"run_{active.run.id}" / f"{item.position + 1:03d}_{_safe(acc.name)}"
        session: BrowserSession | None = None
        ctx: StepContext | None = None
        retryable = False
        final_status, message = FAILED, ""
        close_browser = active.options.close_browser_at_end
        if close_browser is None:
            close_browser = s.close_browser_at_end
        try:
            if conn is None:
                raise FlowError("This account's browser connection was deleted.")
            self._set_status(active, st, STARTING, "Opening browser…")
            adapter = self.adapters.get(conn)
            session = BrowserSession(adapter, acc.remote_profile_id, self.playwright,
                                     auto_switch_tabs=s.auto_switch_new_tab, dialog_action=s.dialog_action)
            st.session = session
            page = await session.open()
            humanizer = Humanizer(s.humanize, sleep=st.control.sleep, rng=random.Random(),
                                  slow_factor=1.5 if active.options.test_mode else 1.0)
            ctx = StepContext(
                settings=s, variables=Variables(account=self.repos.accounts.variables(acc) | {
                    "name": acc.name, "profile_id": acc.remote_profile_id, "group": item.group_name,
                    "position": str(item.position + 1)}),
                control=st.control, humanizer=humanizer, page=page, browser_context=session.context,
                browser=session.browser, test_mode=active.options.test_mode, account_name=acc.name,
                account_position=item.position, flow_name=active.flow.name, artifacts_dir=artifacts,
                exports_dir=self.paths.exports, shared=active.shared, shared_lock=active.shared_lock,
                log=lambda level, msg: self._log(active, st, level, msg),
                screenshot_cb=lambda path: self._emit("item_screenshot", active.run.id, item.id, path=path),
                notify_cb=lambda msg: self._emit("notify", active.run.id, item.id, message=msg),
            )
            run_ctx = ctx

            def on_new_page(p: Any) -> None:
                if s.auto_switch_new_tab:
                    run_ctx.page = p

            def on_disconnect() -> None:
                run_ctx.disconnected = True

            session.on_new_page = on_new_page
            session.on_disconnect = on_disconnect

            async def close_cb() -> None:
                await session.close(stop_profile=True)  # type: ignore[union-attr]

            ctx.close_browser_cb = close_cb
            if s.trace_on_error:
                try:
                    await session.context.tracing.start(screenshots=True, snapshots=True)
                except Exception as exc:
                    logger.debug("Tracing unavailable: {}", exc)
            self._set_status(active, st, RUNNING, "")
            runner = FlowRunner(active.steps, ctx, on_event=lambda e: self._on_step(active, st, e))
            outcome = await asyncio.wait_for(runner.run(), timeout=max(60.0, s.max_run_minutes * 60))
            if outcome.success:
                final_status, message = DONE, outcome.message
            else:
                final_status, message = FAILED, outcome.message or "The flow ended as failed."
                if outcome.close_browser:
                    close_browser = True
        except StopRequested:
            final_status, message = STOPPED, "Stopped by you."
        except asyncio.CancelledError:
            if st.kill_reason:
                final_status, message = FAILED, st.kill_reason
            else:
                final_status, message = STOPPED, "Force-stopped."
            raise
        except TimeoutError:
            final_status, message = FAILED, f"Took longer than the maximum of {s.max_run_minutes:g} minutes."
        except BrowserDisconnected as exc:
            final_status, message = FAILED, str(exc)
            item.error_detail = exc.detail
            retryable = True
        except (AdapterError, FlowError) as exc:
            final_status, message = FAILED, friendly_error(exc)
            item.error_detail = getattr(exc, "detail", "") or ""
            retryable = isinstance(exc, AdapterError)
        except Exception as exc:  # noqa: BLE001
            final_status, message = FAILED, friendly_error(exc)
            item.error_detail = repr(exc)[:4000]
            logger.exception("Unexpected error on {}: {}", acc.name, exc)
        finally:
            if final_status == FAILED and session is not None and not session.closed and not session.disconnected:
                await self._capture_failure(session, ctx, item, artifacts)
            elif s.trace_on_error and session is not None and not session.closed:
                try:
                    await session.context.tracing.stop()
                except Exception:
                    pass
            if session is not None:
                # Failed or stopped runs always stop the profile; successful ones follow the setting.
                must_close = bool(close_browser) or final_status in (FAILED, STOPPED)
                await asyncio.shield(session.close(stop_profile=must_close))
            st.session = None
            item.finished_at = utcnow()
            item.duration_s = round(time.monotonic() - started, 2)
            item.timeline_json = json.dumps(st.timeline[-500:])
            if ctx is not None:
                item.results_json = json.dumps({**ctx.results, **{"vars": ctx.variables.snapshot()}}, default=str)[:20000]
                if ctx.screenshots and not item.screenshot_path:
                    item.screenshot_path = ctx.screenshots[-1]
            item.error = message if final_status != DONE else ""
            self._set_status(active, st, final_status, message)
            if acc.id:
                self.repos.accounts.mark_result(acc.id, final_status)
        return retryable and final_status == FAILED and not st.control.stopping

    async def _capture_failure(self, session: BrowserSession, ctx: StepContext | None, item: RunItem,
                               folder: Path) -> None:
        s = self.settings
        page = (ctx.page if ctx is not None else None) or session.page
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        if page is not None and not page.is_closed():
            if s.screenshot_on_error:
                try:
                    path = folder / f"error_{stamp}.png"
                    await asyncio.wait_for(page.screenshot(path=str(path)), timeout=15)
                    item.screenshot_path = str(path)
                    self._emit("item_screenshot", item.run_id, item.id, path=str(path))
                except Exception as exc:
                    logger.debug("Error screenshot failed: {}", exc)
            if s.html_on_error:
                try:
                    path = folder / f"error_{stamp}.html"
                    path.write_text(await asyncio.wait_for(page.content(), timeout=15), "utf-8")
                    item.html_path = str(path)
                except Exception as exc:
                    logger.debug("HTML snapshot failed: {}", exc)
        if s.trace_on_error:
            try:
                path = self.paths.traces / f"run{item.run_id}_item{item.id}_{stamp}.zip"
                await asyncio.wait_for(session.context.tracing.stop(path=str(path)), timeout=30)
                item.trace_path = str(path)
            except Exception as exc:
                logger.debug("Trace save failed: {}", exc)

    # ------------------------------------------------------------------ item state
    def _on_step(self, active: _ActiveRun, st: _ItemState, event: StepEvent) -> None:
        if event.status in ("ok", "failed", "skipped"):
            st.timeline.append(event.to_timeline())
        if event.status in ("running", "retry"):
            title = event.step.summary()
            st.item.current_step = f"{event.index + 1}. {title}"[:250]
            self._emit("item_step", active.run.id, st.item.id, index=event.index, total=len(active.steps),
                       title=title, attempt=event.detail)
        elif event.status == "failed":
            self._log(active, st, "error", f"Step {event.index + 1} failed: {event.error}")

    def _set_status(self, active: _ActiveRun, st: _ItemState, status: str, message: str = "") -> None:
        st.item.status = status
        item = st.item
        self.repos.runs.update_item(
            item.id, status=status, current_step=item.current_step, error=item.error, error_detail=item.error_detail,
            screenshot_path=item.screenshot_path, html_path=item.html_path, trace_path=item.trace_path,
            timeline_json=item.timeline_json, results_json=item.results_json, attempts=item.attempts,
            started_at=item.started_at, finished_at=item.finished_at, duration_s=item.duration_s)
        self._emit("item_status", active.run.id, item.id, status=status, message=message, account=st.account.name,
                   group=item.group_name, manual=st.control.manual_message)

    def _log(self, active: _ActiveRun, st: _ItemState, level: str, message: str) -> None:
        logger.log(level.upper() if level.upper() in ("INFO", "WARNING", "ERROR", "DEBUG") else "INFO",
                   "[run {} | {}] {}", active.run.id, st.account.name, message)
        self._emit("item_log", active.run.id, st.item.id, level=level, message=message,
                   time=datetime.now().strftime("%H:%M:%S"))
        if st.control.manual_message:
            self._emit("item_status", active.run.id, st.item.id, status=WAITING, message=st.control.manual_message,
                       account=st.account.name, group=st.item.group_name, manual=st.control.manual_message)

    # ------------------------------------------------------------------ watchdog
    def _ensure_watchdog(self) -> None:
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.get_running_loop().create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        """Cancel accounts whose run shows no activity for a long time (a hung browser)."""
        while self.runs:
            await asyncio.sleep(self.WATCHDOG_INTERVAL_S)
            limit = max(180.0, self.settings.step_timeout_s * 6)
            now = time.monotonic()
            for active in list(self.runs.values()):
                for st in active.items:
                    if st.item.status != RUNNING or st.task is None or st.task.done():
                        continue
                    if st.control.paused or st.control.waiting_manual:
                        continue
                    if now - st.control.last_activity > limit:
                        logger.warning("Watchdog: {} looks stuck, stopping it", st.account.name)
                        self._log(active, st, "error", f"No progress for {limit:.0f} seconds; stopping this account.")
                        st.kill_reason = "The browser stopped responding (no progress for a long time)."
                        st.task.cancel()

    async def shutdown(self) -> None:
        """Stop everything (app exit). Browsers are closed; unfinished accounts are put back in
        the queue and the run is left 'running' so it can be resumed on the next start."""
        unfinished: dict[int, list[int]] = {}
        for active in list(self.runs.values()):
            unfinished[active.run.id] = [st.item.id for st in active.items if st.item.status not in FINAL_STATUSES]
            active.control.stop(force=True)
            for st in active.items:
                if st.task and not st.task.done():
                    st.task.cancel()
        tasks = [a.task for a in self.runs.values() if a.task]
        if tasks:
            await asyncio.wait(tasks, timeout=20)
        for run_id, item_ids in unfinished.items():
            if not item_ids:
                continue
            for item_id in item_ids:
                self.repos.runs.update_item(item_id, status=QUEUED, error="")
            run = self.repos.runs.get(run_id)
            if run is not None:
                run.status = "running"
                run.finished_at = None
                self.repos.runs.save_run(run)
        if self._watchdog:
            self._watchdog.cancel()
        await self.playwright.stop()


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "account"
