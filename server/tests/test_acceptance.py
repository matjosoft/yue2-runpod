"""Acceptance scenarios (PRD section 12): AT-1 (API level), AT-4, AT-5, AT-7, AT-9, AT-10,
AT-11.

These run the server with its real lifespan and poller loop, against the real worker
app with a FakeEngine, both in-process.
"""

import io
import json
import logging
from typing import ClassVar

import pytest
import soundfile as sf
from pydantic import BaseModel
from server_support import (
    SONG_STAGES,
    TOKEN,
    FakeWorker,
    StartServer,
    StartWorker,
    StepGate,
    song_request,
)

from studio_server.core.logs import JsonFormatter
from studio_worker.engines.base import Engine
from studio_worker.engines.fake import FakeEngine
from studio_worker.operations.base import OperationContext
from studio_worker.operations.registry import registered


async def test_at1_happy_path(start_server: StartServer, start_worker: StartWorker) -> None:
    """AT-1: stages in order, ``succeeded``, a 48 kHz stereo FLAC, listed in the library."""
    gate = StepGate()
    async with (
        start_worker(FakeEngine(stage_gate=gate)) as worker,
        start_server(worker.transport, start_poller=True) as server,
    ):
        created = await server.create(song_request(), title="AT-1")
        assert created.status_code == 201
        gen = created.json()
        assert gen["status"] == "queued"
        for stage in SONG_STAGES:
            current = await server.wait_for(gen["id"], "running", stage=stage)
            assert current["stages"] == SONG_STAGES
            gate.step()
        done = await server.wait_for(gen["id"], "succeeded")

        flac = next(a for a in done["artifacts"] if a["role"] == "primary_audio")
        audio = await server.request("GET", flac["url"])
        assert audio.status_code == 200
        info = sf.info(io.BytesIO(audio.content))
        assert (info.samplerate, info.channels, info.format) == (48000, 2, "FLAC")

        library = (await server.request("GET", "/api/generations")).json()
        assert [g["id"] for g in library["items"]] == [gen["id"]]
        assert library["items"][0]["status"] == "succeeded"
        assert library["items"][0]["title"] == "AT-1"


async def test_at4_wrong_token(
    start_server: StartServer, worker: FakeWorker, caplog: pytest.LogCaptureFixture
) -> None:
    """AT-4: a wrong token shows as ``unauthorized``; the token appears in no response
    and no log line. The right token never leaks either."""
    wrong = "wrong-token-" + "q" * 40
    caplog.set_level(logging.DEBUG)
    async with start_server(worker.transport, start_poller=True, worker_token=None) as server:
        await server.request("PUT", "/api/settings", json={"token": wrong})
        check = (await server.request("POST", "/api/worker/check")).json()
        assert check["state"] == "unauthorized"
        assert check["error"]["code"] == "unauthorized"
        created = await server.create()
        assert created.status_code == 502
        assert created.json()["error"]["code"] == "unauthorized"
        for method, path in [
            ("GET", "/api/settings"),
            ("GET", "/api/health"),
            ("GET", "/api/capabilities"),
            ("GET", "/api/generations"),
            ("GET", "/openapi.json"),
        ]:
            await server.request(method, path)

        await server.request("PUT", "/api/settings", json={"token": TOKEN})
        assert (await server.request("POST", "/api/worker/check")).json()["state"] == "ready"
        gen = (await server.create()).json()
        await server.wait_for(gen["id"], "succeeded")
        await server.request("GET", "/api/settings")

    formatter = JsonFormatter()
    log_text = "\n".join(formatter.format(r) for r in caplog.records)
    assert caplog.records, "expected some log lines"
    for secret in (wrong, TOKEN):
        for body in server.seen_bodies:
            assert secret not in body
        assert secret not in log_text
    # Lyrics and style are not logged either (PRD section 11, "Privacy").
    assert song_request()["lyrics"] not in log_text
    assert song_request()["style"] not in log_text


async def test_at5_server_restart_mid_job(
    start_server: StartServer, start_worker: StartWorker
) -> None:
    """AT-5: stop the server while a job runs, start it again, and the generation still
    finishes and downloads."""
    gate = StepGate()
    async with start_worker(FakeEngine(stage_gate=gate)) as worker:
        async with start_server(worker.transport, start_poller=True) as server:
            gen = (await server.create()).json()
            await server.wait_for(gen["id"], "running", stage="plan")
        # The server is down; the worker keeps going.
        gate.set()
        async with start_server(worker.transport, start_poller=True) as server:
            done = await server.wait_for(gen["id"], "succeeded")
            assert done["worker_job_id"] == gen["worker_job_id"]
            flac = next(a for a in done["artifacts"] if a["name"] == "audio.flac")
            response = await server.request("GET", flac["url"])
            assert len(response.content) == flac["size_bytes"]


async def test_at9_persistence_and_delete(start_server: StartServer, worker: FakeWorker) -> None:
    """AT-9: the library survives a server restart; delete removes the files on disk."""
    async with start_server(worker.transport, start_poller=True) as server:
        ids = []
        for i in range(2):
            gen = (await server.create(title=f"take {i}")).json()
            await server.wait_for(gen["id"], "succeeded")
            ids.append(gen["id"])
        before = (await server.request("GET", "/api/generations")).json()
    async with start_server(worker.transport, start_poller=True) as server:
        after = (await server.request("GET", "/api/generations")).json()
        assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)
        gen_dir = server.settings.data_dir / "generations" / ids[0]
        assert gen_dir.is_dir()
        assert (await server.request("DELETE", f"/api/generations/{ids[0]}")).status_code == 204
        assert not gen_dir.exists()
    async with start_server(worker.transport) as server:
        items = (await server.request("GET", "/api/generations")).json()["items"]
        assert [g["id"] for g in items] == [ids[1]]


async def test_at11_worker_restart_then_retry(
    start_server: StartServer, start_worker: StartWorker
) -> None:
    """AT-11: the worker restarts mid-job, so the generation fails with
    ``worker_restarted``; Retry (the same request, posted again) creates a new
    generation and a new worker job, and that one succeeds."""
    gate = StepGate()
    async with start_worker(FakeEngine(stage_gate=gate)) as worker:
        async with start_server(worker.transport, start_poller=True) as server:
            gen = (await server.create(title="AT-11")).json()
            await server.wait_for(gen["id"], "running", stage="plan")
        job_file = worker.jobs_dir / gen["worker_job_id"] / "job.json"
        crashed = job_file.read_bytes()  # what a kill -9 would leave on disk
    job_file.write_bytes(crashed)

    async with (
        start_worker() as worker,
        start_server(worker.transport, start_poller=True) as server,
    ):
        failed = await server.wait_for(gen["id"], "failed")
        assert failed["error"]["code"] == "worker_restarted"
        assert failed["error"]["retryable"] is True

        retry = await server.create(failed["request"], title=failed["title"])
        assert retry.status_code == 201
        again = retry.json()
        assert again["id"] != gen["id"]
        assert again["worker_job_id"] != gen["worker_job_id"]
        assert again["request"] == gen["request"]  # same seed too
        await server.wait_for(again["id"], "succeeded")


async def test_at7_cancel(start_server: StartServer, start_worker: StartWorker) -> None:
    """AT-7 / S-9 / W-10: cancel a queued generation (at once) and a running one (at the
    next stage boundary); both end ``canceled`` with no files, and the worker keeps
    serving."""
    gate = StepGate()
    async with (
        start_worker(FakeEngine(stage_gate=gate)) as worker,
        start_server(worker.transport, start_poller=True) as server,
    ):
        running = (await server.create()).json()
        await server.wait_for(running["id"], "running", stage="plan")
        queued = (await server.create()).json()

        response = await server.request("POST", f"/api/generations/{queued['id']}/cancel")
        assert (response.status_code, response.json()["status"]) == (200, "canceled")
        response = await server.request("POST", f"/api/generations/{running['id']}/cancel")
        assert response.status_code == 200
        gate.set()
        done = await server.wait_for(running["id"], "canceled")
        assert done["artifacts"] == [] and done["error"] is None
        assert worker.engine.calls == ["plan"]
        gen_dir = server.settings.data_dir / "generations" / running["id"]
        assert not gen_dir.exists() or not any(gen_dir.rglob("*"))

        after = (await server.create()).json()  # the worker is still usable
        await server.wait_for(after["id"], "succeeded")


class EchoParams(BaseModel):
    text: str


class Echo:
    """A test-only operation. Nothing in the server or the worker core knows about it."""

    name: ClassVar[str] = "test.echo"
    params_model: ClassVar[type[BaseModel]] = EchoParams

    def stages(self, engine: Engine) -> list[str]:
        return ["echo", "save"]

    def run(self, ctx: OperationContext, params: BaseModel) -> None:
        assert isinstance(params, EchoParams)
        ctx.stage("echo")
        ctx.stage("save")
        (ctx.job_dir / "echo.txt").write_text(params.text, encoding="utf-8")
        ctx.add_artifact("echo.txt", "result", "text/plain")


async def test_at10_new_operation_end_to_end(start_server: StartServer, worker: FakeWorker) -> None:
    """AT-10: an operation registered only in a test appears in capabilities and runs
    end to end through the server, with no change to core code."""
    async with start_server(worker.transport, start_poller=True) as server:
        with registered(Echo):
            await server.request("POST", "/api/worker/check")  # refreshes capabilities
            caps = (await server.request("GET", "/api/capabilities")).json()
            echo = next(o for o in caps["operations"] if o["name"] == "test.echo")
            assert echo["stages"] == ["echo", "save"]
            assert echo["params_schema"] == EchoParams.model_json_schema()

            created = await server.create({"text": "hello"}, operation="test.echo")
            assert created.status_code == 201, created.text
            gen = created.json()
            assert (gen["operation"], gen["request"]) == ("test.echo", {"text": "hello"})
            assert gen["stages"] == ["echo", "save"]
            done = await server.wait_for(gen["id"], "succeeded")
            assert done["stage"] == "save"
            assert set(done["timings"]) == {"queue_s", "echo_s", "save_s", "total_s"}
            [artifact] = done["artifacts"]
            assert artifact["name"] == "echo.txt" and artifact["role"] == "result"
            body = await server.request("GET", f"/api/generations/{gen['id']}/artifacts/echo.txt")
            assert (body.status_code, body.text) == (200, "hello")

        await server.request("POST", "/api/worker/check")
        caps = (await server.request("GET", "/api/capabilities")).json()
        assert [o["name"] for o in caps["operations"]] == ["song.generate"]
