"""Application projections of supplied original facts; no execution/recovery rights.

Keep snapshot field selection, completed Deep Agents state and result assembly
shared by normal execution and read-only reconstruction. Callers own original
material verification, clocks/identities, event persistence and finalization.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.application.deep_agents.result_projection import DeepAgentRuntimeResult
from src.application.runtime.state_persistence import persisted_turn_budget
from src.schemas.framework_approval import approval_value_hash
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


@dataclass(frozen=True)
class DeepAgentCallbackFacts:
    pilot_stage: str
    adapter_stage: str
    subagent_events: list[dict[str, Any]]


def deep_agent_terminal_event_specs(result: DeepAgentRuntimeResult, *, pilot_stage: str):
    """One payload rule for legacy emission and versioned pure intent projection."""
    specs = []
    if result.todos:
        specs.append(("graph.plan_built", "deepagents.todo",
            "Deep Agents cognitive plan has been synchronized for this turn.",
            {"harness": "deepagents", "pilot_stage": pilot_stage, "plan_step_count": len(result.todos),
                "plan_outline": " | ".join(item["content"] for item in result.todos), "todos": result.todos}))
    specs.append(("runtime.turn_completed", "runtime",
        "Deep Agents pilot execution finished for the current turn.",
        {"harness": "deepagents", "pilot_stage": pilot_stage, "final_response_length": len(result.output_text),
            **({"structured_output_version": result.metadata["structured_output_version"],
                "structured_result_sha256": approval_value_hash(result.metadata["structured_result"])}
                if "structured_output_version" in result.metadata else {}),
            **({"root_model_budget": result.metadata["root_model_budget"]} if "root_model_budget" in result.metadata else {}),
            **({"adapter_stage": result.metadata.get("stage"),
                "callback_events_sha256": approval_value_hash(result.subagent_events)}
                if result.metadata.get("native_terminal") is not None else {})}))
    return specs


def project_deep_agent_terminal_events(preparation, result: DeepAgentRuntimeResult, *, event_log):
    """Derive only not-yet-allocated terminal intents, never callback originals.

    No upstream counterpart (checked: framework tool UUID5 identities, existing
    graph event payload/ACK ports, pinned Checkpoint.ts, v4 §9.2): application
    terminal-event identity rules must be frozen before native invocation.
    The timestamp explicitly denotes the native checkpoint's creation, not an
    application completion/commit time. All sources/prefixes must already be
    authenticated by their readers; this pure function grants no sealing,
    completion-declaration, recovery or execution authority.
    """
    from src.application.deep_agents.native_terminal import NativeTerminalProof
    from src.runtime.execution_logging import build_graph_event_intent
    from src.schemas.framework_graph_input import FrameworkTerminalEventPreparation, TERMINAL_EVENT_PREPARATION_KEY

    state, identity = preparation.initial_state, preparation.identity
    binding = identity.binding
    rules = FrameworkTerminalEventPreparation.model_validate(state["context_bundle"].get(TERMINAL_EVENT_PREPARATION_KEY))
    if (state.get("session_id") != binding.session_id or state.get("turn_id") != binding.turn_id
        or state["context_bundle"].get("runtime_binding") != binding.model_dump(mode="json")
        or result.metadata.get("stage") != rules.adapter_stage or result.interrupts or result.native_interrupts):
        raise ValueError("Terminal event projection differs from its original scope, stage or completed result")
    terminal = NativeTerminalProof.model_validate(result.metadata.get("native_terminal"))
    if (terminal.binding_fingerprint != binding.fingerprint or terminal.thread_id != binding.thread_id
        or terminal.checkpoint_namespace != ""):
        raise ValueError("Terminal event projection differs from its original native checkpoint binding")
    try:
        timestamp = datetime.fromisoformat(result.native_checkpoint_timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError("Terminal event projection has no original checkpoint timestamp") from exc
    if timestamp.utcoffset() is None:
        raise ValueError("Terminal event projection requires an aware original checkpoint timestamp")
    prefix = state.get("event_log", [])
    if event_log[:len(prefix)] != prefix:
        raise ValueError("Terminal event projection changed its original prepared event prefix")
    projected_state = {**state, "event_log": list(event_log)}
    events = []
    for event_type, phase, message, payload in deep_agent_terminal_event_specs(result, pilot_stage=rules.pilot_stage):
        seed = json.dumps(["framework-terminal-event-v1", binding.fingerprint,
            identity.assistant_message_id, identity.snapshot_id, event_type], separators=(",", ":"))
        event = build_graph_event_intent(projected_state, event_type, phase, message,
            event_id=str(uuid5(NAMESPACE_URL, seed)), timestamp=timestamp,
            timestamp_source=rules.timestamp_source, native_checkpoint_id=terminal.checkpoint_id,
            terminal_event_projection_version=rules.schema_version, **payload)
        events.append(event)
        projected_state["event_log"].append({"id": event.id, "timestamp": event.timestamp.isoformat(),
            "type": event.type, "payload": event.payload})
    return events


def project_deep_agent_callback_facts(
    event_log: list[dict[str, Any]], *, turn_id: str, returned_event_offset: int,
) -> DeepAgentCallbackFacts:
    """Consume original invocation facts from an already verified collection.

    No upstream counterpart (checked: core 1.6.10 Runnable.astream_events v2,
    RuntimeEventCompletion, v4 §9.2): the SDK does not define application stages
    or which callback suffix belongs to an approval invocation.
    The event-completion reader owns provenance, ordering and completeness.
    Approval invocations return only their own callback suffix, not callbacks
    accumulated by earlier invocations. Runtime and adapter stages can differ
    (e.g. governance enabled with no visible tools); preserve both originals.
    Older collections without adapter_stage remain missing material, never a
    reason to infer it from today's configuration or native display messages.
    Audit redaction may legitimately remove original callback text. Its digest
    must still match before read-only reuse; this never rejects normal output
    merely because recovery materials are unavailable. The digest is not source
    authority; the existing event-completion reader supplies that verification.
    This pure port grants no recovery or execution permission.
    """
    if (type(returned_event_offset) is not int or not 0 <= returned_event_offset < len(event_log)
        or event_log[-1].get("type") != "runtime.turn_completed"):
        raise ValueError("Original Deep Agents callback collection has no completed invocation boundary")
    completed = event_log[-1]["payload"]
    stages = {"DA-E1", "DA-E2", "DA-E3", "DA-E4"}
    if any(not isinstance(completed.get(key), str) or completed[key] not in stages
        for key in ("pilot_stage", "adapter_stage")):
        raise ValueError("Original Deep Agents callback collection has no supported original stages")
    callbacks = _deep_agent_callback_events(event_log, turn_id=turn_id, returned_event_offset=returned_event_offset)
    if completed.get("callback_events_sha256") != approval_value_hash(callbacks):
        raise ValueError("Original Deep Agents callback material differs from its declared original digest")
    return DeepAgentCallbackFacts(completed["pilot_stage"], completed["adapter_stage"], callbacks)


def project_deep_agent_checkpoint_callback_facts(preparation, *, event_log, returned_event_offset):
    """Consume authenticated ACK originals, never guess callbacks from messages."""
    from src.schemas.framework_graph_input import FrameworkTerminalEventPreparation, TERMINAL_EVENT_PREPARATION_KEY

    rules = FrameworkTerminalEventPreparation.model_validate(preparation.initial_state["context_bundle"].get(TERMINAL_EVENT_PREPARATION_KEY))
    if (type(returned_event_offset) is not int or not 0 <= returned_event_offset < len(event_log)
        or any(item["type"] in {"runtime.turn_completed", "graph.plan_built"} for item in event_log)):
        raise ValueError("Checkpoint callback facts require their original pre-terminal invocation boundary")
    callbacks = _deep_agent_callback_events(event_log, turn_id=preparation.identity.binding.turn_id,
        returned_event_offset=returned_event_offset)
    return DeepAgentCallbackFacts(rules.pilot_stage, rules.adapter_stage, callbacks)


def _deep_agent_callback_events(event_log, *, turn_id, returned_event_offset):
    suffix = event_log[returned_event_offset:]
    if any(item.get("payload", {}).get("turn_id") != turn_id for item in suffix):
        raise ValueError("Original Deep Agents callback collection differs from its Turn")
    callbacks = []
    for item in suffix:
        event_type = item.get("type", "")
        if not event_type.startswith("graph.subagent_"):
            continue
        raw = item["payload"].get("subagent_event")
        if (event_type not in {"graph.subagent_started", "graph.subagent_completed"}
            or not isinstance(raw, dict) or set(raw) != {"type", "name", "run_id", "parent_run_id"}
            or any(not isinstance(value, str) for value in raw.values())
            or raw["type"] != event_type.removeprefix("graph.subagent_")):
            raise ValueError("Original Deep Agents callback material is missing or has an unsupported format")
        callbacks.append(deepcopy(raw))
    return callbacks


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
    from src.schemas.framework_cognitive_result import validate_cognitive_completion

    validate_cognitive_completion(initial_state.get("context_bundle") or {}, result.metadata)
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


def project_original_completed_turn(*, preparation, result, binding, facts, event_log, returned_event_offset, materials):
    """Use the same original slots/identity for ordinary and approval reads.

    All inputs must already be verified by their scoped readers. This is only
    composition of the existing pure projections, never admission or writes.
    """
    from src.application.runtime.tool_messages import build_chat_tool_messages, build_runtime_tool_messages
    from src.schemas.framework_graph_input import TERMINAL_EVENT_PREPARATION_KEY

    if TERMINAL_EVENT_PREPARATION_KEY in preparation.initial_state.get("context_bundle", {}):
        count = len(deep_agent_terminal_event_specs(result, pilot_stage=facts.pilot_stage))
        intents = project_deep_agent_terminal_events(preparation, result, event_log=event_log[:-count])
        expected = [{"id": item.id, "timestamp": item.timestamp.isoformat(), "type": item.type, "payload": item.payload}
            for item in intents]
        if event_log[-count:] != expected:
            raise ValueError("Original terminal events differ from their frozen projection rules")

    initial = deepcopy(preparation.initial_state)
    records = [item.record.model_dump(mode="python") for item in materials]
    initial["tool_messages"] = build_runtime_tool_messages(binding.turn_id, records, binding_id=binding.binding_id,
        original_materials=materials, require_original=True)
    state = completed_deep_agent_state(initial, result, tool_records=records, binding=binding, pilot_stage=facts.pilot_stage)
    state["event_log"] = event_log
    identity = preparation.identity
    snapshot = project_runtime_snapshot(session_id=binding.session_id, state=state, snapshot_id=identity.snapshot_id,
        version=identity.snapshot_version, created_at=identity.snapshot_created_at)
    return project_completed_turn(state=state, snapshot=snapshot,
        events=[ExecutionEvent(session_id=binding.session_id, **item) for item in event_log[returned_event_offset:]],
        tool_messages=build_chat_tool_messages(binding.turn_id, records, binding_id=binding.binding_id,
            hygiene_metadata={}, original_materials=materials))


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
