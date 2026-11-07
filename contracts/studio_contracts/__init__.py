"""Shared contracts for the Studio worker and server.

Pydantic models here are the single source of truth for both HTTP APIs (PRD section 6).
The OpenAPI documents in ``contracts/openapi/`` are generated from the apps that use
them; run ``make contracts`` after any change.
"""

from studio_contracts.errors import ClientErrorCode, ErrorCode, ErrorEnvelope, ErrorInfo
from studio_contracts.jobs import Artifact, ArtifactRole, Job, JobList, JobState, JobSubmit
from studio_contracts.server import (
    Generation,
    GenerationArtifact,
    GenerationCreate,
    GenerationList,
    GenerationStatus,
    ServerErrorEnvelope,
    ServerErrorInfo,
    ServerHealth,
    SettingsUpdate,
    SettingsView,
    WorkerCheck,
    WorkerCheckState,
    WorkerSummary,
)
from studio_contracts.song import SongParams
from studio_contracts.worker import (
    Capabilities,
    EngineInfo,
    GpuInfo,
    Health,
    ModelState,
    ModelStatus,
    OperationInfo,
    QueueStatus,
    WorkerStatus,
)

API_VERSION = "1.0"
"""Worker API version, ``major.minor``. A server refuses a worker whose major differs."""

__all__ = [
    "API_VERSION",
    "Artifact",
    "ArtifactRole",
    "Capabilities",
    "ClientErrorCode",
    "EngineInfo",
    "ErrorCode",
    "ErrorEnvelope",
    "ErrorInfo",
    "Generation",
    "GenerationArtifact",
    "GenerationCreate",
    "GenerationList",
    "GenerationStatus",
    "GpuInfo",
    "Health",
    "Job",
    "JobList",
    "JobState",
    "JobSubmit",
    "ModelState",
    "ModelStatus",
    "OperationInfo",
    "QueueStatus",
    "ServerErrorEnvelope",
    "ServerErrorInfo",
    "ServerHealth",
    "SettingsUpdate",
    "SettingsView",
    "SongParams",
    "WorkerCheck",
    "WorkerCheckState",
    "WorkerStatus",
    "WorkerSummary",
]
