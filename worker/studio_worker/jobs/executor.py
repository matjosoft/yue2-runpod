"""Model loader and the single executor thread (PRD section 7; W-1, W-3, W-4, W-10, W-12).

Route handlers never block on the engine: they call ``submit`` and read snapshots.
Only the loader thread (before ``ready``) and the executor thread touch the engine.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from studio_contracts import Artifact, ErrorCode, ErrorInfo, Job, JobState, ModelState
from studio_worker.engines.base import Engine, EngineOOM
from studio_worker.jobs.errors import error_info
from studio_worker.jobs.queue import JobQueue, QueueFullError
from studio_worker.jobs.store import JobConflictError, JobNotFoundError, JobStore, utcnow
from studio_worker.operations.base import Operation, OperationCanceled, OperationContext

log = logging.getLogger("studio_worker.jobs")

MAX_ERROR_MESSAGE = 500


class NotReadyError(Exception):
    pass


class ModelLoader:
    """Loads the engine once, in a background thread (W-1; PRD section 8 startup)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.state = ModelState.loading
        self.error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._load, name="model-loader", daemon=True)
        self._thread.start()

    def _load(self) -> None:
        log.info("model loading", extra={"engine": self.engine.kind})
        start = time.monotonic()
        try:
            self.engine.load()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_MESSAGE]
            self.state = ModelState.error
            log.error("model failed to load", extra={"error": self.error})
            return
        self.state = ModelState.ready
        log.info("model ready", extra={"seconds": round(time.monotonic() - start, 2)})

    @property
    def ready(self) -> bool:
        return self.state is ModelState.ready

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)


class JobExecutor:
    def __init__(
        self,
        store: JobStore,
        engine: Engine,
        lookup: Callable[[str], Operation | None],
        *,
        max_queue: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.engine = engine
        self.lookup = lookup
        self.queue = JobQueue(max_queue)
        self.loader = ModelLoader(engine)
        self._clock = clock
        self._submit_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._running: str | None = None
        self._cancel_events: dict[str, threading.Event] = {}
        self._last_active = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------------------

    def start(self) -> None:
        self.loader.start()
        self._thread = threading.Thread(target=self._loop, name="job-executor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self.queue.close()
        if self._thread is not None:
            self._thread.join(timeout)
        self.loader.join(timeout)

    # --- submit and status ------------------------------------------------------------

    def submit(
        self, client_request_id: str, operation: Operation, params: BaseModel
    ) -> tuple[Job, bool]:
        """Queue a job. Returns ``(job, created)``; ``created`` is False when the
        ``client_request_id`` was seen before (W-2 idempotency). Raises
        ``NotReadyError`` or ``QueueFullError``."""
        with self._submit_lock:
            existing = self.store.find_by_client_request_id(client_request_id)
            if existing is not None:
                return existing, False
            if not self.loader.ready:
                raise NotReadyError(self.loader.state.value)
            if len(self.queue) >= self.queue.max_size:
                raise QueueFullError(client_request_id)
            job = self.store.create(
                client_request_id=client_request_id,
                operation=operation.name,
                params=params.model_dump(mode="json", exclude_none=True),
                stages=list(operation.stages(self.engine)),
            )
            self.queue.put(job.id)
        log.info("job queued", extra={"job_id": job.id, "operation": job.operation})
        return job, True

    @property
    def running_job_id(self) -> str | None:
        with self._state_lock:
            return self._running

    def idle_seconds(self) -> int:
        """0 while a job runs; otherwise seconds since the last job finished, or since
        startup (W-12)."""
        with self._state_lock:
            if self._running is not None:
                return 0
            return max(0, int(self._clock() - self._last_active))

    def cancel(self, job_id: str) -> Job:
        """Cancel a job (W-10). A queued job ends ``canceled`` at once. A running job is
        flagged and ends ``canceled`` at its next stage boundary, so the answer still
        says ``running``; asking again is harmless. Raises ``JobNotFoundError``, or
        ``JobConflictError`` for a finished job."""
        with self._state_lock:
            job = self.store.get(job_id)
            if job.state.is_terminal:
                raise JobConflictError(f"job is {job.state.value}; it can no longer be canceled")
            if job.state is JobState.queued:
                # The executor may already have taken the id off the queue; ``_run``
                # re-checks the state under this lock before it starts the job.
                self.queue.remove(job_id)
                job = self.store.update(job_id, state=JobState.canceled, finished_at=utcnow())
                log.info("job canceled while queued", extra={"job_id": job_id})
                return job
            event = self._cancel_events.get(job_id)
            if event is not None:
                event.set()
        log.info("job cancel requested", extra={"job_id": job_id})
        return job

    # --- the executor thread ----------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self.queue.get(timeout=0.5)
            if job_id is None:
                continue
            try:
                self._run(job_id)
            except Exception:
                log.exception("executor error", extra={"job_id": job_id})
                self._fail_quietly(job_id, error_info(ErrorCode.internal, "Internal worker error"))
            finally:
                with self._state_lock:
                    self._running = None
                    self._cancel_events.pop(job_id, None)
                    self._last_active = self._clock()

    def _run(self, job_id: str) -> None:
        try:
            job = self.store.get(job_id)
        except JobNotFoundError:
            return
        if job.state is not JobState.queued:
            return
        operation = self.lookup(job.operation)
        params = None if operation is None else operation.params_model.model_validate(job.params)

        cancel_event = threading.Event()
        started = utcnow()
        timings: dict[str, float] = {
            "queue_s": round((started - job.created_at).total_seconds(), 3)
        }
        # Re-check under the lock that ``cancel`` takes: a cancel that arrived after the
        # id left the queue has already finished the job, and that is not an error.
        with self._state_lock:
            if self.store.get(job_id).state is not JobState.queued:
                return
            if operation is None or params is None:  # registry changed since submit
                self._finish(
                    job_id,
                    JobState.failed,
                    error=error_info(
                        ErrorCode.internal, f"Operation {job.operation!r} is no longer registered"
                    ),
                )
                return
            self._running = job_id
            self._cancel_events[job_id] = cancel_event
            self._last_active = self._clock()
            self.store.update(job_id, state=JobState.running, started_at=started, timings=timings)
        log.info("job running", extra={"job_id": job_id})

        run_start = time.monotonic()
        current: dict[str, Any] = {"stage": None, "since": run_start}
        artifacts: list[Artifact] = []
        resolved: dict[str, Any] = {}

        def close_stage() -> None:
            if current["stage"] is not None:
                elapsed = time.monotonic() - current["since"]
                timings[f"{current['stage']}_s"] = round(elapsed, 3)

        def on_stage(name: str, progress: float | None) -> None:
            if name not in job.stages:
                raise ValueError(f"stage {name!r} is not declared by {job.operation}")
            if name != current["stage"]:
                close_stage()
                current["stage"], current["since"] = name, time.monotonic()
                log.info("job stage", extra={"job_id": job_id, "stage": name})
            self.store.update(job_id, stage=name, progress=progress, timings=dict(timings))

        def on_resolved(values: dict[str, Any]) -> None:
            resolved.update(values)
            self.store.update(job_id, params_resolved=dict(resolved))

        ctx = OperationContext(
            engine=self.engine,
            job_dir=self.store.artifacts_dir(job_id),
            on_stage=on_stage,
            on_artifact=artifacts.append,
            on_resolved=on_resolved,
            cancel_event=cancel_event,
        )
        try:
            operation.run(ctx, params)
        except OperationCanceled:
            outcome: tuple[JobState, ErrorInfo | None] = (JobState.canceled, None)
        except EngineOOM as exc:
            outcome = (
                JobState.failed,
                error_info(ErrorCode.oom, "The GPU ran out of memory", details=_exc_details(exc)),
            )
        except Exception as exc:
            outcome = (
                JobState.failed,
                error_info(ErrorCode.engine_error, "The engine failed", details=_exc_details(exc)),
            )
        else:
            outcome = (JobState.succeeded, None)
        close_stage()
        timings["total_s"] = round(time.monotonic() - run_start, 3)
        state, error = outcome
        if state is not JobState.succeeded:
            artifacts = []
            self.store.clear_artifacts(job_id)
        self._finish(job_id, state, error=error, timings=timings, artifacts=artifacts)

    def _finish(self, job_id: str, state: JobState, **changes: Any) -> None:
        job = self.store.update(job_id, state=state, finished_at=utcnow(), progress=None, **changes)
        extra: dict[str, Any] = {"job_id": job_id, "state": state.value}
        if job.error is not None:
            extra["error_code"] = job.error.code.value
        log.info("job finished", extra=extra)

    def _fail_quietly(self, job_id: str, error: ErrorInfo) -> None:
        try:
            job = self.store.get(job_id)
            if not job.state.is_terminal:
                self._finish(job_id, JobState.failed, error=error, artifacts=[])
        except Exception:
            log.exception("could not mark job failed", extra={"job_id": job_id})


def _exc_details(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)[:MAX_ERROR_MESSAGE]}
