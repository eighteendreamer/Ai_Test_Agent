from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Protocol

import pymysql

from src.core.config import Settings
from src.schemas.tool_job import ToolArtifactRecord, ToolJobRecord, ToolJobStatus


class ToolJobStore(Protocol):
    async def initialize(self) -> None: ...
    async def save_job(self, job: ToolJobRecord) -> ToolJobRecord: ...
    async def get_job(self, job_id: str) -> ToolJobRecord | None: ...
    async def list_jobs(self, session_id: str | None = None) -> list[ToolJobRecord]: ...
    async def save_artifact(self, artifact: ToolArtifactRecord) -> ToolArtifactRecord: ...
    async def list_artifacts(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]: ...
    async def mark_stale_running_jobs(self, timeout_seconds: int) -> list[ToolJobRecord]: ...


class InMemoryToolJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, ToolJobRecord] = {}
        self._artifacts: dict[str, ToolArtifactRecord] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def save_job(self, job: ToolJobRecord) -> ToolJobRecord:
        async with self._lock:
            job.updated_at = datetime.utcnow()
            self._jobs[job.id] = job
            return job

    async def get_job(self, job_id: str) -> ToolJobRecord | None:
        return self._jobs.get(job_id)

    async def list_jobs(self, session_id: str | None = None) -> list[ToolJobRecord]:
        values = list(self._jobs.values())
        if session_id:
            values = [job for job in values if job.session_id == session_id]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    async def save_artifact(self, artifact: ToolArtifactRecord) -> ToolArtifactRecord:
        async with self._lock:
            self._artifacts[artifact.id] = artifact
            return artifact

    async def list_artifacts(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]:
        values = list(self._artifacts.values())
        if session_id:
            values = [item for item in values if item.session_id == session_id]
        if tool_job_id:
            values = [item for item in values if item.tool_job_id == tool_job_id]
        return sorted(values, key=lambda item: item.created_at)

    async def mark_stale_running_jobs(self, timeout_seconds: int) -> list[ToolJobRecord]:
        threshold = datetime.utcnow() - timedelta(seconds=timeout_seconds)
        updated: list[ToolJobRecord] = []
        for job in self._jobs.values():
            if job.status == ToolJobStatus.running and (job.heartbeat_at or job.updated_at) < threshold:
                job.status = ToolJobStatus.resume_requested
                job.updated_at = datetime.utcnow()
                updated.append(job)
        return updated


class MySQLToolJobStore:
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

    async def list_artifacts(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]:
        return await asyncio.to_thread(self._list_artifacts_sync, session_id, tool_job_id)

    async def mark_stale_running_jobs(self, timeout_seconds: int) -> list[ToolJobRecord]:
        return await asyncio.to_thread(self._mark_stale_running_jobs_sync, timeout_seconds)

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.tool_job_table}` (
                        `id` VARCHAR(64) NOT NULL PRIMARY KEY,
                        `session_id` VARCHAR(64) NOT NULL,
                        `turn_id` VARCHAR(64) NOT NULL,
                        `trace_id` VARCHAR(64) NOT NULL,
                        `call_id` VARCHAR(64) NOT NULL,
                        `tool_key` VARCHAR(128) NOT NULL,
                        `tool_name` VARCHAR(255) NOT NULL,
                        `status` VARCHAR(64) NOT NULL,
                        `attempt` INT NOT NULL DEFAULT 1,
                        `summary` LONGTEXT NULL,
                        `error_message` LONGTEXT NULL,
                        `artifact_count` INT NOT NULL DEFAULT 0,
                        `input_json` LONGTEXT NULL,
                        `output_json` LONGTEXT NULL,
                        `metadata_json` LONGTEXT NULL,
                        `created_at` DATETIME NOT NULL,
                        `updated_at` DATETIME NOT NULL,
                        `heartbeat_at` DATETIME NULL,
                        `started_at` DATETIME NULL,
                        `completed_at` DATETIME NULL,
                        KEY `idx_tool_job_session` (`session_id`, `created_at`),
                        KEY `idx_tool_job_status` (`status`, `updated_at`)
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.tool_artifact_table}` (
                        `id` VARCHAR(64) NOT NULL PRIMARY KEY,
                        `tool_job_id` VARCHAR(64) NOT NULL,
                        `session_id` VARCHAR(64) NOT NULL,
                        `turn_id` VARCHAR(64) NOT NULL,
                        `trace_id` VARCHAR(64) NOT NULL,
                        `tool_key` VARCHAR(128) NOT NULL,
                        `artifact_type` VARCHAR(128) NOT NULL,
                        `label` VARCHAR(255) NULL,
                        `path` LONGTEXT NOT NULL,
                        `metadata_json` LONGTEXT NULL,
                        `created_at` DATETIME NOT NULL,
                        KEY `idx_artifact_session` (`session_id`, `created_at`),
                        KEY `idx_artifact_job` (`tool_job_id`, `created_at`)
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
            conn.commit()

    def _save_job_sync(self, job: ToolJobRecord) -> ToolJobRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.tool_job_table}`
                    (`id`, `session_id`, `turn_id`, `trace_id`, `call_id`, `tool_key`, `tool_name`,
                     `status`, `attempt`, `summary`, `error_message`, `artifact_count`, `input_json`,
                     `output_json`, `metadata_json`, `created_at`, `updated_at`, `heartbeat_at`,
                     `started_at`, `completed_at`)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `status`=VALUES(`status`),
                        `attempt`=VALUES(`attempt`),
                        `summary`=VALUES(`summary`),
                        `error_message`=VALUES(`error_message`),
                        `artifact_count`=VALUES(`artifact_count`),
                        `input_json`=VALUES(`input_json`),
                        `output_json`=VALUES(`output_json`),
                        `metadata_json`=VALUES(`metadata_json`),
                        `updated_at`=VALUES(`updated_at`),
                        `heartbeat_at`=VALUES(`heartbeat_at`),
                        `started_at`=VALUES(`started_at`),
                        `completed_at`=VALUES(`completed_at`)
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
                        _json_dumps(job.input_payload),
                        _json_dumps(job.output_payload),
                        _json_dumps(job.metadata),
                        job.created_at,
                        job.updated_at,
                        job.heartbeat_at,
                        job.started_at,
                        job.completed_at,
                    ),
                )
            conn.commit()
        return job

    def _get_job_sync(self, job_id: str) -> ToolJobRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM `{self._settings.tool_job_table}` WHERE id=%s", (job_id,))
                row = cur.fetchone()
        return self._row_to_job(row) if row else None

    def _list_jobs_sync(self, session_id: str | None = None) -> list[ToolJobRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if session_id:
                    cur.execute(
                        f"SELECT * FROM `{self._settings.tool_job_table}` WHERE session_id=%s ORDER BY created_at DESC",
                        (session_id,),
                    )
                else:
                    cur.execute(f"SELECT * FROM `{self._settings.tool_job_table}` ORDER BY created_at DESC")
                rows = cur.fetchall()
        return [self._row_to_job(row) for row in rows]

    def _save_artifact_sync(self, artifact: ToolArtifactRecord) -> ToolArtifactRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.tool_artifact_table}`
                    (`id`, `tool_job_id`, `session_id`, `turn_id`, `trace_id`, `tool_key`, `artifact_type`,
                     `label`, `path`, `metadata_json`, `created_at`)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `artifact_type`=VALUES(`artifact_type`),
                        `label`=VALUES(`label`),
                        `path`=VALUES(`path`),
                        `metadata_json`=VALUES(`metadata_json`)
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
                        _json_dumps(artifact.metadata),
                        artifact.created_at,
                    ),
                )
            conn.commit()
        return artifact

    def _list_artifacts_sync(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]:
        conditions: list[str] = []
        values: list[object] = []
        if session_id:
            conditions.append("session_id=%s")
            values.append(session_id)
        if tool_job_id:
            conditions.append("tool_job_id=%s")
            values.append(tool_job_id)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM `{self._settings.tool_artifact_table}` {where_clause} ORDER BY created_at ASC",
                    tuple(values),
                )
                rows = cur.fetchall()
        return [self._row_to_artifact(row) for row in rows]

    def _mark_stale_running_jobs_sync(self, timeout_seconds: int) -> list[ToolJobRecord]:
        threshold = datetime.utcnow() - timedelta(seconds=timeout_seconds)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE `{self._settings.tool_job_table}`
                    SET `status`=%s, `updated_at`=%s
                    WHERE `status`=%s AND COALESCE(`heartbeat_at`, `updated_at`) < %s
                    """,
                    (
                        ToolJobStatus.resume_requested.value,
                        datetime.utcnow(),
                        ToolJobStatus.running.value,
                        threshold,
                    ),
                )
                cur.execute(
                    f"SELECT * FROM `{self._settings.tool_job_table}` WHERE `status`=%s ORDER BY updated_at DESC",
                    (ToolJobStatus.resume_requested.value,),
                )
                rows = cur.fetchall()
            conn.commit()
        return [self._row_to_job(row) for row in rows]

    def _row_to_job(self, row: dict) -> ToolJobRecord:
        return ToolJobRecord(
            id=row["id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            trace_id=row["trace_id"],
            call_id=row["call_id"],
            tool_key=row["tool_key"],
            tool_name=row["tool_name"],
            status=ToolJobStatus(row["status"]),
            attempt=int(row["attempt"] or 1),
            input_payload=_json_loads(row.get("input_json")),
            output_payload=_json_loads(row.get("output_json")),
            summary=row.get("summary") or "",
            error_message=row.get("error_message"),
            artifact_count=int(row.get("artifact_count") or 0),
            created_at=_to_datetime(row["created_at"]),
            updated_at=_to_datetime(row["updated_at"]),
            heartbeat_at=_to_datetime(row.get("heartbeat_at")),
            started_at=_to_datetime(row.get("started_at")),
            completed_at=_to_datetime(row.get("completed_at")),
            metadata=_json_loads(row.get("metadata_json")),
        )

    def _row_to_artifact(self, row: dict) -> ToolArtifactRecord:
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
            created_at=_to_datetime(row["created_at"]),
            metadata=_json_loads(row.get("metadata_json")),
        )

    def _connect(self):
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password,
            database=self._settings.mysql_database,
            charset=self._settings.mysql_charset,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None) -> dict:
    if not value:
        return {}
    loaded = json.loads(value)
    if isinstance(loaded, dict):
        return loaded
    return {"value": loaded}


def _to_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
