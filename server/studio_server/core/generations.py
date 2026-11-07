"""Generation records: API view, seed filling, worker-state mirroring, submit and cancel.

Shared by the ``generations`` feature and the poller, so it lives in core
(docs/decisions.md). It never branches on an operation name: the seed is filled from the
operation's published params schema.
"""

import logging
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import quote

from studio_contracts import (
    Generation,
    GenerationArtifact,
    GenerationStatus,
    JobState,
    OperationInfo,
)
from studio_server.core.backend import BackendError, JobRef, JobView, MusicBackend
from studio_server.core.db import Database, GenerationRecord, StoredArtifact
from studio_server.core.worker import WorkerLink

log = logging.getLogger("studio_server.generations")

SEED_FALLBACK_MAX = 2**31 - 1

CANCELABLE = frozenset({GenerationStatus.queued, GenerationStatus.running})
"""S-9: only these can be canceled. ``downloading`` cannot: the worker job has already
succeeded (docs/decisions.md, M5)."""

STATUS_FOR_STATE: dict[JobState, GenerationStatus] = {
    JobState.queued: GenerationStatus.queued,
    JobState.running: GenerationStatus.running,
    JobState.succeeded: GenerationStatus.downloading,  # succeeded once files are local
    JobState.failed: GenerationStatus.failed,
    JobState.canceled: GenerationStatus.canceled,
}


def artifact_url(gen_id: str, name: str) -> str:
    return f"/api/generations/{gen_id}/artifacts/{quote(name, safe='/')}"


def to_api(rec: GenerationRecord) -> Generation:
    """The API view. Only artifacts stored and verified locally are listed."""
    return Generation(
        id=rec.id,
        title=rec.title,
        operation=rec.operation,
        request=rec.request,
        status=rec.status,
        stage=rec.stage,
        stages=rec.stages,
        error=rec.error,
        worker_job_id=rec.worker_job_id,
        timings=rec.timings,
        artifacts=[
            GenerationArtifact(
                name=a.name,
                role=a.role,
                media_type=a.media_type,
                size_bytes=a.size_bytes,
                url=artifact_url(rec.id, a.name),
            )
            for a in rec.artifacts
            if a.stored
        ],
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


def default_title(now_local: datetime) -> str:
    return f"Untitled {now_local:%Y-%m-%d %H:%M}"


def _int_bounds(prop: dict[str, Any]) -> tuple[int, int] | None:
    """``(minimum, maximum)`` of an integer property, also inside ``anyOf`` (pydantic
    writes ``int | None`` that way)."""
    for candidate in [prop, *prop.get("anyOf", [])]:
        if isinstance(candidate, dict) and candidate.get("type") == "integer":
            low = candidate.get("minimum", 0)
            high = candidate.get("maximum", SEED_FALLBACK_MAX)
            if isinstance(low, int) and isinstance(high, int) and low <= high:
                return low, high
    return None


def fill_seed(request: dict[str, Any], op: OperationInfo) -> dict[str, Any]:
    """S-1: if the operation takes an integer ``seed`` and none was given, pick one, so
    the stored request always says which seed was used."""
    props = op.params_schema.get("properties", {})
    prop = props.get("seed") if isinstance(props, dict) else None
    if not isinstance(prop, dict) or request.get("seed") is not None:
        return request
    bounds = _int_bounds(prop)
    if bounds is None:
        return request
    low, high = bounds
    return {**request, "seed": low + secrets.randbelow(high - low + 1)}


def mirror_changes(rec: GenerationRecord, view: JobView) -> dict[str, Any]:
    """Record fields that differ from the worker job. Empty when nothing changed."""
    target: dict[str, Any] = {
        "status": STATUS_FOR_STATE[view.state],
        "stage": view.stage,
        "stages": view.stages,
        "timings": view.timings,
        "error": view.error,
    }
    if view.state is JobState.succeeded and not rec.artifacts:
        target["artifacts"] = [StoredArtifact(**a.model_dump()) for a in view.artifacts]
    return {k: v for k, v in target.items() if getattr(rec, k) != v}


async def submit(link: WorkerLink, db: Database, rec: GenerationRecord) -> GenerationRecord:
    """Submit to the backend with the record's ``client_request_id`` (idempotent on the
    worker) and store the job id. Raises ``BackendError``.

    If the record was canceled while the submit was in flight, the new job is canceled
    too (best effort). If it was deleted meanwhile, the job is canceled and
    ``KeyError`` is raised."""
    backend = link.backend()
    ref = await backend.submit(rec.operation, rec.request, rec.client_request_id)
    try:
        updated = db.update(rec.id, worker_job_id=ref.job_id)
    except KeyError:
        await _cancel_quietly(backend, ref, rec.id)
        raise
    if updated.status is GenerationStatus.canceled:
        await _cancel_quietly(backend, ref, rec.id)
    return updated


async def _cancel_quietly(backend: MusicBackend, ref: JobRef, gen_id: str) -> None:
    try:
        await backend.cancel(ref)
    except BackendError as exc:
        log.info("remote cancel skipped", extra={"generation_id": gen_id, "code": exc.code})


async def cancel(link: WorkerLink, db: Database, rec: GenerationRecord) -> GenerationRecord:
    """S-9: cancel the worker job, then mirror it once. A queued job comes back
    ``canceled``; a running one still says ``running`` and the poller mirrors
    ``canceled`` when the worker reaches the next stage boundary. A record with no
    worker job, or whose job the worker no longer has, is canceled locally. Raises
    ``BackendError`` (a worker 409 means the job has already finished)."""
    if rec.worker_job_id is None:
        return db.update(rec.id, status=GenerationStatus.canceled)
    ref = JobRef(rec.worker_job_id)
    backend = link.backend()
    try:
        await backend.cancel(ref)
    except BackendError as exc:
        if exc.http_status != 404:
            raise
        return db.update(rec.id, status=GenerationStatus.canceled)
    try:
        view = await backend.get_job(ref)
    except BackendError as exc:  # the poller catches up
        log.info("job read after cancel failed", extra={"generation_id": rec.id, "code": exc.code})
        return db.get(rec.id) or rec
    current = db.get(rec.id)
    if current is None:
        raise KeyError(rec.id)
    if current.status not in CANCELABLE:
        return current  # the poller moved it on while we waited; keep the newer state
    changes = mirror_changes(current, view)
    return db.update(rec.id, **changes) if changes else current
