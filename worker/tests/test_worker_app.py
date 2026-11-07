import sys
from pathlib import Path

import httpx
import pytest
from worker_support import Worker

from studio_worker import __version__, main
from studio_worker.engines.yue2 import Yue2Engine


async def test_openapi_document_is_served(worker: Worker) -> None:
    response = await worker.client.get("/openapi.json")
    assert response.status_code == 200
    info = response.json()["info"]
    assert info == {"title": "Studio worker", "version": __version__}


async def test_module_level_app_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``uvicorn studio_worker.main:app`` (PRD section 8) builds the app from env."""
    main._app_from_env.cache_clear()
    monkeypatch.setenv("WORKER_API_KEY", "e" * 32)
    monkeypatch.setenv("ENGINE", "fake")
    monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    try:
        app = main.app
        assert app is main.app
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://w") as client:
                assert (await client.get("/healthz")).json() == {"ok": True}
        assert (tmp_path / "jobs").is_dir()
    finally:
        main._app_from_env.cache_clear()


def test_module_level_app_refuses_a_short_key(monkeypatch: pytest.MonkeyPatch) -> None:
    main._app_from_env.cache_clear()
    monkeypatch.setenv("WORKER_API_KEY", "short")
    try:
        with pytest.raises(ValueError, match="WORKER_API_KEY"):
            _ = main.app
    finally:
        main._app_from_env.cache_clear()


def test_default_engine_is_yue2_and_loads_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    """``uvicorn studio_worker.main:app`` with ENGINE unset builds the real engine; the
    yue2 package is imported only when the model loads, not when the app is built."""
    main._app_from_env.cache_clear()
    monkeypatch.setenv("WORKER_API_KEY", "e" * 32)
    monkeypatch.delenv("ENGINE", raising=False)
    monkeypatch.delitem(sys.modules, "yue2", raising=False)
    try:
        app = main.app
        assert isinstance(app.state.runtime.engine, Yue2Engine)
        assert "yue2" not in sys.modules
    finally:
        main._app_from_env.cache_clear()
