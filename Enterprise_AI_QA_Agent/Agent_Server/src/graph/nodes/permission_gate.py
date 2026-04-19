from __future__ import annotations

from src.application.permission_service import PermissionPolicyContext, PermissionService
from src.graph.state import AgentGraphState
from src.registry.tools import ToolRegistry
from src.runtime.execution_logging import append_graph_event
from src.schemas.session import MessageKind, RuntimeMode, SessionMode


def build_permission_gate(
    permission_service: PermissionService,
    tool_registry: ToolRegistry,
):
    def permission_gate(state: AgentGraphState) -> AgentGraphState:
        tool_descriptors = tool_registry.get_many(state["available_tool_keys"])
        input_envelope = dict(state.get("context_bundle", {}).get("input_envelope") or {})
        input_routing = dict(state.get("context_bundle", {}).get("input_routing") or {})
        policy_context = PermissionPolicyContext(
            session_mode=SessionMode(state["session_mode"]),
            runtime_mode=RuntimeMode(state["runtime_mode"]),
            selected_agent_key=state["selected_agent_key"],
            message_kind=MessageKind(input_envelope.get("message_kind", MessageKind.user_input.value)),
            submit_mode=str(input_envelope.get("submit_mode") or "immediate"),
            execution_lane=str(input_routing.get("execution_lane") or "conversation_turn"),
            source=str(input_envelope.get("source") or "session.send_message"),
        )
        evaluation = permission_service.evaluate(
            policy_context=policy_context,
            tools=tool_descriptors,
        )

        state["allowed_tool_keys"] = evaluation.allowed_tool_keys
        state["approval_required_tool_keys"] = evaluation.approval_required_tool_keys
        state["denied_tool_keys"] = evaluation.denied_tool_keys
        state["model_visible_tool_keys"] = evaluation.model_visible_tool_keys
        state["permission_decisions"] = [item.to_payload() for item in evaluation.decisions]
        state["pending_approvals"] = []
        append_graph_event(
            state,
            "graph.permission_evaluated",
            "permission_gate",
            "Tool permissions have been evaluated for this turn.",
            policy_session_mode=policy_context.session_mode.value,
            policy_runtime_mode=policy_context.runtime_mode.value,
            policy_message_kind=policy_context.message_kind.value,
            policy_execution_lane=policy_context.execution_lane,
            available_tools=",".join(state["available_tool_keys"]) or "none",
            model_visible_tools=",".join(state["model_visible_tool_keys"]) or "none",
            allowed_tools=",".join(state["allowed_tool_keys"]) or "none",
            approval_required_tools=",".join(state["approval_required_tool_keys"]) or "none",
            denied_tools=",".join(state["denied_tool_keys"]) or "none",
            hidden_tools=",".join(evaluation.hidden_tool_keys) or "none",
            model_visible_tool_count=len(state["model_visible_tool_keys"]),
            allowed_tool_count=len(state["allowed_tool_keys"]),
            approval_required_count=len(state["approval_required_tool_keys"]),
            denied_tool_count=len(state["denied_tool_keys"]),
        )
        return state

    return permission_gate
