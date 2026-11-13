"""A stand-in for the ``yue2`` package (``yue2_infer`` 0.1.5), for GPU-free tests of Yue2Engine.

It mirrors the signatures, return types and ``save_artifacts`` file set recorded from the
wheel source in docs/yue2-api-notes.md (sections 3 and "Staged reconstruction"). It is not
the real package: passing these tests shows that the engine calls the API as documented,
not that the real model behaves as documented (that is the owner's GPU check in M6).
"""

import hashlib
import json
import math
import sys
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import soundfile as sf

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 1920  # 25 latent frames per second
DEFAULT_MODEL = "m-a-p/YuE2-3B"
DEFAULT_VAE = "m-a-p/YuE2-Vae"


@dataclass
class StubState:
    """What the stub records, and the failures it injects. One per test."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_at: dict[str, BaseException] = field(default_factory=dict)
    semantic_tokens: int = 25  # one second of audio

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs(self, name: str) -> dict[str, Any]:
        return next(kw for n, kw in self.calls if n == name)


STATE = StubState()


def _record(name: str, **kwargs: Any) -> None:
    STATE.calls.append((name, kwargs))
    if name in STATE.fail_at:
        raise STATE.fail_at[name]


# --- yue2.storage -----------------------------------------------------------------------


def identity(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_hashes(directory: Path) -> dict[str, dict[str, object]]:
    return {
        p.name: {"sha256": _sha256_file(p), "bytes": p.stat().st_size}
        for p in sorted(directory.iterdir())
        if p.is_file() and p.name != "result.json"
    }


# --- yue2.protocol ----------------------------------------------------------------------


@dataclass
class SongRequest:
    style: str
    lyrics: str
    cot: str = "full"
    seed: int = 831001
    abc: str | None = None
    cfg_scale: float | None = None
    id: str = "song"

    def __post_init__(self) -> None:
        if self.cot not in {"off", "melody", "full"}:
            raise ValueError("cot must be off, melody or full")
        if not isinstance(self.style, str) or not isinstance(self.lyrics, str):
            raise TypeError("style and lyrics must be strings")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise ValueError("seed must be an int")
        if not 0 <= self.seed < 2**63:
            raise ValueError("seed out of range")
        if self.cfg_scale is not None and not (
            math.isfinite(self.cfg_scale) and 0 <= self.cfg_scale <= 20
        ):
            raise ValueError("cfg_scale must be in [0, 20]")
        # protocol.py:99-100
        if self.abc is not None and (
            self.cot == "off" or not isinstance(self.abc, str) or not self.abc.strip()
        ):
            raise ValueError("External ABC requires nonempty text and cot=melody/full")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- yue2.pipeline ----------------------------------------------------------------------


@dataclass
class SymbolicPlan:
    request: SongRequest
    abc: str | None
    abc_ids: list[int]
    prefix: list[int]
    timing: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        if self.abc is not None:
            (directory / "score.abc").write_text(self.abc, encoding="utf-8")
        np.save(directory / "abc_tokens.npy", np.asarray(self.abc_ids, dtype=np.int32))
        np.save(directory / "prefix.npy", np.asarray(self.prefix, dtype=np.int32))
        write_json(
            directory / "plan.json",
            {"request": self.request.to_dict(), "timing": self.timing, "abc": self.abc},
        )
        names = ["abc_tokens.npy", "prefix.npy", "plan.json"]
        if self.abc is not None:
            names.append("score.abc")
        write_json(
            directory / "plan_manifest.json", {n: _sha256_file(directory / n) for n in names}
        )


@dataclass
class SemanticResult:
    plan: SymbolicPlan
    tokens: list[int]
    timing: dict[str, Any]
    truncated: bool


@dataclass
class SongResult:
    audio: npt.NDArray[np.float32]
    sample_rate: int
    semantic: SemanticResult
    latents: npt.NDArray[np.float32]
    config: dict[str, Any]
    weights: dict[str, Any]
    timing: dict[str, Any]
    request_identity: str

    @property
    def truncated(self) -> dict[str, bool]:
        return {"abc": self.semantic.plan.truncated, "semantic": self.semantic.truncated}

    def save(self, path: Path) -> str:
        sf.write(path, self.audio, self.sample_rate, subtype="PCM_24")
        return str(path)

    def save_artifacts(self, directory: Path) -> dict[str, Any]:
        _record("save_artifacts", directory=directory)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.semantic.plan.save(directory)
        self.save(directory / "audio.flac")
        np.save(directory / "semantic.npy", np.asarray(self.semantic.tokens, dtype=np.int32))
        np.save(directory / "latent.npy", self.latents.astype(np.float32))
        write_json(directory / "request.json", self.semantic.plan.request.to_dict())
        write_json(directory / "config.json", self.config)
        result = {
            "status": "complete",
            "identity": self.request_identity,
            "truncated": self.truncated,
            "sample_rate": self.sample_rate,
            "audio_seconds": len(self.audio) / self.sample_rate,
            "weights": self.weights,
            "timing": self.timing,
            "artifacts": collect_hashes(directory),
        }
        write_json(directory / "result.json", result)
        return result


class YuE2Pipeline:
    def __init__(self, model_dir: str, vae_dir: str, **kwargs: Any) -> None:
        self.model_dir = model_dir
        self.vae_dir = vae_dir
        self.weights = {"mot": {"files": {}, "config_sha256": "m"}, "vae": {"files": {}}}
        self.load_timing = {"construct_seconds": 0.0}
        self.closed = False

    @classmethod
    def from_pretrained(
        cls,
        model: str = DEFAULT_MODEL,
        *,
        vae: str = DEFAULT_VAE,
        revision: str | None = None,
        vae_revision: str | None = None,
        local_files_only: bool = False,
        token: str | None = None,
        cache_dir: str | None = None,
        progress: bool = True,
        **kwargs: Any,
    ) -> "YuE2Pipeline":
        _record(
            "from_pretrained",
            model=model,
            vae=vae,
            revision=revision,
            vae_revision=vae_revision,
            local_files_only=local_files_only,
            token=token,
            cache_dir=cache_dir,
            progress=progress,
            **kwargs,
        )
        return cls(model, vae, progress=progress, **kwargs)

    def _request(
        self,
        style: str | None = None,
        lyrics: str | None = None,
        *,
        tags: str | None = None,
        **kwargs: Any,
    ) -> SongRequest:
        style = tags if style is None else style
        if style is None or lyrics is None:
            raise ValueError("Provide style and lyrics")
        return SongRequest(style=style, lyrics=lyrics, **kwargs)

    def plan(
        self,
        style: str | None = None,
        lyrics: str | None = None,
        *,
        tags: str | None = None,
        request: SongRequest | None = None,
        abc_sampling: object = None,
        cancelled: Callable[[], bool] | None = None,
        on_token: object = None,
        **kwargs: Any,
    ) -> SymbolicPlan:
        _record("plan", style=style, lyrics=lyrics, request=request, cancelled=cancelled, **kwargs)
        request = request or self._request(style, lyrics, tags=tags, **kwargs)
        if request.cot == "off":
            return SymbolicPlan(request, None, [], [1, 2, 3])
        if request.abc is not None:  # pipeline.py:260-264: tokenized, not generated
            ids = [ord(c) for c in request.abc]
            timing = {"seconds": 0.0, "output_tokens": 0, "external_prefix_tokens": len(ids)}
            return SymbolicPlan(request, request.abc, ids, [1, 2, 3, *ids], timing)
        abc = f"X:1\nK:C\n% seed {request.seed}\nCDEF|\n"
        return SymbolicPlan(request, abc, [7, 8, 9], [1, 2, 3, 7, 8, 9], {"seconds": 0.1})

    def generate_semantic(
        self,
        plan: SymbolicPlan,
        *,
        sampling: object = None,
        cancelled: Callable[[], bool] | None = None,
        on_token: object = None,
    ) -> SemanticResult:
        _record("generate_semantic", plan=plan, cancelled=cancelled)
        if not isinstance(plan, SymbolicPlan):
            raise TypeError("plan must be a SymbolicPlan")
        return SemanticResult(plan, list(range(STATE.semantic_tokens)), {"seconds": 0.2}, False)

    def synthesize(
        self, semantic: SemanticResult, *, cancelled: Callable[[], bool] | None = None
    ) -> npt.NDArray[np.float32]:
        _record("synthesize", semantic=semantic, cancelled=cancelled)
        return np.zeros((len(semantic.tokens), 64), dtype=np.float32)

    def decode(
        self, latents: npt.NDArray[np.float32], *, full: bool = False, vae: object = None
    ) -> npt.NDArray[np.float32]:
        _record("decode", latents=latents)
        n = latents.shape[0] * SAMPLES_PER_FRAME
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        tone = (0.1 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
        return np.stack([tone, tone], axis=1)

    def effective_config(
        self, request: SongRequest, abc_sampling: object = None, semantic_sampling: object = None
    ) -> dict[str, Any]:
        guidance = (
            (1.01 if request.cot == "off" else 1.0)
            if request.cfg_scale is None
            else (request.cfg_scale)
        )
        return {"cfg_scale": guidance, "validation_status": "unvalidated"}

    def close(self) -> None:
        _record("close")
        self.closed = True

    def __call__(
        self,
        style: str | None = None,
        lyrics: str | None = None,
        *,
        tags: str | None = None,
        abc_sampling: object = None,
        semantic_sampling: object = None,
        cancelled: Callable[[], bool] | None = None,
        on_token: object = None,
        **kwargs: Any,
    ) -> SongResult:
        _record("__call__", style=style, lyrics=lyrics, cancelled=cancelled, **kwargs)
        request = self._request(style, lyrics, tags=tags, **kwargs)
        config = self.effective_config(request)
        request_id = identity(
            {"request": request.to_dict(), "config": config, "weights": self.weights}
        )
        plan = self.plan(request=request)
        semantic = self.generate_semantic(plan)
        latents = self.synthesize(semantic)
        audio = self.decode(latents)
        timing = {
            "abc": plan.timing,
            "semantic": semantic.timing,
            "nar_seconds": 0.0,
            "vae_seconds": 0.0,
            "load": dict(self.load_timing),
            "e2e_seconds": 0.0,
        }
        return SongResult(
            audio, SAMPLE_RATE, semantic, latents, config, self.weights, timing, request_id
        )


@contextmanager
def installed() -> Iterator[StubState]:
    """Install this stub as ``yue2`` and ``yue2.storage`` in ``sys.modules``, with fresh state."""
    global STATE
    STATE = StubState()
    package = types.ModuleType("yue2")
    storage = types.ModuleType("yue2.storage")
    package.__dict__.update(
        __version__="0.1.5",
        YuE2Pipeline=YuE2Pipeline,
        SymbolicPlan=SymbolicPlan,
        SemanticResult=SemanticResult,
        SongResult=SongResult,
        storage=storage,
    )
    storage.__dict__.update(identity=identity)
    saved = {name: sys.modules.get(name) for name in ("yue2", "yue2.storage")}
    sys.modules["yue2"] = package
    sys.modules["yue2.storage"] = storage
    try:
        yield STATE
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


DOCUMENTED_FILES = {
    "audio.flac",
    "score.abc",
    "abc_tokens.npy",
    "prefix.npy",
    "plan.json",
    "plan_manifest.json",
    "semantic.npy",
    "latent.npy",
    "request.json",
    "config.json",
    "result.json",
}
"""Everything ``save_artifacts`` writes (docs/yue2-api-notes.md); ``score.abc`` only when
``cot`` is not ``off``."""
