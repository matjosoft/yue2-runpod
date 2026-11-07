"""Yue2Engine against the ``yue2`` stub (tests/yue2_stub.py). No GPU and no real package.

These prove the engine drives the API as recorded in docs/yue2-api-notes.md: the right
calls, kwargs, artifact roles and error translation. They do not prove real-model behaviour.
"""

import asyncio
import hashlib
import json
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import soundfile as sf
import yue2_stub
from worker_support import AUTH, TOKEN, song_params

from studio_contracts import ArtifactRole, SongParams
from studio_worker.config import WorkerSettings
from studio_worker.engines.base import ArtifactSpec, EngineOOM, SongEngine
from studio_worker.engines.factory import create_engine
from studio_worker.engines.yue2 import Yue2Engine, artifact_type
from studio_worker.main import create_app


@pytest.fixture
def stub() -> Iterator[yue2_stub.StubState]:
    with yue2_stub.installed() as state:
        yield state


def _params(**overrides: Any) -> SongParams:
    values: dict[str, Any] = {"style": "jazz", "lyrics": "[Verse]\nla", "seed": 7}
    values.update(overrides)
    return SongParams(**values)


def _loaded(**kwargs: Any) -> Yue2Engine:
    engine = Yue2Engine(**kwargs)
    engine.load()
    return engine


def _run_staged(engine: Yue2Engine, p: SongParams, out: Path) -> list[ArtifactSpec]:
    plan = engine.plan(p)
    semantic = engine.generate_semantic(plan)
    latents = engine.synthesize(semantic)
    audio = engine.decode(latents)
    return engine.write_artifacts(out, plan=plan, semantic=semantic, latents=latents, audio=audio)


class _OutOfMemoryError(RuntimeError):
    """Named like ``torch.cuda.OutOfMemoryError``; the engine never imports torch."""


_OutOfMemoryError.__name__ = "OutOfMemoryError"


# --- Step 1: the stub itself -------------------------------------------------------------


def test_stub_writes_the_documented_files(stub: yue2_stub.StubState, tmp_path: Path) -> None:
    pipe = yue2_stub.YuE2Pipeline.from_pretrained()
    pipe(style="s", lyrics="l", seed=1).save_artifacts(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == yue2_stub.DOCUMENTED_FILES


# --- Step 2: load, info, close -----------------------------------------------------------


def test_is_a_song_engine_and_does_not_import_yue2_until_load() -> None:
    sys.modules.pop("yue2", None)
    engine = Yue2Engine()
    assert isinstance(engine, SongEngine)
    assert engine.kind == "yue2"
    assert engine.supports_staged is True
    assert "yue2" not in sys.modules


def test_load_passes_the_documented_kwargs(stub: yue2_stub.StubState) -> None:
    _loaded(
        model_repo="m-a-p/YuE2-3B",
        model_revision="abc",
        vae_revision="def",
        hf_token="hf_secret",
        hf_home=Path("/workspace/hf"),
    )
    assert stub.names() == ["from_pretrained"]
    kw = stub.kwargs("from_pretrained")
    assert kw["model"] == "m-a-p/YuE2-3B"
    assert kw["vae"] == "m-a-p/YuE2-Vae"  # the package default: not passed
    assert kw["revision"] == "abc"
    assert kw["vae_revision"] == "def"
    assert kw["token"] == "hf_secret"
    assert kw["cache_dir"] == str(Path("/workspace/hf/hub"))
    assert kw["device"] == "cuda"
    assert kw["progress"] is False
    assert "verify_hashes" not in kw  # left at the package default, True


def test_load_passes_a_vae_repo_when_set(stub: yue2_stub.StubState) -> None:
    _loaded(vae_repo="m-a-p/YuE2-Vae-legacy")
    assert stub.kwargs("from_pretrained")["vae"] == "m-a-p/YuE2-Vae-legacy"


def test_load_error_propagates(stub: yue2_stub.StubState) -> None:
    stub.fail_at["from_pretrained"] = RuntimeError("The unquantized preset requires CUDA BF16")
    with pytest.raises(RuntimeError, match="BF16"):
        Yue2Engine().load()


def test_info_before_load_and_after(stub: yue2_stub.StubState) -> None:
    engine = Yue2Engine(model_revision="8f24312")
    before = engine.info()
    assert before.kind == "yue2"
    assert before.models == {"lm": "m-a-p/YuE2-3B@8f24312", "vae": "m-a-p/YuE2-Vae"}
    engine.load()
    assert engine.info().package_version == "0.1.5"


def test_info_without_the_package_says_unknown() -> None:
    sys.modules.pop("yue2", None)
    assert Yue2Engine().info().package_version == "unknown"


def test_close_is_idempotent(stub: yue2_stub.StubState) -> None:
    Yue2Engine().close()  # before load: nothing to do
    engine = _loaded()
    engine.close()
    engine.close()
    assert stub.names().count("close") == 1


# --- Step 3: staged calls, write_artifacts, generate ------------------------------------


def test_staged_calls_and_kwargs(stub: yue2_stub.StubState, tmp_path: Path) -> None:
    engine = _loaded()
    _run_staged(engine, _params(seed=42, cot="melody"), tmp_path)
    assert stub.names() == [
        "from_pretrained",
        "plan",
        "generate_semantic",
        "synthesize",
        "decode",
        "save_artifacts",
    ]
    plan_kw = stub.kwargs("plan")
    assert plan_kw["style"] == "jazz" and plan_kw["lyrics"] == "[Verse]\nla"
    assert plan_kw["seed"] == 42 and type(plan_kw["seed"]) is int
    assert plan_kw["cot"] == "melody"
    assert "cfg_scale" not in plan_kw  # absent means the model default
    assert stub.kwargs("generate_semantic")["plan"].request.seed == 42


def test_cfg_scale_is_passed_when_set(stub: yue2_stub.StubState, tmp_path: Path) -> None:
    engine = _loaded()
    _run_staged(engine, _params(cfg_scale=1.5), tmp_path)
    assert stub.kwargs("plan")["cfg_scale"] == 1.5
    config = (tmp_path / "config.json").read_text()
    assert '"cfg_scale": 1.5' in config


def test_seed_must_be_resolved(stub: yue2_stub.StubState) -> None:
    with pytest.raises(ValueError, match="seed"):
        _loaded().plan(_params(seed=None))


EXPECTED_TYPES = {
    "audio.flac": (ArtifactRole.primary_audio, "audio/flac"),
    "score.abc": (ArtifactRole.score, "text/vnd.abc"),
    "plan.json": (ArtifactRole.plan, "application/json"),
    "result.json": (ArtifactRole.result, "application/json"),
    "abc_tokens.npy": (ArtifactRole.intermediate, "application/octet-stream"),
    "prefix.npy": (ArtifactRole.intermediate, "application/octet-stream"),
    "semantic.npy": (ArtifactRole.intermediate, "application/octet-stream"),
    "latent.npy": (ArtifactRole.intermediate, "application/octet-stream"),
    "request.json": (ArtifactRole.other, "application/json"),
    "config.json": (ArtifactRole.other, "application/json"),
    "plan_manifest.json": (ArtifactRole.other, "application/json"),
    "engine.json": (ArtifactRole.other, "application/json"),
}


def test_write_artifacts_reports_every_file_with_its_role(
    stub: yue2_stub.StubState, tmp_path: Path
) -> None:
    specs = _run_staged(_loaded(), _params(), tmp_path)
    assert {s.relpath: (s.role, s.media_type) for s in specs} == EXPECTED_TYPES
    assert {p.name for p in tmp_path.iterdir()} == set(EXPECTED_TYPES)
    assert set(EXPECTED_TYPES) == yue2_stub.DOCUMENTED_FILES | {"engine.json"}
    info = sf.info(tmp_path / "audio.flac")
    assert (info.samplerate, info.channels) == (48000, 2)


def test_write_artifacts_rebuilds_the_song_result_like_call(
    stub: yue2_stub.StubState, tmp_path: Path
) -> None:
    """Same identity and result fields as ``pipe(...)`` (docs: Staged reconstruction)."""
    _run_staged(_loaded(), _params(seed=5), tmp_path / "staged")
    pipe = yue2_stub.YuE2Pipeline.from_pretrained()
    pipe(style="jazz", lyrics="[Verse]\nla", seed=5, cot="full").save_artifacts(tmp_path / "call")
    staged = json.loads((tmp_path / "staged" / "result.json").read_text())
    call = json.loads((tmp_path / "call" / "result.json").read_text())
    assert staged["identity"] == call["identity"]
    assert staged["truncated"] == {"abc": False, "semantic": False}
    assert set(staged["timing"]) == set(call["timing"])
    assert staged["sample_rate"] == 48000


def test_engine_file_records_the_package_version(stub: yue2_stub.StubState, tmp_path: Path) -> None:
    """PRD section 11: the wheel version is recorded in the artifacts."""
    _run_staged(_loaded(model_revision="r1"), _params(), tmp_path / "staged")
    _loaded().generate(_params(), tmp_path / "single")
    staged = json.loads((tmp_path / "staged" / "engine.json").read_text())
    assert staged == {
        "kind": "yue2",
        "package_version": "0.1.5",
        "models": {"lm": "m-a-p/YuE2-3B@r1", "vae": "m-a-p/YuE2-Vae"},
    }
    assert (tmp_path / "single" / "engine.json").is_file()


def test_cot_off_has_no_score(stub: yue2_stub.StubState, tmp_path: Path) -> None:
    specs = _run_staged(_loaded(), _params(cot="off"), tmp_path)
    assert "score.abc" not in {s.relpath for s in specs}


def test_generate_single_call(stub: yue2_stub.StubState, tmp_path: Path) -> None:
    specs = _loaded().generate(_params(seed=9, cfg_scale=2.0), tmp_path)
    kw = stub.kwargs("__call__")
    assert kw["seed"] == 9 and kw["cfg_scale"] == 2.0 and kw["cot"] == "full"
    assert {s.relpath for s in specs} == set(EXPECTED_TYPES)


def test_unknown_files_are_other() -> None:
    assert artifact_type("extra.wav") == (ArtifactRole.other, "application/octet-stream")
    assert artifact_type("new.npy") == (ArtifactRole.intermediate, "application/octet-stream")


# --- Step 4: error translation ------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [_OutOfMemoryError("CUDA error"), RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")],
)
@pytest.mark.parametrize("call", ["plan", "generate_semantic", "synthesize", "decode"])
def test_oom_becomes_engine_oom(
    stub: yue2_stub.StubState, tmp_path: Path, exc: Exception, call: str
) -> None:
    stub.fail_at[call] = exc
    with pytest.raises(EngineOOM) as info:
        _run_staged(_loaded(), _params(), tmp_path)
    assert info.value.__cause__ is exc


@pytest.mark.parametrize("exc", [ValueError("context overflow"), FloatingPointError("nan")])
def test_other_errors_propagate_unchanged(
    stub: yue2_stub.StubState, tmp_path: Path, exc: Exception
) -> None:
    stub.fail_at["generate_semantic"] = exc
    with pytest.raises(type(exc)) as info:
        _run_staged(_loaded(), _params(), tmp_path)
    assert info.value is exc


# --- Step 5: factory and the worker app ---------------------------------------------------


def test_factory_builds_yue2_from_settings(tmp_path: Path) -> None:
    sys.modules.pop("yue2", None)
    settings = WorkerSettings(
        api_key=TOKEN,
        engine="yue2",
        yue2_model_repo="org/model",
        yue2_vae_repo="org/vae",
        yue2_model_revision="r1",
        yue2_vae_revision="r2",
        hf_home=tmp_path / "hf",
        hf_token="hf_x",
    )
    engine = create_engine(settings)
    assert isinstance(engine, Yue2Engine)
    assert engine.info().models == {"lm": "org/model@r1", "vae": "org/vae@r2"}
    assert "yue2" not in sys.modules


@asynccontextmanager
async def _yue2_worker(jobs_dir: Path) -> AsyncIterator[httpx.AsyncClient]:
    settings = WorkerSettings(api_key=TOKEN, engine="fake", jobs_dir=jobs_dir)
    app = create_app(settings, Yue2Engine())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://worker", headers=AUTH
        ) as client:
            yield client


async def _wait(client: httpx.AsyncClient, path: str, done: set[str], key: str) -> dict[str, Any]:
    for _ in range(1000):
        body: dict[str, Any] = (await client.get(path)).json()
        value = body[key]["state"] if key == "model" else body[key]
        if value in done:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError(f"{path} stuck: {body}")


async def _submit(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/v1/jobs",
        json={
            "client_request_id": str(uuid.uuid4()),
            "operation": "song.generate",
            "params": song_params(),
        },
    )
    assert response.status_code == 202, response.text
    job: dict[str, Any] = response.json()
    return job


async def test_worker_runs_a_job_through_yue2_engine(
    stub: yue2_stub.StubState, tmp_path: Path
) -> None:
    async with _yue2_worker(tmp_path / "jobs") as client:
        await _wait(client, "/v1/status", {"ready"}, "model")
        caps = (await client.get("/v1/capabilities")).json()
        assert caps["engine"]["kind"] == "yue2"
        job = await _submit(client)
        assert job["stages"] == ["plan", "semantic", "synthesis", "decode", "save"]
        job = await _wait(client, f"/v1/jobs/{job['id']}", {"succeeded", "failed"}, "state")
        assert job["state"] == "succeeded", job["error"]
        assert isinstance(job["params_resolved"]["seed"], int)
        manifest = {a["name"]: a for a in job["artifacts"]}
        assert {n: (manifest[n]["role"], manifest[n]["media_type"]) for n in manifest} == {
            n: (role.value, media) for n, (role, media) in EXPECTED_TYPES.items()
        }
        for name, art in manifest.items():
            data = (await client.get(f"/v1/jobs/{job['id']}/artifacts/{name}")).content
            assert hashlib.sha256(data).hexdigest() == art["sha256"]
            assert len(data) == art["size_bytes"]


async def test_oom_fails_the_job_and_the_worker_takes_the_next(
    stub: yue2_stub.StubState, tmp_path: Path
) -> None:
    """GPU-free analogue of AT-14."""
    stub.fail_at["generate_semantic"] = _OutOfMemoryError("CUDA out of memory")
    async with _yue2_worker(tmp_path / "jobs") as client:
        await _wait(client, "/v1/status", {"ready"}, "model")
        job = await _submit(client)
        job = await _wait(client, f"/v1/jobs/{job['id']}", {"succeeded", "failed"}, "state")
        assert job["state"] == "failed"
        assert job["error"]["code"] == "oom"
        assert job["artifacts"] == []
        del stub.fail_at["generate_semantic"]
        job = await _submit(client)
        job = await _wait(client, f"/v1/jobs/{job['id']}", {"succeeded", "failed"}, "state")
        assert job["state"] == "succeeded"


async def test_engine_errors_fail_the_job_as_engine_error(
    stub: yue2_stub.StubState, tmp_path: Path
) -> None:
    stub.fail_at["decode"] = ValueError("Decoded audio is not finite")
    async with _yue2_worker(tmp_path / "jobs") as client:
        await _wait(client, "/v1/status", {"ready"}, "model")
        job = await _submit(client)
        job = await _wait(client, f"/v1/jobs/{job['id']}", {"succeeded", "failed"}, "state")
        assert job["error"]["code"] == "engine_error"


async def test_load_failure_is_model_error_and_submit_is_503(
    stub: yue2_stub.StubState, tmp_path: Path
) -> None:
    stub.fail_at["from_pretrained"] = RuntimeError("The unquantized preset requires CUDA BF16")
    async with _yue2_worker(tmp_path / "jobs") as client:
        status = await _wait(client, "/v1/status", {"error"}, "model")
        assert "BF16" in status["model"]["error"]
        response = await client.post(
            "/v1/jobs",
            json={
                "client_request_id": str(uuid.uuid4()),
                "operation": "song.generate",
                "params": song_params(),
            },
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "not_ready"
        assert "Retry-After" in response.headers


# --- Step 7: intermediate sizes at the largest documented shapes -------------------------


def test_intermediates_at_max_shape_are_far_below_100_mb(tmp_path: Path) -> None:
    """Shapes and dtypes from docs/yue2-api-notes.md (OQ-3, OQ-4): 9000 semantic tokens
    (one latent frame each, 64 float32 values), up to 4096 ABC tokens, and a prefix of at
    most 24576 - 9000 = 15576 tokens."""
    arrays = {
        "latent.npy": np.zeros((9000, 64), dtype=np.float32),
        "semantic.npy": np.zeros(9000, dtype=np.int32),
        "abc_tokens.npy": np.zeros(4096, dtype=np.int32),
        "prefix.npy": np.zeros(15576, dtype=np.int32),
    }
    sizes = {}
    for name, array in arrays.items():
        np.save(tmp_path / name, array)
        sizes[name] = (tmp_path / name).stat().st_size
    assert sizes == {
        "latent.npy": 2_304_128,
        "semantic.npy": 36_128,
        "abc_tokens.npy": 16_512,
        "prefix.npy": 62_432,
    }
    assert sum(sizes.values()) < 100_000_000
