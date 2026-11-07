"""Artifact storage: path safety, verification and atomic placement (S-3, S-4)."""

import hashlib
from pathlib import Path

import pytest

from studio_contracts import Artifact, ArtifactRole
from studio_server.core.storage import Storage, VerifyError, is_safe_relpath


def entry(name: str, content: bytes) -> Artifact:
    return Artifact(
        name=name,
        role=ArtifactRole.other,
        media_type="application/octet-stream",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


@pytest.mark.parametrize(
    "name",
    ["../x", "a/../../x", "/etc/passwd", "a\\b", "C:/x", "", "a//b", "./a", ".partial/x", "a\x00b"],
)
def test_unsafe_names_are_rejected(name: str) -> None:
    assert not is_safe_relpath(name)


@pytest.mark.parametrize("name", ["audio.flac", "sub/dir/file.npy", "a.b-c_d"])
def test_safe_names(name: str) -> None:
    assert is_safe_relpath(name)


def test_artifact_path_stays_inside_the_generation(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    rel = storage.relative_dir("gen_1")
    assert (
        storage.artifact_path(rel, "a/b.txt") == (tmp_path / "generations/gen_1/a/b.txt").resolve()
    )
    with pytest.raises(ValueError):
        storage.artifact_path(rel, "../gen_2/x")


def test_relative_dir_rejects_bad_ids() -> None:
    with pytest.raises(ValueError):
        Storage.relative_dir("../x")


def test_finalize_moves_a_verified_file_into_place(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    rel = storage.relative_dir("gen_1")
    tmp = storage.temp_path(rel)
    tmp.write_bytes(b"hello")
    final = storage.finalize(rel, tmp, entry("sub/hello.txt", b"hello"))
    assert final.read_bytes() == b"hello"
    assert not tmp.exists()


@pytest.mark.parametrize("written", [b"hellO", b"hell", b"hello!"])
def test_corrupt_file_never_becomes_visible(tmp_path: Path, written: bytes) -> None:
    storage = Storage(tmp_path)
    rel = storage.relative_dir("gen_1")
    tmp = storage.temp_path(rel)
    tmp.write_bytes(written)
    with pytest.raises(VerifyError):
        storage.finalize(rel, tmp, entry("hello.txt", b"hello"))
    assert not tmp.exists()
    assert not (storage.generation_dir(rel) / "hello.txt").exists()


def test_clean_partials_and_delete(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    rel = storage.relative_dir("gen_1")
    storage.temp_path(rel).write_bytes(b"half")
    (storage.generation_dir(rel) / "keep.txt").write_bytes(b"done")
    assert storage.clean_partials() == 1
    assert [p.name for p in storage.generation_dir(rel).iterdir()] == ["keep.txt"]
    storage.delete_generation(rel)
    assert not storage.generation_dir(rel).exists()
    storage.delete_generation(rel)  # idempotent
