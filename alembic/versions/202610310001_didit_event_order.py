"""Track provider event order so delayed deliveries cannot undo newer results."""
from alembic import op
import sqlalchemy as sa

revision = "202610310001"
down_revision = "202610250001"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("didit_sessions")}
    if "provider_event_at" not in columns:
        op.add_column("didit_sessions", sa.Column("provider_event_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("didit_sessions", "provider_event_at")
