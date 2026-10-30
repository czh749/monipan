"""Freeze the existing 31-table v0.4 schema; populated databases use checked adoption."""

from alembic import op
from sqlalchemy import inspect
from app.migrations.frozen_v04 import metadata


revision = "20261018_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if set(inspect(connection).get_table_names()) - {"alembic_version"}:
        raise RuntimeError("Initial upgrade requires an empty database; use checked baseline adoption for existing data")
    metadata().create_all(connection, checkfirst=False)


def downgrade():
    raise RuntimeError("Baseline downgrade would delete business tables and is intentionally blocked; restore a verified backup")
