"""Local server API contracts (PRD section 6, "Local server API")."""

from typing import Any

import pytest
from pydantic import ValidationError

from studio_contracts import (
    ClientErrorCode,
    ErrorCode,
    Generation,
    GenerationCreate,
    GenerationStatus,
    ServerErrorEnvelope,
    ServerHealth,
    SettingsUpdate,
    SettingsView,
    WorkerCheck,
    WorkerCheckState,
)

PRD_GENERATION: dict[str, Any] = {
    "id": "gen_01JABCDEF",
    "title": "Untitled 2026-11-07 10:00",
    "operation": "song.generate",
    "request": {"style": "...", "lyrics": "...", "seed": 12345, "cot": "full"},
    "status": "running",
    "stage": "semantic",
    "stages": ["plan", "semantic", "synthesis", "decode", "save"],
    "error": None,
    "worker_job_id": "job_01JABCDEF",
    "timings": {"queue_s": 2.1},
    "artifacts": [
        {
            "name": "audio.flac",
            "role": "primary_audio",
            "media_type": "audio/flac",
            "size_bytes": 31457280,
            "url": "/api/generations/gen_01JABCDEF/artifacts/audio.flac",
        }
    ],
    "created_at": "2026-11-07T10:00:00Z",
    "updated_at": "2026-11-07T10:00:09Z",
}


def test_generation_round_trips_prd_example() -> None:
    generation = Generation.model_validate(PRD_GENERATION)
    dumped = generation.model_dump(mode="json")
    assert set(dumped) == set(PRD_GENERATION)
    assert dumped["artifacts"] == PRD_GENERATION["artifacts"]
    assert dumped["request"] == PRD_GENERATION["request"]


def test_generation_statuses_match_prd() -> None:
    assert [s.value for s in GenerationStatus] == [
        "queued",
        "running",
        "downloading",
        "succeeded",
        "failed",
        "canceled",
    ]
    terminal = {s for s in GenerationStatus if s.is_terminal}
    assert terminal == {
        GenerationStatus.succeeded,
        GenerationStatus.failed,
        GenerationStatus.canceled,
    }


def test_client_error_codes_match_prd() -> None:
    assert {c.value for c in ClientErrorCode} == {
        "worker_unreachable",
        "incompatible_worker",
        "download_failed",
        "not_configured",
    }
    # Client codes are separate: the worker never emits them.
    assert not {c.value for c in ClientErrorCode} & {c.value for c in ErrorCode}


def test_server_error_envelope_accepts_worker_and_client_codes() -> None:
    for code in ("queue_full", "worker_unreachable"):
        body: dict[str, Any] = {
            "error": {"code": code, "message": "m", "retryable": True, "details": {}}
        }
        assert ServerErrorEnvelope.model_validate(body).error.code == code
    with pytest.raises(ValidationError):
        ServerErrorEnvelope.model_validate(
            {"error": {"code": "nope", "message": "m", "retryable": True}}
        )


def test_generation_create_forbids_extra_fields() -> None:
    GenerationCreate.model_validate({"operation": "song.generate", "request": {}})
    with pytest.raises(ValidationError):
        GenerationCreate.model_validate({"operation": "song.generate", "request": {}, "params": {}})
    with pytest.raises(ValidationError):
        GenerationCreate.model_validate(
            {"operation": "song.generate", "request": {}, "title": "x" * 201}
        )


def test_settings_view_never_carries_a_token() -> None:
    assert set(SettingsView.model_fields) == {"worker_url", "token_set"}


def test_settings_update_distinguishes_omitted_from_null() -> None:
    assert SettingsUpdate.model_validate({}).model_fields_set == set()
    cleared = SettingsUpdate.model_validate({"token": None})
    assert cleared.model_fields_set == {"token"}
    assert cleared.token is None
    with pytest.raises(ValidationError):
        SettingsUpdate.model_validate({"worker_url": "ftp://x"})
    with pytest.raises(ValidationError):
        SettingsUpdate.model_validate({"tokn": "x"})


def test_settings_update_hides_token_in_repr() -> None:
    update = SettingsUpdate.model_validate({"token": "secret-token-value"})
    assert "secret-token-value" not in repr(update)


def test_worker_check_states() -> None:
    # S-5's five states, plus incompatible and error (owner-approved, docs/decisions.md).
    assert [s.value for s in WorkerCheckState] == [
        "not_configured",
        "unreachable",
        "unauthorized",
        "warming_up",
        "ready",
        "incompatible",
        "error",
    ]


def test_server_health_shape() -> None:
    health = ServerHealth(worker=WorkerCheck(state=WorkerCheckState.ready, error=None, worker=None))
    assert health.model_dump(mode="json") == {
        "ok": True,
        "worker": {"state": "ready", "error": None, "worker": None},
    }
