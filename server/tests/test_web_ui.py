"""The server serves the built web UI, with a fallback to index.html for client routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from studio_server.config import DEFAULT_WEB_DIR, ServerSettings
from studio_server.main import create_app

INDEX = "<!doctype html><title>Studio</title>"


def build_dir(tmp_path: Path) -> Path:
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(INDEX)
    (web / "assets" / "app.js").write_text("console.log('hi')")
    (tmp_path / "secret.txt").write_text("do not serve")
    return web


@asynccontextmanager
async def client_for(tmp_path: Path, web_dir: Path | None) -> AsyncIterator[httpx.AsyncClient]:
    settings = ServerSettings(data_dir=tmp_path / "data", web_dir=web_dir)
    app = create_app(settings, start_poller=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://server") as client:
            yield client


def test_default_web_dir_is_the_web_build() -> None:
    assert DEFAULT_WEB_DIR.parts[-2:] == ("web", "dist")
    assert (DEFAULT_WEB_DIR.parent / "package.json").is_file()


async def test_serves_index_for_root_and_client_routes(tmp_path: Path) -> None:
    async with client_for(tmp_path, build_dir(tmp_path)) as client:
        for path in ["/", "/create?gen=gen_01X", "/library/gen_01X", "/settings"]:
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.text == INDEX
            assert response.headers["content-type"].startswith("text/html")


async def test_serves_built_files(tmp_path: Path) -> None:
    async with client_for(tmp_path, build_dir(tmp_path)) as client:
        response = await client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log('hi')"


async def test_api_paths_keep_the_json_envelope(tmp_path: Path) -> None:
    async with client_for(tmp_path, build_dir(tmp_path)) as client:
        for path in ["/api", "/api/nope", "/api/generations/gen_x/nope"]:
            response = await client.get(path)
            assert response.status_code == 404, path
            assert response.json()["error"]["code"] == "not_found"
        assert (await client.get("/api/health")).json()["ok"] is True


async def test_never_serves_files_outside_the_build(tmp_path: Path) -> None:
    async with client_for(tmp_path, build_dir(tmp_path)) as client:
        for path in ["/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/..%2f..%2fsecret.txt"]:
            response = await client.get(path)
            assert "do not serve" not in response.text, path


async def test_unbuilt_ui_gives_a_helpful_404(tmp_path: Path) -> None:
    async with client_for(tmp_path, tmp_path / "missing") as client:
        response = await client.get("/")
    assert response.status_code == 404
    assert "pnpm -C web build" in response.json()["error"]["message"]


async def test_no_web_dir_serves_no_ui(tmp_path: Path) -> None:
    async with client_for(tmp_path, None) as client:
        response = await client.get("/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_ui_route_is_not_in_the_api_contract(tmp_path: Path) -> None:
    app = create_app(
        ServerSettings(data_dir=tmp_path, web_dir=build_dir(tmp_path)), start_poller=False
    )
    assert all(path.startswith("/api/") for path in app.openapi()["paths"])
