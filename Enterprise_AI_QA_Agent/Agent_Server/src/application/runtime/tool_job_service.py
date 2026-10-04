from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.runtime.tool_job_store import ToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_job import ToolArtifactRecord, ToolJobDetail, ToolJobRecord, ToolJobStatus, ToolOutputObservation
from src.infrastructure.storage_utils import make_json_safe

logger = logging.getLogger(__name__)
OUTPUT_OBSERVATION_KEY = "tool_output_observation"

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
        once_per_call: bool = False,
    ) -> ToolJobRecord:
        job = self.build_job(
            tool=tool, call_id=call_id, session_id=session_id, turn_id=turn_id,
            trace_id=trace_id, input_payload=input_payload, metadata=metadata,
            attempt=attempt, once_per_call=once_per_call,
        )
        if once_per_call:
            return await self._store.create_job_if_absent(job)
        return await self._store.save_job(job)

    def build_job(
        self, tool: ToolDescriptor, call_id: str, session_id: str, turn_id: str,
        trace_id: str, input_payload: dict[str, Any], metadata: dict[str, Any] | None = None,
        attempt: int = 1, once_per_call: bool = False,
    ) -> ToolJobRecord:
        """Prepare a record without publishing it before its owning transaction."""
        now = datetime.utcnow()
        job = ToolJobRecord(
            id=(
                str(uuid5(NAMESPACE_URL, json.dumps(["deepagents-tool", session_id, turn_id, call_id])))
                if once_per_call else str(uuid4())
            ),
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
        return job

    def supports_approval_transaction(self, settings) -> bool:
        return (
            callable(getattr(self._store, "save_job_in_transaction", None))
            and getattr(self._store, "_settings", None) == settings
        )

    def save_job_in_transaction(self, cur, job: ToolJobRecord) -> ToolJobRecord:
        return self._store.save_job_in_transaction(cur, job)

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
        if not summary and not metadata:
            # The runtime's ordinary heartbeat must not overwrite output or
            # resurrect a terminal job with an earlier whole-row snapshot.
            return await self._store.heartbeat_job_execution(job_id)
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

    @staticmethod
    def _output_hash(output: dict) -> str:
        raw = json.dumps(make_json_safe(output), ensure_ascii=False, sort_keys=True,
            allow_nan=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _output_observation(self, job: ToolJobRecord) -> ToolOutputObservation:
        observation = ToolOutputObservation.model_validate(job.metadata.get(OUTPUT_OBSERVATION_KEY))
        if observation.output_hash != self._output_hash(job.output_payload):
            raise ValueError(f"ToolJob {job.id} output no longer matches its returned-output checkpoint")
        return observation

    async def observe_output(self, job_id: str, *, status: str, output_payload: dict) -> ToolJobRecord:
        payload = deepcopy(make_json_safe(output_payload))
        observation = ToolOutputObservation(status=status, output_hash=self._output_hash(payload), observed_at=datetime.utcnow())
        try:
            saved = await self._store.observe_job_output(job_id, output_payload=payload,
                observation=observation.model_dump(mode="json"))
        except Exception as exc:
            # A lost commit acknowledgement is uncertain; query this identity
            # rather than replace its output or call the tool again.
            logger.warning("tool_output_observation_acknowledgement_uncertain", extra={
                "tool_job_id": job_id, "error_type": type(exc).__name__})
            saved = None
        if saved is None:
            saved = await self._store.get_job(job_id)
            if (saved is None or OUTPUT_OBSERVATION_KEY not in saved.metadata
                or self._output_observation(saved).output_hash != observation.output_hash):
                raise RuntimeError(f"ToolJob {job_id} returned output could not be confirmed in storage")
        logger.info("tool_output_observation_saved", extra={"tool_job_id": job_id,
            "tool_key": saved.tool_key, "run_item_id": saved.metadata.get("run_item_id"),
            "operation_status": observation.status})
        return saved

    async def finish_output_processing(self, job_id: str, *, status: ToolJobStatus, output_payload: dict,
                                       summary: str, artifacts: list[dict] | None = None) -> ToolJobRecord:
        job = await self._store.get_job(job_id)
        if job is None:
            raise ValueError(f"ToolJob {job_id} disappeared during output processing")
        if job.status not in {ToolJobStatus.running, ToolJobStatus.resume_requested}:
            return job
        self._output_observation(job)
        await self._save_artifacts(job, artifacts or [])
        return await self._settle_observed_output(job, status=status, output_payload=output_payload,
            summary=summary, error_message=str(output_payload.get("error") or summary) if status == ToolJobStatus.failed else None)

    async def recover_observed_output(self, job_id: str, *, reason: str) -> ToolJobRecord | None:
        job = await self._store.get_job(job_id)
        if job is None or job.status not in {ToolJobStatus.running, ToolJobStatus.resume_requested}:
            return job
        if OUTPUT_OBSERVATION_KEY not in job.metadata:
            return None  # No returned result: effects still require actual reconciliation.
        observation = self._output_observation(job)
        output = deepcopy(job.output_payload)
        output["output_delivery"] = {"status": "interrupted", "reason": reason,
            "operation_status": observation.status}
        status = ToolJobStatus.failed if observation.status == "failed" else ToolJobStatus.partial
        saved = await self._settle_observed_output(job, status=status, output_payload=output,
            summary=str(output.get("summary") or job.summary) + "；工具响应已保留，后续结果交付未完成。",
            error_message=reason)
        logger.warning("tool_output_observation_recovered", extra={"tool_job_id": job_id,
            "tool_key": saved.tool_key, "run_item_id": saved.metadata.get("run_item_id"), "status": saved.status.value})
        return saved

    async def _settle_observed_output(self, job, *, status, output_payload, summary, error_message):
        count = len(await self._store.list_artifacts(tool_job_id=job.id))
        saved = await self._store.settle_job_output(job.id, expected_output=job.output_payload,
            expected_observation=job.metadata[OUTPUT_OBSERVATION_KEY], output_payload=make_json_safe(output_payload),
            status=status, summary=summary, error_message=error_message, artifact_count=count)
        if saved is None:
            saved = await self._store.get_job(job.id)
            if saved is None or saved.status in {ToolJobStatus.running, ToolJobStatus.resume_requested}:
                raise RuntimeError(f"ToolJob {job.id} changed while its observed output was being settled")
            logger.warning("tool_output_settlement_existing_terminal_preserved", extra={
                "tool_job_id": job.id, "status": saved.status.value})
        return saved

    async def mark_waiting_approval(
        self,
        job_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolJobRecord | None:
        return await self._mark(job_id, ToolJobStatus.waiting_approval, summary=summary, metadata=metadata)

    async def mark_waiting_resource(
        self, job_id: str, *, summary: str, metadata: dict[str, Any] | None = None,
    ) -> ToolJobRecord:
        return await self._mark(job_id, ToolJobStatus.waiting_resource, summary=summary, metadata=metadata)

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
        artifacts: list[dict[str, Any]] | None = None,
    ) -> ToolJobRecord | None:
        job = await self._mark(
            job_id,
            ToolJobStatus.failed,
            summary=summary,
            error_message=error_message,
            output_payload=output_payload or {},
        )
        if job is None:
            return None
        saved_artifacts = await self._save_artifacts(job, artifacts or [])
        job.artifact_count = len(saved_artifacts)
        job.completed_at = datetime.utcnow()
        job.updated_at = job.completed_at
        return await self._store.save_job(job)

    async def claim_execution(self, job_id: str) -> ToolJobRecord | None:
        """Use the Store's atomic queued/waiting-to-running transition."""
        return await self._store.claim_job_execution(job_id)

    async def finish_execution(self, job_id: str, *, status: ToolJobStatus, output_payload: dict[str, Any],
                               summary: str, error_message: str | None = None) -> ToolJobRecord | None:
        """Settle atomically without overwriting a concurrent cancellation/recovery."""
        return await self._store.finish_job_execution(job_id, status=status, output_payload=output_payload,
                                                     summary=summary, error_message=error_message)

    async def heartbeat_execution(self, job_id: str) -> ToolJobRecord | None:
        return await self._store.heartbeat_job_execution(job_id)

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

    async def get_job(self, job_id: str) -> ToolJobRecord | None:
        return await self._store.get_job(job_id)

    async def list_jobs(self, session_id: str | None = None) -> list[ToolJobRecord]:
        return await self._store.list_jobs(session_id=session_id)

    async def list_artifacts(
        self,
        session_id: str | None = None,
        tool_job_id: str | None = None,
    ) -> list[ToolArtifactRecord]:
        return await self._store.list_artifacts(session_id=session_id, tool_job_id=tool_job_id)

    async def get_artifact(self, artifact_id: str) -> ToolArtifactRecord | None:
        return await self._store.get_artifact(artifact_id)

    async def list_artifacts_for_sessions(
        self,
        session_ids: list[str],
    ) -> list[ToolArtifactRecord]:
        return await self._store.list_artifacts_for_sessions(session_ids)

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
