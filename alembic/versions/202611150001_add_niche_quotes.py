"""Manual niche requests and immutable quote versions."""
from alembic import op
from app.models import Base
from app import niche_models  # noqa: F401

revision = '202611150001'
down_revision = '202611140003'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('niche_requests', 'niche_quotes'):
        Base.metadata.tables[name].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table('niche_quotes')
    op.drop_table('niche_requests')
