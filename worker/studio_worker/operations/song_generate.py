"""``song.generate``: the first registered operation."""

import secrets
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel

from studio_contracts import SongParams
from studio_contracts.song import SEED_MAX
from studio_worker.engines.base import (
    SONG_STAGES_SINGLE,
    SONG_STAGES_STAGED,
    Engine,
    SongEngine,
)
from studio_worker.operations.base import OperationCanceled, OperationContext
from studio_worker.operations.registry import register_operation


def _song_engine(engine: Engine) -> SongEngine:
    if not isinstance(engine, SongEngine):
        raise TypeError(f"engine {engine.kind!r} cannot run song.generate")
    return engine


@register_operation
class SongGenerate:
    name: ClassVar[str] = "song.generate"
    params_model: ClassVar[type[BaseModel]] = SongParams

    def stages(self, engine: Engine) -> Sequence[str]:
        staged = _song_engine(engine).supports_staged
        return SONG_STAGES_STAGED if staged else SONG_STAGES_SINGLE

    def run(self, ctx: OperationContext, params: BaseModel) -> None:
        assert isinstance(params, SongParams)
        engine = _song_engine(ctx.engine)
        # The engine rejects seed=None, so the worker always picks one and reports it.
        seed = params.seed if params.seed is not None else secrets.randbelow(SEED_MAX + 1)
        ctx.resolved(seed=seed)
        p = params.model_copy(update={"seed": seed})
        out = ctx.job_dir

        def step[R](name: str, call: Callable[[], R]) -> R:
            if ctx.cancel_requested():
                raise OperationCanceled(name)
            ctx.stage(name)
            return call()

        if engine.supports_staged:
            plan: Any = step("plan", lambda: engine.plan(p))
            semantic: Any = step("semantic", lambda: engine.generate_semantic(plan))
            latents: Any = step("synthesis", lambda: engine.synthesize(semantic))
            audio: Any = step("decode", lambda: engine.decode(latents))
            specs = step(
                "save",
                lambda: engine.write_artifacts(
                    out, plan=plan, semantic=semantic, latents=latents, audio=audio
                ),
            )
        else:
            specs = step("generate", lambda: engine.generate(p, out))
            step("save", lambda: None)
        for spec in specs:
            ctx.add_artifact(spec.relpath, spec.role, spec.media_type)
