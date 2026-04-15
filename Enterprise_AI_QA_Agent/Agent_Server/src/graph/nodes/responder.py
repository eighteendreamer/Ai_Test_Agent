from __future__ import annotations

from src.graph.state import AgentGraphState
from src.runtime.execution_logging import append_graph_event, truncate_text


def responder(state: AgentGraphState) -> AgentGraphState:
    if state["continue_loop"] or not state["final_response"].strip():
        return state

    plan_text = "\n".join(f"{index}. {item}" for index, item in enumerate(state["plan_steps"], start=1))
    tool_text = (
        "\n".join(f"- {item['tool_key']}: {item['status']} - {item['summary']}" for item in state["tool_results"])
        if state["tool_results"]
        else "- No tools selected for this turn."
    )
    skill_text = ", ".join(state["resolved_skill_keys"]) or "none"
    approval_text = (
        "Pending tool approvals exist. The framework has paused sensitive tool execution until approval is resolved."
        if state["pending_approvals"]
        else "No pending approvals. The runtime skeleton completed this turn without blocking tools."
    )

    model_text = state["final_response"].strip()
    state["final_response"] = (
        f"{model_text}\n\n"
        f"[Framework]\n"
        f"Agent: {state['selected_agent_name']}\n"
        f"Model: {state['selected_model_name']}\n"
        f"Resolved skills: {skill_text}\n"
        f"Available tools: {', '.join(state['available_tool_keys']) or 'none'}\n"
        f"Allowed tools: {', '.join(state['allowed_tool_keys']) or 'none'}\n"
        f"Execution plan:\n{plan_text}\n\n"
        f"Tool status:\n{tool_text}\n\n"
        f"{approval_text}"
    ).strip()
    append_graph_event(
        state,
        "graph.response_ready",
        "responder",
        "Assistant response payload has been finalized for the client.",
        agent_key=state["selected_agent_key"],
        model_key=state["selected_model_key"],
        pending_approval_count=len(state["pending_approvals"]),
        response_preview=truncate_text(state["final_response"], 180),
    )
    return state
