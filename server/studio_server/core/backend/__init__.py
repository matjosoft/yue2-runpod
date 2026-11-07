"""Backend adapter: the only path from the server to the GPU (PRD section 5)."""

from studio_server.core.backend.base import (
    BackendError,
    Closeable,
    JobRef,
    JobView,
    MusicBackend,
)
from studio_server.core.backend.factory import (
    BACKENDS,
    BackendConfig,
    UnknownBackendError,
    create_backend,
)

__all__ = [
    "BACKENDS",
    "BackendConfig",
    "BackendError",
    "Closeable",
    "JobRef",
    "JobView",
    "MusicBackend",
    "UnknownBackendError",
    "create_backend",
]
