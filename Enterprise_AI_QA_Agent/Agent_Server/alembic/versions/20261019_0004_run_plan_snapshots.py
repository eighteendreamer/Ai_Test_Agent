"""Preserve complete claim-time Run plans independently of the mutable record.

Revision ID: 20261019_0004
Revises: 20261002_0003
Existing Run records are not rewritten or represented as recovered history.
Fresh installations create the Run table through its existing initializer.
"""
import re

from alembic import op

revision = "20261019_0004"
down_revision = "20261002_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    database = op.get_context().config.attributes["settings"].database
    quote = op.get_bind().dialect.identifier_preparer.quote
    parts = [quoted.replace('""', '"') if quoted else plain.strip().lower()
        for quoted, plain in re.findall(r'"((?:[^"]|"")*)"|([^".]+)', database.postgres_test_run_table)]
    table = ".".join(quote(part) for part in parts)
    op.execute(f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS "
        "plan_snapshots JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    raise RuntimeError("Claim-time plan evidence cannot be destructively downgraded.")
