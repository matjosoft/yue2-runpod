"""Parameters of the ``song.generate`` operation (PRD section 6).

The length limits are app-side guardrails, not model facts. They are module constants
so that they can be changed in one place; they are not runtime settings, because the
JSON Schema published in ``/v1/capabilities`` must be stable (docs/decisions.md).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

STYLE_MAX_CHARS = 1000
LYRICS_MAX_CHARS = 8000
SEED_MAX = 2_147_483_647
CFG_SCALE_MAX = 20.0
"""The yue2 engine rejects ``cfg_scale`` above 20 (docs/yue2-api-notes.md, section 3)."""
ABC_MAX_CHARS = 16000
"""App-side guardrail for a supplied score. The engine's real limit is in tokens: the
whole prompt, score included, must fit in 24576 - 9000 tokens (docs/yue2-api-notes.md,
OQ-3), and a score that overflows it fails the job as ``engine_error``."""

Cot = Literal["full", "melody", "off"]


class SongParams(BaseModel):
    """``song.generate`` params. Unknown fields are rejected so that typos fail loudly."""

    model_config = ConfigDict(extra="forbid")

    style: str = Field(min_length=1, max_length=STYLE_MAX_CHARS)
    lyrics: str = Field(min_length=1, max_length=LYRICS_MAX_CHARS)
    seed: int | None = Field(default=None, ge=0, le=SEED_MAX, strict=True)
    cot: Cot = "full"
    cfg_scale: float | None = Field(default=None, gt=0, le=CFG_SCALE_MAX, strict=True)
    abc: str | None = Field(default=None, min_length=1, max_length=ABC_MAX_CHARS)
    """A score to sing instead of planning one (added after M5). With ``cot="full"`` it is
    read as a chord-annotated score, with ``cot="melody"`` as a melody-only one; the
    engine does not check which it is. ``cot="off"`` cannot take a score."""

    @field_validator("abc")
    @classmethod
    def _abc_needs_planning(cls, value: str | None, info: ValidationInfo) -> str | None:
        # Mirrors the engine's own check (yue2 protocol.py:99-100), so the request fails
        # at submit with a field error instead of later as engine_error.
        if value is None:
            return None
        if not value.strip():
            raise ValueError("the score must not be blank")
        if info.data.get("cot") == "off":
            raise ValueError("a score needs planning mode full or melody, not off")
        return value
