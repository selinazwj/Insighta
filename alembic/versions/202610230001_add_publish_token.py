"""Deduplicate retries of a listing submission."""
from alembic import op
import sqlalchemy as sa

revision = '202610230001'
down_revision = '202610200001'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'surveys' not in inspector.get_table_names():
        return
    if 'publish_token' not in {c['name'] for c in inspector.get_columns('surveys')}:
        op.add_column('surveys', sa.Column('publish_token', sa.String(), nullable=True))
    if 'ix_surveys_publish_token' not in {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('surveys')}:
        op.create_index('ix_surveys_publish_token', 'surveys', ['publish_token'], unique=True)


def downgrade():
    op.drop_index('ix_surveys_publish_token', table_name='surveys')
    op.drop_column('surveys', 'publish_token')
