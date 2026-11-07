"""/healthz, /v1/status (W-1, W-12) and warm-up (AT-12)."""

import threading

from worker_support import StartWorker, Worker

from studio_worker.engines.fake import FakeEngine


async def test_healthz_is_ok_and_nothing_else(worker: Worker) -> None:
    response = await worker.client.get("/healthz")
    assert response.json() == {"ok": True}


async def test_status_shape(worker: Worker) -> None:
    status = await worker.status()
    assert set(status) == {"uptime_s", "idle_seconds", "model", "gpu", "queue"}
    assert status["model"] == {"state": "ready", "error": None}
    assert status["gpu"] is None
    assert status["queue"] == {"queued": 0, "running": None, "max": 4}
    assert status["uptime_s"] >= 0 and status["idle_seconds"] >= 0


async def test_status_shows_running_and_queued_jobs(start_worker: StartWorker) -> None:
    hold = threading.Event()
    async with start_worker(FakeEngine(stage_gate=hold)) as w:
        first = (await w.submit()).json()
        await w.wait_for(first["id"], "running")
        await w.submit()
        status = await w.status()
        assert status["queue"] == {"queued": 1, "running": first["id"], "max": 4}
        assert status["idle_seconds"] == 0  # W-12: reset while a job runs
        hold.set()


async def test_at12_warm_up(start_worker: StartWorker) -> None:
    """AT-12: with a delayed model load, status shows loading and submit returns 503
    not_ready with Retry-After; once loaded, status is ready and submit is accepted."""
    loaded = threading.Event()
    engine = FakeEngine(load_gate=loaded)
    async with start_worker(engine, wait_ready=False) as w:
        assert (await w.status())["model"] == {"state": "loading", "error": None}
        response = await w.submit()
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "10"
        error = response.json()["error"]
        assert error["code"] == "not_ready" and error["retryable"] is True
        assert error["details"] == {"model_state": "loading"}
        assert (await w.client.get("/healthz")).status_code == 200  # alive while loading

        loaded.set()
        await w.wait_model("ready")
        response = await w.submit()
        assert response.status_code == 202
        await w.wait_for(response.json()["id"], "succeeded")


async def test_load_failure_keeps_returning_503(start_worker: StartWorker) -> None:
    engine = FakeEngine(load_error=RuntimeError("The unquantized preset requires CUDA BF16"))
    async with start_worker(engine, wait_ready=False) as w:
        status = await w.wait_model("error")
        assert status["model"]["error"] == (
            "RuntimeError: The unquantized preset requires CUDA BF16"
        )
        for _ in range(2):
            response = await w.submit()
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "not_ready"
            assert response.json()["error"]["message"] == "The model failed to load"


async def test_model_is_loaded_once_across_jobs(worker: Worker) -> None:
    calls: list[int] = []
    original = worker.engine.load

    def counting_load() -> None:
        calls.append(1)
        original()

    worker.engine.load = counting_load  # type: ignore[method-assign]
    for _ in range(2):
        job = (await worker.submit()).json()
        await worker.wait_for(job["id"], "succeeded")
    assert calls == []  # W-1: no reload between jobs
