from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.application.mcp_runtime_service import MCPRuntimeService
from src.application.memory_runtime_service import MemoryRuntimeService
from src.application.model_runtime_service import ModelRuntimeService
from src.application.permission_service import PermissionService
from src.application.prompt_assembly_service import PromptAssemblyService
from src.application.skill_runtime_service import SkillRuntimeService
from src.application.tool_job_service import ToolJobService
from src.application.tool_runtime_service import ToolRuntimeService
from src.graph.nodes.context_builder import build_context_builder_node
from src.graph.nodes.finalizer import build_finalizer_node
from src.graph.nodes.model_invoker import build_model_invoker_node, route_after_model_invoker
from src.graph.nodes.permission_gate import build_permission_gate
from src.graph.nodes.planner import planner
from src.graph.nodes.prompt_assembler import build_prompt_assembler_node
from src.graph.nodes.responder import responder
from src.graph.nodes.router import build_router_node
from src.graph.nodes.tool_executor import build_tool_executor_node
from src.graph.state import AgentGraphState
from src.registry.agents import AgentRegistry
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry


def build_agent_graph(
    agent_registry: AgentRegistry,
    tool_registry: ToolRegistry,
    model_registry: ModelRegistry,
    skill_registry: SkillRegistry,
    skill_runtime_service: SkillRuntimeService,
    mcp_runtime_service: MCPRuntimeService,
    memory_runtime_service: MemoryRuntimeService | None,
    permission_service: PermissionService,
    prompt_assembly_service: PromptAssemblyService,
    model_runtime_service: ModelRuntimeService,
    tool_runtime_service: ToolRuntimeService,
    tool_job_service: ToolJobService | None = None,
):
    graph = StateGraph(AgentGraphState)
    graph.add_node(
        "context_builder",
        build_context_builder_node(memory_runtime_service=memory_runtime_service),
    )
    graph.add_node(
        "router",
        build_router_node(
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            model_registry=model_registry,
            skill_registry=skill_registry,
            skill_runtime_service=skill_runtime_service,
            mcp_runtime_service=mcp_runtime_service,
            memory_runtime_service=memory_runtime_service,
        ),
    )
    graph.add_node("planner", planner)
    graph.add_node(
        "permission_gate",
        build_permission_gate(
            permission_service=permission_service,
            tool_registry=tool_registry,
        ),
    )
    graph.add_node(
        "prompt_assembler",
        build_prompt_assembler_node(
            prompt_assembly_service=prompt_assembly_service,
            agent_registry=agent_registry,
        ),
    )
    graph.add_node(
        "model_invoker",
        build_model_invoker_node(
            model_runtime_service=model_runtime_service,
            tool_registry=tool_registry,
        ),
    )
    graph.add_node(
        "tool_executor",
        build_tool_executor_node(
            tool_registry=tool_registry,
            permission_service=permission_service,
            tool_runtime_service=tool_runtime_service,
            tool_job_service=tool_job_service,
        ),
    )
    graph.add_node(
        "finalizer",
        build_finalizer_node(
            model_runtime_service=model_runtime_service,
        ),
    )
    graph.add_node("responder", responder)

    graph.add_edge(START, "context_builder")
    graph.add_edge("context_builder", "router")
    graph.add_edge("router", "planner")
    graph.add_edge("planner", "permission_gate")
    graph.add_edge("permission_gate", "prompt_assembler")
    graph.add_edge("prompt_assembler", "model_invoker")
    graph.add_conditional_edges(
        "model_invoker",
        route_after_model_invoker,
        {
            "tool_executor": "tool_executor",
            "finalizer": "finalizer",
        },
    )
    graph.add_edge("tool_executor", "finalizer")
    graph.add_edge("finalizer", "responder")
    graph.add_edge("responder", END)
    return graph.compile()
