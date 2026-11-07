"""Server configuration (PRD section 10, "Server configuration")."""

from pathlib import Path

import pytest

from studio_server.config import ConfigError, ServerSettings


def test_defaults_match_prd() -> None:
    s = ServerSettings.from_env({}, dotenv=None)
    assert s.data_dir == Path("~/.studio").expanduser()
    assert s.host == "127.0.0.1"  # never 0.0.0.0 by default
    assert s.port == 8080
    assert s.worker_url is None
    assert s.worker_token is None
    assert s.poll_interval_s == 2
    assert s.unreachable_grace_s == 600
    assert s.download_intermediates is True
    assert s.log_level == "INFO"
    assert s.backend_kind == "http_worker"
    assert s.db_path == s.data_dir / "studio.db"


def test_environment_overrides(tmp_path: Path) -> None:
    s = ServerSettings.from_env(
        {
            "STUDIO_DATA_DIR": str(tmp_path),
            "STUDIO_HOST": "0.0.0.0",
            "STUDIO_PORT": "9000",
            "STUDIO_WORKER_URL": "https://pod.example/",
            "STUDIO_WORKER_TOKEN": "t" * 40,
            "STUDIO_POLL_INTERVAL_S": "0.5",
            "STUDIO_UNREACHABLE_GRACE_S": "30",
            "STUDIO_DOWNLOAD_INTERMEDIATES": "false",
            "STUDIO_LOG_LEVEL": "debug",
        },
        dotenv=None,
    )
    assert s.data_dir == tmp_path
    assert (s.host, s.port) == ("0.0.0.0", 9000)
    assert s.worker_url == "https://pod.example/"
    assert s.worker_token == "t" * 40
    assert s.poll_interval_s == 0.5
    assert s.unreachable_grace_s == 30
    assert s.download_intermediates is False
    assert s.log_level == "DEBUG"


def test_data_dir_expands_home() -> None:
    s = ServerSettings.from_env({"STUDIO_DATA_DIR": "~/elsewhere"}, dotenv=None)
    assert s.data_dir == Path("~/elsewhere").expanduser()


def test_dotenv_fills_in_but_environment_wins(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("STUDIO_PORT=9100\nSTUDIO_WORKER_URL=http://from-dotenv\n")
    s = ServerSettings.from_env({"STUDIO_PORT": "9200"}, dotenv=dotenv)
    assert s.port == 9200
    assert s.worker_url == "http://from-dotenv"


def test_missing_dotenv_is_fine(tmp_path: Path) -> None:
    assert ServerSettings.from_env({}, dotenv=tmp_path / "absent.env").port == 8080


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("STUDIO_PORT", "eighty"),
        ("STUDIO_POLL_INTERVAL_S", "0"),
        ("STUDIO_UNREACHABLE_GRACE_S", "-1"),
        ("STUDIO_DOWNLOAD_INTERMEDIATES", "maybe"),
        ("STUDIO_WORKER_URL", "ftp://pod"),
    ],
)
def test_invalid_values_are_rejected(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        ServerSettings.from_env({name: value}, dotenv=None)


def test_token_is_not_in_repr() -> None:
    s = ServerSettings.from_env({"STUDIO_WORKER_TOKEN": "secret-" + "x" * 40}, dotenv=None)
    assert "secret-" not in repr(s)
