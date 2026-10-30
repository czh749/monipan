"""Run migrations on the explicitly checked connection supplied by app.schema."""

from alembic import context
from app.database import Base
from app import models


configuration = context.config
connection = configuration.attributes.get("connection")
if connection is None or configuration.attributes.get("managed_operation") not in {"adopt", "upgrade"}:
    raise RuntimeError("Use python -m app.schema; direct stamp/upgrade bypasses the required preflight")
context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
