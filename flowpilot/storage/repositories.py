"""Repositories: the only place that talks SQL. UI and engine use these classes.

Objects are returned detached (``expire_on_commit=False``) so they can be read freely
outside a session; changes must be saved back through a repository method.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from flowpilot.security import SecretBox
from flowpilot.storage.db import Database
from flowpilot.storage.models import (
    Account,
    Base,
    Connection,
    Flow,
    FlowVersion,
    Group,
    Run,
    RunItem,
    Schedule,
    utcnow,
)

T = TypeVar("T", bound=Base)
MAX_FLOW_VERSIONS = 10


class _Repo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _session(self) -> Session:
        return self.db.session()

    def _save(self, obj: T) -> T:
        with self._session() as s:
            obj = s.merge(obj)
            s.commit()
            s.refresh(obj)
            s.expunge(obj)
            return obj

    def _get(self, model: type[T], obj_id: int | None) -> T | None:
        if obj_id is None:
            return None
        with self._session() as s:
            return s.get(model, obj_id)

    def _delete(self, model: type[T], ids: list[int]) -> None:
        if not ids:
            return
        with self._session() as s:
            s.execute(delete(model).where(model.id.in_(ids)))  # type: ignore[attr-defined]
            s.commit()


class ConnectionRepo(_Repo):
    """Anti-detect browser connections; API keys are stored encrypted."""

    def __init__(self, db: Database, secrets: SecretBox) -> None:
        super().__init__(db)
        self.secrets = secrets

    def list(self) -> list[Connection]:
        with self._session() as s:
            return list(s.scalars(select(Connection).order_by(Connection.name)))

    def get(self, conn_id: int | None) -> Connection | None:
        return self._get(Connection, conn_id)

    def save(self, conn: Connection, api_key: str | None = None) -> Connection:
        if api_key is not None:
            conn.api_key_encrypted = self.secrets.encrypt(api_key)
        return self._save(conn)

    def api_key(self, conn: Connection) -> str:
        return self.secrets.decrypt(conn.api_key_encrypted)

    def extra(self, conn: Connection) -> dict[str, Any]:
        try:
            return json.loads(conn.extra_json or "{}")
        except ValueError:
            return {}

    def delete(self, conn_id: int) -> None:
        self._delete(Connection, [conn_id])


class GroupRepo(_Repo):
    def list(self) -> list[Group]:
        with self._session() as s:
            return list(s.scalars(select(Group).order_by(Group.order, Group.id)))

    def get(self, group_id: int | None) -> Group | None:
        return self._get(Group, group_id)

    def create(self, name: str, color: str = "#7C6CF2") -> Group:
        with self._session() as s:
            max_order = s.scalar(select(func.max(Group.order))) or 0
        return self._save(Group(name=name, color=color, order=max_order + 1))

    def save(self, group: Group) -> Group:
        return self._save(group)

    def reorder(self, ordered_ids: list[int]) -> None:
        with self._session() as s:
            for pos, gid in enumerate(ordered_ids):
                g = s.get(Group, gid)
                if g:
                    g.order = pos
            s.commit()

    def delete(self, group_id: int) -> None:
        self._delete(Group, [group_id])

    def get_or_create(self, name: str) -> Group:
        with self._session() as s:
            g = s.scalar(select(Group).where(Group.name == name))
        return g or self.create(name)


class AccountRepo(_Repo):
    """Accounts (browser profiles); variables are stored as one encrypted JSON blob."""

    def __init__(self, db: Database, secrets: SecretBox) -> None:
        super().__init__(db)
        self.secrets = secrets

    def list(self, group_id: int | None = None, only_enabled: bool = False) -> list[Account]:
        stmt = select(Account).order_by(Account.sort_order, Account.id)
        if group_id is not None:
            stmt = stmt.where(Account.group_id == group_id)
        if only_enabled:
            stmt = stmt.where(Account.enabled.is_(True))
        with self._session() as s:
            return list(s.scalars(stmt))

    def get(self, account_id: int | None) -> Account | None:
        return self._get(Account, account_id)

    def get_many(self, ids: list[int]) -> list[Account]:
        if not ids:
            return []
        with self._session() as s:
            found = {a.id: a for a in s.scalars(select(Account).where(Account.id.in_(ids)))}
        return [found[i] for i in ids if i in found]

    def find_by_remote(self, connection_id: int, remote_id: str) -> Account | None:
        with self._session() as s:
            return s.scalar(
                select(Account).where(Account.connection_id == connection_id, Account.remote_profile_id == remote_id)
            )

    def save(self, account: Account, variables: dict[str, str] | None = None) -> Account:
        if variables is not None:
            account.variables_encrypted_json = self.secrets.encrypt(json.dumps(variables)) if variables else ""
        return self._save(account)

    def variables(self, account: Account) -> dict[str, str]:
        if not account.variables_encrypted_json:
            return {}
        try:
            data = json.loads(self.secrets.decrypt(account.variables_encrypted_json))
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def set_enabled(self, ids: list[int], enabled: bool) -> None:
        self._bulk_update(ids, enabled=enabled)

    def move_to_group(self, ids: list[int], group_id: int | None) -> None:
        self._bulk_update(ids, group_id=group_id)

    def mark_result(self, account_id: int, status: str) -> None:
        self._bulk_update([account_id], last_run_status=status, last_run_at=utcnow())

    def _bulk_update(self, ids: list[int], **values: Any) -> None:
        with self._session() as s:
            for acc in s.scalars(select(Account).where(Account.id.in_(ids))):
                for key, value in values.items():
                    setattr(acc, key, value)
            s.commit()

    def delete(self, ids: list[int]) -> None:
        self._delete(Account, ids)


class FlowRepo(_Repo):
    """Flows with a rolling version history."""

    def list(self) -> list[Flow]:
        with self._session() as s:
            return list(s.scalars(select(Flow).order_by(Flow.name)))

    def get(self, flow_id: int | None) -> Flow | None:
        return self._get(Flow, flow_id)

    def create(self, name: str, steps_json: str = "[]", description: str = "") -> Flow:
        return self._save(Flow(name=name, steps_json=steps_json, description=description))

    def save(self, flow: Flow, snapshot: bool = True) -> Flow:
        """Save the flow; if the steps changed, store a version snapshot (keeps the last 10)."""
        with self._session() as s:
            previous = s.get(Flow, flow.id) if flow.id else None
            changed = previous is None or previous.steps_json != flow.steps_json
            if previous is not None and changed and snapshot:
                s.add(FlowVersion(flow_id=previous.id, version=previous.version, steps_json=previous.steps_json))
                flow.version = previous.version + 1
            flow.updated_at = utcnow()
            merged = s.merge(flow)
            s.commit()
            s.refresh(merged)
            s.expunge(merged)
            if previous is not None:
                old_ids = list(
                    s.scalars(
                        select(FlowVersion.id)
                        .where(FlowVersion.flow_id == merged.id)
                        .order_by(FlowVersion.version.desc())
                        .offset(MAX_FLOW_VERSIONS)
                    )
                )
                if old_ids:
                    s.execute(delete(FlowVersion).where(FlowVersion.id.in_(old_ids)))
                    s.commit()
            return merged

    def versions(self, flow_id: int) -> list[FlowVersion]:
        with self._session() as s:
            return list(
                s.scalars(select(FlowVersion).where(FlowVersion.flow_id == flow_id).order_by(FlowVersion.version.desc()))
            )

    def delete(self, flow_id: int) -> None:
        self._delete(Flow, [flow_id])


class RunRepo(_Repo):
    """Runs and their per-account items."""

    def create_run(self, run: Run, items: list[RunItem]) -> tuple[Run, list[RunItem]]:
        with self._session() as s:
            s.add(run)
            s.flush()
            for item in items:
                item.run_id = run.id
                s.add(item)
            s.commit()
            for obj in [run, *items]:
                s.refresh(obj)
                s.expunge(obj)
        return run, items

    def save_run(self, run: Run) -> Run:
        return self._save(run)

    def save_item(self, item: RunItem) -> RunItem:
        return self._save(item)

    def update_item(self, item_id: int, **values: Any) -> None:
        with self._session() as s:
            item = s.get(RunItem, item_id)
            if item is None:
                return
            for key, value in values.items():
                setattr(item, key, value)
            s.commit()

    def get(self, run_id: int) -> Run | None:
        return self._get(Run, run_id)

    def list(self, limit: int = 500) -> list[Run]:
        with self._session() as s:
            return list(s.scalars(select(Run).order_by(Run.id.desc()).limit(limit)))

    def items(self, run_id: int) -> list[RunItem]:
        with self._session() as s:
            return list(s.scalars(select(RunItem).where(RunItem.run_id == run_id).order_by(RunItem.position)))

    def interrupted(self) -> list[Run]:
        """Runs that were still running when the app last closed."""
        with self._session() as s:
            return list(s.scalars(select(Run).where(Run.status.in_(["running", "paused"]))))

    def succeeded_today(self, account_ids: list[int], flow_id: int | None) -> set[int]:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = (
            select(RunItem.account_id)
            .join(Run, Run.id == RunItem.run_id)
            .where(RunItem.status == "done", RunItem.finished_at >= start, RunItem.account_id.in_(account_ids))
        )
        if flow_id is not None:
            stmt = stmt.where(Run.flow_id == flow_id)
        with self._session() as s:
            return {i for i in s.scalars(stmt) if i is not None}

    def delete_older_than(self, days: int) -> list[RunItem]:
        """Delete runs older than ``days``; returns deleted items so files can be removed."""
        cutoff = utcnow() - timedelta(days=days)
        with self._session() as s:
            run_ids = list(s.scalars(select(Run.id).where(Run.started_at < cutoff)))
            if not run_ids:
                return []
            items = list(s.scalars(select(RunItem).where(RunItem.run_id.in_(run_ids))))
            for it in items:
                s.expunge(it)
            s.execute(delete(RunItem).where(RunItem.run_id.in_(run_ids)))
            s.execute(delete(Run).where(Run.id.in_(run_ids)))
            s.commit()
            return items

    def delete(self, run_ids: list[int]) -> None:
        with self._session() as s:
            s.execute(delete(RunItem).where(RunItem.run_id.in_(run_ids)))
            s.execute(delete(Run).where(Run.id.in_(run_ids)))
            s.commit()


class ScheduleRepo(_Repo):
    def list(self) -> list[Schedule]:
        with self._session() as s:
            return list(s.scalars(select(Schedule).order_by(Schedule.id)))

    def get(self, schedule_id: int) -> Schedule | None:
        return self._get(Schedule, schedule_id)

    def save(self, schedule: Schedule) -> Schedule:
        return self._save(schedule)

    def delete(self, schedule_id: int) -> None:
        self._delete(Schedule, [schedule_id])


class Repositories:
    """Convenience bundle of all repositories."""

    def __init__(self, db: Database, secrets: SecretBox) -> None:
        self.db = db
        self.secrets = secrets
        self.connections = ConnectionRepo(db, secrets)
        self.groups = GroupRepo(db)
        self.accounts = AccountRepo(db, secrets)
        self.flows = FlowRepo(db)
        self.runs = RunRepo(db)
        self.schedules = ScheduleRepo(db)
