"""Artifact storage under ``<data dir>/generations/<id>/`` (S-3, S-4, S-7).

Downloads go to a temp file in ``<generation>/.partial/``, are checked against the
manifest's size and sha256, and are then renamed into place atomically, so a partial
file is never served. Names are accepted only from the manifest, and only if they are
safe relative POSIX paths (PRD section 6, "Rules both sides follow").
"""

import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path

from studio_contracts import Artifact

log = logging.getLogger("studio_server.storage")

GENERATIONS_DIR = "generations"
PARTIAL_DIR = ".partial"


def is_safe_relpath(name: str) -> bool:
    """A relative POSIX path with no ``..``, ``.``, empty parts, backslashes or drive
    letters. Same rule as the worker's (worker/studio_worker/operations/base.py)."""
    if not name or "\\" in name or "\x00" in name or ":" in name:
        return False
    if name.startswith("/"):
        return False
    return all(part not in ("", ".", "..", PARTIAL_DIR) for part in name.split("/"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class VerifyError(Exception):
    """A downloaded file does not match its manifest entry."""


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.root = data_dir / GENERATIONS_DIR

    @staticmethod
    def relative_dir(gen_id: str) -> str:
        """The ``data_dir`` column value for a generation."""
        if not is_safe_relpath(gen_id) or "/" in gen_id:
            raise ValueError(f"bad generation id {gen_id!r}")
        return f"{GENERATIONS_DIR}/{gen_id}"

    def generation_dir(self, rel_dir: str) -> Path:
        return self.data_dir / rel_dir

    def artifact_path(self, rel_dir: str, name: str) -> Path:
        """Where an artifact lives. The caller must already have found ``name`` in the
        manifest; this re-checks that it is a safe path inside the generation dir."""
        if not is_safe_relpath(name):
            raise ValueError(f"unsafe artifact name {name!r}")
        base = self.generation_dir(rel_dir).resolve()
        path = (base / name).resolve()
        if not path.is_relative_to(base):
            raise ValueError(f"artifact name escapes its folder: {name!r}")
        return path

    def temp_path(self, rel_dir: str) -> Path:
        partial = self.generation_dir(rel_dir) / PARTIAL_DIR
        partial.mkdir(parents=True, exist_ok=True)
        return partial / f"{uuid.uuid4().hex}.part"

    def finalize(self, rel_dir: str, tmp: Path, entry: Artifact) -> Path:
        """Verify ``tmp`` against ``entry`` and move it into place. On a mismatch the
        temp file is removed and ``VerifyError`` raised; nothing becomes visible."""
        try:
            size = tmp.stat().st_size
            if size != entry.size_bytes:
                raise VerifyError(f"size {size} != {entry.size_bytes}")
            digest = sha256_file(tmp)
            if digest != entry.sha256:
                raise VerifyError("sha256 mismatch")
            final = self.artifact_path(rel_dir, entry.name)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, final)
            return final
        finally:
            tmp.unlink(missing_ok=True)

    def delete_generation(self, rel_dir: str) -> None:
        path = self.generation_dir(rel_dir)
        if path.exists():
            shutil.rmtree(path)

    def clean_partials(self) -> int:
        """Remove temp files left by an interrupted download (run at startup)."""
        removed = 0
        if not self.root.is_dir():
            return 0
        for partial in self.root.glob(f"*/{PARTIAL_DIR}"):
            removed += sum(1 for _ in partial.iterdir())
            shutil.rmtree(partial, ignore_errors=True)
        if removed:
            log.info("removed partial downloads", extra={"count": removed})
        return removed
