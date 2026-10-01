from __future__ import annotations

from datetime import datetime, timezone

from src.application.mcp.errors import (
    MCPManagementUnavailableError,
    MCPManagementValidationError,
    MCPServerNotFoundError,
)
from src.application.mcp.host.connection_manager import McpConnectionManager
from src.application.mcp.runtime_manager import MCPRuntimeManager
from src.application.mcp.server_store import MCPServerStore
from src.registry.mcp import MCPRegistry
from src.schemas.mcp_management import (
    ManagedMCPPromptsResponse,
    ManagedMCPResourcesResponse,
    ManagedMCPServerDescriptor,
    ManagedMCPTestResponse,
    ManagedMCPToolCallResponse,
    ManagedMCPToolsResponse,
    MCPProviderDescriptor,
    MCPServerCreateRequest,
    MCPServerImportRequest,
    MCPServerImportResponse,
    MCPServerRecord,
    MCPServerUpdateRequest,
)


class MCPManagerService:
    def __init__(
        self,
        *,
        builtin_registry: MCPRegistry,
        mcp_server_store: MCPServerStore,
        runtime_manager: MCPRuntimeManager | None = None,
        connection_manager: McpConnectionManager | None = None,
    ) -> None:
        self._builtin_registry = builtin_registry
        self._mcp_server_store = mcp_server_store
        self._runtime_manager = runtime_manager
        self._connection_manager = connection_manager

    async def list_managed_servers(self) -> list[ManagedMCPServerDescriptor]:
        builtin = [self._from_builtin(server) for server in self._builtin_registry.list()]
        external = [self._from_external(item) for item in await self._mcp_server_store.list_servers()]
        return builtin + external

    def list_builtin_servers(self) -> list[ManagedMCPServerDescriptor]:
        return [self._from_builtin(server) for server in self._builtin_registry.list()]

    async def list_external_servers(self) -> list[ManagedMCPServerDescriptor]:
        return [self._from_external(item) for item in await self._mcp_server_store.list_servers()]

    def list_providers(self) -> list[MCPProviderDescriptor]:
        return []

    async def create_server(self, payload: MCPServerCreateRequest) -> ManagedMCPServerDescriptor:
        record = await self._mcp_server_store.create_server(payload)
        return self._from_external(record)

    async def import_servers(self, payload: MCPServerImportRequest) -> MCPServerImportResponse:
        return await self._mcp_server_store.import_servers(payload)

    async def update_server(self, server_key: str, payload: MCPServerUpdateRequest) -> ManagedMCPServerDescriptor:
        record = await self._record_for_server_key(server_key)
        updated = await self._mcp_server_store.update_server(record.id, payload)
        if self._connection_manager is not None:
            await self._connection_manager.disconnect(server_key)
        return self._from_external(updated)

    async def delete_server(self, server_key: str) -> dict[str, object]:
        record = await self._record_for_server_key(server_key)
        if self._connection_manager is not None:
            await self._connection_manager.disconnect(server_key)
        return await self._mcp_server_store.delete_server(record.id)

    async def confirm_stdio_server(self, server_key: str) -> ManagedMCPServerDescriptor:
        record = await self._record_for_server_key(server_key)
        updated = await self._mcp_server_store.update_server(
            record.id,
            MCPServerUpdateRequest(confirmed_at=datetime.now(timezone.utc)),
        )
        return self._from_external(updated)

    async def reconnect_server(self, server_key: str) -> ManagedMCPServerDescriptor:
        record = await self._record_for_server_key(server_key)
        if not record.enabled:
            raise MCPManagementValidationError("Cannot reconnect a disabled MCP server.")
        if self._connection_manager is None:
            raise MCPManagementUnavailableError("MCP Host connection manager is not configured.")
        try:
            await self._connection_manager.reconnect(server_key)
        except Exception:
            pass
        return self._from_external(record)

    async def list_server_tools(self, server_key: str) -> ManagedMCPToolsResponse:
        runtime_manager = self._require_runtime_manager()
        descriptor = await self._resolve_managed_server(server_key)
        return await runtime_manager.list_tools(descriptor)

    async def list_server_resources(self, server_key: str) -> ManagedMCPResourcesResponse:
        runtime_manager = self._require_runtime_manager()
        descriptor = await self._resolve_managed_server(server_key)
        return await runtime_manager.list_resources(descriptor)

    async def list_server_prompts(self, server_key: str) -> ManagedMCPPromptsResponse:
        runtime_manager = self._require_runtime_manager()
        descriptor = await self._resolve_managed_server(server_key)
        return await runtime_manager.list_prompts(descriptor)

    async def test_server(self, server_key: str) -> ManagedMCPTestResponse:
        runtime_manager = self._require_runtime_manager()
        descriptor = await self._resolve_managed_server(server_key)
        return await runtime_manager.test_server(descriptor)

    async def call_server_tool(
        self,
        server_key: str,
        *,
        tool_name: str,
        arguments: dict[str, object],
    ) -> ManagedMCPToolCallResponse:
        runtime_manager = self._require_runtime_manager()
        descriptor = await self._resolve_managed_server(server_key)
        return await runtime_manager.call_tool(descriptor, tool_name=tool_name, arguments=arguments)

    def _from_builtin(self, server) -> ManagedMCPServerDescriptor:
        return ManagedMCPServerDescriptor(
            key=server.key,
            name=server.name,
            summary=server.summary,
            transport=server.transport,
            status=server.status,
            capabilities=list(server.capabilities),
            enabled=server.enabled,
            purpose=server.summary,
            config={},
            supported_protocols=[server.transport],
            source_kind="builtin",
            supports_workspace_selection=False,
            supports_document_import=False,
            metadata={},
        )

    def _from_external(self, server: MCPServerRecord) -> ManagedMCPServerDescriptor:
        status = "enabled" if server.enabled else "disabled"
        connection_state_payload = None
        server_key = McpConnectionManager.server_key_for_record(server)
        if self._runtime_manager is not None:
            connection_state = self._runtime_manager.get_connection_state(server_key)
            if connection_state is not None:
                status = connection_state.status
                connection_state_payload = connection_state.to_payload()
        summary = server.purpose or server.description or f"External MCP server via {server.transport} transport."
        return ManagedMCPServerDescriptor(
            key=server_key,
            name=server.name,
            summary=summary,
            transport=server.transport,
            status=status,
            capabilities=list(server.capabilities),
            enabled=server.enabled,
            purpose=server.purpose or server.description,
            config=dict(server.config),
            supported_protocols=list(server.supported_protocols or [server.transport]),
            source_kind="external",
            provider_key=None,
            provider_name=None,
            integration_id=None,
            project_name=server.project_name,
            endpoint_url=server.endpoint_url,
            document_url=server.document_url,
            supports_workspace_selection=False,
            supports_document_import=False,
            metadata={
                **dict(server.metadata),
                "server_id": server.id,
                "cwd": server.cwd,
                "supported_protocols": list(server.supported_protocols or [server.transport]),
                "confirmed_at": server.confirmed_at.isoformat() if server.confirmed_at else None,
                "credential_summary": self._credential_summary(server),
                "resource_count": connection_state_payload.get("resource_count", 0) if connection_state_payload else 0,
                "prompt_count": connection_state_payload.get("prompt_count", 0) if connection_state_payload else 0,
                **({"connection_state": connection_state_payload} if connection_state_payload else {}),
            },
        )

    async def _resolve_managed_server(self, server_key: str) -> ManagedMCPServerDescriptor:
        builtin = self._builtin_registry.get(server_key)
        if builtin is not None:
            return self._from_builtin(builtin)

        if server_key.startswith("mcp:"):
            return self._from_external(await self._record_for_server_key(server_key))

        raise MCPServerNotFoundError("MCP server not found.")

    async def _record_for_server_key(self, server_key: str) -> MCPServerRecord:
        if not server_key.startswith("mcp:"):
            raise MCPManagementValidationError("Only external MCP Host servers support this operation.")
        return await self._mcp_server_store.get_server(server_key.split(":", 1)[1])

    def _require_runtime_manager(self) -> MCPRuntimeManager:
        if self._runtime_manager is None:
            raise MCPManagementUnavailableError("MCP runtime manager is not configured.")
        return self._runtime_manager

    def _credential_summary(self, server: MCPServerRecord) -> dict[str, object]:
        config = server.config or {}
        headers = config.get("headers") if isinstance(config.get("headers"), dict) else server.headers
        env = config.get("env") if isinstance(config.get("env"), dict) else server.env
        return {
            "headers": sorted(str(key) for key in headers.keys()),
            "env": sorted(str(key) for key in env.keys()),
            "has_command": bool(config.get("command") or server.command),
            "has_cwd": bool(config.get("cwd") or server.cwd),
        }
