from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from src.core.config import Settings
from src.schemas.integration import (
    IntegrationCreateRequest,
    IntegrationImportSourceDescriptor,
    IntegrationImportSourcesResponse,
    IntegrationRecord,
    IntegrationTestResponse,
    IntegrationUpdateRequest,
    IntegrationWorkspaceDescriptor,
)
from src.schemas.mcp_management import ResolvedImportDocument


class IntegrationCatalogService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._data_dir = (Path(__file__).resolve().parents[2] / settings.data_dir / "integrations").resolve()
        self._catalog_path = self._data_dir / "catalog.json"
        self._lock = asyncio.Lock()
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def list_integrations(self) -> list[IntegrationRecord]:
        async with self._lock:
            catalog = self._load_catalog()
        items = [IntegrationRecord.model_validate(item) for item in catalog]
        items = [item for item in items if item.kind == "api"]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    async def list_legacy_mcp_integrations(self) -> list[IntegrationRecord]:
        async with self._lock:
            catalog = self._load_catalog()
        items = [IntegrationRecord.model_validate(item) for item in catalog]
        return sorted([item for item in items if item.kind == "mcp"], key=lambda item: item.updated_at, reverse=True)

    async def get_integration(self, integration_id: str) -> IntegrationRecord:
        async with self._lock:
            catalog = self._load_catalog()
            item = self._find_item(catalog, integration_id)
        record = IntegrationRecord.model_validate(item)
        if record.kind != "api":
            raise ValueError("MCP servers must be managed through /registry/mcp/managed.")
        return record

    async def create_integration(self, payload: IntegrationCreateRequest) -> IntegrationRecord:
        if payload.kind != "api":
            raise ValueError("MCP servers must be managed through /registry/mcp/managed.")
        now = datetime.now(timezone.utc)
        record = IntegrationRecord(
            id=str(uuid4()),
            name=payload.name.strip(),
            kind=payload.kind,
            enabled=payload.enabled,
            description=self._normalize_optional_text(payload.description),
            project_name=self._normalize_optional_text(payload.project_name),
            document_url=self._normalize_optional_text(payload.document_url),
            transport=payload.transport,
            endpoint_url=self._normalize_optional_text(payload.endpoint_url),
            command=self._normalize_optional_text(payload.command),
            capabilities=self._normalize_string_list(payload.capabilities),
            headers=self._normalize_string_map(payload.headers),
            env=self._normalize_string_map(payload.env),
            base_url=self._normalize_optional_text(payload.base_url),
            auth_type=payload.auth_type,
            auth_config=self._normalize_string_map(payload.auth_config),
            metadata=dict(payload.metadata),
            created_at=now,
            updated_at=now,
        )
        self._validate_record(record)

        async with self._lock:
            catalog = self._load_catalog()
            catalog.append(record.model_dump(mode="json"))
            self._save_catalog(catalog)
        return record

    async def update_integration(self, integration_id: str, payload: IntegrationUpdateRequest) -> IntegrationRecord:
        async with self._lock:
            catalog = self._load_catalog()
            item = self._find_item(catalog, integration_id)
            if str(item.get("kind") or "") != "api":
                raise ValueError("MCP servers must be managed through /registry/mcp/managed.")
            updated = dict(item)

            for field in ("name", "description", "project_name", "document_url", "endpoint_url", "command", "base_url"):
                value = getattr(payload, field)
                if value is not None:
                    updated[field] = self._normalize_optional_text(value)

            for field in ("transport", "auth_type", "enabled"):
                value = getattr(payload, field)
                if value is not None:
                    updated[field] = value

            if payload.capabilities is not None:
                updated["capabilities"] = self._normalize_string_list(payload.capabilities)
            if payload.headers is not None:
                updated["headers"] = self._normalize_string_map(payload.headers)
            if payload.env is not None:
                updated["env"] = self._normalize_string_map(payload.env)
            if payload.auth_config is not None:
                updated["auth_config"] = self._normalize_string_map(payload.auth_config)
            if payload.metadata is not None:
                updated["metadata"] = dict(payload.metadata)

            updated["updated_at"] = datetime.now(timezone.utc).isoformat()
            record = IntegrationRecord.model_validate(updated)
            self._validate_record(record)
            item.clear()
            item.update(record.model_dump(mode="json"))
            self._save_catalog(catalog)

        return record

    async def delete_integration(self, integration_id: str) -> dict[str, Any]:
        async with self._lock:
            catalog = self._load_catalog()
            item = self._find_item(catalog, integration_id)
            if str(item.get("kind") or "") != "api":
                raise ValueError("MCP servers must be managed through /registry/mcp/managed.")
            catalog = [item for item in catalog if str(item.get("id") or "") != integration_id]
            self._save_catalog(catalog)
        return {"ok": True, "deleted_id": integration_id}

    async def test_integration(self, integration_id: str) -> IntegrationTestResponse:
        record = await self.get_integration(integration_id)
        return await self._test_api_integration(record)

    async def list_import_sources(
        self,
        integration_id: str,
        *,
        workspace_id: str | None = None,
    ) -> IntegrationImportSourcesResponse:
        record = await self.get_integration(integration_id)
        return self.describe_import_sources(record)

    async def resolve_import_document(
        self,
        integration_id: str,
        *,
        override_document_url: str | None = None,
        import_source_id: str | None = None,
        workspace_id: str | None = None,
    ) -> ResolvedImportDocument:
        record = await self.get_integration(integration_id)
        normalized_source_id = self._normalize_optional_text(import_source_id)

        url, headers, auth = self.build_document_import_request(
            record,
            override_document_url=override_document_url,
            import_source_id=import_source_id,
            workspace_id=workspace_id,
        )
        return ResolvedImportDocument(
            mode="url",
            document_url=url,
            headers=headers,
            auth=auth,
            project_name=record.project_name,
            title=record.name,
        )

    def describe_import_sources(self, record: IntegrationRecord) -> IntegrationImportSourcesResponse:
        workspaces: list[IntegrationWorkspaceDescriptor] = []
        sources: list[IntegrationImportSourceDescriptor] = []
        metadata = record.metadata if isinstance(record.metadata, dict) else {}

        raw_workspace_catalog = metadata.get("workspace_catalog")
        if isinstance(raw_workspace_catalog, list):
            for workspace_index, workspace in enumerate(raw_workspace_catalog, start=1):
                if not isinstance(workspace, dict):
                    continue
                workspace_id = self._normalize_optional_text(workspace.get("id")) or f"workspace-{workspace_index}"
                workspace_name = self._normalize_optional_text(workspace.get("name")) or workspace_id
                workspace_description = self._normalize_optional_text(workspace.get("description"))
                workspace_project_name = self._normalize_optional_text(workspace.get("project_name")) or record.project_name
                document_items = workspace.get("documents")
                document_count = 0
                if isinstance(document_items, list):
                    for source_index, source in enumerate(document_items, start=1):
                        descriptor = self._build_import_source_descriptor(
                            source=source,
                            default_id=f"{workspace_id}-source-{source_index}",
                            default_label=f"{workspace_name} 文档 {source_index}",
                            default_kind="workspace_document",
                            workspace_id=workspace_id,
                            workspace_name=workspace_name,
                            default_project_name=workspace_project_name,
                        )
                        if descriptor is None:
                            continue
                        sources.append(descriptor)
                        document_count += 1

                workspaces.append(
                    IntegrationWorkspaceDescriptor(
                        id=workspace_id,
                        name=workspace_name,
                        description=workspace_description,
                        project_name=workspace_project_name,
                        document_count=document_count,
                    )
                )

        raw_import_sources = metadata.get("import_sources")
        if isinstance(raw_import_sources, list):
            for source_index, source in enumerate(raw_import_sources, start=1):
                descriptor = self._build_import_source_descriptor(
                    source=source,
                    default_id=f"source-{source_index}",
                    default_label=f"{record.name} 文档 {source_index}",
                    default_kind="document_url",
                    workspace_id=None,
                    workspace_name=None,
                    default_project_name=record.project_name,
                )
                if descriptor is not None:
                    sources.append(descriptor)

        default_document_url = self._normalize_optional_text(record.document_url)
        if default_document_url:
            sources.insert(
                0,
                IntegrationImportSourceDescriptor(
                    id="default-document-url",
                    label="默认文档地址",
                    document_url=default_document_url,
                    kind="document_url",
                    summary="使用接入配置中的默认文档地址",
                    project_name=record.project_name,
                ),
            )

        return IntegrationImportSourcesResponse(
            integration_id=record.id,
            kind=record.kind,
            supports_workspace_selection=bool(workspaces),
            workspaces=workspaces,
            sources=sources,
        )

    def build_document_import_request(
        self,
        record: IntegrationRecord,
        *,
        override_document_url: str | None = None,
        import_source_id: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[str, dict[str, str], tuple[str, str] | None]:
        import_sources = self.describe_import_sources(record)
        resolved_url = self._resolve_import_document_url(
            record=record,
            import_sources=import_sources,
            override_document_url=override_document_url,
            import_source_id=import_source_id,
            workspace_id=workspace_id,
        )

        headers = dict(record.headers)
        auth_tuple: tuple[str, str] | None = None
        if record.kind == "api":
            auth_type = record.auth_type
            auth_config = record.auth_config
            if auth_type == "bearer":
                token = self._normalize_optional_text(auth_config.get("token"))
                if token:
                    headers.setdefault("Authorization", f"Bearer {token}")
            elif auth_type == "api_key":
                header_name = self._normalize_optional_text(auth_config.get("header_name")) or "X-API-Key"
                token = self._normalize_optional_text(auth_config.get("token"))
                if token:
                    headers.setdefault(header_name, token)
            elif auth_type == "basic":
                username = self._normalize_optional_text(auth_config.get("username"))
                password = self._normalize_optional_text(auth_config.get("password"))
                if username is not None and password is not None:
                    auth_tuple = (username, password)

        return resolved_url, headers, auth_tuple

    async def _test_api_integration(self, record: IntegrationRecord) -> IntegrationTestResponse:
        target_url = record.document_url or record.base_url
        if not target_url:
            return IntegrationTestResponse(
                ok=False,
                message="API 接入缺少文档地址或基础地址，无法测试。",
                integration_id=record.id,
            )

        headers, auth_tuple = self.build_document_import_request(record)[1:]
        start = time.perf_counter()
        try:
            response = await self._send_api_probe(target_url, headers=headers, auth=auth_tuple)
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            response.raise_for_status()
        except Exception as exc:
            return IntegrationTestResponse(
                ok=False,
                message=f"API 接入测试失败：{exc}",
                integration_id=record.id,
                target_url=target_url,
            )

        return IntegrationTestResponse(
            ok=True,
            message="API 接入测试成功。",
            integration_id=record.id,
            target_url=target_url,
            status_code=response.status_code,
            latency_ms=latency_ms,
            preview=self._response_preview(response),
        )

    async def _test_mcp_integration(self, record: IntegrationRecord) -> IntegrationTestResponse:
        if record.transport == "stdio":
            return IntegrationTestResponse(
                ok=True,
                message="MCP stdio 接入配置已保存。实际连通性需要在运行时宿主环境中验证。",
                integration_id=record.id,
                target_url=record.command,
            )

        target_url = record.endpoint_url or record.document_url
        if not target_url:
            return IntegrationTestResponse(
                ok=False,
                message="MCP 接入缺少 Endpoint URL 或文档地址，无法测试。",
                integration_id=record.id,
            )

        if record.transport == "websocket":
            return IntegrationTestResponse(
                ok=True,
                message="WebSocket MCP 接入配置已保存。当前仅完成配置校验，运行时再进行真实握手。",
                integration_id=record.id,
                target_url=target_url,
            )

        initialize_headers = self._build_mcp_initialize_headers(record.headers)
        start = time.perf_counter()
        try:
            initialize_response = await self._send_mcp_initialize(target_url, headers=initialize_headers)
            initialize_latency_ms = round((time.perf_counter() - start) * 1000, 1)
        except Exception as exc:
            return IntegrationTestResponse(
                ok=False,
                message=f"MCP 接入测试失败：initialize 握手请求未完成，{exc}",
                integration_id=record.id,
                target_url=target_url,
            )

        if 200 <= initialize_response.status_code < 300:
            return IntegrationTestResponse(
                ok=True,
                message="MCP HTTP 接入测试成功（initialize 握手已通过）。",
                integration_id=record.id,
                target_url=target_url,
                status_code=initialize_response.status_code,
                latency_ms=initialize_latency_ms,
                preview=self._response_preview(initialize_response),
            )

        if self._should_fallback_to_sse(initialize_response.status_code):
            sse_headers = self._build_mcp_sse_headers(record.headers)
            fallback_start = time.perf_counter()
            try:
                sse_response = await self._send_mcp_sse_probe(target_url, headers=sse_headers)
                sse_latency_ms = round((time.perf_counter() - fallback_start) * 1000, 1)
            except Exception as exc:
                return IntegrationTestResponse(
                    ok=False,
                    message=(
                        "MCP 接入测试失败：initialize 握手返回"
                        f" {initialize_response.status_code}，且 SSE 兼容探测也失败：{exc}"
                    ),
                    integration_id=record.id,
                    target_url=target_url,
                    status_code=initialize_response.status_code,
                    preview=self._response_preview(initialize_response),
                )

            if 200 <= sse_response.status_code < 300:
                return IntegrationTestResponse(
                    ok=True,
                    message="MCP HTTP 接入测试成功（initialize 不可用，SSE 兼容探测已通过）。",
                    integration_id=record.id,
                    target_url=target_url,
                    status_code=sse_response.status_code,
                    latency_ms=sse_latency_ms,
                    preview=self._response_preview(sse_response),
                )

            return IntegrationTestResponse(
                ok=False,
                message=(
                    "MCP 接入测试失败：initialize 握手返回"
                    f" {initialize_response.status_code}，SSE 兼容探测返回 {sse_response.status_code}。"
                ),
                integration_id=record.id,
                target_url=target_url,
                status_code=sse_response.status_code,
                preview=self._response_preview(sse_response) or self._response_preview(initialize_response),
            )

        return IntegrationTestResponse(
            ok=False,
            message=f"MCP 接入测试失败：initialize 握手未通过，状态码 {initialize_response.status_code}。",
            integration_id=record.id,
            target_url=target_url,
            status_code=initialize_response.status_code,
            latency_ms=initialize_latency_ms,
            preview=self._response_preview(initialize_response),
        )

    async def _send_api_probe(
        self,
        target_url: str,
        *,
        headers: dict[str, str],
        auth: tuple[str, str] | None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=min(self._settings.model.llm_request_timeout_seconds, 30.0),
            follow_redirects=True,
        ) as client:
            return await client.get(target_url, headers=headers, auth=auth)

    async def _send_mcp_initialize(self, target_url: str, *, headers: dict[str, str]) -> httpx.Response:
        payload = {
            "jsonrpc": "2.0",
            "id": "connectivity-check",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "enterprise-ai-qa-agent",
                    "version": "0.2.0",
                },
            },
        }
        async with httpx.AsyncClient(
            timeout=min(self._settings.model.llm_request_timeout_seconds, 30.0),
            follow_redirects=True,
        ) as client:
            return await client.post(target_url, headers=headers, json=payload)

    async def _send_mcp_sse_probe(self, target_url: str, *, headers: dict[str, str]) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=min(self._settings.model.llm_request_timeout_seconds, 30.0),
            follow_redirects=True,
        ) as client:
            return await client.get(target_url, headers=headers)

    def _build_mcp_initialize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        request_headers = dict(headers)
        request_headers.setdefault("Accept", "application/json, text/event-stream")
        request_headers.setdefault("Content-Type", "application/json")
        return request_headers

    def _build_mcp_sse_headers(self, headers: dict[str, str]) -> dict[str, str]:
        request_headers = dict(headers)
        request_headers["Accept"] = "text/event-stream"
        return request_headers

    def _should_fallback_to_sse(self, status_code: int) -> bool:
        return status_code in {404, 405, 406, 415}

    def _response_preview(self, response: httpx.Response) -> str | None:
        text = response.text if response.text else ""
        preview = text.strip()[:180]
        return preview or None

    def _resolve_import_document_url(
        self,
        *,
        record: IntegrationRecord,
        import_sources: IntegrationImportSourcesResponse,
        override_document_url: str | None,
        import_source_id: str | None,
        workspace_id: str | None,
    ) -> str:
        normalized_override = self._normalize_optional_text(override_document_url)
        normalized_source_id = self._normalize_optional_text(import_source_id)
        normalized_workspace_id = self._normalize_optional_text(workspace_id)

        if normalized_source_id:
            for source in import_sources.sources:
                if source.id != normalized_source_id:
                    continue
                if normalized_workspace_id and source.workspace_id and source.workspace_id != normalized_workspace_id:
                    raise ValueError("所选导入源不属于当前工作区。")
                return source.document_url
            raise ValueError("未找到所选的导入源，请重新选择。")

        if normalized_override:
            return normalized_override

        if import_sources.supports_workspace_selection:
            raise ValueError("当前 MCP 接入需要先选择工作区和接口文档，不能直接导入。")

        default_url = self._normalize_optional_text(record.document_url)
        if default_url:
            return default_url

        raise ValueError("当前接入未配置可导入的文档源。")

    def _build_import_source_descriptor(
        self,
        *,
        source: Any,
        default_id: str,
        default_label: str,
        default_kind: str,
        workspace_id: str | None,
        workspace_name: str | None,
        default_project_name: str | None,
    ) -> IntegrationImportSourceDescriptor | None:
        if not isinstance(source, dict):
            return None
        document_url = self._normalize_optional_text(source.get("document_url") or source.get("url"))
        if not document_url:
            return None

        descriptor_id = self._normalize_optional_text(source.get("id")) or default_id
        label = self._normalize_optional_text(source.get("label") or source.get("name")) or default_label
        kind = self._normalize_optional_text(source.get("kind")) or default_kind
        summary = self._normalize_optional_text(source.get("summary") or source.get("description"))
        project_name = self._normalize_optional_text(source.get("project_name")) or default_project_name

        return IntegrationImportSourceDescriptor(
            id=descriptor_id,
            label=label,
            document_url=document_url,
            kind=kind,
            summary=summary,
            project_name=project_name,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
        )

    def _validate_record(self, record: IntegrationRecord) -> None:
        if not record.name:
            raise ValueError("接入名称不能为空。")
        if record.kind == "mcp":
            if record.transport == "stdio" and not record.command:
                raise ValueError("stdio 类型的 MCP 接入必须填写启动命令。")
            if record.transport in {"http", "websocket"} and not (record.endpoint_url or record.document_url):
                raise ValueError("HTTP/WebSocket 类型的 MCP 接入必须填写 Endpoint URL 或文档地址。")
        if record.kind == "api" and not (record.base_url or record.document_url):
            raise ValueError("API 接入至少需要填写基础地址或文档地址。")

    def _load_catalog(self) -> list[dict[str, Any]]:
        if not self._catalog_path.exists():
            return []
        try:
            raw = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return raw if isinstance(raw, list) else []

    def _save_catalog(self, catalog: list[dict[str, Any]]) -> None:
        self._catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_item(self, catalog: list[dict[str, Any]], integration_id: str) -> dict[str, Any]:
        for item in catalog:
            if str(item.get("id") or "") == integration_id:
                return item
        raise ValueError(f"未找到第三方接入：{integration_id}")

    def _normalize_optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _normalize_string_list(self, values: list[str]) -> list[str]:
        normalized = [item.strip() for item in values if str(item).strip()]
        return list(dict.fromkeys(normalized))

    def _normalize_string_map(self, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, item in value.items():
            normalized_key = str(key).strip()
            normalized_value = str(item).strip()
            if normalized_key and normalized_value:
                normalized[normalized_key] = normalized_value
        return normalized

    def _derive_label_from_url(self, url: str) -> str:
        path = urlparse(url).path
        name = Path(path).name.strip()
        return name or url
