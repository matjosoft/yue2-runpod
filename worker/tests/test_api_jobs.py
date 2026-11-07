"""Job routes: W-2, W-3, W-4, W-10, W-11, plus AT-6, AT-7, AT-8 and AT-11."""

import asyncio
import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from worker_support import StartWorker, Worker, song_params

from studio_contracts import Job
from studio_worker import main
from studio_worker.engines.fake import FakeEngine
from studio_worker.jobs.store import utcnow


async def test_submit_returns_202_with_a_queued_job(worker: Worker) -> None:
    crid = str(uuid.uuid4())
    response = await worker.submit(song_params(seed=12345, cfg_scale=1.2), client_request_id=crid)
    assert response.status_code == 202
    job = Job.model_validate(response.json())
    assert job.client_request_id == crid
    assert job.operation == "song.generate"
    assert job.stages == ["plan", "semantic", "synthesis", "decode", "save"]
    assert job.params == {**song_params(), "seed": 12345, "cfg_scale": 1.2, "cot": "full"}


async def test_job_lifecycle_is_visible_through_get(start_worker: StartWorker) -> None:
    """W-4: queued, then running with each declared stage in order, then succeeded."""
    hold = threading.Event()
    engine = FakeEngine(stage_gate=hold)
    async with start_worker(engine) as w:
        job = (await w.submit()).json()
        assert job["state"] == "queued" and job["stage"] is None
        assert job["started_at"] is None and job["finished_at"] is None
        seen = []
        for stage in job["stages"]:
            current = await w.wait_for(job["id"], "running", stage=stage)
            seen.append(current["stage"])
            assert current["started_at"] is not None
            hold.set()
            hold.clear()
        hold.set()
        done = await w.wait_for(job["id"], "succeeded")
    assert seen == job["stages"]
    assert done["params_resolved"]["seed"] >= 0
    assert done["error"] is None
    assert datetime.fromisoformat(done["finished_at"]) >= datetime.fromisoformat(done["started_at"])
    assert done["created_at"].endswith("Z")


async def test_failed_job_carries_the_error_object(start_worker: StartWorker) -> None:
    engine = FakeEngine(fail_at="decode", fail_with=ValueError("non-finite audio"))
    async with start_worker(engine) as w:
        job = (await w.submit()).json()
        done = await w.wait_for(job["id"], "failed")
    assert done["error"] == {
        "code": "engine_error",
        "message": "The engine failed",
        "retryable": False,
        "details": {"type": "ValueError", "message": "non-finite audio"},
    }
    assert done["artifacts"] == []


async def test_unknown_operation_is_422(worker: Worker) -> None:
    response = await worker.submit({}, operation="song.nope")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["fields"] == [{"loc": "operation", "msg": "Unknown operation"}]
    assert error["details"]["operations"] == ["song.generate"]


async def test_bad_params_are_422_with_field_locations(worker: Worker) -> None:
    lyrics = "secret lyrics " * 10
    response = await worker.submit(
        {"style": "x" * 1001, "lyrics": lyrics, "cfg_scale": 25, "styel": "typo"}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error" and error["retryable"] is False
    locs = sorted(f["loc"] for f in error["details"]["fields"])
    assert locs == ["params.cfg_scale", "params.styel", "params.style"]
    assert "x" * 100 not in response.text  # the rejected input is not echoed


async def test_malformed_body_is_422(worker: Worker) -> None:
    response = await worker.client.post(
        "/v1/jobs", content=b"{nope", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    response = await worker.client.post("/v1/jobs", json={"operation": "song.generate"})
    locs = {f["loc"] for f in response.json()["error"]["details"]["fields"]}
    assert locs == {"client_request_id", "params"}


async def test_at8_same_client_request_id_creates_one_job(worker: Worker) -> None:
    """AT-8 / W-2: posting the same client_request_id twice returns the same job and
    runs the engine once."""
    crid = str(uuid.uuid4())
    first = await worker.submit(client_request_id=crid)
    second = await worker.submit(client_request_id=crid)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    await worker.wait_for(first.json()["id"], "succeeded")
    third = await worker.submit(client_request_id=crid)  # also after it finished
    assert third.json()["id"] == first.json()["id"]
    assert third.json()["state"] == "succeeded"
    jobs = (await worker.client.get("/v1/jobs")).json()["jobs"]
    assert [j["id"] for j in jobs] == [first.json()["id"]]
    assert worker.engine.calls.count("plan") == 1


async def test_at6_queue_full(start_worker: StartWorker) -> None:
    """AT-6 / W-3: with MAX_QUEUE=4, one job runs and four wait; of MAX_QUEUE + 2
    submits, the excess one gets 429 queue_full."""
    hold = threading.Event()
    async with start_worker(FakeEngine(stage_gate=hold), max_queue=4) as w:
        running = (await w.submit()).json()
        await w.wait_for(running["id"], "running")
        responses = [await w.submit() for _ in range(5)]
        assert [r.status_code for r in responses] == [202, 202, 202, 202, 429]
        error = responses[-1].json()["error"]
        assert error == {
            "code": "queue_full",
            "message": "The job queue is full",
            "retryable": True,
            "details": {"max": 4},
        }
        hold.set()
        for r in responses[:4]:
            await w.wait_for(r.json()["id"], "succeeded")
        assert w.engine.max_concurrent == 1  # two jobs never overlap on the GPU


async def test_list_is_newest_first(worker: Worker) -> None:
    ids = [(await worker.submit()).json()["id"] for _ in range(3)]
    listed = [j["id"] for j in (await worker.client.get("/v1/jobs")).json()["jobs"]]
    assert listed == list(reversed(ids))


async def test_unknown_job_is_404(worker: Worker) -> None:
    response = await worker.client.get("/v1/jobs/job_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_delete_only_finished_jobs(start_worker: StartWorker) -> None:
    hold = threading.Event()
    async with start_worker(FakeEngine(stage_gate=hold)) as w:
        job = (await w.submit()).json()
        await w.wait_for(job["id"], "running")
        response = await w.client.delete(f"/v1/jobs/{job['id']}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
        hold.set()
        await w.wait_for(job["id"], "succeeded")
        response = await w.client.delete(f"/v1/jobs/{job['id']}")
        assert response.status_code == 204 and response.content == b""
        assert (await w.client.get(f"/v1/jobs/{job['id']}")).status_code == 404
        assert not (w.settings.jobs_dir / job["id"]).exists()
        assert (await w.client.delete(f"/v1/jobs/{job['id']}")).status_code == 404


async def test_at11_worker_restart_fails_interrupted_jobs(
    start_worker: StartWorker, jobs_dir: Path
) -> None:
    """AT-11: a worker that restarts while a job runs reports it failed with
    worker_restarted (and a queued one too); finished jobs survive."""
    hold = threading.Event()
    async with start_worker(FakeEngine(stage_gate=hold)) as w:
        done = (await w.submit()).json()
        hold.set()
        await w.wait_for(done["id"], "succeeded")
        hold.clear()
        running = (await w.submit()).json()
        await w.wait_for(running["id"], "running")
        queued = (await w.submit()).json()
        # Simulate a crash: freeze the on-disk state as it is now.
        snapshot = {
            i: (jobs_dir / i / "job.json").read_bytes()
            for i in (done["id"], running["id"], queued["id"])
        }
    for job_id, data in snapshot.items():
        (jobs_dir / job_id / "job.json").write_bytes(data)
    assert json.loads(snapshot[running["id"]])["state"] == "running"

    async with start_worker() as w:
        for job_id in (running["id"], queued["id"]):
            job = await w.job(job_id)
            assert job["state"] == "failed"
            assert job["error"]["code"] == "worker_restarted"
            assert job["error"]["retryable"] is True
        assert (await w.job(done["id"]))["state"] == "succeeded"
        # The idempotency index survives too: a retry with the old id is not rerun.
        again = await w.submit(client_request_id=running["client_request_id"])
        assert again.json()["id"] == running["id"]
        # A retry with a new client_request_id is a new job, and it runs.
        retry = (await w.submit()).json()
        await w.wait_for(retry["id"], "succeeded")


# --- cancel (W-10, AT-7) ----------------------------------------------------------------


async def test_cancel_errors(start_worker: StartWorker) -> None:
    async with start_worker() as w:
        response = await w.client.post("/v1/jobs/job_nope/cancel")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

        job = (await w.submit()).json()
        await w.wait_for(job["id"], "succeeded")
        response = await w.client.post(f"/v1/jobs/{job['id']}/cancel")
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "conflict" and error["retryable"] is False
        assert error["details"] == {"job_id": job["id"]}

        response = await w.client.post(
            f"/v1/jobs/{job['id']}/cancel", headers={"Authorization": ""}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


async def test_at7_cancel_queued_and_running_jobs(start_worker: StartWorker) -> None:
    """AT-7 / W-10: a queued job cancels at once; a running job ends ``canceled`` at the
    next stage boundary, with no output and nothing to download."""
    hold = threading.Event()
    async with start_worker(FakeEngine(stage_gate=hold)) as w:
        running = (await w.submit()).json()
        await w.wait_for(running["id"], "running", stage="plan")
        queued = (await w.submit()).json()

        response = await w.client.post(f"/v1/jobs/{queued['id']}/cancel")
        assert response.status_code == 200
        body = Job.model_validate(response.json())
        assert body.state == "canceled" and body.finished_at is not None

        response = await w.client.post(f"/v1/jobs/{running['id']}/cancel")
        assert response.status_code == 200
        assert response.json()["state"] == "running"
        again = await w.client.post(f"/v1/jobs/{running['id']}/cancel")
        assert again.status_code == 200  # a second cancel is accepted

        hold.set()
        done = await w.wait_for(running["id"], "canceled")
        assert done["artifacts"] == [] and done["error"] is None
        assert w.engine.calls == ["plan"]
        assert list((w.settings.jobs_dir / running["id"] / "artifacts").iterdir()) == []
        download = await w.client.get(f"/v1/jobs/{running['id']}/artifacts/audio.flac")
        assert download.status_code == 404
        # A canceled job is finished, so it can be deleted.
        assert (await w.client.delete(f"/v1/jobs/{running['id']}")).status_code == 204


# --- TTL cleanup (W-11) -----------------------------------------------------------------


async def test_expired_jobs_are_404_and_running_jobs_survive(start_worker: StartWorker) -> None:
    hold = threading.Event()
    async with start_worker(FakeEngine(stage_gate=hold)) as w:
        done = (await w.submit()).json()
        hold.set()
        await w.wait_for(done["id"], "succeeded")
        hold.clear()
        running = (await w.submit()).json()
        await w.wait_for(running["id"], "running")

        runtime = w.app.state.runtime
        assert runtime.sweep_expired(utcnow() + timedelta(hours=23)) == []
        assert runtime.sweep_expired(utcnow() + timedelta(hours=25)) == [done["id"]]

        for path in (f"/v1/jobs/{done['id']}", f"/v1/jobs/{done['id']}/artifacts/audio.flac"):
            response = await w.client.get(path)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"
        assert not (w.settings.jobs_dir / done["id"]).exists()
        assert (await w.job(running["id"]))["state"] == "running"
        hold.set()
        await w.wait_for(running["id"], "succeeded")


async def test_startup_deletes_expired_jobs(start_worker: StartWorker, jobs_dir: Path) -> None:
    async with start_worker() as w:
        done = (await w.submit()).json()
        await w.wait_for(done["id"], "succeeded")
    assert (jobs_dir / done["id"]).is_dir()
    async with start_worker(job_ttl_hours=1e-9) as w:
        assert (await w.client.get(f"/v1/jobs/{done['id']}")).status_code == 404
    assert not (jobs_dir / done["id"]).exists()


async def test_the_sweep_runs_periodically(
    start_worker: StartWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "SWEEP_INTERVAL_S", 0.01)
    async with start_worker(job_ttl_hours=1e-9) as w:
        job = (await w.submit()).json()
        deadline = asyncio.get_running_loop().time() + 10
        while (await w.client.get(f"/v1/jobs/{job['id']}")).status_code != 404:
            assert asyncio.get_running_loop().time() < deadline, "job was never swept"
            await asyncio.sleep(0.01)
