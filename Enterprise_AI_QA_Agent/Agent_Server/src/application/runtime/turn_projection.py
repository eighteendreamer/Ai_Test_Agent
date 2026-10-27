"""Application projections of supplied original facts; no execution/recovery rights.

Keep snapshot field selection, completed Deep Agents state and result assembly
shared by normal execution and read-only reconstruction. Callers own original
material verification, clocks/identities, event persistence and finalization.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.application.deep_agents.result_projection import DeepAgentRuntimeResult
from src.application.runtime.state_persistence import persisted_turn_budget
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ChatMessage, ExecutionEvent, SessionSnapshot


@dataclass
class RuntimeTurnResult:
    output_text: str
    events: list[ExecutionEvent]
    snapshot: SessionSnapshot
    approvals: list[dict]
    state: dict
    tool_messages: list[ChatMessage]
    pending_turn: dict


def completed_deep_agent_state(
    initial_state: dict[str, Any], result: DeepAgentRuntimeResult, *,
    tool_records: list[dict[str, Any]], binding: RuntimeBinding | None, pilot_stage: str,
) -> dict[str, Any]:
    """Apply the original completion rule without changing input facts.

    Tools, callback facts and model output must already be settled/supplied by
    their original ports. Application state must contain the original tool
    message slots, whose JSON strings cannot be rebuilt from JSONB record order
    or substituted with the SDK's model_content. Missing slots remain missing
    material, not permission to manufacture output. Runtime conversation,
    budget, context and extensions remain original; native messages do not
    replace legacy runtime_messages.
    """
    if result.interrupts or result.native_interrupts:
        raise ValueError("A result with native interrupts cannot be projected as completed")
    state = deepcopy(initial_state)
    if binding is not None:
        if (state.get("session_id") != binding.session_id or state.get("turn_id") != binding.turn_id
            or state.get("context_bundle", {}).get("runtime_binding") != binding.model_dump(mode="json")):
            raise ValueError("Completed projection differs from its original runtime binding")
        state["context_bundle"]["deep_agent_approval_batch"] = []
    state["framework_messages"] = deepcopy(result.framework_messages)
    state["plan_steps"] = [item["content"] for item in result.todos]
    state["tool_results"] = deepcopy(tool_records)
    messages = state.get("tool_messages")
    if (not isinstance(messages, list) or len(messages) != len(tool_records)
        or any(not isinstance(message, dict) or message.get("role") != "tool" or message.get("tool_call_id") != record["call_id"]
            or message.get("name") != record["tool_key"] or not isinstance(message.get("content"), str)
            for message, record in zip(messages, tool_records))):
        raise ValueError("Original application tool message slots are missing or differ from their records")
    state["pending_approvals"] = []
    state["pending_turn"] = {}
    state["cognitive_subagent_events"] = deepcopy(result.subagent_events)
    state["model_response_text"] = result.output_text
    state["model_response_summary"] = {
        "mode": "ok", "harness": "deepagents", "pilot_stage": pilot_stage, **deepcopy(result.metadata),
    }
    state["final_response"] = result.output_text
    state["termination_reason"] = "completed"
    state["control_state"] = "completed"
    return state


def project_completed_turn(
    *, state: dict[str, Any], snapshot: SessionSnapshot, events: list[ExecutionEvent],
    tool_messages: list[ChatMessage],
) -> RuntimeTurnResult:
    """Assemble completed output using already allocated/persisted intentions.

    The complete graph log stays in state/snapshot. The caller supplies the
    original returned event range (possibly an approval suffix), never invented
    events. No native proof or complete event log grants application recovery.
    """
    if (state.get("control_state") != "completed" or state.get("termination_reason") != "completed"
        or state.get("pending_approvals") or state.get("pending_turn") or snapshot.stage != "completed"):
        raise ValueError("Runtime completion projection contains pending or non-completed facts")
    if (snapshot.session_id != state.get("session_id")
        or snapshot.graph_state.get("turn_id") != state.get("turn_id")):
        raise ValueError("Runtime completion snapshot differs from its original scope")
    return RuntimeTurnResult(output_text=state["final_response"], events=deepcopy(events),
        snapshot=snapshot.model_copy(deep=True), approvals=[], state=deepcopy(state),
        tool_messages=deepcopy(tool_messages), pending_turn={})


def project_runtime_snapshot(
    *, session_id: str, state: dict[str, Any], snapshot_id: str, version: int, created_at: datetime,
) -> SessionSnapshot:
    """Select the existing snapshot fields; all identity/time inputs are explicit."""
    graph_state = {
        "turn_id": state["turn_id"],
        "trace_id": state["trace_id"],
        "user_message": state["user_message"],
        "normalized_input": state["normalized_input"],
        "session_mode": state["session_mode"],
        "runtime_mode": state["runtime_mode"],
        "mode_key": state["mode_key"],
        "selected_agent_key": state["selected_agent_key"],
        "selected_agent_name": state["selected_agent_name"],
        "selected_model_key": state["selected_model_key"],
        "selected_model_name": state["selected_model_name"],
        "selected_model_provider": state["selected_model_provider"],
        "requested_skill_keys": state["requested_skill_keys"],
        "resolved_skill_keys": state["resolved_skill_keys"],
        "skill_prompt_blocks": state["skill_prompt_blocks"],
        "memory_hits": state["memory_hits"],
        "memory_prompt_blocks": state["memory_prompt_blocks"],
        "test_case_hits": state.get("test_case_hits", []),
        "test_case_prompt_blocks": state.get("test_case_prompt_blocks", []),
        "observation_hits": state["observation_hits"],
        "observation_prompt_blocks": state["observation_prompt_blocks"],
        "active_mcp_servers": state["active_mcp_servers"],
        "mcp_prompt_blocks": state["mcp_prompt_blocks"],
        "available_tool_keys": state["available_tool_keys"],
        "deferred_tool_keys": state["deferred_tool_keys"],
        "model_visible_tool_keys": state["model_visible_tool_keys"],
        "allowed_tool_keys": state["allowed_tool_keys"],
        "approval_required_tool_keys": state["approval_required_tool_keys"],
        "denied_tool_keys": state["denied_tool_keys"],
        "permission_decisions": state["permission_decisions"],
        "pending_approvals": state["pending_approvals"],
        "plan_steps": state["plan_steps"],
        "system_prompt_sections": state["system_prompt_sections"],
        "runtime_message_sections": state["runtime_message_sections"],
        "system_prompt": state["system_prompt"],
        "runtime_messages": state["runtime_messages"],
        "framework_messages": list(state.get("framework_messages") or []),
        "model_request_payload": state["model_request_payload"],
        "model_response_summary": state["model_response_summary"],
        "model_response_text": state["model_response_text"],
        **persisted_turn_budget(state),
        "assistant_tool_call_message": state["assistant_tool_call_message"],
        "model_tool_calls": state["model_tool_calls"],
        "tool_results": state["tool_results"],
        "tool_messages": state["tool_messages"],
        "worker_dispatches": state["worker_dispatches"],
        "context_bundle": state["context_bundle"],
        "event_log": state["event_log"],
        "pending_turn": state["pending_turn"],
        "control_state": state["control_state"],
        "loop_iteration": state["loop_iteration"],
        "max_iterations": state["max_iterations"],
        "continue_loop": state["continue_loop"],
        "termination_reason": state["termination_reason"],
        "final_response": state["final_response"],
    }
    return SessionSnapshot(id=snapshot_id, session_id=session_id, version=version,
        stage=snapshot_stage_for_state(state), created_at=created_at, graph_state=deepcopy(graph_state))


def snapshot_stage_for_state(state: dict[str, Any]) -> str:
    if state["termination_reason"] == "interrupted":
        return "interrupted"
    if state["pending_approvals"]:
        return "waiting_approval"
    if state["pending_turn"]:
        return "resumable"
    if state["termination_reason"] == "failed":
        return "failed"
    return "completed"
