"""PostgreSQL 录制事件流存储（方案 7 章 / P0-2）。

职责：
- ui_recording 会话元数据读写（状态机持久层，迁移合法性由 RecorderSessionService 校验）；
- ui_recording_event 原始事件流水：追加写、不可变（只 INSERT，不 UPDATE）；
- (recording_id, seq) 联合唯一约束保证批量幂等：网络重试、重复批次不产生重复行。

不连库纯单测见 tests/test_recording_store.py（SQL 组装与行映射）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime, make_json_safe
from src.schemas.recording import (
    RecordingEventAck,
    RecordingSession,
    RecordingStatus,
    RecorderEvent,
    dedupe_event_batch,
)

logger = logging.getLogger(__name__)


class PostgresRecordingStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session_table = settings.database.postgres_recording_table
        self._event_table = settings.database.postgres_recording_event_table

    # ------------------------------------------------------------------
    # async facade（对齐 PostgresSessionStore / PostgresToolJobStore 惯例）
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def create_session(self, session: RecordingSession) -> RecordingSession:
        return await asyncio.to_thread(self._create_session_sync, session)

    async def append_events(
        self, recording_id: str, events: list[RecorderEvent]
    ) -> RecordingEventAck:
        return await asyncio.to_thread(self._append_events_sync, recording_id, events)

    async def update_status(
        self,
        recording_id: str,
        status: RecordingStatus,
        *,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        finalize_metrics: dict[str, Any] | None = None,
    ) -> RecordingSession | None:
        return await asyncio.to_thread(
            self._update_status_sync,
            recording_id,
            status,
            started_at,
            ended_at,
            finalize_metrics,
        )

    async def get_session(self, recording_id: str) -> RecordingSession | None:
        return await asyncio.to_thread(self._get_session_sync, recording_id)

    async def list_sessions(
        self,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RecordingSession]:
        return await asyncio.to_thread(
            self._list_sessions_sync, project_id, limit, offset
        )

    async def get_events(self, recording_id: str) -> list[RecorderEvent]:
        return await asyncio.to_thread(self._get_events_sync, recording_id)

    async def discard_session(self, recording_id: str) -> RecordingSession | None:
        return await asyncio.to_thread(self._discard_session_sync, recording_id)

    async def delete_session(self, recording_id: str) -> bool:
        return await asyncio.to_thread(self._delete_session_sync, recording_id)

    # ------------------------------------------------------------------
    # sync 实现
    # ------------------------------------------------------------------
    @staticmethod
    def _count_from_row(row: Any) -> int:
        """COUNT(*) 行解析：兼容 dict（fake/驱动）与 SQLAlchemy Row（按列名）。"""
        if row is None:
            return 0
        if isinstance(row, dict):
            return int(row.get("cnt") or 0)
        try:
            return int(row["cnt"] or 0)
        except (KeyError, TypeError, IndexError):
            return int(row[0] or 0)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._session_table} (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        name TEXT NOT NULL DEFAULT '',
                        entry_url TEXT NOT NULL,
                        driver_kind TEXT NOT NULL DEFAULT 'embedded',
                        status TEXT NOT NULL DEFAULT 'launching',
                        session_id TEXT NULL,
                        approval_id TEXT NULL,
                        step_count INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        started_at TIMESTAMPTZ NULL,
                        ended_at TIMESTAMPTZ NULL,
                        finalize_metrics JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._event_table} (
                        id TEXT PRIMARY KEY,
                        recording_id TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        type TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        screenshot_ref TEXT NULL,
                        UNIQUE(recording_id, seq)
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._session_table}_project_updated "
                    f"ON {self._session_table} (project_id, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._session_table}_status "
                    f"ON {self._session_table} (status)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._event_table}_recording_seq "
                    f"ON {self._event_table} (recording_id, seq ASC)"
                )

    def _create_session_sync(self, session: RecordingSession) -> RecordingSession:
        now = datetime.now(timezone.utc)
        session.updated_at = now
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._session_table} (
                        id, project_id, name, entry_url, driver_kind, status,
                        session_id, approval_id, step_count,
                        created_at, updated_at, started_at, ended_at,
                        finalize_metrics, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        entry_url = EXCLUDED.entry_url,
                        driver_kind = EXCLUDED.driver_kind,
                        status = EXCLUDED.status,
                        session_id = EXCLUDED.session_id,
                        approval_id = EXCLUDED.approval_id,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        session.id,
                        session.project_id,
                        session.name,
                        session.entry_url,
                        session.driver_kind.value,
                        session.status.value,
                        session.session_id,
                        session.approval_id,
                        int(session.step_count),
                        session.created_at,
                        now,
                        session.started_at,
                        session.ended_at,
                        json.dumps(
                            make_json_safe(session.finalize_metrics), ensure_ascii=False
                        ),
                        json.dumps(make_json_safe(session.metadata), ensure_ascii=False),
                    ),
                )
        return session

    def _append_events_sync(
        self, recording_id: str, events: list[RecorderEvent]
    ) -> RecordingEventAck:
        """批量幂等追加：批内按 seq 去重 + ON CONFLICT (recording_id, seq) DO NOTHING。

        流水不可变：只 INSERT，绝不 UPDATE 既有事件行。
        accepted = 本批真正新插入的行数（逐条 rowcount 累计）；
        duplicates = 批内重复 + 与库中既有 (recording_id, seq) 冲突的行数。
        """
        if not events:
            return RecordingEventAck(accepted=0, duplicates=0)

        deduped = dedupe_event_batch(events)
        in_batch_duplicates = len(events) - len(deduped)
        inserted = 0

        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS cnt FROM {self._event_table} WHERE recording_id = %s",
                    (recording_id,),
                )
                before = self._count_from_row(cur.fetchone())

                for event in deduped:
                    payload = event.model_dump(mode="json")
                    cur.execute(
                        f"""
                        INSERT INTO {self._event_table} (
                            id, recording_id, seq, type, timestamp, payload, screenshot_ref
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s::jsonb, %s
                        )
                        ON CONFLICT (recording_id, seq) DO NOTHING
                        """,
                        (
                            str(uuid4()),
                            recording_id,
                            int(event.seq),
                            event.type,
                            ensure_utc_datetime(event.timestamp),
                            json.dumps(make_json_safe(payload), ensure_ascii=False),
                            event.screenshot_ref,
                        ),
                    )
                    inserted += int(cur.rowcount or 0)

                total = before + inserted
                cur.execute(
                    f"""
                    UPDATE {self._session_table}
                    SET step_count = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (total, datetime.now(timezone.utc), recording_id),
                )

        logger.debug(
            "recording events appended",
            extra={
                "recording_id": recording_id,
                "batch_size": len(events),
                "inserted": inserted,
                "in_batch_duplicates": in_batch_duplicates,
            },
        )
        conflict_duplicates = len(deduped) - inserted
        return RecordingEventAck(
            accepted=inserted,
            duplicates=in_batch_duplicates + conflict_duplicates,
        )

    def _update_status_sync(
        self,
        recording_id: str,
        status: RecordingStatus,
        started_at: datetime | None,
        ended_at: datetime | None,
        finalize_metrics: dict[str, Any] | None,
    ) -> RecordingSession | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                sets = ["status = %s", "updated_at = %s"]
                params: list[Any] = [status.value, datetime.now(timezone.utc)]
                if started_at is not None:
                    sets.append("started_at = %s")
                    params.append(ensure_utc_datetime(started_at))
                if ended_at is not None:
                    sets.append("ended_at = %s")
                    params.append(ensure_utc_datetime(ended_at))
                if finalize_metrics is not None:
                    sets.append("finalize_metrics = %s::jsonb")
                    params.append(
                        json.dumps(make_json_safe(finalize_metrics), ensure_ascii=False)
                    )
                params.append(recording_id)
                cur.execute(
                    f"UPDATE {self._session_table} SET {', '.join(sets)} WHERE id = %s",
                    tuple(params),
                )
        return self._get_session_sync(recording_id)

    def _get_session_sync(self, recording_id: str) -> RecordingSession | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._session_table} WHERE id = %s",
                    (recording_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_session(row)

    def _list_sessions_sync(
        self, project_id: str | None, limit: int, offset: int
    ) -> list[RecordingSession]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if project_id:
                    cur.execute(
                        f"SELECT * FROM {self._session_table} "
                        f"WHERE project_id = %s ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                        (project_id, int(limit), int(offset)),
                    )
                else:
                    cur.execute(
                        f"SELECT * FROM {self._session_table} "
                        f"ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                        (int(limit), int(offset)),
                    )
                return [self._row_to_session(row) for row in cur.fetchall()]

    def _get_events_sync(self, recording_id: str) -> list[RecorderEvent]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._event_table} "
                    f"WHERE recording_id = %s ORDER BY seq ASC",
                    (recording_id,),
                )
                return [self._row_to_event(row) for row in cur.fetchall()]

    def _discard_session_sync(self, recording_id: str) -> RecordingSession | None:
        """销毁：PG 标记 discarded（流水保留以供审计），图谱不写。"""
        session = self._get_session_sync(recording_id)
        if session is None:
            return None
        return self._update_status_sync(
            recording_id,
            RecordingStatus.discarded,
            started_at=None,
            ended_at=datetime.now(timezone.utc),
            finalize_metrics=None,
        )

    def _delete_session_sync(self, recording_id: str) -> bool:
        """物理删除：会话行 + 事件流水（先删子表防孤儿行）。返回是否存在。"""
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self._event_table} WHERE recording_id = %s",
                    (recording_id,),
                )
                cur.execute(
                    f"DELETE FROM {self._session_table} WHERE id = %s",
                    (recording_id,),
                )
                deleted = int(cur.rowcount or 0)
        logger.info(
            "recording session deleted: recording_id=%s session_rows=%s",
            recording_id,
            deleted,
        )
        return deleted > 0

    # ------------------------------------------------------------------
    # 行映射
    # ------------------------------------------------------------------
    def _row_to_session(self, row: Any) -> RecordingSession:
        data = row if isinstance(row, dict) else dict(row)
        return RecordingSession(
            id=str(data["id"]),
            project_id=str(data["project_id"]),
            name=str(data.get("name") or ""),
            entry_url=str(data["entry_url"]),
            driver_kind=str(data.get("driver_kind") or "embedded"),
            status=str(data.get("status") or "launching"),
            session_id=data.get("session_id"),
            approval_id=data.get("approval_id"),
            step_count=int(data.get("step_count") or 0),
            created_at=ensure_utc_datetime(data["created_at"]),
            updated_at=ensure_utc_datetime(data["updated_at"]),
            started_at=(
                ensure_utc_datetime(data["started_at"])
                if data.get("started_at")
                else None
            ),
            ended_at=(
                ensure_utc_datetime(data["ended_at"]) if data.get("ended_at") else None
            ),
            finalize_metrics=dict(data.get("finalize_metrics") or {}),
            metadata=dict(data.get("metadata") or {}),
        )

    def _row_to_event(self, row: Any) -> RecorderEvent:
        data = row if isinstance(row, dict) else dict(row)
        payload = dict(data.get("payload") or {})
        # payload 存完整事件 dump；列值（seq/type/timestamp/screenshot_ref）为索引冗余，
        # 以列值为准重建，保证列与 payload 不一致时以索引列为准。
        payload["seq"] = int(data["seq"])
        payload["type"] = str(data["type"])
        payload["timestamp"] = ensure_utc_datetime(data["timestamp"])
        if data.get("screenshot_ref"):
            payload["screenshot_ref"] = str(data["screenshot_ref"])
        return RecorderEvent.model_validate(payload)
