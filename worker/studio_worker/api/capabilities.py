"""``GET /v1/capabilities``, built from the operation registry (W-7)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from studio_contracts import API_VERSION, Capabilities, OperationInfo
from studio_worker import __version__
from studio_worker.errors import ERROR_RESPONSES
from studio_worker.operations.registry import all_operations
from studio_worker.runtime import WorkerRuntime, get_runtime

router = APIRouter(responses=ERROR_RESPONSES)


@router.get("/capabilities", response_model=Capabilities, tags=["status"])
async def get_capabilities(
    rt: Annotated[WorkerRuntime, Depends(get_runtime)],
) -> Capabilities:
    return Capabilities(
        api_version=API_VERSION,
        worker_version=__version__,
        engine=rt.engine.info(),
        operations=[
            OperationInfo(
                name=op.name,
                stages=list(op.stages(rt.engine)),
                params_schema=op.params_model.model_json_schema(),
            )
            for op in all_operations()
        ],
    )
