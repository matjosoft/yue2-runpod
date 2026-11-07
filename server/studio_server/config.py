"""Server settings from environment variables and ``.env`` (PRD section 10).

Precedence, highest first: values saved through Settings (in the database, applied by
``core.worker``), environment variables, ``.env``, defaults. Only the worker URL and
token can be saved through Settings.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_DATA_DIR = "~/.studio"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Invalid server configuration. The server refuses to start."""


def is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://")) and len(value) > len("https://")


@dataclass(frozen=True)
class ServerSettings:
    data_dir: Path = field(default_factory=lambda: Path(DEFAULT_DATA_DIR).expanduser())
    host: str = "127.0.0.1"
    port: int = 8080
    worker_url: str | None = None
    worker_token: str | None = field(default=None, repr=False)
    poll_interval_s: float = 2.0
    unreachable_grace_s: float = 600.0
    download_intermediates: bool = True
    log_level: str = "INFO"
    backend_kind: str = "http_worker"
    """``MusicBackend`` implementation (PRD section 7). The MVP has only ``http_worker``."""
    check_interval_s: float = 5.0
    """How often the poller refreshes the cached worker check behind ``/api/health``."""
    retry_base_s: float = 0.5
    """First backoff delay for retried worker calls; doubles each retry (tests use 0)."""

    def __post_init__(self) -> None:
        if not 0 < self.port < 65536:
            raise ConfigError("STUDIO_PORT must be between 1 and 65535")
        if self.poll_interval_s <= 0:
            raise ConfigError("STUDIO_POLL_INTERVAL_S must be greater than 0")
        if self.unreachable_grace_s < 0:
            raise ConfigError("STUDIO_UNREACHABLE_GRACE_S must not be negative")
        if self.worker_url is not None and not is_http_url(self.worker_url):
            raise ConfigError("STUDIO_WORKER_URL must start with http:// or https://")
        if self.check_interval_s <= 0 or self.retry_base_s < 0:
            raise ConfigError("check interval and retry delay must not be negative")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, dotenv: Path | None = Path(".env")
    ) -> "ServerSettings":
        """Read ``STUDIO_*`` variables. ``env`` defaults to ``os.environ``; values from
        the ``dotenv`` file fill in only what the environment does not set."""
        merged: dict[str, str] = {}
        if dotenv is not None and dotenv.is_file():
            merged.update({k: v for k, v in dotenv_values(dotenv).items() if v is not None})
        merged.update(os.environ if env is None else env)

        def opt(name: str) -> str | None:
            value = merged.get(name, "").strip()
            return value or None

        def num[T: (int, float)](name: str, kind: type[T], default: T) -> T:
            raw = opt(name)
            if raw is None:
                return default
            try:
                return kind(raw)
            except ValueError:
                raise ConfigError(f"{name} must be a number, not {raw!r}") from None

        def flag(name: str, default: bool) -> bool:
            raw = opt(name)
            if raw is None:
                return default
            if raw.lower() in _TRUE:
                return True
            if raw.lower() in _FALSE:
                return False
            raise ConfigError(f"{name} must be true or false, not {raw!r}")

        return cls(
            data_dir=Path(opt("STUDIO_DATA_DIR") or DEFAULT_DATA_DIR).expanduser(),
            host=opt("STUDIO_HOST") or "127.0.0.1",
            port=num("STUDIO_PORT", int, 8080),
            worker_url=opt("STUDIO_WORKER_URL"),
            worker_token=opt("STUDIO_WORKER_TOKEN"),
            poll_interval_s=num("STUDIO_POLL_INTERVAL_S", float, 2.0),
            unreachable_grace_s=num("STUDIO_UNREACHABLE_GRACE_S", float, 600.0),
            download_intermediates=flag("STUDIO_DOWNLOAD_INTERMEDIATES", True),
            log_level=(opt("STUDIO_LOG_LEVEL") or "INFO").upper(),
        )
