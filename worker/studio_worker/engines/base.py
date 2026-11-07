"""Engine interfaces (PRD section 7, "Worker design"). The Protocols are verbatim.

``ArtifactSpec`` and ``EngineOOM`` are the two supporting types the PRD names or implies
but does not define (docs/decisions.md).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from studio_contracts import ArtifactRole, EngineInfo, SongParams


@dataclass(frozen=True)
class ArtifactSpec:
    """A file an engine wrote, relative to the output folder it was given."""

    relpath: str
    role: ArtifactRole
    media_type: str


class EngineOOM(Exception):
    """The engine ran out of GPU memory. Real engines translate their own OOM into this,
    so that only the engine module needs to know the framework's exception type."""


@runtime_checkable
class Engine(Protocol):
    kind: str

    def load(self) -> None: ...
    def info(self) -> EngineInfo: ...
    def close(self) -> None: ...


@runtime_checkable
class SongEngine(Engine, Protocol):  # what song.generate needs
    supports_staged: bool

    def plan(self, p: SongParams) -> Any: ...
    def generate_semantic(self, plan: Any) -> Any: ...
    def synthesize(self, semantic: Any) -> Any: ...
    def decode(self, latents: Any) -> Any: ...
    def write_artifacts(self, out: Path, **parts: Any) -> list[ArtifactSpec]: ...
    def generate(self, p: SongParams, out: Path) -> list[ArtifactSpec]: ...  # single call


SONG_STAGES_STAGED: Sequence[str] = ("plan", "semantic", "synthesis", "decode", "save")
SONG_STAGES_SINGLE: Sequence[str] = ("generate", "save")
