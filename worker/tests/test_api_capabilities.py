"""/v1/capabilities is built from the registry (W-7)."""

from typing import ClassVar

from pydantic import BaseModel
from worker_support import StartWorker, Worker

from studio_contracts import API_VERSION, SongParams
from studio_worker import __version__
from studio_worker.engines.base import Engine
from studio_worker.engines.fake import FakeEngine
from studio_worker.operations.base import OperationContext
from studio_worker.operations.registry import all_operations, registered


class EchoParams(BaseModel):
    text: str


class Echo:
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


async def test_capabilities_shape(worker: Worker) -> None:
    caps = (await worker.client.get("/v1/capabilities")).json()
    assert caps["api_version"] == API_VERSION
    assert caps["worker_version"] == __version__
    assert caps["engine"] == {
        "kind": "fake",
        "package_version": __version__,
        "models": {"lm": "fake", "vae": "fake"},
    }
    [op] = caps["operations"]
    assert op["name"] == "song.generate"
    assert op["stages"] == ["plan", "semantic", "synthesis", "decode", "save"]
    assert op["params_schema"] == SongParams.model_json_schema()
    assert op["params_schema"]["additionalProperties"] is False


async def test_schemas_match_the_registry(worker: Worker) -> None:
    caps = (await worker.client.get("/v1/capabilities")).json()
    assert {o["name"]: o["params_schema"] for o in caps["operations"]} == {
        op.name: op.params_model.model_json_schema() for op in all_operations()
    }


async def test_single_call_engine_reports_two_stages(start_worker: StartWorker) -> None:
    async with start_worker(FakeEngine(staged=False)) as w:
        caps = (await w.client.get("/v1/capabilities")).json()
        assert caps["operations"][0]["stages"] == ["generate", "save"]
        job = (await w.submit()).json()
        assert job["stages"] == ["generate", "save"]
        done = await w.wait_for(job["id"], "succeeded")
        assert set(done["timings"]) == {"queue_s", "generate_s", "save_s", "total_s"}


async def test_a_new_operation_needs_no_core_change(worker: Worker) -> None:
    """W-7 (and the start of AT-10, which M5 completes): a test-only operation shows up
    in capabilities and runs through submit, status and artifact download."""
    with registered(Echo):
        caps = (await worker.client.get("/v1/capabilities")).json()
        names = [o["name"] for o in caps["operations"]]
        assert names == ["song.generate", "test.echo"]
        echo = next(o for o in caps["operations"] if o["name"] == "test.echo")
        assert echo["params_schema"] == EchoParams.model_json_schema()

        response = await worker.submit({"text": "hello"}, operation="test.echo")
        assert response.status_code == 202
        job = await worker.wait_for(response.json()["id"], "succeeded")
        assert job["stages"] == ["echo", "save"]
        assert [a["name"] for a in job["artifacts"]] == ["echo.txt"]
        body = await worker.client.get(f"/v1/jobs/{job['id']}/artifacts/echo.txt")
        assert body.text == "hello"

    caps = (await worker.client.get("/v1/capabilities")).json()
    assert [o["name"] for o in caps["operations"]] == ["song.generate"]
