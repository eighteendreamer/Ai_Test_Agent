from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import PurePosixPath

from src.application.documents.api_doc_store import ApiDocConflictError
from src.application.documents.api_docs_service import ApiDocNotFoundError
from src.application.documents.git_process import GitDocumentProcess
from src.runtime.limited_parser_process import ParserProcessError
from src.schemas.api_docs import ApiDocVersionUploadRequest, GitDocumentOrigin
from src.schemas.document_git import (
    GitDocumentImportRequest, GitDocumentResolution, GitDocumentResolveRequest,
    GitDocumentSettings, GitDocumentSettingsRecord,
)

CONFIG_KEY = "document.git_sources"
logger = logging.getLogger(__name__)


class GitDocumentService:
    def __init__(self, *, config_store, project_service, documents, process=None):
        self._configs, self._projects, self._documents = config_store, project_service, documents
        self._process = process or GitDocumentProcess()

    async def get_settings(self, project_id: str) -> GitDocumentSettingsRecord:
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, CONFIG_KEY, scope_key=project_id)
        # Repository ownership never inherits a global or another project's list.
        return GitDocumentSettingsRecord(scope_key=project_id, revision=record.revision if record else 0,
            payload=GitDocumentSettings.model_validate(record.payload) if record else GitDocumentSettings())

    async def save_settings(self, project_id: str, payload: GitDocumentSettings) -> GitDocumentSettingsRecord:
        await self._projects.require_active(project_id)
        repositories = []
        for source in payload.repositories:
            # Disabled sources can remain recorded when their disk is unavailable.
            if source.enabled:
                result = await self._read({"operation": "validate", "root_path": source.root_path},
                    payload.policy, project_id, source.id)
                source = source.model_copy(update={"root_path": result["repository_root"]})
            repositories.append(source)
        normalized = payload.model_copy(update={"repositories": repositories})
        record = await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            normalized.model_dump(mode="json"), scope_key=project_id)
        logger.info("document_git_settings_saved", extra={"project_id": project_id,
            "revision": record.revision, "repository_count": len(repositories)})
        return GitDocumentSettingsRecord(scope_key=project_id, revision=record.revision,
            payload=GitDocumentSettings.model_validate(record.payload))

    @staticmethod
    def _source(settings, repository_id):
        source = next((item for item in settings.payload.repositories if item.id == repository_id), None)
        if source is None or not source.enabled:
            raise ValueError("当前项目未登记或未启用该 Git 仓库来源")
        return source

    async def _read(self, request, policy, project_id, repository_id):
        try:
            return await self._process.run(request, policy)
        except ParserProcessError as exc:
            logger.warning("document_git_read_failed", extra={"project_id": project_id,
                "repository_id": repository_id, "operation": request["operation"],
                "exit_code": exc.exit_code, "stdout": exc.stdout, "stderr": exc.stderr})
            raise

    @staticmethod
    def _origin(settings, source, result):
        return GitDocumentOrigin(repository_id=source.id, repository_name=source.name,
            repository_root=result["repository_root"], config_revision=settings.revision,
            commit=result["commit"], blob=result["blob"], path=result["path"], mode=result["mode"],
            sha256=result["sha256"], size_bytes=result["size_bytes"], git_version=result["git_version"],
            reader_policy=settings.payload.policy.model_dump(mode="json"))

    async def resolve(self, project_id: str, payload: GitDocumentResolveRequest) -> GitDocumentResolution:
        await self._projects.require_active(project_id)
        settings = await self.get_settings(project_id)
        source = self._source(settings, payload.repository_id)
        result = await self._read({"operation": "resolve", "root_path": source.root_path,
            "reference": payload.reference, "path": payload.path}, settings.payload.policy, project_id, source.id)
        origin = self._origin(settings, source, result)
        logger.info("document_git_source_resolved", extra={"project_id": project_id,
            "repository_id": source.id, "commit": origin.commit, "blob": origin.blob})
        return GitDocumentResolution(project_id=project_id, origin=origin)

    @staticmethod
    def request_hash(project_id: str, payload: GitDocumentImportRequest) -> str:
        value = {"project_id": project_id, **payload.model_dump(mode="json", exclude={"request_id"})}
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    async def get_submission(self, project_id: str, request_id: str, *, document_id: str | None = None):
        await self._projects.get(project_id)
        saved = await self._documents.get_source_submission(project_id, request_id, document_id=document_id)
        if saved is None:
            raise ApiDocNotFoundError("未找到此项目及文档的 Git 提交记录")
        return saved

    async def import_document(self, project_id: str, payload: GitDocumentImportRequest):
        signature = self.request_hash(project_id, payload)
        # An already committed request must not read moving refs or depend on disk/config availability.
        saved = await self._documents.get_source_submission(project_id, payload.request_id,
            document_id=payload.document_id, source_request_hash=signature)
        if saved is not None:
            return saved
        await self._projects.require_active(project_id)
        if payload.document_id:
            target = await self._documents.get_document(payload.document_id)
            if target.project_id != project_id:
                raise ApiDocNotFoundError("文档不属于当前项目")
            if target.current_version_id != payload.expected_parent_version_id:
                raise ApiDocConflictError("文档父版本已变化，请刷新后重新选择")
        settings = await self.get_settings(project_id)
        if settings.revision != payload.config_revision:
            raise ApiDocConflictError("Git 来源配置已变化，请重新解析固定来源")
        source = self._source(settings, payload.repository_id)
        result = await self._read({"operation": "read", "root_path": source.root_path,
            "reference": payload.commit, "path": payload.path, "expected_blob": payload.blob},
            settings.payload.policy, project_id, source.id)
        origin = self._origin(settings, source, result)
        arguments = {"filename": PurePosixPath(payload.path).name, "content_base64": result["content_base64"],
            "project_id": project_id, "source": "git", "request_id": payload.request_id}
        if payload.document_id:
            saved = await self._documents.upload_version(payload.document_id,
                ApiDocVersionUploadRequest(**arguments, expected_parent_version_id=payload.expected_parent_version_id),
                source_origin=origin, source_request_hash=signature)
        else:
            document = await self._documents.upload_document(**arguments, title=payload.title,
                source_origin=origin, source_request_hash=signature)
            # Return the same immutable submission result as subsequent replay/history calls.
            saved = await self.get_submission(project_id, payload.request_id, document_id=document.id)
        logger.info("document_git_import_completed", extra={"project_id": project_id,
            "repository_id": source.id, "request_id": payload.request_id,
            "document_id": saved.document.id, "version_id": saved.version.id,
            "commit": origin.commit, "blob": origin.blob, "created_new": saved.created_new})
        return saved
