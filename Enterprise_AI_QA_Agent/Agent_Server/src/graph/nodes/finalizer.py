from __future__ import annotations

from src.graph.state import AgentGraphState
from src.runtime.execution_logging import append_graph_event


def build_finalizer_node(
    model_runtime_service,
):
    async def finalizer(state: AgentGraphState) -> AgentGraphState:
        if state.get("interrupt_requested"):
            state["continue_loop"] = False
            return state

        if not state["model_tool_calls"]:
            state["final_response"] = state["model_response_text"]
            state["pending_turn"] = {}
            state["continue_loop"] = False
            state["termination_reason"] = "assistant_final"
            append_graph_event(
                state,
                "graph.execution_completed",
                "finalizer",
                "Model finished without requesting any tools.",
                final_response_length=len(state["final_response"]),
            )
            return state

        if state["pending_approvals"]:
            state["final_response"] = _build_pending_approval_response(state)
            state["continue_loop"] = False
            state["termination_reason"] = "waiting_approval"
            return state

        if state["continue_loop"]:
            usage = state.get("turn_token_usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            context_window = int(state.get("model_context_window") or 0)
            if prompt_tokens > 0 and context_window > 0 and prompt_tokens >= context_window * 0.9:
                state["continue_loop"] = False
                state["termination_reason"] = "context_budget_exhausted"
                state["final_response"] = _build_context_exhausted_response(state)
                append_graph_event(
                    state,
                    "graph.context_budget_exhausted",
                    "finalizer",
                    "Runtime stopped before the next model call would exceed the context window.",
                    prompt_tokens=prompt_tokens,
                    context_window=context_window,
                    loop_iteration=state["loop_iteration"],
                )
            elif state["loop_iteration"] + 1 >= state["max_iterations"]:
                state["continue_loop"] = False
                state["termination_reason"] = "max_iterations"
                state["final_response"] = (
                    "The runtime reached the maximum recursive tool iterations for this turn. "
                    "Execution stopped to avoid an unbounded loop."
                )
                append_graph_event(
                    state,
                    "graph.max_iterations_reached",
                    "finalizer",
                    "Runtime stopped because the recursive loop reached its safety limit.",
                    loop_iteration=state["loop_iteration"],
                    max_iterations=state["max_iterations"],
                )
            else:
                append_graph_event(
                    state,
                    "graph.loop_continuing",
                    "finalizer",
                    "Runtime will continue into the next recursive model iteration.",
                    next_iteration=state["loop_iteration"] + 1,
                    max_iterations=state["max_iterations"],
                )
                return state
        else:
            state["termination_reason"] = "tool_loop_completed"
            state["pending_turn"] = {}
            append_graph_event(
                state,
                "graph.execution_completed",
                "finalizer",
                "Runtime finished the tool loop and produced the final response.",
                tool_result_count=len(state["tool_results"]),
                final_response_length=len(state["final_response"]),
            )
        return state

    return finalizer


def _build_context_exhausted_response(state: AgentGraphState) -> str:
    completed_tools = [
        f"- {item['tool_name']}: {item['summary']}"
        for item in state["tool_results"]
        if item["status"] == "completed"
    ]
    sections = [
        "The model context window is nearly full, so the runtime stopped this turn "
        "instead of issuing a request that would fail.",
    ]
    if state["model_response_text"].strip():
        sections.insert(0, state["model_response_text"].strip())
    if completed_tools:
        sections.append("Completed tools so far:\n" + "\n".join(completed_tools))
    sections.append(
        "Full tool outputs are persisted in the session records. "
        "Start a follow-up request to continue from these results."
    )
    return "\n\n".join(sections).strip()


def _build_pending_approval_response(state: AgentGraphState) -> str:
    completed_tools = [
        f"- {item['tool_name']}: {item['summary']}"
        for item in state["tool_results"]
        if item["status"] == "completed"
    ]
    blocked_tools = [
        f"- {item['tool_name']}: approval_id={item['id']}"
        for item in state["pending_approvals"]
    ]
    sections = [state["model_response_text"].strip()] if state["model_response_text"].strip() else []
    sections.append(
        "Execution is waiting for approval before protected tools can run."
    )
    if completed_tools:
        sections.append("Completed tools:\n" + "\n".join(completed_tools))
    if blocked_tools:
        sections.append("Pending approvals:\n" + "\n".join(blocked_tools))
    sections.append(
        f"Agent: {state['selected_agent_name']} | Model: {state['selected_model_name']}"
    )
    return "\n\n".join(sections).strip()
