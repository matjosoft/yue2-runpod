"""Artifact manifest and download (W-5), with Range support and path safety."""

import hashlib
import json
from collections.abc import MutableMapping
from typing import Any

import pytest
import soundfile as sf
from worker_support import TOKEN, Worker


@pytest.fixture
async def done(worker: Worker) -> dict[str, Any]:
    job = (await worker.submit()).json()
    return await worker.wait_for(job["id"], "succeeded")


async def test_manifest_lists_everything_with_roles(done: dict[str, Any]) -> None:
    roles = {a["name"]: a["role"] for a in done["artifacts"]}
    assert roles == {
        "audio.flac": "primary_audio",
        "score.abc": "score",
        "plan.json": "plan",
        "semantic.npy": "intermediate",
        "request.json": "other",
        "result.json": "result",
    }
    for artifact in done["artifacts"]:
        assert set(artifact) == {"name", "role", "media_type", "size_bytes", "sha256"}


async def test_downloads_match_the_manifest(
    worker: Worker, done: dict[str, Any], tmp_path: Any
) -> None:
    for artifact in done["artifacts"]:
        response = await worker.client.get(f"/v1/jobs/{done['id']}/artifacts/{artifact['name']}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(artifact["media_type"])
        assert response.headers["accept-ranges"] == "bytes"
        assert len(response.content) == artifact["size_bytes"]
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        if artifact["name"] == "audio.flac":
            (tmp_path / "a.flac").write_bytes(response.content)
            info = sf.info(str(tmp_path / "a.flac"))
            assert (info.samplerate, info.channels) == (48000, 2)


async def test_range_request(worker: Worker, done: dict[str, Any]) -> None:
    url = f"/v1/jobs/{done['id']}/artifacts/audio.flac"
    full = (await worker.client.get(url)).content
    response = await worker.client.get(url, headers={"Range": "bytes=10-109"})
    assert response.status_code == 206
    assert response.content == full[10:110]
    assert response.headers["content-range"] == f"bytes 10-109/{len(full)}"
    tail = await worker.client.get(url, headers={"Range": "bytes=-16"})
    assert tail.status_code == 206 and tail.content == full[-16:]


@pytest.mark.parametrize(
    ("header", "status"), [("bytes=999999999-", 416), ("bytes=abc", 422), ("pages=1-2", 422)]
)
async def test_bad_ranges_use_the_envelope(
    worker: Worker, done: dict[str, Any], header: str, status: int
) -> None:
    url = f"/v1/jobs/{done['id']}/artifacts/audio.flac"
    response = await worker.client.get(url, headers={"Range": header})
    assert response.status_code == status
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "name",
    [
        "missing.flac",
        "..%2Fjob.json",
        "%2E%2E/job.json",
        "%2Fetc%2Fpasswd",
        "a%5Cb",
        "AUDIO.FLAC",
    ],
)
async def test_only_manifest_names_are_served(
    worker: Worker, done: dict[str, Any], name: str
) -> None:
    response = await worker.client.get(f"/v1/jobs/{done['id']}/artifacts/{name}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("name", ["../job.json", "./audio.flac", "x/../../job.json"])
async def test_raw_dot_segments_are_rejected(
    worker: Worker, done: dict[str, Any], name: str
) -> None:
    # httpx (and its ASGI transport) normalise dot segments, so call the ASGI app
    # directly with the raw path, the way a hostile client behind a lax proxy could.
    status, body = await raw_get(worker, f"/v1/jobs/{done['id']}/artifacts/{name}")
    assert status == 404
    assert json.loads(body)["error"]["code"] == "not_found"


async def raw_get(worker: Worker, path: str) -> tuple[int, bytes]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
        "client": ("127.0.0.1", 1),
        "server": ("worker", 80),
    }
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await worker.app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


async def test_artifacts_of_unknown_or_unfinished_jobs(worker: Worker) -> None:
    response = await worker.client.get("/v1/jobs/job_nope/artifacts/audio.flac")
    assert response.status_code == 404
    assert response.json()["error"]["details"] == {"job_id": "job_nope"}
