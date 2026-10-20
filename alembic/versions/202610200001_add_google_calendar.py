"""Add encrypted Google Calendar connections and booking event mappings."""
from alembic import op
import sqlalchemy as sa

revision = '202610200001'
down_revision = '202607310002'
branch_labels = None
depends_on = None


def upgrade():
    existing = sa.inspect(op.get_bind()).get_table_names()
    if 'google_calendar_connections' not in existing:
        op.create_table('google_calendar_connections',
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
            sa.Column('google_subject', sa.String(), nullable=False),
            sa.Column('google_email', sa.String(), nullable=False),
            sa.Column('refresh_token_encrypted', sa.Text(), nullable=False),
            sa.Column('access_token_encrypted', sa.Text(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('needs_reconnect', sa.Boolean(), nullable=False),
        )
    if 'google_calendar_events' not in existing:
        op.create_table('google_calendar_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('response_id', sa.Integer(), sa.ForeignKey('responses.id'), nullable=False),
            sa.Column('session_index', sa.Integer(), nullable=False),
            sa.Column('event_id', sa.String(), nullable=False),
            sa.Column('google_subject', sa.String(), nullable=False),
            sa.UniqueConstraint('user_id', 'response_id', 'session_index'),
        )


def downgrade():
    op.drop_table('google_calendar_events')
    op.drop_table('google_calendar_connections')
