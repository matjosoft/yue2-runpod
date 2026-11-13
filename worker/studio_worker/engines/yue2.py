"""The real engine: YuE2 through the ``yue2_infer`` package (pinned 0.1.5).

This is the only module that imports ``yue2`` (PRD section 7), and it does so lazily in
``load()``, so the worker, its tests and the fake image never need the package or torch.
Every call below is recorded, with its source line, in docs/yue2-api-notes.md.

Staged mode (``supports_staged``) calls ``plan``, ``generate_semantic``, ``synthesize`` and
``decode`` one at a time, so the operation can report each stage, and then builds the
``SongResult`` exactly as ``YuE2Pipeline.__call__`` does (notes: "Staged reconstruction").
The saved artifact set is therefore the same as a one-call run.

The pipeline is not thread-safe. The worker runs one executor thread, and the per-run
timings kept here rely on that.
"""

import importlib
import importlib.metadata
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from studio_contracts import ArtifactRole, EngineInfo, SongParams
from studio_worker.engines.base import ArtifactSpec, EngineOOM

log = logging.getLogger(__name__)

DISTRIBUTION = "yue2-infer"
DEFAULT_MODEL_REPO = "m-a-p/YuE2-3B"
DEFAULT_VAE_REPO = "m-a-p/YuE2-Vae"
"""``from_pretrained``'s own default (``pipeline.py:168``); reported, not passed."""
SAMPLE_RATE = 48000
"""``decode`` returns 48 kHz audio but not the rate (``modeling_vae.py:292``)."""

_OCTET = "application/octet-stream"
_JSON = "application/json"
ARTIFACT_TYPES: dict[str, tuple[ArtifactRole, str]] = {
    "audio.flac": (ArtifactRole.primary_audio, "audio/flac"),
    "score.abc": (ArtifactRole.score, "text/vnd.abc"),
    "plan.json": (ArtifactRole.plan, _JSON),
    "result.json": (ArtifactRole.result, _JSON),
    "request.json": (ArtifactRole.other, _JSON),
    "config.json": (ArtifactRole.other, _JSON),
    "plan_manifest.json": (ArtifactRole.other, _JSON),
    "engine.json": (ArtifactRole.other, _JSON),
}
ENGINE_FILE = "engine.json"
"""Written by the worker next to yue2's own files: the package version and model repos
(PRD section 11: the wheel version is recorded in artifacts; yue2 records only a hash of
its source in ``config.json``)."""


def artifact_type(name: str) -> tuple[ArtifactRole, str]:
    """Role and media type of a file ``save_artifacts`` wrote. Token and latent arrays are
    ``intermediate``; anything unexpected is kept as ``other`` (W-5: keep everything)."""
    if name in ARTIFACT_TYPES:
        return ARTIFACT_TYPES[name]
    if name.endswith(".npy"):
        return ArtifactRole.intermediate, _OCTET
    return ArtifactRole.other, _OCTET


def _is_oom(exc: BaseException) -> bool:
    # torch.cuda.OutOfMemoryError, matched by name so torch is never imported here, and
    # by message for OOMs torch raises as a plain RuntimeError (notes section 4).
    names = {cls.__name__ for cls in type(exc).__mro__}
    return "OutOfMemoryError" in names or "out of memory" in str(exc).lower()


@contextmanager
def _translate_errors() -> Iterator[None]:
    """GPU OOM becomes ``EngineOOM`` (job error ``oom``). Everything else propagates and
    the executor reports it as ``engine_error``."""
    try:
        yield
    except EngineOOM:
        raise
    except Exception as exc:
        if _is_oom(exc):
            raise EngineOOM(f"{type(exc).__name__}: {exc}"[:500]) from exc
        raise


@dataclass
class _RunTimings:
    """What ``__call__`` measures around the stages, kept between the staged calls."""

    started: float
    nar_seconds: float = 0.0
    vae_seconds: float = 0.0
    finished: float | None = None


class Yue2Engine:
    kind = "yue2"

    def __init__(
        self,
        *,
        model_repo: str = DEFAULT_MODEL_REPO,
        vae_repo: str | None = None,
        model_revision: str | None = None,
        vae_revision: str | None = None,
        hf_token: str | None = None,
        hf_home: Path | None = None,
        device: str = "cuda",
        staged: bool = True,
    ) -> None:
        self.model_repo = model_repo
        self.vae_repo = vae_repo
        self.model_revision = model_revision
        self.vae_revision = vae_revision
        self._hf_token = hf_token
        self.hf_home = hf_home
        self.device = device
        self.supports_staged = staged
        self._yue2: ModuleType | None = None
        self._pipe: Any = None
        self._run: _RunTimings | None = None

    # --- Engine -----------------------------------------------------------------------

    def load(self) -> None:
        yue2 = importlib.import_module("yue2")
        kwargs: dict[str, Any] = {
            "model": self.model_repo,
            "revision": self.model_revision,
            "vae_revision": self.vae_revision,
            "token": self._hf_token,
            "progress": False,  # progress bars go to stderr; the worker reports stages
            "device": self.device,  # through **kwargs to __init__ (W-1: cuda)
        }
        if self.vae_repo is not None:
            kwargs["vae"] = self.vae_repo
        if self.hf_home is not None:
            kwargs["cache_dir"] = str(self.hf_home / "hub")  # HF_HOME's hub cache
        started = time.monotonic()
        with _translate_errors():
            self._pipe = yue2.YuE2Pipeline.from_pretrained(**kwargs)
        self._yue2 = yue2
        log.info(
            "yue2 pipeline constructed",
            extra={"models": self.info().models, "seconds": round(time.monotonic() - started, 1)},
        )

    def info(self) -> EngineInfo:
        return EngineInfo(
            kind=self.kind,
            package_version=self._package_version(),
            models={
                "lm": _pinned(self.model_repo, self.model_revision),
                "vae": _pinned(self.vae_repo or DEFAULT_VAE_REPO, self.vae_revision),
            },
        )

    def close(self) -> None:
        pipe, self._pipe = self._pipe, None
        if pipe is not None:
            pipe.close()

    # --- SongEngine: staged -------------------------------------------------------------

    def plan(self, p: SongParams) -> Any:
        pipe = self._loaded()
        self._run = _RunTimings(started=time.perf_counter())
        with _translate_errors():
            return pipe.plan(**_request_kwargs(p))

    def generate_semantic(self, plan: Any) -> Any:
        pipe = self._loaded()
        with _translate_errors():
            return pipe.generate_semantic(plan)

    def synthesize(self, semantic: Any) -> Any:
        pipe = self._loaded()
        started = time.perf_counter()
        with _translate_errors():
            latents = pipe.synthesize(semantic)
        self._timings().nar_seconds = time.perf_counter() - started
        return latents

    def decode(self, latents: Any) -> Any:
        pipe = self._loaded()
        started = time.perf_counter()
        with _translate_errors():
            audio = pipe.decode(latents)
        run = self._timings()
        run.finished = time.perf_counter()
        run.vae_seconds = run.finished - started
        return audio

    def write_artifacts(self, out: Path, **parts: Any) -> list[ArtifactSpec]:
        pipe = self._loaded()
        plan, semantic = parts["plan"], parts["semantic"]
        latents, audio = parts["latents"], parts["audio"]
        run = self._timings()
        storage = importlib.import_module("yue2.storage")
        # Built exactly as YuE2Pipeline.__call__ builds it (pipeline.py:378-398).
        request = plan.request
        config = pipe.effective_config(request)
        request_id = storage.identity(
            {"request": request.to_dict(), "config": config, "weights": pipe.weights}
        )
        timing = {
            "abc": plan.timing,
            "semantic": semantic.timing,
            "nar_seconds": run.nar_seconds,
            "vae_seconds": run.vae_seconds,
            "load": dict(pipe.load_timing),
            "e2e_seconds": (run.finished or time.perf_counter()) - run.started,
        }
        result = self._module().SongResult(
            audio, SAMPLE_RATE, semantic, latents, config, pipe.weights, timing, request_id
        )
        with _translate_errors():
            result.save_artifacts(out)
        self._run = None
        self._write_engine_file(out)
        return _specs(out)

    # --- SongEngine: single call --------------------------------------------------------

    def generate(self, p: SongParams, out: Path) -> list[ArtifactSpec]:
        pipe = self._loaded()
        with _translate_errors():
            result = pipe(**_request_kwargs(p))
            result.save_artifacts(out)
        self._write_engine_file(out)
        return _specs(out)

    # --- helpers ------------------------------------------------------------------------

    def _write_engine_file(self, out: Path) -> None:
        text = json.dumps(self.info().model_dump(mode="json"), indent=2, sort_keys=True)
        (out / ENGINE_FILE).write_text(text + "\n", encoding="utf-8")

    def _loaded(self) -> Any:
        if self._pipe is None:
            raise RuntimeError("the YuE2 pipeline is not loaded")
        return self._pipe

    def _module(self) -> ModuleType:
        assert self._yue2 is not None
        return self._yue2

    def _timings(self) -> _RunTimings:
        if self._run is None:  # stages called out of order; still produce a record
            self._run = _RunTimings(started=time.perf_counter())
        return self._run

    def _package_version(self) -> str:
        if self._yue2 is not None:
            return str(getattr(self._yue2, "__version__", "unknown"))
        try:
            return importlib.metadata.version(DISTRIBUTION)
        except importlib.metadata.PackageNotFoundError:
            return "unknown"


def _pinned(repo: str, revision: str | None) -> str:
    return f"{repo}@{revision}" if revision else repo


def _request_kwargs(p: SongParams) -> dict[str, Any]:
    """``SongRequest`` fields. The package rejects ``seed=None``, so the operation always
    resolves one; ``cfg_scale`` is left out when absent so the model default applies.
    A supplied ``abc`` score makes ``plan`` tokenize it instead of generating one
    (``pipeline.py:260-264``); it is left out when absent, which is the package default."""
    if p.seed is None:
        raise ValueError("seed must be resolved before calling the engine")
    kwargs: dict[str, Any] = {"style": p.style, "lyrics": p.lyrics, "cot": p.cot, "seed": p.seed}
    if p.cfg_scale is not None:
        kwargs["cfg_scale"] = p.cfg_scale
    if p.abc is not None:
        kwargs["abc"] = p.abc
    return kwargs


def _specs(out: Path) -> list[ArtifactSpec]:
    """Every file ``save_artifacts`` wrote, relative to ``out``, in a stable order."""
    specs = []
    for path in sorted(p for p in out.rglob("*") if p.is_file()):
        relpath = path.relative_to(out).as_posix()
        role, media_type = artifact_type(path.name)
        specs.append(ArtifactSpec(relpath=relpath, role=role, media_type=media_type))
    return specs
