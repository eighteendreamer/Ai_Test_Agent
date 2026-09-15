"""Durable PostgreSQL outbox for publishing Harness tasks to Redis.

The outbox is intentionally independent from Redis.  A business transaction
can record an event first; a relay may publish it repeatedly until the row is
marked as published.  PostgreSQL therefore remains the source of truth.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect


@dataclass(frozen=True)
class TaskOutboxRecord:
    id: int
    event_key: str
    stream: str
    payload: dict[str, Any]
    attempts: int
    created_at: datetime | None = None


class PostgresTaskOutbox:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def enqueue(self, *, event_key: str, stream: str, payload: dict[str, Any]) -> bool:
        return await asyncio.to_thread(self._enqueue_sync, event_key, stream, payload)

    async def claim(self, *, limit: int = 100) -> list[TaskOutboxRecord]:
        return await asyncio.to_thread(self._claim_sync, limit)

    async def mark_published(self, outbox_id: int) -> None:
        await asyncio.to_thread(self._mark_published_sync, outbox_id)

    async def mark_failed(self, outbox_id: int, *, error: str) -> None:
        await asyncio.to_thread(self._mark_failed_sync, outbox_id, error)

    def _table(self) -> str:
        return self._settings.database.postgres_task_outbox_table

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table()} (
                        id BIGSERIAL PRIMARY KEY,
                        event_key TEXT NOT NULL UNIQUE,
                        stream TEXT NOT NULL,
                        payload JSONB NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        published_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self._table()}_pending_idx "
                    f"ON {self._table()} (created_at) WHERE published_at IS NULL"
                )
            conn.commit()

    def _enqueue_sync(self, event_key: str, stream: str, payload: dict[str, Any]) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table()} (event_key, stream, payload)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (event_key) DO NOTHING
                    """,
                    (event_key, stream, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                )
                inserted = cur.rowcount == 1
            conn.commit()
        return inserted

    def _claim_sync(self, limit: int) -> list[TaskOutboxRecord]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, event_key, stream, payload, attempts, created_at
                    FROM {self._table()}
                    WHERE published_at IS NULL
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                    """,
                    (max(1, limit),),
                )
                rows = cur.fetchall()
                ids = [int(row[0] if not hasattr(row, "keys") else row["id"]) for row in rows]
                if ids:
                    cur.execute(
                        f"UPDATE {self._table()} SET attempts = attempts + 1, updated_at = NOW() WHERE id = ANY(%s)",
                        (ids,),
                    )
            conn.commit()
        records: list[TaskOutboxRecord] = []
        for row in rows:
            if hasattr(row, "keys"):
                value = row
                records.append(TaskOutboxRecord(int(value["id"]), value["event_key"], value["stream"], value["payload"], int(value["attempts"]) + 1, value.get("created_at")))
            else:
                records.append(TaskOutboxRecord(int(row[0]), row[1], row[2], row[3], int(row[4]) + 1, row[5]))
        return records

    def _mark_published_sync(self, outbox_id: int) -> None:
        self._update_sync(outbox_id, published=True, error=None)

    def _mark_failed_sync(self, outbox_id: int, error: str) -> None:
        self._update_sync(outbox_id, published=False, error=error[:2000])

    def _update_sync(self, outbox_id: int, *, published: bool, error: str | None) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self._table()} SET published_at = CASE WHEN %s THEN NOW() ELSE NULL END, last_error = %s, updated_at = NOW() WHERE id = %s",
                    (published, error, outbox_id),
                )
            conn.commit()
