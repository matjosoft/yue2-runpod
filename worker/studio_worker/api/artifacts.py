"""``GET /v1/jobs/{id}/artifacts/{name}`` with Range support (W-5).

A name is served only if it is in the job's manifest; the path is never built from
the request alone.
"""

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse
from starlette.responses import MalformedRangeHeader, RangeNotSatisfiable

from studio_contracts import ErrorCode
from studio_worker.errors import ERROR_RESPONSES, ApiError
from studio_worker.jobs.store import JobNotFoundError
from studio_worker.runtime import WorkerRuntime, get_runtime

router = APIRouter(prefix="/jobs", tags=["artifacts"], responses=ERROR_RESPONSES)


@router.get(
    "/{job_id}/artifacts/{name:path}",
    response_class=FileResponse,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "The file"},
        206: {"content": {"application/octet-stream": {}}, "description": "A byte range"},
    },
)
async def get_artifact(
    job_id: str,
    name: str,
    rt: Annotated[WorkerRuntime, Depends(get_runtime)],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> FileResponse:
    try:
        found = rt.store.artifact_path(job_id, name)
    except JobNotFoundError:
        raise ApiError(ErrorCode.not_found, "Unknown job", details={"job_id": job_id}) from None
    if found is None:
        raise ApiError(ErrorCode.not_found, "Unknown artifact", details={"job_id": job_id})
    path, media_type = found
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        raise ApiError(
            ErrorCode.not_found, "Artifact file is gone", details={"job_id": job_id}
        ) from None
    response = FileResponse(path, media_type=media_type, stat_result=stat)
    if range_header is not None:
        _check_range(response, range_header, stat.st_size)
    return response


def _check_range(response: FileResponse, header: str, size: int) -> None:
    """Reject a bad Range here, so the error carries the envelope; Starlette would send
    plain text. Uses Starlette's own parser, so the two cannot disagree."""
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
