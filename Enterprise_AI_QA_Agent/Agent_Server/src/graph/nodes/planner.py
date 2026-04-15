from __future__ import annotations

from src.graph.state import AgentGraphState
from src.runtime.execution_logging import append_graph_event


def planner(state: AgentGraphState) -> AgentGraphState:
    state["plan_steps"] = [
        f"Align runtime context for session mode '{state['session_mode']}' and runtime mode '{state['runtime_mode']}'.",
        f"Route this turn to agent '{state['selected_agent_name']}' using model '{state['selected_model_name']}'.",
        "Load reusable skills, tools, and future MCP connectors from their registries instead of hard-coding logic.",
        "Run permission gating before any non-safe tool execution to satisfy Harness Engineering constraints.",
        "Persist event traces and checkpoint-ready snapshot data for replay, review, and automated verification.",
    ]
    append_graph_event(
        state,
        "graph.plan_built",
        "planner",
        "Execution plan has been generated for the current turn.",
        agent_key=state["selected_agent_key"],
        model_key=state["selected_model_key"],
        plan_step_count=len(state["plan_steps"]),
        plan_outline=" | ".join(state["plan_steps"]),
    )
    return state
