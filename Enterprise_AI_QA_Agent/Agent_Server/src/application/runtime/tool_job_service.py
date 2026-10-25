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
from src.schemas.tool_job import ToolArtifactRecord, ToolJobDetail, ToolJobRecord, ToolJobStatus, ToolOutputObservation, ToolNonExecutionEvidence
from src.infrastructure.storage_utils import make_json_safe
from src.application.runtime.case_action_binding import action_admission_enabled, CaseActionClaimUnavailable
from src.application.runtime.case_operation import (
    CaseOperationRecoveryRequired,
    VERIFICATION_KEY,
    save_operation_verification_observation,
)

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
    def __init__(self, store: ToolJobStore, heartbeat_timeout_seconds: int = 90, case_action_admission=None) -> None:
        self._store = store
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._case_action_admission = case_action_admission

    async def initialize(self) -> list[ToolJobRecord]:
        await self._store.initialize()
        if self._case_action_admission is not None:
            await self._case_action_admission.initialize()
        return await self._store.mark_stale_running_jobs(self._heartbeat_timeout_seconds)

    async def list_case_effect_evidence(self, *, project_id: str, run_id: str, plan_id: str) -> list[dict]:
        store = getattr(self._store, "effect_evidence", None)
        if store is None:
            raise RuntimeError("固定对象证据查询未配置；不能用空集合代替未知范围")
        records = await store.list_run_evidence(project_id, run_id, plan_id)
        output, seen_closures = [], set()
        for row in records:
            job = row["origin_job"]
            metadata = job["metadata"]
            admission = metadata.get("case_action_admission")
            closure = admission.get("consumption") if isinstance(admission, dict) else None
            entry = {"origin_job_id": job["id"], "operation_id": row["operation"]["operation_id"],
                "binding": deepcopy(metadata["case_action_binding"]), "status": job["status"],
                "evidence_state": row["evidence_state"], "returned_evidence": deepcopy(row["returned_evidence"])}
            if isinstance(closure, dict) and isinstance(closure.get("closure_sha256"), str):
                if closure["closure_sha256"] not in seen_closures:
                    entry["consumption_closure"] = deepcopy(closure)
                    seen_closures.add(closure["closure_sha256"])
            output.append(entry)
        return output

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

    async def observe_output(self, job_id: str, *, status: str, output_payload: dict,
                             non_execution: ToolNonExecutionEvidence | None = None) -> ToolJobRecord:
        payload = deepcopy(make_json_safe(output_payload))
        observation = ToolOutputObservation(status=status, output_hash=self._output_hash(payload),
            observed_at=datetime.utcnow(), non_execution=non_execution)
        verification_metadata = None
        current = await self._store.get_job(job_id)
        if current is not None and isinstance(current.metadata.get(VERIFICATION_KEY), dict):
            verification_metadata = {
                VERIFICATION_KEY: save_operation_verification_observation(
                    current.metadata[VERIFICATION_KEY], payload, job_status=status,
                )
            }
        observe_kwargs = {
            "output_payload": payload,
            "observation": observation.model_dump(mode="json"),
        }
        owner_token = str((current.metadata if current else {}).get("execution_owner_token") or "") or None
        if owner_token is not None:
            observe_kwargs["owner_token"] = owner_token
        if verification_metadata is not None:
            observe_kwargs["metadata"] = verification_metadata
        try:
            saved = await self._store.observe_job_output(job_id, **observe_kwargs)
        except Exception as exc:
            # A lost commit acknowledgement is uncertain; query this identity
            # rather than replace its output or call the tool again.
            logger.warning("tool_output_observation_acknowledgement_uncertain", extra={
                "tool_job_id": job_id, "error_type": type(exc).__name__})
            saved = None
        if saved is None:
            saved = await self._store.get_job(job_id)
            if (saved is None or OUTPUT_OBSERVATION_KEY not in saved.metadata
                or self._output_observation(saved).output_hash != observation.output_hash
                or self._output_observation(saved).non_execution != observation.non_execution):
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
            status=status, summary=summary, error_message=error_message, artifact_count=count,
            owner_token=str(job.metadata.get("execution_owner_token") or "") or None)
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

    async def mark_waiting_children(self, job_id: str, *, summary: str, metadata: dict[str, Any]) -> ToolJobRecord | None:
        return await self._store.park_child_wait(job_id, summary=summary, metadata=metadata)

    async def claim_child_wait(self, job_id: str) -> ToolJobRecord | None:
        return await self._store.claim_child_wait(job_id)

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

    async def claim_execution(self, job_id: str, *, context_bundle=None) -> ToolJobRecord | None:
        """Use the Store's atomic queued/waiting-to-running transition."""
        if action_admission_enabled(context_bundle or {}):
            if self._case_action_admission is None:
                raise CaseActionClaimUnavailable("固定动作确认策略尚未配置PG领取服务，不能发送")
            claimed = await self._case_action_admission.claim(job_id, context_bundle)
            if isinstance(claimed,CaseOperationRecoveryRequired):
                await self.recover_observed_output(claimed.origin_job_id,
                    reason="原逻辑操作可靠返回已保存，恢复结果交付而不重新发送")
                claimed = await self._case_action_admission.claim(job_id,context_bundle)
                if isinstance(claimed,CaseOperationRecoveryRequired):
                    raise CaseActionClaimUnavailable("原逻辑操作可靠返回恢复尚未确认；保留原记录并先查证")
            if claimed is None:
                # Identity was checked by the PG domain claim. A durable returned
                # result precedes any newer applicability decision; recover delivery only.
                latest = await self._store.get_job(job_id)
                if latest is not None and latest.status in {ToolJobStatus.running, ToolJobStatus.resume_requested}:
                    return await self.recover_observed_output(job_id, reason="原动作返回已保存，恢复未完成的结果交付")
                # Preserve the existing claim contract for terminal jobs: None
                # lets callers distinguish replay from a newly denied claim.
                return None
            return claimed
        return await self._store.claim_job_execution(job_id)

    async def validate_case_operation(self,job):
        if self._case_action_admission is None:
            raise ValueError("逻辑操作证据核对缺少原PG服务")
        return await self._case_action_admission.operations.validate_job(job)

    async def finish_execution(self, job_id: str, *, status: ToolJobStatus, output_payload: dict[str, Any],
                               summary: str, error_message: str | None = None,
                               owner_token: str | None = None) -> ToolJobRecord | None:
        """Settle atomically without overwriting a concurrent cancellation/recovery."""
        return await self._store.finish_job_execution(job_id, status=status, output_payload=output_payload,
                                                     summary=summary, error_message=error_message,
                                                     owner_token=owner_token)

    async def heartbeat_execution(self, job_id: str, owner_token: str | None = None) -> ToolJobRecord | None:
        return await self._store.heartbeat_job_execution(job_id, owner_token)

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

    async def list_latest_jobs_for_sessions(self, session_ids: list[str], tool_keys: list[str]) -> list[ToolJobRecord]:
        return await self._store.list_latest_jobs_for_sessions(session_ids, tool_keys)

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
