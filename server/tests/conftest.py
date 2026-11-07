"""Server test fixtures: the real server app on ``tmp_path``, wired to the real worker
app with a ``FakeEngine`` over ``httpx.ASGITransport``. Lifespans are entered
explicitly, because ASGITransport does not run them."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from server_support import (
    TOKEN,
    WORKER_URL,
    FakeWorker,
    Server,
    StartServer,
    StartWorker,
    SwitchableTransport,
)

from studio_server.config import ServerSettings
from studio_server.main import create_app as create_server_app
from studio_worker.config import WorkerSettings
from studio_worker.engines.fake import FakeEngine
from studio_worker.main import create_app as create_worker_app


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def server_settings(data_dir: Path) -> ServerSettings:
    return ServerSettings(
        data_dir=data_dir,
        worker_url=WORKER_URL,
        worker_token=TOKEN,
        poll_interval_s=0.01,
        check_interval_s=0.05,
        retry_base_s=0.0,
    )


@pytest.fixture
def start_worker(tmp_path: Path) -> StartWorker:
    @asynccontextmanager
    async def start(
        engine: FakeEngine | None = None, *, max_queue: int = 4, wait_ready: bool = True
    ) -> AsyncIterator[FakeWorker]:
        engine = engine or FakeEngine()
        jobs_dir = tmp_path / "jobs"
        settings = WorkerSettings(
            api_key=TOKEN, engine="fake", jobs_dir=jobs_dir, max_queue=max_queue
        )
        app = create_worker_app(settings, engine)
        async with app.router.lifespan_context(app):
            transport = SwitchableTransport(httpx.ASGITransport(app=app))
            worker = FakeWorker(app=app, engine=engine, transport=transport, jobs_dir=jobs_dir)
            try:
                if wait_ready:
                    await _wait_model_ready(transport.inner)
                yield worker
            finally:
                for gate in (engine.load_gate, engine.stage_gate):
                    if gate is not None:
                        gate.set()

    return start


async def _wait_model_ready(transport: httpx.AsyncBaseTransport) -> None:
    async with httpx.AsyncClient(
        transport=transport, base_url=WORKER_URL, headers={"Authorization": f"Bearer {TOKEN}"}
    ) as client:
        for _ in range(1000):
            status = (await client.get("/v1/status")).json()
            if status["model"]["state"] == "ready":
                return
            await asyncio.sleep(0.01)
    raise AssertionError("fake model never became ready")


@pytest.fixture
def start_server(server_settings: ServerSettings) -> StartServer:
    @asynccontextmanager
    async def start(
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        start_poller: bool = False,
        **overrides: Any,
    ) -> AsyncIterator[Server]:
        settings = replace(server_settings, **overrides)
        app = create_server_app(settings, transport=transport, start_poller=start_poller)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://server"
            ) as client,
        ):
            yield Server(app=app, client=client, runtime=app.state.runtime, settings=settings)

    return start


@pytest.fixture
async def worker(start_worker: StartWorker) -> AsyncIterator[FakeWorker]:
    async with start_worker() as w:
        yield w


@pytest.fixture
async def server(start_server: StartServer, worker: FakeWorker) -> AsyncIterator[Server]:
    """A server wired to ``worker``, with the poller off (tests call ``tick()``)."""
    async with start_server(worker.transport) as s:
        yield s
