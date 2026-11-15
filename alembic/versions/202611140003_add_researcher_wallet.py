"""Researcher cash ledger, funding lots, promotional credits and invoices."""
from alembic import op
import sqlalchemy as sa
from app.models import Base
from app import wallet_models

revision = '202611140003'
down_revision = '202611140002'
branch_labels = None
depends_on = None
TABLES = ['wallet_accounts', 'wallet_ledger', 'wallet_topups', 'wallet_funding_lots', 'wallet_coupons',
          'wallet_promo_grants', 'wallet_payments', 'wallet_allocations', 'wallet_promo_uses',
          'wallet_refund_parts', 'billing_invoices']


def upgrade():
    bind = op.get_bind()
    for table, columns in {
        'surveys': [('funding_source', sa.String()), ('refund_destination', sa.String())],
        'study_settlements': [('refund_destination', sa.String()), ('refund_gross_cents', sa.Integer())],
    }.items():
        present = {c['name'] for c in sa.inspect(bind).get_columns(table)}
        for name, type_ in columns:
            if name not in present:
                op.add_column(table, sa.Column(name, type_, nullable=True))
    for name in TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade():
    for name in reversed(TABLES): op.drop_table(name)
    for table, names in {'surveys':['funding_source','refund_destination'],
                         'study_settlements':['refund_destination','refund_gross_cents']}.items():
        for name in names: op.drop_column(table, name)
