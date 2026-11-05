"""Pure controlled result projection; sources/ACKs/finalization are separate."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from uuid import NAMESPACE_URL, uuid5

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.application.deep_agents.framework_transcript import serialize_messages
from src.application.deep_agents.native_terminal import require_closed_native_messages
from src.application.model_clients.completion import is_model_response_incomplete, model_completion_status
from src.application.runtime.tool_messages import build_chat_tool_messages, build_runtime_tool_messages
from src.application.runtime.turn_projection import project_completed_turn, project_runtime_snapshot
from src.runtime.execution_logging import GRAPH_EVENT_LOG_VERSION_KEY, build_graph_event_intent
from src.schemas.controlled_case_task_input import ControlledCaseResultTaskInputAsset, parse_controlled_case_task_input
from src.schemas.framework_approval import FrameworkToolCallIdentity, approval_value_hash


def project_controlled_case_result(original, native, root_model_budget, event_log, materials, *, parent=None):
    """Reuse one rule for live completion and authenticated original reads.

    No upstream counterpart (checked: turn_projection, original case execution,
    v4 §9.2/§9.3/§12.1): direct MessagesState has no DA stages, cognitive
    result or application state. v3 input explicitly selects current final AI
    text. Never fall back to an earlier answer or treat text as test evidence.
    Identity is frozen before execution; terminal time is checkpoint creation,
    NOT application completion time. This function performs no I/O or clocks.
    """
    original = parse_controlled_case_task_input(original)
    if not isinstance(original, ControlledCaseResultTaskInputAsset):
        raise ValueError("Controlled result requires its original selected projection contract")
    binding, preparation = original.graph_input.binding, original.runtime_preparation
    proof, messages = native.proof, native.values.get("messages")
    if (proof.binding_fingerprint != binding.fingerprint or proof.thread_id != binding.thread_id
        or proof.checkpoint_namespace != "" or native.values.get("__interrupt__")):
        raise ValueError("Controlled result differs from its completed native binding")
    require_closed_native_messages(messages)
    if (serialize_messages(messages[:len(original.graph_input.framework_messages)]) != original.graph_input.framework_messages
        or not all(isinstance(item, (HumanMessage, AIMessage, ToolMessage)) for item in messages)
        or not isinstance(messages[-1], AIMessage) or messages[-1].tool_calls):
        raise ValueError("Controlled result differs from its formal native input or final AI boundary")
    output_text = messages[-1].text.strip()
    if not output_text:
        raise ValueError("Controlled result has no current final AI text under its frozen output contract")
    metadata = deepcopy(messages[-1].response_metadata)
    # Same provider fields as message_adapter/openai_responses_client; pinned
    # ChatOpenAI 1.6.2 retains Responses status/incomplete_details in metadata.
    completion = {key: metadata[key] for key in ("finish_reason", "stop_reason", "stream_terminal_received") if key in metadata}
    if binding.model_transport == "openai_responses":
        completion["finish_reason"] = metadata.get("status")
        completion["stop_reason"] = (metadata.get("incomplete_details") or {}).get("reason") or metadata.get("status")
    if is_model_response_incomplete(completion):
        raise ValueError("Controlled result has a known incomplete or blocked model termination")
    by_source = {(item.source.source_message_id, item.source.tool_call_id): item for item in materials}
    source = None
    used = []
    for message in messages:
        if isinstance(message, AIMessage):
            source = message
        elif isinstance(message, ToolMessage):
            material = by_source.get((source.id if source else None, message.tool_call_id))
            calls = [call for call in source.tool_calls if call["id"] == message.tool_call_id] if source else []
            if material is None or len(calls) != 1:
                raise ValueError("Controlled result has an unprepared native business tool")
            call = calls[0]
            identity = FrameworkToolCallIdentity(checkpoint_namespace="", source_message_id=source.id,
                tool_call_id=call["id"], tool_name=call["name"], arguments=call["args"])
            material.require_native_message(message, binding=binding, source=identity)
            if (material.source != identity
                or approval_value_hash(material.record.input) != approval_value_hash(identity.arguments)):
                raise ValueError("Controlled result tool material differs from its original model call")
            used.append(material.record.call_id)
    if len(used) != len(materials) or len(set(used)) != len(used):
        raise ValueError("Controlled result tool originals are missing, extra or repeated")
    state = deepcopy(parent.state if parent is not None else preparation.initial_state)
    prefix = state["event_log"]
    if (event_log[:len(prefix)] != prefix or any(item["type"] == "runtime.turn_completed" for item in event_log)):
        raise ValueError("Controlled result changed its prepared prefix or already has a terminal intent")
    state["event_log"] = deepcopy(event_log)
    state["context_bundle"][GRAPH_EVENT_LOG_VERSION_KEY] = 1
    state["framework_messages"] = serialize_messages(messages)
    records = [item.record.model_dump(mode="python") for item in materials]
    state["tool_results"] = records
    state["tool_messages"] = build_runtime_tool_messages(binding.turn_id, records, binding_id=binding.binding_id,
        original_materials=materials, require_original=True)
    if parent is not None:
        if parent.binding != binding:
            raise ValueError("Controlled result differs from its original approval parent")
        state["context_bundle"]["deep_agent_approval_batch"] = []
    state.update(pending_approvals=[], pending_turn={}, model_response_text=output_text, final_response=output_text,
        termination_reason="completed", control_state="completed", continue_loop=False)
    state["model_response_summary"] = {"mode": "ok", "harness": "langgraph", "controlled_case_result_version": 1,
        "message_count": len(messages), "native_terminal": proof.model_dump(mode="json"),
        "root_model_budget": deepcopy(root_model_budget), "response_metadata": metadata,
        "completion_status": model_completion_status(completion), **completion}
    timestamp = datetime.fromisoformat(native.checkpoint_timestamp)
    if timestamp.utcoffset() is None:
        raise ValueError("Controlled result has no aware original checkpoint timestamp")
    identity = preparation.identity
    seed = json.dumps(["controlled-case-terminal-event-v1", binding.fingerprint,
        identity.assistant_message_id, identity.snapshot_id], separators=(",", ":"))
    event = build_graph_event_intent(state, "runtime.turn_completed", "runtime",
        "Controlled formal case graph finished; application finalization and test verdict remain separate.",
        event_id=str(uuid5(NAMESPACE_URL, seed)), timestamp=timestamp, harness="langgraph",
        controlled_case_result_version=1, timestamp_source=original.result_preparation.timestamp_source,
        native_checkpoint_id=proof.checkpoint_id, final_response_length=len(output_text),
        root_model_budget=root_model_budget)
    state["event_log"].append({"id": event.id, "timestamp": event.timestamp.isoformat(), "type": event.type, "payload": event.payload})
    snapshot = project_runtime_snapshot(session_id=binding.session_id, state=state, snapshot_id=identity.snapshot_id,
        version=identity.snapshot_version, created_at=identity.snapshot_created_at)
    from src.schemas.session import ExecutionEvent

    return project_completed_turn(state=state, snapshot=snapshot,
        events=[ExecutionEvent(session_id=binding.session_id, **item) for item in state["event_log"]],
        tool_messages=build_chat_tool_messages(binding.turn_id, records, binding_id=binding.binding_id,
            hygiene_metadata={}, original_materials=materials))
