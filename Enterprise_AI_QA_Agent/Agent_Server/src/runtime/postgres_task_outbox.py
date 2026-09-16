"""Durable PostgreSQL outbox for publishing Harness tasks to Redis.

The outbox is intentionally independent from Redis.  A business transaction
can record an event first; a relay may publish it repeatedly until the row is
marked as published.  PostgreSQL therefore remains the source of truth.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue

LOGGER = logging.getLogger(__name__)

QueueResolver = Callable[
    [str], RedisTaskQueue | Awaitable[RedisTaskQueue | None] | None
]


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

    async def ensure(self, *, event_key: str, stream: str, payload: dict[str, Any]) -> TaskOutboxRecord:
        return await asyncio.to_thread(self._ensure_sync, event_key, stream, payload)

    async def claim(self, *, limit: int = 100, relay_id: str, stream: str | None = None) -> list[TaskOutboxRecord]:
        return await asyncio.to_thread(self._claim_sync, limit, relay_id, stream)

    async def mark_published(self, outbox_id: int, *, relay_id: str) -> bool:
        return await asyncio.to_thread(self._mark_published_sync, outbox_id, relay_id)

    async def mark_failed(self, outbox_id: int, *, error: str, relay_id: str) -> bool:
        return await asyncio.to_thread(self._mark_failed_sync, outbox_id, error, relay_id)

    async def relay_once(
        self, queue: RedisTaskQueue | None = None, *, limit: int = 100,
        queue_for_stream: QueueResolver | None = None,
    ) -> int:
        """Publish a bounded batch and acknowledge only successful publishes."""
        if limit < 1:
            raise ValueError("Outbox relay limit must be greater than zero")
        published = 0
        if queue is None and queue_for_stream is None:
            raise ValueError("A queue or stream queue resolver is required")
        relay_id = f"relay_{uuid.uuid4().hex}"
        # Claim just in time: later records must not spend their lease waiting
        # for earlier network calls in a large batch. Failed rows have backoff.
        for _ in range(max(1, limit)):
            stream = getattr(queue, "stream", None) if queue_for_stream is None else None
            if stream is None and queue_for_stream is None:
                records = await self.claim(limit=1, relay_id=relay_id)
            else:
                records = await self.claim(limit=1, relay_id=relay_id, stream=stream)
            if not records:
                break
            record = records[0]
            try:
                target_queue = queue
                if queue_for_stream is not None:
                    resolved = queue_for_stream(record.stream)
                    target_queue = await resolved if inspect.isawaitable(resolved) else resolved
                target_stream = getattr(target_queue, "stream", record.stream) if target_queue else None
                if target_queue is None or target_stream != record.stream:
                    raise ValueError("Outbox destination stream mismatch")
                message_id = await target_queue.enqueue(record.payload)
            except Exception as exc:
                await self.mark_failed(record.id, error=type(exc).__name__, relay_id=relay_id)
                LOGGER.error(
                    "task_outbox_publish_failed",
                    extra={
                        "outbox_id": record.id,
                        "event_key": record.event_key,
                        "stream": record.stream,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            confirmed = await self.mark_published(record.id, relay_id=relay_id)
            if not confirmed:
                LOGGER.warning("task_outbox_publish_lease_lost", extra={"outbox_id": record.id})
                continue
            published += 1
            LOGGER.info(
                "task_outbox_published",
                extra={"outbox_id": record.id, "event_key": record.event_key, "stream": record.stream, "message_id": message_id},
            )
        return published

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
                        claimed_by TEXT,
                        claimed_until TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(f"ALTER TABLE {self._table()} ADD COLUMN IF NOT EXISTS claimed_by TEXT")
                cur.execute(f"ALTER TABLE {self._table()} ADD COLUMN IF NOT EXISTS claimed_until TIMESTAMPTZ")
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self._table()}_pending_idx "
                    f"ON {self._table()} (created_at) WHERE published_at IS NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {self._table()}_pending_stream_idx "
                    f"ON {self._table()} (stream, created_at) WHERE published_at IS NULL"
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

    def _ensure_sync(self, event_key: str, stream: str, payload: dict[str, Any]) -> TaskOutboxRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {self._table()} (event_key, stream, payload)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (event_key) DO UPDATE SET updated_at={self._table()}.updated_at
                    RETURNING id, event_key, stream, payload, attempts, created_at
                """, (event_key, stream, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
                row = cur.fetchone()
            conn.commit()
        value = row
        record = TaskOutboxRecord(
            int(value["id"]), value["event_key"], value["stream"], value["payload"],
            int(value["attempts"]), value.get("created_at"),
        )
        if record.stream != stream:
            raise ValueError(
                f"Outbox event key {event_key!r} is already bound to stream {record.stream!r}"
            )
        return record

    def _claim_sync(self, limit: int, relay_id: str, stream: str | None) -> list[TaskOutboxRecord]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, event_key, stream, payload, attempts, created_at
                    FROM {self._table()}
                    WHERE published_at IS NULL
                      AND (claimed_until IS NULL OR claimed_until <= NOW())
                      AND (%s::text IS NULL OR stream = %s)
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                    """,
                    (stream, stream, max(1, limit)),
                )
                rows = cur.fetchall()
                ids = [int(row[0] if not hasattr(row, "keys") else row["id"]) for row in rows]
                if ids:
                    cur.execute(
                        f"UPDATE {self._table()} SET attempts = attempts + 1, claimed_by = %s, claimed_until = NOW() + %s * INTERVAL '1 second', updated_at = NOW() WHERE id = ANY(%s)",
                        (relay_id, self._settings.orchestration.task_outbox_lease_seconds, ids),
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

    def _mark_published_sync(self, outbox_id: int, relay_id: str) -> bool:
        return self._update_sync(outbox_id, published=True, error=None, relay_id=relay_id)

    def _mark_failed_sync(self, outbox_id: int, error: str, relay_id: str) -> bool:
        return self._update_sync(outbox_id, published=False, error=error[:2000], relay_id=relay_id)

    def _update_sync(self, outbox_id: int, *, published: bool, error: str | None, relay_id: str) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self._table()} SET published_at = CASE WHEN %s THEN NOW() ELSE NULL END, "
                    "last_error = %s, claimed_by = NULL, "
                    "claimed_until = CASE WHEN %s THEN NULL ELSE NOW() + %s * INTERVAL '1 second' END, "
                    "updated_at = NOW() WHERE id = %s AND claimed_by = %s "
                    "AND claimed_until > NOW() AND published_at IS NULL",
                    (published, error, published, self._settings.orchestration.task_outbox_retry_seconds,
                     outbox_id, relay_id),
                )
                updated = cur.rowcount == 1
            conn.commit()
        return updated
