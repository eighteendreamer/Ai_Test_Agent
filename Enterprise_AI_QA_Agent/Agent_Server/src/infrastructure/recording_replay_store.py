"""PostgreSQL persistence for idempotent recording replay reports."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime, make_json_safe
from src.schemas.recording import RecordingReplayRecord


class PostgresRecordingReplayStore:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._recordings = settings.database.postgres_recording_table
        self._table = f"{self._recordings}_replay"

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"""CREATE TABLE IF NOT EXISTS {self._table} (
                id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES {self._recordings}(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
                created_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ NULL,
                report JSONB NULL,
                error TEXT NULL,
                UNIQUE(recording_id, request_id)
            )""")
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._table}_recording_created ON {self._table}(recording_id, created_at DESC, id)")

    @staticmethod
    def _record(row) -> RecordingReplayRecord:
        return RecordingReplayRecord.model_validate(dict(row))

    async def create_or_get(self, record: RecordingReplayRecord) -> tuple[RecordingReplayRecord, bool]:
        return await asyncio.to_thread(self._create_or_get, record)

    def _create_or_get(self, record: RecordingReplayRecord) -> tuple[RecordingReplayRecord, bool]:
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"""INSERT INTO {self._table}(id,recording_id,project_id,request_id,request_hash,status,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(recording_id,request_id) DO NOTHING""",
                (record.id, record.recording_id, record.project_id, record.request_id, record.request_hash,
                 record.status, ensure_utc_datetime(record.created_at)))
            created = bool(cursor.rowcount)
            cursor.execute(f"SELECT * FROM {self._table} WHERE recording_id=%s AND request_id=%s",
                (record.recording_id, record.request_id))
            stored = self._record(cursor.fetchone())
        if stored.request_hash != record.request_hash:
            raise ValueError("recording replay request identity reused with different content")
        return stored, created

    async def finish(self, replay_id: str, *, report: dict | None = None, error: str | None = None) -> RecordingReplayRecord:
        return await asyncio.to_thread(self._finish, replay_id, report, error)

    def _finish(self, replay_id: str, report: dict | None, error: str | None) -> RecordingReplayRecord:
        status = "failed" if error else "completed"
        finished_at = datetime.now(timezone.utc)
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"""UPDATE {self._table} SET status=%s,finished_at=%s,report=%s::jsonb,error=%s
                WHERE id=%s AND status='running' RETURNING *""",
                (status, finished_at, json.dumps(make_json_safe(report), ensure_ascii=False) if report is not None else None,
                 error, replay_id))
            row = cursor.fetchone()
            if row is None:
                cursor.execute(f"SELECT * FROM {self._table} WHERE id=%s", (replay_id,))
                row = cursor.fetchone()
            if row is None:
                raise KeyError("recording replay not found")
            return self._record(row)

    async def list(self, recording_id: str, limit: int = 20) -> list[RecordingReplayRecord]:
        return await asyncio.to_thread(self._list, recording_id, limit)

    def _list(self, recording_id: str, limit: int) -> list[RecordingReplayRecord]:
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {self._table} WHERE recording_id=%s ORDER BY created_at DESC,id LIMIT %s",
                (recording_id, max(1, min(int(limit), 100))))
            return [self._record(row) for row in cursor.fetchall()]
