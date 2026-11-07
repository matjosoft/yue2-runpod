"""Maps ``backend.kind`` to a ``MusicBackend`` implementation (PRD section 7).

A new backend (serverless, local yue2.cpp, ComfyUI) is a new class plus one entry in
``BACKENDS``; nothing else changes.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from studio_server.core.backend.base import MusicBackend
from studio_server.core.backend.http_worker import HttpWorkerBackend


@dataclass(frozen=True)
class BackendConfig:
    kind: str
    url: str
    token: str = field(repr=False)
    retry_base_s: float = 0.5
    transport: httpx.AsyncBaseTransport | None = None
    """Tests route the backend to an in-process worker app through this."""


def _http_worker(config: BackendConfig) -> MusicBackend:
    return HttpWorkerBackend(
        config.url, config.token, transport=config.transport, retry_base_s=config.retry_base_s
    )


BACKENDS: dict[str, Callable[[BackendConfig], MusicBackend]] = {
    "http_worker": _http_worker,
}


class UnknownBackendError(ValueError):
    pass


def create_backend(config: BackendConfig) -> MusicBackend:
    try:
        factory = BACKENDS[config.kind]
    except KeyError:
        raise UnknownBackendError(f"unknown backend kind {config.kind!r}") from None
    return factory(config)
