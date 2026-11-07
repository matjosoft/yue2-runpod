"""Artifact serving with Range (S-7) and deleting generations (S-4)."""

import threading
from typing import Any

from server_support import FakeWorker, Server, StartServer, StartWorker

from studio_worker.engines.fake import FakeEngine


async def finished(server: Server) -> dict[str, Any]:
    gen = (await server.create()).json()
    return await server.wait_for(gen["id"], "succeeded", tick=True)


def artifact(gen: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = next(a for a in gen["artifacts"] if a["name"] == name)
    return found


async def test_full_download(server: Server) -> None:
    gen = await finished(server)
    flac = artifact(gen, "audio.flac")
    assert flac["url"] == f"/api/generations/{gen['id']}/artifacts/audio.flac"
    response = await server.request("GET", flac["url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/flac"
    assert response.headers["accept-ranges"] == "bytes"
    assert len(response.content) == flac["size_bytes"]


async def test_range_requests(server: Server) -> None:
    """S-7: seeking in the player issues Range requests."""
    gen = await finished(server)
    flac = artifact(gen, "audio.flac")
    full = (await server.request("GET", flac["url"])).content
    size = len(full)

    part = await server.request("GET", flac["url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 0-99/{size}"
    assert part.content == full[:100]

    tail = await server.request("GET", flac["url"], headers={"Range": "bytes=-10"})
    assert tail.status_code == 206
    assert tail.content == full[-10:]

    middle = await server.request("GET", flac["url"], headers={"Range": f"bytes={size - 5}-"})
    assert middle.content == full[-5:]


async def test_bad_ranges(server: Server) -> None:
    gen = await finished(server)
    url = artifact(gen, "audio.flac")["url"]
    size = artifact(gen, "audio.flac")["size_bytes"]
    unsatisfiable = await server.request("GET", url, headers={"Range": f"bytes={size + 10}-"})
    assert unsatisfiable.status_code == 416
    assert unsatisfiable.headers["content-range"] == f"bytes */{size}"
    assert unsatisfiable.json()["error"]["code"] == "validation_error"
    malformed = await server.request("GET", url, headers={"Range": "pages=1-2"})
    assert malformed.status_code == 422


async def test_only_manifest_names_are_served(server: Server) -> None:
    gen = await finished(server)
    base = f"/api/generations/{gen['id']}/artifacts"
    for name in ("nope.txt", "../../studio.db", "..%2F..%2Fstudio.db", ".partial/x"):
        response = await server.request("GET", f"{base}/{name}")
        assert response.status_code == 404, name
        assert response.json()["error"]["code"] == "not_found"


async def test_artifact_of_unknown_generation(server: Server) -> None:
    response = await server.request("GET", "/api/generations/gen_x/artifacts/audio.flac")
    assert response.status_code == 404


async def test_delete_removes_files_and_remote_job(server: Server, worker: FakeWorker) -> None:
    """S-4: the generation's files are gone from disk; the worker job is deleted too."""
    gen = await finished(server)
    gen_dir = server.settings.data_dir / "generations" / gen["id"]
    assert gen_dir.is_dir()
    response = await server.request("DELETE", f"/api/generations/{gen['id']}")
    assert response.status_code == 204
    assert not gen_dir.exists()
    assert (await server.request("GET", f"/api/generations/{gen['id']}")).status_code == 404
    assert not (worker.jobs_dir / gen["worker_job_id"]).exists()
    assert (await server.request("DELETE", f"/api/generations/{gen['id']}")).status_code == 404


async def test_delete_works_when_worker_is_gone(server: Server, worker: FakeWorker) -> None:
    gen = await finished(server)
    worker.transport.down = True
    assert (await server.request("DELETE", f"/api/generations/{gen['id']}")).status_code == 204
    assert not (server.settings.data_dir / "generations" / gen["id"]).exists()


async def test_delete_in_progress_is_409(
    start_server: StartServer, start_worker: StartWorker
) -> None:
    gate = threading.Event()
    async with (
        start_worker(FakeEngine(stage_gate=gate)) as worker,
        start_server(worker.transport) as server,
    ):
        gen = (await server.create()).json()
        response = await server.request("DELETE", f"/api/generations/{gen['id']}")
        gate.set()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
