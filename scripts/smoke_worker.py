#!/usr/bin/env python3
"""Smoke-test a worker end to end with one real generation (PRD section 12, D-2).

    uv run --locked python scripts/smoke_worker.py --url <url> --token <token> [--timeout 900]

Steps, stopping with a non-zero exit at the first failure:

1. ``GET /healthz``, then wait for ``/v1/status`` to show ``model.state`` = ``ready``.
2. ``/v1/capabilities`` lists ``song.generate`` and a compatible ``api_version``.
3. Submit a short two-section song with a fixed seed.
4. Poll to a terminal state, printing each stage change and its time.
5. Download every artifact and verify its size and sha256.
6. ``audio.flac`` is 48 kHz stereo and longer than ``--min-audio-seconds`` (default 10);
   ``score.abc`` and ``result.json`` exist (AT-13).
7. Print timings, total artifact size and the ``truncated`` flags from ``result.json``.

``--timeout`` bounds each wait separately: the model warm-up and the job. The token is
never printed.
"""

import argparse
import asyncio
import hashlib
import io
import json
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import soundfile as sf

API_MAJOR = "1"
OPERATION = "song.generate"
SEED = 12345
STYLE = "English, pop, warm female vocal, piano, bass and drums, 100 bpm"
# Two sections, but enough lines that the song should run well past the 10 s check.
LYRICS = (
    "[Verse]\n"
    "Morning light across the floor\n"
    "Coffee waiting by the door\n"
    "Radio is playing low\n"
    "Every song I used to know\n"
    "\n"
    "[Chorus]\n"
    "Here we go, here we go again\n"
    "Singing with the summer rain\n"
    "Here we go, here we go again\n"
    "Dancing till the morning came\n"
)
TERMINAL = {"succeeded", "failed", "canceled"}
SAMPLE_RATE = 48000

Out = Callable[[str], None]


class SmokeFailure(Exception):
    """One step failed; the message says which and why."""


def _error_text(response: httpx.Response) -> str:
    try:
        error = response.json()["error"]
        return f"{error['code']}: {error['message']}"
    except (ValueError, KeyError, TypeError):
        return response.text[:200]


def _expect(response: httpx.Response, status: int, what: str) -> Any:
    if response.status_code == 401:
        raise SmokeFailure(f"{what}: the worker rejected the token (401)")
    if response.status_code != status:
        raise SmokeFailure(f"{what}: HTTP {response.status_code} ({_error_text(response)})")
    return (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else None
    )


async def _get_json(client: httpx.AsyncClient, path: str, what: str) -> Any:
    return _expect(await client.get(path), 200, what)


async def wait_ready(
    client: httpx.AsyncClient, *, timeout_s: float, poll_s: float, out: Out
) -> None:
    _expect(await client.get("/healthz"), 200, "GET /healthz")
    out("healthz: ok")
    started = time.monotonic()
    last_state = None
    while True:
        try:
            status = await _get_json(client, "/v1/status", "GET /v1/status")
        except httpx.TransportError as exc:  # flaky proxy: keep waiting
            out(f"status: network error ({type(exc).__name__}), retrying")
            status = None
        if status is not None:
            model = status["model"]
            if model["state"] != last_state:
                last_state = model["state"]
                out(f"model: {last_state} after {time.monotonic() - started:.0f} s")
            if model["state"] == "ready":
                if status.get("gpu"):
                    gpu = status["gpu"]
                    out(f"gpu: {gpu['name']}, {gpu['vram_used_mb']}/{gpu['vram_total_mb']} MiB")
                return
            if model["state"] == "error":
                raise SmokeFailure(f"model failed to load: {model['error']}")
        if time.monotonic() - started > timeout_s:
            raise SmokeFailure(f"model not ready after {timeout_s:.0f} s (state {last_state})")
        await asyncio.sleep(poll_s)


async def check_capabilities(client: httpx.AsyncClient, out: Out) -> None:
    caps = await _get_json(client, "/v1/capabilities", "GET /v1/capabilities")
    major = str(caps["api_version"]).split(".")[0]
    if major != API_MAJOR:
        raise SmokeFailure(f"incompatible api_version {caps['api_version']} (need {API_MAJOR}.x)")
    names = [op["name"] for op in caps["operations"]]
    if OPERATION not in names:
        raise SmokeFailure(f"capabilities do not list {OPERATION} (have {names})")
    engine = caps["engine"]
    out(
        f"capabilities: api {caps['api_version']}, worker {caps['worker_version']}, "
        f"engine {engine['kind']} {engine['package_version']} {engine['models']}"
    )


async def submit(client: httpx.AsyncClient, out: Out) -> dict[str, Any]:
    body = {
        "client_request_id": str(uuid.uuid4()),
        "operation": OPERATION,
        "params": {"style": STYLE, "lyrics": LYRICS, "seed": SEED, "cot": "full"},
    }
    job: dict[str, Any] = _expect(await client.post("/v1/jobs", json=body), 202, "POST /v1/jobs")
    out(f"submitted: {job['id']} (seed {SEED}, stages {job['stages']})")
    return job


async def wait_job(
    client: httpx.AsyncClient, job_id: str, *, timeout_s: float, poll_s: float, out: Out
) -> dict[str, Any]:
    started = time.monotonic()
    seen: tuple[str, str | None] | None = None
    while True:
        try:
            job: dict[str, Any] = await _get_json(client, f"/v1/jobs/{job_id}", "GET job")
        except httpx.TransportError as exc:
            out(f"job: network error ({type(exc).__name__}), retrying")
        else:
            now = (job["state"], job["stage"])
            if now != seen:
                seen = now
                out(
                    f"  {time.monotonic() - started:7.1f} s  {job['state']:<9} {job['stage'] or ''}"
                )
            if job["state"] in TERMINAL:
                if job["state"] != "succeeded":
                    raise SmokeFailure(f"job ended {job['state']}: {job['error']}")
                return job
        if time.monotonic() - started > timeout_s:
            raise SmokeFailure(f"job not finished after {timeout_s:.0f} s")
        await asyncio.sleep(poll_s)


async def download_all(
    client: httpx.AsyncClient, job: dict[str, Any], out: Out
) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for art in job["artifacts"]:
        name = art["name"]
        response = await client.get(f"/v1/jobs/{job['id']}/artifacts/{name}")
        _expect(response, 200, f"download {name}")
        data = response.content
        if len(data) != art["size_bytes"]:
            raise SmokeFailure(f"{name}: {len(data)} bytes, manifest says {art['size_bytes']}")
        if hashlib.sha256(data).hexdigest() != art["sha256"]:
            raise SmokeFailure(f"{name}: sha256 does not match the manifest")
        files[name] = data
        out(f"  ok  {name:<20} {art['role']:<14} {len(data):>12,} B")
    return files


def check_outputs(files: dict[str, bytes], *, min_audio_s: float, out: Out) -> None:
    if "audio.flac" not in files:
        raise SmokeFailure("no audio.flac in the artifacts")
    info = sf.info(io.BytesIO(files["audio.flac"]))
    out(f"audio.flac: {info.samplerate} Hz, {info.channels} ch, {info.duration:.1f} s")
    if info.samplerate != SAMPLE_RATE or info.channels != 2:
        raise SmokeFailure(
            f"audio.flac is {info.samplerate} Hz / {info.channels} ch, not 48 kHz stereo"
        )
    if info.duration <= min_audio_s:
        raise SmokeFailure(f"audio.flac is {info.duration:.1f} s, not longer than {min_audio_s} s")
    for name in ("score.abc", "result.json"):
        if name not in files:
            raise SmokeFailure(f"no {name} in the artifacts")


def report(job: dict[str, Any], files: dict[str, bytes], out: Out) -> None:
    out(f"timings: {json.dumps(job['timings'])}")
    out(f"artifacts: {len(files)} files, {sum(len(d) for d in files.values()):,} B in total")
    truncated: object = "unknown (no result.json)"
    if "result.json" in files:
        try:
            truncated = json.loads(files["result.json"]).get("truncated", "unknown")
        except ValueError:
            truncated = "unknown (result.json is not JSON)"
    out(f"truncated: {json.dumps(truncated)}")


async def run_smoke(
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
    min_audio_s: float,
    poll_s: float = 2.0,
    out: Out = print,
) -> None:
    await wait_ready(client, timeout_s=timeout_s, poll_s=poll_s, out=out)
    await check_capabilities(client, out)
    job = await submit(client, out)
    job = await wait_job(client, job["id"], timeout_s=timeout_s, poll_s=poll_s, out=out)
    files = await download_all(client, job, out)
    check_outputs(files, min_audio_s=min_audio_s, out=out)
    report(job, files, out)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--url", required=True, help="worker base URL")
    parser.add_argument("--token", required=True, help="WORKER_API_KEY")
    parser.add_argument(
        "--timeout", type=float, default=900.0, help="seconds for each wait (default 900)"
    )
    parser.add_argument(
        "--min-audio-seconds",
        type=float,
        default=10.0,
        help="audio.flac must be longer than this (default 10; lower it for a fake worker)",
    )
    return parser.parse_args(argv)


async def amain(
    args: argparse.Namespace,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    poll_s: float = 2.0,
    out: Out = print,
) -> int:
    headers = {"Authorization": f"Bearer {args.token}"}
    async with httpx.AsyncClient(
        base_url=args.url.rstrip("/"), headers=headers, timeout=60.0, transport=transport
    ) as client:
        try:
            await run_smoke(
                client,
                timeout_s=args.timeout,
                min_audio_s=args.min_audio_seconds,
                poll_s=poll_s,
                out=out,
            )
        except SmokeFailure as exc:
            out(f"FAIL: {exc}")
            return 1
        except httpx.HTTPError as exc:
            out(f"FAIL: network error talking to the worker ({type(exc).__name__})")
            return 1
    out("PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(amain(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
