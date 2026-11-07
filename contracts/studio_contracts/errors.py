"""Error envelope and stable error codes (PRD section 6, "Errors")."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    """Worker error codes. The server's own client-side codes are ``ClientErrorCode``."""

    unauthorized = "unauthorized"
    not_found = "not_found"
    conflict = "conflict"
    validation_error = "validation_error"
    queue_full = "queue_full"
    not_ready = "not_ready"
    oom = "oom"
    engine_error = "engine_error"
    worker_restarted = "worker_restarted"
    internal = "internal"


class ClientErrorCode(StrEnum):
    """Codes only the local server adds to a generation's error (PRD section 6). They
    are kept apart from ``ErrorCode`` so the worker's OpenAPI never lists them."""

    worker_unreachable = "worker_unreachable"
    incompatible_worker = "incompatible_worker"
    download_failed = "download_failed"
    not_configured = "not_configured"


class ErrorInfo(BaseModel):
    """The object inside every error envelope, and ``job.error`` for failed jobs."""

    code: ErrorCode
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """Every non-2xx response body: ``{"error": {...}}``."""

    error: ErrorInfo
