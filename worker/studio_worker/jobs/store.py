"""Job store: one folder per job, ``JOBS_DIR/<id>/job.json`` written atomically, and
an ``artifacts/`` folder beside it (PRD section 7)."""

import logging
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from studio_contracts import ErrorCode, Job, JobState
from studio_worker.jobs.errors import error_info
from studio_worker.operations.base import is_safe_relpath

log = logging.getLogger("studio_worker.jobs")

LIST_LIMIT = 50
JOB_FILE = "job.json"
ARTIFACTS_DIR = "artifacts"

ALLOWED: dict[JobState, frozenset[JobState]] = {
    JobState.queued: frozenset({JobState.running, JobState.canceled, JobState.failed}),
    JobState.running: frozenset({JobState.succeeded, JobState.failed, JobState.canceled}),
    JobState.succeeded: frozenset(),
    JobState.failed: frozenset(),
    JobState.canceled: frozenset(),
}


def _check_transition(old: JobState, new: JobState) -> None:
    if new is not old and new not in ALLOWED[old]:
        raise JobConflictError(f"cannot move job from {old.value} to {new.value}")


class JobNotFoundError(KeyError):
    pass


class JobConflictError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class UlidFactory:
    """Monotonic ULIDs (stdlib only): 48-bit ms time + 80 random bits, Crockford base32.
    Within one millisecond the random part is incremented, so ids sort by creation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_rand = 0

    def new(self) -> str:
        with self._lock:
            ms = time.time_ns() // 1_000_000
            if ms <= self._last_ms:
                ms = self._last_ms
                self._last_rand = (self._last_rand + 1) % (1 << 80)
            else:
                self._last_rand = secrets.randbits(80)
            self._last_ms = ms
            value = (ms << 80) | self._last_rand
        return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


class JobStore:
    def __init__(self, jobs_dir: Path, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.jobs_dir = jobs_dir
        self._clock = clock
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._by_request: dict[str, str] = {}
        self._ids = UlidFactory()

    # --- startup ----------------------------------------------------------------------

    def load(self) -> list[str]:
        """Read every ``job.json``. Jobs left ``queued`` or ``running`` by a previous
        process become ``failed`` with ``worker_restarted``. Returns their ids."""
        recovered: list[str] = []
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs.clear()
            self._by_request.clear()
            for path in sorted(self.jobs_dir.glob(f"*/{JOB_FILE}")):
                try:
                    job = Job.model_validate_json(path.read_bytes())
                except (OSError, ValidationError) as exc:
                    log.warning(
                        "skipping unreadable job file",
                        extra={"path": str(path), "error": type(exc).__name__},
                    )
                    continue
                if job.id != path.parent.name:
                    log.warning("skipping job file in the wrong folder", extra={"path": str(path)})
                    continue
                if not job.state.is_terminal:
                    job = job.model_copy(
                        update={
                            "state": JobState.failed,
                            "finished_at": self._clock(),
                            "error": error_info(
                                ErrorCode.worker_restarted,
                                "The worker restarted before the job finished",
                            ),
                            "artifacts": [],
                        }
                    )
                    self._write(job)
                    recovered.append(job.id)
                self._jobs[job.id] = job
                self._by_request[job.client_request_id] = job.id
        if recovered:
            log.info("failed interrupted jobs", extra={"job_ids": recovered})
        return recovered

    # --- reads ------------------------------------------------------------------------

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            return job.model_copy(deep=True)

    def find_by_client_request_id(self, client_request_id: str) -> Job | None:
        with self._lock:
            job_id = self._by_request.get(client_request_id)
            return None if job_id is None else self.get(job_id)

    def list_recent(self, limit: int = LIST_LIMIT) -> list[Job]:
        with self._lock:
            ids = sorted(self._jobs, reverse=True)[:limit]  # ULIDs sort by creation
            return [self._jobs[i].model_copy(deep=True) for i in ids]

    def count(self, state: JobState) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.state is state)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def artifacts_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / ARTIFACTS_DIR

    def artifact_path(self, job_id: str, name: str) -> tuple[Path, str] | None:
        """Path and media type of a manifest entry, or ``None``. Names are looked up in
        the manifest, never joined from user input alone."""
        if not is_safe_relpath(name):
            return None
        job = self.get(job_id)
        for artifact in job.artifacts:
            if artifact.name == name:
                return self.artifacts_dir(job_id) / name, artifact.media_type
        return None

    # --- writes -----------------------------------------------------------------------

    def create(
        self,
        *,
        client_request_id: str,
        operation: str,
        params: dict[str, Any],
        stages: list[str],
    ) -> Job:
        with self._lock:
            job = Job(
                id=f"job_{self._ids.new()}",
                client_request_id=client_request_id,
                operation=operation,
                state=JobState.queued,
                stage=None,
                stages=stages,
                progress=None,
                params=params,
                params_resolved={},
                created_at=self._clock(),
                started_at=None,
                finished_at=None,
                timings={},
                error=None,
                artifacts=[],
            )
            self.artifacts_dir(job.id).mkdir(parents=True)
            self._write(job)
            self._jobs[job.id] = job
            self._by_request[client_request_id] = job.id
            return job.model_copy(deep=True)

    def update(self, job_id: str, **changes: Any) -> Job:
        """Apply field changes, enforce legal state transitions, persist atomically."""
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise JobNotFoundError(job_id)
            if "state" in changes:
                _check_transition(current.state, JobState(changes["state"]))
            job = Job.model_validate({**current.model_dump(), **changes})
            self._write(job)
            self._jobs[job_id] = job
            return job.model_copy(deep=True)

    def delete(self, job_id: str) -> None:
        """Remove a terminal job's files and record. 409 territory if not terminal."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            if not job.state.is_terminal:
                raise JobConflictError(
                    f"job is {job.state.value}; only finished jobs can be deleted"
                )
            shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
            del self._jobs[job_id]
            if self._by_request.get(job.client_request_id) == job_id:
                del self._by_request[job.client_request_id]

    def clear_artifacts(self, job_id: str) -> None:
        """Discard whatever a failed or canceled job wrote."""
        folder = self.artifacts_dir(job_id)
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)

    def _write(self, job: Job) -> None:
        folder = self.job_dir(job.id)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".{JOB_FILE}.tmp"
        data = job.model_dump_json(indent=2).encode("utf-8")
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, folder / JOB_FILE)
