"""Module boundaries (PRD section 7, "Local server design")."""

import ast
from pathlib import Path

import pytest

import studio_server

PACKAGE = Path(studio_server.__file__).resolve().parent
FEATURES = PACKAGE / "features"
FEATURE_NAMES = sorted(p.name for p in FEATURES.iterdir() if (p / "__init__.py").is_file())


def imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_feature_list() -> None:
    from studio_server.features import FEATURES as LIST

    assert [f.name for f in LIST] == ["settings", "worker", "generations"]
    assert FEATURE_NAMES == ["generations", "settings", "worker"]


def test_core_never_imports_features() -> None:
    for path in (PACKAGE / "core").rglob("*.py"):
        assert not any(m.startswith("studio_server.features") for m in imports(path)), path


@pytest.mark.parametrize("feature", FEATURE_NAMES)
def test_features_never_import_each_other(feature: str) -> None:
    others = {f"studio_server.features.{name}" for name in FEATURE_NAMES if name != feature}
    for path in (FEATURES / feature).rglob("*.py"):
        found = {m for m in imports(path) if any(m.startswith(o) for o in others)}
        assert not found, path


def test_server_never_imports_the_worker() -> None:
    for path in PACKAGE.rglob("*.py"):
        assert not any(m.split(".")[0] == "studio_worker" for m in imports(path)), path


def test_only_the_backend_adapter_speaks_http_to_the_worker() -> None:
    for path in PACKAGE.rglob("*.py"):
        if path.parent.name == "backend":
            continue
        uses_httpx = "httpx" in imports(path)
        # core/worker.py and core/runtime.py only pass a test transport through.
        if uses_httpx:
            assert path.name in {"worker.py", "runtime.py", "main.py"}, path
