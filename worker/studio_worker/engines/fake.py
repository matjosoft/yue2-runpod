"""FakeEngine: a GPU-free ``SongEngine`` for tests and local development (W-9).

It walks the same staged calls as the real engine and writes a short, valid 48 kHz
stereo FLAC plus small stand-ins for the other files ``save_artifacts`` writes. The
constructor arguments are test hooks: gates block a call until released, delays slow
it down, and ``fail_at`` makes one stage raise.
"""

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import soundfile as sf

from studio_contracts import ArtifactRole, EngineInfo, SongParams
from studio_worker import __version__
from studio_worker.engines.base import ArtifactSpec

SAMPLE_RATE = 48_000
FRAMES_PER_SECOND = 25  # 48000 / 1920, as in yue2 (docs/yue2-api-notes.md, OQ-3)
LATENT_DIM = 64
GATE_TIMEOUT_S = 30.0


@dataclass
class FakePlan:
    params: SongParams
    abc: str | None


@dataclass
class FakeSemantic:
    plan: FakePlan
    tokens: list[int] = field(default_factory=list)


class FakeEngine:
    kind = "fake"

    def __init__(
        self,
        *,
        staged: bool = True,
        audio_seconds: float = 1.0,
        load_delay_s: float = 0.0,
        stage_delay_s: float = 0.0,
        load_gate: threading.Event | None = None,
        stage_gate: threading.Event | None = None,
        load_error: Exception | None = None,
        fail_at: str | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.supports_staged = staged
        self.audio_seconds = audio_seconds
        self.load_delay_s = load_delay_s
        self.stage_delay_s = stage_delay_s
        self.load_gate = load_gate
        self.stage_gate = stage_gate
        self.load_error = load_error
        self.fail_at = fail_at
        self.fail_with = fail_with
        self.loaded = False
        self.closed = False
        self.calls: list[str] = []
        """Stage names in call order, across all jobs."""
        self.max_concurrent = 0
        """Most engine calls ever in flight at once. Must stay at most 1 (W-3)."""
        self._active = 0
        self._lock = threading.Lock()

    # --- Engine -----------------------------------------------------------------------

    def load(self) -> None:
        if self.load_gate is not None and not self.load_gate.wait(GATE_TIMEOUT_S):
            raise TimeoutError("fake load gate was never released")
        if self.load_delay_s:
            time.sleep(self.load_delay_s)
        if self.load_error is not None:
            raise self.load_error
        self.loaded = True

    def info(self) -> EngineInfo:
        return EngineInfo(
            kind=self.kind, package_version=__version__, models={"lm": "fake", "vae": "fake"}
        )

    def close(self) -> None:
        self.closed = True

    # --- SongEngine -------------------------------------------------------------------

    def plan(self, p: SongParams) -> FakePlan:
        with self._step("plan"):
            abc = None if p.cot == "off" else _fake_abc(p)
            return FakePlan(params=p, abc=abc)

    def generate_semantic(self, plan: Any) -> FakeSemantic:
        with self._step("semantic"):
            assert isinstance(plan, FakePlan)
            n = max(1, round(self.audio_seconds * FRAMES_PER_SECOND))
            rng = np.random.default_rng(_seed(plan.params))
            return FakeSemantic(plan=plan, tokens=rng.integers(0, 16384, n).tolist())

    def synthesize(self, semantic: Any) -> npt.NDArray[np.float32]:
        with self._step("synthesis"):
            assert isinstance(semantic, FakeSemantic)
            rng = np.random.default_rng(_seed(semantic.plan.params))
            shape = (len(semantic.tokens), LATENT_DIM)
            return rng.standard_normal(shape).astype(np.float32)

    def decode(self, latents: Any) -> npt.NDArray[np.float32]:
        with self._step("decode"):
            return _audio(int(np.asarray(latents).shape[0]))

    def write_artifacts(self, out: Path, **parts: Any) -> list[ArtifactSpec]:
        with self._step("save"):
            return _write(out, **parts)

    def generate(self, p: SongParams, out: Path) -> list[ArtifactSpec]:
        # One call, like ``pipe(...)`` followed by ``save_artifacts``: no per-stage hooks.
        with self._step("generate"):
            abc = None if p.cot == "off" else _fake_abc(p)
            plan = FakePlan(params=p, abc=abc)
            n = max(1, round(self.audio_seconds * FRAMES_PER_SECOND))
            semantic = FakeSemantic(plan=plan, tokens=[0] * n)
            latents = np.zeros((n, LATENT_DIM), dtype=np.float32)
            return _write(out, plan=plan, semantic=semantic, latents=latents, audio=_audio(n))

    # --- internals --------------------------------------------------------------------

    @contextmanager
    def _step(self, name: str) -> Iterator[None]:
        with self._lock:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
            self.calls.append(name)
        try:
            if self.stage_gate is not None and not self.stage_gate.wait(GATE_TIMEOUT_S):
                raise TimeoutError("fake stage gate was never released")
            if self.stage_delay_s:
                time.sleep(self.stage_delay_s)
            if self.fail_at == name:
                raise self.fail_with or RuntimeError(f"fake failure at {name}")
            yield
        finally:
            with self._lock:
                self._active -= 1


def _audio(frames: int) -> npt.NDArray[np.float32]:
    """Stereo test tones, one latent frame's worth of samples per frame."""
    n = frames * SAMPLE_RATE // FRAMES_PER_SECOND
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    left = 0.2 * np.sin(2 * np.pi * 440.0 * t)
    right = 0.2 * np.sin(2 * np.pi * 660.0 * t)
    return np.stack([left, right], axis=1).astype(np.float32)


def _seed(p: SongParams) -> int:
    return p.seed if p.seed is not None else 0


def _fake_abc(p: SongParams) -> str:
    return f"X:1\nT:fake\nM:4/4\nL:1/8\nK:C\n% seed {_seed(p)}\nCDEF GABc|\n"


def _write(
    out: Path,
    *,
    plan: FakePlan,
    semantic: FakeSemantic,
    latents: npt.NDArray[np.float32],
    audio: npt.NDArray[np.float32],
) -> list[ArtifactSpec]:
    out.mkdir(parents=True, exist_ok=True)
    p = plan.params
    request = p.model_dump(mode="json")
    specs: list[ArtifactSpec] = []

    def spec(name: str, role: ArtifactRole, media_type: str) -> None:
        specs.append(ArtifactSpec(relpath=name, role=role, media_type=media_type))

    sf.write(out / "audio.flac", audio, SAMPLE_RATE, subtype="PCM_24", format="FLAC")
    spec("audio.flac", ArtifactRole.primary_audio, "audio/flac")
    if plan.abc is not None:
        (out / "score.abc").write_text(plan.abc, encoding="utf-8")
        spec("score.abc", ArtifactRole.score, "text/vnd.abc")
    _write_json(out / "plan.json", {"request": request, "abc": plan.abc, "truncated": False})
    spec("plan.json", ArtifactRole.plan, "application/json")
    np.save(out / "semantic.npy", np.asarray(semantic.tokens, dtype=np.int32))
    spec("semantic.npy", ArtifactRole.intermediate, "application/octet-stream")
    _write_json(out / "request.json", request)
    spec("request.json", ArtifactRole.other, "application/json")
    result = {
        "status": "complete",
        "engine": "fake",
        "truncated": {"abc": False, "semantic": False},
        "sample_rate": SAMPLE_RATE,
        "audio_seconds": len(audio) / SAMPLE_RATE,
        "latent_frames": int(latents.shape[0]),
    }
    _write_json(out / "result.json", result)
    spec("result.json", ArtifactRole.result, "application/json")
    return specs


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
