"""The worker's long-lived objects, shared by the routes through ``app.state``."""

import time
from dataclasses import dataclass, field
from typing import cast

from fastapi import Request

from studio_worker.config import WorkerSettings
from studio_worker.engines.base import Engine
from studio_worker.jobs.executor import JobExecutor
from studio_worker.jobs.store import JobStore


@dataclass
class WorkerRuntime:
    settings: WorkerSettings
    engine: Engine
    store: JobStore
    executor: JobExecutor
    started: float = field(default_factory=time.monotonic)


def get_runtime(request: Request) -> WorkerRuntime:
    return cast(WorkerRuntime, request.app.state.runtime)
