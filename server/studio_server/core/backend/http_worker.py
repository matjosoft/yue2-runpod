"""``http_worker``: the ``MusicBackend`` that talks to the worker API (PRD section 6).

It translates calls into HTTP and errors into ``BackendError``; it holds no feature
logic. Transient failures (network errors, 429, 502, 503, 504) are retried with
exponential backoff, at most 3 times. No other 4xx is retried. The token is sent only in
the ``Authorization`` header and never appears in an error or a log line.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from studio_contracts import (
    API_VERSION,
    Capabilities,
    ClientErrorCode,
    ErrorCode,
    ErrorEnvelope,
    Job,
    JobSubmit,
    ServerErrorInfo,
    WorkerStatus,
)
from studio_server.core.backend.base import BackendError, JobRef, JobView
from studio_server.core.errors import Code, error_info
from studio_server.core.storage import is_safe_relpath

log = logging.getLogger("studio_server.backend")

MAX_RETRIES = 3
RETRY_STATUSES = frozenset({429, 502, 503, 504})
TIMEOUT = httpx.Timeout(15.0, connect=10.0)
CHUNK_BYTES = 1024 * 1024

_STATUS_CODES: dict[int, Code] = {
    401: ErrorCode.unauthorized,
    404: ErrorCode.not_found,
    409: ErrorCode.conflict,
    422: ErrorCode.validation_error,
    429: ErrorCode.queue_full,
}


def api_major(version: str) -> str:
    return version.split(".", 1)[0]


def _unreachable(reason: str, **details: Any) -> BackendError:
    return BackendError(
        error_info(
            ClientErrorCode.worker_unreachable,
            f"Could not reach the worker: {reason}",
            details=details,
        )
    )


def _error_from_response(response: httpx.Response) -> BackendError:
    """The worker's envelope when it sent one; otherwise a code from the status (e.g. a
    proxy's HTML 502 page)."""
    try:
        worker_info = ErrorEnvelope.model_validate_json(response.content).error
        info = ServerErrorInfo.model_validate(worker_info.model_dump())
    except ValidationError:
        status = response.status_code
        if status in (502, 503, 504):
            return BackendError(
                error_info(
                    ClientErrorCode.worker_unreachable,
                    f"The worker is unavailable (HTTP {status})",
                    details={"http_status": status},
                ),
                status,
            )
        code = _STATUS_CODES.get(status, ErrorCode.internal)
        info = error_info(
            code, f"The worker answered HTTP {status}", details={"http_status": status}
        )
    return BackendError(info, response.status_code)


class HttpWorkerBackend:
    kind = "http_worker"

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_base_s: float = 0.5,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        self._retry_base_s = retry_base_s
        self._max_retries = max_retries

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- MusicBackend -----------------------------------------------------------------

    async def status(self) -> WorkerStatus:
        response = await self._request("GET", "/v1/status")
        return self._parse(WorkerStatus, response)

    async def capabilities(self) -> Capabilities:
        """Refuses a worker whose API major version differs (PRD section 6)."""
        response = await self._request("GET", "/v1/capabilities")
        caps = self._parse(Capabilities, response)
        if api_major(caps.api_version) != api_major(API_VERSION):
            raise BackendError(
                error_info(
                    ClientErrorCode.incompatible_worker,
                    f"The worker speaks API {caps.api_version}; this server needs "
                    f"{api_major(API_VERSION)}.x",
                    details={"worker_api_version": caps.api_version, "api_version": API_VERSION},
                )
            )
        return caps

    async def submit(
        self, operation: str, params: dict[str, Any], client_request_id: str
    ) -> JobRef:
        body = JobSubmit(
            client_request_id=client_request_id, operation=operation, params=params
        ).model_dump(mode="json")
        response = await self._request("POST", "/v1/jobs", json=body)
        return JobRef(job_id=self._parse(Job, response).id)

    async def get_job(self, ref: JobRef) -> JobView:
        response = await self._request("GET", self._job_path(ref))
        job = self._parse(Job, response)
        return JobView(
            state=job.state,
            stage=job.stage,
            stages=job.stages,
            progress=job.progress,
            timings=job.timings,
            params_resolved=job.params_resolved,
            error=None
            if job.error is None
            else ServerErrorInfo.model_validate(job.error.model_dump()),
            artifacts=job.artifacts,
        )

    async def cancel(self, ref: JobRef) -> None:
        await self._request("POST", self._job_path(ref) + "/cancel")

    async def delete_job(self, ref: JobRef) -> None:
        await self._request("DELETE", self._job_path(ref))

    async def download_artifact(self, ref: JobRef, name: str, dest: Path) -> None:
        """Stream one artifact to ``dest``. The caller verifies size and sha256."""
        if not is_safe_relpath(name):
            raise BackendError(
                error_info(
                    ErrorCode.validation_error, "Unsafe artifact name", details={"name": name}
                )
            )
        path = f"{self._job_path(ref)}/artifacts/{quote(name, safe='/')}"
        for attempt in range(self._max_retries + 1):
            try:
                async with self._client.stream("GET", path) as response:
                    if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                        await self._backoff(attempt)
                        continue
                    if response.status_code != 200:
                        await response.aread()
                        raise _error_from_response(response)
                    with dest.open("wb") as f:
                        async for chunk in response.aiter_bytes(CHUNK_BYTES):
                            f.write(chunk)
                    return
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise _unreachable(
                    "the download was interrupted", type=type(exc).__name__
                ) from None
        raise AssertionError("unreachable")  # pragma: no cover

    # --- internals --------------------------------------------------------------------

    @staticmethod
    def _job_path(ref: JobRef) -> str:
        return f"/v1/jobs/{quote(ref.job_id, safe='')}"

    async def _backoff(self, attempt: int) -> None:
        delay = self._retry_base_s * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    log.info(
                        "worker call failed; retrying", extra={"path": path, "attempt": attempt + 1}
                    )
                    await self._backoff(attempt)
                    continue
                raise _unreachable("no connection", type=type(exc).__name__) from None
            if response.is_success:
                return response
            if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                log.info(
                    "worker busy; retrying",
                    extra={"path": path, "status": response.status_code, "attempt": attempt + 1},
                )
                await self._backoff(attempt)
                continue
            raise _error_from_response(response)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _parse[M: (WorkerStatus, Capabilities, Job)](model: type[M], response: httpx.Response) -> M:
        try:
            return model.model_validate_json(response.content)
        except ValidationError:
            raise BackendError(
                error_info(
                    ErrorCode.internal,
                    "The worker sent a response this server does not understand",
                    details={"model": model.__name__},
                ),
                response.status_code,
            ) from None
