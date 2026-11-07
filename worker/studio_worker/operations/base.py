"""Operation interface and context (PRD section 7, "Worker design").

Operations never import FastAPI, so a RunPod serverless handler can reuse them later.
``OperationContext`` reports through callbacks, so this layer also knows nothing about
the job store.
"""

import hashlib
import threading
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

from studio_contracts import Artifact, ArtifactRole
from studio_worker.engines.base import Engine


class OperationCanceled(Exception):
    """Raised by an operation that saw ``ctx.cancel_requested()`` between stages."""


def is_safe_relpath(name: str) -> bool:
    """A relative POSIX path with no ``..``, ``.``, empty parts, backslashes or drive
    letters. Artifact names must pass this (PRD section 6, "Rules both sides follow")."""
    if not name or "\\" in name or "\x00" in name or ":" in name:
        return False
    if name.startswith("/"):
        return False
    parts = name.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OperationContext:
    """What an operation sees while it runs.

    ``job_dir`` is the job's ``artifacts/`` folder: operations write their files there,
    and ``add_artifact`` paths are relative to it (docs/decisions.md).
    """

    def __init__(
        self,
        *,
        engine: Engine,
        job_dir: Path,
        on_stage: Callable[[str, float | None], None],
        on_artifact: Callable[[Artifact], None],
        on_resolved: Callable[[dict[str, Any]], None],
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.engine = engine
        self.job_dir = job_dir
        self._on_stage = on_stage
        self._on_artifact = on_artifact
        self._on_resolved = on_resolved
        self._cancel = cancel_event or threading.Event()

    def stage(self, name: str, progress: float | None = None) -> None:
        self._on_stage(name, progress)

    def add_artifact(self, relpath: str, role: str, media_type: str) -> None:
        if not is_safe_relpath(relpath):
            raise ValueError(f"unsafe artifact path: {relpath!r}")
        root = self.job_dir.resolve()
        path = (self.job_dir / PurePosixPath(relpath)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"artifact is not a file inside the job folder: {relpath!r}")
        self._on_artifact(
            Artifact(
                name=relpath,
                role=ArtifactRole(role),
                media_type=media_type,
                size_bytes=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )

    def resolved(self, **values: Any) -> None:  # e.g. the seed actually used
        self._on_resolved(values)

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()


class Operation(Protocol):
    name: ClassVar[str]  # "song.generate"
    params_model: ClassVar[type[BaseModel]]  # validated before the job is queued

    def stages(self, engine: Engine) -> Sequence[str]: ...
    def run(self, ctx: OperationContext, params: BaseModel) -> None: ...
