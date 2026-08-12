from __future__ import annotations

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.capabilities.capability_resolver import CapabilityResolver
from src.application.capabilities.tool_exposure_policy import ToolExposurePolicy
from src.application.security.prompt_injection_policy import PromptInjectionPolicy
from src.application.skills.skill_runtime_service import SkillRuntimeService
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
    capability_resolver = CapabilityResolver()
    tool_exposure_policy = ToolExposurePolicy()
    prompt_injection_policy = PromptInjectionPolicy()

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
        ]

        loaded_skill_tools = [
            tool_key
            for skill in skill_registry.get_many(resolved_skills)
            for tool_key in skill.tool_keys
        ]
        agent_skill_tools = [
            tool_key
            for skill in skill_registry.get_many(agent.supported_skills)
            for tool_key in skill.tool_keys
        ]
        context_bundle = dict(state.get("context_bundle") or {})
        required_capabilities = [
            str(item).strip()
            for item in context_bundle.get("required_capabilities", [])
            if str(item).strip()
        ]
        safety_assessment = dict(context_bundle.get("safety_assessment") or {})
        observation_assessments = [
            prompt_injection_policy.assess(block, "memory")
            for block in state.get("observation_prompt_blocks", [])
        ]
        safety_assessment = prompt_injection_policy.merge_into_safety(
            safety_assessment,
            observation_assessments,
        )
        context_bundle["safety_assessment"] = safety_assessment
        selected_mode = dict(context_bundle.get("selected_mode") or {})
        allowed_capabilities = list(
            dict.fromkeys(
                [
                    *selected_mode.get("core_capability_keys", []),
                    *selected_mode.get("on_demand_capability_keys", []),
                ]
            )
        )
        denied_capabilities = set(selected_mode.get("denied_capability_keys", []))
        allowed_capabilities = [
            capability for capability in allowed_capabilities if capability not in denied_capabilities
        ]
        restrict_tool_expansion = "do_not_expand_tool_access" in safety_assessment.get("restrictions", [])
        requested_tool_keys = [
            str(item).strip()
            for item in context_bundle.get("requested_tool_keys", [])
            if str(item).strip()
        ]
        mode_tool_keys = [
            str(item).strip()
            for item in selected_mode.get("registered_tool_keys", [])
            if str(item).strip()
        ]
        initial_tool_keys = list(
            dict.fromkeys(
                [
                    "skill",
                    *mode_tool_keys,
                    *loaded_skill_tools,
                    *agent_skill_tools,
                    *requested_tool_keys,
                ]
            )
        )
        tools = capability_resolver.eligible_tools(
            tools=tool_registry.get_many(initial_tool_keys),
            active_mode_key=state["mode_key"],
            required_capabilities=required_capabilities,
            allowed_capabilities=allowed_capabilities,
        )
        tools = tool_exposure_policy.filter_supported(tools=tools, agent=agent)
        state["selected_agent_key"] = agent.key
        state["selected_agent_name"] = agent.name
        state["selected_model_key"] = selected_model.key
        state["selected_model_name"] = selected_model.name
        state["selected_model_provider"] = selected_model.provider
        state["resolved_skill_keys"] = resolved_skills
        state["skill_prompt_blocks"] = skill_runtime_service.build_prompt_blocks(
            resolved_skills,
            include_content=True,
        )
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
        retrieved_assessments = [
            *[
                prompt_injection_policy.assess(block, "memory")
                for block in state.get("memory_prompt_blocks", [])
            ],
            *[
                prompt_injection_policy.assess(block, "retrieved_document")
                for block in state.get("mcp_prompt_blocks", [])
            ],
        ]
        safety_assessment = prompt_injection_policy.merge_into_safety(
            safety_assessment,
            retrieved_assessments,
        )
        context_bundle["safety_assessment"] = safety_assessment
        state["available_tool_keys"] = [tool.key for tool in tools]
        eligible_deferred_tools = capability_resolver.eligible_tools(
            tools=tool_registry.list(),
            active_mode_key=state["mode_key"],
            required_capabilities=required_capabilities,
            allowed_capabilities=allowed_capabilities,
        )
        eligible_deferred_tools = tool_exposure_policy.filter_supported(
            tools=eligible_deferred_tools,
            agent=agent,
        )
        restrict_tool_expansion = "do_not_expand_tool_access" in safety_assessment.get("restrictions", [])
        if restrict_tool_expansion:
            requested = set(required_capabilities)
            eligible_deferred_tools = [
                tool for tool in eligible_deferred_tools
                if requested.intersection(tool.capability_keys)
            ]
        state["deferred_tool_keys"] = [
            tool.key
            for tool in eligible_deferred_tools
            if tool.key not in state["available_tool_keys"]
        ]
        context_bundle["available_skills"] = [
            skill.model_dump(mode="python")
            for skill in skill_registry.list()
        ]
        context_bundle["selected_agent_supported_skills"] = list(agent.supported_skills)
        context_bundle["selected_agent_supported_capabilities"] = list(agent.supported_capabilities)
        context_bundle["eligible_deferred_tool_count"] = len(state["deferred_tool_keys"])
        context_bundle["requested_tool_keys"] = requested_tool_keys
        context_bundle["mode_registered_tool_keys"] = mode_tool_keys
        context_bundle["indirect_injection_signal_count"] = len(
            safety_assessment.get("indirect_injection_signals", [])
        )
        state["context_bundle"] = context_bundle
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
            mode_registered_tools=",".join(mode_tool_keys) or "none",
            requested_tools=",".join(requested_tool_keys) or "none",
            deferred_tool_count=len(state["deferred_tool_keys"]),
            indirect_injection_signal_count=context_bundle["indirect_injection_signal_count"],
        )
        return state

    return router
