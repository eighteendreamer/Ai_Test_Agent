"""Durable state and transactional Outbox intent for Redis vector rebuilds."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect


TERMINAL_STATUSES = frozenset({"index_activated", "index_validated", "empty"})
PROGRESS_STATUSES = frozenset(
    {"dimension_detected", "index_building", "index_validating"}
)


@dataclass(frozen=True)
class VectorRebuildJob:
    task_id: str
    entity: str
    embedding_version: str
    activate: bool
    status: str
    dimension: int | None
    source_count: int
    replicated: int
    active_index: str | None
    attempts: int
    worker_id: str | None
    lease_expires_at: datetime | None
    last_error_type: str | None
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None


class PostgresVectorRebuildJobStore:
    def __init__(self, settings: Settings, *, lease_seconds: int = 900) -> None:
        self._settings = settings
        self._lease_seconds = max(1, int(lease_seconds))
        self._table = settings.database.postgres_vector_rebuild_job_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def enqueue(
        self,
        *,
        task_id: str,
        embedding_version: str,
        activate: bool,
        stream: str,
        outbox_table: str,
        payload: dict[str, Any],
    ) -> bool:
        return await asyncio.to_thread(
            self._enqueue_sync,
            task_id,
            embedding_version,
            activate,
            stream,
            outbox_table,
            payload,
        )

    async def begin(self, task_id: str, *, worker_id: str) -> bool:
        return await asyncio.to_thread(self._begin_sync, task_id, worker_id)

    async def progress(
        self,
        task_id: str,
        *,
        worker_id: str,
        status: str,
        dimension: int | None,
        source_count: int,
        replicated: int,
    ) -> bool:
        if status not in PROGRESS_STATUSES:
            raise ValueError(f"Unsupported vector rebuild progress status: {status}")
        return await asyncio.to_thread(
            self._progress_sync,
            task_id,
            worker_id,
            status,
            dimension,
            source_count,
            replicated,
        )

    async def complete(
        self,
        task_id: str,
        *,
        worker_id: str,
        status: str,
        dimension: int | None,
        source_count: int,
        replicated: int,
        active_index: str | None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"Unsupported vector rebuild terminal status: {status}")
        return await asyncio.to_thread(
            self._complete_sync,
            task_id,
            worker_id,
            status,
            dimension,
            source_count,
            replicated,
            active_index,
        )

    async def fail(self, task_id: str, *, worker_id: str, error_type: str) -> bool:
        return await asyncio.to_thread(
            self._fail_sync, task_id, worker_id, error_type
        )

    async def get(self, task_id: str) -> VectorRebuildJob | None:
        return await asyncio.to_thread(self._get_sync, task_id)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        task_id TEXT PRIMARY KEY,
                        entity TEXT NOT NULL,
                        embedding_version TEXT NOT NULL,
                        activate BOOLEAN NOT NULL DEFAULT FALSE,
                        status TEXT NOT NULL,
                        dimension INTEGER NULL,
                        source_count BIGINT NOT NULL DEFAULT 0,
                        replicated BIGINT NOT NULL DEFAULT 0,
                        active_index TEXT NULL,
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
                    f"CREATE INDEX IF NOT EXISTS {self._table}_version_idx "
                    f"ON {self._table} (entity, embedding_version, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self._table}_status_idx "
                    f"ON {self._table} (status, updated_at DESC)"
                )
            conn.commit()

    def _enqueue_sync(
        self,
        task_id: str,
        embedding_version: str,
        activate: bool,
        stream: str,
        outbox_table: str,
        payload: dict[str, Any],
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table} (
                        task_id, entity, embedding_version, activate, status
                    ) VALUES (%s, 'memory', %s, %s, 'queued')
                    ON CONFLICT (task_id) DO NOTHING
                    """,
                    (task_id, embedding_version, bool(activate)),
                )
                inserted = cur.rowcount == 1
                cur.execute(
                    f"""
                    INSERT INTO {outbox_table} (event_key, stream, payload)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (event_key) DO NOTHING
                    """,
                    (
                        f"vector_rebuild:{task_id}",
                        stream,
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                outbox_inserted = cur.rowcount == 1
                if inserted != outbox_inserted:
                    raise RuntimeError(
                        "Vector rebuild job and Outbox idempotency state diverged"
                    )
            conn.commit()
        return inserted

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
                        status IN ('queued', 'index_failed')
                        OR (
                            status NOT IN ('index_activated', 'index_validated', 'empty')
                            AND lease_expires_at <= NOW()
                        )
                      )
                    """,
                    (worker_id, self._lease_seconds, task_id),
                )
                claimed = cur.rowcount == 1
            conn.commit()
        return claimed

    def _progress_sync(
        self,
        task_id: str,
        worker_id: str,
        status: str,
        dimension: int | None,
        source_count: int,
        replicated: int,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET status = %s, dimension = %s, source_count = %s,
                        replicated = %s,
                        lease_expires_at = NOW() + %s * INTERVAL '1 second',
                        updated_at = NOW()
                    WHERE task_id = %s AND worker_id = %s
                      AND lease_expires_at > NOW()
                      AND status NOT IN ('index_activated', 'index_validated', 'empty')
                    """,
                    (
                        status,
                        dimension,
                        max(0, int(source_count)),
                        max(0, int(replicated)),
                        self._lease_seconds,
                        task_id,
                        worker_id,
                    ),
                )
                updated = cur.rowcount == 1
            conn.commit()
        return updated

    def _complete_sync(
        self,
        task_id: str,
        worker_id: str,
        status: str,
        dimension: int | None,
        source_count: int,
        replicated: int,
        active_index: str | None,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET status = %s, dimension = %s, source_count = %s,
                        replicated = %s, active_index = %s,
                        worker_id = NULL, lease_expires_at = NULL,
                        last_error_type = NULL, completed_at = NOW(), updated_at = NOW()
                    WHERE task_id = %s AND worker_id = %s
                      AND lease_expires_at > NOW()
                    """,
                    (
                        status,
                        dimension,
                        max(0, int(source_count)),
                        max(0, int(replicated)),
                        active_index,
                        task_id,
                        worker_id,
                    ),
                )
                updated = cur.rowcount == 1
            conn.commit()
        return updated

    def _fail_sync(self, task_id: str, worker_id: str, error_type: str) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET status = 'index_failed', last_error_type = %s,
                        worker_id = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE task_id = %s AND worker_id = %s
                      AND status NOT IN ('index_activated', 'index_validated', 'empty')
                    """,
                    (str(error_type)[:200], task_id, worker_id),
                )
                updated = cur.rowcount == 1
            conn.commit()
        return updated

    def _get_sync(self, task_id: str) -> VectorRebuildJob | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT task_id, entity, embedding_version, activate, status, "
                    f"dimension, source_count, replicated, active_index, attempts, "
                    f"worker_id, lease_expires_at, last_error_type, created_at, "
                    f"updated_at, completed_at FROM {self._table} WHERE task_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        value: dict[str, Any] = row if hasattr(row, "keys") else {
            "task_id": row[0],
            "entity": row[1],
            "embedding_version": row[2],
            "activate": row[3],
            "status": row[4],
            "dimension": row[5],
            "source_count": row[6],
            "replicated": row[7],
            "active_index": row[8],
            "attempts": row[9],
            "worker_id": row[10],
            "lease_expires_at": row[11],
            "last_error_type": row[12],
            "created_at": row[13],
            "updated_at": row[14],
            "completed_at": row[15],
        }
        return VectorRebuildJob(
            task_id=str(value["task_id"]),
            entity=str(value["entity"]),
            embedding_version=str(value["embedding_version"]),
            activate=bool(value["activate"]),
            status=str(value["status"]),
            dimension=(
                int(value["dimension"]) if value.get("dimension") is not None else None
            ),
            source_count=int(value.get("source_count") or 0),
            replicated=int(value.get("replicated") or 0),
            active_index=value.get("active_index"),
            attempts=int(value.get("attempts") or 0),
            worker_id=value.get("worker_id"),
            lease_expires_at=value.get("lease_expires_at"),
            last_error_type=value.get("last_error_type"),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
            completed_at=value.get("completed_at"),
        )
