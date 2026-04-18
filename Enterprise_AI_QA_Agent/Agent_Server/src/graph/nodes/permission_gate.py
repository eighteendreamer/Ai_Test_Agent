from __future__ import annotations

from src.application.permission_service import PermissionService
from src.graph.state import AgentGraphState
from src.registry.tools import ToolRegistry
from src.runtime.execution_logging import append_graph_event
from src.schemas.session import SessionMode


def build_permission_gate(
    permission_service: PermissionService,
    tool_registry: ToolRegistry,
):
    def permission_gate(state: AgentGraphState) -> AgentGraphState:
        tool_descriptors = tool_registry.get_many(state["available_tool_keys"])
        evaluation = permission_service.evaluate(
            session_mode=SessionMode(state["session_mode"]),
            tools=tool_descriptors,
        )

        state["allowed_tool_keys"] = evaluation.allowed_tool_keys
        state["approval_required_tool_keys"] = evaluation.approval_required_tool_keys
        state["denied_tool_keys"] = evaluation.denied_tool_keys
        state["permission_decisions"] = [item.to_payload() for item in evaluation.decisions]
        state["pending_approvals"] = []
        append_graph_event(
            state,
            "graph.permission_evaluated",
            "permission_gate",
            "Tool permissions have been evaluated for this turn.",
            available_tools=",".join(state["available_tool_keys"]) or "none",
            allowed_tools=",".join(state["allowed_tool_keys"]) or "none",
            approval_required_tools=",".join(state["approval_required_tool_keys"]) or "none",
            denied_tools=",".join(state["denied_tool_keys"]) or "none",
            allowed_tool_count=len(state["allowed_tool_keys"]),
            approval_required_count=len(state["approval_required_tool_keys"]),
            denied_tool_count=len(state["denied_tool_keys"]),
        )
        return state

    return permission_gate
