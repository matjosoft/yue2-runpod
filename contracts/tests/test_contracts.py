"""Contract models (PRD section 6). The JSON examples are copied verbatim from the PRD."""

import re
from typing import Any

import pytest
from pydantic import ValidationError

from studio_contracts import (
    API_VERSION,
    Artifact,
    ArtifactRole,
    Capabilities,
    ErrorCode,
    ErrorEnvelope,
    Health,
    Job,
    JobState,
    JobSubmit,
    ModelState,
    SongParams,
    WorkerStatus,
)

PRD_SUBMIT: dict[str, Any] = {
    "client_request_id": "0b8e5f0c-6a3e-4d62-9d0e-2f6f3d1f7a11",
    "operation": "song.generate",
    "params": {
        "style": "English, jazz-funk, warm lead vocal, Rhodes, bass and drums",
        "lyrics": "[Verse]\n...\n[Chorus]\n...",
        "seed": 12345,
        "cot": "full",
        "cfg_scale": 1.2,
    },
}

PRD_JOB: dict[str, Any] = {
    "id": "job_01JABCDEF",
    "client_request_id": "0b8e5f0c-6a3e-4d62-9d0e-2f6f3d1f7a11",
    "operation": "song.generate",
    "state": "running",
    "stage": "semantic",
    "stages": ["plan", "semantic", "synthesis", "decode", "save"],
    "progress": None,
    "params": {"style": "...", "lyrics": "...", "cot": "full"},
    "params_resolved": {"seed": 12345},
    "created_at": "2026-11-07T10:00:00Z",
    "started_at": "2026-11-07T10:00:02Z",
    "finished_at": None,
    "timings": {"queue_s": 2.1, "plan_s": 9.4},
    "error": None,
    "artifacts": [],
}

PRD_STATUS: dict[str, Any] = {
    "uptime_s": 1234,
    "idle_seconds": 87,
    "model": {"state": "ready", "error": None},
    "gpu": {"name": "NVIDIA GeForce RTX 4090", "vram_total_mb": 24564, "vram_used_mb": 11500},
    "queue": {"queued": 0, "running": None, "max": 4},
}

PRD_CAPABILITIES: dict[str, Any] = {
    "api_version": "1.0",
    "worker_version": "0.1.0",
    "engine": {
        "kind": "yue2",
        "package_version": "0.1.5",
        "models": {"lm": "m-a-p/YuE2-3B", "vae": "m-a-p/YuE2-Vae"},
    },
    "operations": [
        {
            "name": "song.generate",
            "stages": ["plan", "semantic", "synthesis", "decode", "save"],
            "params_schema": {},
        }
    ],
}


def song(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"style": "s", "lyrics": "[Verse]\nla"}
    params.update(overrides)
    return params


def test_api_version_is_major_minor() -> None:
    assert re.fullmatch(r"\d+\.\d+", API_VERSION)
    assert API_VERSION.split(".")[0] == "1"


# --- song.generate params -----------------------------------------------------------


def test_song_params_accepts_prd_example() -> None:
    p = SongParams.model_validate(PRD_SUBMIT["params"])
    assert p.seed == 12345
    assert p.cfg_scale == 1.2


def test_song_params_defaults() -> None:
    p = SongParams.model_validate(song())
    assert p.cot == "full"
    assert p.seed is None
    assert p.cfg_scale is None


def test_song_params_dump_omits_absent_optionals() -> None:
    # Matches the PRD job example: params carry cot but no seed when none was sent.
    dumped = SongParams.model_validate(song()).model_dump(mode="json", exclude_none=True)
    assert dumped == {"style": "s", "lyrics": "[Verse]\nla", "cot": "full"}


@pytest.mark.parametrize(
    ("field", "ok", "bad"),
    [
        ("style", ["x", "x" * 1000], ["", "x" * 1001]),
        ("lyrics", ["x", "x" * 8000], ["", "x" * 8001]),
        ("seed", [0, 2147483647], [-1, 2147483648]),
        ("cot", ["full", "melody", "off"], ["FULL", "none"]),
        ("cfg_scale", [0.01, 1.2, 20], [0, -1, 20.01]),
    ],
)
def test_song_params_limits(field: str, ok: list[Any], bad: list[Any]) -> None:
    for value in ok:
        SongParams.model_validate(song(**{field: value}))
    for value in bad:
        with pytest.raises(ValidationError):
            SongParams.model_validate(song(**{field: value}))


@pytest.mark.parametrize("missing", ["style", "lyrics"])
def test_song_params_required(missing: str) -> None:
    params = song()
    del params[missing]
    with pytest.raises(ValidationError):
        SongParams.model_validate(params)


def test_song_params_forbid_extra() -> None:
    with pytest.raises(ValidationError) as exc:
        SongParams.model_validate(song(styel="typo"))
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("cot", ["full", "melody"])
def test_song_params_accepts_a_score_with_planning(cot: str) -> None:
    score = "X:1\nM:4/4\nL:1/8\nK:C\nCDEF GABc|\n"
    p = SongParams.model_validate(song(cot=cot, abc=score))
    assert p.abc == score  # kept verbatim, whitespace included
    assert p.model_dump(mode="json", exclude_none=True)["abc"] == score


def test_song_params_score_is_optional_and_omitted_when_absent() -> None:
    assert SongParams.model_validate(song()).abc is None


@pytest.mark.parametrize(
    "params",
    [
        song(cot="off", abc="X:1\nK:C\nC|"),  # the engine needs full or melody
        song(abc=""),
        song(abc="  \n\t"),  # the engine rejects a blank score
        song(abc="x" * 16001),
        song(abc=123),
    ],
)
def test_song_params_rejects_bad_scores(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as exc:
        SongParams.model_validate(params)
    assert exc.value.errors()[0]["loc"] == ("abc",)


def test_song_params_score_limit() -> None:
    SongParams.model_validate(song(abc="x" * 16000))


def test_song_params_rejects_string_seed_and_bool_cfg() -> None:
    with pytest.raises(ValidationError):
        SongParams.model_validate(song(seed="12"))
    with pytest.raises(ValidationError):
        SongParams.model_validate(song(cfg_scale=True))


# --- Jobs -----------------------------------------------------------------------------


def test_submit_parses_prd_example() -> None:
    submit = JobSubmit.model_validate(PRD_SUBMIT)
    assert submit.operation == "song.generate"
    assert submit.params["seed"] == 12345


def test_submit_rejects_extra_and_empty_ids() -> None:
    with pytest.raises(ValidationError):
        JobSubmit.model_validate({**PRD_SUBMIT, "priority": 1})
    with pytest.raises(ValidationError):
        JobSubmit.model_validate({**PRD_SUBMIT, "client_request_id": ""})


def test_job_round_trips_prd_example() -> None:
    job = Job.model_validate(PRD_JOB)
    assert job.state is JobState.running
    dumped = job.model_dump(mode="json")
    assert dumped["created_at"] == "2026-11-07T10:00:00Z"
    assert dumped == PRD_JOB


def test_job_rejects_progress_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Job.model_validate({**PRD_JOB, "progress": 1.5})


def test_job_timestamps_must_carry_a_timezone() -> None:
    with pytest.raises(ValidationError):
        Job.model_validate({**PRD_JOB, "created_at": "2026-11-07T10:00:00"})


def test_job_states_and_roles_match_prd() -> None:
    assert [s.value for s in JobState] == ["queued", "running", "succeeded", "failed", "canceled"]
    assert [r.value for r in ArtifactRole] == [
        "primary_audio",
        "score",
        "plan",
        "result",
        "intermediate",
        "other",
    ]
    assert JobState.succeeded.is_terminal and not JobState.running.is_terminal


def test_artifact_fields() -> None:
    art = Artifact(
        name="audio.flac",
        role=ArtifactRole.primary_audio,
        media_type="audio/flac",
        size_bytes=10,
        sha256="0" * 64,
    )
    assert set(art.model_dump()) == {"name", "role", "media_type", "size_bytes", "sha256"}
    with pytest.raises(ValidationError):
        Artifact.model_validate({**art.model_dump(), "sha256": "xyz"})


# --- Errors ---------------------------------------------------------------------------


def test_error_codes_match_prd() -> None:
    assert {c.value for c in ErrorCode} == {
        "unauthorized",
        "not_found",
        "conflict",
        "validation_error",
        "queue_full",
        "not_ready",
        "oom",
        "engine_error",
        "worker_restarted",
        "internal",
    }


def test_error_envelope_shape() -> None:
    env = ErrorEnvelope.model_validate(
        {"error": {"code": "queue_full", "message": "Queue full", "retryable": True}}
    )
    assert env.model_dump(mode="json") == {
        "error": {"code": "queue_full", "message": "Queue full", "retryable": True, "details": {}}
    }


# --- Status, capabilities, health -----------------------------------------------------


def test_status_round_trips_prd_example() -> None:
    status = WorkerStatus.model_validate(PRD_STATUS)
    assert status.model.state is ModelState.ready
    assert status.model_dump(mode="json") == PRD_STATUS


def test_status_allows_no_gpu_and_model_error() -> None:
    status = WorkerStatus.model_validate(
        {**PRD_STATUS, "gpu": None, "model": {"state": "error", "error": "no BF16"}}
    )
    assert status.gpu is None
    assert [s.value for s in ModelState] == ["loading", "ready", "error"]


def test_capabilities_round_trip_prd_example() -> None:
    caps = Capabilities.model_validate(PRD_CAPABILITIES)
    assert caps.model_dump(mode="json") == PRD_CAPABILITIES


def test_health_is_ok_only() -> None:
    assert Health().model_dump() == {"ok": True}
