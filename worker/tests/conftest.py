"""Worker test fixtures: the real app on ``tmp_path`` with a FakeEngine, driven over
``httpx.ASGITransport``. The lifespan is entered explicitly, because ASGITransport does
not run it."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from worker_support import AUTH, TOKEN, StartWorker, Worker

from studio_worker.config import WorkerSettings
from studio_worker.engines.fake import FakeEngine
from studio_worker.main import create_app


@pytest.fixture
def jobs_dir(tmp_path: Path) -> Path:
    return tmp_path / "jobs"


@pytest.fixture
def start_worker(jobs_dir: Path) -> StartWorker:
    @asynccontextmanager
    async def start(
        engine: FakeEngine | None = None,
        *,
        max_queue: int = 4,
        job_ttl_hours: float = 24.0,
        wait_ready: bool = True,
        raise_app_exceptions: bool = True,
    ) -> AsyncIterator[Worker]:
        engine = engine or FakeEngine()
        settings = WorkerSettings(
            api_key=TOKEN,
            engine="fake",
            jobs_dir=jobs_dir,
            max_queue=max_queue,
            job_ttl_hours=job_ttl_hours,
        )
        app = create_app(settings, engine)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://worker", headers=AUTH
            ) as client:
                worker = Worker(app=app, client=client, engine=engine, settings=settings)
                try:
                    if wait_ready:
                        await worker.wait_model("ready")
                    yield worker
                finally:
                    # Never leave the executor or loader thread parked on a gate.
                    for gate in (engine.load_gate, engine.stage_gate):
                        if gate is not None:
                            gate.set()

    return start


@pytest.fixture
async def worker(start_worker: StartWorker) -> AsyncIterator[Worker]:
    async with start_worker() as w:
        yield w
