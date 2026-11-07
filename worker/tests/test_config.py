from pathlib import Path

import pytest

from studio_worker.config import ConfigError, WorkerSettings

KEY = "k" * 32


def test_defaults_match_prd_section_8() -> None:
    s = WorkerSettings.from_env({"WORKER_API_KEY": KEY})
    assert s.engine == "yue2"
    assert s.yue2_model_repo == "m-a-p/YuE2-3B"
    assert s.yue2_vae_repo is None
    assert s.hf_home == Path("/workspace/hf")
    assert s.jobs_dir == Path("/workspace/jobs")
    assert s.max_queue == 4
    assert s.job_ttl_hours == 24
    assert s.log_level == "INFO"
    assert s.port == 8000


def test_reads_every_variable() -> None:
    s = WorkerSettings.from_env(
        {
            "WORKER_API_KEY": KEY,
            "ENGINE": "fake",
            "YUE2_MODEL_REPO": "m/r",
            "YUE2_VAE_REPO": "m/v",
            "YUE2_MODEL_REVISION": "abc",
            "YUE2_VAE_REVISION": "def",
            "HF_HOME": "/hf",
            "HF_TOKEN": "hf_secret",
            "JOBS_DIR": "/jobs",
            "MAX_QUEUE": "2",
            "JOB_TTL_HOURS": "1.5",
            "LOG_LEVEL": "debug",
            "PORT": "9000",
            "FAKE_LOAD_DELAY_S": "0.5",
            "FAKE_STAGE_DELAY_S": "0.25",
        }
    )
    assert (s.engine, s.yue2_model_repo, s.yue2_vae_repo) == ("fake", "m/r", "m/v")
    assert (s.yue2_model_revision, s.yue2_vae_revision) == ("abc", "def")
    assert (s.hf_home, s.hf_token, s.jobs_dir) == (Path("/hf"), "hf_secret", Path("/jobs"))
    assert (s.max_queue, s.job_ttl_hours, s.log_level, s.port) == (2, 1.5, "DEBUG", 9000)
    assert (s.fake_load_delay_s, s.fake_stage_delay_s) == (0.5, 0.25)


@pytest.mark.parametrize("key", [None, "", "k" * 31])
def test_refuses_to_start_without_a_long_key(key: str | None) -> None:
    env = {} if key is None else {"WORKER_API_KEY": key}
    with pytest.raises(ConfigError, match="WORKER_API_KEY"):
        WorkerSettings.from_env(env)


@pytest.mark.parametrize(
    ("name", "value"),
    [("ENGINE", "torch"), ("MAX_QUEUE", "0"), ("MAX_QUEUE", "four"), ("JOB_TTL_HOURS", "0")],
)
def test_rejects_bad_values(name: str, value: str) -> None:
    with pytest.raises(ConfigError):
        WorkerSettings.from_env({"WORKER_API_KEY": KEY, name: value})


def test_secrets_are_not_in_repr() -> None:
    s = WorkerSettings(api_key="secret-" + KEY, hf_token="hf_secret")
    assert "secret" not in repr(s)
