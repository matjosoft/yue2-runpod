"""Error objects for ``job.error`` and the HTTP envelope. FastAPI-free, so the jobs
layer (and a later serverless handler) can build them."""

from collections.abc import Mapping
from typing import Any

from studio_contracts import ErrorCode, ErrorInfo

RETRYABLE: dict[ErrorCode, bool] = {
    ErrorCode.queue_full: True,
    ErrorCode.not_ready: True,
    ErrorCode.worker_restarted: True,
    ErrorCode.oom: True,
}
"""Default ``retryable`` per code. ``oom``, ``engine_error`` and ``internal`` are "maybe"
in the PRD; the defaults chosen are in docs/decisions.md. Codes not listed are False."""


def error_info(
    code: ErrorCode,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
    retryable: bool | None = None,
) -> ErrorInfo:
    return ErrorInfo(
        code=code,
        message=message,
        retryable=RETRYABLE.get(code, False) if retryable is None else retryable,
        details=dict(details or {}),
    )
