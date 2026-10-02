"""Bootstrap tool jobs and artifacts without modifying existing rows.

Revision ID: 20261002_0002
Revises: 20261002_0001

Frozen SQL from runtime/postgres_tool_job_store.py at main@83ee431.
Only the currently supported table shape is covered, not arbitrary older schemas.
"""

import re

from alembic import op

revision = "20261002_0002"
down_revision = "20261002_0001"
branch_labels = None
depends_on = None


def _identifier(name):
    quote = op.get_bind().dialect.identifier_preparer.quote
    parts = [quoted.replace('""', '"') if quoted else plain.strip().lower()
             for quoted, plain in re.findall(r'"((?:[^"]|"")*)"|([^".]+)', name)]
    return ".".join(quote(part) for part in parts), parts[-1]


def upgrade() -> None:
    database = op.get_context().config.attributes["settings"].database
    jobs, job_name = _identifier(database.postgres_tool_job_table)
    artifacts, artifact_name = _identifier(database.postgres_tool_artifact_table)
    quote = op.get_bind().dialect.identifier_preparer.quote
    op.execute(f"""CREATE TABLE IF NOT EXISTS {jobs} (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        call_id TEXT NOT NULL,
        tool_key TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 1,
        summary TEXT NOT NULL DEFAULT '',
        error_message TEXT NULL,
        artifact_count INTEGER NOT NULL DEFAULT 0,
        input_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        output_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        heartbeat_at TIMESTAMPTZ NULL,
        started_at TIMESTAMPTZ NULL,
        completed_at TIMESTAMPTZ NULL
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS {artifacts} (
        id TEXT PRIMARY KEY,
        tool_job_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        tool_key TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        label TEXT NULL,
        path TEXT NOT NULL,
        storage_mode TEXT NOT NULL DEFAULT 'path_only',
        content_text TEXT NOT NULL DEFAULT '',
        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL
    )""")
    for index, table, columns in (
        (f"idx_{job_name}_session_created", jobs, "session_id, created_at DESC"),
        (f"idx_{job_name}_status_updated", jobs, "status, updated_at DESC"),
        (f"idx_{artifact_name}_job_created", artifacts, "tool_job_id, created_at ASC"),
        (f"idx_{artifact_name}_session_created", artifacts, "session_id, created_at ASC"),
    ):
        op.execute(f"CREATE INDEX IF NOT EXISTS {quote(index)} ON {table} ({columns})")


def downgrade() -> None:
    raise RuntimeError("Tool jobs and artifacts contain durable evidence and cannot be destructively downgraded.")
