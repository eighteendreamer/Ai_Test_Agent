"""Version compaction claims, receipts and event watermarks without replaying work.

Revision ID: 20261002_0003
Revises: 20261002_0002

Frozen SQL from runtime/postgres_compaction_record_store.py at main@66a703b.
Supports its current table shape and the legacy shape missing only the watermark.
"""

import re

from alembic import op

revision = "20261002_0003"
down_revision = "20261002_0002"
branch_labels = None
depends_on = None


def _identifier(name):
    quote = op.get_bind().dialect.identifier_preparer.quote
    parts = [quoted.replace('""', '"') if quoted else plain.strip().lower()
             for quoted, plain in re.findall(r'"((?:[^"]|"")*)"|([^".]+)', name)]
    return ".".join(quote(part) for part in parts), parts[-1]


def upgrade() -> None:
    database = op.get_context().config.attributes["settings"].database
    table, table_name = _identifier(database.postgres_compaction_table)
    quote = op.get_bind().dialect.identifier_preparer.quote
    op.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
        compaction_key TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        last_compacted_event_sequence BIGINT NOT NULL DEFAULT 0,
        compression_version TEXT NOT NULL,
        status TEXT NOT NULL,
        memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        last_error TEXT NULL,
        claimed_by TEXT NULL,
        claimed_until TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ NULL
    )""")
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
               "last_compacted_event_sequence BIGINT NOT NULL DEFAULT 0")
    for suffix, columns in (
        ("session_idx", "session_id, updated_at DESC"),
        ("status_idx", "status, updated_at DESC"),
    ):
        op.execute(f"CREATE INDEX IF NOT EXISTS {quote(f'{table_name}_{suffix}')} ON {table} ({columns})")


def downgrade() -> None:
    raise RuntimeError("Compaction claims and receipts cannot be destructively downgraded.")
