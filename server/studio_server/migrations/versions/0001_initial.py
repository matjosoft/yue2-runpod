"""Initial schema: generations and settings.

Revision ID: 0001
Revises:
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("stages_json", sa.Text(), nullable=False),
        sa.Column("error_json", sa.Text(), nullable=True),
        sa.Column("worker_job_id", sa.String(64), nullable=True),
        sa.Column("client_request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("timings_json", sa.Text(), nullable=False),
        sa.Column("artifacts_json", sa.Text(), nullable=False),
        sa.Column("data_dir", sa.Text(), nullable=False),
    )
    op.create_index("ix_generations_status", "generations", ["status"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_index("ix_generations_status", table_name="generations")
    op.drop_table("generations")
