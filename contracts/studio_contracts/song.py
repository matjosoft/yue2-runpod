"""Parameters of the ``song.generate`` operation (PRD section 6).

The length limits are app-side guardrails, not model facts. They are module constants
so that they can be changed in one place; they are not runtime settings, because the
JSON Schema published in ``/v1/capabilities`` must be stable (docs/decisions.md).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

STYLE_MAX_CHARS = 1000
LYRICS_MAX_CHARS = 8000
SEED_MAX = 2_147_483_647
CFG_SCALE_MAX = 20.0
"""The yue2 engine rejects ``cfg_scale`` above 20 (docs/yue2-api-notes.md, section 3)."""

Cot = Literal["full", "melody", "off"]


class SongParams(BaseModel):
    """``song.generate`` params. Unknown fields are rejected so that typos fail loudly."""

    model_config = ConfigDict(extra="forbid")

    style: str = Field(min_length=1, max_length=STYLE_MAX_CHARS)
    lyrics: str = Field(min_length=1, max_length=LYRICS_MAX_CHARS)
    seed: int | None = Field(default=None, ge=0, le=SEED_MAX, strict=True)
    cot: Cot = "full"
    cfg_scale: float | None = Field(default=None, gt=0, le=CFG_SCALE_MAX, strict=True)
