from __future__ import annotations

from src.graph.state import AgentGraphState
from src.runtime.execution_logging import append_graph_event, truncate_text


def context_builder(state: AgentGraphState) -> AgentGraphState:
    context = dict(state["context_bundle"])
    context.update(
        {
            "message_count": state["message_count"],
            "session_mode": state["session_mode"],
            "runtime_mode": state["runtime_mode"],
            "preferred_model": state["preferred_model"] or "auto",
            "loop_iteration": state["loop_iteration"],
            "harness_flags": [
                "event_sourcing",
                "permission_gate",
                "checkpoint_ready",
                "registry_driven",
                "recursive_tool_loop",
            ],
        }
    )
    state["context_bundle"] = context
    append_graph_event(
        state,
        "graph.context_built",
        "context_builder",
        "Runtime context bundle has been prepared for this turn.",
        message_count=state["message_count"],
        session_mode=state["session_mode"],
        runtime_mode=state["runtime_mode"],
        preferred_model=state["preferred_model"] or "auto",
        loop_iteration=state["loop_iteration"],
        user_message_preview=truncate_text(state["user_message"], 160),
        context_keys=",".join(sorted(context.keys())),
    )
    return state
