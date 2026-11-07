"""The one interface between the server and a GPU backend (PRD section 7).

``MusicBackend`` is copied from the PRD; the only change is ``dict`` written as
``dict[str, Any]``, because mypy strict requires type arguments. ``JobRef``, ``JobView``
and ``BackendError`` are not defined by the PRD; their shapes are in docs/decisions.md.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from studio_contracts import Artifact, Capabilities, JobState, ServerErrorInfo, WorkerStatus


@dataclass(frozen=True)
class JobRef:
    """Identifies a job on the backend that runs it."""

    job_id: str


class JobView(BaseModel):
    """What the poller needs from a backend job, independent of the backend kind."""

    state: JobState
    stage: str | None
    stages: list[str]
    progress: float | None
    timings: dict[str, float]
    params_resolved: dict[str, Any]
    error: ServerErrorInfo | None
    artifacts: list[Artifact]


class BackendError(Exception):
    """A backend call failed. ``info`` is the error to show (the worker's own envelope
    when it sent one); ``http_status`` is the worker's status, or ``None`` when no
    response arrived."""

    def __init__(self, info: ServerErrorInfo, http_status: int | None = None) -> None:
        super().__init__(info.message)
        self.info = info
        self.http_status = http_status

    @property
    def code(self) -> str:
        return self.info.code.value


class MusicBackend(Protocol):
    async def status(self) -> WorkerStatus: ...
    async def capabilities(self) -> Capabilities: ...
    async def submit(
        self, operation: str, params: dict[str, Any], client_request_id: str
    ) -> JobRef: ...
    async def get_job(self, ref: JobRef) -> JobView: ...
    async def cancel(self, ref: JobRef) -> None: ...
    async def delete_job(self, ref: JobRef) -> None: ...
    async def download_artifact(self, ref: JobRef, name: str, dest: Path) -> None: ...


@runtime_checkable
class Closeable(Protocol):
    """Backends that hold connections implement this; it is not part of
    ``MusicBackend``, which stays exactly as the PRD writes it."""

    async def aclose(self) -> None: ...
