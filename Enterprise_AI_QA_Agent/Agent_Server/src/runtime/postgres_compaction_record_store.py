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

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

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

    async def complete(self, compaction_key: str, memory_ids: list[str]) -> bool:
        return await asyncio.to_thread(self._complete_sync, compaction_key, memory_ids)

    async def fail(self, compaction_key: str, error: str) -> bool:
        return await asyncio.to_thread(self._fail_sync, compaction_key, error)

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

    def _complete_sync(self, compaction_key: str, memory_ids: list[str]) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table_name}
                    SET status = 'completed', memory_ids = %s::jsonb,
                        claimed_by = NULL, claimed_until = NULL,
                        last_error = NULL, completed_at = NOW(), updated_at = NOW()
                    WHERE compaction_key = %s AND status = 'running'
                    """,
                    (json.dumps(memory_ids, ensure_ascii=False), compaction_key),
                )
                updated = cur.rowcount == 1
            conn.commit()
        return updated

    def _fail_sync(self, compaction_key: str, error: str) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table_name}
                    SET status = 'failed', last_error = %s,
                        claimed_by = NULL, claimed_until = NULL, updated_at = NOW()
                    WHERE compaction_key = %s AND status = 'running'
                    """,
                    (str(error)[:4000], compaction_key),
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
                    f"created_at, updated_at, completed_at FROM {self._table_name} "
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
        }
        memory_ids = value.get("memory_ids") or []
        return CompactionRecord(
            compaction_key=str(value["compaction_key"]),
            session_id=str(value["session_id"]),
            last_event_id=str(value["last_event_id"]),
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
