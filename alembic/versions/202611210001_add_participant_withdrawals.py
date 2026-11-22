"""Durable participant withdrawal attempts and reserved reward snapshots."""
from alembic import op
from app.models import Base
from app import withdrawals  # noqa: F401

revision = '202611210001'
down_revision = '202611150001'
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.tables['participant_withdrawals'].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table('participant_withdrawals')
