"""Job routes: submit, list, get, cancel, delete (W-2, W-3, W-4, W-10)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import ValidationError

from studio_contracts import ErrorCode, Job, JobList, JobSubmit
from studio_worker.errors import ERROR_RESPONSES, ApiError, validation_error
from studio_worker.jobs.executor import NotReadyError
from studio_worker.jobs.queue import QueueFullError
from studio_worker.jobs.store import JobConflictError, JobNotFoundError
from studio_worker.operations.registry import all_operations, get_operation
from studio_worker.runtime import WorkerRuntime, get_runtime

router = APIRouter(prefix="/jobs", tags=["jobs"], responses=ERROR_RESPONSES)

Runtime = Annotated[WorkerRuntime, Depends(get_runtime)]


def not_found(job_id: str) -> ApiError:
    return ApiError(ErrorCode.not_found, "Unknown job", details={"job_id": job_id})


@router.post("", status_code=202, response_model=Job)
async def submit_job(body: JobSubmit, rt: Runtime) -> Job:
    """Validate, then queue. The order of checks is: params (422), idempotency (the
    same ``client_request_id`` returns the existing job), readiness (503), queue (429)."""
    operation = get_operation(body.operation)
    if operation is None:
        raise ApiError(
            ErrorCode.validation_error,
            f"Unknown operation {body.operation!r}",
            details={
                "fields": [{"loc": "operation", "msg": "Unknown operation"}],
                "operations": [op.name for op in all_operations()],
            },
        )
    try:
        params = operation.params_model.model_validate(body.params)
    except ValidationError as exc:
        raise validation_error("Invalid params", exc, prefix=("params",)) from None
    try:
        job, _created = rt.executor.submit(body.client_request_id, operation, params)
    except NotReadyError:
        loader = rt.executor.loader
        message = (
            "The model is still loading" if loader.error is None else "The model failed to load"
        )
        raise ApiError(
            ErrorCode.not_ready, message, details={"model_state": loader.state.value}
        ) from None
    except QueueFullError:
        raise ApiError(
            ErrorCode.queue_full,
            "The job queue is full",
            details={"max": rt.executor.queue.max_size},
        ) from None
    return job


@router.get("", response_model=JobList)
async def list_jobs(rt: Runtime) -> JobList:
    """Recent jobs, newest first, at most 50."""
    return JobList(jobs=rt.store.list_recent())


@router.get("/{job_id}", response_model=Job)
async def get_job(job_id: str, rt: Runtime) -> Job:
    try:
        return rt.store.get(job_id)
    except JobNotFoundError:
        raise not_found(job_id) from None


@router.post("/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: str, rt: Runtime) -> Job:
    """Cancel a job (W-10). A queued job is ``canceled`` in the answer. A running job
    stops at its next stage boundary, so the answer still says ``running``; poll until
    it is ``canceled``. Its output is discarded. 409 if the job has already finished."""
    try:
        return rt.executor.cancel(job_id)
    except JobNotFoundError:
        raise not_found(job_id) from None
    except JobConflictError as exc:
        raise ApiError(ErrorCode.conflict, str(exc), details={"job_id": job_id}) from None


@router.delete("/{job_id}", status_code=204, response_class=Response)
async def delete_job(job_id: str, rt: Runtime) -> Response:
    """Delete a finished job's files. 409 while the job is queued or running."""
    try:
        rt.store.delete(job_id)
    except JobNotFoundError:
        raise not_found(job_id) from None
    except JobConflictError as exc:
        raise ApiError(ErrorCode.conflict, str(exc), details={"job_id": job_id}) from None
    return Response(status_code=204)
