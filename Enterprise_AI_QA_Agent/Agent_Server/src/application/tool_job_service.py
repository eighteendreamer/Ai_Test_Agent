from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.runtime.tool_job_store import ToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_job import ToolArtifactRecord, ToolJobDetail, ToolJobRecord, ToolJobStatus

TEXT_ARTIFACT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".html",
    ".xml",
    ".csv",
    ".log",
    ".yml",
    ".yaml",
}
INLINE_TEXT_MAX_BYTES = 1024 * 1024


class ToolJobService:
    def __init__(self, store: ToolJobStore, heartbeat_timeout_seconds: int = 90) -> None:
        self._store = store
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds

    async def initialize(self) -> list[ToolJobRecord]:
        await self._store.initialize()
        return await self._store.mark_stale_running_jobs(self._heartbeat_timeout_seconds)

    async def create_job(
        self,
        tool: ToolDescriptor,
        call_id: str,
        session_id: str,
        turn_id: str,
        trace_id: str,
        input_payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        attempt: int = 1,
    ) -> ToolJobRecord:
        now = datetime.utcnow()
        job = ToolJobRecord(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            call_id=call_id,
            tool_key=tool.key,
            tool_name=tool.name,
            status=ToolJobStatus.queued,
            attempt=attempt,
            input_payload=input_payload,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        return await self._store.save_job(job)

    async def mark_running(self, job: ToolJobRecord) -> ToolJobRecord:
        now = datetime.utcnow()
        job.status = ToolJobStatus.running
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.updated_at = now
        return await self._store.save_job(job)

    async def heartbeat(
        self,
        job_id: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolJobRecord | None:
        job = await self._store.get_job(job_id)
        if job is None:
            return None
        job.heartbeat_at = datetime.utcnow()
        job.updated_at = job.heartbeat_at
        if summary:
            job.summary = summary
        if metadata:
            job.metadata.update(metadata)
        return await self._store.save_job(job)

    async def mark_waiting_approval(
        self,
        job_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolJobRecord | None:
        return await self._mark(job_id, ToolJobStatus.waiting_approval, summary=summary, metadata=metadata)

    async def mark_completed(
        self,
        job_id: str,
        summary: str,
        output_payload: dict[str, Any],
        artifacts: list[dict[str, Any]] | None = None,
    ) -> ToolJobRecord | None:
        job = await self._mark(job_id, ToolJobStatus.completed, summary=summary, output_payload=output_payload)
        if job is None:
            return None
        saved_artifacts = await self._save_artifacts(job, artifacts or [])
        job.artifact_count = len(saved_artifacts)
        job.completed_at = datetime.utcnow()
        job.updated_at = job.completed_at
        return await self._store.save_job(job)

    async def mark_partial(
        self,
        job_id: str,
        summary: str,
        output_payload: dict[str, Any],
        artifacts: list[dict[str, Any]] | None = None,
    ) -> ToolJobRecord | None:
        job = await self._mark(job_id, ToolJobStatus.partial, summary=summary, output_payload=output_payload)
        if job is None:
            return None
        saved_artifacts = await self._save_artifacts(job, artifacts or [])
        job.artifact_count = len(saved_artifacts)
        job.completed_at = datetime.utcnow()
        job.updated_at = job.completed_at
        return await self._store.save_job(job)

    async def mark_failed(
        self,
        job_id: str,
        summary: str,
        error_message: str,
        output_payload: dict[str, Any] | None = None,
    ) -> ToolJobRecord | None:
        return await self._mark(
            job_id,
            ToolJobStatus.failed,
            summary=summary,
            error_message=error_message,
            output_payload=output_payload or {},
        )

    async def cancel_job(self, job_id: str, reason: str | None = None) -> ToolJobRecord | None:
        return await self._mark(job_id, ToolJobStatus.cancelled, summary=reason or "Cancelled by operator.")

    async def mark_denied(
        self,
        job_id: str,
        summary: str,
        output_payload: dict[str, Any] | None = None,
    ) -> ToolJobRecord | None:
        return await self._mark(job_id, ToolJobStatus.denied, summary=summary, output_payload=output_payload or {})

    async def request_resume(self, job_id: str, reason: str | None = None) -> ToolJobRecord | None:
        return await self._mark(job_id, ToolJobStatus.resume_requested, summary=reason or "Resume requested.")

    async def request_retry(self, job_id: str, reason: str | None = None) -> ToolJobRecord | None:
        return await self._mark(job_id, ToolJobStatus.retry_requested, summary=reason or "Retry requested.")

    async def get_job_detail(self, job_id: str) -> ToolJobDetail | None:
        job = await self._store.get_job(job_id)
        if job is None:
            return None
        artifacts = await self._store.list_artifacts(tool_job_id=job_id)
        return ToolJobDetail(**job.model_dump(mode="python"), artifacts=artifacts)

    async def list_jobs(self, session_id: str | None = None) -> list[ToolJobRecord]:
        return await self._store.list_jobs(session_id=session_id)

    async def list_artifacts(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]:
        return await self._store.list_artifacts(session_id=session_id, tool_job_id=tool_job_id)

    async def _mark(
        self,
        job_id: str,
        status: ToolJobStatus,
        summary: str,
        output_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolJobRecord | None:
        job = await self._store.get_job(job_id)
        if job is None:
            return None
        job.status = status
        job.summary = summary
        job.output_payload = output_payload or job.output_payload
        job.error_message = error_message
        job.updated_at = datetime.utcnow()
        if status in {
            ToolJobStatus.completed,
            ToolJobStatus.partial,
            ToolJobStatus.failed,
            ToolJobStatus.denied,
            ToolJobStatus.cancelled,
        }:
            job.completed_at = datetime.utcnow()
        if metadata:
            job.metadata.update(metadata)
        return await self._store.save_job(job)

    async def _save_artifacts(self, job: ToolJobRecord, artifacts: list[dict[str, Any]]) -> list[ToolArtifactRecord]:
        saved: list[ToolArtifactRecord] = []
        for item in artifacts:
            path = str(item.get("path") or "").strip()
            inline_content = str(item.get("content") or "")
            if not path and not inline_content:
                continue
            metadata = {key: value for key, value in item.items() if key not in {"type", "label", "path", "content"}}
            storage_mode = "path_only"
            content_text = ""
            if inline_content:
                storage_mode = "inline_text"
                content_text = inline_content
            elif path:
                content_text = self._read_text_artifact(path)
                if content_text:
                    storage_mode = "inline_text"
            metadata["__storage_mode"] = storage_mode
            if content_text:
                metadata["__content_text"] = content_text
            artifact = ToolArtifactRecord(
                id=str(uuid4()),
                tool_job_id=job.id,
                session_id=job.session_id,
                turn_id=job.turn_id,
                trace_id=job.trace_id,
                tool_key=job.tool_key,
                artifact_type=str(item.get("type") or "file"),
                label=item.get("label"),
                path=path or "inline://artifact",
                created_at=datetime.utcnow(),
                metadata=metadata,
            )
            saved.append(await self._store.save_artifact(artifact))
        return saved

    def _read_text_artifact(self, raw_path: str) -> str:
        try:
            path = Path(raw_path)
            if not path.exists() or not path.is_file():
                return ""
            if path.suffix.lower() not in TEXT_ARTIFACT_EXTENSIONS:
                return ""
            if path.stat().st_size > INLINE_TEXT_MAX_BYTES:
                return ""
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
