"""Durable comparison of two exact snapshots; never reparse or read a live template."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import platform
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.difference_engine import parse_snapshot_reference
from src.application.documents.difference_process import DocumentDifferenceProcess
from src.runtime.limited_parser_process import ParserProcessError
from src.schemas.document_differences import (
    DocumentDifferenceFailureArtifact, DocumentDifferenceFailureView, DocumentDifferenceResult,
    DocumentDifferenceSeed, DocumentDifferenceSnapshot, DocumentDifferenceSummary,
)
from src.schemas.document_parsing import DocumentParseSnapshotView

DOCUMENT_DIFFERENCE_STREAM = "qa:tasks:document_difference"
logger = logging.getLogger(__name__)


def result_summary(result):
    return DocumentDifferenceSummary(raw_changes=len(result.raw.changes), normalized_changes=len(result.normalized.changes),
        section_changes=len(result.sections), table_changes=len(result.tables), originals_equal=result.originals_equal,
        parsing_basis_changed=result.parsing_basis_changed)


class DocumentDifferenceService:
    def __init__(self, *, store, documents, projects, configs, objects, parsing, stream, comparator=None):
        self._store, self._docs, self._projects = store, documents, projects
        self._configs, self._objects, self._parsing, self._stream = configs, objects, parsing, stream
        self._comparator = comparator or DocumentDifferenceProcess()

    async def initialize(self):
        await self._configs.initialize()
        await self._store.initialize()

    async def _require_document(self, project_id, document_id):
        await self._projects.get(project_id)
        document = await self._docs.get_document(document_id)
        if document.project_id != project_id:
            raise KeyError("文档不属于当前项目")
        return document

    async def submit(self, project_id, document_id, request):
        await self._require_document(project_id, document_id)
        digest = self._store.request_hash(request)
        prior = await self._store.get_by_request(project_id, document_id, request.creation_key, request_hash=digest)
        if prior:
            logger.info("document_difference_submission_replayed", extra={"task_id": prior.id, "project_id": project_id})
            return prior
        if not self._stream:
            raise RuntimeError("持久比较的派发流尚未启用")
        # Metadata only: object downloads and costly validation are Worker work.
        snapshots = []
        for snapshot_id in (request.old_snapshot_id, request.new_snapshot_id):
            snapshot = await self._parsing.get_snapshot(project_id, document_id, snapshot_id)
            snapshots.append(snapshot)
        await self._projects.require_active(project_id)
        config = await self._configs.get_settings(project_id)
        seed = DocumentDifferenceSeed(project_id=project_id, document_id=document_id,
            version_id=snapshots[1].seed.version_id, old_snapshot=snapshots[0], new_snapshot=snapshots[1],
            config=config, python_version=platform.python_version())
        return await self._store.submit(seed, creation_key=request.creation_key, request_hash=digest, stream=self._stream)

    async def get(self, project_id, document_id, task_id):
        await self._require_document(project_id, document_id)
        task = await self._store.get(project_id, task_id)
        if task is None or task.document_id != document_id:
            raise KeyError("当前文档没有该比较任务")
        return task

    async def get_by_request(self, project_id, document_id, creation_key):
        await self._require_document(project_id, document_id)
        task = await self._store.get_by_request(project_id, document_id, creation_key)
        if task is None:
            raise KeyError("当前文档没有该比较提交")
        return task

    async def _require_version(self, project_id, document_id, version_id):
        await self._require_document(project_id, document_id)
        if version_id:
            await self._docs.get_version(document_id, version_id)

    async def list_tasks(self, project_id, document_id, *, version_id=None, limit=20, offset=0):
        await self._require_version(project_id, document_id, version_id)
        return await self._store.list_tasks(project_id, document_id, version_id=version_id, limit=limit, offset=offset)

    async def list_snapshots(self, project_id, document_id, *, version_id=None, limit=20, offset=0):
        await self._require_version(project_id, document_id, version_id)
        return await self._store.list_snapshots(project_id, document_id, version_id=version_id, limit=limit, offset=offset)

    async def read_snapshot(self, project_id, document_id, snapshot_id):
        snapshot = await self.get_snapshot(project_id, document_id, snapshot_id)
        return snapshot, await self._read_result(snapshot)

    async def get_snapshot(self, project_id, document_id, snapshot_id):
        """Fixed metadata for downstream submissions; no object download."""
        await self._require_document(project_id, document_id)
        snapshot = await self._store.get_snapshot(project_id, document_id, snapshot_id)
        if snapshot is None:
            raise KeyError("当前文档没有该比较结果")
        return snapshot

    async def _read_result(self, snapshot):
        raw = (await self._objects.read_object_uri(snapshot.storage_uri))["content"]
        if len(raw) != snapshot.size_bytes or hashlib.sha256(raw).hexdigest() != snapshot.sha256:
            raise ValueError("比较结果大小或哈希不一致")
        result = await asyncio.to_thread(DocumentDifferenceResult.model_validate_json, raw)
        seed = snapshot.seed
        self._store.validate_snapshot_metadata(snapshot, seed)
        if (result.old != parse_snapshot_reference(seed.old_snapshot) or result.new != parse_snapshot_reference(seed.new_snapshot) or
            result.policy != snapshot.effective_policy or result_summary(result) != snapshot.summary or
            result.warnings != snapshot.warnings or result.implementation_version != seed.implementation_version):
            raise ValueError("比较结果与固定输入或执行策略不一致")
        return result

    async def read_failure(self, project_id, document_id, task_id):
        task = await self.get(project_id, document_id, task_id)
        artifact = task.failure_artifact
        if task.status != "failed" or artifact is None:
            raise ValueError("该比较任务没有已保存的进程失败诊断")
        raw = (await self._objects.read_object_uri(artifact.storage_uri))["content"]
        if len(raw) != artifact.size_bytes or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise ValueError("比较失败诊断大小或哈希不一致")
        payload = json.loads(raw)
        if payload.get("exit_code") != artifact.exit_code:
            raise ValueError("比较失败诊断退出码不一致")
        return DocumentDifferenceFailureView(task_id=task.id, artifact=artifact, **payload)

    async def execute(self, claim):
        seed = claim.seed
        document = await self._docs.get_document(seed.document_id)
        if document.project_id != seed.project_id:
            return await self._store.finish(claim, error_code="document_binding_changed", message="文档项目归属已变化，未读取比较输入")
        if claim.snapshot:
            await self._read_result(claim.snapshot)
            return await self._store.finish(claim)
        if claim.remaining_seconds <= 0:
            return await self._store.finish(claim, error_code="document_difference_deadline", message="原比较任务预算已耗尽")
        if seed.python_version != platform.python_version() or seed.implementation_version != DocumentDifferenceResult.model_fields["implementation_version"].default:
            return await self._store.finish(claim, error_code="document_difference_engine_changed", message="比较引擎或Python版本与固定输入不同，请提交新任务")
        policy = seed.config.payload.policy.model_copy(deep=True)
        policy.timeout_seconds = min(policy.timeout_seconds, claim.remaining_seconds)
        phase, input_snapshot_id = "reading_input", None
        try:
            async with asyncio.timeout(claim.remaining_seconds):
                views = []
                for fixed in (seed.old_snapshot, seed.new_snapshot):
                    input_snapshot_id = fixed.id
                    actual, result = await self._parsing.read_snapshot(seed.project_id, seed.document_id, fixed.id)
                    if actual != fixed:
                        raise ValueError("比较来源快照已变化")
                    views.append(DocumentParseSnapshotView(snapshot=actual, result=result))
                phase, input_snapshot_id = "comparing", None
                result = await self._comparator.compare(old=views[0], new=views[1], policy=policy)
                encoded = (await asyncio.to_thread(result.model_dump_json)).encode("utf-8")
        except ParserProcessError as exc:
            logger.error("document_difference_worker_rejected", extra={"task_id": claim.task.id,
                "attempt_id": claim.attempt_id, "exit_code": exc.exit_code,
                "stdout_chars": len(exc.stdout), "stderr_chars": len(exc.stderr)})
            trace = json.dumps({"exit_code": exc.exit_code, "error": str(exc), "stdout": exc.stdout,
                "stderr": exc.stderr}, ensure_ascii=False).encode("utf-8")
            saved = await self._objects.store_uploaded_bytes(content=trace, filename="failure.json", content_type="application/json",
                object_prefix=f"document-differences/{seed.project_id}/{claim.task.id}/{claim.attempt_id}/failure")
            artifact = DocumentDifferenceFailureArtifact(storage_uri=saved["uri"], sha256=hashlib.sha256(trace).hexdigest(),
                size_bytes=len(trace), exit_code=exc.exit_code)
            return await self._store.finish(claim, error_code="document_difference_process_failed",
                message="比较失败，完整受限进程记录已保存", failure_artifact=artifact)
        except TimeoutError:
            logger.warning("document_difference_deadline_exceeded", extra={"task_id": claim.task.id, "attempt_id": claim.attempt_id})
            return await self._store.finish(claim, error_code="document_difference_deadline", message="固定输入读取或计算超过原任务预算")
        except (ValueError, KeyError) as exc:
            # Plain validation messages here are internal; Pydantic errors may
            # embed document content and therefore are represented only by type.
            reason = str(exc) if type(exc) in {ValueError, KeyError} else type(exc).__name__
            logger.error("document_difference_input_rejected", extra={"task_id": claim.task.id,
                "attempt_id": claim.attempt_id, "error_type": type(exc).__name__,
                "phase": phase, "input_snapshot_id": input_snapshot_id, "reason": reason})
            return await self._store.finish(claim, error_code="document_difference_input_invalid", message=f"固定比较输入或策略核验失败：{reason}")
        if (result.old != parse_snapshot_reference(seed.old_snapshot) or result.new != parse_snapshot_reference(seed.new_snapshot) or
            result.policy != policy or result.implementation_version != seed.implementation_version):
            return await self._store.finish(claim, error_code="document_difference_result_invalid", message="比较结果与固定输入或策略不一致")
        snapshot_id = str(uuid4())
        saved = await self._objects.store_uploaded_bytes(content=encoded, filename="result.json", content_type="application/json",
            object_prefix=f"document-differences/{seed.project_id}/{claim.task.id}/{claim.attempt_id}/{snapshot_id}")
        snapshot = DocumentDifferenceSnapshot(id=snapshot_id, task_id=claim.task.id, seed=seed, storage_uri=saved["uri"],
            sha256=hashlib.sha256(encoded).hexdigest(), size_bytes=len(encoded), effective_policy=policy,
            status="partial" if "partial" in {seed.old_snapshot.status, seed.new_snapshot.status} else "complete",
            summary=result_summary(result), warnings=result.warnings, created_at=datetime.now(timezone.utc))
        # Unknown commit outcomes retain the object: recovery checks the stored checkpoint.
        await self._store.save_snapshot_checkpoint(claim, snapshot)
        return await self._store.finish(claim)
