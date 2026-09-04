from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from src.application.mcp.host.connection_state import (
    McpCallResult,
    McpConnectionState,
    McpPromptInfo,
    McpResourceInfo,
    McpToolInfo,
)
from src.application.mcp.host.tool_bridge import McpToolBridge
from src.application.mcp.host.transports import SseMcpTransport, StdioMcpTransport, StreamableHttpMcpTransport
from src.application.mcp.security import StdioCommandGuard
from src.application.mcp.server_store import MCPServerStore
from src.core.config import Settings
from src.schemas.mcp_management import MCPServerRecord


class McpConnectionManager:
    def __init__(
        self,
        *,
        settings: Settings,
        mcp_server_store: MCPServerStore,
        tool_bridge: McpToolBridge | None = None,
    ) -> None:
        self._settings = settings
        self._mcp_server_store = mcp_server_store
        self._tool_bridge = tool_bridge
        self._guard = StdioCommandGuard.from_settings(settings)
        self._states: dict[str, McpConnectionState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._transports: dict[str, Any] = {}
        self._sessions: dict[str, Any] = {}
        self._tools: dict[str, list[McpToolInfo]] = {}
        self._resources: dict[str, list[McpResourceInfo]] = {}
        self._prompts: dict[str, list[McpPromptInfo]] = {}
        self._health_task: asyncio.Task | None = None
        self._closing = False

    async def startup(self) -> None:
        self._closing = False
        interval = float(getattr(self._settings, "mcp_health_check_interval_seconds", 0.0) or 0.0)
        if interval > 0 and self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop(interval))

    async def shutdown(self) -> None:
        self._closing = True
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        for server_key in list(self._states):
            await self.disconnect(server_key)

    async def connect(self, server: MCPServerRecord) -> McpConnectionState:
        server_key = self.server_key_for_record(server)
        lock = self._lock_for(server_key)
        async with lock:
            state = self._set_state(
                server_key,
                status="connecting",
                transport=server.transport,
                last_error=None,
            )
            try:
                await self._close_transport(server_key)
                transport = self._build_transport(server)
                session = await transport.open()
                await session.initialize()
                tools = self._normalize_tools(await session.list_tools())
                resources = await self._safe_list_resources(session)
                prompts = await self._safe_list_prompts(session)
                self._transports[server_key] = transport
                self._sessions[server_key] = session
                self._tools[server_key] = tools
                self._resources[server_key] = resources
                self._prompts[server_key] = prompts
                if self._tool_bridge is not None:
                    self._tool_bridge.sync_server_tools(server_key, tools)
                state = self._set_state(
                    server_key,
                    status="connected",
                    transport=server.transport,
                    tool_count=len(tools),
                    resource_count=len(resources),
                    prompt_count=len(prompts),
                    connected_at=datetime.now(timezone.utc),
                    last_health_at=datetime.now(timezone.utc),
                    last_error=None,
                )
                return state
            except Exception as exc:
                await self._close_transport(server_key)
                if self._tool_bridge is not None:
                    self._tool_bridge.remove_server_tools(server_key)
                self._tools[server_key] = []
                self._resources[server_key] = []
                self._prompts[server_key] = []
                state = self._set_state(
                    server_key,
                    status="failed",
                    transport=server.transport,
                    tool_count=0,
                    resource_count=0,
                    prompt_count=0,
                    last_error=str(exc),
                )
                raise

    async def disconnect(self, server_key: str) -> None:
        lock = self._lock_for(server_key)
        async with lock:
            await self._close_transport(server_key)
            self._tools.pop(server_key, None)
            self._resources.pop(server_key, None)
            self._prompts.pop(server_key, None)
            if self._tool_bridge is not None:
                self._tool_bridge.remove_server_tools(server_key)
            self._set_state(server_key, status="disconnected", tool_count=0, resource_count=0, prompt_count=0)

    async def reconnect(self, server_key: str) -> McpConnectionState:
        server = await self._record_for_server_key(server_key)
        self._set_state(server_key, status="reconnecting")
        return await self.connect(server)

    async def list_tools(self, server_key: str) -> list[McpToolInfo]:
        if server_key not in self._sessions:
            server = await self._record_for_server_key(server_key)
            await self.connect(server)
        return list(self._tools.get(server_key, []))

    async def list_resources(self, server_key: str) -> list[McpResourceInfo]:
        if server_key not in self._sessions:
            server = await self._record_for_server_key(server_key)
            await self.connect(server)
        return list(self._resources.get(server_key, []))

    async def list_prompts(self, server_key: str) -> list[McpPromptInfo]:
        if server_key not in self._sessions:
            server = await self._record_for_server_key(server_key)
            await self.connect(server)
        return list(self._prompts.get(server_key, []))

    async def call_tool(self, server_key: str, tool_name: str, arguments: dict[str, Any]) -> McpCallResult:
        if server_key not in self._sessions:
            server = await self._record_for_server_key(server_key)
            try:
                await self.connect(server)
            except Exception as exc:
                return McpCallResult(
                    ok=False,
                    summary=f"MCP server '{server_key}' connection failed.",
                    error=str(exc),
                )
        lock = self._lock_for(server_key)
        async with lock:
            session = self._sessions.get(server_key)
            if session is None:
                return McpCallResult(
                    ok=False,
                    summary=f"MCP server '{server_key}' is not connected.",
                    error="mcp_server_not_connected",
                )
            try:
                result = await session.call_tool(tool_name, arguments or {})
                payload = self._normalize_call_payload(result)
                self._set_state(server_key, status="connected", last_health_at=datetime.now(timezone.utc), last_error=None)
                return McpCallResult(
                    ok=not bool(payload.get("isError")),
                    summary=f"MCP tool '{tool_name}' executed.",
                    payload=payload,
                    error=str(payload.get("error") or "") or None,
                )
            except Exception as exc:
                self._set_state(server_key, status="degraded", last_error=str(exc))

        try:
            await self.reconnect(server_key)
        except Exception as reconnect_exc:
            return McpCallResult(
                ok=False,
                summary=f"MCP server '{server_key}' reconnect failed.",
                error=str(reconnect_exc),
            )
        return await self._call_after_reconnect(server_key, tool_name, arguments)

    def get_state(self, server_key: str) -> McpConnectionState:
        return self._states.get(server_key) or McpConnectionState(server_key=server_key)

    def list_states(self) -> list[McpConnectionState]:
        return list(self._states.values())

    @staticmethod
    def server_key_for_record(server: MCPServerRecord) -> str:
        return f"mcp:{server.id}"

    async def _call_after_reconnect(
        self,
        server_key: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpCallResult:
        lock = self._lock_for(server_key)
        async with lock:
            session = self._sessions.get(server_key)
            if session is None:
                return McpCallResult(ok=False, summary="MCP reconnect did not produce a session.", error="reconnect_failed")
            try:
                result = await session.call_tool(tool_name, arguments or {})
                payload = self._normalize_call_payload(result)
                return McpCallResult(
                    ok=not bool(payload.get("isError")),
                    summary=f"MCP tool '{tool_name}' executed after reconnect.",
                    payload=payload,
                    error=str(payload.get("error") or "") or None,
                )
            except Exception as exc:
                return McpCallResult(
                    ok=False,
                    summary=f"MCP tool '{tool_name}' failed after reconnect.",
                    error=str(exc),
                )

    async def _health_loop(self, interval_seconds: float) -> None:
        while not self._closing:
            await asyncio.sleep(interval_seconds)
            if self._closing:
                break
            await self.health_check_once()

    async def health_check_once(self) -> None:
        for server_key, state in list(self._states.items()):
            if state.status not in {"connected", "degraded"}:
                continue
            session = self._sessions.get(server_key)
            if session is None:
                continue
            try:
                response = await session.list_tools()
                tools = self._normalize_tools(response)
                resources = await self._safe_list_resources(session)
                prompts = await self._safe_list_prompts(session)
                self._tools[server_key] = tools
                self._resources[server_key] = resources
                self._prompts[server_key] = prompts
                if self._tool_bridge is not None:
                    self._tool_bridge.sync_server_tools(server_key, tools)
                self._set_state(
                    server_key,
                    status="connected",
                    tool_count=len(tools),
                    resource_count=len(resources),
                    prompt_count=len(prompts),
                    last_health_at=datetime.now(timezone.utc),
                    last_error=None,
                )
            except Exception as exc:
                self._set_state(server_key, status="degraded", last_error=str(exc))
                try:
                    await self.reconnect(server_key)
                except Exception:
                    continue

    def _build_transport(self, server: MCPServerRecord):
        config = self._server_config(server)
        transport = str(config.get("transport") or config.get("transport_type") or server.transport or "").strip().lower().replace("-", "_")
        if transport == "http":
            transport = "streamable_http"
        if not transport and config.get("command"):
            transport = "stdio"
        if not transport and (config.get("url") or config.get("endpoint_url")):
            transport = "streamable_http"
        if transport == "streamable_http":
            endpoint_url = self._text(config.get("url") or config.get("endpoint_url"))
            if not endpoint_url:
                raise ValueError("Streamable HTTP MCP server requires endpoint_url.")
            return StreamableHttpMcpTransport(
                endpoint_url=endpoint_url,
                headers=self._string_map(config.get("headers")),
                timeout_seconds=min(self._settings.model.llm_request_timeout_seconds, 45.0),
            )
        if transport == "sse":
            endpoint_url = self._text(config.get("url") or config.get("endpoint_url"))
            if not endpoint_url:
                raise ValueError("SSE MCP server requires endpoint_url.")
            return SseMcpTransport(
                endpoint_url=endpoint_url,
                headers=self._string_map(config.get("headers")),
                timeout_seconds=min(self._settings.model.llm_request_timeout_seconds, 45.0),
            )
        if transport == "stdio":
            executable_command = self._text(config.get("command"))
            args = self._string_list(config.get("args"))
            command = " ".join([executable_command or "", *args]).strip()
            executable, args = self._guard.validate(
                command=command,
                confirmed_at=server.confirmed_at.isoformat() if server.confirmed_at else "",
            )
            return StdioMcpTransport(
                command=executable,
                args=args,
                env=self._string_map(config.get("env")),
                cwd=self._text(config.get("cwd")),
            )
        raise ValueError(f"Unsupported MCP transport: {transport or 'unknown'}.")

    def _server_config(self, server: MCPServerRecord) -> dict[str, Any]:
        if isinstance(server.config, dict) and server.config:
            return dict(server.config)
        if server.transport == "stdio":
            return {
                "transport": "stdio",
                "command": server.command,
                "args": list(server.args),
                "env": dict(server.env),
                **({"cwd": server.cwd} if server.cwd else {}),
            }
        if server.transport == "sse":
            return {
                "transport": "sse",
                "url": server.endpoint_url,
                "headers": dict(server.headers),
            }
        return {
            "transport": "streamable_http",
            "url": server.endpoint_url,
            "headers": dict(server.headers),
        }

    def _text(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def _string_map(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items() if str(key).strip()}

    async def _record_for_server_key(self, server_key: str) -> MCPServerRecord:
        if not server_key.startswith("mcp:"):
            raise ValueError(f"Only MCP Host servers are managed by this connection manager: {server_key}.")
        server_id = server_key.split(":", 1)[1]
        return await self._mcp_server_store.get_server(server_id)

    def _lock_for(self, server_key: str) -> asyncio.Lock:
        lock = self._locks.get(server_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[server_key] = lock
        return lock

    def _set_state(self, server_key: str, **updates) -> McpConnectionState:
        state = self._states.get(server_key) or McpConnectionState(server_key=server_key)
        for key, value in updates.items():
            setattr(state, key, value)
        self._states[server_key] = state
        return state

    async def _close_transport(self, server_key: str) -> None:
        self._sessions.pop(server_key, None)
        transport = self._transports.pop(server_key, None)
        if transport is not None:
            await transport.close()

    def _normalize_tools(self, response: Any) -> list[McpToolInfo]:
        raw_tools = getattr(response, "tools", response)
        if isinstance(raw_tools, dict):
            raw_tools = raw_tools.get("tools", [])
        tools: list[McpToolInfo] = []
        for item in raw_tools or []:
            data = item.model_dump(mode="python") if hasattr(item, "model_dump") else item
            if not isinstance(data, dict):
                data = {
                    "name": getattr(item, "name", ""),
                    "description": getattr(item, "description", ""),
                    "inputSchema": getattr(item, "inputSchema", None) or getattr(item, "input_schema", None),
                }
            name = str(data.get("name") or "").strip()
            if not name:
                continue
            input_schema = data.get("inputSchema") or data.get("input_schema") or {}
            tools.append(
                McpToolInfo(
                    name=name,
                    description=str(data.get("description") or ""),
                    input_schema=input_schema if isinstance(input_schema, dict) else {},
                )
            )
        return tools

    async def _safe_list_resources(self, session: Any) -> list[McpResourceInfo]:
        if not hasattr(session, "list_resources"):
            return []
        try:
            return self._normalize_resources(await session.list_resources())
        except Exception:
            return []

    async def _safe_list_prompts(self, session: Any) -> list[McpPromptInfo]:
        if not hasattr(session, "list_prompts"):
            return []
        try:
            return self._normalize_prompts(await session.list_prompts())
        except Exception:
            return []

    def _normalize_resources(self, response: Any) -> list[McpResourceInfo]:
        raw_resources = getattr(response, "resources", response)
        if isinstance(raw_resources, dict):
            raw_resources = raw_resources.get("resources", [])
        resources: list[McpResourceInfo] = []
        for item in raw_resources or []:
            data = item.model_dump(mode="python") if hasattr(item, "model_dump") else item
            if not isinstance(data, dict):
                data = {
                    "uri": getattr(item, "uri", ""),
                    "name": getattr(item, "name", ""),
                    "description": getattr(item, "description", ""),
                    "mimeType": getattr(item, "mimeType", None) or getattr(item, "mime_type", None),
                }
            uri = str(data.get("uri") or "").strip()
            if not uri:
                continue
            resources.append(
                McpResourceInfo(
                    uri=uri,
                    name=str(data.get("name") or ""),
                    description=str(data.get("description") or ""),
                    mime_type=data.get("mimeType") or data.get("mime_type"),
                )
            )
        return resources

    def _normalize_prompts(self, response: Any) -> list[McpPromptInfo]:
        raw_prompts = getattr(response, "prompts", response)
        if isinstance(raw_prompts, dict):
            raw_prompts = raw_prompts.get("prompts", [])
        prompts: list[McpPromptInfo] = []
        for item in raw_prompts or []:
            data = item.model_dump(mode="python") if hasattr(item, "model_dump") else item
            if not isinstance(data, dict):
                data = {
                    "name": getattr(item, "name", ""),
                    "description": getattr(item, "description", ""),
                    "arguments": getattr(item, "arguments", []),
                }
            name = str(data.get("name") or "").strip()
            if not name:
                continue
            raw_arguments = data.get("arguments") if isinstance(data.get("arguments"), list) else []
            prompts.append(
                McpPromptInfo(
                    name=name,
                    description=str(data.get("description") or ""),
                    arguments=[item for item in raw_arguments if isinstance(item, dict)],
                )
            )
        return prompts

    def _normalize_call_payload(self, result: Any) -> dict[str, Any]:
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="python")
        if isinstance(result, dict):
            return result
        return {
            "content": getattr(result, "content", None),
            "structuredContent": getattr(result, "structuredContent", None),
            "isError": getattr(result, "isError", False),
        }
