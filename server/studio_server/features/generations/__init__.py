"""``generations`` feature: create, list, get, cancel, delete and artifact serving
(S-1, S-4, S-7, S-9)."""

import logging
import os
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, Path, Query, Response
from fastapi.responses import FileResponse
from starlette.responses import MalformedRangeHeader, RangeNotSatisfiable

from studio_contracts import (
    ClientErrorCode,
    ErrorCode,
    Generation,
    GenerationCreate,
    GenerationList,
    GenerationStatus,
)
from studio_server.core import generations
from studio_server.core.backend import BackendError, JobRef, MusicBackend
from studio_server.core.db import GenerationRecord
from studio_server.core.errors import ERROR_RESPONSES, ApiError
from studio_server.core.ids import new_generation_id
from studio_server.core.runtime import Runtime
from studio_server.features.base import Feature

log = logging.getLogger("studio_server.generations")

GenId = Annotated[str, Path(alias="id", max_length=64)]
"""The ``{id}`` path parameter (PRD section 6 names it ``id``)."""

router = APIRouter(prefix="/api/generations", tags=["generations"], responses=ERROR_RESPONSES)

RELAYED_STATUSES = frozenset({422, 429, 503})
"""Worker rejections passed to the UI with the worker's own status; every other backend
failure is 502, and ``not_configured`` is 409 (docs/decisions.md, M2)."""


def _not_found(gen_id: str) -> ApiError:
    return ApiError(ErrorCode.not_found, "Unknown generation", details={"generation_id": gen_id})


def _relay(exc: BackendError) -> ApiError:
    info = exc.info
    if exc.code == ClientErrorCode.not_configured.value:
        return ApiError.from_info(info, 409)
    if exc.http_status in RELAYED_STATUSES:
        fields = info.details.get("fields")
        if isinstance(fields, list):
            # The worker says ``params.<field>``; this API calls the object ``request``.
            details = {
                **info.details,
                "fields": [_rename_loc(f) for f in fields],
            }
            info = info.model_copy(update={"details": details})
        return ApiError.from_info(info, exc.http_status or 502)
    return ApiError.from_info(info, 502)


def _rename_loc(field: Any) -> Any:
    if isinstance(field, dict) and isinstance(field.get("loc"), str):
        loc: str = field["loc"]
        if loc == "params" or loc.startswith("params."):
            return {**field, "loc": "request" + loc[len("params") :]}
    return field


@router.post("", status_code=201, response_model=Generation)
async def create_generation(body: GenerationCreate, rt: Runtime) -> Generation:
    """Validate, fill in the seed, store the record as ``queued`` and submit it. If the
    worker rejects the submit or cannot be reached, no record is kept and the error is
    returned, so the UI can keep the typed input (AT-3)."""
    try:
        caps = await rt.link.capabilities()
    except BackendError as exc:
        raise _relay(exc) from None
    op = next((o for o in caps.operations if o.name == body.operation), None)
    if op is None:
        raise ApiError(
            ErrorCode.validation_error,
            f"The worker does not offer {body.operation!r}",
            details={
                "fields": [{"loc": "operation", "msg": "Unknown operation"}],
                "operations": [o.name for o in caps.operations],
            },
        )
    now = rt.db.now()
    gen_id = new_generation_id()
    title = (body.title or "").strip() or generations.default_title(datetime.now().astimezone())
    rec = GenerationRecord(
        id=gen_id,
        created_at=now,
        updated_at=now,
        title=title,
        operation=op.name,
        request=generations.fill_seed(dict(body.request), op),
        status=GenerationStatus.queued,
        stage=None,
        stages=list(op.stages),
        error=None,
        worker_job_id=None,
        client_request_id=str(uuid.uuid4()),
        timings={},
        artifacts=[],
        data_dir=rt.storage.relative_dir(gen_id),
    )
    rt.db.insert(rec)
    rt.link.submitting.add(gen_id)
    try:
        rec = await generations.submit(rt.link, rt.db, rec)
    except BackendError as exc:
        rt.db.delete(gen_id)
        log.info("submit rejected", extra={"generation_id": gen_id, "code": exc.code})
        raise _relay(exc) from None
    except KeyError:  # canceled and deleted while the submit was in flight
        raise _not_found(gen_id) from None
    finally:
        rt.link.submitting.discard(gen_id)
    log.info(
        "generation created", extra={"generation_id": gen_id, "worker_job_id": rec.worker_job_id}
    )
    return generations.to_api(rec)


@router.get("", response_model=GenerationList)
async def list_generations(
    rt: Runtime,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
) -> GenerationList:
    """Newest first. Pass ``next_cursor`` back as ``cursor`` for the next page."""
    recs = rt.db.page(limit=limit + 1, before=cursor)
    more = len(recs) > limit
    recs = recs[:limit]
    return GenerationList(
        items=[generations.to_api(r) for r in recs],
        next_cursor=recs[-1].id if more else None,
    )


def _get(rt: Runtime, gen_id: str) -> GenerationRecord:
    rec = rt.db.get(gen_id)
    if rec is None:
        raise _not_found(gen_id)
    return rec


@router.get("/{id}", response_model=Generation)
async def get_generation(gen_id: GenId, rt: Runtime) -> Generation:
    return generations.to_api(_get(rt, gen_id))


@router.post("/{id}/cancel", response_model=Generation)
async def cancel_generation(gen_id: GenId, rt: Runtime) -> Generation:
    """Cancel a ``queued`` or ``running`` generation (S-9). A queued one comes back
    ``canceled``. A running one comes back still ``running`` and ends ``canceled`` at
    the worker's next stage boundary; keep polling. Nothing is downloaded. 409 in any
    other status, including ``downloading``."""
    rec = _get(rt, gen_id)
    if rec.status not in generations.CANCELABLE:
        raise ApiError(
            ErrorCode.conflict,
            f"A {rec.status.value} generation cannot be canceled",
            details={"generation_id": gen_id, "status": rec.status.value},
        )
    try:
        rec = await generations.cancel(rt.link, rt.db, rec)
    except BackendError as exc:
        if exc.http_status == 409:  # the worker job finished; the poller catches up
            raise ApiError.from_info(exc.info, 409) from None
        raise _relay(exc) from None
    log.info(
        "generation cancel requested",
        extra={"generation_id": gen_id, "status": rec.status.value},
    )
    return generations.to_api(rec)


async def _delete_remote(backend: MusicBackend, job_id: str) -> None:
    try:
        await backend.delete_job(JobRef(job_id))
    except BackendError as exc:
        log.info("remote delete skipped", extra={"worker_job_id": job_id, "code": exc.code})


@router.delete("/{id}", status_code=204, response_class=Response)
async def delete_generation(gen_id: GenId, rt: Runtime, background: BackgroundTasks) -> Response:
    """Delete the record and its local files, then, best effort and after the response,
    the worker's job. 409 while the generation is still in progress: cancel it first."""
    rec = _get(rt, gen_id)
    if not rec.status.is_terminal:
        raise ApiError(
            ErrorCode.conflict,
            "The generation is still in progress",
            details={"generation_id": gen_id, "status": rec.status.value},
        )
    rt.storage.delete_generation(rec.data_dir)
    rt.db.delete(gen_id)
    if rec.worker_job_id is not None and rt.link.is_configured():
        background.add_task(_delete_remote, rt.link.backend(), rec.worker_job_id)
    log.info("generation deleted", extra={"generation_id": gen_id})
    return Response(status_code=204)


@router.get(
    "/{id}/artifacts/{name:path}",
    response_class=FileResponse,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "The file"},
        206: {"content": {"application/octet-stream": {}}, "description": "A byte range"},
    },
)
async def get_artifact(
    gen_id: GenId,
    name: str,
    rt: Runtime,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> FileResponse:
    """A stored artifact, with HTTP Range support (S-7). Only names in the generation's
    manifest that are stored locally are served."""
    rec = _get(rt, gen_id)
    entry = next((a for a in rec.artifacts if a.name == name and a.stored), None)
    if entry is None:
        raise ApiError(ErrorCode.not_found, "Unknown artifact", details={"generation_id": gen_id})
    try:
        path = rt.storage.artifact_path(rec.data_dir, entry.name)
        stat = os.stat(path)
    except (ValueError, FileNotFoundError):
        raise ApiError(
            ErrorCode.not_found, "Artifact file is gone", details={"generation_id": gen_id}
        ) from None
    response = FileResponse(path, media_type=entry.media_type, stat_result=stat)
    if range_header is not None:
        _check_range(response, range_header, stat.st_size)
    return response


def _check_range(response: FileResponse, header: str, size: int) -> None:
    """Reject a bad Range with the envelope; Starlette would send plain text. Uses
    Starlette's own parser (copied from worker/studio_worker/api/artifacts.py)."""
    try:
        response._parse_range_header(header, size)
    except MalformedRangeHeader:
        raise ApiError(ErrorCode.validation_error, "Malformed Range header") from None
    except RangeNotSatisfiable:
        raise ApiError(
            ErrorCode.validation_error,
            "Range not satisfiable",
            status_code=416,
            headers={"Content-Range": f"bytes */{size}"},
        ) from None


feature = Feature(name="generations", router=router)
