from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.parser_process import DocumentParseProcess
from src.runtime.limited_parser_process import ParserProcessError
from src.schemas.document_parsing import DocumentParseResult, DocumentParseSeed, DocumentParseSnapshot, DocumentParseFailureArtifact, DocumentParseFailureView

logger = logging.getLogger(__name__)
DOCUMENT_PARSE_STREAM = "qa:tasks:document_parsing"


class DocumentParsingService:
    def __init__(self, *, store, documents, projects, configs, objects, stream: str | None, parser=None):
        self._store, self._docs, self._projects = store, documents, projects
        self._configs, self._objects, self._stream = configs, objects, stream
        self._parser = parser or DocumentParseProcess()

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
            logger.info("document_parse_submission_replayed", extra={"task_id": prior.id, "project_id": project_id})
            return prior
        if not self._stream:
            raise RuntimeError("持久解析的派发流尚未启用")
        version = await self._docs.get_version(document_id, request.version_id)
        asset = version.snapshot.original_asset
        if asset is None:
            raise ValueError("该历史版本缺少原件，不能伪造原件解析")
        config, options, parser = await self._configs.prepare(project_id, asset.filename, request.options)
        seed = DocumentParseSeed(project_id=project_id, document_id=document_id, version_id=version.id,
            original_asset=asset.model_copy(deep=True), config=config, options=options, parser=parser)
        return await self._store.submit(seed, creation_key=request.creation_key, request_hash=digest, stream=self._stream)

    async def get(self, project_id, document_id, task_id):
        await self._require_document(project_id, document_id)
        task = await self._store.get(project_id, task_id)
        if task is None or task.document_id != document_id:
            raise KeyError("当前文档没有该解析任务")
        return task

    async def get_by_request(self, project_id, document_id, creation_key):
        await self._require_document(project_id, document_id)
        task = await self._store.get_by_request(project_id, document_id, creation_key)
        if task is None:
            raise KeyError("当前文档没有该解析提交")
        return task

    async def read_snapshot(self, project_id, document_id, snapshot_id):
        await self._require_document(project_id, document_id)
        snapshot = await self._store.get_snapshot(project_id, document_id, snapshot_id)
        if snapshot is None:
            raise KeyError("当前文档没有该解析快照")
        result = await self._read_snapshot_result(snapshot)
        return snapshot, result

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

    async def read_failure(self, project_id, document_id, task_id):
        task = await self.get(project_id, document_id, task_id)
        artifact = task.failure_artifact
        if task.status != "failed" or artifact is None:
            raise ValueError("该解析任务没有已保存的进程失败诊断")
        raw = (await self._objects.read_object_uri(artifact.storage_uri))["content"]
        if len(raw) != artifact.size_bytes or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise ValueError("解析失败诊断大小或哈希不一致")
        payload = json.loads(raw)
        if payload.get("exit_code") != artifact.exit_code:
            raise ValueError("解析失败诊断退出码不一致")
        return DocumentParseFailureView(task_id=task.id, artifact=artifact, **payload)

    async def latest_snapshot(self, project_id, document_id, version_id):
        await self._require_document(project_id, document_id)
        snapshot = await self._store.latest_snapshot(project_id, document_id, version_id)
        if snapshot is None:
            return None
        return snapshot, await self._read_snapshot_result(snapshot)

    async def _read_snapshot_result(self, snapshot):
        stored = await self._objects.read_object_uri(snapshot.storage_uri)
        raw = stored["content"]
        if len(raw) != snapshot.size_bytes or hashlib.sha256(raw).hexdigest() != snapshot.sha256:
            raise ValueError("解析快照内容大小或哈希不一致")
        result = DocumentParseResult.model_validate_json(raw)
        seed = snapshot.seed
        if (result.source_sha256 != seed.original_asset.sha256 or result.source_size_bytes != seed.original_asset.size_bytes or
            result.options != seed.options or result.parser != seed.parser or result.parser != snapshot.parser or
            result.dependency_versions != snapshot.dependency_versions or result.status != snapshot.status or result.warnings != snapshot.warnings):
            raise ValueError("解析快照内容与固定依据不一致")
        return result

    async def execute(self, claim):
        seed = claim.seed
        document = await self._docs.get_document(seed.document_id)
        if document.project_id != seed.project_id:
            return await self._store.finish(claim, error_code="document_binding_changed", message="文档项目归属已变化，未读取原件")
        if claim.snapshot:
            # Resume final submission without rerunning extraction or reading settings.
            await self._read_snapshot_result(claim.snapshot)
            return await self._store.finish(claim)
        if claim.remaining_seconds <= 0:
            return await self._store.finish(claim, error_code="document_parse_deadline", message="原解析任务预算已耗尽")
        raw = (await self._objects.read_object_uri(seed.original_asset.storage_uri))["content"]
        if len(raw) != seed.original_asset.size_bytes or hashlib.sha256(raw).hexdigest() != seed.original_asset.sha256:
            return await self._store.finish(claim, error_code="document_original_integrity", message="原件大小或哈希不一致")
        policy = seed.config.payload.policy.model_copy(deep=True)
        policy.timeout_seconds = min(policy.timeout_seconds, claim.remaining_seconds)
        try:
            result = await self._parser.parse(filename=seed.original_asset.filename, content=raw, policy=policy, options=seed.options)
        except ParserProcessError as exc:
            # Keep the complete bounded worker trace in RustFS, never in ordinary logs.
            logger.error("document_parse_worker_rejected", extra={"task_id": claim.task.id,
                "attempt_id": claim.attempt_id, "exit_code": exc.exit_code,
                "stdout_chars": len(exc.stdout), "stderr_chars": len(exc.stderr)})
            encoded = json.dumps({"exit_code": exc.exit_code, "error": str(exc), "stdout": exc.stdout,
                "stderr": exc.stderr}, ensure_ascii=False).encode("utf-8")
            saved = await self._objects.store_uploaded_bytes(content=encoded, filename="failure.json", content_type="application/json",
                object_prefix=f"document-parsing/{seed.project_id}/{claim.task.id}/{claim.attempt_id}/failure")
            artifact = DocumentParseFailureArtifact(storage_uri=saved["uri"], sha256=hashlib.sha256(encoded).hexdigest(),
                size_bytes=len(encoded), exit_code=exc.exit_code)
            return await self._store.finish(claim, error_code="document_parser_failed", message="解析失败，完整受限进程记录已保存", failure_artifact=artifact)
        if result.parser != seed.parser:
            return await self._store.finish(claim, error_code="document_parser_version_changed", message="已部署解析器与任务固定版本不一致，请提交新的解析任务")
        snapshot_id = str(uuid4())
        encoded = result.model_dump_json().encode("utf-8")
        saved = await self._objects.store_uploaded_bytes(content=encoded, filename="result.json", content_type="application/json",
            object_prefix=f"document-parsing/{seed.project_id}/{claim.task.id}/{claim.attempt_id}/{snapshot_id}")
        snapshot = DocumentParseSnapshot(id=snapshot_id, task_id=claim.task.id, seed=seed,
            storage_uri=saved["uri"], sha256=hashlib.sha256(encoded).hexdigest(), size_bytes=len(encoded),
            status=result.status, parser=result.parser, dependency_versions=result.dependency_versions,
            warnings=result.warnings, created_at=datetime.now(timezone.utc))
        # Unknown commit outcomes retain the object for recovery and inspection.
        await self._store.save_snapshot_checkpoint(claim, snapshot)
        return await self._store.finish(claim)
