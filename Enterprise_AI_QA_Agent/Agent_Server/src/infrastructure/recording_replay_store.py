"""PostgreSQL persistence for idempotent recording replay reports."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
import logging
import re
from uuid import uuid4

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime, make_json_safe
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.recording import RecordingReplayClaim, RecordingReplayCommand, RecordingReplayRecord

logger = logging.getLogger(__name__)


class PostgresRecordingReplayStore:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._recordings = settings.database.postgres_recording_table
        self._table = f"{self._recordings}_replay"
        self._outbox = settings.database.postgres_task_outbox_table
        for identifier in (self._recordings, self._table, self._outbox):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", identifier):
                raise ValueError("Invalid recording replay table identifier")
        self.lease_seconds = 30.0

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
                checkpoint JSONB NULL,
                UNIQUE(recording_id, request_id)
            )""")
            for statement in (
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS attempt_no INTEGER NOT NULL DEFAULT 0",
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS attempt_id TEXT NULL",
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS worker_id TEXT NULL",
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS lease_token TEXT NULL",
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL",
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL",
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS checkpoint JSONB NULL",
            ):
                cursor.execute(statement)
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

    async def submit(self, record: RecordingReplayRecord, *, stream: str) -> tuple[RecordingReplayRecord, bool]:
        return await asyncio.to_thread(self._submit, record, stream)

    def _submit(self, record: RecordingReplayRecord, stream: str) -> tuple[RecordingReplayRecord, bool]:
        command = RecordingReplayCommand(task_id=record.id, replay_id=record.id,
            recording_id=record.recording_id, project_id=record.project_id)
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"""INSERT INTO {self._table}
                (id,recording_id,project_id,request_id,request_hash,status,created_at,updated_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(recording_id,request_id) DO NOTHING""",
                (record.id, record.recording_id, record.project_id, record.request_id,
                 record.request_hash, record.status, ensure_utc_datetime(record.created_at),
                 ensure_utc_datetime(record.created_at)))
            created = bool(cursor.rowcount)
            cursor.execute(f"SELECT * FROM {self._table} WHERE recording_id=%s AND request_id=%s",
                (record.recording_id, record.request_id))
            stored = self._record(cursor.fetchone())
            if created:
                cursor.execute(f"INSERT INTO {self._outbox}(event_key,stream,payload) VALUES(%s,%s,%s::jsonb)",
                    (f"recording-replay:{record.id}", stream, command.model_dump_json()))
        if stored.request_hash != record.request_hash:
            raise ValueError("recording replay request identity reused with different content")
        return stored, created

    async def get_by_id(self, project_id: str, replay_id: str) -> RecordingReplayRecord | None:
        return await asyncio.to_thread(self._get_by_id, project_id, replay_id)

    def _get_by_id(self, project_id: str, replay_id: str) -> RecordingReplayRecord | None:
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {self._table} WHERE id=%s AND project_id=%s", (replay_id, project_id))
            row = cursor.fetchone()
            return self._record(row) if row else None

    async def claim(self, project_id: str, replay_id: str, *, worker_id: str) -> RecordingReplayClaim | None:
        return await asyncio.to_thread(self._claim, project_id, replay_id, worker_id)

    def _claim(self, project_id: str, replay_id: str, worker_id: str) -> RecordingReplayClaim | None:
        attempt_id, lease_token = str(uuid4()), uuid4().hex
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"""UPDATE {self._table} SET attempt_no=attempt_no+1,attempt_id=%s,
                worker_id=%s,lease_token=%s,lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',
                started_at=COALESCE(started_at,clock_timestamp()),updated_at=clock_timestamp()
                WHERE id=%s AND project_id=%s AND status='running'
                AND (lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp())
                RETURNING *,EXTRACT(EPOCH FROM (lease_expires_at-clock_timestamp())) AS remaining_seconds""",
                (attempt_id, worker_id, lease_token, self.lease_seconds, replay_id, project_id))
            row = cursor.fetchone()
        if not row:
            return None
        return RecordingReplayClaim(record=self._record(row), worker_id=worker_id,
            attempt_id=attempt_id, lease_token=lease_token,
            remaining_seconds=float(row["remaining_seconds"]))

    async def heartbeat(self, claim: RecordingReplayClaim) -> None:
        def update():
            with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
                cursor.execute(f"""UPDATE {self._table} SET lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',
                    updated_at=clock_timestamp() WHERE id=%s AND project_id=%s AND status='running'
                    AND attempt_id=%s AND worker_id=%s AND lease_token=%s AND lease_expires_at>clock_timestamp()""",
                    (self.lease_seconds, claim.record.id, claim.record.project_id, claim.attempt_id,
                     claim.worker_id, claim.lease_token))
                if cursor.rowcount != 1:
                    raise ContinuationLeaseLostError("录制回放任务租约已失效")
        await asyncio.to_thread(update)

    async def save_checkpoint(self, claim: RecordingReplayClaim, checkpoint: dict) -> RecordingReplayRecord:
        """Persist an action boundary while the claim still owns the lease."""
        return await asyncio.to_thread(self._save_checkpoint, claim, checkpoint)

    def _save_checkpoint(self, claim: RecordingReplayClaim, checkpoint: dict) -> RecordingReplayRecord:
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"""UPDATE {self._table} SET checkpoint=%s::jsonb,updated_at=clock_timestamp()
                WHERE id=%s AND project_id=%s AND status='running'
                AND attempt_id=%s AND worker_id=%s AND lease_token=%s
                AND lease_expires_at>clock_timestamp() RETURNING *""",
                (json.dumps(make_json_safe(checkpoint), ensure_ascii=False), claim.record.id,
                 claim.record.project_id, claim.attempt_id, claim.worker_id, claim.lease_token))
            row = cursor.fetchone()
        if row is None:
            raise ContinuationLeaseLostError("录制回放任务当前 attempt 无法保存动作检查点")
        return self._record(row)

    async def finish_claim(self, claim: RecordingReplayClaim, *, report: dict | None = None,
                           error: str | None = None) -> RecordingReplayRecord:
        return await asyncio.to_thread(self._finish_claim, claim, report, error)

    def _finish_claim(self, claim, report, error):
        status = "failed" if error else "completed"
        with postgres_connect(self._settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"""UPDATE {self._table} SET status=%s,finished_at=clock_timestamp(),
                report=%s::jsonb,error=%s,updated_at=clock_timestamp(),lease_token=NULL,worker_id=NULL,
                lease_expires_at=NULL WHERE id=%s AND project_id=%s AND status='running'
                AND attempt_id=%s AND worker_id=%s AND lease_token=%s AND lease_expires_at>clock_timestamp()
                RETURNING *""",
                (status, json.dumps(make_json_safe(report), ensure_ascii=False) if report is not None else None,
                 error, claim.record.id, claim.record.project_id, claim.attempt_id, claim.worker_id, claim.lease_token))
            row = cursor.fetchone()
            if row is None:
                raise ContinuationLeaseLostError("录制回放任务当前 attempt 不再拥有写入权")
            return self._record(row)

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
