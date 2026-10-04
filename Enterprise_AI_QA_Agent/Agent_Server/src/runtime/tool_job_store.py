from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Protocol

from src.schemas.tool_job import ToolArtifactRecord, ToolJobRecord, ToolJobStatus


class ToolJobStore(Protocol):
    async def initialize(self) -> None: ...
    async def save_job(self, job: ToolJobRecord) -> ToolJobRecord: ...
    async def create_job_if_absent(self, job: ToolJobRecord) -> ToolJobRecord: ...
    async def claim_job_execution(self, job_id: str) -> ToolJobRecord | None: ...
    async def heartbeat_job_execution(self, job_id: str) -> ToolJobRecord | None: ...
    async def observe_job_output(self, job_id: str, *, output_payload: dict, observation: dict) -> ToolJobRecord | None: ...
    async def settle_job_output(self, job_id: str, *, expected_output: dict, expected_observation: dict,
                               output_payload: dict, status: ToolJobStatus, summary: str,
                               error_message: str | None, artifact_count: int) -> ToolJobRecord | None: ...
    async def finish_job_execution(self, job_id: str, *, status: ToolJobStatus, output_payload: dict,
                                   summary: str, error_message: str | None) -> ToolJobRecord | None: ...
    async def get_job(self, job_id: str) -> ToolJobRecord | None: ...
    async def list_jobs(self, session_id: str | None = None) -> list[ToolJobRecord]: ...
    async def save_artifact(self, artifact: ToolArtifactRecord) -> ToolArtifactRecord: ...
    async def get_artifact(self, artifact_id: str) -> ToolArtifactRecord | None: ...
    async def list_artifacts(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]: ...
    async def list_artifacts_for_sessions(
        self,
        session_ids: list[str],
    ) -> list[ToolArtifactRecord]: ...
    async def mark_stale_running_jobs(self, timeout_seconds: int, *, job_ids: list[str] | None = None) -> list[ToolJobRecord]: ...


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

    async def create_job_if_absent(self, job: ToolJobRecord) -> ToolJobRecord:
        async with self._lock:
            stored = self._jobs.setdefault(job.id, job.model_copy(deep=True))
            return stored.model_copy(deep=True)

    async def claim_job_execution(self, job_id: str) -> ToolJobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.started_at is not None or job.status not in {
                ToolJobStatus.queued, ToolJobStatus.waiting_approval,
            }:
                return None
            now = datetime.utcnow()
            job.status = ToolJobStatus.running
            job.started_at = job.heartbeat_at = job.updated_at = now
            return job.model_copy(deep=True)

    async def heartbeat_job_execution(self, job_id: str) -> ToolJobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == ToolJobStatus.running:
                job.heartbeat_at = job.updated_at = datetime.utcnow()
                return job.model_copy(deep=True)
            return None

    async def observe_job_output(self, job_id: str, *, output_payload: dict, observation: dict) -> ToolJobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != ToolJobStatus.running or "tool_output_observation" in job.metadata:
                return None
            job.output_payload = output_payload
            job.metadata["tool_output_observation"] = observation
            job.updated_at = datetime.utcnow()
            return job.model_copy(deep=True)

    async def settle_job_output(self, job_id: str, *, expected_output: dict, expected_observation: dict,
                               output_payload: dict, status: ToolJobStatus, summary: str,
                               error_message: str | None, artifact_count: int) -> ToolJobRecord | None:
        if status not in {ToolJobStatus.completed, ToolJobStatus.partial, ToolJobStatus.failed}:
            raise ValueError("Observed output requires a terminal tool status")
        async with self._lock:
            job = self._jobs.get(job_id)
            if (job is None or job.status not in {ToolJobStatus.running, ToolJobStatus.resume_requested}
                or job.output_payload != expected_output or job.metadata.get("tool_output_observation") != expected_observation):
                return None
            job.status, job.output_payload, job.summary, job.error_message = status, output_payload, summary, error_message
            job.artifact_count = artifact_count
            job.completed_at = job.updated_at = datetime.utcnow()
            return job.model_copy(deep=True)

    async def list_jobs(self, session_id: str | None = None) -> list[ToolJobRecord]:
        values = list(self._jobs.values())
        if session_id:
            values = [job for job in values if job.session_id == session_id]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    async def finish_job_execution(self, job_id: str, *, status: ToolJobStatus, output_payload: dict,
                                   summary: str, error_message: str | None) -> ToolJobRecord | None:
        if status not in {ToolJobStatus.completed, ToolJobStatus.failed, ToolJobStatus.cancelled}:
            raise ValueError("Execution settlement requires a terminal status")
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or not (job.status == ToolJobStatus.running or
                (job.status == status == ToolJobStatus.cancelled)):
                return None
            job.status, job.output_payload, job.summary, job.error_message = status, output_payload, summary, error_message
            job.completed_at = job.updated_at = datetime.utcnow()
            return job.model_copy(deep=True)

    async def save_artifact(self, artifact: ToolArtifactRecord) -> ToolArtifactRecord:
        async with self._lock:
            self._artifacts[artifact.id] = artifact
            return artifact

    async def get_artifact(self, artifact_id: str) -> ToolArtifactRecord | None:
        return self._artifacts.get(artifact_id)

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

    async def list_artifacts_for_sessions(
        self,
        session_ids: list[str],
    ) -> list[ToolArtifactRecord]:
        normalized_ids = {str(item).strip() for item in session_ids if str(item).strip()}
        if not normalized_ids:
            return []
        values = [
            item
            for item in self._artifacts.values()
            if item.session_id in normalized_ids
        ]
        return sorted(values, key=lambda item: item.created_at)

    async def mark_stale_running_jobs(self, timeout_seconds: int, *, job_ids: list[str] | None = None) -> list[ToolJobRecord]:
        threshold = datetime.utcnow() - timedelta(seconds=timeout_seconds)
        updated: list[ToolJobRecord] = []
        for job in self._jobs.values():
            if job_ids is not None and job.id not in job_ids:
                continue
            if job.status == ToolJobStatus.running and (job.heartbeat_at or job.updated_at) < threshold:
                job.status = ToolJobStatus.resume_requested
                job.updated_at = datetime.utcnow()
                updated.append(job)
        return updated
