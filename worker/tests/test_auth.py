"""W-6: bearer auth on everything except /healthz, compared in constant time."""

import hmac

import pytest
from worker_support import TOKEN, Worker

V1_ROUTES = [
    ("GET", "/v1/status"),
    ("GET", "/v1/capabilities"),
    ("GET", "/v1/jobs"),
    ("POST", "/v1/jobs"),
    ("GET", "/v1/jobs/job_X"),
    ("DELETE", "/v1/jobs/job_X"),
    ("GET", "/v1/jobs/job_X/artifacts/audio.flac"),
]
BAD_AUTHORIZATION: list[bytes | None] = [
    None,
    b"Bearer wrong-token-0123456789abcdef0123456789",
    f"Bearer {TOKEN}x".encode(),
    f"Basic {TOKEN}".encode(),
    TOKEN.encode(),
    b"Bearer",
    "Bearer ñ-non-ascii".encode(),
]


@pytest.mark.parametrize(("method", "path"), V1_ROUTES)
@pytest.mark.parametrize("authorization", BAD_AUTHORIZATION)
async def test_v1_requires_the_token(
    worker: Worker, method: str, path: str, authorization: bytes | None
) -> None:
    worker.client.headers.pop("Authorization")
    headers = {} if authorization is None else {b"Authorization": authorization}
    response = await worker.client.request(method, path, headers=headers, json={})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"
    assert TOKEN not in response.text


async def test_healthz_needs_no_token(worker: Worker) -> None:
    worker.client.headers.pop("Authorization")
    response = await worker.client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_comparison_is_constant_time(worker: Worker, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    assert (await worker.client.get("/v1/status")).status_code == 200
    assert calls == [(TOKEN.encode(), TOKEN.encode())]
