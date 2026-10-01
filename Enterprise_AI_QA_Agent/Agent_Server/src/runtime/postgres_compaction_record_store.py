"""Durable idempotency and audit records for hot-memory compaction."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect


@dataclass(frozen=True)
class CompactionRecord:
    compaction_key: str
    session_id: str
    last_event_id: str
    last_compacted_event_sequence: int
    compression_version: str
    status: str
    memory_ids: list[str]
    last_error: str | None
    claimed_by: str | None
    claimed_until: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None


class PostgresCompactionRecordStore:
    def __init__(self, settings: Settings, *, lease_seconds: int = 300) -> None:
        self._settings = settings
        self._lease_seconds = max(1, int(lease_seconds))
        self._table_name = settings.database.postgres_compaction_table
        self._event_table_name = settings.database.postgres_event_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    @property
    def lease_seconds(self) -> int:
        return self._lease_seconds

    async def begin(
        self,
        *,
        compaction_key: str,
        session_id: str,
        last_event_id: str,
        compression_version: str,
        owner_id: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._begin_sync,
            compaction_key,
            session_id,
            last_event_id,
            compression_version,
            owner_id or f"compactor_{uuid.uuid4().hex}",
        )

    async def complete(self, compaction_key: str, memory_ids: list[str], *, owner_id: str) -> bool:
        return await asyncio.to_thread(self._complete_sync, compaction_key, memory_ids, owner_id)

    async def renew(self, compaction_key: str, *, owner_id: str) -> bool:
        return await asyncio.to_thread(self._renew_sync, compaction_key, owner_id)

    async def fail(self, compaction_key: str, error: str, *, owner_id: str) -> bool:
        return await asyncio.to_thread(self._fail_sync, compaction_key, error, owner_id)

    async def get(self, compaction_key: str) -> CompactionRecord | None:
        return await asyncio.to_thread(self._get_sync, compaction_key)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table_name} (
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
                    )
                    """
                )
                cur.execute(
                    f"ALTER TABLE {self._table_name} ADD COLUMN IF NOT EXISTS "
                    "last_compacted_event_sequence BIGINT NOT NULL DEFAULT 0"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self._table_name}_session_idx "
                    f"ON {self._table_name} (session_id, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self._table_name}_status_idx "
                    f"ON {self._table_name} (status, updated_at DESC)"
                )
            conn.commit()

    def _begin_sync(
        self,
        compaction_key: str,
        session_id: str,
        last_event_id: str,
        compression_version: str,
        owner_id: str,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table_name} (
                        compaction_key, session_id, last_event_id,
                        compression_version, status, claimed_by,
                        claimed_until
                    ) VALUES (%s, %s, %s, %s, 'running', %s,
                              NOW() + %s * INTERVAL '1 second')
                    ON CONFLICT (compaction_key) DO NOTHING
                    """,
                    (
                        compaction_key,
                        session_id,
                        last_event_id,
                        compression_version,
                        owner_id,
                        self._lease_seconds,
                    ),
                )
                if cur.rowcount == 1:
                    conn.commit()
                    return True
                cur.execute(
                    f"""
                    UPDATE {self._table_name}
                    SET status = 'running', claimed_by = %s,
                        claimed_until = NOW() + %s * INTERVAL '1 second',
                        last_error = NULL, updated_at = NOW()
                    WHERE compaction_key = %s
                      AND (
                        status = 'failed'
                        OR (status = 'running' AND claimed_until <= NOW())
                      )
                    """,
                    (owner_id, self._lease_seconds, compaction_key),
                )
                claimed = cur.rowcount == 1
            conn.commit()
        return claimed

    def _renew_sync(self, compaction_key: str, owner_id: str) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table_name}
                    SET claimed_until = NOW() + %s * INTERVAL '1 second',
                        updated_at = NOW()
                    WHERE compaction_key = %s AND status = 'running'
                      AND claimed_by = %s AND claimed_until > NOW()
                    """,
                    (self._lease_seconds, compaction_key, owner_id),
                )
                renewed = cur.rowcount == 1
            conn.commit()
        return renewed

    def _complete_sync(self, compaction_key: str, memory_ids: list[str], owner_id: str) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table_name}
                    SET status = 'completed', memory_ids = %s::jsonb,
                        claimed_by = NULL, claimed_until = NULL,
                        last_error = NULL, completed_at = NOW(), updated_at = NOW(),
                        last_compacted_event_sequence = COALESCE(
                            (SELECT event.event_sequence
                             FROM {self._event_table_name} event
                             WHERE event.session_id = {self._table_name}.session_id
                               AND event.id = {self._table_name}.last_event_id),
                            {self._table_name}.last_compacted_event_sequence
                        )
                    WHERE compaction_key = %s AND status = 'running'
                      AND claimed_by = %s AND claimed_until > NOW()
                    """,
                    (json.dumps(memory_ids, ensure_ascii=False), compaction_key, owner_id),
                )
                updated = cur.rowcount == 1
            conn.commit()
        return updated

    async def uncompacted_event_count(self) -> int:
        return await asyncio.to_thread(self._uncompacted_event_count_sync)

    def _uncompacted_event_count_sync(self) -> int:
        """Count durable FlowEvents no completed compaction cycle has folded yet."""
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH durable AS (
                        SELECT session_id, MAX(event_sequence) AS latest_sequence
                        FROM {self._event_table_name}
                        GROUP BY session_id
                    ), compacted AS (
                        SELECT session_id,
                               MAX(last_compacted_event_sequence) AS watermark
                        FROM {self._table_name}
                        WHERE status = 'completed'
                        GROUP BY session_id
                    )
                    SELECT COALESCE(SUM(GREATEST(durable.latest_sequence - COALESCE(compacted.watermark, 0), 0)), 0)
                        AS uncompacted_events
                    FROM durable
                    LEFT JOIN compacted ON compacted.session_id = durable.session_id
                    """
                )
                row = cur.fetchone()
        return int(row["uncompacted_events"]) if row else 0

    def _fail_sync(self, compaction_key: str, error: str, owner_id: str) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table_name}
                    SET status = 'failed', last_error = %s,
                        claimed_by = NULL, claimed_until = NULL, updated_at = NOW()
                    WHERE compaction_key = %s AND status = 'running'
                      AND claimed_by = %s AND claimed_until > NOW()
                    """,
                    (str(error)[:4000], compaction_key, owner_id),
                )
                updated = cur.rowcount == 1
            conn.commit()
        return updated

    def _get_sync(self, compaction_key: str) -> CompactionRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT compaction_key, session_id, last_event_id, compression_version, "
                    f"status, memory_ids, last_error, claimed_by, claimed_until, "
                    f"created_at, updated_at, completed_at, last_compacted_event_sequence "
                    f"FROM {self._table_name} "
                    "WHERE compaction_key = %s",
                    (compaction_key,),
                )
                row = cur.fetchone()
        if not row:
            return None
        value: dict[str, Any] = row if hasattr(row, "keys") else {
            "compaction_key": row[0], "session_id": row[1], "last_event_id": row[2],
            "compression_version": row[3], "status": row[4], "memory_ids": row[5],
            "last_error": row[6], "claimed_by": row[7], "claimed_until": row[8],
            "created_at": row[9], "updated_at": row[10], "completed_at": row[11],
            "last_compacted_event_sequence": row[12],
        }
        memory_ids = value.get("memory_ids") or []
        return CompactionRecord(
            compaction_key=str(value["compaction_key"]),
            session_id=str(value["session_id"]),
            last_event_id=str(value["last_event_id"]),
            last_compacted_event_sequence=int(
                value.get("last_compacted_event_sequence") or 0
            ),
            compression_version=str(value["compression_version"]),
            status=str(value["status"]),
            memory_ids=[str(item) for item in memory_ids],
            last_error=value.get("last_error"),
            claimed_by=value.get("claimed_by"),
            claimed_until=value.get("claimed_until"),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
            completed_at=value.get("completed_at"),
        )
