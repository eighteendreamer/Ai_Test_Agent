from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from uuid import uuid4

import httpx

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.security.upload_security_service import UploadSecurityService
from src.core.config import Settings
from src.schemas.api_docs import ApiDocRecord, UploadedAttachmentRecord
from src.schemas.integration import IntegrationRecord
from src.application.documents.api_doc_store import ApiDocStore, JsonApiDocStore
from src.application.projects.project_service import ProjectService


logger = logging.getLogger(__name__)


class ApiDocsService:
    def __init__(
        self,
        *,
        settings: Settings,
        artifact_storage_service: ArtifactStorageService,
        upload_security_service: UploadSecurityService | None = None,
        catalog_store: ApiDocStore | None = None,
        project_service: ProjectService | None = None,
    ) -> None:
        self._settings = settings
        self._artifact_storage_service = artifact_storage_service
        self._upload_security_service = upload_security_service
        self._data_dir = (Path(__file__).resolve().parents[2] / settings.data_dir / "api_docs").resolve()
        self._catalog_path = self._data_dir / "catalog.json"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._catalog_store = catalog_store or JsonApiDocStore(self._catalog_path)
        self._project_service = project_service

    async def initialize(self) -> None:
        await self._catalog_store.initialize()

    async def list_documents(
        self,
        *,
        project_id: str | None = None,
        unbound: bool = False,
        project_name: str | None = None,
        project_url: str | None = None,
    ) -> list[ApiDocRecord]:
        items = await self._catalog_store.list(project_id=project_id, unbound=unbound)
        normalized_name = self._normalize_optional_text(project_name)
        normalized_url = self._normalize_optional_text(project_url)
        if normalized_name:
            items = [item for item in items if (item.project_name or "").casefold() == normalized_name.casefold()]
        if normalized_url:
            expected_url = normalized_url.rstrip("/").casefold()
            items = [item for item in items if (item.project_url or "").rstrip("/").casefold() == expected_url]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    async def get_document(self, doc_id: str) -> ApiDocRecord:
        record = await self._catalog_store.get(doc_id)
        if record is None:
            raise ValueError(f"未找到 API 文档：{doc_id}")
        return record

    async def read_document_content(self, doc_id: str, *, max_chars: int | None = None) -> dict[str, Any]:
        record = await self.get_document(doc_id)
        content = record.preview_text or ""
        read_error: str | None = None
        if record.storage_uri:
            try:
                stored = await self._artifact_storage_service.read_object_uri(record.storage_uri)
                decoded = self._decode_text(stored.get("content", b""))
                if decoded:
                    content = decoded
            except Exception as exc:
                read_error = str(exc)

        full_length = len(content)
        limit = max_chars if isinstance(max_chars, int) and max_chars > 0 else None
        truncated = bool(limit and full_length > limit)
        if limit:
            content = content[:limit]

        return {
            "document": record.model_dump(mode="json"),
            "content": content,
            "full_length": full_length,
            "truncated": truncated,
            "read_error": read_error,
        }

    async def search_documents(
        self,
        *,
        query: str | None = None,
        doc_id: str | None = None,
        project_name: str | None = None,
        project_url: str | None = None,
        method: str | None = None,
        path: str | None = None,
        limit: int = 10,
        include_preview: bool = False,
        max_chars: int = 1200,
    ) -> dict[str, Any]:
        normalized_query = self._normalize_optional_text(query)
        normalized_doc_id = self._normalize_optional_text(doc_id)
        normalized_project_name = self._normalize_optional_text(project_name)
        normalized_project_url = self._normalize_optional_text(project_url)
        normalized_method = self._normalize_optional_text(method)
        normalized_path = self._normalize_optional_text(path)
        if normalized_method:
            normalized_method = normalized_method.upper()
        derived_method, derived_path = self._derive_search_filters(normalized_query)
        normalized_method = normalized_method or derived_method
        normalized_path = normalized_path or derived_path
        limit = self._clamp_int(limit, default=10, minimum=1, maximum=50)
        max_chars = self._clamp_int(max_chars, default=1200, minimum=200, maximum=12000)
        tokens = self._search_tokens(normalized_query)

        documents = await self.list_documents(
            project_name=normalized_project_name,
            project_url=normalized_project_url,
        )
        matches: list[dict[str, Any]] = []
        searched_count = 0
        for record in documents:
            if normalized_doc_id and record.id != normalized_doc_id:
                continue
            if normalized_project_name and not self._text_matches_query(
                normalized_project_name,
                f"{record.project_name or ''}\n{record.title}\n{record.filename}",
            ):
                continue
            if normalized_project_url and normalized_project_url not in (record.project_url or ""):
                continue

            searched_count += 1
            content_result = await self.read_document_content(record.id)
            content = str(content_result.get("content") or "")
            endpoints = self._extract_markdown_endpoints(content)
            endpoint_match_count = 0
            for endpoint in endpoints:
                method_value = str(endpoint.get("method") or "").upper()
                path_value = str(endpoint.get("path") or "")
                if normalized_method and method_value != normalized_method:
                    continue
                if normalized_path and normalized_path.lower() not in path_value.lower():
                    full_url = str(endpoint.get("full_url") or "")
                    if normalized_path.lower() not in full_url.lower():
                        continue
                haystack = "\n".join(
                    [
                        record.title,
                        record.filename,
                        record.project_name or "",
                        record.project_url or "",
                        method_value,
                        path_value,
                        str(endpoint.get("summary") or ""),
                        str(endpoint.get("full_url") or ""),
                        str(endpoint.get("section") or ""),
                    ]
                )
                if not self._text_matches_query(normalized_query, haystack):
                    continue

                endpoint_match_count += 1
                score = self._score_api_doc_match(
                    query=normalized_query,
                    tokens=tokens,
                    haystack=haystack,
                    record=record,
                    endpoint=endpoint,
                    method=normalized_method,
                    path=normalized_path,
                )
                match = {
                    "match_type": "endpoint",
                    "doc_id": record.id,
                    "title": record.title,
                    "filename": record.filename,
                    "project_name": record.project_name,
                    "project_url": record.project_url,
                    "format_label": record.format_label,
                    "endpoint_count": record.endpoint_count,
                    "method": method_value,
                    "path": path_value,
                    "full_url": endpoint.get("full_url"),
                    "summary": endpoint.get("summary"),
                    "score": score,
                    "excerpt": self._build_search_excerpt(str(endpoint.get("section") or ""), tokens, max_chars),
                    "updated_at": record.updated_at.isoformat(),
                }
                if include_preview:
                    match["content_preview"] = str(endpoint.get("section") or "")[:max_chars]
                matches.append(match)

            if endpoint_match_count:
                continue
            if normalized_method or normalized_path:
                continue

            document_haystack = "\n".join(
                [
                    record.title,
                    record.filename,
                    record.project_name or "",
                    record.project_url or "",
                    record.format_label,
                    content,
                ]
            )
            if not self._text_matches_query(normalized_query, document_haystack):
                continue
            score = self._score_api_doc_match(
                query=normalized_query,
                tokens=tokens,
                haystack=document_haystack,
                record=record,
                endpoint=None,
                method=None,
                path=None,
            )
            match = {
                "match_type": "document",
                "doc_id": record.id,
                "title": record.title,
                "filename": record.filename,
                "project_name": record.project_name,
                "project_url": record.project_url,
                "format_label": record.format_label,
                "endpoint_count": record.endpoint_count,
                "method": None,
                "path": None,
                "full_url": None,
                "summary": record.title,
                "score": score,
                "excerpt": self._build_search_excerpt(content, tokens, max_chars),
                "updated_at": record.updated_at.isoformat(),
            }
            if include_preview:
                match["content_preview"] = content[:max_chars]
            matches.append(match)

        matches.sort(key=lambda item: (float(item.get("score") or 0), str(item.get("updated_at") or "")), reverse=True)
        selected = matches[:limit]
        return {
            "summary": f"Found {len(selected)} API document matches across {searched_count} imported document(s).",
            "query": normalized_query,
            "filters": {
                "doc_id": normalized_doc_id,
                "project_name": normalized_project_name,
                "project_url": normalized_project_url,
                "method": normalized_method,
                "path": normalized_path,
            },
            "matches": selected,
            "metrics": {
                "match_count": len(selected),
                "total_match_count": len(matches),
                "document_count": len(documents),
                "searched_document_count": searched_count,
            },
        }

    async def upload_document(
        self,
        *,
        filename: str,
        content_base64: str,
        source: str = "manual_upload",
        title: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        project_url: str | None = None,
        content_type: str | None = None,
    ) -> ApiDocRecord:
        await self._require_bindable_project(project_id)
        content = self._decode_base64(content_base64)
        original_filename = filename
        original_size_bytes = len(content)
        declared_content_type = self._normalize_optional_text(content_type) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        original_content_type = declared_content_type
        normalized_project_url = self._normalize_optional_text(project_url)
        converted = self._maybe_convert_api_document_to_markdown(
            filename=filename,
            content=content,
            content_type=declared_content_type,
            title=title,
            project_name=project_name,
            project_url=normalized_project_url,
        )
        if converted is not None:
            filename = str(converted["filename"])
            content = converted["content"]  # type: ignore[assignment]
            declared_content_type = "text/markdown"
        preview_text, preview_truncated, preview_error = self._build_preview(filename, content, declared_content_type)
        if converted is not None:
            format_label = str(converted["format_label"])
            endpoint_count = converted.get("endpoint_count")  # type: ignore[assignment]
        else:
            format_label, endpoint_count = self._detect_format(filename, content, preview_text)
        doc_id = str(uuid4())
        now = datetime.now(timezone.utc)
        normalized_title = self._normalize_optional_text(title) or (
            str(converted.get("title")) if converted and converted.get("title") else None
        ) or Path(filename).stem or filename
        normalized_project_name = self._normalize_optional_text(project_name)
        storage_result = await self._store_upload(
            content=content,
            filename=filename,
            object_prefix=f"api-docs/{doc_id}",
            profile="api_document",
            source=source,
            content_type=declared_content_type,
        )
        content_type = str(storage_result.get("content_type") or declared_content_type)

        record = ApiDocRecord(
            id=doc_id,
            title=normalized_title.strip(),
            filename=filename,
            project_id=project_id,
            legacy_project_name=normalized_project_name,
            project_name=normalized_project_name,
            project_url=normalized_project_url,
            source=source,
            format_label=format_label,
            content_type=content_type,
            size_bytes=len(content),
            storage_uri=str(storage_result["uri"]),
            bucket=str(storage_result.get("bucket") or ""),
            object_name=str(storage_result.get("object_name") or ""),
            endpoint_count=endpoint_count,
            preview_available=preview_text is not None,
            preview_truncated=preview_truncated,
            preview_text=preview_text,
            preview_error=preview_error,
            uploaded_at=now,
            updated_at=now,
            metadata={
                "original_filename": original_filename,
                "stored_filename": filename,
                "original_content_type": original_content_type,
                "stored_content_type": content_type,
                "original_size_bytes": original_size_bytes,
                "storage_backend": storage_result.get("storage_backend", "rustfs"),
                "size_bytes": len(content),
                "security": storage_result.get("security_report"),
                "project_name": normalized_project_name,
                "project_id": project_id,
                "project_url": normalized_project_url,
                "converted_to_markdown": converted is not None,
                "conversion": converted.get("metadata") if converted else None,
            },
        )

        created = await self._catalog_store.create(record)
        logger.info(
            "api_document_created",
            extra={"api_doc_id": created.id, "project_id": created.project_id, "source": created.source},
        )
        return created

    async def update_document(
        self,
        doc_id: str,
        **changes: Any,
    ) -> ApiDocRecord:
        record = await self.get_document(doc_id)
        if "project_id" in changes:
            await self._require_bindable_project(changes["project_id"])
        item = record.model_dump(mode="python")
        if "title" in changes:
            item["title"] = self._normalize_optional_text(changes["title"]) or record.title
        if "project_id" in changes:
            item["project_id"] = changes["project_id"]
        if "project_name" in changes:
            legacy_name = self._normalize_optional_text(changes["project_name"])
            item["legacy_project_name"] = legacy_name
            item["project_name"] = legacy_name
        if "project_url" in changes:
            item["project_url"] = self._normalize_optional_text(changes["project_url"])
        item["updated_at"] = datetime.now(timezone.utc)
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            item["metadata"] = metadata
        metadata["project_id"] = item.get("project_id")
        metadata["project_name"] = item.get("legacy_project_name")
        metadata["project_url"] = item.get("project_url")
        if "project_url" in changes:
            await self._sync_markdown_project_url(item, item.get("project_url"))
        updated = await self._catalog_store.update(ApiDocRecord.model_validate(item))
        logger.info(
            "api_document_updated",
            extra={"api_doc_id": updated.id, "project_id": updated.project_id},
        )
        return updated

    async def import_document_from_url(
        self,
        *,
        url: str,
        title: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        project_url: str | None = None,
        source: str = "tools_api_docs_url",
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        filename: str | None = None,
    ) -> ApiDocRecord:
        fetched = await self._fetch_remote_document(url=url, headers=headers, auth=auth)
        fetched = await self._resolve_remote_api_document(
            original_url=url,
            fetched=fetched,
            headers=headers,
            auth=auth,
        )
        resolved_filename = filename or self._derive_remote_filename(
            original_url=url,
            final_url=fetched["final_url"],
            content_type=fetched["content_type"],
        )
        return await self.upload_document(
            filename=resolved_filename,
            content_base64=base64.b64encode(fetched["content"]).decode("ascii"),
            source=source,
            title=title,
            project_id=project_id,
            project_name=project_name,
            project_url=self._normalize_optional_text(project_url)
            or self._infer_project_url_from_url(str(fetched.get("final_url") or url))
            or self._infer_project_url_from_url(url),
            content_type=str(fetched.get("content_type") or ""),
        )

    async def import_document_from_integration(
        self,
        *,
        integration: IntegrationRecord,
        source: str = "tools_api_docs_integration",
        title: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        project_url: str | None = None,
        document_url: str | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> ApiDocRecord:
        import_title = title or integration.name
        import_project_name = project_name or integration.project_name
        import_project_url = (
            self._normalize_optional_text(project_url)
            or self._normalize_optional_text(integration.base_url)
            or self._infer_project_url_from_url(document_url or integration.document_url or integration.endpoint_url or integration.base_url or "")
        )
        return await self.import_document_from_url(
            url=document_url or integration.document_url or integration.endpoint_url or integration.base_url or "",
            title=import_title,
            project_id=project_id,
            project_name=import_project_name,
            project_url=import_project_url,
            source=source,
            headers=headers,
            auth=auth,
        )

    async def upload_attachment(
        self,
        *,
        filename: str,
        content_base64: str,
        source: str = "chat_attachment",
    ) -> UploadedAttachmentRecord:
        content = self._decode_base64(content_base64)
        declared_content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        preview_text, preview_truncated, preview_error = self._build_preview(filename, content, declared_content_type)
        attachment_id = str(uuid4())
        now = datetime.now(timezone.utc)
        storage_result = await self._store_upload(
            content=content,
            filename=filename,
            object_prefix=f"attachments/{attachment_id}",
            profile="chat_attachment",
            source=source,
            content_type=declared_content_type,
        )
        content_type = str(storage_result.get("content_type") or declared_content_type)
        return UploadedAttachmentRecord(
            id=attachment_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            storage_uri=str(storage_result["uri"]),
            preview_text=preview_text,
            preview_truncated=preview_truncated,
            preview_error=preview_error,
            uploaded_at=now,
            metadata={
                "source": source,
                "storage_backend": storage_result.get("storage_backend", "rustfs"),
                "bucket": storage_result.get("bucket", ""),
                "object_name": storage_result.get("object_name", ""),
                "security": storage_result.get("security_report"),
            },
        )

    async def delete_document(self, doc_id: str) -> dict[str, Any]:
        item = await self._catalog_store.delete(doc_id)
        if item is None:
            raise ValueError(f"未找到 API 文档：{doc_id}")
        uri = item.storage_uri
        if uri:
            try:
                await self._artifact_storage_service.delete_object_uri(uri)
            except Exception:
                pass
        return {"ok": True, "deleted_id": doc_id}

    async def _require_bindable_project(self, project_id: str | None) -> None:
        if not project_id:
            return
        if self._project_service is None:
            raise RuntimeError("Project service is required to bind an API document")
        await self._project_service.require_active(project_id)

    async def _sync_markdown_project_url(self, item: dict[str, Any], project_url: str | None) -> None:
        filename = str(item.get("filename") or "")
        content_type = str(item.get("content_type") or "")
        if Path(filename).suffix.lower() != ".md" and not content_type.startswith("text/markdown"):
            return

        uri = str(item.get("storage_uri") or "")
        markdown = str(item.get("preview_text") or "")
        if uri:
            try:
                stored = await self._artifact_storage_service.read_object_uri(uri)
                markdown = self._decode_text(stored.get("content", b"")) or markdown
            except Exception:
                pass
        if not markdown:
            return

        updated = self._apply_project_url_to_markdown(markdown, project_url)
        if updated == markdown:
            return

        content = updated.encode("utf-8")
        storage_result: dict[str, Any] | None = None
        bucket = str(item.get("bucket") or "")
        object_name = str(item.get("object_name") or "")
        if bucket and object_name:
            try:
                storage_result = await self._artifact_storage_service.store_uploaded_bytes(
                    content=content,
                    filename=filename or "api-document.md",
                    object_prefix="",
                    content_type="text/markdown",
                    bucket_name=bucket,
                    object_name=object_name,
                )
            except Exception:
                storage_result = None

        preview_text, preview_truncated, preview_error = self._build_preview(filename, content, "text/markdown")
        item["preview_text"] = preview_text
        item["preview_truncated"] = preview_truncated
        item["preview_error"] = preview_error
        item["preview_available"] = preview_text is not None
        item["size_bytes"] = len(content)
        item["content_type"] = "text/markdown"
        if storage_result is not None:
            item["storage_uri"] = str(storage_result.get("uri") or item.get("storage_uri") or "")
            item["bucket"] = str(storage_result.get("bucket") or bucket)
            item["object_name"] = str(storage_result.get("object_name") or object_name)
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            item["metadata"] = metadata
        metadata["size_bytes"] = len(content)
        metadata["stored_content_type"] = "text/markdown"
        metadata["project_url_synced_to_markdown"] = True

    def _apply_project_url_to_markdown(self, markdown: str, project_url: str | None) -> str:
        normalized_url = self._normalize_optional_text(project_url)
        lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = [
            line
            for line in lines
            if not line.startswith("**项目 URL**:") and not line.startswith("**调用地址**:")
        ]
        if normalized_url:
            insert_at = 0
            for index, line in enumerate(lines):
                if line.startswith("**所属项目**:"):
                    insert_at = index + 1
                    break
                if line.startswith("**接口数量**:"):
                    insert_at = index + 1
            lines.insert(insert_at, f"**项目 URL**: {normalized_url}")

        updated_lines: list[str] = []
        current_endpoint_path: str | None = None
        for line in lines:
            endpoint_match = re.match(r"^###\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(.+?)\s*$", line, flags=re.IGNORECASE)
            if endpoint_match:
                current_endpoint_path = endpoint_match.group(2).strip()
                updated_lines.append(line)
                continue
            if line.startswith("**调用地址**:"):
                if normalized_url and current_endpoint_path:
                    updated_lines.append(f"**调用地址**: `{self._join_project_url_path(normalized_url, current_endpoint_path)}`")
                continue
            updated_lines.append(line)
            if normalized_url and current_endpoint_path and line == "":
                previous = updated_lines[-2] if len(updated_lines) >= 2 else ""
                if previous.startswith(f"### ") and current_endpoint_path:
                    updated_lines.append(f"**调用地址**: `{self._join_project_url_path(normalized_url, current_endpoint_path)}`")
                    updated_lines.append("")
        return "\n".join(updated_lines).strip() + "\n"

    async def _store_upload(
        self,
        *,
        content: bytes,
        filename: str,
        object_prefix: str,
        profile: str,
        source: str,
        content_type: str,
    ) -> dict[str, Any]:
        if self._upload_security_service is not None:
            return await self._upload_security_service.secure_store_upload(
                content=content,
                filename=filename,
                object_prefix=object_prefix,
                profile=profile,
                source=source,
                content_type=content_type,
            )
        return await self._artifact_storage_service.store_uploaded_bytes(
            content=content,
            filename=filename,
            object_prefix=object_prefix,
            content_type=content_type,
        )

    def _decode_base64(self, value: str) -> bytes:
        try:
            return base64.b64decode(value, validate=True)
        except Exception as exc:
            raise ValueError("上传文件内容不是有效的 Base64 数据。") from exc

    def _build_preview(self, filename: str, content: bytes, content_type: str) -> tuple[str | None, bool, str | None]:
        is_text_like = content_type.startswith("text/") or Path(filename).suffix.lower() in {
            ".json",
            ".yaml",
            ".yml",
            ".txt",
            ".md",
            ".csv",
            ".xml",
            ".html",
            ".js",
            ".ts",
        }
        if not is_text_like:
            return None, False, "该文件不是文本类型，暂不支持内容预览。"

        decoded: str | None = None
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                decoded = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if decoded is None:
            return None, False, "文件内容无法按常见文本编码解析。"

        limit = 20000
        truncated = len(decoded) > limit
        return decoded[:limit], truncated, None

    def _detect_format(self, filename: str, content: bytes, preview_text: str | None) -> tuple[str, int | None]:
        suffix = Path(filename).suffix.lower()
        if suffix in {".yaml", ".yml"}:
            if preview_text and ("openapi:" in preview_text.lower() or "swagger:" in preview_text.lower()):
                return "OpenAPI / Swagger", self._count_yaml_paths(preview_text)
            return "YAML 文档", None

        if suffix == ".json":
            try:
                parsed = json.loads(content.decode("utf-8"))
            except Exception:
                return "JSON 文档", None
            if isinstance(parsed, dict) and ("openapi" in parsed or "swagger" in parsed):
                return "OpenAPI / Swagger", self._count_openapi_paths(parsed)
            if isinstance(parsed, dict) and "item" in parsed and "info" in parsed:
                return "Postman Collection", self._count_postman_requests(parsed.get("item"))
            return "JSON 文档", None

        if suffix == ".md":
            return "Markdown 文档", None
        if suffix in {".txt", ".log"}:
            return "文本文件", None
        return "其他文档", None

    def _maybe_convert_api_document_to_markdown(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        title: str | None,
        project_name: str | None,
        project_url: str | None,
    ) -> dict[str, Any] | None:
        suffix = Path(filename).suffix.lower()
        if suffix == ".md" or content_type.split(";", 1)[0].strip().lower() == "text/markdown":
            return None

        text = self._decode_text(content)
        if not text:
            return None

        parsed: dict[str, Any] | None = None
        stripped = text.lstrip()
        if suffix == ".json" or "json" in content_type.lower() or stripped.startswith("{"):
            try:
                payload = json.loads(text)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                if self._is_openapi_payload(payload):
                    parsed = self._parse_openapi_payload(payload)
                elif self._is_postman_payload(payload):
                    parsed = self._parse_postman_payload(payload)
                elif self._is_har_payload(payload):
                    parsed = self._parse_har_payload(payload)
        elif suffix in {".yaml", ".yml"} or "yaml" in content_type.lower() or stripped.lower().startswith(("openapi:", "swagger:")):
            if "openapi:" in stripped.lower() or "swagger:" in stripped.lower() or "\npaths:" in stripped.lower():
                parsed = self._parse_yaml_openapi_text(text)

        if not parsed or not parsed.get("endpoints"):
            return None

        document_title = self._normalize_optional_text(title) or str(parsed.get("service_name") or "").strip() or Path(filename).stem
        markdown = self._render_api_markdown(
            parsed=parsed,
            title=document_title,
            original_filename=filename,
            project_name=self._normalize_optional_text(project_name),
            project_url=self._normalize_optional_text(project_url),
        )
        markdown_filename = self._derive_markdown_filename(filename)
        endpoints = parsed.get("endpoints")
        endpoint_count = len(endpoints) if isinstance(endpoints, list) else None
        return {
            "filename": markdown_filename,
            "content": markdown.encode("utf-8"),
            "title": document_title,
            "format_label": f"{parsed.get('format_label') or 'API 文档'} Markdown",
            "endpoint_count": endpoint_count,
            "metadata": {
                "converted_from": filename,
                "source_format": parsed.get("format_label"),
                "service_name": parsed.get("service_name"),
                "project_url": self._normalize_optional_text(project_url),
                "endpoint_count": endpoint_count,
                "warnings": parsed.get("warnings") or [],
            },
        }

    def _is_openapi_payload(self, payload: dict[str, Any]) -> bool:
        return "openapi" in payload or "swagger" in payload or (
            isinstance(payload.get("paths"), dict) and isinstance(payload.get("info"), dict)
        )

    def _is_postman_payload(self, payload: dict[str, Any]) -> bool:
        return isinstance(payload.get("info"), dict) and isinstance(payload.get("item"), list)

    def _is_har_payload(self, payload: dict[str, Any]) -> bool:
        log = payload.get("log")
        return isinstance(log, dict) and isinstance(log.get("entries"), list)

    def _parse_openapi_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
        endpoints: list[dict[str, Any]] = []
        for path, operations in paths.items():
            if not isinstance(operations, dict):
                continue
            path_parameters = operations.get("parameters") if isinstance(operations.get("parameters"), list) else []
            for method, spec in operations.items():
                method_name = str(method).upper()
                if method_name.lower() not in {"get", "post", "put", "delete", "patch", "head", "options"}:
                    continue
                if not isinstance(spec, dict):
                    continue
                operation = self._resolve_openapi_ref(spec, payload)
                parameters = list(path_parameters)
                if isinstance(operation.get("parameters"), list):
                    parameters.extend(operation.get("parameters") or [])
                endpoints.append(
                    {
                        "method": method_name,
                        "path": str(path),
                        "summary": self._normalize_optional_text(operation.get("summary") or operation.get("operationId")),
                        "description": self._normalize_optional_text(operation.get("description")),
                        "params": self._format_openapi_parameters(parameters, operation, payload),
                        "success_example": self._extract_openapi_response_example(operation, payload, success=True),
                        "error_example": self._extract_openapi_response_example(operation, payload, success=False),
                        "notes": self._format_openapi_notes(operation),
                    }
                )

        servers = payload.get("servers")
        base_urls: list[str] = []
        if isinstance(servers, list):
            for server in servers:
                if isinstance(server, dict) and server.get("url"):
                    base_urls.append(str(server["url"]))
        elif payload.get("host"):
            scheme = "https"
            schemes = payload.get("schemes")
            if isinstance(schemes, list) and schemes:
                scheme = str(schemes[0])
            base_path = str(payload.get("basePath") or "")
            base_urls.append(f"{scheme}://{payload['host']}{base_path}")

        return {
            "format_label": "OpenAPI / Swagger",
            "service_name": str(info.get("title") or "API 文档"),
            "description": self._normalize_optional_text(info.get("description")),
            "version": str(payload.get("openapi") or payload.get("swagger") or info.get("version") or ""),
            "base_urls": base_urls,
            "endpoints": endpoints,
            "warnings": [],
        }

    def _parse_postman_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        endpoints: list[dict[str, Any]] = []
        self._collect_postman_items(payload.get("item"), endpoints)
        return {
            "format_label": "Postman Collection",
            "service_name": str(info.get("name") or "Postman API"),
            "description": self._normalize_optional_text(info.get("description")),
            "version": str(info.get("schema") or ""),
            "base_urls": [],
            "endpoints": endpoints,
            "warnings": [],
        }

    def _parse_har_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        log = payload.get("log") if isinstance(payload.get("log"), dict) else {}
        entries = log.get("entries") if isinstance(log.get("entries"), list) else []
        endpoints: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
            response = entry.get("response") if isinstance(entry.get("response"), dict) else {}
            url = str(request.get("url") or "")
            if not url:
                continue
            parsed_url = urlparse(url)
            method = str(request.get("method") or "GET").upper()
            path = parsed_url.path or "/"
            key = f"{method}:{path}"
            if key in seen:
                continue
            seen.add(key)
            endpoints.append(
                {
                    "method": method,
                    "path": path,
                    "summary": self._generate_har_summary(method, path),
                    "description": f"从 HAR 导入的接口，状态码：{response.get('status', '未知')}",
                    "params": self._format_har_params(request),
                    "success_example": self._extract_har_success_example(response),
                    "error_example": None,
                    "notes": f"原始 URL: {url}",
                }
            )
        service_name = "HAR Imported API"
        if entries:
            first_request = entries[0].get("request") if isinstance(entries[0], dict) else {}
            first_url = str(first_request.get("url") or "") if isinstance(first_request, dict) else ""
            if first_url:
                netloc = urlparse(first_url).netloc
                if netloc:
                    service_name = f"{netloc} API"
        return {
            "format_label": "HAR",
            "service_name": service_name,
            "description": None,
            "version": str(log.get("version") or ""),
            "base_urls": [],
            "endpoints": endpoints,
            "warnings": [],
        }

    def _parse_yaml_openapi_text(self, content: str) -> dict[str, Any]:
        endpoints: list[dict[str, Any]] = []
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        service_name = self._extract_yaml_scalar(lines, "title") or "API 文档"
        version = self._extract_yaml_scalar(lines, "openapi") or self._extract_yaml_scalar(lines, "swagger") or ""
        in_paths = False
        paths_indent = 0
        path_indent = 0
        method_indent = 0
        current_path = ""
        current_endpoint: dict[str, Any] | None = None
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            stripped = line.strip()
            if re.match(r"^paths\s*:", stripped, flags=re.IGNORECASE):
                in_paths = True
                paths_indent = indent
                continue
            if not in_paths:
                continue
            if indent <= paths_indent and not stripped.startswith("/"):
                break
            path_match = re.match(r"""^["']?(/[^"']*?)["']?\s*:\s*$""", stripped)
            if path_match:
                current_path = path_match.group(1)
                path_indent = indent
                current_endpoint = None
                continue
            method_match = re.match(r"^(get|post|put|delete|patch|head|options)\s*:\s*$", stripped, flags=re.IGNORECASE)
            if current_path and method_match and indent > path_indent:
                current_endpoint = {
                    "method": method_match.group(1).upper(),
                    "path": current_path,
                    "summary": None,
                    "description": None,
                    "params": None,
                    "success_example": None,
                    "error_example": None,
                    "notes": None,
                }
                endpoints.append(current_endpoint)
                method_indent = indent
                continue
            if current_endpoint and indent > method_indent:
                scalar_match = re.match(r"^(summary|description|operationId)\s*:\s*(.*)$", stripped, flags=re.IGNORECASE)
                if scalar_match:
                    key = scalar_match.group(1).lower()
                    value = self._clean_yaml_scalar(scalar_match.group(2))
                    if key == "summary":
                        current_endpoint["summary"] = value
                    elif key == "operationid" and not current_endpoint.get("summary"):
                        current_endpoint["summary"] = value
                    elif key == "description":
                        current_endpoint["description"] = value

        return {
            "format_label": "OpenAPI / Swagger",
            "service_name": service_name,
            "description": self._extract_yaml_scalar(lines, "description"),
            "version": version,
            "base_urls": [],
            "endpoints": endpoints,
            "warnings": ["YAML 文档使用轻量解析器转换，复杂 schema 示例可能不会完整展开。"],
        }

    def _render_api_markdown(
        self,
        *,
        parsed: dict[str, Any],
        title: str,
        original_filename: str,
        project_name: str | None,
        project_url: str | None,
    ) -> str:
        endpoints = parsed.get("endpoints") if isinstance(parsed.get("endpoints"), list) else []
        normalized_project_url = self._normalize_optional_text(project_url)
        lines = [
            f"# API 文档 - {title}",
            "",
            f"**来源格式**: {parsed.get('format_label') or 'API 文档'}",
            f"**原始文件**: {original_filename}",
            f"**接口数量**: {len(endpoints)}",
        ]
        if project_name:
            lines.append(f"**所属项目**: {project_name}")
        if normalized_project_url:
            lines.append(f"**项目 URL**: {normalized_project_url}")
        if parsed.get("version"):
            lines.append(f"**版本**: {parsed['version']}")
        base_urls = parsed.get("base_urls") if isinstance(parsed.get("base_urls"), list) else []
        if base_urls:
            lines.append(f"**Base URL**: {', '.join(str(item) for item in base_urls)}")
        if parsed.get("description"):
            lines.extend(["", str(parsed["description"])])

        lines.extend(["", "---", "", "## 接口总览", ""])
        if endpoints:
            lines.extend(["| 方法 | 路径 | 说明 |", "| --- | --- | --- |"])
            for endpoint in endpoints:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            self._escape_markdown_table_cell(str(endpoint.get("method") or "")),
                            f"`{self._escape_markdown_table_cell(str(endpoint.get('path') or '/'))}`",
                            self._escape_markdown_table_cell(str(endpoint.get("summary") or endpoint.get("description") or "")),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("暂无可识别接口。")

        lines.extend(["", "## 接口详情", ""])
        for endpoint in endpoints:
            lines.extend(self._endpoint_to_markdown(endpoint, project_url=normalized_project_url))
            lines.append("")

        warnings = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []
        if warnings:
            lines.extend(["", "## 转换提示", ""])
            for warning in warnings:
                lines.append(f"- {warning}")

        return "\n".join(lines).strip() + "\n"

    def _endpoint_to_markdown(self, endpoint: dict[str, Any], *, project_url: str | None = None) -> list[str]:
        method = str(endpoint.get("method") or "GET").upper()
        path = str(endpoint.get("path") or "/")
        lines = [f"### {method} {path}", ""]
        full_url = self._join_project_url_path(project_url, path)
        if full_url:
            lines.extend([f"**调用地址**: `{full_url}`", ""])
        if endpoint.get("summary"):
            lines.extend([f"**功能**: {endpoint['summary']}", ""])
        if endpoint.get("description"):
            lines.extend([str(endpoint["description"]), ""])
        if endpoint.get("params"):
            lines.extend(["**请求参数**:", "", str(endpoint["params"]), ""])
        if endpoint.get("success_example"):
            lines.extend(["**成功响应**:", "```json", str(endpoint["success_example"]), "```", ""])
        if endpoint.get("error_example"):
            lines.extend(["**错误响应**:", "```json", str(endpoint["error_example"]), "```", ""])
        if endpoint.get("notes"):
            lines.extend(["**说明**:", str(endpoint["notes"]), ""])
        lines.append("---")
        return lines

    def _derive_markdown_filename(self, filename: str) -> str:
        stem = Path(filename).stem.strip() or "api-document"
        safe_stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", stem, flags=re.UNICODE).strip(".-")
        return f"{safe_stem or 'api-document'}.md"

    def _infer_project_url_from_url(self, url: str) -> str | None:
        normalized = self._normalize_optional_text(url)
        if not normalized:
            return None
        parsed = urlparse(normalized)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    def _join_project_url_path(self, project_url: str | None, path: str) -> str | None:
        normalized_url = self._normalize_optional_text(project_url)
        if not normalized_url:
            return None
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{normalized_url.rstrip('/')}{normalized_path}"

    def _format_openapi_parameters(
        self,
        parameters: list[Any],
        operation: dict[str, Any],
        root: dict[str, Any],
    ) -> str | None:
        parts: list[str] = []
        for item in parameters:
            if not isinstance(item, dict):
                continue
            param = self._resolve_openapi_ref(item, root)
            name = str(param.get("name") or "")
            if not name:
                continue
            location = str(param.get("in") or "")
            required = "必填" if param.get("required") else "选填"
            description = str(param.get("description") or "").strip()
            parts.append(f"- `{name}` ({location}, {required}): {description or '无说明'}")

        request_body = operation.get("requestBody")
        if isinstance(request_body, dict):
            request_body = self._resolve_openapi_ref(request_body, root)
            content = request_body.get("content") if isinstance(request_body.get("content"), dict) else {}
            schema = None
            media_type = ""
            for candidate in ("application/json", "application/*+json", "multipart/form-data", "application/x-www-form-urlencoded"):
                if isinstance(content.get(candidate), dict):
                    schema = content[candidate].get("schema")
                    media_type = candidate
                    break
            if schema:
                parts.extend(["", f"**请求体** ({media_type}):", "```json", self._schema_to_json_preview(schema, root), "```"])
        return "\n".join(parts).strip() or None

    def _extract_openapi_response_example(
        self,
        operation: dict[str, Any],
        root: dict[str, Any],
        *,
        success: bool,
    ) -> str | None:
        responses = operation.get("responses")
        if not isinstance(responses, dict):
            return None
        for status_code, response in responses.items():
            status = int(status_code) if str(status_code).isdigit() else 0
            if success and not (200 <= status < 300):
                continue
            if not success and not (400 <= status < 600):
                continue
            if not isinstance(response, dict):
                continue
            response = self._resolve_openapi_ref(response, root)
            content = response.get("content") if isinstance(response.get("content"), dict) else {}
            for media_value in content.values():
                if not isinstance(media_value, dict):
                    continue
                if "example" in media_value:
                    return self._json_dumps(media_value["example"])
                examples = media_value.get("examples")
                if isinstance(examples, dict) and examples:
                    first = next(iter(examples.values()))
                    if isinstance(first, dict) and "value" in first:
                        return self._json_dumps(first["value"])
                if isinstance(media_value.get("schema"), dict):
                    return self._schema_to_json_preview(media_value["schema"], root)
            description = self._normalize_optional_text(response.get("description"))
            if description:
                return self._json_dumps({"description": description})
        return None

    def _format_openapi_notes(self, operation: dict[str, Any]) -> str | None:
        notes: list[str] = []
        tags = operation.get("tags")
        if isinstance(tags, list) and tags:
            notes.append(f"标签: {', '.join(str(item) for item in tags)}")
        if operation.get("deprecated"):
            notes.append("该接口已废弃")
        if operation.get("security"):
            notes.append("需要认证")
        return "\n".join(notes) or None

    def _schema_to_json_preview(self, schema: Any, root: dict[str, Any], depth: int = 0) -> str:
        value = self._schema_to_example(schema, root, depth=depth)
        return self._json_dumps(value)

    def _schema_to_example(self, schema: Any, root: dict[str, Any], depth: int = 0) -> Any:
        if depth > 4:
            return "..."
        if not isinstance(schema, dict):
            return {}
        schema = self._resolve_openapi_ref(schema, root)
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
            return schema["enum"][0]
        schema_type = schema.get("type")
        if not schema_type and "properties" in schema:
            schema_type = "object"
        if schema_type == "object":
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            return {key: self._schema_to_example(value, root, depth=depth + 1) for key, value in properties.items()}
        if schema_type == "array":
            return [self._schema_to_example(schema.get("items"), root, depth=depth + 1)]
        if schema_type == "integer":
            return 0
        if schema_type == "number":
            return 0.0
        if schema_type == "boolean":
            return True
        if schema_type == "string":
            return "string"
        return {}

    def _resolve_openapi_ref(self, value: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
        ref = value.get("$ref")
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return value
        current: Any = root
        for part in ref.lstrip("#/").split("/"):
            if not isinstance(current, dict):
                return value
            current = current.get(part.replace("~1", "/").replace("~0", "~"))
        return current if isinstance(current, dict) else value

    def _collect_postman_items(self, items: Any, endpoints: list[dict[str, Any]], folder_path: str = "") -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            nested = item.get("item")
            if isinstance(nested, list) and "request" not in item:
                folder_name = str(item.get("name") or "")
                next_folder = f"{folder_path}/{folder_name}" if folder_path and folder_name else folder_name or folder_path
                self._collect_postman_items(nested, endpoints, next_folder)
                continue
            request = item.get("request")
            if not isinstance(request, dict):
                continue
            summary = str(item.get("name") or "")
            if folder_path:
                summary = f"[{folder_path}] {summary}".strip()
            endpoints.append(
                {
                    "method": str(request.get("method") or "GET").upper(),
                    "path": self._postman_url_to_path(request.get("url")),
                    "summary": summary or None,
                    "description": self._normalize_optional_text(item.get("description") or request.get("description")),
                    "params": self._format_postman_params(request),
                    "success_example": self._extract_postman_response(item.get("response"), success=True),
                    "error_example": self._extract_postman_response(item.get("response"), success=False),
                    "notes": self._format_postman_notes(request),
                }
            )

    def _postman_url_to_path(self, url: Any) -> str:
        if isinstance(url, str):
            parsed = urlparse(url)
            return parsed.path or "/" + url.split("?", 1)[0].lstrip("/")
        if not isinstance(url, dict):
            return "/"
        path = url.get("path")
        if isinstance(path, list):
            value = "/" + "/".join(str(item).strip("/") for item in path if item)
        else:
            value = "/" + str(path or "").strip("/")
        return re.sub(r"\{\{([^}]+)\}\}", r"{\1}", value) or "/"

    def _format_postman_params(self, request: dict[str, Any]) -> str | None:
        parts: list[str] = []
        url = request.get("url") if isinstance(request.get("url"), dict) else {}
        query = url.get("query") if isinstance(url, dict) else None
        if isinstance(query, list) and query:
            parts.append("**Query 参数**:")
            for item in query:
                if isinstance(item, dict):
                    parts.append(f"- `{item.get('key', '')}`: {item.get('description') or item.get('value') or '无说明'}")
        headers = request.get("header")
        if isinstance(headers, list):
            visible_headers = [item for item in headers if isinstance(item, dict) and str(item.get("key") or "").lower() not in {"content-type", "user-agent"}]
            if visible_headers:
                parts.extend(["", "**Headers**:"])
                for item in visible_headers:
                    parts.append(f"- `{item.get('key', '')}`: {item.get('description') or item.get('value') or '无说明'}")
        body = request.get("body") if isinstance(request.get("body"), dict) else {}
        if body:
            mode = str(body.get("mode") or "")
            if mode == "raw" and body.get("raw"):
                parts.extend(["", "**请求体**:", "```json", str(body.get("raw")), "```"])
            elif mode in {"formdata", "urlencoded"} and isinstance(body.get(mode), list):
                parts.extend(["", f"**{mode}**:"])
                for item in body[mode]:
                    if isinstance(item, dict):
                        parts.append(f"- `{item.get('key', '')}`: {item.get('description') or item.get('value') or '无说明'}")
        return "\n".join(parts).strip() or None

    def _extract_postman_response(self, responses: Any, *, success: bool) -> str | None:
        if not isinstance(responses, list):
            return None
        for response in responses:
            if not isinstance(response, dict):
                continue
            code = int(response.get("code") or 0)
            if success and not (200 <= code < 300):
                continue
            if not success and not (400 <= code < 600):
                continue
            body = str(response.get("body") or "").strip()
            if not body:
                continue
            try:
                return self._json_dumps(json.loads(body))
            except Exception:
                return body
        return None

    def _format_postman_notes(self, request: dict[str, Any]) -> str | None:
        auth = request.get("auth") if isinstance(request.get("auth"), dict) else {}
        auth_type = auth.get("type") if isinstance(auth, dict) else None
        return f"认证方式: {auth_type}" if auth_type else None

    def _format_har_params(self, request: dict[str, Any]) -> str | None:
        params: list[dict[str, str]] = []
        for item in request.get("queryString") or []:
            if isinstance(item, dict) and item.get("name"):
                params.append({"name": str(item["name"]), "value": str(item.get("value") or ""), "type": "query"})
        for item in request.get("headers") or []:
            if isinstance(item, dict) and item.get("name") and str(item.get("name")).lower() not in {"cookie", "user-agent", "accept-encoding", "connection"}:
                params.append({"name": str(item["name"]), "value": str(item.get("value") or ""), "type": "header"})
        post_data = request.get("postData") if isinstance(request.get("postData"), dict) else {}
        if post_data.get("text"):
            params.append({"name": "body", "value": str(post_data["text"])[:4000], "type": str(post_data.get("mimeType") or "body")})
        return self._json_dumps(params) if params else None

    def _extract_har_success_example(self, response: dict[str, Any]) -> str | None:
        status = int(response.get("status") or 0)
        if not (200 <= status < 300):
            return None
        content = response.get("content") if isinstance(response.get("content"), dict) else {}
        text = str(content.get("text") or "").strip()
        if not text:
            return None
        if "json" in str(content.get("mimeType") or "").lower():
            try:
                return self._json_dumps(json.loads(text))
            except Exception:
                return text
        return text[:4000]

    def _generate_har_summary(self, method: str, path: str) -> str:
        resource = next((part for part in reversed(path.split("/")) if part and not part.startswith("{")), "resource")
        action_map = {"GET": "获取", "POST": "创建", "PUT": "更新", "PATCH": "修改", "DELETE": "删除"}
        return f"{action_map.get(method.upper(), '访问')}{resource}"

    def _extract_yaml_scalar(self, lines: list[str], key: str) -> str | None:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", flags=re.IGNORECASE)
        for line in lines:
            match = pattern.match(line)
            if match:
                return self._clean_yaml_scalar(match.group(1))
        return None

    def _clean_yaml_scalar(self, value: str) -> str:
        cleaned = value.strip()
        if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
            cleaned = cleaned[1:-1]
        return cleaned.strip()

    def _json_dumps(self, value: Any) -> str:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return value
        return json.dumps(value, ensure_ascii=False, indent=2)

    def _escape_markdown_table_cell(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")

    def _count_openapi_paths(self, payload: dict[str, Any]) -> int | None:
        paths = payload.get("paths")
        if not isinstance(paths, dict):
            return None
        count = 0
        for methods in paths.values():
            if isinstance(methods, dict):
                count += sum(1 for key in methods.keys() if str(key).lower() in {"get", "post", "put", "delete", "patch", "head", "options"})
        return count or len(paths)

    def _count_postman_requests(self, items: Any) -> int | None:
        if not isinstance(items, list):
            return None
        count = 0
        stack = list(items)
        while stack:
            item = stack.pop()
            if not isinstance(item, dict):
                continue
            if "request" in item:
                count += 1
            nested = item.get("item")
            if isinstance(nested, list):
                stack.extend(nested)
        return count or None

    def _count_yaml_paths(self, content: str) -> int | None:
        count = 0
        in_paths = False
        for line in content.splitlines():
            if line.strip().lower().startswith("paths:"):
                in_paths = True
                continue
            if in_paths:
                if line and not line.startswith((" ", "\t")):
                    break
                stripped = line.strip()
                if stripped.startswith("/") and stripped.endswith(":"):
                    count += 1
        return count or None

    async def _fetch_remote_document(
        self,
        *,
        url: str,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized_url = self._normalize_optional_text(url)
        if not normalized_url:
            raise ValueError("导入地址不能为空。")

        client_auth = httpx.BasicAuth(*auth) if auth is not None else None
        try:
            async with httpx.AsyncClient(
                timeout=min(self._settings.model.llm_request_timeout_seconds, 45.0),
                follow_redirects=True,
            ) as client:
                response = await client.get(normalized_url, headers=headers or {}, auth=client_auth)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"远程文档拉取失败：{exc}") from exc

        return {
            "content": response.content,
            "content_type": response.headers.get("content-type", "application/octet-stream"),
            "final_url": str(response.url),
        }

    def _derive_remote_filename(self, *, original_url: str, final_url: str, content_type: str) -> str:
        suffix = self._suffix_for_content_type(content_type) or ".txt"
        for candidate in (final_url, original_url):
            parsed = urlparse(candidate)
            name = Path(unquote(parsed.path)).name
            if name:
                if Path(name).suffix:
                    return name
                return f"{name}{suffix}"
        return f"remote-api-doc{suffix}"

    async def _resolve_remote_api_document(
        self,
        *,
        original_url: str,
        fetched: dict[str, Any],
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        if self._looks_like_api_document(fetched):
            return fetched
        if not self._looks_like_html_document(fetched):
            return fetched

        html = self._decode_text(fetched.get("content", b""))
        candidate_urls = self._discover_openapi_candidate_urls(
            original_url=original_url,
            final_url=str(fetched.get("final_url") or original_url),
            html=html,
        )
        if not candidate_urls:
            return fetched

        last_error: Exception | None = None
        checked_urls: set[str] = set()
        index = 0
        while index < len(candidate_urls):
            candidate_url = candidate_urls[index]
            index += 1
            if candidate_url in checked_urls:
                continue
            checked_urls.add(candidate_url)
            try:
                candidate = await self._fetch_remote_document(url=candidate_url, headers=headers, auth=auth)
            except Exception as exc:
                last_error = exc
                continue
            if self._looks_like_api_document(candidate):
                metadata = dict(candidate.get("metadata") or {})
                metadata["discovered_from_url"] = original_url
                metadata["discovery_mode"] = "swagger_ui_html"
                candidate["metadata"] = metadata
                return candidate
            for nested_url in self._discover_openapi_candidate_urls_from_payload(candidate):
                self._append_unique_url(candidate_urls, nested_url)
            if self._looks_like_discovery_text_document(candidate):
                candidate_text = self._decode_text(candidate.get("content", b""))
                for nested_url in self._discover_openapi_candidate_urls(
                    original_url=original_url,
                    final_url=str(candidate.get("final_url") or candidate_url),
                    html=candidate_text,
                ):
                    self._append_unique_url(candidate_urls, nested_url)

        if last_error is not None:
            raise ValueError(
                "URL 指向的是 Swagger/Redoc 页面，但自动拉取 OpenAPI 文档失败。"
                "请直接填写 /openapi.json、/swagger.json 或对应的接口文档 JSON/YAML 地址。"
            ) from last_error
        raise ValueError(
            "URL 指向的是 HTML 页面，但未能发现可解析的 OpenAPI/Swagger 文档。"
            "请直接填写 /openapi.json、/swagger.json 或对应的接口文档 JSON/YAML 地址。"
        )

    def _discover_openapi_candidate_urls(self, *, original_url: str, final_url: str, html: str) -> list[str]:
        candidates: list[str] = []
        base_url = final_url or original_url

        for pattern in (
            r"""(?:url|urls|spec-url|specUrl|configUrl|config-url)\s*[:=]\s*["']([^"']+)["']""",
            r"""["'](?:url|spec-url|specUrl|configUrl|config-url)["']\s*:\s*["']([^"']+)["']""",
            r"""href=["']([^"']+(?:openapi|swagger|api-docs)[^"']*)["']""",
            r"""src=["']([^"']+(?:swagger|redoc|api-docs)[^"']*)["']""",
        ):
            for match in re.finditer(pattern, html, flags=re.IGNORECASE):
                self._append_unique_url(candidates, urljoin(base_url, match.group(1)))

        parsed = urlparse(base_url)
        normalized_path = parsed.path.rstrip("/")
        conventional_paths = []
        path_lower = normalized_path.lower()
        ui_markers = ("/docs", "/redoc", "/swagger", "/swagger-ui", "/swagger-ui/index.html", "/doc.html", "/api-docs")
        if any(marker in path_lower for marker in ui_markers):
            parent = normalized_path
            for marker in ("/swagger-ui/index.html", "/swagger-ui", "/redoc", "/docs", "/swagger", "/doc.html"):
                marker_index = path_lower.find(marker)
                if marker_index >= 0:
                    parent = normalized_path[:marker_index]
                    break
            parent = parent.rstrip("/")
            conventional_paths.extend(self._openapi_conventional_paths(parent))
        conventional_paths.extend(self._openapi_conventional_paths(""))
        origin = f"{parsed.scheme}://{parsed.netloc}"
        for path in conventional_paths:
            self._append_unique_url(candidates, urljoin(f"{origin}/", path.lstrip("/")))
        return candidates

    def _openapi_conventional_paths(self, prefix: str) -> list[str]:
        prefix = prefix.rstrip("/")
        paths = [
            "/openapi.json",
            "/swagger.json",
            "/openapi.yaml",
            "/swagger.yaml",
            "/v3/api-docs",
            "/v3/api-docs/swagger-config",
            "/v2/api-docs",
            "/api-docs",
            "/swagger/v1/swagger.json",
            "/swagger/v2/swagger.json",
            "/api/openapi.json",
            "/api/swagger.json",
            "/api/v3/api-docs",
            "/api/v3/api-docs/swagger-config",
            "/api/v2/api-docs",
        ]
        if not prefix:
            return paths
        return [f"{prefix}{path}" for path in paths] + paths

    def _discover_openapi_candidate_urls_from_payload(self, fetched: dict[str, Any]) -> list[str]:
        text = self._decode_text(fetched.get("content", b""))
        if not text:
            return []
        stripped = text.lstrip()
        if not stripped.startswith("{"):
            return []
        try:
            payload = json.loads(text)
        except Exception:
            return []
        if not isinstance(payload, dict):
            return []

        base_url = str(fetched.get("final_url") or "")
        candidates: list[str] = []
        for key in ("url", "configUrl", "specUrl", "spec-url"):
            value = payload.get(key)
            if isinstance(value, str):
                self._append_unique_url(candidates, urljoin(base_url, value))
        urls = payload.get("urls")
        if isinstance(urls, list):
            for item in urls:
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    self._append_unique_url(candidates, urljoin(base_url, item["url"]))
                elif isinstance(item, str):
                    self._append_unique_url(candidates, urljoin(base_url, item))
        return candidates

    def _append_unique_url(self, items: list[str], value: str) -> None:
        normalized = value.strip()
        if normalized and normalized not in items:
            items.append(normalized)

    def _looks_like_api_document(self, fetched: dict[str, Any]) -> bool:
        content = fetched.get("content", b"")
        content_type = str(fetched.get("content_type") or "").split(";", 1)[0].strip().lower()
        final_url = str(fetched.get("final_url") or "")
        suffix = Path(urlparse(final_url).path).suffix.lower()
        text = self._decode_text(content)
        if not text:
            return False
        stripped = text.lstrip()
        if content_type == "application/json" or content_type.endswith("+json") or suffix == ".json" or stripped.startswith("{"):
            try:
                parsed = json.loads(text)
            except Exception:
                return False
            return isinstance(parsed, dict) and (
                "openapi" in parsed
                or "swagger" in parsed
                or ("info" in parsed and "item" in parsed)
                or "paths" in parsed
            )
        if (
            content_type in {"application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml"}
            or suffix in {".yaml", ".yml"}
            or stripped.lower().startswith(("openapi:", "swagger:"))
        ):
            lowered = stripped.lower()
            return "openapi:" in lowered or "swagger:" in lowered or "\npaths:" in lowered
        return False

    def _looks_like_html_document(self, fetched: dict[str, Any]) -> bool:
        content_type = str(fetched.get("content_type") or "").split(";", 1)[0].strip().lower()
        content = fetched.get("content", b"")
        text = self._decode_text(content[:100_000] if isinstance(content, bytes) else content)
        lowered = text[:5000].lower()
        return (
            content_type == "text/html"
            or "<!doctype html" in lowered
            or "<html" in lowered
            or "swagger-ui" in lowered
            or "redoc" in lowered
        )

    def _looks_like_discovery_text_document(self, fetched: dict[str, Any]) -> bool:
        content_type = str(fetched.get("content_type") or "").split(";", 1)[0].strip().lower()
        final_url = str(fetched.get("final_url") or "")
        suffix = Path(urlparse(final_url).path).suffix.lower()
        content = fetched.get("content", b"")
        text = self._decode_text(content[:100_000] if isinstance(content, bytes) else content)
        lowered = text[:5000].lower()
        return (
            content_type in {"text/html", "application/javascript", "text/javascript", "application/x-javascript"}
            or suffix in {".html", ".htm", ".js"}
            or "swaggeruibundle" in lowered
            or "swagger-ui" in lowered
            or "redoc.init" in lowered
            or "<redoc" in lowered
        )

    def _decode_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, (bytes, bytearray)):
            return ""
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                return bytes(content).decode(encoding)
            except UnicodeDecodeError:
                continue
        return ""

    def _derive_search_filters(self, query: str | None) -> tuple[str | None, str | None]:
        if not query:
            return None, None
        method_match = re.search(r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b", query, flags=re.IGNORECASE)
        path_match = re.search(r"(/[^\s`'\"，。；;]+)", query)
        method = method_match.group(1).upper() if method_match else None
        path = path_match.group(1).strip(".,，。；;") if path_match else None
        return method, path

    def _search_tokens(self, query: str | None) -> list[str]:
        if not query:
            return []
        lowered = query.lower()
        tokens = [
            token.strip()
            for token in re.split(r"[\s,，。；;:：`'\"()\[\]{}<>]+", lowered)
            if token.strip()
        ]
        return list(dict.fromkeys(tokens))

    def _text_matches_query(self, query: str | None, haystack: str) -> bool:
        normalized_query = self._normalize_optional_text(query)
        if not normalized_query:
            return True
        lowered = haystack.lower()
        query_lower = normalized_query.lower()
        if query_lower in lowered:
            return True
        tokens = self._search_tokens(normalized_query)
        return bool(tokens) and all(token in lowered for token in tokens)

    def _extract_markdown_endpoints(self, markdown: str) -> list[dict[str, Any]]:
        heading_pattern = re.compile(
            r"^###\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(.+?)\s*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )
        headings = list(heading_pattern.finditer(markdown or ""))
        endpoints: list[dict[str, Any]] = []
        for index, match in enumerate(headings):
            start = match.start()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
            section = markdown[start:end].strip()
            full_url_match = re.search(r"\*\*调用地址\*\*:\s*`?([^`\n]+)`?", section)
            summary_match = re.search(r"\*\*功能\*\*:\s*(.+)", section)
            endpoints.append(
                {
                    "method": match.group(1).upper(),
                    "path": match.group(2).strip().strip("`"),
                    "full_url": full_url_match.group(1).strip() if full_url_match else None,
                    "summary": summary_match.group(1).strip() if summary_match else None,
                    "section": section,
                }
            )
        return endpoints

    def _score_api_doc_match(
        self,
        *,
        query: str | None,
        tokens: list[str],
        haystack: str,
        record: ApiDocRecord,
        endpoint: dict[str, Any] | None,
        method: str | None,
        path: str | None,
    ) -> float:
        lowered = haystack.lower()
        score = 1.0
        if query and query.lower() in lowered:
            score += 50.0
        for token in tokens:
            score += min(lowered.count(token), 20)
        if endpoint is not None:
            score += 10.0
            endpoint_path = str(endpoint.get("path") or "").lower()
            endpoint_method = str(endpoint.get("method") or "").upper()
            if method and endpoint_method == method:
                score += 20.0
            if path:
                path_lower = path.lower()
                if endpoint_path == path_lower:
                    score += 40.0
                elif path_lower in endpoint_path:
                    score += 20.0
        title_lower = record.title.lower()
        filename_lower = record.filename.lower()
        for token in tokens:
            if token in title_lower:
                score += 8.0
            if token in filename_lower:
                score += 5.0
        return score

    def _build_search_excerpt(self, text: str, tokens: list[str], max_chars: int) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return ""
        max_chars = max(120, max_chars)
        lowered = normalized.lower()
        first_index = -1
        for token in tokens:
            index = lowered.find(token)
            if index >= 0 and (first_index < 0 or index < first_index):
                first_index = index
        if first_index < 0:
            return normalized[:max_chars]
        start = max(0, first_index - max_chars // 3)
        end = min(len(normalized), start + max_chars)
        excerpt = normalized[start:end]
        if start > 0:
            excerpt = "..." + excerpt
        if end < len(normalized):
            excerpt += "..."
        return excerpt

    def _suffix_for_content_type(self, content_type: str) -> str:
        normalized = content_type.split(";", 1)[0].strip().lower()
        if normalized == "application/json" or normalized.endswith("+json"):
            return ".json"
        if normalized in {"application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml"}:
            return ".yaml"
        if normalized == "text/markdown":
            return ".md"
        return mimetypes.guess_extension(normalized) or ""

    def _normalize_optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _clamp_int(self, value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))
