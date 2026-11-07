"""SQLite via SQLAlchemy 2, with Alembic migrations (S-8; PRD section 7).

The ``generations`` table is generic: it has no song-specific columns. JSON columns
hold the request, stages, error, timings and the full artifact manifest. Access is
synchronous: the queries are tiny and SQLite runs in-process (docs/decisions.md).
"""

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, Index, String, Text, create_engine, event, select
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import ConnectionPoolEntry
from sqlalchemy.types import TypeDecorator

from studio_contracts import Artifact, GenerationStatus, ServerErrorInfo

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """Aware UTC datetimes stored as fixed-width ISO 8601 text, so they sort as text."""

    impl = String(27)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC).strftime(_TS_FORMAT)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return datetime.strptime(str(value), _TS_FORMAT).replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class GenerationRow(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())
    title: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(128))
    request_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str | None] = mapped_column(String(64))
    stages_json: Mapped[str] = mapped_column(Text)
    error_json: Mapped[str | None] = mapped_column(Text)
    worker_job_id: Mapped[str | None] = mapped_column(String(64))
    client_request_id: Mapped[str] = mapped_column(String(128), unique=True)
    timings_json: Mapped[str] = mapped_column(Text)
    artifacts_json: Mapped[str] = mapped_column(Text)
    data_dir: Mapped[str] = mapped_column(Text)

    __table_args__ = (Index("ix_generations_status", "status"),)


class SettingRow(Base):
    """Values saved through Settings. Key-value, so later settings need no migration."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class StoredArtifact(Artifact):
    """A worker manifest entry, plus whether the file is stored and verified locally."""

    stored: bool = False


@dataclass(frozen=True)
class GenerationRecord:
    id: str
    created_at: datetime
    updated_at: datetime
    title: str
    operation: str
    request: dict[str, Any]
    status: GenerationStatus
    stage: str | None
    stages: list[str]
    error: ServerErrorInfo | None
    worker_job_id: str | None
    client_request_id: str
    timings: dict[str, float]
    artifacts: list[StoredArtifact]
    data_dir: str
    """Relative to the server's data dir, e.g. ``generations/<id>``."""


_FIELDS = {f.name for f in fields(GenerationRecord)}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _to_row(rec: GenerationRecord) -> dict[str, Any]:
    return {
        "id": rec.id,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "title": rec.title,
        "operation": rec.operation,
        "request_json": _dumps(rec.request),
        "status": rec.status.value,
        "stage": rec.stage,
        "stages_json": _dumps(rec.stages),
        "error_json": None if rec.error is None else rec.error.model_dump_json(),
        "worker_job_id": rec.worker_job_id,
        "client_request_id": rec.client_request_id,
        "timings_json": _dumps(rec.timings),
        "artifacts_json": _dumps([a.model_dump(mode="json") for a in rec.artifacts]),
        "data_dir": rec.data_dir,
    }


def _from_row(row: GenerationRow) -> GenerationRecord:
    return GenerationRecord(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        title=row.title,
        operation=row.operation,
        request=json.loads(row.request_json),
        status=GenerationStatus(row.status),
        stage=row.stage,
        stages=json.loads(row.stages_json),
        error=None
        if row.error_json is None
        else ServerErrorInfo.model_validate_json(row.error_json),
        worker_job_id=row.worker_job_id,
        client_request_id=row.client_request_id,
        timings=json.loads(row.timings_json),
        artifacts=[StoredArtifact.model_validate(a) for a in json.loads(row.artifacts_json)],
        data_dir=row.data_dir,
    )


def _sqlite_pragmas(dbapi_conn: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


class Database:
    """Generation and settings storage. Thread-safe enough for one process: SQLite
    serialises writes, and each call uses its own short session."""

    def __init__(self, path: Path, *, now: Callable[[], datetime] = utcnow) -> None:
        self.path = path
        self.now = now
        # No disk access here: the OpenAPI exporter builds the app without a data dir.
        self.engine: Engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False}
        )
        event.listen(self.engine, "connect", _sqlite_pragmas)
        self._lock = threading.Lock()

    # --- lifecycle --------------------------------------------------------------------

    def migrate(self) -> None:
        """``alembic upgrade head`` on this database, creating the file if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        with self.engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        if os.name == "posix":
            self.path.chmod(0o600)  # the worker token is stored here

    def close(self) -> None:
        self.engine.dispose()

    # --- generations ------------------------------------------------------------------

    def insert(self, rec: GenerationRecord) -> None:
        with Session(self.engine) as s, s.begin():
            s.add(GenerationRow(**_to_row(rec)))

    def get(self, gen_id: str) -> GenerationRecord | None:
        with Session(self.engine) as s:
            row = s.get(GenerationRow, gen_id)
            return None if row is None else _from_row(row)

    def update(self, gen_id: str, **changes: Any) -> GenerationRecord:
        """Change record fields (by ``GenerationRecord`` name) and bump ``updated_at``.
        Raises ``KeyError`` if the generation is gone."""
        unknown = set(changes) - _FIELDS
        if unknown:
            raise TypeError(f"unknown fields {sorted(unknown)}")
        with self._lock, Session(self.engine) as s, s.begin():
            row = s.get(GenerationRow, gen_id)
            if row is None:
                raise KeyError(gen_id)
            rec = replace(_from_row(row), **changes, updated_at=self.now())
            for key, value in _to_row(rec).items():
                setattr(row, key, value)
            return rec

    def delete(self, gen_id: str) -> bool:
        with Session(self.engine) as s, s.begin():
            row = s.get(GenerationRow, gen_id)
            if row is None:
                return False
            s.delete(row)
            return True

    def page(self, *, limit: int, before: str | None) -> list[GenerationRecord]:
        """Newest first. Ids are monotonic ULIDs, so id order is creation order."""
        query = select(GenerationRow).order_by(GenerationRow.id.desc()).limit(limit)
        if before is not None:
            query = query.where(GenerationRow.id < before)
        with Session(self.engine) as s:
            return [_from_row(row) for row in s.scalars(query)]

    def active(self) -> list[GenerationRecord]:
        """Non-terminal generations, oldest first: what the poller advances."""
        terminal = [s.value for s in GenerationStatus if s.is_terminal]
        query = (
            select(GenerationRow)
            .where(GenerationRow.status.not_in(terminal))
            .order_by(GenerationRow.id)
        )
        with Session(self.engine) as s:
            return [_from_row(row) for row in s.scalars(query)]

    # --- settings ---------------------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        with Session(self.engine) as s:
            row = s.get(SettingRow, key)
            return None if row is None else row.value

    def set_setting(self, key: str, value: str | None) -> None:
        """Save a value; ``None`` deletes it."""
        with Session(self.engine) as s, s.begin():
            row = s.get(SettingRow, key)
            if value is None:
                if row is not None:
                    s.delete(row)
            elif row is None:
                s.add(SettingRow(key=key, value=value))
            else:
                row.value = value
