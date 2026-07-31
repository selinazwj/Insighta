"""remove legacy raw-ID verification columns

Revision ID: 202607310001
Revises: 202607220001
Create Date: 2026-07-31 22:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202607310001"
down_revision: Union[str, None] = "202607220001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_COLUMNS = (
    "id_document_path",
    "id_review_status",
    "id_uploaded_at",
)


def _user_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "users" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("users")}


def upgrade() -> None:
    existing = _user_columns()
    removable = [name for name in LEGACY_COLUMNS if name in existing]
    if not removable:
        return
    with op.batch_alter_table("users") as batch:
        for name in removable:
            batch.drop_column(name)


def downgrade() -> None:
    existing = _user_columns()
    definitions = {
        "id_document_path": sa.String(),
        "id_review_status": sa.String(),
        "id_uploaded_at": sa.DateTime(),
    }
    with op.batch_alter_table("users") as batch:
        for name in LEGACY_COLUMNS:
            if name not in existing:
                batch.add_column(sa.Column(name, definitions[name], nullable=True))
