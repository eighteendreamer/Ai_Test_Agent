from __future__ import annotations

from src.application.memory_runtime_service import MemoryRuntimeService
from src.application.mcp_runtime_service import MCPRuntimeService
from src.application.skill_runtime_service import SkillRuntimeService
from src.graph.state import AgentGraphState
from src.registry.agents import AgentRegistry
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.execution_logging import append_graph_event


def build_router_node(
    agent_registry: AgentRegistry,
    tool_registry: ToolRegistry,
    model_registry: ModelRegistry,
    skill_registry: SkillRegistry,
    skill_runtime_service: SkillRuntimeService,
    mcp_runtime_service: MCPRuntimeService,
    memory_runtime_service: MemoryRuntimeService | None = None,
):
    async def router(state: AgentGraphState) -> AgentGraphState:
        requested_agent = state["selected_agent_key"] or "auto"
        requested_model = state["selected_model_key"] or state["preferred_model"] or "auto"
        agent = agent_registry.resolve_for_message(
            message=state["user_message"],
            explicit_key=state["selected_agent_key"] or None,
        )

        selected_model = model_registry.resolve_for_agent(
            requested_key=state["selected_model_key"] or agent.default_model,
            supported_model_keys=agent.supported_models,
        )
        resolved_skills = [
            skill.key
            for skill in skill_registry.get_many(state["requested_skill_keys"])
            if skill.key in agent.supported_skills
        ]

        tools = tool_registry.get_many(agent.supported_tools)
        state["selected_agent_key"] = agent.key
        state["selected_agent_name"] = agent.name
        state["selected_model_key"] = selected_model.key
        state["selected_model_name"] = selected_model.name
        state["selected_model_provider"] = selected_model.provider
        state["resolved_skill_keys"] = resolved_skills
        state["skill_prompt_blocks"] = skill_runtime_service.build_prompt_blocks(resolved_skills)
        state["memory_hits"] = []
        state["memory_prompt_blocks"] = []
        if memory_runtime_service is not None:
            memory_result = await memory_runtime_service.retrieve_for_turn(
                session_id=state["session_id"],
                trace_id=state["trace_id"],
                query=state["normalized_input"] or state["user_message"],
                context=state["context_bundle"],
            )
            state["memory_hits"] = [item.model_dump(mode="python") for item in memory_result.hits]
            state["memory_prompt_blocks"] = memory_result.prompt_blocks
        state["active_mcp_servers"] = mcp_runtime_service.list_active_servers()
        state["mcp_prompt_blocks"] = mcp_runtime_service.build_prompt_blocks(state["active_mcp_servers"])
        state["available_tool_keys"] = [tool.key for tool in tools]
        append_graph_event(
            state,
            "graph.route_selected",
            "router",
            "Agent, model, skills, and toolset have been resolved for this turn.",
            requested_agent=requested_agent,
            resolved_agent=agent.key,
            agent_name=agent.name,
            requested_model=requested_model,
            model_key=selected_model.key,
            model_name=selected_model.name,
            model_provider=selected_model.provider,
            resolved_skills=",".join(resolved_skills) or "none",
            memory_hit_count=len(state["memory_hits"]),
            active_mcp_count=len(state["active_mcp_servers"]),
            available_tools=",".join(state["available_tool_keys"]) or "none",
        )
        return state

    return router
