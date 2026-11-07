"""``http_worker`` backend and the factory (PRD section 6 and 7)."""

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from server_support import TOKEN, WORKER_URL, FakeWorker, song_request

from studio_contracts import API_VERSION
from studio_server.core.backend import (
    BackendConfig,
    BackendError,
    JobRef,
    UnknownBackendError,
    create_backend,
)
from studio_server.core.backend.http_worker import HttpWorkerBackend


def backend_for(transport: httpx.AsyncBaseTransport, token: str = TOKEN) -> HttpWorkerBackend:
    return HttpWorkerBackend(WORKER_URL, token, transport=transport, retry_base_s=0)


async def wait_job(backend: HttpWorkerBackend, ref: JobRef, state: str) -> Any:
    import asyncio

    for _ in range(1000):
        view = await backend.get_job(ref)
        if view.state == state:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError(f"job never reached {state}")


async def test_against_the_fake_worker(worker: FakeWorker, tmp_path: Path) -> None:
    backend = backend_for(worker.transport)
    try:
        assert (await backend.status()).model.state == "ready"
        caps = await backend.capabilities()
        assert caps.api_version == API_VERSION
        assert "song.generate" in [op.name for op in caps.operations]

        ref = await backend.submit("song.generate", song_request(seed=7), "crid-1")
        assert await backend.submit("song.generate", song_request(seed=7), "crid-1") == ref

        view = await wait_job(backend, ref, "succeeded")
        assert view.stages == ["plan", "semantic", "synthesis", "decode", "save"]
        assert view.params_resolved == {"seed": 7}
        flac = next(a for a in view.artifacts if a.name == "audio.flac")

        dest = tmp_path / "audio.flac"
        await backend.download_artifact(ref, "audio.flac", dest)
        assert dest.stat().st_size == flac.size_bytes
        assert hashlib.sha256(dest.read_bytes()).hexdigest() == flac.sha256

        await backend.delete_job(ref)
        with pytest.raises(BackendError) as err:
            await backend.get_job(ref)
        assert (err.value.code, err.value.http_status) == ("not_found", 404)
    finally:
        await backend.aclose()


async def test_wrong_token_is_unauthorized_and_not_retried(worker: FakeWorker) -> None:
    backend = backend_for(worker.transport, token="wrong-" + "x" * 40)
    with pytest.raises(BackendError) as err:
        await backend.status()
    assert (err.value.code, err.value.http_status) == ("unauthorized", 401)
    assert len(worker.transport.requests) == 1
    await backend.aclose()


async def test_worker_validation_error_is_relayed(worker: FakeWorker) -> None:
    backend = backend_for(worker.transport)
    with pytest.raises(BackendError) as err:
        await backend.submit("song.generate", song_request(style="x" * 1001), "crid")
    assert (err.value.code, err.value.http_status) == ("validation_error", 422)
    assert len(worker.transport.requests) == 1  # a 4xx is never retried
    await backend.aclose()


def counting(handler: Any) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        result: httpx.Response = handler(request)
        return result

    return httpx.MockTransport(wrapped), seen


def envelope(code: str, status: int) -> httpx.Response:
    body = {"error": {"code": code, "message": code, "retryable": True, "details": {}}}
    return httpx.Response(status, json=body)


@pytest.mark.parametrize(("code", "status"), [("queue_full", 429), ("not_ready", 503)])
async def test_transient_statuses_are_retried_three_times(code: str, status: int) -> None:
    transport, seen = counting(lambda r: envelope(code, status))
    backend = backend_for(transport)
    with pytest.raises(BackendError) as err:
        await backend.submit("song.generate", song_request(), "crid")
    assert (err.value.code, err.value.http_status) == (code, status)
    assert len(seen) == 4  # one try plus 3 retries
    await backend.aclose()


async def test_retry_then_success() -> None:
    answers = [httpx.Response(502, text="<html>bad gateway</html>")]

    def handler(request: httpx.Request) -> httpx.Response:
        if answers:
            return answers.pop()
        return httpx.Response(200, json=json.loads(_status_json()))

    transport, seen = counting(handler)
    backend = backend_for(transport)
    assert (await backend.status()).model.state == "ready"
    assert len(seen) == 2
    await backend.aclose()


def _status_json() -> str:
    return json.dumps(
        {
            "uptime_s": 1,
            "idle_seconds": 1,
            "model": {"state": "ready", "error": None},
            "gpu": None,
            "queue": {"queued": 0, "running": None, "max": 4},
        }
    )


async def test_network_errors_become_worker_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    transport, seen = counting(handler)
    backend = backend_for(transport)
    with pytest.raises(BackendError) as err:
        await backend.status()
    assert err.value.code == "worker_unreachable"
    assert err.value.http_status is None
    assert len(seen) == 4
    assert TOKEN not in json.dumps(err.value.info.model_dump(mode="json"))
    await backend.aclose()


async def test_proxy_html_502_becomes_worker_unreachable() -> None:
    transport, _ = counting(lambda r: httpx.Response(502, text="<html>Bad gateway</html>"))
    backend = backend_for(transport)
    with pytest.raises(BackendError) as err:
        await backend.status()
    assert (err.value.code, err.value.http_status) == ("worker_unreachable", 502)
    await backend.aclose()


async def test_other_major_version_is_incompatible(worker: FakeWorker) -> None:
    caps = await backend_for(worker.transport).capabilities()
    other = caps.model_copy(update={"api_version": "2.0"}).model_dump_json()
    transport, _ = counting(lambda r: httpx.Response(200, content=other))
    backend = backend_for(transport)
    with pytest.raises(BackendError) as err:
        await backend.capabilities()
    assert err.value.code == "incompatible_worker"
    assert err.value.info.details["worker_api_version"] == "2.0"
    await backend.aclose()


async def test_minor_version_difference_is_fine(worker: FakeWorker) -> None:
    caps = await backend_for(worker.transport).capabilities()
    newer = caps.model_copy(update={"api_version": "1.7"}).model_dump_json()
    transport, _ = counting(lambda r: httpx.Response(200, content=newer))
    assert (await backend_for(transport).capabilities()).api_version == "1.7"


async def test_token_is_sent_as_bearer_only() -> None:
    transport, seen = counting(lambda r: httpx.Response(200, content=_status_json()))
    await backend_for(transport).status()
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(seen[0].url)


async def test_unsafe_artifact_names_are_refused_before_any_request(tmp_path: Path) -> None:
    transport, seen = counting(lambda r: httpx.Response(200))
    backend = backend_for(transport)
    for name in ("../job.json", "/etc/passwd", "a\\b", ""):
        with pytest.raises(BackendError):
            await backend.download_artifact(JobRef("job_1"), name, tmp_path / "x")
    assert seen == []


def test_factory_maps_kind_to_backend() -> None:
    backend = create_backend(BackendConfig(kind="http_worker", url=WORKER_URL, token=TOKEN))
    assert isinstance(backend, HttpWorkerBackend)
    with pytest.raises(UnknownBackendError):
        create_backend(BackendConfig(kind="comfyui", url=WORKER_URL, token=TOKEN))


def test_backend_config_hides_token() -> None:
    assert TOKEN not in repr(BackendConfig(kind="http_worker", url=WORKER_URL, token=TOKEN))
