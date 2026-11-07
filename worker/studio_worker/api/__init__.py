"""Transport: FastAPI routes. Knows nothing about YuE2."""

from fastapi import APIRouter, Depends

from studio_worker.api import artifacts, capabilities, jobs, status
from studio_worker.auth import require_token


def build_routers() -> list[APIRouter]:
    v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])
    v1.include_router(status.router)
    v1.include_router(capabilities.router)
    v1.include_router(jobs.router)
    v1.include_router(artifacts.router)
    return [status.health_router, v1]
