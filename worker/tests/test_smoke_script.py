"""scripts/smoke_worker.py (D-2) driven against the in-process fake worker."""

import argparse
import io

import httpx
import numpy as np
import pytest
import smoke_worker
import soundfile as sf
from worker_support import TOKEN, StartWorker

from studio_worker.engines.fake import FakeEngine


def _args(token: str = TOKEN, min_audio_seconds: float = 0.5) -> argparse.Namespace:
    return smoke_worker.parse_args(
        [
            "--url",
            "http://worker",
            "--token",
            token,
            "--timeout",
            "10",
            "--min-audio-seconds",
            str(min_audio_seconds),
        ]
    )


async def _run(
    start_worker: StartWorker, args: argparse.Namespace, engine: FakeEngine | None = None
) -> tuple[int, list[str]]:
    lines: list[str] = []
    async with start_worker(engine) as w:
        transport = httpx.ASGITransport(app=w.app)
        code = await smoke_worker.amain(args, transport=transport, poll_s=0.01, out=lines.append)
    return code, lines


def test_defaults_match_the_prd() -> None:
    args = smoke_worker.parse_args(["--url", "https://x", "--token", "t" * 32])
    assert args.timeout == 900.0
    assert args.min_audio_seconds == 10.0


async def test_happy_path_passes(start_worker: StartWorker) -> None:
    code, lines = await _run(start_worker, _args())
    assert code == 0, lines
    text = "\n".join(lines)
    assert lines[-1] == "PASS"
    for expected in ("healthz: ok", "model: ready", "capabilities: api 1.0", "audio.flac:"):
        assert expected in text
    for stage in ("plan", "semantic", "synthesis", "decode", "save"):
        assert stage in text
    assert 'truncated: {"abc": false, "semantic": false}' in text
    assert TOKEN not in text


async def test_wrong_token_fails_without_printing_it(start_worker: StartWorker) -> None:
    bad = "wrong-token-" + "x" * 40
    code, lines = await _run(start_worker, _args(token=bad))
    assert code == 1
    assert lines[-1] == "FAIL: GET /v1/status: the worker rejected the token (401)"
    assert all(bad not in line and TOKEN not in line for line in lines)


async def test_failed_job_fails(start_worker: StartWorker) -> None:
    code, lines = await _run(start_worker, _args(), FakeEngine(fail_at="semantic"))
    assert code == 1
    assert lines[-1].startswith("FAIL: job ended failed:")
    assert "engine_error" in lines[-1]


async def test_too_short_audio_fails(start_worker: StartWorker) -> None:
    code, lines = await _run(start_worker, _args(min_audio_seconds=10))
    assert code == 1
    assert "not longer than 10" in lines[-1]


async def test_model_load_error_fails(start_worker: StartWorker) -> None:
    lines: list[str] = []
    engine = FakeEngine(load_error=RuntimeError("no BF16"))
    async with start_worker(engine, wait_ready=False) as w:
        await w.wait_model("error")
        code = await smoke_worker.amain(
            _args(), transport=httpx.ASGITransport(app=w.app), poll_s=0.01, out=lines.append
        )
    assert code == 1
    assert lines[-1].startswith("FAIL: model failed to load:")


async def test_incompatible_api_version_fails(
    start_worker: StartWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The worker says 1.0; a script that needs major 2 must refuse it.
    monkeypatch.setattr(smoke_worker, "API_MAJOR", "2")
    code, lines = await _run(start_worker, _args())
    assert code == 1
    assert lines[-1] == "FAIL: incompatible api_version 1.0 (need 2.x)"


@pytest.mark.parametrize("missing", ["score.abc", "result.json"])
def test_score_and_result_are_required(missing: str) -> None:
    """AT-13: score.abc and result.json must come back with the audio."""
    buf = io.BytesIO()
    sf.write(buf, np.zeros((48000 * 2, 2), dtype=np.float32), 48000, format="FLAC")
    files = {"audio.flac": buf.getvalue(), "score.abc": b"X:1", "result.json": b"{}"}
    del files[missing]
    with pytest.raises(smoke_worker.SmokeFailure, match=f"no {missing}"):
        smoke_worker.check_outputs(files, min_audio_s=1.0, out=lambda line: None)
