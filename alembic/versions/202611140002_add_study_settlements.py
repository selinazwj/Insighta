"""Add review deadlines and durable original-card settlement records."""
from alembic import op
import sqlalchemy as sa
from app.models import StudySettlement

revision = "202611140002"
down_revision = "202611140001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table, columns in {
        "surveys": [("deadline_at", sa.DateTime()), ("close_reason", sa.String()),
                    ("stripe_funding_payment_intent_id", sa.String())],
        "responses": [("review_due_at", sa.DateTime()), ("payout_reviewed_at", sa.DateTime())],
    }.items():
        present = {c['name'] for c in sa.inspect(bind).get_columns(table)}
        for name, type_ in columns:
            if name not in present:
                op.add_column(table, sa.Column(name, type_, nullable=True))
    StudySettlement.__table__.create(bind=bind, checkfirst=True)


def downgrade():
    op.drop_table("study_settlements")
    for table, names in {"responses": ["review_due_at", "payout_reviewed_at"],
                         "surveys": ["deadline_at", "close_reason", "stripe_funding_payment_intent_id"]}.items():
        for name in names:
            op.drop_column(table, name)
