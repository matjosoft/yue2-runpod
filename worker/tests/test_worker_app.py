import httpx

from studio_worker import __version__
from studio_worker.main import create_app


async def test_openapi_document_is_served() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
        response = await client.get("/openapi.json")
    assert response.status_code == 200
    info = response.json()["info"]
    assert info == {"title": "Studio worker", "version": __version__}
