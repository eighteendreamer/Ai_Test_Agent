"""Pure initial waiting projection; persistence/authority stay with atomic park."""
from copy import deepcopy

from typing import Any
from pydantic import TypeAdapter

from langchain_core.messages import AIMessage, ToolMessage

from src.application.deep_agents.framework_transcript import serialize_messages
from src.application.deep_agents.native_interrupts import _source_call
from src.application.runtime.state_persistence import build_pending_turn
from src.application.runtime.tool_messages import build_runtime_tool_messages
from src.runtime.execution_logging import GRAPH_EVENT_LOG_VERSION_KEY
from src.schemas.controlled_case_task_input import ControlledCasePreparedTaskInputAsset, parse_controlled_case_task_input
from src.schemas.framework_approval import approval_value_hash, framework_approval_id, framework_tool_call_id
from src.schemas.framework_tool_projection import FrameworkToolProjection


def wait_native_messages(snapshot, *, retained_sources=None):
    """Include official pending task results not yet reduced into root values.

    Ordering here only collects evidence. Existing application ordinals come
    from captured tool material, never StateSnapshot task traversal order.
    """
    if any(task.state is not None or (retained_sources is None and task.error is not None) for task in snapshot.tasks):
        raise ValueError("Controlled waiting state has a child graph or unresolved native error")
    messages = list(snapshot.values.get("messages", []))
    source = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    for task in snapshot.tasks:
        if task.result is None:
            continue
        if not isinstance(task.result, dict) or not isinstance(task.result.get("messages"), list):
            if retained_sources is not None:
                # Later/unretained results cannot authorize or block a
                # historical observation. Missing retained material is still
                # rejected by the shared original-material reader/projector.
                continue
            raise ValueError("Controlled waiting state has unsupported native task results")
        for message in task.result["messages"]:
            if retained_sources is not None and (not isinstance(message, ToolMessage)
                or (source.id if source else None, message.tool_call_id) not in retained_sources):
                continue
            if not isinstance(message, ToolMessage):
                raise ValueError("Controlled waiting task has a non-tool native result")
            if message not in messages:
                messages.append(message)
    return messages


def project_controlled_approval_progress(parent, material, native_messages, natives, event_log, *,
                                        state, records, materials, root_model_budget):
    """One original slot changes; no new ordinal, clock or execution intent.

    No upstream counterpart (checked: formal Runtime single-item projection,
    original tool-message merge, v4 §9.2): reconstruct from frozen parent and
    applied material, not mutable ToolJob text or a submitted state dictionary.
    """
    binding = parent.binding
    state, records = deepcopy((state, records))
    matches = [r for r in parent.state['tool_results'] if r['call_id'] == material.record.call_id]
    if len(matches) != 1 or matches[0]['status'] != 'waiting_approval':
        raise ValueError('Controlled progress has no unique original pending tool slot')
    if event_log[:len(parent.state['event_log'])] != parent.state['event_log']:
        raise ValueError('Controlled progress changed its original event prefix')
    by_call = {r['call_id']: r for r in records}
    by_call[material.record.call_id] = material.record.model_dump(mode='json')
    records = TypeAdapter(Any).dump_python(list(by_call.values()), mode='json')
    state['tool_results'] = records
    state['tool_messages'] = build_runtime_tool_messages(binding.turn_id, records, binding_id=binding.binding_id,
        previous_records=parent.state['tool_results'], previous_messages=parent.state['tool_messages'],
        original_materials=materials, require_original=True)
    state['framework_messages'] = serialize_messages(native_messages)
    pending = {framework_approval_id(binding, n) for n in natives}
    if not pending.issubset({a['id'] for a in parent.state['pending_approvals']}):
        raise ValueError('Controlled progress requires original sibling pending identities')
    state['pending_approvals'] = [a for a in parent.state['pending_approvals'] if a['id'] in pending]
    state['context_bundle']['deep_agent_approval_batch'] = deepcopy(state['pending_approvals'])
    state['model_response_summary'] = {**state['model_response_summary'], 'root_model_budget': deepcopy(root_model_budget)}
    state['event_log'] = deepcopy(event_log)
    state.update(pending_turn={}, termination_reason='', control_state='resuming', continue_loop=True)
    return state


def project_controlled_case_wait(original, native_messages, natives, approvals, waiting_records,
                                 materials, event_log, root_model_budget):
    """Freeze NEW waiting slots after known completion-order slots.

    No upstream counterpart (checked: controlled result projector, ordinary
    waiting/tool merge, v4 §9.2): choose source-AI call order for NEW waiting
    slots and persist it once. This is not reconstruction of historical UI
    order, and it never reorders already captured terminal material ordinals.
    """
    original = parse_controlled_case_task_input(original)
    if not isinstance(original, ControlledCasePreparedTaskInputAsset):
        raise ValueError("Controlled wait needs its frozen complete application preparation")
    binding = original.graph_input.binding
    prefix = original.graph_input.framework_messages
    if serialize_messages(native_messages[:len(prefix)]) != prefix:
        raise ValueError("Controlled wait changed its original native input prefix")
    source = next((m for m in reversed(native_messages) if isinstance(m, AIMessage)), None)
    if source is None or not source.tool_calls or len({c['id'] for c in source.tool_calls}) != len(source.tool_calls):
        raise ValueError("Controlled wait has no unique current source tool calls")
    positions = {call['id']: index for index, call in enumerate(source.tool_calls)}
    by_native = {framework_tool_call_id(binding, item): item for item in natives}
    by_approval = {item.id: item for item in approvals}
    by_record = {item.call_id: item for item in waiting_records}
    if len(by_native) != len(natives) or set(by_record) != set(by_native) or len(by_approval) != len(natives):
        raise ValueError("Controlled wait has incomplete original application approvals")
    for call_id, native in by_native.items():
        ai, _ = _source_call(native_messages, native.tool_call_id, native.tool_name, native.arguments)
        record = by_record[call_id]
        if (native.checkpoint_namespace != "" or ai.id != native.source_message_id
            or framework_approval_id(binding, native) != record.approval_id
            or record.approval_id not in by_approval or record.status != "waiting_approval"):
            raise ValueError("Controlled waiting slot differs from its original native call")
    # All terminal tool material must refer to a real native model call. A
    # material hash alone does not authenticate the source-AI/ToolMessage pair.
    used = []
    current = None
    by_material = {(m.source.source_message_id, m.source.tool_call_id): m for m in materials}
    for message in native_messages:
        if isinstance(message, AIMessage):
            current = message
        elif isinstance(message, ToolMessage):
            material = by_material.get((current.id if current else None, message.tool_call_id))
            if material is None or current is None:
                raise ValueError("Controlled waiting native result lacks original tool material")
            call = [c for c in current.tool_calls if c['id'] == message.tool_call_id]
            if (len(call) != 1 or call[0]['name'] != material.source.tool_name
                or approval_value_hash(call[0]['args']) != approval_value_hash(material.source.arguments)
                or FrameworkToolProjection.from_tool_message(message, binding=binding, source=material.source) != material):
                raise ValueError("Controlled waiting tool material differs from its original source")
            used.append(material.record.call_id)
    current_settled = {m.source.tool_call_id for m in materials if m.source.source_message_id == source.id}
    current_pending = {n.tool_call_id for n in natives}
    if (set(used) != {m.record.call_id for m in materials} or len(used) != len(set(used))
        or current_settled & current_pending or (current_settled | current_pending) != set(positions)):
        raise ValueError("Controlled wait has missing or extra original tool material")
    ordered_waits = sorted(waiting_records, key=lambda r: positions[by_native[r.call_id].tool_call_id])
    records = [m.record.model_dump(mode="python") for m in materials] + [r.model_dump(mode="python") for r in ordered_waits]
    if len({r['call_id'] for r in records}) != len(records):
        raise ValueError("Controlled wait has overlapping terminal and pending slots")
    state = deepcopy(original.runtime_preparation.initial_state)
    initial = state['event_log']
    if event_log[:len(initial)] != initial or any(e['type'] == 'runtime.turn_completed' for e in event_log):
        raise ValueError("Controlled wait changed its prepared events or has a terminal intent")
    state['event_log'] = deepcopy(event_log)
    state['context_bundle'][GRAPH_EVENT_LOG_VERSION_KEY] = 1
    state['framework_messages'] = serialize_messages(native_messages)
    state['tool_results'] = records
    state['tool_messages'] = build_runtime_tool_messages(binding.turn_id, records, binding_id=binding.binding_id,
        original_materials=materials, require_original=True)
    pending = [by_approval[r.approval_id].model_dump(mode="python") for r in ordered_waits]
    state['context_bundle']['deep_agent_approval_batch'] = pending
    state.update(pending_approvals=pending, control_state='waiting_approval', termination_reason='waiting_approval',
        continue_loop=False, final_response='', model_response_text='')
    state['model_response_summary'] = {'harness': 'langgraph', 'controlled_case_wait_version': 1,
        'root_model_budget': deepcopy(root_model_budget)}
    state['pending_turn'] = build_pending_turn(state, 'waiting_approval')
    return state
