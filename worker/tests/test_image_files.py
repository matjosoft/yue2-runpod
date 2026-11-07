"""Static checks of the worker image files (no Docker needed): pins match the recorded
wheel, both targets exist, nothing secret is baked in, and the entrypoint is the PRD one."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "worker" / "Dockerfile").read_text(encoding="utf-8")
NOTES = (ROOT / "docs" / "yue2-api-notes.md").read_text(encoding="utf-8")


def _arg(name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.MULTILINE)
    assert match, f"ARG {name} has no default"
    return match.group(1)


def test_wheel_pins_match_the_api_notes() -> None:
    assert _arg("YUE2_WHEEL_VERSION") == "0.1.5"
    sha = _arg("YUE2_WHEEL_SHA256")
    assert f"| Wheel sha256 | `{sha}` |" in NOTES
    revision = _arg("YUE2_REPO_REVISION")
    assert f"repo sha `{revision}`" in NOTES
    assert "uv pip install huggingface-hub==0.36.2" in DOCKERFILE
    assert "sha256sum -c -" in DOCKERFILE


def test_local_packages_install_without_the_uv_workspace() -> None:
    """The image has no workspace root, so [tool.uv.sources] must be ignored."""
    installs = re.findall(r"uv pip install (?!huggingface)[^\n]*", DOCKERFILE)
    assert len(installs) == 2
    assert all("--no-sources" in line for line in installs)


def test_both_targets_exist_and_yue2_is_the_default() -> None:
    targets = re.findall(r"^FROM \S+ AS (\S+)$", DOCKERFILE, re.MULTILINE)
    assert {"fake", "yue2"} <= set(targets)
    assert targets[-1] == "yue2"  # the last stage is what a plain `docker build` produces
    assert "FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04 AS yue2" in DOCKERFILE


def test_entrypoint_is_the_prd_one() -> None:
    cmds = re.findall(r"^CMD (.+)$", DOCKERFILE, re.MULTILINE)
    assert len(cmds) == 2
    for cmd in cmds:
        assert "uvicorn studio_worker.main:app --host 0.0.0.0" in cmd
        assert "${PORT:-8000}" in cmd


def test_no_secrets_or_weights_are_baked_in() -> None:
    for name in ("WORKER_API_KEY", "HF_TOKEN"):
        assert not re.search(rf"^\s*(ENV|ARG)\b.*\b{name}\b", DOCKERFILE, re.MULTILINE)
    assert "safetensors" not in DOCKERFILE
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignore and "*.env" in ignore


def test_runtime_defaults_match_prd_section_8() -> None:
    for env in ("ENGINE=yue2", "HF_HOME=/workspace/hf", "JOBS_DIR=/workspace/jobs", "PORT=8000"):
        assert env in DOCKERFILE
    assert "EXPOSE 8000" in DOCKERFILE


def test_build_script_is_executable() -> None:
    script = ROOT / "scripts" / "build_worker.sh"
    assert os.access(script, os.X_OK)
    assert "--target" in script.read_text(encoding="utf-8")
