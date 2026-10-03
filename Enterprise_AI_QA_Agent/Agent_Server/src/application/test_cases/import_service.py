from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.projects.project_service import ProjectService
from src.application.security.upload_security_service import UploadSecurityService
from src.application.test_cases.case_service import TestCaseService
from src.application.test_cases.case_store import TestCaseStore
from src.application.test_cases.import_process import CaseImportProcess, ImportProcessError
from src.application.test_cases.import_store import CaseImportStore
from src.core.config import Settings
from src.infrastructure.application_config_store import ApplicationConfigStore
from src.schemas.case_import import (
    CaseImportBatchRecord, CaseImportBatchView, CaseImportIssue, CaseImportOptions, CaseImportPreview, CaseImportSettings,
    CaseImportSettingsRecord, CaseImportSourceRecord, CaseImportUploadRequest,
)

logger = logging.getLogger(__name__)
CONFIG_KEY = "test_case.import_settings"
PREVIEW_NAMESPACE = UUID("5cb91e56-6a51-5812-baf8-79e95728c931")


class CaseImportService:
    def __init__(self, *, settings: Settings, store: CaseImportStore, config_store: ApplicationConfigStore,
                 project_service: ProjectService, case_service: TestCaseService, case_store: TestCaseStore,
                 artifact_storage_service: ArtifactStorageService, upload_security_service: UploadSecurityService,
                 parser_process: CaseImportProcess | None = None):
        self._settings = settings
        self._store = store
        self._configs = config_store
        self._projects = project_service
        self._cases = case_service
        self._case_store = case_store
        self._objects = artifact_storage_service
        self._security = upload_security_service
        self._parser = parser_process or CaseImportProcess()

    async def initialize(self):
        await self._store.initialize()
        defaults = CaseImportSettings()
        defaults.policy.limits.max_bytes = min(defaults.policy.limits.max_bytes, self._settings.security.upload_scan_max_bytes)
        await asyncio.to_thread(self._configs.save, CONFIG_KEY, defaults.model_dump(mode="json"), only_if_missing=True)

    async def get_settings(self, project_id: str) -> CaseImportSettingsRecord:
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, CONFIG_KEY)
        if record is None:
            raise RuntimeError("用例导入配置尚未初始化")
        return CaseImportSettingsRecord(scope_key=record.scope_key, revision=record.revision,
                                        payload=CaseImportSettings.model_validate(record.payload))

    async def save_settings(self, project_id: str, payload: CaseImportSettings) -> CaseImportSettingsRecord:
        await self._projects.require_active(project_id)
        record = await asyncio.to_thread(self._configs.save, CONFIG_KEY, payload.model_dump(mode="json"), scope_key=project_id)
        return CaseImportSettingsRecord(scope_key=record.scope_key, revision=record.revision, payload=CaseImportSettings.model_validate(record.payload))

    async def upload(self, project_id: str, payload: CaseImportUploadRequest) -> CaseImportSourceRecord:
        await self._projects.require_active(project_id)
        config = await self.get_settings(project_id)
        filename = Path(payload.filename.replace("\\", "/")).name.strip()
        if Path(filename).suffix.lower() not in {".xlsx", ".json"}:
            raise ValueError("用例导入仅支持 .xlsx 和 .json")
        maximum = min(config.payload.policy.limits.max_bytes, self._settings.security.upload_scan_max_bytes)
        if len(payload.content_base64) > ((maximum + 2) // 3) * 4:
            raise ValueError("导入文件超过大小限制")
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except binascii.Error:
            raise ValueError("导入文件编码无效") from None
        if not content or len(content) > maximum:
            raise ValueError("导入文件为空或超过大小限制")
        file_hash = hashlib.sha256(content).hexdigest()
        existing = await self._store.find_source(project_id, file_hash)
        if existing:
            logger.info("case_import_source_reused", extra={"project_id": project_id, "source_id": existing.id})
            return existing
        sheets = await self._parser.inspect(filename=filename, content=content, policy=config.payload.policy,
            header_row=config.payload.options.header_row if config.payload.options else 1)
        source_id = str(uuid4())
        stored = await self._security.secure_store_upload(content=content, filename=filename,
            object_prefix=f"case-import/{project_id}/{source_id}", profile="chat_attachment", source="case_import")
        source = CaseImportSourceRecord(id=source_id, project_id=project_id, filename=filename, file_hash=file_hash,
            size_bytes=len(content), storage_uri=stored["uri"], security_report=stored["security_report"], sheets=sheets, created_at=datetime.now(timezone.utc))
        try:
            saved = await self._store.save_source(source)
        except Exception:
            # A lost commit acknowledgement must not delete a referenced object.
            try:
                saved = await self._store.find_source(project_id, file_hash)
            except Exception as lookup_error:
                logger.error("case_import_source_commit_uncertain", extra={"project_id": project_id, "source_id": source_id, "error_type": type(lookup_error).__name__})
                raise RuntimeError("导入目录保存失败，原件保留待核对") from None
            if saved is None:
                await self._delete_unreferenced(source)
                raise RuntimeError("导入目录保存失败，未创建导入来源") from None
        if saved.storage_uri != source.storage_uri:
            await self._delete_unreferenced(source)
        logger.info("case_import_source_saved", extra={"project_id": project_id, "source_id": saved.id, "size_bytes": saved.size_bytes})
        return saved

    async def _delete_unreferenced(self, source):
        try:
            await self._objects.delete_object_uri(source.storage_uri)
        except Exception as exc:
            logger.error("case_import_object_cleanup_failed", extra={"project_id": source.project_id, "source_id": source.id, "error_type": type(exc).__name__})
            raise RuntimeError("导入原件清理失败，需核对未关联对象") from None

    async def get_source(self, project_id: str, source_id: str) -> CaseImportSourceRecord:
        await self._projects.get(project_id)
        source = await self._store.get_source(project_id, source_id)
        if source is None:
            raise KeyError("导入来源不存在或不属于当前项目")
        return source

    async def read_original(self, project_id: str, source_id: str) -> tuple[CaseImportSourceRecord, bytes]:
        source = await self.get_source(project_id, source_id)
        content = (await self._objects.read_object_uri(source.storage_uri))["content"]
        if len(content) != source.size_bytes or hashlib.sha256(content).hexdigest() != source.file_hash:
            raise RuntimeError("导入原件与保存的哈希不一致，停止解析")
        return source, content

    async def inspect(self, project_id: str, source_id: str, header_row: int = 1):
        source, content = await self.read_original(project_id, source_id)
        config = await self.get_settings(project_id)
        return await self._parser.inspect(filename=source.filename, content=content, policy=config.payload.policy, header_row=header_row)

    async def preview(self, project_id: str, source_id: str, options: CaseImportOptions) -> CaseImportBatchView:
        await self._projects.require_active(project_id)
        source, content = await self.read_original(project_id, source_id)
        config = await self.get_settings(project_id)
        try:
            preview = await self._parser.parse(filename=source.filename, content=content, policy=config.payload.policy, options=options)
        except ImportProcessError as exc:
            preview = CaseImportPreview(filename=source.filename, file_hash=source.file_hash, options=options, sheets=source.sheets,
                issues=[CaseImportIssue(code="parser_failed", message=str(exc))],
                diagnostics={"exit_code": exc.exit_code, "stdout": exc.stdout, "stderr": exc.stderr})
            logger.warning("case_import_preview_failed", extra={"project_id": project_id, "source_id": source_id, "exit_code": exc.exit_code})
        fingerprint = hashlib.sha256(json.dumps({"source_id": source.id, "config": config.model_dump(mode="json"), "preview": preview.model_dump(mode="json")},
                                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        batch_id = str(uuid5(PREVIEW_NAMESPACE, source.id + ":" + fingerprint))
        for candidate in preview.candidates:
            candidate.draft.source_refs[0].source_id = source.id
            candidate.draft.source_refs[0].metadata.update({"source_id": source.id, "batch_id": batch_id, "preview_hash": fingerprint})
            candidate.draft.generation_metadata["import"].update({"source_id": source.id, "batch_id": batch_id, "preview_hash": fingerprint})
        batch = await self._store.save_batch(CaseImportBatchRecord(id=batch_id, project_id=project_id, source_id=source.id,
            preview_hash=fingerprint, config_snapshot=config, preview=preview, created_at=datetime.now(timezone.utc)))
        logger.info("case_import_preview_saved", extra={"project_id": project_id, "batch_id": batch.id, "candidate_count": len(batch.preview.candidates), "issue_count": len(batch.preview.issues)})
        return await self._view(batch)

    async def get_batch(self, project_id: str, batch_id: str) -> CaseImportBatchView:
        await self._projects.get(project_id)
        batch = await self._store.get_batch(project_id, batch_id)
        if batch is None:
            raise KeyError("导入批次不存在或不属于当前项目")
        return await self._view(batch)

    async def _view(self, batch) -> CaseImportBatchView:
        conflicts = set() if batch.status == "committed" else await self._case_store.find_case_keys(
            batch.project_id, [candidate.draft.case_key for candidate in batch.preview.candidates])
        return CaseImportBatchView(batch=batch, conflicting_case_keys=sorted(conflicts))

    async def commit(self, project_id: str, batch_id: str) -> CaseImportBatchView:
        view = await self.get_batch(project_id, batch_id)
        if view.batch.status == "committed":
            return view
        if view.conflicting_case_keys:
            raise ValueError("用例编号已存在：" + ", ".join(view.conflicting_case_keys) + "；未覆盖旧用例")
        entries = await self._cases.prepare_drafts(project_id=project_id,
            payloads=[candidate.draft for candidate in view.batch.preview.candidates], created_by="case-import")
        batch = await self._store.commit_batch(project_id, batch_id, entries)
        logger.info("case_import_committed", extra={"project_id": project_id, "batch_id": batch.id, "case_count": len(batch.case_ids)})
        return await self._view(batch)
