"""Local server API contract, used by the web UI (PRD section 6, "Local server API").

Request models reject unknown fields; response models ignore them (docs/decisions.md).
Shapes the PRD leaves open are recorded in docs/decisions.md under M2.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from studio_contracts.errors import ClientErrorCode, ErrorCode
from studio_contracts.jobs import ArtifactRole
from studio_contracts.worker import ModelState

TITLE_MAX_CHARS = 200
WORKER_URL_MAX_CHARS = 2048
TOKEN_MAX_CHARS = 512


class ServerErrorInfo(BaseModel):
    """Like ``ErrorInfo``, but the code may also be one of the server's client codes."""

    code: ErrorCode | ClientErrorCode
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ServerErrorEnvelope(BaseModel):
    """Every non-2xx response of the local server: ``{"error": {...}}``."""

    error: ServerErrorInfo


class GenerationStatus(StrEnum):
    """``queued`` and ``running`` mirror the worker; ``downloading`` exists only here."""

    queued = "queued"
    running = "running"
    downloading = "downloading"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            GenerationStatus.succeeded,
            GenerationStatus.failed,
            GenerationStatus.canceled,
        )


class GenerationArtifact(BaseModel):
    """An artifact stored locally. ``url`` serves it, with Range support."""

    name: str
    role: ArtifactRole
    media_type: str
    size_bytes: int
    url: str


class Generation(BaseModel):
    id: str
    title: str
    operation: str
    request: dict[str, Any]
    """The params sent to the worker, including the seed the server filled in."""
    status: GenerationStatus
    stage: str | None
    stages: list[str]
    error: ServerErrorInfo | None
    worker_job_id: str | None
    timings: dict[str, float]
    artifacts: list[GenerationArtifact]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class GenerationCreate(BaseModel):
    """Body of ``POST /api/generations``. ``request`` has the same shape as
    ``Generation.request``, so a stored request can be sent again as is."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=TITLE_MAX_CHARS)
    """Blank or absent means ``Untitled <local date and time>``."""
    operation: str = Field(min_length=1, max_length=128)
    request: dict[str, Any]


class GenerationList(BaseModel):
    """``GET /api/generations``: newest first. Pass ``next_cursor`` back as ``cursor``."""

    items: list[Generation]
    next_cursor: str | None


class SettingsView(BaseModel):
    """``GET /api/settings``. The token is write-only: only whether one is set."""

    worker_url: str | None
    token_set: bool


class SettingsUpdate(BaseModel):
    """``PUT /api/settings``. A field that is omitted keeps its saved value; ``null``
    clears it (the environment default then applies); a value replaces it."""

    model_config = ConfigDict(extra="forbid")

    worker_url: str | None = Field(
        default=None, max_length=WORKER_URL_MAX_CHARS, pattern=r"^https?://\S+$"
    )
    token: str | None = Field(default=None, min_length=1, max_length=TOKEN_MAX_CHARS, repr=False)


class WorkerCheckState(StrEnum):
    """S-5's five states, plus ``incompatible`` (API major differs) and ``error`` (the
    model failed to load). Owner-approved in M2 (docs/decisions.md)."""

    not_configured = "not_configured"
    unreachable = "unreachable"
    unauthorized = "unauthorized"
    warming_up = "warming_up"
    ready = "ready"
    incompatible = "incompatible"
    error = "error"


class WorkerSummary(BaseModel):
    api_version: str
    worker_version: str
    model_state: ModelState


class WorkerCheck(BaseModel):
    """Result of ``POST /api/worker/check``, also embedded in ``/api/health``."""

    state: WorkerCheckState
    error: ServerErrorInfo | None
    worker: WorkerSummary | None


class ServerHealth(BaseModel):
    """``GET /api/health``: the server is up, plus the last worker check."""

    ok: Literal[True] = True
    worker: WorkerCheck
