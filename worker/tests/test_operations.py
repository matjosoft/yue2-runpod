import ast
import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

import studio_worker
from studio_contracts import Artifact, SongParams
from studio_worker.engines.base import Engine
from studio_worker.engines.fake import FakeEngine
from studio_worker.operations.base import (
    OperationCanceled,
    OperationContext,
    is_safe_relpath,
)
from studio_worker.operations.registry import (
    all_operations,
    get_operation,
    register_operation,
    registered,
)
from studio_worker.operations.song_generate import SongGenerate

PACKAGE = Path(studio_worker.__file__).parent


class Recorder:
    def __init__(self, engine: Engine, job_dir: Path) -> None:
        self.stages: list[str] = []
        self.artifacts: list[Artifact] = []
        self.resolved: dict[str, Any] = {}
        self.cancel = threading.Event()
        self.ctx = OperationContext(
            engine=engine,
            job_dir=job_dir,
            on_stage=lambda name, progress: self.stages.append(name),
            on_artifact=self.artifacts.append,
            on_resolved=self.resolved.update,
            cancel_event=self.cancel,
        )


class EchoParams(BaseModel):
    text: str


class Echo:
    name: ClassVar[str] = "test.echo"
    params_model: ClassVar[type[BaseModel]] = EchoParams

    def stages(self, engine: Engine) -> list[str]:
        return ["echo"]

    def run(self, ctx: OperationContext, params: BaseModel) -> None:
        ctx.stage("echo")


def test_song_generate_is_registered() -> None:
    op = get_operation("song.generate")
    assert isinstance(op, SongGenerate)
    assert op.params_model is SongParams


def test_registry_rejects_duplicates_and_scopes_test_operations() -> None:
    with registered(Echo):
        assert get_operation("test.echo") is not None
        with pytest.raises(ValueError, match="already registered"):
            register_operation(Echo)
    assert get_operation("test.echo") is None
    assert [op.name for op in all_operations()] == ["song.generate"]


def test_stages_follow_the_engine() -> None:
    op = SongGenerate()
    assert list(op.stages(FakeEngine())) == ["plan", "semantic", "synthesis", "decode", "save"]
    assert list(op.stages(FakeEngine(staged=False))) == ["generate", "save"]


@pytest.mark.parametrize("staged", [True, False])
def test_song_generate_reports_stages_seed_and_artifacts(tmp_path: Path, staged: bool) -> None:
    engine = FakeEngine(staged=staged)
    rec = Recorder(engine, tmp_path)
    SongGenerate().run(rec.ctx, SongParams(style="s", lyrics="l"))
    assert rec.stages == list(SongGenerate().stages(engine))
    seed = rec.resolved["seed"]
    assert isinstance(seed, int) and 0 <= seed <= 2147483647
    names = {a.name for a in rec.artifacts}
    assert {"audio.flac", "score.abc", "result.json"} <= names
    flac = next(a for a in rec.artifacts if a.name == "audio.flac")
    assert flac.size_bytes == (tmp_path / "audio.flac").stat().st_size
    assert len(flac.sha256) == 64


def test_song_generate_keeps_a_given_seed(tmp_path: Path) -> None:
    rec = Recorder(FakeEngine(), tmp_path)
    SongGenerate().run(rec.ctx, SongParams(style="s", lyrics="l", seed=42))
    assert rec.resolved == {"seed": 42}


def test_song_generate_stops_between_stages_when_canceled(tmp_path: Path) -> None:
    engine = FakeEngine()
    rec = Recorder(engine, tmp_path)
    original = engine.plan

    def plan_then_cancel(p: SongParams) -> Any:
        result = original(p)
        rec.cancel.set()
        return result

    engine.plan = plan_then_cancel  # type: ignore[method-assign]
    with pytest.raises(OperationCanceled):
        SongGenerate().run(rec.ctx, SongParams(style="s", lyrics="l"))
    assert rec.stages == ["plan"]
    assert rec.artifacts == []


@pytest.mark.parametrize(
    "name",
    ["", "/etc/passwd", "../x", "a/../b", "a/./b", "a//b", "a\\b", "C:x", "a/", ".", "x\x00"],
)
def test_unsafe_relpaths(name: str) -> None:
    assert not is_safe_relpath(name)


@pytest.mark.parametrize("name", ["audio.flac", "stems/vocals.flac", "a.b-c_d"])
def test_safe_relpaths(name: str) -> None:
    assert is_safe_relpath(name)


def test_add_artifact_rejects_unsafe_missing_and_escaping_paths(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (tmp_path / "outside.txt").write_text("x")
    (job_dir / "link").symlink_to(tmp_path / "outside.txt")
    rec = Recorder(FakeEngine(), job_dir)
    for bad in ["../outside.txt", "missing.txt", "link"]:
        with pytest.raises(ValueError):
            rec.ctx.add_artifact(bad, "other", "text/plain")
    with pytest.raises(ValueError):
        rec.ctx.add_artifact("x.txt", "not-a-role", "text/plain")
    assert rec.artifacts == []


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("layer", ["operations", "engines", "jobs"])
def test_lower_layers_never_import_the_web_framework(layer: str) -> None:
    for path in (PACKAGE / layer).rglob("*.py"):
        assert not _imports(path) & {"fastapi", "starlette"}, path


def test_only_the_yue2_engine_imports_yue2() -> None:
    for path in PACKAGE.rglob("*.py"):
        if path == PACKAGE / "engines" / "yue2.py":
            continue
        assert "yue2" not in _imports(path), path
