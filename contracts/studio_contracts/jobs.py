"""Worker job contract (PRD section 6, "Job object" and "Submit request")."""

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from studio_contracts.errors import ErrorInfo


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobState.succeeded, JobState.failed, JobState.canceled)


class ArtifactRole(StrEnum):
    primary_audio = "primary_audio"
    score = "score"
    plan = "plan"
    result = "result"
    intermediate = "intermediate"
    other = "other"


class Artifact(BaseModel):
    """One entry of a job's artifact manifest. ``name`` is a relative POSIX path."""

    name: str = Field(min_length=1)
    role: ArtifactRole
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class JobSubmit(BaseModel):
    """Body of ``POST /v1/jobs``. ``params`` is validated against the operation's model."""

    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    params: dict[str, Any]


class Job(BaseModel):
    id: str
    client_request_id: str
    operation: str
    state: JobState
    stage: str | None
    stages: list[str]
    progress: float | None = Field(ge=0, le=1)
    params: dict[str, Any]
    params_resolved: dict[str, Any]
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    timings: dict[str, float]
    error: ErrorInfo | None
    artifacts: list[Artifact]


class JobList(BaseModel):
    """Body of ``GET /v1/jobs``: recent jobs, newest first, at most 50."""

    jobs: list[Job]
