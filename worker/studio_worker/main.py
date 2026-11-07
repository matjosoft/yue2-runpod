"""Worker application factory.

``uvicorn studio_worker.main:app`` (PRD section 8) reads settings from the environment;
tests and the OpenAPI exporter call ``create_app`` with explicit settings and engine.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache

from fastapi import FastAPI

from studio_worker import __version__
from studio_worker import operations as _operations  # registers built-in operations
from studio_worker.api import build_routers
from studio_worker.config import WorkerSettings
from studio_worker.engines.base import Engine
from studio_worker.engines.factory import create_engine
from studio_worker.errors import install_error_handlers
from studio_worker.gpu import GpuProbe
from studio_worker.jobs.executor import JobExecutor
from studio_worker.jobs.store import JobStore
from studio_worker.logs import configure_logging, install_request_logging
from studio_worker.operations.registry import get_operation
from studio_worker.runtime import WorkerRuntime

assert _operations
log = logging.getLogger("studio_worker")

SWEEP_INTERVAL_S = 60.0
"""How often expired jobs are deleted (W-11). An expired job can stay visible for up to
this long after its TTL (docs/decisions.md)."""


async def _sweep_forever(runtime: WorkerRuntime) -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_S)
        try:
            await asyncio.to_thread(runtime.sweep_expired)
        except Exception:
            log.exception("expired-job sweep failed")


def create_app(settings: WorkerSettings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or WorkerSettings.from_env()
    engine = engine or create_engine(settings)
    store = JobStore(settings.jobs_dir)
    executor = JobExecutor(store, engine, get_operation, max_queue=settings.max_queue)
    gpu = GpuProbe() if settings.engine == "yue2" else None
    runtime = WorkerRuntime(
        settings=settings, engine=engine, store=store, executor=executor, gpu=gpu
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store.load()
        runtime.sweep_expired()
        executor.start()
        sweeper = asyncio.create_task(_sweep_forever(runtime), name="job-ttl-sweep")
        log.info("worker started", extra={"engine": engine.kind, "version": __version__})
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            executor.stop()
            engine.close()

    app = FastAPI(title="Studio worker", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    install_error_handlers(app)
    install_request_logging(app)
    for router in build_routers():
        app.include_router(router)
    return app


@cache
def _app_from_env() -> FastAPI:
    settings = WorkerSettings.from_env()
    configure_logging(settings.log_level)
    return create_app(settings)


def __getattr__(name: str) -> FastAPI:
    # Lazy, so that importing this module (tests, the OpenAPI exporter) needs no env.
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
