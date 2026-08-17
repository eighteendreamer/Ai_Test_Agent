from __future__ import annotations

from collections import defaultdict

from src.application.mcp.manager_service import MCPManagerService
from src.application.security.command_profiles import get_profile_registry
from src.application.security.tool_catalog import FAMILY_RUNNER_MAP, SURFACE_FAMILY_MAP
from src.registry.agents import AgentRegistry
from src.registry.mcp import MCPRegistry
from src.registry.modes import ModeRegistry
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.schemas.session import RuntimeMode, SessionMode


class RegistryService:
    def __init__(
        self,
        agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
        model_registry: ModelRegistry,
        skill_registry: SkillRegistry,
        mcp_registry: MCPRegistry,
        mode_registry: ModeRegistry,
        mcp_manager_service: MCPManagerService | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._tool_registry = tool_registry
        self._model_registry = model_registry
        self._skill_registry = skill_registry
        self._mcp_registry = mcp_registry
        self._mode_registry = mode_registry
        self._mcp_manager_service = mcp_manager_service

    def list_agents(self):
        return self._agent_registry.list()

    def list_tools(self):
        return self._tool_registry.list()

    def list_tool_summaries(self):
        return self._tool_registry.list_summaries()

    def get_tool(self, key: str):
        return self._tool_registry.get(key)

    def search_tools(
        self,
        query: str,
        limit: int = 10,
        include_schema: bool = False,
    ):
        return self._tool_registry.search(
            query=query,
            limit=limit,
            include_schema=include_schema,
        )

    def list_models(self):
        return self._model_registry.list()

    def list_model_configs(self):
        return self._model_registry.list_configs()

    def list_skills(self):
        return self._skill_registry.list()

    def list_mcp_servers(self):
        return self._mcp_registry.list()

    async def list_managed_mcp_servers(self):
        if self._mcp_manager_service is None:
            return self._mcp_registry.list()
        return await self._mcp_manager_service.list_managed_servers()

    def list_mcp_providers(self):
        if self._mcp_manager_service is None:
            return []
        return self._mcp_manager_service.list_providers()

    async def list_managed_mcp_tools(self, server_key: str):
        if self._mcp_manager_service is None:
            raise ValueError("Managed MCP service is not configured.")
        return await self._mcp_manager_service.list_server_tools(server_key)

    async def list_managed_mcp_resources(self, server_key: str):
        if self._mcp_manager_service is None:
            raise ValueError("Managed MCP service is not configured.")
        return await self._mcp_manager_service.list_server_resources(server_key)

    async def list_managed_mcp_prompts(self, server_key: str):
        if self._mcp_manager_service is None:
            raise ValueError("Managed MCP service is not configured.")
        return await self._mcp_manager_service.list_server_prompts(server_key)

    async def test_managed_mcp_server(self, server_key: str):
        if self._mcp_manager_service is None:
            raise ValueError("Managed MCP service is not configured.")
        return await self._mcp_manager_service.test_server(server_key)

    async def call_managed_mcp_tool(self, server_key: str, tool_name: str, arguments: dict[str, object]):
        if self._mcp_manager_service is None:
            raise ValueError("Managed MCP service is not configured.")
        return await self._mcp_manager_service.call_server_tool(
            server_key,
            tool_name=tool_name,
            arguments=arguments,
        )

    def list_modes(self):
        return self._mode_registry.list()

    def list_security_profiles(self) -> dict[str, object]:
        registry = get_profile_registry()
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for p in registry.list_all():
            grouped[p.tool_family].append({
                "profile_key": p.profile_key,
                "tool_name": p.tool_name,
                "description": p.description,
                "tool_family": p.tool_family,
                "surface_types": list(p.surface_types),
                "allowed_arguments": list(p.allowed_arguments),
                "risk_level": p.risk_level,
                "requires_approval": p.requires_approval,
                "timeout_seconds": p.timeout_seconds,
                "parser_key": p.parser_key,
                "artifact_policy": p.artifact_policy,
                "notes": p.notes,
                "verification_capabilities": registry.verification_capabilities(
                    p.profile_key
                ),
            })
        families = [
            {
                "family": family,
                "runner_key": FAMILY_RUNNER_MAP.get(family, "security-scan-runner"),
                "profiles": profiles,
            }
            for family, profiles in grouped.items()
        ]
        return {
            "families": families,
            "surface_family_map": dict(SURFACE_FAMILY_MAP),
            "family_runner_map": dict(FAMILY_RUNNER_MAP),
            "total_count": sum(len(f["profiles"]) for f in families),
        }

    def framework_summary(self) -> dict[str, object]:
        return {
            "session_modes": [item.value for item in SessionMode],
            "runtime_modes": [item.value for item in RuntimeMode],
            "agent_count": len(self._agent_registry.list()),
            "tool_count": len(self._tool_registry.list()),
            "model_count": len(self._model_registry.list()),
            "skill_count": len(self._skill_registry.list()),
            "mcp_count": len(self._mcp_registry.list()),
            "mode_count": len(self._mode_registry.list()),
        }
