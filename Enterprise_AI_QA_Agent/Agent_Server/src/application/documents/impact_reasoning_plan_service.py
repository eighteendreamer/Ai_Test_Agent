"""Freeze task ownership before inference; successful preparation is not analysis."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from src.application.documents.impact_checkpoint_io import confirmed_impact_write, store_impact_json
from src.application.documents.impact_reasoning_plan import DocumentImpactReasoningPlans
from src.application.documents.impact_sources import impact_record_hash
from src.schemas.document_impact_reasoning import DocumentImpactReasoningCheckpoint, DocumentImpactReasoningPlan

logger = logging.getLogger(__name__)


class DocumentImpactReasoningPlanService:
    def __init__(self, *, tasks, store, objects):
        self._tasks, self._store, self._objects = tasks, store, objects

    async def _fixed(self, project_id, document_id, task_id):
        seed = await self._store.get_seed(project_id, document_id, task_id)
        if seed is None or seed.reasoning_settings is None:
            raise ValueError("该任务没有冻结推理策略，请创建新任务；原输入和结果仍可读")
        _, envelope = await self._tasks.read_input(project_id, document_id, task_id)
        manifest_checkpoint, manifest = await self._tasks.read_manifest(project_id, document_id, task_id)
        return seed, envelope, manifest_checkpoint, manifest

    async def read(self, project_id, document_id, task_id):
        await self._tasks.get(project_id, document_id, task_id)
        checkpoint = await self._store.reasoning_plans.get(project_id, document_id, task_id)
        if checkpoint is None:
            raise KeyError("该影响任务尚未保存推理计划")
        seed, envelope, manifest_checkpoint, manifest = await self._fixed(project_id, document_id, task_id)
        if (checkpoint.input_sha256 != envelope.input_sha256 or checkpoint.manifest_sha256 != manifest_checkpoint.sha256 or
            checkpoint.config_sha256 != impact_record_hash(seed.reasoning_settings)):
            raise ValueError("推理计划检查点与固定输入、证据清单或策略不同")
        raw = (await self._objects.read_object_uri(checkpoint.storage_uri))["content"]
        if len(raw) != checkpoint.size_bytes or hashlib.sha256(raw).hexdigest() != checkpoint.sha256:
            raise ValueError("推理计划对象大小或哈希不一致")
        plan = await asyncio.to_thread(DocumentImpactReasoningPlan.model_validate_json, raw)
        expected = await asyncio.to_thread(DocumentImpactReasoningPlans.build,
            seed, envelope, manifest, manifest_checkpoint.sha256)
        if plan != expected:
            raise ValueError("推理计划不能替换完整固定范围、分组、来源或策略")
        return checkpoint, plan

    async def prepare(self, claim):
        project, doc, task_id = claim.task.project_id, claim.task.document_id, claim.task.id
        prior = await self._store.reasoning_plans.get(project, doc, task_id)
        if prior is not None:
            return await self.read(project, doc, task_id)
        task = await self._tasks.get(project, doc, task_id)
        remaining = min(claim.remaining_seconds, (task.deadline_at - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            logger.warning("document_impact_reasoning_plan_deadline", extra={"task_id": task_id})
            raise TimeoutError("原影响任务预算已耗尽，不能创建推理计划")
        async with asyncio.timeout(remaining):
            seed, envelope, manifest_checkpoint, manifest = await self._fixed(project, doc, task_id)
            if seed != claim.seed:
                raise ValueError("推理计划不能替换领取时的固定依据")
            plan = await asyncio.to_thread(DocumentImpactReasoningPlans.build,
                seed, envelope, manifest, manifest_checkpoint.sha256)
            reference = await confirmed_impact_write(store_impact_json(self._objects, claim, plan, "reasoning-plan.json"),
                task_id=task_id, stage="reasoning_plan_object")
        checkpoint = DocumentImpactReasoningCheckpoint(task_id=task_id, input_sha256=envelope.input_sha256,
            manifest_sha256=manifest_checkpoint.sha256, config_sha256=impact_record_hash(seed.reasoning_settings),
            storage_uri=reference.storage_uri, sha256=reference.sha256, size_bytes=reference.size_bytes,
            created_at=datetime.now(timezone.utc))
        try:
            saved = await confirmed_impact_write(self._store.reasoning_plans.save(claim, checkpoint),
                task_id=task_id, stage="reasoning_plan_checkpoint")
        except Exception as exc:
            # A lost commit reply is not rollback. Keep the object for durable replay.
            logger.warning("document_impact_reasoning_plan_commit_unconfirmed", extra={"task_id": task_id,
                "attempt_id": claim.attempt_id, "plan_sha256": checkpoint.sha256, "error_type": type(exc).__name__})
            raise
        if saved.storage_uri != reference.storage_uri:
            try:
                if not await self._store.reasoning_plans.object_is_referenced(reference.storage_uri):
                    await self._objects.delete_object_uri(reference.storage_uri)
            except Exception as exc:
                logger.warning("document_impact_reasoning_plan_cleanup_unconfirmed", extra={"task_id": task_id,
                    "plan_sha256": reference.sha256, "error_type": type(exc).__name__})
        return await self.read(project, doc, task_id)
