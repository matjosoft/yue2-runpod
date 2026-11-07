import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from studio_contracts import ErrorCode, Job, JobState
from studio_worker.jobs.store import (
    JobConflictError,
    JobNotFoundError,
    JobStore,
    UlidFactory,
)


def new_job(store: JobStore, crid: str = "c1") -> Job:
    return store.create(
        client_request_id=crid,
        operation="song.generate",
        params={"style": "s", "lyrics": "l", "cot": "full"},
        stages=["generate", "save"],
    )


def test_create_writes_job_json_and_artifacts_folder(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = new_job(store)
    assert job.id.startswith("job_") and len(job.id) == 4 + 26
    assert job.state is JobState.queued and job.stage is None
    assert (tmp_path / job.id / "artifacts").is_dir()
    on_disk = Job.model_validate_json((tmp_path / job.id / "job.json").read_bytes())
    assert on_disk == job
    assert not list(tmp_path.rglob("*.tmp"))


def test_updates_persist_atomically(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = new_job(store)
    store.update(job.id, state=JobState.running, stage="generate")
    on_disk = json.loads((tmp_path / job.id / "job.json").read_text())
    assert (on_disk["state"], on_disk["stage"]) == ("running", "generate")
    assert not list(tmp_path.rglob("*.tmp"))


def test_get_returns_a_copy(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = new_job(store)
    job.params["style"] = "mutated"
    assert store.get(job.id).params["style"] == "s"


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ([JobState.running, JobState.succeeded], True),
        ([JobState.running, JobState.failed], True),
        ([JobState.canceled], True),
        ([JobState.succeeded], False),
        ([JobState.running, JobState.queued], False),
        ([JobState.running, JobState.succeeded, JobState.running], False),
        ([JobState.failed, JobState.running], False),
    ],
)
def test_state_transitions(tmp_path: Path, path: list[JobState], ok: bool) -> None:
    store = JobStore(tmp_path)
    job = new_job(store)
    *head, last = path
    for state in head:
        store.update(job.id, state=state)
    if ok:
        assert store.update(job.id, state=last).state is last
    else:
        with pytest.raises(JobConflictError):
            store.update(job.id, state=last)


def test_restart_fails_interrupted_jobs_and_keeps_finished_ones(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    queued = new_job(store, "a")
    running = new_job(store, "b")
    done = new_job(store, "c")
    store.update(running.id, state=JobState.running, stage="generate")
    store.update(done.id, state=JobState.running)
    store.update(done.id, state=JobState.succeeded)

    fresh = JobStore(tmp_path)
    assert sorted(fresh.load()) == sorted([queued.id, running.id])
    for job_id in (queued.id, running.id):
        job = fresh.get(job_id)
        assert job.state is JobState.failed
        assert job.error is not None and job.error.code is ErrorCode.worker_restarted
        assert job.error.retryable is True
        assert job.finished_at is not None
    assert fresh.get(done.id).state is JobState.succeeded
    # The idempotency index is rebuilt from disk.
    found = fresh.find_by_client_request_id("b")
    assert found is not None and found.id == running.id
    # And the recovery was persisted.
    again = JobStore(tmp_path)
    assert again.load() == []


def test_load_skips_corrupt_files(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    good = new_job(store)
    (tmp_path / "job_BROKEN").mkdir()
    (tmp_path / "job_BROKEN" / "job.json").write_text("{not json")
    fresh = JobStore(tmp_path)
    fresh.load()
    assert [j.id for j in fresh.list_recent()] == [good.id]


def test_list_is_newest_first_and_capped_at_50(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    ids = [new_job(store, f"c{i}").id for i in range(55)]
    listed = [j.id for j in store.list_recent()]
    assert listed == list(reversed(ids))[:50]


def test_ulids_are_monotonic() -> None:
    factory = UlidFactory()
    ids = [factory.new() for _ in range(1000)]
    assert ids == sorted(ids) and len(set(ids)) == 1000
    assert all(len(i) == 26 for i in ids)


def test_delete_only_finished_jobs(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = new_job(store)
    with pytest.raises(JobConflictError):
        store.delete(job.id)
    store.update(job.id, state=JobState.canceled)
    store.delete(job.id)
    assert not (tmp_path / job.id).exists()
    with pytest.raises(JobNotFoundError):
        store.get(job.id)
    assert store.find_by_client_request_id("c1") is None
    with pytest.raises(JobNotFoundError):
        store.delete(job.id)


def test_artifact_path_only_from_the_manifest(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = new_job(store)
    (store.artifacts_dir(job.id) / "a.txt").write_text("hi")
    assert store.artifact_path(job.id, "a.txt") is None  # not in the manifest yet
    store.update(
        job.id,
        artifacts=[
            {
                "name": "a.txt",
                "role": "other",
                "media_type": "text/plain",
                "size_bytes": 2,
                "sha256": "0" * 64,
            }
        ],
    )
    assert store.artifact_path(job.id, "a.txt") == (
        store.artifacts_dir(job.id) / "a.txt",
        "text/plain",
    )
    assert store.artifact_path(job.id, "../job.json") is None


# --- TTL cleanup (W-11) -----------------------------------------------------------------


def test_sweep_removes_only_expired_finished_jobs(tmp_path: Path) -> None:
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    clock = {"now": now - timedelta(hours=30)}
    store = JobStore(tmp_path, clock=lambda: clock["now"])
    old_done = new_job(store, "old-done")
    store.update(old_done.id, state=JobState.canceled, finished_at=now - timedelta(hours=24))
    old_running = new_job(store, "old-running")
    store.update(old_running.id, state=JobState.running)
    old_queued = new_job(store, "old-queued")
    fresh = new_job(store, "fresh")
    store.update(fresh.id, state=JobState.canceled, finished_at=now - timedelta(hours=23))

    removed = store.sweep_expired(now, timedelta(hours=24))

    assert removed == [old_done.id]
    assert not (tmp_path / old_done.id).exists()
    with pytest.raises(JobNotFoundError):
        store.get(old_done.id)
    assert store.find_by_client_request_id("old-done") is None  # a new job can reuse it
    for job in (old_running, old_queued, fresh):
        assert store.get(job.id).id == job.id
        assert (tmp_path / job.id).is_dir()
    assert store.sweep_expired(now, timedelta(hours=24)) == []
