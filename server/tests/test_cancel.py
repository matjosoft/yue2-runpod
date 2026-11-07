"""Cancel a generation (S-9, W-10). The poller is driven by ``tick()``."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from server_support import (
    SONG_STAGES,
    TIMEOUT_S,
    FakeWorker,
    Server,
    StartServer,
    StartWorker,
    StepGate,
    song_request,
)

from studio_contracts import GenerationStatus, JobState
from studio_server.core.backend import JobRef
from studio_server.core.db import GenerationRecord
from studio_server.core.ids import new_generation_id
from studio_worker.engines.fake import FakeEngine


def files_under(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


async def cancel(server: Server, gen_id: str) -> httpx.Response:
    return await server.request("POST", f"/api/generations/{gen_id}/cancel")


async def wait_worker_job(server: Server, job_id: str, state: JobState) -> None:
    backend = server.runtime.link.backend()
    deadline = asyncio.get_running_loop().time() + TIMEOUT_S
    while (await backend.get_job(JobRef(job_id))).state is not state:
        assert asyncio.get_running_loop().time() < deadline, f"worker job never {state}"
        await asyncio.sleep(0.01)


def insert_unsubmitted(server: Server) -> str:
    """A record the server stored but never submitted (it stopped in between)."""
    gen_id = new_generation_id()
    now = server.runtime.db.now()
    server.runtime.db.insert(
        GenerationRecord(
            id=gen_id,
            created_at=now,
            updated_at=now,
            title="t",
            operation="song.generate",
            request=song_request(seed=1),
            status=GenerationStatus.queued,
            stage=None,
            stages=SONG_STAGES,
            error=None,
            worker_job_id=None,
            client_request_id=str(uuid.uuid4()),
            timings={},
            artifacts=[],
            data_dir=server.runtime.storage.relative_dir(gen_id),
        )
    )
    return gen_id


async def test_cancel_a_queued_generation(
    start_server: StartServer, start_worker: StartWorker
) -> None:
    gate = StepGate()
    async with (
        start_worker(FakeEngine(stage_gate=gate)) as worker,
        start_server(worker.transport) as server,
    ):
        first = (await server.create()).json()
        await server.wait_for(first["id"], "running", stage="plan", tick=True)
        second = (await server.create()).json()

        response = await cancel(server, second["id"])
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == second["id"]
        assert body["status"] == "canceled" and body["error"] is None
        gate.set()
        await server.wait_for(first["id"], "succeeded", tick=True)
        assert (await server.generation(second["id"]))["status"] == "canceled"
        assert worker.engine.calls.count("plan") == 1


async def test_cancel_a_running_generation(
    start_server: StartServer, start_worker: StartWorker
) -> None:
    gate = StepGate()
    async with (
        start_worker(FakeEngine(stage_gate=gate)) as worker,
        start_server(worker.transport) as server,
    ):
        gen = (await server.create()).json()
        await server.wait_for(gen["id"], "running", stage="plan", tick=True)

        response = await cancel(server, gen["id"])
        assert response.status_code == 200
        assert response.json()["status"] == "running"  # stops at the next stage boundary
        gate.set()
        done = await server.wait_for(gen["id"], "canceled", tick=True)
        assert done["artifacts"] == [] and done["error"] is None
        assert worker.engine.calls == ["plan"]
        assert not [r for r in worker.transport.requests if "/artifacts/" in r.url.path]
        assert files_under(server.settings.data_dir / "generations") == []


@pytest.mark.parametrize("status", ["downloading", "succeeded", "failed", "canceled"])
async def test_cancel_is_409_unless_queued_or_running(server: Server, status: str) -> None:
    gen = (await server.create()).json()
    server.runtime.db.update(gen["id"], status=GenerationStatus(status))
    response = await cancel(server, gen["id"])
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"] == {"generation_id": gen["id"], "status": status}
    assert (await server.generation(gen["id"]))["status"] == status


async def test_cancel_unknown_generation_is_404(server: Server) -> None:
    response = await cancel(server, "gen_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_cancel_with_the_worker_unreachable_changes_nothing(
    server: Server, worker: FakeWorker
) -> None:
    gen = (await server.create()).json()
    worker.transport.down = True
    response = await cancel(server, gen["id"])
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "worker_unreachable"
    assert (await server.generation(gen["id"]))["status"] == "queued"


async def test_worker_conflict_is_409(server: Server) -> None:
    """The worker job already finished, but the poller has not mirrored it yet."""
    gen = (await server.create()).json()
    await wait_worker_job(server, gen["worker_job_id"], JobState.succeeded)
    response = await cancel(server, gen["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    done = await server.wait_for(gen["id"], "succeeded", tick=True)
    assert done["artifacts"]


async def test_job_gone_from_the_worker_is_canceled_locally(server: Server) -> None:
    gen = (await server.create()).json()
    server.runtime.db.update(gen["id"], worker_job_id="job_UNKNOWN")
    response = await cancel(server, gen["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


async def test_unsubmitted_record_is_canceled_locally(server: Server, worker: FakeWorker) -> None:
    gen_id = insert_unsubmitted(server)
    response = await cancel(server, gen_id)
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
    await server.runtime.poller.tick()
    assert (await server.generation(gen_id))["worker_job_id"] is None
    assert not [r for r in worker.transport.requests if r.method == "POST"]


async def test_cancel_during_a_poller_resubmit_is_kept(
    server: Server, worker: FakeWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poller is resubmitting a record when the cancel lands. The cancel wins: the
    record stays ``canceled`` and the job the submit created is canceled too."""
    gen_id = insert_unsubmitted(server)
    inner = worker.transport.handle_async_request

    async def cancel_during_submit(request: httpx.Request) -> httpx.Response:
        response = await inner(request)
        if request.method == "POST" and request.url.path == "/v1/jobs":
            assert (await cancel(server, gen_id)).json()["status"] == "canceled"
        return response

    monkeypatch.setattr(worker.transport, "handle_async_request", cancel_during_submit)
    await server.runtime.poller.tick()
    monkeypatch.undo()

    rec = server.runtime.db.get(gen_id)
    assert rec is not None and rec.status is GenerationStatus.canceled
    assert rec.worker_job_id is not None
    await wait_worker_job(server, rec.worker_job_id, JobState.canceled)


async def test_delete_stays_409_until_canceled(
    start_server: StartServer, start_worker: StartWorker
) -> None:
    """Deleting an in-progress generation is refused; cancel first, then delete."""
    gate = StepGate()
    async with (
        start_worker(FakeEngine(stage_gate=gate)) as worker,
        start_server(worker.transport) as server,
    ):
        gen = (await server.create()).json()
        await server.wait_for(gen["id"], "running", tick=True)
        response = await server.request("DELETE", f"/api/generations/{gen['id']}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
        await cancel(server, gen["id"])
        gate.set()
        await server.wait_for(gen["id"], "canceled", tick=True)
        response = await server.request("DELETE", f"/api/generations/{gen['id']}")
        assert response.status_code == 204


# --- races found in review ----------------------------------------------------------------


def hook_requests(
    monkeypatch: pytest.MonkeyPatch,
    worker: FakeWorker,
    when: Callable[[httpx.Request], bool],
    action: Callable[[], Awaitable[None]],
) -> None:
    """Run ``action`` once, right after the worker answers the first request that
    matches ``when``: a user's request landing while the server waits for the worker."""
    inner = worker.transport.handle_async_request
    fired = False

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal fired
        response = await inner(request)
        if not fired and when(request):
            fired = True
            await action()
        return response

    monkeypatch.setattr(worker.transport, "handle_async_request", handle)


def is_submit(request: httpx.Request) -> bool:
    return request.method == "POST" and request.url.path == "/v1/jobs"


async def test_cancel_during_create_cancels_the_new_job(
    server: Server, worker: FakeWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The create request is still waiting for the worker when the cancel lands (a
    slow worker, and the new row already visible in the library)."""

    async def cancel_the_new_record() -> None:
        [rec] = server.runtime.db.page(limit=1, before=None)
        assert (await cancel(server, rec.id)).json()["status"] == "canceled"

    hook_requests(monkeypatch, worker, is_submit, cancel_the_new_record)
    created = await server.create()
    monkeypatch.undo()
    assert created.status_code == 201
    gen = created.json()
    assert gen["status"] == "canceled" and gen["worker_job_id"] is not None
    await wait_worker_job(server, gen["worker_job_id"], JobState.canceled)


async def test_cancel_then_delete_during_a_poller_resubmit(
    server: Server, worker: FakeWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    gen_id = insert_unsubmitted(server)

    async def cancel_and_delete() -> None:
        await cancel(server, gen_id)
        response = await server.request("DELETE", f"/api/generations/{gen_id}")
        assert response.status_code == 204

    hook_requests(monkeypatch, worker, is_submit, cancel_and_delete)
    await server.runtime.poller.tick()
    monkeypatch.undo()
    assert server.runtime.db.get(gen_id) is None
    [job] = worker.app.state.runtime.store.list_recent()
    await wait_worker_job(server, job.id, JobState.canceled)


async def test_a_poller_error_never_overwrites_a_cancel(
    server: Server, worker: FakeWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poller's read gets a 404 while the user's cancel (also a 404) marks the
    record canceled; the poller must not then fail it with not_found."""
    gen = (await server.create()).json()
    server.runtime.db.update(gen["id"], worker_job_id="job_UNKNOWN")

    async def user_cancels() -> None:
        assert (await cancel(server, gen["id"])).json()["status"] == "canceled"

    hook_requests(
        monkeypatch,
        worker,
        lambda r: r.method == "GET" and r.url.path == "/v1/jobs/job_UNKNOWN",
        user_cancels,
    )
    await server.runtime.poller.tick()
    monkeypatch.undo()
    after = await server.generation(gen["id"])
    assert (after["status"], after["error"]) == ("canceled", None)


async def test_cancel_never_writes_an_older_state_back(
    start_server: StartServer, start_worker: StartWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the cancel handler reads the job, the poller moves the record on; the
    handler's older view must not replace it."""
    gate = StepGate()
    async with (
        start_worker(FakeEngine(stage_gate=gate)) as worker,
        start_server(worker.transport) as server,
    ):
        gen = (await server.create()).json()
        await server.wait_for(gen["id"], "running", stage="plan", tick=True)
        canceled_seen = False

        async def poller_moves_on() -> None:
            nonlocal canceled_seen
            canceled_seen = True
            server.runtime.db.update(gen["id"], status=GenerationStatus.canceled)

        hook_requests(
            monkeypatch,
            worker,
            lambda r: r.method == "GET" and r.url.path == f"/v1/jobs/{gen['worker_job_id']}",
            poller_moves_on,
        )
        response = await cancel(server, gen["id"])
        monkeypatch.undo()
        assert canceled_seen
        assert response.status_code == 200
        assert response.json()["status"] == "canceled"
        assert (await server.generation(gen["id"]))["status"] == "canceled"
