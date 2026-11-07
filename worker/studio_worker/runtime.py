"""The worker's long-lived objects, shared by the routes through ``app.state``."""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast

from fastapi import Request

from studio_worker.config import WorkerSettings
from studio_worker.engines.base import Engine
from studio_worker.gpu import GpuProbe
from studio_worker.jobs.executor import JobExecutor
from studio_worker.jobs.store import JobStore, utcnow


@dataclass
class WorkerRuntime:
    settings: WorkerSettings
    engine: Engine
    store: JobStore
    executor: JobExecutor
    gpu: GpuProbe | None = None
    """``None`` (no probing) for the fake engine."""
    started: float = field(default_factory=time.monotonic)

    def sweep_expired(self, now: datetime | None = None) -> list[str]:
        """Delete finished jobs older than ``JOB_TTL_HOURS`` (W-11)."""
        ttl = timedelta(hours=self.settings.job_ttl_hours)
        return self.store.sweep_expired(now or utcnow(), ttl)


def get_runtime(request: Request) -> WorkerRuntime:
    return cast(WorkerRuntime, request.app.state.runtime)
