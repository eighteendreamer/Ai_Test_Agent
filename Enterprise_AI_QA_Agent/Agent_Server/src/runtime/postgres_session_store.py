from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime

from src.core.config import Settings
from src.domain.models import SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime, make_json_safe
from src.schemas.session import (
    ChatMessage,
    ExecutionEvent,
    MessageRole,
    RuntimeMode,
    SessionMode,
    SessionSnapshot,
    SessionStatus,
    ToolApprovalRequest,
    ToolApprovalStatus,
)
from src.schemas.task_pool import TaskPoolSessionSummary


class PostgresSessionStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queues: dict[str, asyncio.Queue[ExecutionEvent]] = defaultdict(asyncio.Queue)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def save_session(self, session: SessionRecord) -> SessionRecord:
        return await asyncio.to_thread(self._save_session_sync, session)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return await asyncio.to_thread(self._get_session_sync, session_id, True)

    async def list_sessions(
        self,
        limit: int | None = None,
        offset: int = 0,
        mode_key: str | None = None,
        cursor_before: datetime | None = None,
    ) -> list[SessionRecord]:
        return await asyncio.to_thread(self._list_sessions_sync, limit, offset, mode_key, cursor_before)

    async def list_task_pool_sessions(
        self,
        limit: int = 24,
        offset: int = 0,
    ) -> list[TaskPoolSessionSummary]:
        return await asyncio.to_thread(
            self._list_task_pool_sessions_sync,
            limit,
            offset,
        )

    async def list_history_overviews(self, limit: int = 10) -> list[dict]:
        return await asyncio.to_thread(self._list_history_overviews_sync, limit)

    async def list_recent_questions(
        self,
        session_limit: int = 10,
        question_limit: int = 10,
        include_assistant: bool = False,
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._list_recent_questions_sync,
            session_limit,
            question_limit,
            include_assistant,
        )

    async def append_event(
        self,
        session_id: str,
        event: ExecutionEvent,
        *,
        publish: bool = True,
    ) -> None:
        await asyncio.to_thread(self._append_event_sync, session_id, event)
        if publish:
            await self._queues[session_id].put(event)

    async def list_events(
        self,
        session_id: str,
        limit: int | None = None,
        after_event_id: str | None = None,
    ) -> list[ExecutionEvent]:
        return await asyncio.to_thread(self._list_events_sync, session_id, limit, after_event_id)

    def get_queue(self, session_id: str) -> asyncio.Queue[ExecutionEvent]:
        return self._queues[session_id]

    async def save_snapshot(self, session_id: str, snapshot: SessionSnapshot) -> None:
        await asyncio.to_thread(self._save_snapshot_sync, session_id, snapshot)

    async def list_snapshots(
        self,
        session_id: str,
        limit: int | None = None,
        include_graph_state: bool = True,
    ) -> list[SessionSnapshot]:
        return await asyncio.to_thread(
            self._list_snapshots_sync,
            session_id,
            limit,
            include_graph_state,
        )

    async def get_latest_snapshot(self, session_id: str) -> SessionSnapshot | None:
        return await asyncio.to_thread(self._get_latest_snapshot_sync, session_id)

    async def save_approval(self, session_id: str, approval: ToolApprovalRequest) -> None:
        await asyncio.to_thread(self._save_approval_sync, session_id, approval)

    async def list_approvals(self, session_id: str) -> list[ToolApprovalRequest]:
        return await asyncio.to_thread(self._list_approvals_sync, session_id)

    async def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest:
        return await asyncio.to_thread(
            self._resolve_approval_sync,
            session_id,
            approval_id,
            status,
            reason,
        )

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_session_table} (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL,
                        session_mode TEXT NOT NULL,
                        runtime_mode TEXT NOT NULL,
                        mode_key TEXT NOT NULL DEFAULT 'default',
                        project_id UUID NULL REFERENCES {self._settings.database.postgres_project_table}(id),
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        preferred_model TEXT NULL,
                        selected_agent TEXT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        event_count INTEGER NOT NULL DEFAULT 0,
                        snapshot_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                cur.execute(
                    f"ALTER TABLE {self._settings.database.postgres_session_table} "
                    f"ADD COLUMN IF NOT EXISTS project_id UUID NULL REFERENCES {self._settings.database.postgres_project_table}(id)"
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_message_table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_event_table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_snapshot_table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        stage TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        graph_state JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        UNIQUE(session_id, version)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_approval_table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        tool_key TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        resolved_at TIMESTAMPTZ NULL,
                        decision_note TEXT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_updated "
                    f"ON {self._settings.database.postgres_session_table} (updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_project_updated "
                    f"ON {self._settings.database.postgres_session_table} (project_id, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_message_table}_session_created "
                    f"ON {self._settings.database.postgres_message_table} (session_id, created_at ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_event_table}_session_timestamp "
                    f"ON {self._settings.database.postgres_event_table} (session_id, timestamp ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_event_table}_session_timestamp_id "
                    f"ON {self._settings.database.postgres_event_table} (session_id, timestamp ASC, id ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_snapshot_table}_session_version "
                    f"ON {self._settings.database.postgres_snapshot_table} (session_id, version DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_approval_table}_session_created "
                    f"ON {self._settings.database.postgres_approval_table} (session_id, created_at ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_mode_updated "
                    f"ON {self._settings.database.postgres_session_table} (mode_key, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_status "
                    f"ON {self._settings.database.postgres_session_table} (status)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_created "
                    f"ON {self._settings.database.postgres_session_table} (created_at DESC)"
                )

    def _save_session_sync(self, session: SessionRecord) -> SessionRecord:
        now = datetime.utcnow()
        session.updated_at = now
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._settings.database.postgres_session_table} (
                        id, title, status, session_mode, runtime_mode, mode_key, project_id,
                        created_at, updated_at, preferred_model, selected_agent,
                        metadata, event_count, snapshot_count
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s::jsonb, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        status = EXCLUDED.status,
                        session_mode = EXCLUDED.session_mode,
                        runtime_mode = EXCLUDED.runtime_mode,
                        mode_key = EXCLUDED.mode_key,
                        project_id = EXCLUDED.project_id,
                        updated_at = EXCLUDED.updated_at,
                        preferred_model = EXCLUDED.preferred_model,
                        selected_agent = EXCLUDED.selected_agent,
                        metadata = EXCLUDED.metadata,
                        event_count = GREATEST({self._settings.database.postgres_session_table}.event_count, EXCLUDED.event_count),
                        snapshot_count = GREATEST({self._settings.database.postgres_session_table}.snapshot_count, EXCLUDED.snapshot_count)
                    RETURNING event_count, snapshot_count
                    """,
                    (
                        session.id,
                        session.title,
                        session.status.value,
                        session.session_mode.value,
                        session.runtime_mode.value,
                        session.mode_key,
                        session.project_id,
                        session.created_at,
                        now,
                        session.preferred_model,
                        session.selected_agent,
                        json.dumps(make_json_safe(session.metadata), ensure_ascii=False),
                        int(session.event_count),
                        int(session.snapshot_count),
                    ),
                )
                counts = cur.fetchone() or {}
                session.event_count = int(counts.get("event_count") or 0)
                session.snapshot_count = int(counts.get("snapshot_count") or 0)
                for item in session.messages:
                    if not bool(item.metadata.get("persist_transcript", True)):
                        continue
                    cur.execute(
                        f"""
                        INSERT INTO {self._settings.database.postgres_message_table} (
                            id, session_id, role, content, created_at, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (id) DO UPDATE SET
                            session_id = EXCLUDED.session_id,
                            role = EXCLUDED.role,
                            content = EXCLUDED.content,
                            created_at = EXCLUDED.created_at,
                            metadata = EXCLUDED.metadata
                        """,
                        (
                            item.id,
                            session.id,
                            item.role.value,
                            item.content,
                            item.created_at,
                            json.dumps(make_json_safe(item.metadata), ensure_ascii=False),
                        ),
                    )
        return session

    def _get_session_sync(self, session_id: str, include_messages: bool) -> SessionRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._settings.database.postgres_session_table} WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                messages: list[ChatMessage] = []
                if include_messages:
                    cur.execute(
                        f"""
                        SELECT * FROM {self._settings.database.postgres_message_table}
                        WHERE session_id = %s
                        ORDER BY created_at ASC
                        """,
                        (session_id,),
                    )
                    messages = [_message_from_row(item) for item in (cur.fetchall() or [])]
        return _session_from_row(row, messages)

    def _list_sessions_sync(
        self,
        limit: int | None = None,
        offset: int = 0,
        mode_key: str | None = None,
        cursor_before: datetime | None = None,
    ) -> list[SessionRecord]:
        conditions: list[str] = []
        params: list[object] = []
        if mode_key:
            conditions.append("mode_key = %s")
            params.append(mode_key)
        if cursor_before is not None:
            conditions.append("updated_at < %s")
            params.append(cursor_before)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM {self._settings.database.postgres_session_table} {where} ORDER BY updated_at DESC"
        if limit is not None:
            query += " LIMIT %s"
            params.append(max(int(limit or 0), 0))
            if cursor_before is None and offset:
                query += " OFFSET %s"
                params.append(max(int(offset or 0), 0))
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if params:
                    cur.execute(query, params)
                else:
                    cur.execute(query)
                rows = cur.fetchall() or []
        return [_session_from_row(item, []) for item in rows]

    def _list_task_pool_sessions_sync(
        self,
        limit: int = 24,
        offset: int = 0,
    ) -> list[TaskPoolSessionSummary]:
        normalized_limit = max(int(limit or 0), 0)
        normalized_offset = max(int(offset or 0), 0)
        if normalized_limit <= 0:
            return []
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        title,
                        status,
                        session_mode,
                        runtime_mode,
                        mode_key,
                        project_id,
                        created_at,
                        updated_at,
                        selected_agent,
                        COALESCE(metadata->'worker_dispatches', '[]'::jsonb)
                            AS worker_dispatches,
                        COALESCE(metadata->>'parent_session_id', '')
                            AS parent_session_id
                    FROM {self._settings.database.postgres_session_table}
                    WHERE mode_key = 'code_review'
                       OR session_mode = 'background_task'
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (normalized_limit, normalized_offset),
                )
                rows = cur.fetchall() or []
        return [_task_pool_session_from_row(item) for item in rows]

    def _list_history_overviews_sync(self, limit: int = 10) -> list[dict]:
        normalized_limit = max(1, min(int(limit), 50))
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT s.id, s.title, s.status, s.created_at, s.updated_at,
                           s.event_count, s.snapshot_count, s.selected_agent,
                           s.preferred_model,
                           (
                               SELECT COUNT(*)
                               FROM {self._settings.database.postgres_message_table} m
                               WHERE m.session_id = s.id
                           ) AS message_count
                    FROM {self._settings.database.postgres_session_table} s
                    ORDER BY s.updated_at DESC
                    LIMIT %s
                    """,
                    (normalized_limit,),
                )
                rows = list(cur.fetchall() or [])
        return [_history_overview_from_row(row) for row in rows]

    def _list_recent_questions_sync(
        self,
        session_limit: int = 10,
        question_limit: int = 10,
        include_assistant: bool = False,
    ) -> list[dict]:
        roles = ["user", "assistant"] if include_assistant else ["user"]
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH recent_sessions AS (
                        SELECT id
                        FROM {self._settings.database.postgres_session_table}
                        ORDER BY updated_at DESC
                        LIMIT %s
                    )
                    SELECT m.session_id, m.role, m.content, m.created_at
                    FROM recent_sessions rs
                    JOIN {self._settings.database.postgres_message_table} m
                      ON m.session_id = rs.id
                    WHERE m.role = ANY(%s)
                      AND BTRIM(m.content) <> ''
                    ORDER BY m.created_at DESC
                    LIMIT %s
                    """,
                    (
                        max(1, min(int(session_limit), 50)),
                        roles,
                        max(1, min(int(question_limit), 50)),
                    ),
                )
                rows = list(cur.fetchall() or [])
        return [
            {
                "session_id": row["session_id"],
                "role": row["role"],
                "content": str(row.get("content") or "").strip(),
                "created_at": (
                    ensure_utc_datetime(row.get("created_at")) or datetime.utcnow()
                ).isoformat(),
            }
            for row in rows
        ]

    def _append_event_sync(self, session_id: str, event: ExecutionEvent) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._settings.database.postgres_event_table} (
                        id, session_id, type, timestamp, payload
                    ) VALUES (%s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        event.id,
                        session_id,
                        event.type,
                        event.timestamp,
                        json.dumps(make_json_safe(event.payload), ensure_ascii=False),
                    ),
                )
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table}
                    SET event_count = event_count + 1,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (datetime.utcnow(), session_id),
                )

    def _list_events_sync(
        self,
        session_id: str,
        limit: int | None = None,
        after_event_id: str | None = None,
    ) -> list[ExecutionEvent]:
        limit_value = _normalize_optional_limit(limit)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if after_event_id:
                    cur.execute(
                        f"""
                        SELECT timestamp, id FROM {self._settings.database.postgres_event_table}
                        WHERE session_id = %s AND id = %s
                        LIMIT 1
                        """,
                        (session_id, after_event_id),
                    )
                    cursor = cur.fetchone()
                    if not cursor:
                        return []
                    if limit_value is not None:
                        cur.execute(
                            f"""
                            SELECT * FROM {self._settings.database.postgres_event_table}
                            WHERE session_id = %s
                              AND (timestamp > %s OR (timestamp = %s AND id > %s))
                            ORDER BY timestamp ASC, id ASC
                            LIMIT %s
                            """,
                            (
                                session_id,
                                cursor["timestamp"],
                                cursor["timestamp"],
                                cursor["id"],
                                limit_value,
                            ),
                        )
                    else:
                        cur.execute(
                            f"""
                            SELECT * FROM {self._settings.database.postgres_event_table}
                            WHERE session_id = %s
                              AND (timestamp > %s OR (timestamp = %s AND id > %s))
                            ORDER BY timestamp ASC, id ASC
                            """,
                            (
                                session_id,
                                cursor["timestamp"],
                                cursor["timestamp"],
                                cursor["id"],
                            ),
                        )
                elif limit_value is not None:
                    cur.execute(
                        f"""
                        SELECT * FROM (
                            SELECT * FROM {self._settings.database.postgres_event_table}
                            WHERE session_id = %s
                            ORDER BY timestamp DESC, id DESC
                            LIMIT %s
                        ) recent_events
                        ORDER BY timestamp ASC, id ASC
                        """,
                        (session_id, limit_value),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT * FROM {self._settings.database.postgres_event_table}
                        WHERE session_id = %s
                        ORDER BY timestamp ASC, id ASC
                        """,
                        (session_id,),
                    )
                rows = cur.fetchall() or []
        return [
            ExecutionEvent(
                id=str(row["id"]),
                type=row["type"],
                session_id=session_id,
                timestamp=ensure_utc_datetime(row["timestamp"]) or datetime.utcnow(),
                payload=dict(row.get("payload") or {}),
            )
            for row in rows
        ]

    def _save_snapshot_sync(self, session_id: str, snapshot: SessionSnapshot) -> None:
        now = datetime.utcnow()
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._settings.database.postgres_snapshot_table} (
                        id, session_id, version, stage, created_at, graph_state
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        version = EXCLUDED.version,
                        stage = EXCLUDED.stage,
                        created_at = EXCLUDED.created_at,
                        graph_state = EXCLUDED.graph_state
                    """,
                    (
                        snapshot.id,
                        session_id,
                        snapshot.version,
                        snapshot.stage,
                        snapshot.created_at,
                        json.dumps(make_json_safe(snapshot.graph_state), ensure_ascii=False),
                    ),
                )
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table}
                    SET snapshot_count = GREATEST(snapshot_count, %s),
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (snapshot.version, now, session_id),
                )

    def _list_snapshots_sync(
        self,
        session_id: str,
        limit: int | None = None,
        include_graph_state: bool = True,
    ) -> list[SessionSnapshot]:
        limit_value = _normalize_optional_limit(limit)
        columns = "*" if include_graph_state else "id, session_id, version, stage, created_at, '{}'::jsonb AS graph_state"
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if limit_value is not None:
                    cur.execute(
                        f"""
                        SELECT * FROM (
                            SELECT {columns} FROM {self._settings.database.postgres_snapshot_table}
                            WHERE session_id = %s
                            ORDER BY version DESC
                            LIMIT %s
                        ) recent_snapshots
                        ORDER BY version ASC
                        """,
                        (session_id, limit_value),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT {columns} FROM {self._settings.database.postgres_snapshot_table}
                        WHERE session_id = %s
                        ORDER BY version ASC
                        """,
                        (session_id,),
                    )
                rows = cur.fetchall() or []
        return [_snapshot_from_row(item) for item in rows]

    def _get_latest_snapshot_sync(self, session_id: str) -> SessionSnapshot | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {self._settings.database.postgres_snapshot_table}
                    WHERE session_id = %s
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
        return _snapshot_from_row(row) if row else None

    def _save_approval_sync(self, session_id: str, approval: ToolApprovalRequest) -> None:
        now = datetime.utcnow()
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._settings.database.postgres_approval_table} (
                        id, session_id, tool_key, tool_name, reason, status,
                        created_at, resolved_at, decision_note, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        tool_key = EXCLUDED.tool_key,
                        tool_name = EXCLUDED.tool_name,
                        reason = EXCLUDED.reason,
                        status = EXCLUDED.status,
                        created_at = EXCLUDED.created_at,
                        resolved_at = EXCLUDED.resolved_at,
                        decision_note = EXCLUDED.decision_note,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        approval.id,
                        session_id,
                        approval.tool_key,
                        approval.tool_name,
                        approval.reason,
                        approval.status.value,
                        approval.created_at,
                        approval.resolved_at,
                        approval.decision_note,
                        json.dumps(make_json_safe(approval.metadata), ensure_ascii=False),
                    ),
                )
                cur.execute(
                    f"UPDATE {self._settings.database.postgres_session_table} SET updated_at = %s WHERE id = %s",
                    (now, session_id),
                )

    def _list_approvals_sync(self, session_id: str) -> list[ToolApprovalRequest]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {self._settings.database.postgres_approval_table}
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall() or []
        return [_approval_from_row(item) for item in rows]

    def _resolve_approval_sync(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest:
        resolved_at = datetime.utcnow()
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_approval_table}
                    SET status = %s,
                        decision_note = %s,
                        resolved_at = %s
                    WHERE id = %s AND session_id = %s AND status = 'pending'
                    RETURNING *
                    """,
                    (status.value, reason, resolved_at, approval_id, session_id),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        f"SELECT * FROM {self._settings.database.postgres_approval_table} "
                        "WHERE id = %s AND session_id = %s",
                        (approval_id, session_id),
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        raise KeyError(approval_id)
                    approval = _approval_from_row(existing)
                    if approval.status == status:
                        return approval
                    raise ValueError(f"Approval already resolved: {approval_id}")
                cur.execute(
                    f"UPDATE {self._settings.database.postgres_session_table} SET updated_at = %s WHERE id = %s",
                    (resolved_at, session_id),
                )
        return _approval_from_row(row)

    async def delete_session(self, session_id: str) -> bool:
        def _do() -> bool:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    for tbl in (
                        self._settings.database.postgres_approval_table,
                        self._settings.database.postgres_snapshot_table,
                        self._settings.database.postgres_event_table,
                        self._settings.database.postgres_message_table,
                    ):
                        cur.execute(f"DELETE FROM {tbl} WHERE session_id = %s", (session_id,))
                    cur.execute(
                        f"DELETE FROM {self._settings.database.postgres_session_table} WHERE id = %s RETURNING id",
                        (session_id,),
                    )
                    deleted = cur.fetchone() is not None
                    conn.commit()
                    return deleted

        return await asyncio.to_thread(_do)

    async def count_sessions(self, before: datetime | None = None, after: datetime | None = None) -> int:
        def _do() -> int:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    table = self._settings.database.postgres_session_table
                    if before is None and after is None:
                        cur.execute(f"SELECT COUNT(id) AS cnt FROM {table}")
                    elif before and after:
                        cur.execute(
                            f"SELECT COUNT(id) AS cnt FROM {table} WHERE updated_at >= %s AND updated_at < %s",
                            (after, before),
                        )
                    elif after:
                        cur.execute(
                            f"SELECT COUNT(id) AS cnt FROM {table} WHERE updated_at >= %s",
                            (after,),
                        )
                    else:
                        cur.execute(
                            f"SELECT COUNT(id) AS cnt FROM {table} WHERE updated_at < %s",
                            (before,),
                        )
                    row = cur.fetchone()
                    return int(row["cnt"]) if row else 0
        return await asyncio.to_thread(_do)

    async def count_sessions_by_status(self) -> dict[str, int]:
        def _do() -> dict[str, int]:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT status, COUNT(id) AS cnt FROM {self._settings.database.postgres_session_table} GROUP BY status"
                    )
                    rows = cur.fetchall() or []
                    return {row["status"]: int(row["cnt"]) for row in rows}
        return await asyncio.to_thread(_do)

    async def count_project_sessions(self, project_id: str) -> int:
        def _do() -> int:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT COUNT(id) AS cnt FROM {self._settings.database.postgres_session_table} WHERE project_id = %s",
                        (project_id,),
                    )
                    row = cur.fetchone()
                    return int(row["cnt"]) if row else 0
        return await asyncio.to_thread(_do)

    async def list_project_history_context(
        self,
        project_id: str,
        *,
        limit: int,
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._list_project_history_context_sync,
            project_id,
            max(1, min(limit, 50)),
        )

    def _list_project_history_context_sync(self, project_id: str, limit: int) -> list[dict]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, title, mode_key, status, updated_at, metadata
                    FROM {self._settings.database.postgres_session_table}
                    WHERE project_id = %s
                    ORDER BY updated_at DESC, id ASC
                    LIMIT %s
                    """,
                    (project_id, limit),
                )
                rows = cur.fetchall() or []
        return [_history_context_from_row(row) for row in rows]

    async def delete_sessions_before(self, cutoff: datetime | None = None) -> int:
        def _do() -> int:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    if cutoff is None:
                        where = ""
                        params: tuple = ()
                    else:
                        where = "WHERE updated_at < %s"
                        params = (cutoff,)
                    id_query = f"SELECT id FROM {self._settings.database.postgres_session_table} {where}"
                    for tbl in (
                        self._settings.database.postgres_approval_table,
                        self._settings.database.postgres_snapshot_table,
                        self._settings.database.postgres_event_table,
                        self._settings.database.postgres_message_table,
                    ):
                        cur.execute(f"DELETE FROM {tbl} WHERE session_id IN ({id_query})", params)
                    cur.execute(
                        f"DELETE FROM {self._settings.database.postgres_session_table} {where} RETURNING id",
                        params,
                    )
                    deleted = len(cur.fetchall())
                    conn.commit()
                    return deleted
        return await asyncio.to_thread(_do)

    async def get_session_date_range(self) -> dict[str, datetime | None]:
        def _do() -> dict[str, datetime | None]:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    tbl = self._settings.database.postgres_session_table
                    cur.execute(f"SELECT MIN(updated_at) AS oldest, MAX(updated_at) AS newest FROM {tbl}")
                    row = cur.fetchone()
                    if not row:
                        return {"oldest": None, "newest": None}
                    return {"oldest": row["oldest"], "newest": row["newest"]}
        return await asyncio.to_thread(_do)

    async def bulk_export(self, progress_fn=None) -> list[dict]:
        return await asyncio.to_thread(self._bulk_export_sync, progress_fn)

    def _bulk_export_sync(self, progress_fn=None) -> list[dict]:
        """Batch-streamed export: iterate sessions in chunks, 5 queries per chunk."""
        s_tbl = self._settings.database.postgres_session_table
        m_tbl = self._settings.database.postgres_message_table
        e_tbl = self._settings.database.postgres_event_table
        snap_tbl = self._settings.database.postgres_snapshot_table
        a_tbl = self._settings.database.postgres_approval_table
        batch_size = 500

        def _iso(v: object) -> str | None:
            return v.isoformat() if hasattr(v, "isoformat") else None

        bundle: list[dict] = []
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT id FROM {s_tbl} ORDER BY updated_at DESC")
                all_ids = [row["id"] for row in (cur.fetchall() or [])]

            for i in range(0, len(all_ids), batch_size):
                chunk_ids = all_ids[i : i + batch_size]
                if progress_fn:
                    progress_fn(min(i + len(chunk_ids), len(all_ids)))
                placeholders = ",".join(["%s"] * len(chunk_ids))

                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT * FROM {s_tbl} WHERE id IN ({placeholders}) ORDER BY updated_at DESC",
                        chunk_ids,
                    )
                    session_rows = cur.fetchall() or []

                    cur.execute(
                        f"SELECT * FROM {m_tbl} WHERE session_id IN ({placeholders}) ORDER BY session_id, created_at",
                        chunk_ids,
                    )
                    msg_rows = cur.fetchall() or []

                    cur.execute(
                        f"SELECT * FROM {e_tbl} WHERE session_id IN ({placeholders}) ORDER BY session_id, timestamp",
                        chunk_ids,
                    )
                    event_rows = cur.fetchall() or []

                    cur.execute(
                        f"SELECT * FROM {snap_tbl} WHERE session_id IN ({placeholders}) ORDER BY session_id, version",
                        chunk_ids,
                    )
                    snap_rows = cur.fetchall() or []

                    cur.execute(
                        f"SELECT * FROM {a_tbl} WHERE session_id IN ({placeholders}) ORDER BY session_id, created_at",
                        chunk_ids,
                    )
                    approval_rows = cur.fetchall() or []

                from collections import defaultdict
                msgs_map: dict[str, list] = defaultdict(list)
                for r in msg_rows:
                    msgs_map[r["session_id"]].append(r)
                events_map: dict[str, list] = defaultdict(list)
                for r in event_rows:
                    events_map[r["session_id"]].append(r)
                snaps_map: dict[str, list] = defaultdict(list)
                for r in snap_rows:
                    snaps_map[r["session_id"]].append(r)
                approvals_map: dict[str, list] = defaultdict(list)
                for r in approval_rows:
                    approvals_map[r["session_id"]].append(r)

                for s in session_rows:
                    sid = s["id"]
                    bundle.append({
                        "id": sid,
                        "title": s["title"],
                        "status": s["status"],
                        "session_mode": s["session_mode"],
                        "runtime_mode": s["runtime_mode"],
                        "mode_key": s["mode_key"],
                        "created_at": _iso(s["created_at"]),
                        "updated_at": _iso(s["updated_at"]),
                        "preferred_model": s.get("preferred_model"),
                        "selected_agent": s.get("selected_agent"),
                        "project_id": str(s.get("project_id")) if s.get("project_id") else None,
                        "metadata": dict(s.get("metadata") or {}),
                        "event_count": s["event_count"],
                        "snapshot_count": s["snapshot_count"],
                        "messages": [
                            {"id": m["id"], "role": m["role"], "content": m["content"],
                             "created_at": _iso(m["created_at"]), "metadata": dict(m.get("metadata") or {})}
                            for m in msgs_map.get(sid, [])
                        ],
                        "events": [
                            {"type": e["type"], "session_id": sid,
                             "timestamp": _iso(e["timestamp"]), "payload": dict(e.get("payload") or {})}
                            for e in events_map.get(sid, [])
                        ],
                        "snapshots": [
                            {"id": sn["id"], "session_id": sid, "version": sn["version"],
                             "stage": sn["stage"], "created_at": _iso(sn["created_at"]),
                             "graph_state": dict(sn.get("graph_state") or {})}
                            for sn in snaps_map.get(sid, [])
                        ],
                        "approvals": [
                            {"id": a["id"], "session_id": sid, "tool_key": a["tool_key"],
                             "tool_name": a["tool_name"], "reason": a["reason"], "status": a["status"],
                             "created_at": _iso(a["created_at"]), "resolved_at": _iso(a.get("resolved_at")),
                             "decision_note": a.get("decision_note"), "metadata": dict(a.get("metadata") or {})}
                            for a in approvals_map.get(sid, [])
                        ],
                    })
        return bundle


def _session_from_row(row: dict, messages: list[ChatMessage]) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        title=row["title"],
        status=SessionStatus(row["status"]),
        session_mode=SessionMode(row["session_mode"]),
        runtime_mode=RuntimeMode(row["runtime_mode"]),
        mode_key=str(row.get("mode_key") or "default"),
        project_id=str(row.get("project_id")) if row.get("project_id") else None,
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
        preferred_model=row.get("preferred_model"),
        selected_agent=row.get("selected_agent"),
        metadata=dict(row.get("metadata") or {}),
        messages=messages,
        event_count=int(row.get("event_count") or 0),
        snapshot_count=int(row.get("snapshot_count") or 0),
    )


def _history_overview_from_row(row: dict) -> dict:
    return {
        "session_id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "created_at": (
            ensure_utc_datetime(row.get("created_at")) or datetime.utcnow()
        ).isoformat(),
        "updated_at": (
            ensure_utc_datetime(row.get("updated_at")) or datetime.utcnow()
        ).isoformat(),
        "message_count": int(row.get("message_count") or 0),
        "event_count": int(row.get("event_count") or 0),
        "snapshot_count": int(row.get("snapshot_count") or 0),
        "selected_agent": row.get("selected_agent"),
        "preferred_model": row.get("preferred_model"),
    }


def _history_context_from_row(row: dict) -> dict:
    metadata = dict(row.get("metadata") or {})
    failure_summary = str(
        metadata.get("last_error")
        or metadata.get("failure_summary")
        or metadata.get("report_summary")
        or ""
    ).strip()
    if not failure_summary:
        for result in metadata.get("verification_results", []):
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "") in {"failed", "partial"}:
                failure_summary = str(result.get("summary") or "").strip()
                if failure_summary:
                    break
    updated_at = ensure_utc_datetime(row.get("updated_at")) or datetime.utcnow()
    return {
        "session_id": str(row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "mode_key": str(row.get("mode_key") or "default"),
        "status": str(row.get("status") or ""),
        "updated_at": updated_at.isoformat(),
        "failure_summary": failure_summary[:500],
    }


def _task_pool_session_from_row(row: dict) -> TaskPoolSessionSummary:
    raw_workers = row.get("worker_dispatches")
    workers = []
    if isinstance(raw_workers, list):
        workers = [dict(item) for item in raw_workers if isinstance(item, dict)]
    return TaskPoolSessionSummary(
        id=row["id"],
        title=row["title"],
        status=SessionStatus(row["status"]),
        session_mode=SessionMode(row["session_mode"]),
        runtime_mode=RuntimeMode(row["runtime_mode"]),
        mode_key=str(row.get("mode_key") or "default"),
        project_id=str(row.get("project_id")) if row.get("project_id") else None,
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
        selected_agent=row.get("selected_agent"),
        worker_dispatches=workers,
        parent_session_id=str(row.get("parent_session_id") or "").strip(),
    )


def _message_from_row(row: dict) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        role=MessageRole(row["role"]),
        content=row["content"],
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        metadata=dict(row.get("metadata") or {}),
    )


def _snapshot_from_row(row: dict) -> SessionSnapshot:
    return SessionSnapshot(
        id=row["id"],
        session_id=row["session_id"],
        version=int(row["version"]),
        stage=row["stage"],
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        graph_state=dict(row.get("graph_state") or {}),
    )


def _normalize_optional_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    size = max(int(limit), 0)
    return size if size > 0 else 0


def _approval_from_row(row: dict) -> ToolApprovalRequest:
    return ToolApprovalRequest(
        id=row["id"],
        session_id=row["session_id"],
        tool_key=row["tool_key"],
        tool_name=row["tool_name"],
        reason=row["reason"],
        status=ToolApprovalStatus(row["status"]),
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        resolved_at=ensure_utc_datetime(row.get("resolved_at")),
        decision_note=row.get("decision_note"),
        metadata=dict(row.get("metadata") or {}),
    )
