"""Worker check, health and cached capabilities (S-5, S-6)."""

import threading
from typing import Any

import httpx
from server_support import TOKEN, WORKER_URL, FakeWorker, Server, StartServer, StartWorker

from studio_worker.engines.fake import FakeEngine


async def check(server: Server) -> dict[str, Any]:
    response = await server.request("POST", "/api/worker/check")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_ready(start_server: StartServer, worker: FakeWorker) -> None:
    async with start_server(worker.transport) as server:
        body = await check(server)
        assert body["state"] == "ready"
        assert body["error"] is None
        assert body["worker"] == {
            "api_version": "1.0",
            "worker_version": "0.1.0",
            "model_state": "ready",
        }
        health = (await server.request("GET", "/api/health")).json()
        assert health == {"ok": True, "worker": body}


async def test_not_configured(start_server: StartServer) -> None:
    async with start_server(worker_url=None, worker_token=None) as server:
        body = await check(server)
    assert body["state"] == "not_configured"
    assert body["error"]["code"] == "not_configured"


async def test_unreachable(start_server: StartServer, worker: FakeWorker) -> None:
    worker.transport.down = True
    async with start_server(worker.transport) as server:
        body = await check(server)
    assert body["state"] == "unreachable"
    assert body["error"]["code"] == "worker_unreachable"


async def test_unauthorized(start_server: StartServer, worker: FakeWorker) -> None:
    async with start_server(worker.transport, worker_token="wrong-" + "x" * 40) as server:
        body = await check(server)
    assert body["state"] == "unauthorized"


async def test_warming_up_then_ready(start_server: StartServer, start_worker: StartWorker) -> None:
    """AT-12 at server level: a delayed fake model load shows ``warming_up``."""
    gate = threading.Event()
    async with (
        start_worker(FakeEngine(load_gate=gate), wait_ready=False) as worker,
        start_server(worker.transport) as server,
    ):
        body = await check(server)
        assert body["state"] == "warming_up"
        assert body["worker"]["model_state"] == "loading"
        gate.set()
        for _ in range(500):
            if (await check(server))["state"] == "ready":
                break
        assert (await check(server))["state"] == "ready"


async def test_model_load_error(start_server: StartServer, start_worker: StartWorker) -> None:
    engine = FakeEngine(load_error=RuntimeError("no BF16"))
    async with (
        start_worker(engine, wait_ready=False) as worker,
        start_server(worker.transport) as server,
    ):
        for _ in range(500):
            body = await check(server)
            if body["state"] != "warming_up":
                break
    assert body["state"] == "error"
    assert body["error"]["code"] == "not_ready"


async def test_incompatible(start_server: StartServer, worker: FakeWorker) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        response = await worker.transport.handle_async_request(request)
        if request.url.path == "/v1/capabilities":
            body = (await response.aread()).replace(b'"api_version":"1.0"', b'"api_version":"2.0"')
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return response

    class Wrapper(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return await handler(request)

    async with start_server(Wrapper()) as server:
        body = await check(server)
    assert body["state"] == "incompatible"
    assert body["error"]["code"] == "incompatible_worker"


async def test_capabilities_are_cached(start_server: StartServer, worker: FakeWorker) -> None:
    """S-6: capabilities stay available after the worker goes away."""
    async with start_server(worker.transport) as server:
        assert (await check(server))["state"] == "ready"
        worker.transport.down = True
        response = await server.request("GET", "/api/capabilities")
        assert response.status_code == 200
        assert [op["name"] for op in response.json()["operations"]] == ["song.generate"]
        assert (await check(server))["state"] == "unreachable"
        assert (await server.request("GET", "/api/capabilities")).status_code == 200


async def test_capabilities_unavailable(start_server: StartServer, worker: FakeWorker) -> None:
    worker.transport.down = True
    async with start_server(worker.transport) as server:
        response = await server.request("GET", "/api/capabilities")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "worker_unreachable"
    async with start_server(worker_url=None, worker_token=None) as server:
        response = await server.request("GET", "/api/capabilities")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_configured"


async def test_settings_change_drops_the_cache(
    start_server: StartServer, worker: FakeWorker
) -> None:
    async with start_server(worker.transport, worker_url=None, worker_token=None) as server:
        await server.request(
            "PUT", "/api/settings", json={"worker_url": WORKER_URL, "token": TOKEN}
        )
        assert (await check(server))["state"] == "ready"
        await server.request("PUT", "/api/settings", json={"token": None})
        response = await server.request("GET", "/api/capabilities")
        assert response.json()["error"]["code"] == "not_configured"


async def test_health_uses_the_cached_check(start_server: StartServer, worker: FakeWorker) -> None:
    async with start_server(worker.transport) as server:
        before = len(worker.transport.requests)
        health = (await server.request("GET", "/api/health")).json()
        assert health["ok"] is True
        assert health["worker"]["state"] == "unreachable"  # not checked yet
        assert len(worker.transport.requests) == before  # health never calls the worker
