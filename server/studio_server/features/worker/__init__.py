"""``worker`` feature: health, the connection check and cached capabilities (S-5, S-6)."""

from fastapi import APIRouter

from studio_contracts import Capabilities, ServerHealth, WorkerCheck
from studio_server.core.backend import BackendError
from studio_server.core.errors import ERROR_RESPONSES, ApiError
from studio_server.core.runtime import Runtime
from studio_server.features.base import Feature

router = APIRouter(prefix="/api", tags=["worker"], responses=ERROR_RESPONSES)


@router.get("/health", response_model=ServerHealth)
async def health(rt: Runtime) -> ServerHealth:
    """The server is up. ``worker`` is the last check result, refreshed by the poller
    every few seconds, so this call never waits on the worker."""
    return ServerHealth(worker=rt.link.last_check)


@router.post("/worker/check", response_model=WorkerCheck)
async def check_worker(rt: Runtime) -> WorkerCheck:
    """Test the saved settings now. Always 200; the outcome is in ``state``."""
    return await rt.link.check()


@router.get("/capabilities", response_model=Capabilities)
async def capabilities(rt: Runtime) -> Capabilities:
    """The worker's capabilities, cached since the last successful check."""
    try:
        return await rt.link.capabilities()
    except BackendError as exc:
        raise ApiError.from_info(exc.info, 409 if exc.code == "not_configured" else 502) from None


feature = Feature(name="worker", router=router)
