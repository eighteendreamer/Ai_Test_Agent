"""Persist checks of saved responses without dispatching or publishing analysis."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from src.application.documents.impact_response_protocol import DocumentImpactResponseProtocol
from src.application.documents.impact_checkpoint_io import confirmed_impact_write, store_impact_json
from src.schemas.document_impact_responses import DocumentImpactResponseAssessment, DocumentImpactResponseCheckpoint
from src.runtime.store import ContinuationLeaseLostError

logger = logging.getLogger(__name__)


class DocumentImpactResponseService:
    def __init__(self, *, calls, store, objects):
        self._calls, self._store, self._objects = calls, store, objects

    async def read(self, project_id, document_id, task_id, call_id):
        checkpoint = await self._store.responses.get(project_id, document_id, task_id, call_id)
        if checkpoint is None:
            raise KeyError("当前项目文档没有该调用的响应核验记录")
        call, _ = await self._calls.read_response(project_id, document_id, task_id, call_id)
        manifest = await self._store.get_manifest_checkpoint(project_id, document_id, task_id)
        if (call.phase != "response_saved" or checkpoint.task_id != task_id or checkpoint.call_id != call.id or
            checkpoint.request_sha256 != call.request.sha256 or checkpoint.response_sha256 != call.response.sha256 or
            manifest is None or checkpoint.manifest_sha256 != manifest.sha256):
            raise ValueError("影响响应核验检查点不能替换原调用")
        raw = (await self._objects.read_object_uri(checkpoint.result.storage_uri))["content"]
        if len(raw) != checkpoint.result.size_bytes or hashlib.sha256(raw).hexdigest() != checkpoint.result.sha256:
            raise ValueError("影响响应核验对象大小或哈希不符")
        result = await asyncio.to_thread(DocumentImpactResponseAssessment.model_validate_json, raw)
        if any(getattr(result, key) != getattr(checkpoint, key) for key in (
            "validator_version", "task_id", "call_id", "request_sha256", "response_sha256", "manifest_sha256")):
            raise ValueError("影响响应核验正文与检查点身份不一致")
        return checkpoint, result

    async def _discard_unused(self, task_id, reference):
        try:
            if await self._store.responses.object_is_referenced(reference.storage_uri):
                return
            await self._objects.delete_object_uri(reference.storage_uri)
            logger.info("document_impact_response_unused_check_removed", extra={"task_id": task_id,
                "result_sha256": reference.sha256})
        except Exception as exc:
            logger.error("document_impact_response_check_cleanup_unconfirmed", extra={"task_id": task_id,
                "result_sha256": reference.sha256, "error_type": type(exc).__name__})

    async def assess(self, claim, call_id):
        prior = await self._store.responses.get(claim.task.project_id, claim.task.document_id, claim.task.id, call_id)
        if prior is not None:
            return await self.read(claim.task.project_id, claim.task.document_id, claim.task.id, call_id)
        task, envelope, checkpoint, manifest = await self._calls.read_fixed_scope(claim)
        remaining = (task.deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise TimeoutError("影响任务原预算已耗尽，尚未核验的响应仍保留")
        async with asyncio.timeout(remaining):
            record, payload = await self._calls.read_request(task.project_id, task.document_id, task.id, call_id)
            _, response = await self._calls.read_response(task.project_id, task.document_id, task.id, call_id)
            result = await asyncio.to_thread(DocumentImpactResponseProtocol.assess,
                claim.seed, envelope, manifest, record, payload, response)
            reference = await confirmed_impact_write(store_impact_json(self._objects, claim, result, "response-check.json"),
                task_id=task.id, stage="response_check_object")
        saved = DocumentImpactResponseCheckpoint(task_id=task.id, call_id=call_id, request_sha256=record.request.sha256,
            response_sha256=record.response.sha256, manifest_sha256=checkpoint.sha256,
            result=reference, created_at=datetime.now(timezone.utc))
        try:
            recorded = await confirmed_impact_write(self._store.responses.save(claim, saved),
                task_id=task.id, stage="response_check_checkpoint")
        except (ValueError, KeyError, TimeoutError, ContinuationLeaseLostError):
            await self._discard_unused(task.id, reference)
            raise
        # Unconfirmed writes retain the object, propagate and never grant a model retry.
        if recorded.result != reference:
            await self._discard_unused(task.id, reference)
        logger.info("document_impact_response_assessed", extra={"task_id": task.id, "call_id": call_id,
            "status": result.status, "issue_count": len(result.issues), "semantic_status": result.semantic_status})
        return await self.read(task.project_id, task.document_id, task.id, call_id)
