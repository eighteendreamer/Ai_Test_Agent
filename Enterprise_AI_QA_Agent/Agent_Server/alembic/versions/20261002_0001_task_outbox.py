"""Bootstrap only the durable task outbox, preserving legacy rows.

Revision ID: 20261002_0001
Revises: None

Frozen SQL from runtime/postgres_task_outbox.py at main@0f8cdbd. Never call a
runtime store initializer here: later store changes must not mutate this revision.
This is not a baseline for all application tables.
"""

import re

from alembic import op

revision = "20261002_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    config = op.get_context().config
    name = config.attributes["settings"].database.postgres_task_outbox_table
    quote = op.get_bind().dialect.identifier_preparer.quote
    # Match the PostgreSQL interpretation of existing store configuration:
    # unquoted components fold to lowercase, quoted ones retain their spelling.
    parts = [quoted.replace('""', '"') if quoted else plain.strip().lower()
             for quoted, plain in re.findall(r'"((?:[^"]|"")*)"|([^".]+)', name)]
    table = ".".join(quote(part) for part in parts)
    index_prefix = parts[-1]
    op.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
        id BIGSERIAL PRIMARY KEY,
        event_key TEXT NOT NULL UNIQUE,
        stream TEXT NOT NULL,
        payload JSONB NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        published_at TIMESTAMPTZ,
        claimed_by TEXT,
        claimed_until TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS claimed_by TEXT")
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS claimed_until TIMESTAMPTZ")
    op.execute(f"CREATE INDEX IF NOT EXISTS {quote(index_prefix + '_pending_idx')} "
               f"ON {table} (created_at) WHERE published_at IS NULL")
    op.execute(f"CREATE INDEX IF NOT EXISTS {quote(index_prefix + '_pending_stream_idx')} "
               f"ON {table} (stream, created_at) WHERE published_at IS NULL")


def downgrade() -> None:
    # Existing rows may predate this migration. Removing this table would destroy
    # durable queued work; rollback requires a reviewed backup/forward repair.
    raise RuntimeError("Outbox bootstrap is data-preserving and cannot be destructively downgraded.")
