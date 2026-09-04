from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime, make_json_safe
from src.schemas.tool_job import ToolArtifactRecord, ToolJobRecord, ToolJobStatus


class PostgresToolJobStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def save_job(self, job: ToolJobRecord) -> ToolJobRecord:
        return await asyncio.to_thread(self._save_job_sync, job)

    async def get_job(self, job_id: str) -> ToolJobRecord | None:
        return await asyncio.to_thread(self._get_job_sync, job_id)

    async def list_jobs(self, session_id: str | None = None) -> list[ToolJobRecord]:
        return await asyncio.to_thread(self._list_jobs_sync, session_id)

    async def save_artifact(self, artifact: ToolArtifactRecord) -> ToolArtifactRecord:
        return await asyncio.to_thread(self._save_artifact_sync, artifact)

    async def get_artifact(self, artifact_id: str) -> ToolArtifactRecord | None:
        return await asyncio.to_thread(self._get_artifact_sync, artifact_id)

    async def list_artifacts(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]:
        return await asyncio.to_thread(self._list_artifacts_sync, session_id, tool_job_id)

    async def list_artifacts_for_sessions(
        self,
        session_ids: list[str],
    ) -> list[ToolArtifactRecord]:
        return await asyncio.to_thread(self._list_artifacts_for_sessions_sync, session_ids)

    async def mark_stale_running_jobs(self, timeout_seconds: int) -> list[ToolJobRecord]:
        return await asyncio.to_thread(self._mark_stale_running_jobs_sync, timeout_seconds)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_tool_job_table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        call_id TEXT NOT NULL,
                        tool_key TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempt INTEGER NOT NULL DEFAULT 1,
                        summary TEXT NOT NULL DEFAULT '',
                        error_message TEXT NULL,
                        artifact_count INTEGER NOT NULL DEFAULT 0,
                        input_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        output_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        heartbeat_at TIMESTAMPTZ NULL,
                        started_at TIMESTAMPTZ NULL,
                        completed_at TIMESTAMPTZ NULL
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_tool_artifact_table} (
                        id TEXT PRIMARY KEY,
                        tool_job_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        tool_key TEXT NOT NULL,
                        artifact_type TEXT NOT NULL,
                        label TEXT NULL,
                        path TEXT NOT NULL,
                        storage_mode TEXT NOT NULL DEFAULT 'path_only',
                        content_text TEXT NOT NULL DEFAULT '',
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_tool_job_table}_session_created "
                    f"ON {self._settings.database.postgres_tool_job_table} (session_id, created_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_tool_job_table}_status_updated "
                    f"ON {self._settings.database.postgres_tool_job_table} (status, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_tool_artifact_table}_job_created "
                    f"ON {self._settings.database.postgres_tool_artifact_table} (tool_job_id, created_at ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_tool_artifact_table}_session_created "
                    f"ON {self._settings.database.postgres_tool_artifact_table} (session_id, created_at ASC)"
                )

    def _save_job_sync(self, job: ToolJobRecord) -> ToolJobRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._settings.database.postgres_tool_job_table} (
                        id, session_id, turn_id, trace_id, call_id, tool_key, tool_name, status,
                        attempt, summary, error_message, artifact_count, input_payload, output_payload,
                        metadata, created_at, updated_at, heartbeat_at, started_at, completed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        turn_id = EXCLUDED.turn_id,
                        trace_id = EXCLUDED.trace_id,
                        call_id = EXCLUDED.call_id,
                        tool_key = EXCLUDED.tool_key,
                        tool_name = EXCLUDED.tool_name,
                        status = EXCLUDED.status,
                        attempt = EXCLUDED.attempt,
                        summary = EXCLUDED.summary,
                        error_message = EXCLUDED.error_message,
                        artifact_count = EXCLUDED.artifact_count,
                        input_payload = EXCLUDED.input_payload,
                        output_payload = EXCLUDED.output_payload,
                        metadata = EXCLUDED.metadata,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at,
                        heartbeat_at = EXCLUDED.heartbeat_at,
                        started_at = EXCLUDED.started_at,
                        completed_at = EXCLUDED.completed_at
                    """,
                    (
                        job.id,
                        job.session_id,
                        job.turn_id,
                        job.trace_id,
                        job.call_id,
                        job.tool_key,
                        job.tool_name,
                        job.status.value,
                        job.attempt,
                        job.summary,
                        job.error_message,
                        job.artifact_count,
                        json.dumps(make_json_safe(job.input_payload), ensure_ascii=False),
                        json.dumps(make_json_safe(job.output_payload), ensure_ascii=False),
                        json.dumps(make_json_safe(job.metadata), ensure_ascii=False),
                        job.created_at,
                        job.updated_at,
                        job.heartbeat_at,
                        job.started_at,
                        job.completed_at,
                    ),
                )
        return job

    def _get_job_sync(self, job_id: str) -> ToolJobRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._settings.database.postgres_tool_job_table} WHERE id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
        return _job_from_row(row) if row else None

    def _list_jobs_sync(self, session_id: str | None = None) -> list[ToolJobRecord]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if session_id:
                    cur.execute(
                        f"""
                        SELECT * FROM {self._settings.database.postgres_tool_job_table}
                        WHERE session_id = %s
                        ORDER BY created_at DESC
                        """,
                        (session_id,),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT * FROM {self._settings.database.postgres_tool_job_table}
                        ORDER BY created_at DESC
                        """
                    )
                rows = cur.fetchall() or []
        return [_job_from_row(row) for row in rows]

    def _save_artifact_sync(self, artifact: ToolArtifactRecord) -> ToolArtifactRecord:
        storage_mode = str(artifact.metadata.get("__storage_mode") or "path_only")
        content_text = str(artifact.metadata.get("__content_text") or "")
        metadata = _public_metadata(artifact.metadata)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._settings.database.postgres_tool_artifact_table} (
                        id, tool_job_id, session_id, turn_id, trace_id, tool_key, artifact_type,
                        label, path, storage_mode, content_text, metadata, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s::jsonb, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        tool_job_id = EXCLUDED.tool_job_id,
                        session_id = EXCLUDED.session_id,
                        turn_id = EXCLUDED.turn_id,
                        trace_id = EXCLUDED.trace_id,
                        tool_key = EXCLUDED.tool_key,
                        artifact_type = EXCLUDED.artifact_type,
                        label = EXCLUDED.label,
                        path = EXCLUDED.path,
                        storage_mode = EXCLUDED.storage_mode,
                        content_text = EXCLUDED.content_text,
                        metadata = EXCLUDED.metadata,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        artifact.id,
                        artifact.tool_job_id,
                        artifact.session_id,
                        artifact.turn_id,
                        artifact.trace_id,
                        artifact.tool_key,
                        artifact.artifact_type,
                        artifact.label,
                        artifact.path,
                        storage_mode,
                        content_text,
                        json.dumps(make_json_safe(metadata), ensure_ascii=False),
                        artifact.created_at,
                    ),
                )
        return artifact

    def _list_artifacts_sync(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]:
        conditions: list[str] = []
        params: list[object] = []
        if session_id:
            conditions.append("session_id = %s")
            params.append(session_id)
        if tool_job_id:
            conditions.append("tool_job_id = %s")
            params.append(tool_job_id)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {self._settings.database.postgres_tool_artifact_table}
                    {where_clause}
                    ORDER BY created_at ASC
                    """,
                    params,
                )
                rows = cur.fetchall() or []
        return [_artifact_from_row(row) for row in rows]

    def _get_artifact_sync(self, artifact_id: str) -> ToolArtifactRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._settings.database.postgres_tool_artifact_table} WHERE id = %s",
                    (artifact_id,),
                )
                row = cur.fetchone()
        return _artifact_from_row(row) if row else None

    def _list_artifacts_for_sessions_sync(
        self,
        session_ids: list[str],
    ) -> list[ToolArtifactRecord]:
        normalized_ids = list(
            dict.fromkeys(str(item).strip() for item in session_ids if str(item).strip())
        )
        if not normalized_ids:
            return []
        placeholders = ", ".join(["%s"] * len(normalized_ids))
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {self._settings.database.postgres_tool_artifact_table}
                    WHERE session_id IN ({placeholders})
                    ORDER BY created_at ASC
                    """,
                    tuple(normalized_ids),
                )
                rows = cur.fetchall() or []
        return [_artifact_from_row(row) for row in rows]

    def _mark_stale_running_jobs_sync(self, timeout_seconds: int) -> list[ToolJobRecord]:
        threshold = datetime.utcnow() - timedelta(seconds=timeout_seconds)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_tool_job_table}
                    SET status = %s,
                        updated_at = %s
                    WHERE status = %s
                      AND COALESCE(heartbeat_at, updated_at) < %s
                    RETURNING *
                    """,
                    (
                        ToolJobStatus.resume_requested.value,
                        datetime.utcnow(),
                        ToolJobStatus.running.value,
                        threshold,
                    ),
                )
                rows = cur.fetchall() or []
        items = [_job_from_row(row) for row in rows]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items


def _job_from_row(row: dict) -> ToolJobRecord:
    return ToolJobRecord(
        id=row["id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        trace_id=row["trace_id"],
        call_id=row["call_id"],
        tool_key=row["tool_key"],
        tool_name=row["tool_name"],
        status=ToolJobStatus(row["status"]),
        attempt=int(row.get("attempt") or 1),
        input_payload=dict(row.get("input_payload") or {}),
        output_payload=dict(row.get("output_payload") or {}),
        summary=row.get("summary") or "",
        error_message=row.get("error_message"),
        artifact_count=int(row.get("artifact_count") or 0),
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
        heartbeat_at=ensure_utc_datetime(row.get("heartbeat_at")),
        started_at=ensure_utc_datetime(row.get("started_at")),
        completed_at=ensure_utc_datetime(row.get("completed_at")),
        metadata=dict(row.get("metadata") or {}),
    )


def _artifact_from_row(row: dict) -> ToolArtifactRecord:
    metadata = dict(row.get("metadata") or {})
    storage_mode = row.get("storage_mode")
    if storage_mode:
        metadata["__storage_mode"] = storage_mode
    content_text = str(row.get("content_text") or "")
    if content_text:
        metadata["__content_text"] = content_text
    return ToolArtifactRecord(
        id=row["id"],
        tool_job_id=row["tool_job_id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        trace_id=row["trace_id"],
        tool_key=row["tool_key"],
        artifact_type=row["artifact_type"],
        label=row.get("label"),
        path=row["path"],
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        metadata=metadata,
    )


def _public_metadata(metadata: dict) -> dict:
    return {key: value for key, value in (metadata or {}).items() if not str(key).startswith("__")}
