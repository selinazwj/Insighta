"""Adopt or create the application's base tables before incremental revisions.

Existing installations already have these tables; checkfirst preserves them.
This supplies the missing root for databases provisioned directly by Alembic.
"""

from alembic import op
from app.model_registry import Base

revision = "202607210001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    # This baseline also adopts pre-existing user tables. Their ownership cannot
    # be inferred from a version stamp, so a downgrade must not delete them.
    pass
