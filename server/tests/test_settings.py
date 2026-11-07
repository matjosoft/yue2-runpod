"""Settings: worker URL and write-only token (S-5)."""

from server_support import TOKEN, WORKER_URL, StartServer


async def test_get_reports_env_defaults_without_the_token(start_server: StartServer) -> None:
    async with start_server() as server:
        response = await server.request("GET", "/api/settings")
    assert response.status_code == 200
    assert response.json() == {"worker_url": WORKER_URL, "token_set": True}
    assert TOKEN not in response.text


async def test_nothing_configured(start_server: StartServer) -> None:
    async with start_server(worker_url=None, worker_token=None) as server:
        body = (await server.request("GET", "/api/settings")).json()
    assert body == {"worker_url": None, "token_set": False}


async def test_put_saves_and_never_echoes_the_token(start_server: StartServer) -> None:
    new_token = "saved-token-" + "z" * 40
    async with start_server(worker_url=None, worker_token=None) as server:
        response = await server.request(
            "PUT", "/api/settings", json={"worker_url": "https://pod.example", "token": new_token}
        )
        assert response.status_code == 200
        assert response.json() == {"worker_url": "https://pod.example", "token_set": True}
        assert new_token not in response.text
        assert server.runtime.link.effective() == ("https://pod.example", new_token)
        for path in ("/api/settings", "/api/health", "/openapi.json"):
            assert new_token not in (await server.request("GET", path)).text


async def test_saved_values_beat_environment(start_server: StartServer) -> None:
    async with start_server() as server:
        await server.request("PUT", "/api/settings", json={"worker_url": "https://saved.example"})
        assert server.runtime.link.effective() == ("https://saved.example", TOKEN)


async def test_omitted_keeps_null_clears(start_server: StartServer) -> None:
    async with start_server(worker_url=None, worker_token=None) as server:
        await server.request(
            "PUT", "/api/settings", json={"worker_url": "https://a.example", "token": "t" * 40}
        )
        body = (
            await server.request("PUT", "/api/settings", json={"worker_url": "https://b.example"})
        ).json()
        assert body == {"worker_url": "https://b.example", "token_set": True}
        body = (await server.request("PUT", "/api/settings", json={"token": None})).json()
        assert body == {"worker_url": "https://b.example", "token_set": False}


async def test_clearing_a_saved_value_falls_back_to_environment(start_server: StartServer) -> None:
    async with start_server() as server:
        await server.request("PUT", "/api/settings", json={"worker_url": "https://saved.example"})
        body = (await server.request("PUT", "/api/settings", json={"worker_url": None})).json()
    assert body["worker_url"] == WORKER_URL


async def test_settings_persist_across_restart(start_server: StartServer) -> None:
    async with start_server(worker_url=None, worker_token=None) as server:
        await server.request(
            "PUT", "/api/settings", json={"worker_url": "https://a.example", "token": "t" * 40}
        )
    async with start_server(worker_url=None, worker_token=None) as server:
        body = (await server.request("GET", "/api/settings")).json()
    assert body == {"worker_url": "https://a.example", "token_set": True}


async def test_invalid_put_gets_the_envelope(start_server: StartServer) -> None:
    async with start_server() as server:
        response = await server.request("PUT", "/api/settings", json={"worker_url": "ftp://x"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        response = await server.request("PUT", "/api/settings", json={"tokn": "x"})
        assert response.status_code == 422
