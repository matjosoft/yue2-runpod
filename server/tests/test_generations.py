"""``POST/GET /api/generations`` (S-1, AT-2 at API level) and the submit rules."""

import re
import threading
from typing import Any

import httpx
from server_support import (
    SONG_STAGES,
    TOKEN,
    WORKER_URL,
    FakeWorker,
    Server,
    StartServer,
    StartWorker,
    song_request,
)

from studio_contracts import OperationInfo
from studio_server.core.generations import fill_seed
from studio_worker.engines.fake import FakeEngine

SEED_MAX = 2_147_483_647


async def test_create_returns_queued_record_with_seed(server: Server) -> None:
    """S-1: the record comes back ``queued`` and the seed is always filled in."""
    response = await server.create(song_request())
    assert response.status_code == 201, response.text
    gen = response.json()
    assert gen["status"] == "queued"
    assert gen["id"].startswith("gen_")
    assert gen["operation"] == "song.generate"
    assert isinstance(gen["request"]["seed"], int)
    assert 0 <= gen["request"]["seed"] <= SEED_MAX
    assert gen["stages"] == SONG_STAGES
    assert gen["worker_job_id"].startswith("job_")
    assert gen["artifacts"] == []
    assert re.fullmatch(r"Untitled \d{4}-\d\d-\d\d \d\d:\d\d", gen["title"])
    assert await server.generation(gen["id"]) == gen


async def test_given_seed_and_title_are_kept(server: Server, worker: FakeWorker) -> None:
    gen = (await server.create(song_request(seed=42), title="  Night drive ")).json()
    assert gen["request"]["seed"] == 42
    assert gen["title"] == "Night drive"
    job = await _worker_job(worker, gen["worker_job_id"])
    assert job["params"]["seed"] == 42  # the worker runs the seed the record shows


async def _worker_job(worker: FakeWorker, job_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=worker.transport.inner,
        base_url=WORKER_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        body: dict[str, Any] = (await client.get(f"/v1/jobs/{job_id}")).json()
        return body


def test_fill_seed_reads_bounds_from_the_schema() -> None:
    op = OperationInfo(
        name="x",
        stages=[],
        params_schema={
            "properties": {
                "seed": {
                    "anyOf": [{"type": "integer", "minimum": 5, "maximum": 5}, {"type": "null"}]
                }
            }
        },
    )
    assert fill_seed({}, op) == {"seed": 5}
    assert fill_seed({"seed": None}, op) == {"seed": 5}
    assert fill_seed({"seed": 3}, op) == {"seed": 3}
    no_seed = OperationInfo(name="y", stages=[], params_schema={"properties": {"text": {}}})
    assert fill_seed({}, no_seed) == {}


async def test_over_limit_style_is_relayed_as_422_and_not_stored(server: Server) -> None:
    """AT-2 at API level."""
    response = await server.create(song_request(style="x" * 1001))
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["fields"][0]["loc"] == "request.style"
    assert (await server.request("GET", "/api/generations")).json()["items"] == []


async def test_unknown_operation_is_422(server: Server) -> None:
    response = await server.create(operation="song.cover")
    assert response.status_code == 422
    assert response.json()["error"]["details"]["operations"] == ["song.generate"]


async def test_bad_body_is_422(server: Server) -> None:
    response = await server.request("POST", "/api/generations", json={"operation": "song.generate"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_not_configured_is_409(start_server: StartServer) -> None:
    async with start_server(worker_url=None, worker_token=None) as server:
        response = await server.create()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_configured"


async def test_unreachable_worker_is_502_and_keeps_no_record(
    server: Server, worker: FakeWorker
) -> None:
    """AT-3 at API level: the UI gets ``worker_unreachable`` and nothing is stored."""
    worker.transport.down = True
    response = await server.create()
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "worker_unreachable"
    assert (await server.request("GET", "/api/generations")).json()["items"] == []


async def test_rejected_after_capabilities_keeps_no_record(
    server: Server, worker: FakeWorker
) -> None:
    await server.runtime.link.check()  # capabilities cached
    worker.transport.down = True
    response = await server.create()
    assert response.status_code == 502
    assert server.runtime.db.page(limit=10, before=None) == []


async def test_wrong_token_is_502_unauthorized(
    start_server: StartServer, worker: FakeWorker
) -> None:
    async with start_server(worker.transport, worker_token="wrong-" + "x" * 40) as server:
        response = await server.create()
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "unauthorized"


async def test_queue_full_is_429(start_server: StartServer, start_worker: StartWorker) -> None:
    """AT-6 surfaced through the server: the excess submits get ``queue_full``."""
    gate = threading.Event()
    async with (
        start_worker(FakeEngine(stage_gate=gate), max_queue=1) as worker,
        start_server(worker.transport) as server,
    ):
        first = (await server.create()).json()
        await server.wait_for(first["id"], "running", tick=True)  # one runs, the queue is empty
        codes = [(await server.create()).status_code for _ in range(3)]
        gate.set()
    assert codes == [201, 429, 429]


async def test_list_newest_first_with_cursor(server: Server) -> None:
    ids = [(await server.create()).json()["id"] for _ in range(5)]
    first = (await server.request("GET", "/api/generations", params={"limit": 2})).json()
    assert [g["id"] for g in first["items"]] == ids[::-1][:2]
    second = (
        await server.request(
            "GET", "/api/generations", params={"limit": 2, "cursor": first["next_cursor"]}
        )
    ).json()
    assert [g["id"] for g in second["items"]] == ids[::-1][2:4]
    last = (
        await server.request(
            "GET", "/api/generations", params={"limit": 2, "cursor": second["next_cursor"]}
        )
    ).json()
    assert [g["id"] for g in last["items"]] == ids[:1]
    assert last["next_cursor"] is None


async def test_list_limit_bounds(server: Server) -> None:
    assert (await server.request("GET", "/api/generations", params={"limit": 0})).status_code == 422
    assert (
        await server.request("GET", "/api/generations", params={"limit": 101})
    ).status_code == 422


async def test_unknown_generation_is_404(server: Server) -> None:
    response = await server.request("GET", "/api/generations/gen_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
