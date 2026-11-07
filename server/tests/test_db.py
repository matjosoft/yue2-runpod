"""SQLite with Alembic migrations (S-8) and the generation repository."""

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from studio_contracts import ArtifactRole, ErrorCode, GenerationStatus
from studio_server.core.db import Base, Database, GenerationRecord, StoredArtifact
from studio_server.core.errors import error_info
from studio_server.core.ids import new_generation_id

SERVER_DIR = Path(__file__).resolve().parent.parent

GENERATION_COLUMNS = {
    "id",
    "created_at",
    "updated_at",
    "title",
    "operation",
    "request_json",
    "status",
    "stage",
    "stages_json",
    "error_json",
    "worker_job_id",
    "client_request_id",
    "timings_json",
    "artifacts_json",
    "data_dir",
}


def record(**overrides: object) -> GenerationRecord:
    gen_id = new_generation_id()
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": gen_id,
        "created_at": now,
        "updated_at": now,
        "title": "Untitled",
        "operation": "song.generate",
        "request": {"style": "s", "lyrics": "l", "seed": 1},
        "status": GenerationStatus.queued,
        "stage": None,
        "stages": ["plan", "save"],
        "error": None,
        "worker_job_id": None,
        "client_request_id": f"crid-{gen_id}",
        "timings": {},
        "artifacts": [],
        "data_dir": f"generations/{gen_id}",
    }
    values.update(overrides)
    return GenerationRecord(**values)  # type: ignore[arg-type]


def test_alembic_upgrade_head_creates_schema_on_empty_data_dir(tmp_path: Path) -> None:
    """S-8: ``alembic upgrade head`` on an empty data dir creates the schema."""
    data_dir = tmp_path / "fresh" / "data"
    env = {**os.environ, "STUDIO_DATA_DIR": str(data_dir)}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(SERVER_DIR / "alembic.ini"), "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(data_dir / "studio.db") as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master")}
        columns = {row[1] for row in conn.execute("pragma table_info(generations)")}
    assert {"generations", "settings", "alembic_version"} <= tables
    assert columns == GENERATION_COLUMNS  # PRD section 7: generic columns only


def test_migrations_match_the_models(tmp_path: Path) -> None:
    db = Database(tmp_path / "studio.db")
    db.migrate()
    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
    db.close()


def test_migrate_is_idempotent_and_uses_wal(tmp_path: Path) -> None:
    db = Database(tmp_path / "studio.db")
    db.migrate()
    db.migrate()
    with sqlite3.connect(tmp_path / "studio.db") as conn:
        assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"
    db.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_database_file_is_private(tmp_path: Path) -> None:
    db = Database(tmp_path / "studio.db")
    db.migrate()
    assert (tmp_path / "studio.db").stat().st_mode & 0o077 == 0
    db.close()


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "studio.db")
    database.migrate()
    return database


def test_insert_get_round_trip(db: Database) -> None:
    rec = record(
        error=error_info(ErrorCode.not_found, "gone"),
        timings={"queue_s": 1.5},
        artifacts=[
            StoredArtifact(
                name="audio.flac",
                role=ArtifactRole.primary_audio,
                media_type="audio/flac",
                size_bytes=10,
                sha256="0" * 64,
            )
        ],
    )
    db.insert(rec)
    assert db.get(rec.id) == rec
    assert db.get("gen_missing") is None


def test_update_sets_updated_at(db: Database) -> None:
    rec = record()
    db.insert(rec)
    later = rec.updated_at + timedelta(seconds=5)
    db.now = lambda: later
    updated = db.update(rec.id, status=GenerationStatus.running, stage="plan")
    assert (updated.status, updated.stage, updated.updated_at) == ("running", "plan", later)
    assert db.get(rec.id) == updated


def test_client_request_id_is_unique(db: Database) -> None:
    db.insert(record(client_request_id="same"))
    with pytest.raises(Exception, match="UNIQUE"):
        db.insert(record(client_request_id="same"))


def test_page_is_newest_first_with_cursor(db: Database) -> None:
    recs = [record() for _ in range(5)]
    for rec in recs:
        db.insert(rec)
    newest_first = [r.id for r in reversed(recs)]
    first = db.page(limit=2, before=None)
    assert [r.id for r in first] == newest_first[:2]
    second = db.page(limit=2, before=first[-1].id)
    assert [r.id for r in second] == newest_first[2:4]
    assert [r.id for r in db.page(limit=10, before=second[-1].id)] == newest_first[4:]


def test_active_lists_non_terminal_oldest_first(db: Database) -> None:
    a = record(status=GenerationStatus.running)
    b = record(status=GenerationStatus.succeeded)
    c = record(status=GenerationStatus.downloading)
    for rec in (a, b, c):
        db.insert(rec)
    assert [r.id for r in db.active()] == [a.id, c.id]


def test_delete(db: Database) -> None:
    rec = record()
    db.insert(rec)
    assert db.delete(rec.id) is True
    assert db.get(rec.id) is None
    assert db.delete(rec.id) is False


def test_settings_key_value(db: Database) -> None:
    assert db.get_setting("worker_url") is None
    db.set_setting("worker_url", "http://a")
    db.set_setting("worker_url", "http://b")
    assert db.get_setting("worker_url") == "http://b"
    db.set_setting("worker_url", None)
    assert db.get_setting("worker_url") is None
