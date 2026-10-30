"""Add the research directory without modifying the frozen v0.4 baseline."""

from alembic import op
import sqlalchemy as sa

revision = "20261026_02"
down_revision = "20261018_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stocks", sa.Column("research_only", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.create_table(
        "security_catalog_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(6), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("full_name", sa.String(256), nullable=True),
        sa.Column("board", sa.String(32), nullable=False),
        sa.Column("industry", sa.String(80), nullable=False),
        sa.Column("listing_date", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=False),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("exchange", "symbol", name="uq_catalog_exchange_symbol"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    for column in ("symbol", "name", "collected_at"):
        op.create_index("ix_security_catalog_entries_" + column, "security_catalog_entries", [column])


def downgrade():
    raise RuntimeError("Research catalog downgrade requires a reviewed data-preserving rollback plan")
