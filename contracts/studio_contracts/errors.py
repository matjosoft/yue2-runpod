"""Error envelope and stable error codes (PRD section 6, "Errors")."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    """Worker error codes. The server's client-side codes are added in M2."""

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


class ErrorInfo(BaseModel):
    """The object inside every error envelope, and ``job.error`` for failed jobs."""

    code: ErrorCode
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """Every non-2xx response body: ``{"error": {...}}``."""

    error: ErrorInfo
