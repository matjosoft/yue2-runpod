"""Worker settings from environment variables (PRD section 8)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MIN_API_KEY_LENGTH = 32
EngineKind = Literal["yue2", "fake"]


class ConfigError(ValueError):
    """Invalid worker configuration. The worker refuses to start."""


@dataclass(frozen=True)
class WorkerSettings:
    api_key: str = field(repr=False)
    engine: EngineKind = "yue2"
    yue2_model_repo: str = "m-a-p/YuE2-3B"
    yue2_vae_repo: str | None = None
    """``None`` means the package default."""
    yue2_model_revision: str | None = None
    yue2_vae_revision: str | None = None
    hf_home: Path = Path("/workspace/hf")
    hf_token: str | None = field(default=None, repr=False)
    jobs_dir: Path = Path("/workspace/jobs")
    max_queue: int = 4
    job_ttl_hours: float = 24.0
    log_level: str = "INFO"
    port: int = 8000
    fake_load_delay_s: float = 0.0
    """``ENGINE=fake`` only: seconds the fake model takes to load (AT-12 over HTTP)."""
    fake_stage_delay_s: float = 0.0
    """``ENGINE=fake`` only: seconds each fake stage takes."""

    def __post_init__(self) -> None:
        if len(self.api_key) < MIN_API_KEY_LENGTH:
            raise ConfigError(f"WORKER_API_KEY must be at least {MIN_API_KEY_LENGTH} characters")
        if self.engine not in ("yue2", "fake"):
            raise ConfigError(f"ENGINE must be 'yue2' or 'fake', not {self.engine!r}")
        if self.max_queue < 1:
            raise ConfigError("MAX_QUEUE must be at least 1")
        if self.job_ttl_hours <= 0:
            raise ConfigError("JOB_TTL_HOURS must be greater than 0")
        if self.fake_load_delay_s < 0 or self.fake_stage_delay_s < 0:
            raise ConfigError("FAKE_*_DELAY_S must not be negative")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WorkerSettings":
        e = os.environ if env is None else env

        def opt(name: str) -> str | None:
            value = e.get(name, "").strip()
            return value or None

        def num[T: (int, float)](name: str, kind: type[T], default: T) -> T:
            raw = opt(name)
            if raw is None:
                return default
            try:
                return kind(raw)
            except ValueError:
                raise ConfigError(f"{name} must be a number, not {raw!r}") from None

        api_key = e.get("WORKER_API_KEY")
        if not api_key:
            raise ConfigError("WORKER_API_KEY is required")
        engine = (opt("ENGINE") or "yue2").lower()
        if engine not in ("yue2", "fake"):
            raise ConfigError(f"ENGINE must be 'yue2' or 'fake', not {engine!r}")
        return cls(
            api_key=api_key,
            engine="fake" if engine == "fake" else "yue2",
            yue2_model_repo=opt("YUE2_MODEL_REPO") or "m-a-p/YuE2-3B",
            yue2_vae_repo=opt("YUE2_VAE_REPO"),
            yue2_model_revision=opt("YUE2_MODEL_REVISION"),
            yue2_vae_revision=opt("YUE2_VAE_REVISION"),
            hf_home=Path(opt("HF_HOME") or "/workspace/hf"),
            hf_token=opt("HF_TOKEN"),
            jobs_dir=Path(opt("JOBS_DIR") or "/workspace/jobs"),
            max_queue=num("MAX_QUEUE", int, 4),
            job_ttl_hours=num("JOB_TTL_HOURS", float, 24.0),
            log_level=(opt("LOG_LEVEL") or "INFO").upper(),
            port=num("PORT", int, 8000),
            fake_load_delay_s=num("FAKE_LOAD_DELAY_S", float, 0.0),
            fake_stage_delay_s=num("FAKE_STAGE_DELAY_S", float, 0.0),
        )
