"""Fixed impact submissions, persistent input and analysis scope preparation.

This stage leaves tasks running at input_saved or analyzing (scope ready).
Model invocation and final results are not inferred from successful object reads.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.impact_sources import canonical_impact_json
from src.application.documents.impact_analysis_context import DocumentImpactAnalysisContexts
from src.application.documents.impact_batches import DocumentImpactBatches
from src.application.documents.impact_response_protocol import capture_impact_response_protocol
from src.schemas.document_impact import DocumentImpactInputCheckpoint, DocumentImpactInputEnvelope
from src.schemas.document_impact_batches import DocumentImpactBatchManifest, DocumentImpactManifestCheckpoint

logger = logging.getLogger(__name__)


class DocumentImpactTaskService:
    def __init__(self, *, store, sources, documents, projects, objects, stream, analysis_contexts=None):
        self._store, self._sources, self._docs = store, sources, documents
        self._projects, self._objects, self._stream = projects, objects, stream
        self._analysis_contexts = analysis_contexts

    async def initialize(self):
        await self._store.initialize()

    async def _require_document(self, project_id, document_id):
        await self._projects.get(project_id)
        document = await self._docs.get_document(document_id)
        if document.project_id != project_id:
            raise KeyError("文档不属于当前项目")

    async def submit(self, project_id, document_id, request):
        await self._require_document(project_id, document_id)
        digest = self._store.request_hash(request)
        prior = await self._store.get_by_request(project_id, document_id, request.creation_key, request_hash=digest)
        if prior:
            logger.info("document_impact_submission_replayed", extra={"project_id": project_id, "task_id": prior.id})
            return prior
        if not self._stream:
            raise RuntimeError("持久影响分析的派发流尚未启用")
        project = await self._projects.require_active(project_id)
        if self._analysis_contexts is None:
            logger.warning("document_impact_submission_unconfigured", extra={"project_id": project_id,
                "document_id": document_id})
            raise RuntimeError("影响分析模型依据服务尚未注入，不能提交新任务")
        try:
            context = await self._analysis_contexts.capture(project, request)
            batch_settings = await self._analysis_contexts.capture_batch_settings(project.id)
            invocation_snapshot = await self._analysis_contexts.capture_invocation_snapshot(project.id, context.model.key)
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            logger.warning("document_impact_analysis_context_rejected", extra={"project_id": project_id,
                "document_id": document_id, "error_type": type(exc).__name__})
            raise
        seed = await self._sources.capture(project_id, document_id, request.difference_snapshot_id, request)
        seed.analysis_context = context
        seed.batch_settings = batch_settings
        seed.invocation_snapshot = invocation_snapshot
        seed.response_protocol = capture_impact_response_protocol()
        logger.info("document_impact_response_protocol_captured", extra={"project_id": project_id,
            "protocol_version": seed.response_protocol.version, "protocol_sha256": seed.response_protocol.sha256})
        return await self._store.submit(seed, creation_key=request.creation_key, request_hash=digest, stream=self._stream)

    async def get(self, project_id, document_id, task_id):
        await self._require_document(project_id, document_id)
        task = await self._store.get(project_id, task_id)
        if task is None or task.document_id != document_id:
            raise KeyError("当前文档没有该影响任务")
        return task

    async def get_by_request(self, project_id, document_id, creation_key):
        await self._require_document(project_id, document_id)
        task = await self._store.get_by_request(project_id, document_id, creation_key)
        if task is None:
            raise KeyError("当前文档没有该影响提交")
        return task

    async def list_tasks(self, project_id, document_id, *, version_id=None, limit=20, offset=0):
        await self._require_document(project_id, document_id)
        if version_id:
            await self._docs.get_version(document_id, version_id)
        return await self._store.list_tasks(project_id, document_id, version_id=version_id, limit=limit, offset=offset)

    async def read_input(self, project_id, document_id, task_id):
        task = await self.get(project_id, document_id, task_id)
        checkpoint = task.input_checkpoint
        if checkpoint is None:
            raise ValueError("该影响任务尚未保存固定输入")
        seed = await self._store.get_seed(project_id, document_id, task_id)
        if seed is None:
            raise KeyError("当前文档没有该影响提交依据")
        envelope = await self._read_input(seed, checkpoint)
        return checkpoint, envelope

    async def prepare_analysis_request(self, project_id, document_id, task_id):
        """Read saved evidence and build a request, without model dispatch."""
        _, envelope = await self.read_input(project_id, document_id, task_id)
        seed = await self._store.get_seed(project_id, document_id, task_id)
        request = DocumentImpactAnalysisContexts.build_request(seed, envelope)
        logger.info("document_impact_analysis_request_prepared", extra={"project_id": project_id,
            "task_id": task_id, "input_sha256": envelope.input_sha256,
            "prompt_sha256": seed.analysis_context.prompt.sha256})
        return seed.analysis_context, request

    async def _read_input(self, seed, checkpoint):
        raw = (await self._objects.read_object_uri(checkpoint.storage_uri))["content"]
        if len(raw) != checkpoint.size_bytes or hashlib.sha256(raw).hexdigest() != checkpoint.sha256:
            raise ValueError("影响输入对象大小或哈希不一致")
        envelope = await asyncio.to_thread(DocumentImpactInputEnvelope.model_validate_json, raw)
        if checkpoint.input_sha256 != envelope.input_sha256:
            raise ValueError("影响输入内容哈希与检查点不同")
        self._sources.validate_fixed_input(seed, envelope)
        return envelope

    async def read_manifest(self, project_id, document_id, task_id):
        await self.get(project_id, document_id, task_id)
        checkpoint = await self._store.get_manifest_checkpoint(project_id, document_id, task_id)
        if checkpoint is None:
            raise ValueError("该影响任务尚未保存批次清单")
        _, envelope = await self.read_input(project_id, document_id, task_id)
        seed = await self._store.get_seed(project_id, document_id, task_id)
        if checkpoint.task_id != task_id or checkpoint.input_sha256 != envelope.input_sha256:
            raise ValueError("影响批次检查点与固定输入不同")
        raw = (await self._objects.read_object_uri(checkpoint.storage_uri))["content"]
        if len(raw) != checkpoint.size_bytes or hashlib.sha256(raw).hexdigest() != checkpoint.sha256:
            raise ValueError("影响批次对象大小或哈希不一致")
        manifest = await asyncio.to_thread(DocumentImpactBatchManifest.model_validate_json, raw)
        await asyncio.to_thread(DocumentImpactBatches.validate, seed, envelope, manifest)
        return checkpoint, manifest

    async def prepare_manifest(self, claim):
        """Persist the whole declared scope, without invoking or completing analysis."""
        prior = await self._store.get_manifest_checkpoint(claim.task.project_id, claim.task.document_id, claim.task.id)
        if prior is not None:
            await self.read_manifest(claim.task.project_id, claim.task.document_id, claim.task.id)
            return prior
        if claim.seed.batch_settings is None:
            raise ValueError("历史影响任务未冻结分批策略，请创建新任务")
        # Read durable state: the original claim may predate prepare_input.
        task = await self.get(claim.task.project_id, claim.task.document_id, claim.task.id)
        remaining = min(claim.remaining_seconds, (task.deadline_at - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            logger.warning("document_impact_manifest_deadline", extra={"task_id": task.id})
            raise TimeoutError("影响任务原预算已耗尽，不能建立批次清单")
        async with asyncio.timeout(remaining):
            input_checkpoint, envelope = await self.read_input(claim.task.project_id, claim.task.document_id, claim.task.id)
            manifest = await asyncio.to_thread(DocumentImpactBatches.build, claim.seed, envelope)
            encoded = await asyncio.to_thread(canonical_impact_json, manifest)
            saved = await self._objects.store_uploaded_bytes(content=encoded, filename="manifest.json",
                content_type="application/json", object_prefix=f"document-impacts/{claim.task.project_id}/"
                f"{claim.task.id}/{claim.attempt_id}/manifest/{uuid4()}")
        checkpoint = DocumentImpactManifestCheckpoint(task_id=claim.task.id,
            input_sha256=input_checkpoint.input_sha256, storage_uri=saved["uri"],
            sha256=hashlib.sha256(encoded).hexdigest(), size_bytes=len(encoded), created_at=datetime.now(timezone.utc))
        # An unconfirmed PG write must propagate; never delete or overwrite its object on an assumption.
        try:
            return await self._store.save_manifest_checkpoint(claim, checkpoint)
        except Exception as exc:
            logger.warning("document_impact_manifest_commit_unconfirmed", extra={"task_id": claim.task.id,
                "attempt_id": claim.attempt_id, "manifest_sha256": checkpoint.sha256, "error_type": type(exc).__name__})
            raise

    async def prepare_input(self, claim):
        """Save exact content; do not mark AI analysis completed or invoke models."""
        await self._require_document(claim.task.project_id, claim.task.document_id)
        if claim.task.input_checkpoint is not None:
            await self._read_input(claim.seed, claim.task.input_checkpoint)
            return claim.task
        if claim.remaining_seconds <= 0:
            return await self._store.finish_failed(claim, error_code="document_impact_deadline",
                message="影响任务原输入准备预算已耗尽")
        phase = "reading_input"
        try:
            async with asyncio.timeout(claim.remaining_seconds):
                envelope = await self._sources.prepare_fixed(claim.seed, remaining_seconds=claim.remaining_seconds)
                self._sources.validate_fixed_input(claim.seed, envelope)
                encoded = await asyncio.to_thread(canonical_impact_json, envelope)
                phase = "saving_input"
                saved = await self._objects.store_uploaded_bytes(content=encoded, filename="input.json",
                    content_type="application/json", object_prefix=f"document-impacts/{claim.task.project_id}/"
                    f"{claim.task.id}/{claim.attempt_id}/input/{uuid4()}")
        except TimeoutError:
            logger.warning("document_impact_input_deadline", extra={"task_id": claim.task.id,
                "attempt_id": claim.attempt_id, "phase": phase})
            return await self._store.finish_failed(claim, error_code="document_impact_deadline", message="固定影响输入准备超时")
        except (ValueError, KeyError) as exc:
            logger.error("document_impact_fixed_input_rejected", extra={"task_id": claim.task.id,
                "attempt_id": claim.attempt_id, "phase": phase, "error_type": type(exc).__name__})
            return await self._store.finish_failed(claim, error_code="document_impact_input_invalid",
                message="固定输入核验失败，未进行AI分析")
        checkpoint = DocumentImpactInputCheckpoint(task_id=claim.task.id, storage_uri=saved["uri"],
            sha256=hashlib.sha256(encoded).hexdigest(), size_bytes=len(encoded), input_sha256=envelope.input_sha256,
            created_at=datetime.now(timezone.utc))
        # Unknown PG commit retains the object. Recovery reads the durable
        # checkpoint, rather than interpreting a missing HTTP response as rollback.
        return await self._store.save_input_checkpoint(claim, checkpoint)
