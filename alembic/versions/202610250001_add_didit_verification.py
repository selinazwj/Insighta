"""Add Didit session tracking and webhook idempotency tables."""
from alembic import op
import sqlalchemy as sa

revision = "202610250001"
down_revision = "202610230001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "didit_sessions" not in tables:
        op.create_table(
            "didit_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("workflow_id", sa.String(), nullable=True),
            sa.Column("provider_status", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("last_event_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_didit_sessions_id", "didit_sessions", ["id"])
        op.create_index("ix_didit_sessions_user_id", "didit_sessions", ["user_id"])
        op.create_index("ix_didit_sessions_session_id", "didit_sessions", ["session_id"], unique=True)

    if "didit_webhook_events" not in tables:
        op.create_table(
            "didit_webhook_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("webhook_type", sa.String(), nullable=True),
            sa.Column("provider_status", sa.String(), nullable=True),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_didit_webhook_events_id", "didit_webhook_events", ["id"])
        op.create_index(
            "ix_didit_webhook_events_event_id",
            "didit_webhook_events",
            ["event_id"],
            unique=True,
        )
        op.create_index(
            "ix_didit_webhook_events_session_id",
            "didit_webhook_events",
            ["session_id"],
        )


def downgrade():
    op.drop_table("didit_webhook_events")
    op.drop_table("didit_sessions")
