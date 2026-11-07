"""Helpers shared by the server tests (fixtures live in conftest.py).

The server talks to the real worker app with a ``FakeEngine``, in-process over
``httpx.ASGITransport``. ``SwitchableTransport`` sits in between so tests can take the
worker "offline", corrupt downloads or count parallel downloads. Tests synchronise with
gates and bounded polling, never fixed sleeps.
"""

import asyncio
import threading
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from studio_server.config import ServerSettings
from studio_server.core.runtime import ServerRuntime
from studio_worker.engines.fake import FakeEngine

TOKEN = "server-test-token-0123456789abcdef0123456789"
WORKER_URL = "http://worker"
TIMEOUT_S = 10.0
SONG_STAGES = ["plan", "semantic", "synthesis", "decode", "save"]


def song_request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {"style": "jazz-funk, warm vocal", "lyrics": "[Verse]\nla la"}
    request.update(overrides)
    return request


class StepGate(threading.Event):
    """A FakeEngine gate that lets one stage through per ``step()``; ``set()`` opens it
    for good."""

    def __init__(self) -> None:
        super().__init__()
        self._steps = threading.Semaphore(0)

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self._steps.release()

    def wait(self, timeout: float | None = None) -> bool:
        waited = 0.0
        while timeout is None or waited < timeout:
            if self.is_set() or self._steps.acquire(timeout=0.01):
                return True
            waited += 0.01
        return self.is_set()


class SwitchableTransport(httpx.AsyncBaseTransport):
    """Wraps the worker app's transport. ``down`` makes every call a connect error;
    ``corrupt_downloads`` flips one byte in that many artifact downloads; artifact
    requests in flight are counted."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.down = False
        self.corrupt_downloads = 0
        self.download_delay_s = 0.0
        self.in_flight = 0
        self.max_in_flight = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("worker is down (test)", request=request)
        if "/artifacts/" not in request.url.path:
            return await self.inner.handle_async_request(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.download_delay_s:
                await asyncio.sleep(self.download_delay_s)
            response = await self.inner.handle_async_request(request)
            if self.corrupt_downloads > 0 and response.status_code == 200:
                self.corrupt_downloads -= 1
                body = bytearray(await response.aread())
                body[0] ^= 0xFF
                return httpx.Response(200, headers=response.headers, content=bytes(body))
            return response
        finally:
            self.in_flight -= 1


@dataclass
class FakeWorker:
    app: FastAPI
    engine: FakeEngine
    transport: SwitchableTransport
    jobs_dir: Path


@dataclass
class Server:
    app: FastAPI
    client: httpx.AsyncClient
    runtime: ServerRuntime
    settings: ServerSettings
    seen_bodies: list[str] = field(default_factory=list)
    """Every response body this client received (AT-4 checks the token is in none)."""

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = await self.client.request(method, url, **kwargs)
        self.seen_bodies.append(response.text)
        return response

    async def create(self, request: dict[str, Any] | None = None, **body: Any) -> httpx.Response:
        payload: dict[str, Any] = {
            "operation": "song.generate",
            "request": request or song_request(),
        }
        payload.update(body)
        return await self.request("POST", "/api/generations", json=payload)

    async def generation(self, gen_id: str) -> dict[str, Any]:
        response = await self.request("GET", f"/api/generations/{gen_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def wait_for(
        self, gen_id: str, *statuses: str, stage: str | None = None, tick: bool = False
    ) -> dict[str, Any]:
        """Poll until the generation is in one of ``statuses`` (and at ``stage``). With
        ``tick``, drive the poller by hand instead of relying on its loop."""
        deadline = asyncio.get_running_loop().time() + TIMEOUT_S
        while True:
            if tick:
                await self.runtime.poller.tick()
            gen = await self.generation(gen_id)
            if gen["status"] in statuses and (stage is None or gen["stage"] == stage):
                return gen
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"generation stuck in {gen['status']}/{gen['stage']}: {gen}")
            await asyncio.sleep(0.01)


StartWorker = Callable[..., AbstractAsyncContextManager[FakeWorker]]
StartServer = Callable[..., AbstractAsyncContextManager[Server]]
