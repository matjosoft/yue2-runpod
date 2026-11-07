"""The poller state machine (S-2, S-3). The poller is driven by ``tick()``."""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from server_support import (
    SONG_STAGES,
    FakeWorker,
    Server,
    StartServer,
    StartWorker,
    StepGate,
    song_request,
)

from studio_contracts import ArtifactRole, GenerationStatus
from studio_server.core import generations
from studio_server.core.backend import JobRef, JobView
from studio_server.core.db import GenerationRecord
from studio_server.core.ids import new_generation_id
from studio_worker.engines.fake import FakeEngine


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def files_under(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


async def test_stages_are_mirrored_in_order(
    start_server: StartServer, start_worker: StartWorker
) -> None:
    gate = StepGate()
    async with (
        start_worker(FakeEngine(stage_gate=gate)) as worker,
        start_server(worker.transport) as server,
    ):
        gen = (await server.create()).json()
        seen = []
        for stage in SONG_STAGES:
            current = await server.wait_for(gen["id"], "running", stage=stage, tick=True)
            seen.append(current["stage"])
            assert current["stages"] == SONG_STAGES
            gate.step()
        done = await server.wait_for(gen["id"], "succeeded", tick=True)
    assert seen == SONG_STAGES
    assert set(done["timings"]) >= {"queue_s", "plan_s", "save_s", "total_s"}


async def test_success_goes_through_downloading(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses: list[str] = []
    update = server.runtime.db.update

    def spy(gen_id: str, **changes: Any) -> GenerationRecord:
        if "status" in changes:
            statuses.append(changes["status"].value)
        return update(gen_id, **changes)

    monkeypatch.setattr(server.runtime.db, "update", spy)
    gen = (await server.create()).json()
    done = await server.wait_for(gen["id"], "succeeded", tick=True)
    assert statuses[-2:] == ["downloading", "succeeded"]
    names = {a["name"] for a in done["artifacts"]}
    assert {"audio.flac", "score.abc", "result.json"} <= names
    gen_dir = server.settings.data_dir / "generations" / gen["id"]
    assert set(files_under(gen_dir)) == names  # nothing else, no temp files


async def test_worker_failure_is_mirrored(
    start_server: StartServer, start_worker: StartWorker
) -> None:
    async with (
        start_worker(FakeEngine(fail_at="semantic")) as worker,
        start_server(worker.transport) as server,
    ):
        gen = (await server.create()).json()
        failed = await server.wait_for(gen["id"], "failed", tick=True)
    assert failed["error"]["code"] == "engine_error"
    assert failed["artifacts"] == []


async def test_unreachable_worker_fails_after_grace(server: Server, worker: FakeWorker) -> None:
    clock = FakeClock()
    server.runtime.poller.clock = clock
    gen = (await server.create()).json()
    worker.transport.down = True
    await server.runtime.poller.tick()
    clock.now += server.settings.unreachable_grace_s - 1
    await server.runtime.poller.tick()
    assert (await server.generation(gen["id"]))["status"] in ("queued", "running")
    clock.now += 2
    await server.runtime.poller.tick()
    failed = await server.generation(gen["id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "worker_unreachable"


async def test_reachable_again_resets_the_grace(server: Server, worker: FakeWorker) -> None:
    clock = FakeClock()
    server.runtime.poller.clock = clock
    gen = (await server.create()).json()
    worker.transport.down = True
    await server.runtime.poller.tick()
    clock.now += server.settings.unreachable_grace_s - 1
    worker.transport.down = False
    await server.runtime.poller.tick()
    worker.transport.down = True
    clock.now += 2
    await server.runtime.poller.tick()
    assert (await server.generation(gen["id"]))["status"] != "failed"


async def test_job_unknown_to_worker_fails(server: Server) -> None:
    gen = (await server.create()).json()
    server.runtime.db.update(gen["id"], worker_job_id="job_UNKNOWN")
    await server.runtime.poller.tick()
    failed = await server.generation(gen["id"])
    assert (failed["status"], failed["error"]["code"]) == ("failed", "not_found")


async def test_record_without_job_is_resubmitted(server: Server) -> None:
    """A server killed between storing and submitting leaves a record with no worker
    job; the poller submits it with the same ``client_request_id``."""
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
    server.runtime.link.submitting.add(gen_id)
    await server.runtime.poller.tick()
    assert server.runtime.db.get(gen_id).worker_job_id is None  # type: ignore[union-attr]
    server.runtime.link.submitting.discard(gen_id)
    done = await server.wait_for(gen_id, "succeeded", tick=True)
    assert done["worker_job_id"] is not None


async def test_at_most_two_downloads_at_once(server: Server, worker: FakeWorker) -> None:
    worker.transport.download_delay_s = 0.02
    gen = (await server.create()).json()
    done = await server.wait_for(gen["id"], "succeeded", tick=True)
    assert len(done["artifacts"]) > 2
    assert worker.transport.max_in_flight == 2


async def test_corrupt_download_is_retried(server: Server, worker: FakeWorker) -> None:
    """S-3: a corrupted download is fetched again and then verifies."""
    worker.transport.corrupt_downloads = 2
    gen = (await server.create()).json()
    done = await server.wait_for(gen["id"], "succeeded", tick=True)
    assert worker.transport.corrupt_downloads == 0
    flac = next(a for a in done["artifacts"] if a["name"] == "audio.flac")
    response = await server.request("GET", flac["url"])
    assert len(response.content) == flac["size_bytes"]


async def test_always_corrupt_download_fails_without_partial_files(
    server: Server, worker: FakeWorker
) -> None:
    """S-3: retried, then ``download_failed``; no partial file is visible."""
    worker.transport.corrupt_downloads = 10_000
    gen = (await server.create()).json()
    failed = await server.wait_for(gen["id"], "failed", tick=True)
    assert failed["error"]["code"] == "download_failed"
    assert failed["artifacts"] == []
    gen_dir = server.settings.data_dir / "generations" / gen["id"]
    assert files_under(gen_dir) == []


async def test_downloading_resumes_after_restart(
    start_server: StartServer, worker: FakeWorker
) -> None:
    """S-2: a generation left ``downloading`` (server stopped mid-download) finishes on
    the next start; the half-written temp file is cleaned up."""
    async with start_server(worker.transport) as server:
        gen = (await server.create()).json()
        rec = server.runtime.db.get(gen["id"])
        assert rec is not None and rec.worker_job_id is not None
        view = await _wait_worker_job(server, rec.worker_job_id, "succeeded")
        # Record what the poller records before downloading, then "crash".
        server.runtime.db.update(gen["id"], **generations.mirror_changes(rec, view))
        server.runtime.storage.temp_path(rec.data_dir).write_bytes(b"half")
        assert (await server.generation(gen["id"]))["status"] == "downloading"
    async with start_server(worker.transport) as server:
        done = await server.wait_for(gen["id"], "succeeded", tick=True)
        gen_dir = server.settings.data_dir / "generations" / gen["id"]
        assert set(files_under(gen_dir)) == {a["name"] for a in done["artifacts"]}


async def _wait_worker_job(server: Server, job_id: str, state: str) -> JobView:
    backend = server.runtime.link.backend()
    for _ in range(1000):
        view = await backend.get_job(JobRef(job_id))
        if view.state == state:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError(f"worker job never reached {state}")


async def test_intermediates_can_be_skipped(start_server: StartServer, worker: FakeWorker) -> None:
    async with start_server(worker.transport, download_intermediates=False) as server:
        gen = (await server.create()).json()
        done = await server.wait_for(gen["id"], "succeeded", tick=True)
        rec = server.runtime.db.get(gen["id"])
    assert rec is not None
    roles = {a["role"] for a in done["artifacts"]}
    assert ArtifactRole.intermediate.value not in roles
    assert "primary_audio" in roles
    # The full manifest is still recorded (G4).
    assert any(a.role is ArtifactRole.intermediate and not a.stored for a in rec.artifacts)


async def test_poller_loop_runs_by_itself(start_server: StartServer, worker: FakeWorker) -> None:
    async with start_server(worker.transport, start_poller=True) as server:
        gen = (await server.create()).json()
        done = await server.wait_for(gen["id"], "succeeded")
        assert done["status"] == "succeeded"
        health = (await server.request("GET", "/api/health")).json()
        assert health["worker"]["state"] == "ready"  # the loop refreshed the check


async def test_job_expired_on_the_worker_fails_with_a_clear_message(
    server: Server, worker: FakeWorker
) -> None:
    """The worker forgot a job the server still tracks (a TTL sweep while the server
    was off, or a wiped volume): the generation fails instead of polling forever."""
    gen = (await server.create()).json()
    await _wait_worker_job(server, gen["worker_job_id"], "succeeded")
    worker.app.state.runtime.store.delete(gen["worker_job_id"])
    await server.runtime.poller.tick()
    failed = await server.generation(gen["id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "not_found"
    assert failed["error"]["message"] == "The worker no longer has this job"


async def test_delete_succeeds_when_the_worker_job_is_gone(
    server: Server, worker: FakeWorker
) -> None:
    gen = (await server.create()).json()
    done = await server.wait_for(gen["id"], "succeeded", tick=True)
    worker.app.state.runtime.store.delete(done["worker_job_id"])
    response = await server.request("DELETE", f"/api/generations/{gen['id']}")
    assert response.status_code == 204
    assert (await server.request("GET", f"/api/generations/{gen['id']}")).status_code == 404
