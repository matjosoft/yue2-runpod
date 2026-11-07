from pathlib import Path

import httpx

from studio_server import __version__
from studio_server.config import ServerSettings
from studio_server.main import create_app


async def test_openapi_document_is_served(tmp_path: Path) -> None:
    app = create_app(ServerSettings(data_dir=tmp_path), start_poller=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://server") as client:
        response = await client.get("/openapi.json")
    assert response.status_code == 200
    info = response.json()["info"]
    assert info == {"title": "Studio server", "version": __version__}


def test_building_the_app_touches_no_disk(tmp_path: Path) -> None:
    """The OpenAPI exporter builds the app without a data dir; only the lifespan
    creates the database."""
    create_app(ServerSettings(data_dir=tmp_path / "data"), start_poller=False)
    assert not (tmp_path / "data").exists()


async def test_unknown_paths_and_methods_get_the_envelope(tmp_path: Path) -> None:
    app = create_app(ServerSettings(data_dir=tmp_path), start_poller=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://server") as client:
            for method, path, status in [
                ("GET", "/api/nope", 404),
                ("PATCH", "/api/settings", 405),
                ("POST", "/api/generations", 422),
            ]:
                response = await client.request(method, path)
                assert response.status_code == status
                assert set(response.json()["error"]) == {"code", "message", "retryable", "details"}
            assert "x-request-id" in response.headers
