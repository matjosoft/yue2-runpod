"""Generation records: API view, seed filling, worker-state mirroring and submit.

Shared by the ``generations`` feature and the poller, so it lives in core
(docs/decisions.md). It never branches on an operation name: the seed is filled from the
operation's published params schema.
"""

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
from studio_server.core.backend import JobView
from studio_server.core.db import Database, GenerationRecord, StoredArtifact
from studio_server.core.worker import WorkerLink

SEED_FALLBACK_MAX = 2**31 - 1

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
    worker) and store the job id. Raises ``BackendError``."""
    ref = await link.backend().submit(rec.operation, rec.request, rec.client_request_id)
    return db.update(rec.id, worker_job_id=ref.job_id)
