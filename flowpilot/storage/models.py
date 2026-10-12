"""SQLAlchemy ORM models (SQLite)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Naive local timestamp (SQLite has no time zones; the app is single-user, local)."""
    return datetime.now().replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class Meta(Base):
    """Key/value table; holds the schema version."""

    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Connection(Base):
    """An anti-detect browser local API connection."""

    __tablename__ = "connections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512), default="")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Group(Base):
    """A group of accounts; groups run one after another."""

    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    color: Mapped[str] = mapped_column(String(16), default="#7C6CF2")
    order: Mapped[int] = mapped_column(Integer, default=0)


class Account(Base):
    """A browser profile in an anti-detect browser, plus its variables."""

    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"), nullable=True)
    remote_profile_id: Mapped[str] = mapped_column(String(256), default="")
    name: Mapped[str] = mapped_column(String(256))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    tags: Mapped[str] = mapped_column(String(512), default="")
    variables_encrypted_json: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    last_run_status: Mapped[str] = mapped_column(String(32), default="")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]


class Flow(Base):
    """A saved automation (list of steps as JSON)."""

    __tablename__ = "flows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    steps_json: Mapped[str] = mapped_column(Text, default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FlowVersion(Base):
    """Snapshot of a flow at a save; the last N are kept."""

    __tablename__ = "flow_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("flows.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    steps_json: Mapped[str] = mapped_column(Text)
    saved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Run(Base):
    """One batch execution of a flow on many accounts."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_id: Mapped[int | None] = mapped_column(ForeignKey("flows.id", ondelete="SET NULL"), nullable=True)
    flow_name: Mapped[str] = mapped_column(String(256), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="running")
    total: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="manual")


class RunItem(Base):
    """The run of a flow on one account."""

    __tablename__ = "run_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    account_name: Mapped[str] = mapped_column(String(256), default="")
    group_name: Mapped[str] = mapped_column(String(128), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    current_step: Mapped[str] = mapped_column(String(256), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    error_detail: Mapped[str] = mapped_column(Text, default="")
    screenshot_path: Mapped[str] = mapped_column(Text, default="")
    html_path: Mapped[str] = mapped_column(Text, default="")
    trace_path: Mapped[str] = mapped_column(Text, default="")
    timeline_json: Mapped[str] = mapped_column(Text, default="[]")
    results_json: Mapped[str] = mapped_column(Text, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)


class Schedule(Base):
    """A scheduled run of a flow."""

    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    flow_id: Mapped[int] = mapped_column(ForeignKey("flows.id", ondelete="CASCADE"))
    target_json: Mapped[str] = mapped_column(Text, default="{}")
    cron_or_interval: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[str] = mapped_column(String(256), default="")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SelectorHeal(Base):
    """A selector that was self-healed during a run, waiting for the user to accept it."""

    __tablename__ = "selector_heals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_id: Mapped[int | None] = mapped_column(ForeignKey("flows.id", ondelete="CASCADE"), nullable=True)
    step_id: Mapped[str] = mapped_column(String(32))
    step_summary: Mapped[str] = mapped_column(String(300), default="")
    old_selector: Mapped[str] = mapped_column(Text, default="")
    new_selector: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")  # new fallbacks + fingerprint
    score: Mapped[float] = mapped_column(Float, default=0.0)
    account_name: Mapped[str] = mapped_column(String(256), default="")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    times: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / applied / dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
