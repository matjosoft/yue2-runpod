"""The M4 docs stay in step with the code: every worker env var is in the runbook, and the
notice names the weight licence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = (ROOT / "worker" / "studio_worker" / "config.py").read_text(encoding="utf-8")


def _env_vars_read_by_the_worker() -> set[str]:
    names = set(re.findall(r'(?:opt|num|e\.get)\("([A-Z0-9_]+)"', CONFIG))
    assert {"WORKER_API_KEY", "ENGINE", "HF_HOME", "PORT"} <= names  # the regex still works
    return names


def test_runbook_documents_every_worker_env_var() -> None:
    runbook = (ROOT / "docs" / "runpod.md").read_text(encoding="utf-8")
    missing = {name for name in _env_vars_read_by_the_worker() if f"`{name}`" not in runbook}
    assert not missing


def test_notice_names_the_model_and_licence() -> None:
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    for text in ("YuE2", "m-a-p/YuE2-3B", "CC BY-NC 4.0", "personal, non-commercial use"):
        assert text in notice
