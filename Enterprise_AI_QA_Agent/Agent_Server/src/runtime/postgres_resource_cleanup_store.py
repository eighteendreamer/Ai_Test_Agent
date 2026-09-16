"""Durable cleanup receipts; a successful Stream delivery is ACKed after commit."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from psycopg import sql

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.resource_lease_manager import ExpiredResourceLease


class PostgresResourceCleanupStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._table = sql.Identifier(settings.database.postgres_resource_cleanup_table)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    task_id TEXT PRIMARY KEY,
                    record JSONB NOT NULL,
                    status TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    error_type TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
            """).format(self._table))

    async def begin(self, task_id: str, lease: ExpiredResourceLease, *, worker_id: str) -> bool:
        """False means an earlier delivery already committed its terminal receipt.

        Delivery ownership is provided by RedisTaskWorker. The external callback
        must also be idempotent: a crash can occur after deletion but before this
        receipt commits. Docker cleanup therefore requires an immutable full ID.
        """
        record = asdict(lease)
        record.pop("lease_token")
        return await asyncio.to_thread(self._begin_sync, task_id, record, worker_id)

    def _begin_sync(self, task_id: str, record: dict, worker_id: str) -> bool:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                INSERT INTO {table} AS job (task_id, record, status, worker_id)
                VALUES (%s, %s::jsonb, 'running', %s)
                ON CONFLICT (task_id) DO UPDATE
                SET status = 'running', worker_id = EXCLUDED.worker_id,
                    attempts = job.attempts + 1, error_type = NULL, updated_at = NOW()
                WHERE job.status NOT IN ('completed', 'skipped')
                RETURNING task_id
            """).format(table=self._table), (task_id, json.dumps(record), worker_id))
            return cur.fetchone() is not None

    async def finish(self, task_id: str, *, worker_id: str, status: str,
                     error_type: str | None = None) -> None:
        if status not in {"completed", "skipped", "failed"}:
            raise ValueError(f"Unsupported cleanup status: {status}")
        await asyncio.to_thread(self._finish_sync, task_id, worker_id, status, error_type)

    def _finish_sync(self, task_id, worker_id, status, error_type) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("""
                UPDATE {} SET status = %s, error_type = %s, updated_at = NOW(),
                    completed_at = CASE WHEN %s IN ('completed', 'skipped') THEN NOW() ELSE NULL END
                WHERE task_id = %s AND worker_id = %s AND status = 'running'
            """).format(self._table), (status, error_type, status, task_id, worker_id))
            if cur.rowcount != 1:
                raise RuntimeError(f"Cleanup receipt ownership lost for task {task_id}")
