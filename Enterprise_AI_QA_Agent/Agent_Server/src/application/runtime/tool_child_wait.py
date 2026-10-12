"""Pause an original tool at the existing durable, resumable turn boundary."""
from src.application.runtime.state_persistence import persisted_turn_budget
from src.runtime.execution_logging import append_graph_event


def park_tool_wait(state, record):
    """Mirror the approval checkpoint; a waiting tool has no model result yet."""
    if record.status != "waiting_children" or not record.job_id:
        raise ValueError("Child wait requires its original persisted ToolJob")
    call_id = record.call_id
    state["runtime_messages"] = [message for message in state["runtime_messages"]
        if not (message.get("role") == "tool" and message.get("tool_call_id") == call_id)]
    state["context_bundle"] = dict(state["context_bundle"])
    state["continue_loop"] = False
    state["termination_reason"] = "interrupted"
    state["control_state"] = "interrupted"
    state["final_response"] = ""
    state["pending_turn"] = {
        **persisted_turn_budget(state),
        "turn_id": state["turn_id"], "trace_id": state["trace_id"],
        "context_bundle": state["context_bundle"],
        "waiting_tool": {"job_id": record.job_id, "call_id": call_id,
            "tool_key": record.tool_key, "input": record.input,
            "child_session_ids": list(record.output.get("waiting_child_session_ids") or [])},
    }
    append_graph_event(state, "tool.waiting_children", "runtime",
        "Original tool paused until its dispatched children settle.",
        tool_job_id=record.job_id, call_id=call_id,
        child_session_ids=state["pending_turn"]["waiting_tool"]["child_session_ids"])
