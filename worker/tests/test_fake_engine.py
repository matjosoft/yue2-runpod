import json
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from studio_contracts import ArtifactRole, SongParams
from studio_worker.engines.base import Engine, EngineOOM, SongEngine
from studio_worker.engines.fake import FakeEngine


def params(**kw: object) -> SongParams:
    return SongParams.model_validate({"style": "s", "lyrics": "l", "seed": 7, **kw})


def run_staged(engine: FakeEngine, p: SongParams, out: Path) -> list[str]:
    plan = engine.plan(p)
    semantic = engine.generate_semantic(plan)
    latents = engine.synthesize(semantic)
    audio = engine.decode(latents)
    specs = engine.write_artifacts(out, plan=plan, semantic=semantic, latents=latents, audio=audio)
    return sorted(s.relpath for s in specs)


def test_is_a_song_engine() -> None:
    engine = FakeEngine()
    assert isinstance(engine, Engine)
    assert isinstance(engine, SongEngine)
    assert engine.info().kind == "fake"


def test_staged_run_writes_a_48k_stereo_flac(tmp_path: Path) -> None:
    engine = FakeEngine(audio_seconds=1.0)
    names = run_staged(engine, params(), tmp_path)
    assert names == [
        "audio.flac",
        "plan.json",
        "request.json",
        "result.json",
        "score.abc",
        "semantic.npy",
    ]
    info = sf.info(str(tmp_path / "audio.flac"))
    assert (info.samplerate, info.channels, info.format, info.subtype) == (
        48000,
        2,
        "FLAC",
        "PCM_24",
    )
    assert info.duration == pytest.approx(1.0)
    data, _ = sf.read(str(tmp_path / "audio.flac"))
    assert np.abs(data).max() > 0.1  # not silence
    assert json.loads((tmp_path / "request.json").read_text())["seed"] == 7
    assert engine.calls == ["plan", "semantic", "synthesis", "decode", "save"]


def test_single_call_writes_the_same_files(tmp_path: Path) -> None:
    engine = FakeEngine(staged=False)
    specs = engine.generate(params(), tmp_path / "single")
    staged = run_staged(FakeEngine(), params(), tmp_path / "staged")
    assert sorted(s.relpath for s in specs) == staged
    assert engine.calls == ["generate"]
    roles = {s.relpath: s.role for s in specs}
    assert roles["audio.flac"] is ArtifactRole.primary_audio
    assert roles["semantic.npy"] is ArtifactRole.intermediate
    assert sf.info(str(tmp_path / "single" / "audio.flac")).samplerate == 48000


def test_no_score_when_cot_is_off(tmp_path: Path) -> None:
    names = run_staged(FakeEngine(), params(cot="off"), tmp_path)
    assert "score.abc" not in names


@pytest.mark.parametrize("staged", [True, False])
def test_a_supplied_score_is_used_verbatim(tmp_path: Path, staged: bool) -> None:
    score = "X:1\nK:D\n|: d2 fd A2 FA :|\n"
    p = params(cot="melody", abc=score)
    if staged:
        run_staged(FakeEngine(), p, tmp_path)
    else:
        FakeEngine(staged=False).generate(p, tmp_path)
    assert (tmp_path / "score.abc").read_text(encoding="utf-8") == score
    assert json.loads((tmp_path / "request.json").read_text())["abc"] == score


def test_load_hooks() -> None:
    engine = FakeEngine(load_error=RuntimeError("no BF16"))
    with pytest.raises(RuntimeError, match="BF16"):
        engine.load()
    released = threading.Event()
    engine = FakeEngine(load_gate=released)
    thread = threading.Thread(target=engine.load)
    thread.start()
    thread.join(0.05)
    assert not engine.loaded
    released.set()
    thread.join(5)
    assert engine.loaded


def test_fail_at_stage(tmp_path: Path) -> None:
    engine = FakeEngine(fail_at="synthesis", fail_with=EngineOOM("CUDA out of memory"))
    with pytest.raises(EngineOOM):
        run_staged(engine, params(), tmp_path)
    assert engine.calls == ["plan", "semantic", "synthesis"]
