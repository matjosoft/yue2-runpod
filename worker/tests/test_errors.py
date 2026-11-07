"""W-8: every failure path returns the documented envelope, and never the token."""

import threading
from typing import Any

import httpx
import pytest
from worker_support import TOKEN, StartWorker, Worker

from studio_worker.engines.fake import FakeEngine

ERROR_KEYS = {"code", "message", "retryable", "details"}


def assert_envelope(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ERROR_KEYS
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["retryable"], bool)
    assert TOKEN not in response.text
    error: dict[str, Any] = body["error"]
    return error


async def test_401(worker: Worker) -> None:
    response = await worker.client.get("/v1/status", headers={"Authorization": "Bearer no"})
    assert_envelope(response, 401, "unauthorized")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/nope"),
        ("GET", "/v1/nope"),
        ("GET", "/v1/jobs/job_x"),
        ("PUT", "/v1/status"),
        ("POST", "/healthz"),
    ],
)
async def test_404_and_405(worker: Worker, method: str, path: str) -> None:
    response = await worker.client.request(method, path)
    expected = 405 if method in ("PUT", "POST") else 404
    assert_envelope(response, expected, "not_found")


async def test_409_422_429_503(start_worker: StartWorker) -> None:
    hold = threading.Event()
    async with start_worker(FakeEngine(stage_gate=hold), max_queue=1) as w:
        assert_envelope(await w.submit({"style": ""}), 422, "validation_error")
        running = (await w.submit()).json()
        await w.wait_for(running["id"], "running")
        assert_envelope(await w.client.delete(f"/v1/jobs/{running['id']}"), 409, "conflict")
        await w.submit()
        assert_envelope(await w.submit(), 429, "queue_full")
        hold.set()
    loading = threading.Event()
    async with start_worker(FakeEngine(load_gate=loading), wait_ready=False) as w:
        assert_envelope(await w.submit(), 503, "not_ready")


async def test_500_internal(start_worker: StartWorker, monkeypatch: pytest.MonkeyPatch) -> None:
    async with start_worker(raise_app_exceptions=False) as w:

        def boom() -> None:
            raise RuntimeError(f"bug that mentions {TOKEN}")

        monkeypatch.setattr(w.engine, "info", boom)
        error = assert_envelope(await w.client.get("/v1/capabilities"), 500, "internal")
        assert error["message"] == "Internal worker error"
        assert error["details"] == {}


async def test_request_id_is_returned(worker: Worker) -> None:
    response = await worker.client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
    generated = await worker.client.get("/healthz")
    assert len(generated.headers["X-Request-ID"]) == 32
