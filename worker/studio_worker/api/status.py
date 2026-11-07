"""``GET /healthz`` and ``GET /v1/status``."""

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Depends

from studio_contracts import Health, ModelStatus, QueueStatus, WorkerStatus
from studio_worker.errors import ERROR_RESPONSES
from studio_worker.runtime import WorkerRuntime, get_runtime

health_router = APIRouter()
router = APIRouter(responses=ERROR_RESPONSES)

Runtime = Annotated[WorkerRuntime, Depends(get_runtime)]


@health_router.get("/healthz", response_model=Health, tags=["health"])
async def healthz() -> Health:
    """Process is alive. No auth, and nothing but ``{"ok": true}``."""
    return Health()


@router.get("/status", response_model=WorkerStatus, tags=["status"])
async def get_status(rt: Runtime) -> WorkerStatus:
    loader = rt.executor.loader
    # nvidia-smi is a subprocess: keep it off the event loop.
    gpu = await asyncio.to_thread(rt.gpu.get) if rt.gpu is not None else None
    return WorkerStatus(
        uptime_s=int(time.monotonic() - rt.started),
        idle_seconds=rt.executor.idle_seconds(),
        model=ModelStatus(state=loader.state, error=loader.error),
        gpu=gpu,
        queue=QueueStatus(
            queued=len(rt.executor.queue),
            running=rt.executor.running_job_id,
            max=rt.executor.queue.max_size,
        ),
    )
