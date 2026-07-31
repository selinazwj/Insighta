"""create background jobs

Revision ID: 202607310001
Revises: 202607220001
Create Date: 2026-07-31 00:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202607310001"
down_revision: Union[str, None] = "202607220001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "background_jobs" not in inspector.get_table_names():
        op.create_table(
            "background_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("job_type", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("dedupe_key", sa.String(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("run_after", sa.DateTime(), nullable=False),
            sa.Column("locked_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    inspector = sa.inspect(bind)
    existing = {index.get("name") for index in inspector.get_indexes("background_jobs")}
    indexes = [
        ("ix_background_jobs_id", ["id"], False),
        ("ix_background_jobs_job_type", ["job_type"], False),
        ("ix_background_jobs_status", ["status"], False),
        ("ix_background_jobs_dedupe_key", ["dedupe_key"], True),
        ("ix_background_jobs_run_after", ["run_after"], False),
        ("ix_background_jobs_status_run_after", ["status", "run_after"], False),
    ]
    for name, columns, unique in indexes:
        if name not in existing:
            op.create_index(name, "background_jobs", columns, unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "background_jobs" in inspector.get_table_names():
        op.drop_table("background_jobs")
