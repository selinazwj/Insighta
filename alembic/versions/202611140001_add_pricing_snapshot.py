"""Preserve the quote used to fund each new listing; legacy rows remain null."""
from alembic import op
import sqlalchemy as sa

revision = "202611140001"
down_revision = "202610310001"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("surveys")}
    if "pricing_snapshot" not in columns:
        op.add_column("surveys", sa.Column("pricing_snapshot", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("surveys", "pricing_snapshot")
