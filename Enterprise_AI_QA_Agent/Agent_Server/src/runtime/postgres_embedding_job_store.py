"""Durable state for low-priority Embedding tasks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect


TERMINAL_STATUSES = frozenset({"completed", "superseded"})
PROGRESS_STATUSES = frozenset({"embedding", "persisted", "replicating"})


@dataclass(frozen=True)
class EmbeddingJob:
    task_id: str
    source_type: str
    source_id: str
    source_version: str
    project_id: str
    case_id: str
    case_version_id: str
    content_hash: str
    status: str
    embedding_version: str | None
    embedding_dimension: int | None
    redis_key: str | None
    attempts: int
    worker_id: str | None
    lease_expires_at: datetime | None
    last_error_type: str | None
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None


class PostgresEmbeddingJobStore:
    def __init__(self, settings: Settings, *, lease_seconds: int = 900) -> None:
        self._settings = settings
        self._lease_seconds = max(1, int(lease_seconds))
        self._table = settings.database.postgres_embedding_job_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def enqueue(
        self,
        *,
        payload: dict[str, Any],
        stream: str,
        outbox_table: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._enqueue_sync,
            payload,
            stream,
            outbox_table,
        )

    async def begin(self, task_id: str, *, worker_id: str) -> bool:
        return await asyncio.to_thread(self._begin_sync, task_id, worker_id)

    async def progress(
        self,
        task_id: str,
        *,
        worker_id: str,
        status: str,
        embedding_version: str | None,
        embedding_dimension: int | None,
    ) -> bool:
        if status not in PROGRESS_STATUSES:
            raise ValueError(f"Unsupported embedding progress status: {status}")
        return await asyncio.to_thread(
            self._progress_sync,
            task_id,
            worker_id,
            status,
            embedding_version,
            embedding_dimension,
        )

    async def complete(
        self,
        task_id: str,
        *,
        worker_id: str,
        status: str,
        embedding_version: str | None,
        embedding_dimension: int | None,
        redis_key: str | None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"Unsupported embedding terminal status: {status}")
        return await asyncio.to_thread(
            self._complete_sync,
            task_id,
            worker_id,
            status,
            embedding_version,
            embedding_dimension,
            redis_key,
        )

    async def fail(self, task_id: str, *, worker_id: str, error_type: str) -> bool:
        return await asyncio.to_thread(
            self._fail_sync,
            task_id,
            worker_id,
            error_type,
        )

    async def get(self, task_id: str) -> EmbeddingJob | None:
        return await asyncio.to_thread(self._get_sync, task_id)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        task_id TEXT PRIMARY KEY,
                        source_type TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        source_version TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        case_id TEXT NOT NULL,
                        case_version_id TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        embedding_version TEXT NULL,
                        embedding_dimension INTEGER NULL,
                        redis_key TEXT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        worker_id TEXT NULL,
                        lease_expires_at TIMESTAMPTZ NULL,
                        last_error_type TEXT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        completed_at TIMESTAMPTZ NULL
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self._table}_status_idx "
                    f"ON {self._table} (status, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self._table}_source_idx "
                    f"ON {self._table} (source_type, source_id, created_at DESC)"
                )

    def _enqueue_sync(
        self,
        payload: dict[str, Any],
        stream: str,
        outbox_table: str,
    ) -> bool:
        task_id = str(payload["task_id"])
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                inserted = self.insert_job(cur, payload, table_name=self._table)
                cur.execute(
                    f"""
                    INSERT INTO {outbox_table} (event_key, stream, payload)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (event_key) DO NOTHING
                    """,
                    (
                        f"embedding:{task_id}",
                        stream,
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                outbox_inserted = cur.rowcount == 1
                if inserted != outbox_inserted:
                    raise RuntimeError(
                        "Embedding job and Outbox idempotency state diverged"
                    )
        return inserted

    @staticmethod
    def insert_job(cur, payload: dict[str, Any], *, table_name: str) -> bool:
        cur.execute(
            f"""
            INSERT INTO {table_name} (
                task_id, source_type, source_id, source_version, project_id,
                case_id, case_version_id, content_hash, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued')
            ON CONFLICT (task_id) DO NOTHING
            """,
            (
                payload["task_id"],
                payload["source_type"],
                payload["source_id"],
                payload["source_version"],
                payload["project_id"],
                payload["case_id"],
                payload["case_version_id"],
                payload["content_hash"],
            ),
        )
        return cur.rowcount == 1

    def _begin_sync(self, task_id: str, worker_id: str) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET status = 'claimed', attempts = attempts + 1,
                        worker_id = %s,
                        lease_expires_at = NOW() + %s * INTERVAL '1 second',
                        last_error_type = NULL, updated_at = NOW()
                    WHERE task_id = %s
                      AND (
                        status IN ('queued', 'failed')
                        OR (
                            status NOT IN ('completed', 'superseded')
                            AND lease_expires_at <= NOW()
                        )
                      )
                    """,
                    (worker_id, self._lease_seconds, task_id),
                )
                return cur.rowcount == 1

    def _progress_sync(
        self,
        task_id: str,
        worker_id: str,
        status: str,
        embedding_version: str | None,
        embedding_dimension: int | None,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET status = %s,
                        embedding_version = COALESCE(%s, embedding_version),
                        embedding_dimension = COALESCE(%s, embedding_dimension),
                        lease_expires_at = NOW() + %s * INTERVAL '1 second',
                        updated_at = NOW()
                    WHERE task_id = %s AND worker_id = %s
                      AND lease_expires_at > NOW()
                      AND status NOT IN ('completed', 'superseded')
                    """,
                    (
                        status,
                        embedding_version,
                        embedding_dimension,
                        self._lease_seconds,
                        task_id,
                        worker_id,
                    ),
                )
                return cur.rowcount == 1

    def _complete_sync(
        self,
        task_id: str,
        worker_id: str,
        status: str,
        embedding_version: str | None,
        embedding_dimension: int | None,
        redis_key: str | None,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET status = %s, embedding_version = %s,
                        embedding_dimension = %s, redis_key = %s,
                        worker_id = NULL, lease_expires_at = NULL,
                        last_error_type = NULL, completed_at = NOW(), updated_at = NOW()
                    WHERE task_id = %s AND worker_id = %s
                      AND lease_expires_at > NOW()
                    """,
                    (
                        status,
                        embedding_version,
                        embedding_dimension,
                        redis_key,
                        task_id,
                        worker_id,
                    ),
                )
                return cur.rowcount == 1

    def _fail_sync(self, task_id: str, worker_id: str, error_type: str) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET status = 'failed', last_error_type = %s,
                        worker_id = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE task_id = %s AND worker_id = %s
                      AND status NOT IN ('completed', 'superseded')
                    """,
                    (str(error_type)[:200], task_id, worker_id),
                )
                return cur.rowcount == 1

    def _get_sync(self, task_id: str) -> EmbeddingJob | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._table} WHERE task_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return EmbeddingJob(
            task_id=str(row["task_id"]),
            source_type=str(row["source_type"]),
            source_id=str(row["source_id"]),
            source_version=str(row["source_version"]),
            project_id=str(row["project_id"]),
            case_id=str(row["case_id"]),
            case_version_id=str(row["case_version_id"]),
            content_hash=str(row["content_hash"]),
            status=str(row["status"]),
            embedding_version=row.get("embedding_version"),
            embedding_dimension=(
                int(row["embedding_dimension"])
                if row.get("embedding_dimension") is not None
                else None
            ),
            redis_key=row.get("redis_key"),
            attempts=int(row.get("attempts") or 0),
            worker_id=row.get("worker_id"),
            lease_expires_at=row.get("lease_expires_at"),
            last_error_type=row.get("last_error_type"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            completed_at=row.get("completed_at"),
        )
