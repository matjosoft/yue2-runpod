"""The poller: one asyncio task that advances every non-terminal generation (S-2, S-3).

Each tick, for each generation:

- no worker job yet (the server stopped between storing and submitting): resubmit with
  the same ``client_request_id``, which the worker treats as the same job;
- ``queued`` or ``running``: mirror the worker job's state, stage, stages, timings and
  error;
- the worker job succeeded: set ``downloading``, fetch the artifacts (at most 2 at once)
  to temp files, verify size and sha256, rename them into place, then set ``succeeded``.

State lives in the database, so after a restart the poller simply resumes. If the
worker cannot be reached for longer than ``STUDIO_UNREACHABLE_GRACE_S``, the generation
fails with ``worker_unreachable``. The loop also refreshes the cached worker check
behind ``/api/health``.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from studio_contracts import (
    ArtifactRole,
    ClientErrorCode,
    ErrorCode,
    GenerationStatus,
    ServerErrorInfo,
)
from studio_server.config import ServerSettings
from studio_server.core import generations
from studio_server.core.backend import BackendError, JobRef
from studio_server.core.db import Database, GenerationRecord, StoredArtifact
from studio_server.core.errors import error_info
from studio_server.core.storage import Storage, VerifyError
from studio_server.core.worker import WorkerLink

log = logging.getLogger("studio_server.poller")

MAX_PARALLEL_DOWNLOADS = 2
DOWNLOAD_RETRIES = 3
"""A download that fails verification is fetched again up to this many times."""

_TRANSIENT = frozenset(
    {
        ClientErrorCode.worker_unreachable.value,
        ClientErrorCode.not_configured.value,
        ErrorCode.unauthorized.value,
        ErrorCode.queue_full.value,
        ErrorCode.not_ready.value,
        ErrorCode.internal.value,
    }
)
"""Errors that may clear up (network, settings being fixed, a busy worker): they count
toward the unreachable grace period instead of failing the generation at once."""


class Poller:
    def __init__(
        self,
        db: Database,
        storage: Storage,
        link: WorkerLink,
        settings: ServerSettings,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self._storage = storage
        self._link = link
        self._settings = settings
        self.clock = clock
        self._unreachable_since: dict[str, float] = {}
        self._downloads = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
        self._task: asyncio.Task[None] | None = None
        self._last_check: float | None = None

    # --- loop -------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="studio-poller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            now = self.clock()
            if (
                self._last_check is None
                or now - self._last_check >= self._settings.check_interval_s
            ):
                self._last_check = now
                try:
                    await self._link.check()
                except Exception:
                    log.exception("worker check failed")
            try:
                await self.tick()
            except Exception:
                log.exception("poller tick failed")
            await asyncio.sleep(self._settings.poll_interval_s)

    async def tick(self) -> None:
        """Advance every non-terminal generation once."""
        for rec in self._db.active():
            if rec.id in self._link.submitting:
                continue
            try:
                await self._advance(rec)
            except KeyError:
                continue  # deleted while we worked on it

    # --- one generation ---------------------------------------------------------------

    async def _advance(self, rec: GenerationRecord) -> None:
        if rec.status is GenerationStatus.downloading:
            await self._download(rec)
            return
        if rec.worker_job_id is None:
            try:
                rec = await generations.submit(self._link, self._db, rec)
            except BackendError as exc:
                self._on_error(rec, exc, during_submit=True)
                return
        assert rec.worker_job_id is not None
        try:
            view = await self._link.backend().get_job(JobRef(rec.worker_job_id))
        except BackendError as exc:
            self._on_error(rec, exc, during_submit=False)
            return
        self._unreachable_since.pop(rec.id, None)
        changes = generations.mirror_changes(rec, view)
        if changes:
            rec = self._db.update(rec.id, **changes)
            log.info(
                "generation updated",
                extra={"generation_id": rec.id, "status": rec.status.value, "stage": rec.stage},
            )
        if rec.status is GenerationStatus.downloading:
            await self._download(rec)

    def _on_error(self, rec: GenerationRecord, exc: BackendError, *, during_submit: bool) -> None:
        if not during_submit and exc.http_status == 404:
            self._fail(
                rec,
                error_info(
                    ErrorCode.not_found,
                    "The worker no longer has this job",
                    details={"worker_job_id": rec.worker_job_id},
                ),
            )
        elif exc.code in _TRANSIENT:
            self._wait_or_give_up(rec, exc)
        else:
            self._fail(rec, exc.info)

    def _wait_or_give_up(self, rec: GenerationRecord, exc: BackendError) -> None:
        since = self._unreachable_since.setdefault(rec.id, self.clock())
        waited = self.clock() - since
        log.info(
            "worker not reachable for generation",
            extra={"generation_id": rec.id, "code": exc.code, "waited_s": round(waited, 1)},
        )
        if waited >= self._settings.unreachable_grace_s:
            self._fail(
                rec,
                error_info(
                    ClientErrorCode.worker_unreachable,
                    "The worker could not be reached for too long",
                    details={"waited_s": round(waited), "last_error": exc.code},
                ),
            )

    def _fail(self, rec: GenerationRecord, info: ServerErrorInfo) -> None:
        self._unreachable_since.pop(rec.id, None)
        self._db.update(rec.id, status=GenerationStatus.failed, error=info)
        log.info(
            "generation failed",
            extra={"generation_id": rec.id, "code": info.code.value},
        )

    # --- downloads --------------------------------------------------------------------

    async def _download(self, rec: GenerationRecord) -> None:
        assert rec.worker_job_id is not None
        ref = JobRef(rec.worker_job_id)
        wanted = [
            a
            for a in rec.artifacts
            if not a.stored
            and (self._settings.download_intermediates or a.role is not ArtifactRole.intermediate)
        ]
        results = await asyncio.gather(*(self._fetch(rec, ref, a) for a in wanted))
        done = {a.name for a, err in zip(wanted, results, strict=True) if err is None}
        artifacts = [
            a.model_copy(update={"stored": True}) if a.name in done else a for a in rec.artifacts
        ]
        errors = [err for err in results if err is not None]
        if not errors:
            self._unreachable_since.pop(rec.id, None)
            self._db.update(rec.id, status=GenerationStatus.succeeded, artifacts=artifacts)
            log.info("generation succeeded", extra={"generation_id": rec.id, "files": len(done)})
            return
        rec = self._db.update(rec.id, artifacts=artifacts)
        transient = [e for e in errors if isinstance(e, BackendError) and e.code in _TRANSIENT]
        if len(transient) == len(errors):
            self._wait_or_give_up(rec, transient[0])
            return
        failed = [a.name for a, err in zip(wanted, results, strict=True) if err is not None]
        self._fail(
            rec,
            error_info(
                ClientErrorCode.download_failed,
                "Artifacts could not be downloaded and verified",
                details={"artifacts": failed, "reason": str(errors[0])},
            ),
        )

    async def _fetch(
        self, rec: GenerationRecord, ref: JobRef, entry: StoredArtifact
    ) -> Exception | None:
        """Download, verify and store one artifact. Returns the error, if any."""
        async with self._downloads:
            last: Exception | None = None
            for attempt in range(1 + DOWNLOAD_RETRIES):
                tmp = self._storage.temp_path(rec.data_dir)
                try:
                    await self._link.backend().download_artifact(ref, entry.name, tmp)
                    await asyncio.to_thread(self._storage.finalize, rec.data_dir, tmp, entry)
                    return None
                except VerifyError as exc:
                    last = exc
                    log.warning(
                        "artifact failed verification",
                        extra={
                            "generation_id": rec.id,
                            "artifact": entry.name,
                            "attempt": attempt + 1,
                        },
                    )
                except (BackendError, OSError) as exc:
                    return exc
                finally:
                    tmp.unlink(missing_ok=True)
            return last
