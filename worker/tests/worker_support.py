"""Helpers shared by the worker tests (fixtures live in conftest.py).

Tests synchronise with gates (``threading.Event``) and bounded polling, never fixed
sleeps."""

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI

from studio_worker.config import WorkerSettings
from studio_worker.engines.fake import FakeEngine

TOKEN = "test-token-0123456789abcdef0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TIMEOUT_S = 10.0


def song_params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"style": "jazz-funk, warm vocal", "lyrics": "[Verse]\nla la"}
    params.update(overrides)
    return params


@dataclass
class Worker:
    app: FastAPI
    client: httpx.AsyncClient
    engine: FakeEngine
    settings: WorkerSettings

    async def submit(
        self,
        params: dict[str, Any] | None = None,
        *,
        operation: str = "song.generate",
        client_request_id: str | None = None,
    ) -> httpx.Response:
        return await self.client.post(
            "/v1/jobs",
            json={
                "client_request_id": client_request_id or str(uuid.uuid4()),
                "operation": operation,
                "params": song_params() if params is None else params,
            },
        )

    async def job(self, job_id: str) -> dict[str, Any]:
        response = await self.client.get(f"/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def wait_for(self, job_id: str, *states: str, stage: str | None = None) -> dict[str, Any]:
        """Poll until the job is in one of ``states`` (and at ``stage``, if given)."""
        deadline = asyncio.get_running_loop().time() + TIMEOUT_S
        while True:
            job = await self.job(job_id)
            if job["state"] in states and (stage is None or job["stage"] == stage):
                return job
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"job stuck in {job['state']}/{job['stage']}: {job}")
            await asyncio.sleep(0.01)

    async def status(self) -> dict[str, Any]:
        response = await self.client.get("/v1/status")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def wait_model(self, state: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + TIMEOUT_S
        while (status := await self.status())["model"]["state"] != state:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"model stuck in {status['model']}")
            await asyncio.sleep(0.01)
        return status


StartWorker = Callable[..., AbstractAsyncContextManager["Worker"]]
