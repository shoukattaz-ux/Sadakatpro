"""Database engine, sessions and simple versioned migrations.

Migrations are plain Python functions applied in order; the current version is stored in
the ``meta`` table. To change the schema, append a function to :data:`MIGRATIONS` — never
edit an existing one.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from loguru import logger
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from flowpilot.storage.models import Base, Meta


def _migration_1(conn_engine: Engine) -> None:
    """Initial schema."""
    Base.metadata.create_all(conn_engine)


def _migration_2(conn_engine: Engine) -> None:
    """Self-healing selector suggestions (``selector_heals``)."""
    from flowpilot.storage.models import SelectorHeal

    SelectorHeal.__table__.create(conn_engine, checkfirst=True)


MIGRATIONS: list[Callable[[Engine], None]] = [_migration_1, _migration_2]


class Database:
    """Owns the SQLAlchemy engine and session factory."""

    def __init__(self, path: Path | str) -> None:
        url = "sqlite://" if str(path) == ":memory:" else f"sqlite:///{Path(path)}"
        self.engine = create_engine(url, future=True)
        event.listen(self.engine, "connect", self._on_connect)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        self.migrate()

    @staticmethod
    def _on_connect(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    def session(self) -> Session:
        return self.session_factory()

    def schema_version(self) -> int:
        with self.engine.connect() as conn:
            exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'")).first()
        if not exists:
            return 0
        with self.session() as s:
            row = s.scalar(select(Meta).where(Meta.key == "schema_version"))
            return int(row.value) if row else 0

    def migrate(self) -> None:
        current = self.schema_version()
        for number, migration in enumerate(MIGRATIONS, start=1):
            if number <= current:
                continue
            logger.info("Applying database migration {}", number)
            migration(self.engine)
            with self.session() as s:
                s.merge(Meta(key="schema_version", value=str(number)))
                s.commit()

    def dispose(self) -> None:
        self.engine.dispose()
