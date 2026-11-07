"""Worker health, status and capabilities (PRD section 6)."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class Health(BaseModel):
    """``GET /healthz``: ``{"ok": true}`` and nothing else."""

    ok: Literal[True] = True


class ModelState(StrEnum):
    loading = "loading"
    ready = "ready"
    error = "error"


class ModelStatus(BaseModel):
    state: ModelState
    error: str | None


class GpuInfo(BaseModel):
    name: str
    vram_total_mb: int
    vram_used_mb: int


class QueueStatus(BaseModel):
    queued: int
    running: str | None
    """Id of the running job, if any."""
    max: int


class WorkerStatus(BaseModel):
    uptime_s: int
    idle_seconds: int
    model: ModelStatus
    gpu: GpuInfo | None
    queue: QueueStatus


class EngineInfo(BaseModel):
    kind: str
    package_version: str
    models: dict[str, str]


class OperationInfo(BaseModel):
    name: str
    stages: list[str]
    params_schema: dict[str, Any]


class Capabilities(BaseModel):
    api_version: str
    worker_version: str
    engine: EngineInfo
    operations: list[OperationInfo]
