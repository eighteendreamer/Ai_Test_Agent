from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4
from typing import Protocol

from src.schemas.tool_job import ToolArtifactRecord, ToolJobRecord, ToolJobStatus
from src.schemas.runtime_binding import RuntimeExecutionOwner


class ToolJobStore(Protocol):
    async def initialize(self) -> None: ...
    async def save_job(self, job: ToolJobRecord) -> ToolJobRecord: ...
    async def create_job_if_absent(self, job: ToolJobRecord) -> ToolJobRecord: ...
    async def claim_job_execution(self, job_id: str) -> ToolJobRecord | None: ...
    async def assert_framework_dispatch(self, job_id: str) -> None: ...
    async def append_framework_late_output(self, job_id: str, owner: RuntimeExecutionOwner, payload: dict, observation: dict) -> None: ...
    async def heartbeat_job_execution(self, job_id: str, owner_token: str | None = None) -> ToolJobRecord | None: ...
    async def park_child_wait(self, job_id: str, *, summary: str, metadata: dict) -> ToolJobRecord | None: ...
    async def claim_child_wait(self, job_id: str) -> ToolJobRecord | None: ...
    async def observe_job_output(self, job_id: str, *, output_payload: dict, observation: dict,
                                 metadata: dict | None = None, owner_token: str | None = None) -> ToolJobRecord | None: ...
    async def settle_job_output(self, job_id: str, *, expected_output: dict, expected_observation: dict,
                               output_payload: dict, status: ToolJobStatus, summary: str,
                               error_message: str | None, artifact_count: int,
                               owner_token: str | None = None) -> ToolJobRecord | None: ...
    async def finish_job_execution(self, job_id: str, *, status: ToolJobStatus, output_payload: dict,
                                   summary: str, error_message: str | None,
                                   owner_token: str | None = None) -> ToolJobRecord | None: ...
    async def get_job(self, job_id: str) -> ToolJobRecord | None: ...
    async def list_jobs(self, session_id: str | None = None) -> list[ToolJobRecord]: ...
    async def list_latest_jobs_for_sessions(self, session_ids: list[str], tool_keys: list[str]) -> list[ToolJobRecord]: ...
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

    async def assert_framework_dispatch(self, job_id: str) -> None:
        raise RuntimeError("Bound framework execution requires PostgreSQL owner authority")

    async def append_framework_late_output(self, job_id: str, owner: RuntimeExecutionOwner, payload: dict, observation: dict) -> None:
        raise RuntimeError("Bound framework observations require durable PostgreSQL storage")

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
            job.metadata["execution_owner_token"] = str(uuid4())
            job.started_at = job.heartbeat_at = job.updated_at = now
            return job.model_copy(deep=True)

    async def heartbeat_job_execution(self, job_id: str, owner_token: str | None = None) -> ToolJobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if (job is not None and job.status == ToolJobStatus.running
                and (owner_token is None or job.metadata.get("execution_owner_token") == owner_token)):
                job.heartbeat_at = job.updated_at = datetime.utcnow()
                return job.model_copy(deep=True)
            return None

    async def observe_job_output(self, job_id: str, *, output_payload: dict, observation: dict,
                                 metadata: dict | None = None, owner_token: str | None = None) -> ToolJobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if (job is None or job.status != ToolJobStatus.running
                or "tool_output_observation" in job.metadata
                or (owner_token is not None and job.metadata.get("execution_owner_token") != owner_token)):
                return None
            job.output_payload = output_payload
            job.metadata["tool_output_observation"] = observation
            if metadata:
                job.metadata.update(metadata)
            job.updated_at = datetime.utcnow()
            return job.model_copy(deep=True)

    async def park_child_wait(self, job_id: str, *, summary: str, metadata: dict) -> ToolJobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != ToolJobStatus.running or "tool_output_observation" in job.metadata:
                return None
            job.status, job.summary = ToolJobStatus.waiting_children, summary
            job.metadata.update(metadata)
            job.updated_at = datetime.utcnow()
            return job.model_copy(deep=True)

    async def claim_child_wait(self, job_id: str) -> ToolJobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != ToolJobStatus.waiting_children:
                return None
            job.status = ToolJobStatus.running
            job.updated_at = job.heartbeat_at = datetime.utcnow()
            return job.model_copy(deep=True)

    async def settle_job_output(self, job_id: str, *, expected_output: dict, expected_observation: dict,
                               output_payload: dict, status: ToolJobStatus, summary: str,
                               error_message: str | None, artifact_count: int,
                               owner_token: str | None = None) -> ToolJobRecord | None:
        if status not in {ToolJobStatus.completed, ToolJobStatus.partial, ToolJobStatus.failed}:
            raise ValueError("Observed output requires a terminal tool status")
        async with self._lock:
            job = self._jobs.get(job_id)
            if (job is None or job.status not in {ToolJobStatus.running, ToolJobStatus.resume_requested}
                or job.output_payload != expected_output or job.metadata.get("tool_output_observation") != expected_observation
                or (owner_token is not None and job.metadata.get("execution_owner_token") != owner_token)):
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

    async def list_latest_jobs_for_sessions(self, session_ids: list[str], tool_keys: list[str]) -> list[ToolJobRecord]:
        sessions, keys = set(session_ids), set(tool_keys)
        latest: dict[tuple[str, str], ToolJobRecord] = {}
        for job in sorted(self._jobs.values(), key=lambda item: (item.created_at, item.id), reverse=True):
            if job.session_id in sessions and job.tool_key in keys:
                latest.setdefault((job.session_id, job.tool_key), job)
        return [job.model_copy(deep=True) for job in latest.values()]

    async def finish_job_execution(self, job_id: str, *, status: ToolJobStatus, output_payload: dict,
                                   summary: str, error_message: str | None,
                                   owner_token: str | None = None) -> ToolJobRecord | None:
        if status not in {ToolJobStatus.completed, ToolJobStatus.failed, ToolJobStatus.cancelled}:
            raise ValueError("Execution settlement requires a terminal status")
        async with self._lock:
            job = self._jobs.get(job_id)
            if (job is None or not (job.status == ToolJobStatus.running or
                (job.status == status == ToolJobStatus.cancelled))
                or (owner_token is not None and job.metadata.get("execution_owner_token") != owner_token)):
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
