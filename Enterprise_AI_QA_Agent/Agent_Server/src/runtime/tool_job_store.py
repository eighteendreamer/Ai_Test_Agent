from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.arango_runtime import (
    ArangoRuntimeProvider,
    day_bucket,
    ensure_utc_datetime,
    make_json_safe,
    serialize_datetime,
)
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


class ArangoToolJobStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = ArangoRuntimeProvider(settings)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._provider.initialize)

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

    def _save_job_sync(self, job: ToolJobRecord) -> ToolJobRecord:
        collection = self._provider.collection(self._settings.arango_tool_job_collection)
        document = {
            "_key": job.id,
            "id": job.id,
            "session_id": job.session_id,
            "turn_id": job.turn_id,
            "trace_id": job.trace_id,
            "call_id": job.call_id,
            "tool_key": job.tool_key,
            "tool_name": job.tool_name,
            "status": job.status.value,
            "attempt": job.attempt,
            "summary": job.summary,
            "error_message": job.error_message,
            "artifact_count": job.artifact_count,
            "input_payload": make_json_safe(job.input_payload),
            "output_payload": make_json_safe(job.output_payload),
            "metadata": make_json_safe(job.metadata),
            "created_at": serialize_datetime(job.created_at),
            "updated_at": serialize_datetime(job.updated_at),
            "heartbeat_at": serialize_datetime(job.heartbeat_at),
            "started_at": serialize_datetime(job.started_at),
            "completed_at": serialize_datetime(job.completed_at),
            "day_bucket": day_bucket(job.created_at),
            "day_bucket_tz": self._settings.arango_timezone,
        }
        if collection.has(job.id):
            collection.replace(document)
        else:
            collection.insert(document)
        return job

    def _get_job_sync(self, job_id: str) -> ToolJobRecord | None:
        row = self._provider.collection(self._settings.arango_tool_job_collection).get(job_id)
        return _document_to_job(row) if row else None

    def _list_jobs_sync(self, session_id: str | None = None) -> list[ToolJobRecord]:
        bind_vars = {"@collection": self._settings.arango_tool_job_collection}
        query = """
        FOR doc IN @@collection
            SORT doc.created_at DESC
            RETURN doc
        """
        if session_id:
            bind_vars["session_id"] = session_id
            query = """
            FOR doc IN @@collection
                FILTER doc.session_id == @session_id
                SORT doc.created_at DESC
                RETURN doc
            """
        rows = self._provider.execute(query, bind_vars)
        return [_document_to_job(row) for row in rows]

    def _save_artifact_sync(self, artifact: ToolArtifactRecord) -> ToolArtifactRecord:
        collection = self._provider.collection(self._settings.arango_tool_artifact_collection)
        document = {
            "_key": artifact.id,
            "id": artifact.id,
            "tool_job_id": artifact.tool_job_id,
            "session_id": artifact.session_id,
            "turn_id": artifact.turn_id,
            "trace_id": artifact.trace_id,
            "tool_key": artifact.tool_key,
            "artifact_type": artifact.artifact_type,
            "label": artifact.label,
            "path": artifact.path,
            "content_text": str(artifact.metadata.get("__content_text") or ""),
            "storage_mode": str(artifact.metadata.get("__storage_mode") or "path_only"),
            "metadata": make_json_safe(_public_metadata(artifact.metadata)),
            "created_at": serialize_datetime(artifact.created_at),
            "day_bucket": day_bucket(artifact.created_at),
            "day_bucket_tz": self._settings.arango_timezone,
        }
        if collection.has(artifact.id):
            collection.replace(document)
        else:
            collection.insert(document)
        return artifact

    def _list_artifacts_sync(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]:
        bind_vars: dict[str, object] = {"@collection": self._settings.arango_tool_artifact_collection}
        filters: list[str] = []
        if session_id:
            bind_vars["session_id"] = session_id
            filters.append("doc.session_id == @session_id")
        if tool_job_id:
            bind_vars["tool_job_id"] = tool_job_id
            filters.append("doc.tool_job_id == @tool_job_id")
        filter_block = f"FILTER {' AND '.join(filters)}" if filters else ""
        rows = self._provider.execute(
            f"""
            FOR doc IN @@collection
                {filter_block}
                SORT doc.created_at ASC
                RETURN doc
            """,
            bind_vars,
        )
        return [_document_to_artifact(row) for row in rows]

    def _mark_stale_running_jobs_sync(self, timeout_seconds: int) -> list[ToolJobRecord]:
        threshold = datetime.utcnow() - timedelta(seconds=timeout_seconds)
        rows = self._provider.execute(
            """
            FOR doc IN @@collection
                FILTER doc.status == @status
                RETURN doc
            """,
            {"@collection": self._settings.arango_tool_job_collection, "status": ToolJobStatus.running.value},
        )
        collection = self._provider.collection(self._settings.arango_tool_job_collection)
        updated: list[ToolJobRecord] = []
        for row in rows:
            heartbeat = ensure_utc_datetime(row.get("heartbeat_at")) or ensure_utc_datetime(row.get("updated_at")) or datetime.utcnow()
            if heartbeat >= threshold:
                continue
            row["status"] = ToolJobStatus.resume_requested.value
            row["updated_at"] = serialize_datetime(datetime.utcnow())
            collection.replace(row)
            updated.append(_document_to_job(row))
        updated.sort(key=lambda item: item.updated_at, reverse=True)
        return updated


def _document_to_job(row: dict) -> ToolJobRecord:
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


def _document_to_artifact(row: dict) -> ToolArtifactRecord:
    metadata = dict(row.get("metadata") or {})
    if row.get("storage_mode"):
        metadata["storage_mode"] = row["storage_mode"]
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
