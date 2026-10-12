"""Pause an original tool at the existing durable, resumable turn boundary."""
from src.application.runtime.state_persistence import build_pending_turn
from src.runtime.execution_logging import append_graph_event


def waiting_tool_checkpoints(pending_turn):
    """Read complete batches while retaining historical single-tool snapshots."""
    return pending_turn.get("waiting_tools") or [pending_turn["waiting_tool"]]


def park_tool_wait(state, record):
    """Mirror the approval checkpoint; a waiting tool has no model result yet."""
    if record.status != "waiting_children" or not record.job_id:
        raise ValueError("Child wait requires its original persisted ToolJob")
    waiting = [item for item in state["tool_results"] if item.get("status") == "waiting_children"]
    if not waiting:
        waiting = [record.model_dump(mode="python")]
    call_ids = {item["call_id"] for item in waiting}
    conversation = state.get("pending_turn", {}).get("conversation_messages", state["runtime_messages"])
    state["runtime_messages"] = [message for message in conversation
        if not (message.get("role") == "tool" and message.get("tool_call_id") in call_ids)]
    state["context_bundle"] = dict(state["context_bundle"])
    state["continue_loop"] = False
    stage = "waiting_approval" if state["pending_approvals"] else "interrupted"
    state["termination_reason"] = stage
    state["control_state"] = stage
    state["final_response"] = ""
    checkpoints = [{"job_id": item["job_id"], "call_id": item["call_id"],
        "tool_key": item["tool_key"], "input": item["input"],
        "child_session_ids": list(item["output"].get("waiting_child_session_ids") or [])} for item in waiting]
    state["pending_turn"] = {**build_pending_turn(state, stage),
        "waiting_tool": checkpoints[0], "waiting_tools": checkpoints}
    append_graph_event(state, "tool.waiting_children", "runtime",
        "Original tool paused until its dispatched children settle.",
        tool_job_id=record.job_id, call_id=record.call_id,
        waiting_tool_count=len(checkpoints), pending_approval_count=len(state["pending_approvals"]),
        child_session_ids=[identity for item in checkpoints for identity in item["child_session_ids"]])
