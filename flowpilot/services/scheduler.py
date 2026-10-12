"""Scheduled runs (APScheduler on the asyncio loop).

A schedule's ``cron_or_interval`` column holds a JSON spec::

    {"kind": "once", "at": "2026-10-12T09:00"}
    {"kind": "daily", "at": "09:00"}
    {"kind": "weekdays", "at": "09:00"}
    {"kind": "interval", "hours": 4}
    + "jitter_minutes": 15   (random start offset, 0..N minutes)
    + "options": {...}       (RunOptions for the batch)

``target_json`` holds a :class:`TargetSpec`.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from flowpilot.core.engine import EngineEvent, RunEngine, RunOptions, TargetSpec
from flowpilot.core.errors import FlowError
from flowpilot.storage.models import Schedule
from flowpilot.storage.repositories import Repositories

KINDS = {"once": "Once", "daily": "Every day", "weekdays": "Weekdays (Mon–Fri)", "interval": "Every N hours"}


def parse_spec(schedule: Schedule) -> dict[str, Any]:
    try:
        return json.loads(schedule.cron_or_interval or "{}")
    except ValueError:
        return {}


def describe_spec(spec: dict[str, Any]) -> str:
    kind = spec.get("kind", "")
    jitter = int(spec.get("jitter_minutes") or 0)
    tail = f" (+0–{jitter} min random)" if jitter else ""
    if kind == "once":
        return f"Once at {str(spec.get('at', '')).replace('T', ' ')}{tail}"
    if kind == "daily":
        return f"Every day at {spec.get('at', '')}{tail}"
    if kind == "weekdays":
        return f"Mon–Fri at {spec.get('at', '')}{tail}"
    if kind == "interval":
        return f"Every {spec.get('hours', 1)} hour(s){tail}"
    return "Not set"


def build_trigger(spec: dict[str, Any], rng: random.Random | None = None) -> Any:
    """APScheduler trigger for a spec; raises ValueError for bad input."""
    rng = rng or random.Random()
    kind = spec.get("kind")
    jitter_s = int(spec.get("jitter_minutes") or 0) * 60
    if kind == "once":
        when = datetime.fromisoformat(str(spec["at"]))
        if jitter_s:
            when += timedelta(seconds=rng.randint(0, jitter_s))
        return DateTrigger(run_date=when)
    if kind in ("daily", "weekdays"):
        hour, minute = (int(x) for x in str(spec.get("at", "09:00")).split(":")[:2])
        return CronTrigger(hour=hour, minute=minute, day_of_week="mon-fri" if kind == "weekdays" else "*",
                           jitter=jitter_s or None)
    if kind == "interval":
        hours = float(spec.get("hours", 1))
        if hours <= 0:
            raise ValueError("The interval must be more than 0 hours.")
        return IntervalTrigger(hours=hours, jitter=jitter_s or None)
    raise ValueError(f"Unknown schedule type '{kind}'")


class SchedulerService:
    def __init__(self, repos: Repositories, engine: RunEngine) -> None:
        self.repos = repos
        self.engine = engine
        self.scheduler: AsyncIOScheduler | None = None
        self._run_to_schedule: dict[int, int] = {}
        engine.subscribe(self._on_event)

    def start(self) -> None:
        if self.scheduler is None:
            self.scheduler = AsyncIOScheduler()
            self.scheduler.start()
        self.reload()

    def shutdown(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

    def reload(self) -> None:
        """Re-create all jobs from the database."""
        if self.scheduler is None:
            return
        self.scheduler.remove_all_jobs()
        for sched in self.repos.schedules.list():
            if not sched.enabled:
                self._store_next(sched, None)
                continue
            try:
                trigger = build_trigger(parse_spec(sched))
            except (ValueError, KeyError) as exc:
                logger.warning("Schedule {} invalid: {}", sched.id, exc)
                self._store_next(sched, None)
                continue
            job = self.scheduler.add_job(self._fire, trigger, args=[sched.id], id=f"sched-{sched.id}",
                                         misfire_grace_time=3600, coalesce=True, max_instances=1)
            self._store_next(sched, getattr(job, "next_run_time", None))

    def _store_next(self, sched: Schedule, when: datetime | None) -> None:
        sched.next_run = when.replace(tzinfo=None) if when else None
        self.repos.schedules.save(sched)

    def next_run(self, schedule_id: int) -> datetime | None:
        if self.scheduler is None:
            return None
        job = self.scheduler.get_job(f"sched-{schedule_id}")
        return job.next_run_time.replace(tzinfo=None) if job and job.next_run_time else None

    async def run_now(self, schedule_id: int) -> int | None:
        return await self._fire(schedule_id)

    async def _fire(self, schedule_id: int) -> int | None:
        sched = self.repos.schedules.get(schedule_id)
        if sched is None:
            return None
        flow = self.repos.flows.get(sched.flow_id)
        sched.last_run_at = datetime.now().replace(microsecond=0)
        if flow is None:
            sched.last_result = "Flow was deleted"
            self.repos.schedules.save(sched)
            return None
        spec = parse_spec(sched)
        try:
            target = TargetSpec.from_dict(json.loads(sched.target_json or "{}"))
            run_id = await self.engine.start(flow, target, RunOptions.from_dict(spec.get("options") or {}),
                                             source="schedule")
        except (FlowError, ValueError) as exc:
            sched.last_result = f"Could not start: {exc}"
            self.repos.schedules.save(sched)
            logger.warning("Scheduled run {} could not start: {}", schedule_id, exc)
            return None
        self._run_to_schedule[run_id] = schedule_id
        sched.last_result = "Running…"
        sched.next_run = self.next_run(schedule_id)
        self.repos.schedules.save(sched)
        return run_id

    def _on_event(self, event: EngineEvent) -> None:
        if event.kind != "run_finished" or event.run_id not in self._run_to_schedule:
            return
        sched = self.repos.schedules.get(self._run_to_schedule.pop(event.run_id))
        if sched is None:
            return
        d = event.data
        sched.last_result = f"{d.get('status', 'done')}: {d.get('succeeded', 0)}/{d.get('total', 0)} ok"
        self.repos.schedules.save(sched)
