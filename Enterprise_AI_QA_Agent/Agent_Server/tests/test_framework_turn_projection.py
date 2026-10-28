"""Completed Runtime assembly is deterministic; recovery admission is separate."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage

from src.application.deep_agents.native_terminal import collect_native_terminal
from src.application.deep_agents.result_projection import DeepAgentRuntimeResult, project_deep_agent_result
from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
from src.application.runtime.runtime_service import RuntimeService, RuntimeTurnResult
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_context import framework_owner_scope
from src.runtime.session_event_sink import SessionEventSink
from src.schemas.framework_graph_input import FrameworkProjectionIdentity, parse_graph_input_asset
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from src.schemas.session import ExecutionEvent, ExecutionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config, start
from tests.test_framework_native_terminal import Model


@pytest.mark.asyncio
@pytest.mark.parametrize("planning,subagents,redacted,governed_empty", [(False, False, False, False), (True, False, False, False),
    (False, True, False, False), (False, True, True, False), (False, False, False, True)])
async def test_completed_runtime_is_identical_from_original_preparation_native_and_event_materials(
    checkpoint_harness, monkeypatch, tmp_path, planning, subagents, redacted, governed_empty,
):
    h = checkpoint_harness
    await start(h)
    captured, graphs = [], []
    responses = [AIMessage(id="answer", content="original answer", additional_kwargs={"signature": "fixture-signature"})]
    if planning:
        responses.insert(0, AIMessage(id="plan", content="", tool_calls=[{"id": "todo", "name": "write_todos",
            "args": {"todos": [{"content": "inspect", "status": "completed"}]}, "type": "tool_call"}]))
    if subagents:
        responses[:0] = [AIMessage(id="delegate", content="", tool_calls=[{"id": "child", "name": "task",
            "args": {"description": "Inspect the controlled project", "subagent_type": "code-review-researcher"},
            "type": "tool_call"}]), AIMessage(content="controlled child evidence")]
    model = Model(responses=responses)
    async def resolve(key):
        return model
    def factory(**kwargs):
        graph = create_deep_agent(**kwargs)
        graphs.append(graph)
        return graph
    async def remember(asset):
        captured.append(parse_graph_input_asset(asset))
    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve,
        checkpointer_factory=lambda: h.provider.open(owner=h.owner.runtime_owner))
    monkeypatch.setattr(adapter, "_resolve_factory", lambda: factory)
    if redacted:
        execute = adapter.execute
        async def private_callback_label(request):
            original_callback = request.on_subagent_event
            async def callback(item):
                item["name"] = "password=synthetic-test-marker"
                await original_callback(item)
            return await execute(replace(request, on_subagent_event=callback))
        monkeypatch.setattr(adapter, "execute", private_callback_label)
    runtime = RuntimeService(graph=None,
        model_runtime_service=SimpleNamespace(get_model_config=lambda key: None, get_default_model_config=lambda: None),
        tool_runtime_service=object(), tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=adapter, deep_agent_enabled=True, deep_agent_cognitive_planning_enabled=planning,
        deep_agent_read_only_filesystem_enabled=subagents, deep_agent_cognitive_subagents_enabled=subagents,
        deep_agent_governed_tools_enabled=governed_empty)
    session = await h.store.get_session(h.session_id)
    identity = FrameworkProjectionIdentity(binding=h.binding, assistant_message_id="reserved-assistant",
        snapshot_id="reserved-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 27, 0, 1))
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with framework_owner_scope(h.owner.runtime_owner):
        original = await runtime.execute_turn(session, ExecutionRequest(session_id=h.session_id,
            turn_id=h.binding.turn_id, trace_id="projection", user_message="inspect", normalized_input="inspect",
            mode_key="code_review", model_key="fixture", context={"project_root": str(tmp_path)}, runtime_binding=h.binding),
            event_queue=sink, projection_identity=identity, on_graph_input=remember)
    prepared, = captured
    assert prepared.feature_flags.get("terminal_event_projection_version") == 1
    graph, = graphs
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        terminal = await collect_native_terminal(graph, saver, config(h))
        completion = await h.store.read_runtime_event_completion(h.binding, terminal.proof.model_dump(mode="json"))
        assert original.state["event_log"] == completion.event_log
        checkpoint = await saver.aget_tuple(config(h))
        assert checkpoint.metadata.get("framework_checkpoint_event_prefix") is not None, (
            "The native checkpoint has no atomically bound original acknowledged audit prefix")
        source = saver.runtime_source
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.runtime.framework_event_completion import read_checkpoint_event_prefix

    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        prefix = read_checkpoint_event_prefix(cur, h.settings, source, prepared, terminal)
    terminal_count = 2 if planning else 1
    assert prefix == completion.event_log[:-terminal_count]
    receipt = terminal.checkpoint_event_prefix
    assert receipt == checkpoint.metadata["framework_checkpoint_event_prefix"]
    assert receipt["scope"] == "checkpoint_graph_event_prefix_only"
    assert receipt["checkpoint_sha256"] == terminal.proof.checkpoint_sha256
    assert receipt["graph_input_sha256"] == prepared.asset_sha256
    assert "checkpoint_event_prefix" not in terminal.proof.model_dump()
    saved_events = await h.store.list_events(h.session_id)
    original_preparation = prepared.model_dump(mode="json")
    def forbidden(*args, **kwargs):
        raise AssertionError("Projection must not execute, allocate identities or consult the current clock")
    monkeypatch.setattr(adapter, "execute", forbidden)
    monkeypatch.setattr(runtime, "_execute_deep_agent_turn", forbidden)
    monkeypatch.setattr(graph, "ainvoke", forbidden)
    monkeypatch.setattr(model, "_generate", forbidden)
    monkeypatch.setattr("src.application.runtime.runtime_service.uuid4", forbidden)
    # Consume original channels and explicit producer facts, not a saved final
    # Runtime result or a display-text reconstruction. No admission is granted.
    from src.application.runtime.turn_projection import (
        completed_deep_agent_state, project_completed_turn, project_runtime_snapshot, project_deep_agent_callback_facts,
    )
    if redacted:
        # Redaction intentionally cannot reconstruct raw callback text. Normal
        # completion must remain valid; only original-material reuse is denied.
        assert original.output_text == "original answer"
        assert original.state["cognitive_subagent_events"][0]["name"] == "password=synthetic-test-marker"
        persisted = [entry["payload"]["subagent_event"] for entry in completion.event_log
            if entry["type"].startswith("graph.subagent_")]
        assert all(item["name"] == "password=[REDACTED]" for item in persisted)
        with pytest.raises(ValueError, match="declared original digest"):
            project_deep_agent_callback_facts(completion.event_log, turn_id=h.binding.turn_id,
                returned_event_offset=completion.completion.returned_event_offset)
        assert await h.store.list_events(h.session_id) == saved_events
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            assert await saver.aget_tuple(config(h)) == checkpoint
        assert await h.store.get_latest_snapshot(h.session_id) is None
        return
    facts = project_deep_agent_callback_facts(completion.event_log, turn_id=h.binding.turn_id,
        returned_event_offset=completion.completion.returned_event_offset)
    assert facts.adapter_stage == ("DA-E3" if planning or subagents else "DA-E1")
    assert facts.pilot_stage == ("DA-E4" if governed_empty else facts.adapter_stage)
    rules = prepared.runtime_preparation.initial_state["context_bundle"]["framework_terminal_event_preparation"]
    assert rules["pilot_stage"] == facts.pilot_stage and rules["adapter_stage"] == facts.adapter_stage
    assert bool(facts.subagent_events) is subagents
    if subagents:
        assert [item["type"] for item in facts.subagent_events] == ["started", "completed"]
        assert facts.subagent_events[0]["run_id"] == facts.subagent_events[1]["run_id"]
    native_result = project_deep_agent_result(terminal.values, stage=facts.adapter_stage,
        native_terminal=terminal.proof.model_dump(mode="json"), subagent_events=facts.subagent_events,
        native_checkpoint_timestamp=terminal.checkpoint_timestamp)
    from src.application.runtime.turn_projection import project_deep_agent_terminal_events

    terminal_count = 2 if native_result.todos else 1
    prefix = completion.event_log[:-terminal_count]
    monkeypatch.setattr("src.runtime.execution_logging.datetime", SimpleNamespace(now=forbidden))
    intents = project_deep_agent_terminal_events(prepared.runtime_preparation, native_result, event_log=prefix)
    reproduced = [{"id": item.id, "timestamp": item.timestamp.isoformat(), "type": item.type, "payload": item.payload}
        for item in intents]
    assert reproduced == completion.event_log[-terminal_count:]
    assert all(item.timestamp.isoformat() == checkpoint.checkpoint["ts"] for item in intents)
    assert all(item.payload["timestamp_source"] == "native_checkpoint.ts" for item in intents)
    state = completed_deep_agent_state(prepared.runtime_preparation.initial_state, native_result,
        tool_records=[], binding=h.binding, pilot_stage=facts.pilot_stage)
    state["event_log"] = completion.event_log
    snapshot = project_runtime_snapshot(session_id=h.session_id, state=state,
        snapshot_id=identity.snapshot_id, version=identity.snapshot_version, created_at=identity.snapshot_created_at)
    result = project_completed_turn(state=state, snapshot=snapshot, tool_messages=[],
        events=[ExecutionEvent(session_id=h.session_id, **entry)
            for entry in completion.event_log[completion.completion.returned_event_offset:]])
    assert isinstance(result, RuntimeTurnResult)
    assert FrameworkRuntimeResult.from_result(result).model_dump(mode="json") == FrameworkRuntimeResult.from_result(
        original).model_dump(mode="json")
    assert result.snapshot.id == identity.snapshot_id and result.snapshot.created_at == identity.snapshot_created_at
    assert prepared.model_dump(mode="json") == original_preparation
    assert await h.store.list_events(h.session_id) == saved_events
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        assert await saver.aget_tuple(config(h)) == checkpoint
    assert await h.store.get_latest_snapshot(h.session_id) is None


def callback_collection():
    from src.schemas.framework_approval import approval_value_hash

    callbacks = [{"type": kind, "name": "reader", "run_id": run, "parent_run_id": "root"}
        for kind, run in (("started", "prior"), ("completed", "prior"), ("started", "new"), ("completed", "new"))]
    events = [{"id": str(index), "type": "graph.subagent_" + item["type"],
        "payload": {"turn_id": "turn", "subagent_event": deepcopy(item)}} for index, item in enumerate(callbacks)]
    events.append({"id": "complete", "type": "runtime.turn_completed", "payload": {"turn_id": "turn",
        "pilot_stage": "DA-E4", "adapter_stage": "DA-E3", "callback_events_sha256": approval_value_hash(callbacks[2:])}})
    return events, callbacks[2:]


def terminal_inputs(*, planning=False):
    from src.application.deep_agents.native_terminal import NativeTerminalProof
    from src.schemas.framework_graph_input import FrameworkRuntimePreparation
    from tests.test_runtime_binding_persistence import make_binding

    binding = make_binding("session", "turn")
    identity = FrameworkProjectionIdentity(binding=binding, assistant_message_id="reserved-assistant",
        snapshot_id="reserved-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 27))
    prefix = [{"id": "original-prefix", "timestamp": "2026-10-27T00:00:00+00:00", "type": "runtime.turn_started",
        "payload": {"turn_id": "turn", "trace_id": "trace", "step": 1}}]
    preparation = FrameworkRuntimePreparation(identity=identity, initial_state={"session_id": "session", "turn_id": "turn",
        "trace_id": "trace", "event_log": deepcopy(prefix), "context_bundle": {
            "runtime_binding": binding.model_dump(mode="json"), "framework_result_projection_version": 1,
            "framework_terminal_event_preparation": {"schema_version": 1, "timestamp_source": "native_checkpoint.ts",
                "pilot_stage": "DA-E4", "adapter_stage": "DA-E3"}}})
    proof = NativeTerminalProof(binding_fingerprint=binding.fingerprint, thread_id=binding.thread_id,
        checkpoint_namespace="", checkpoint_id="original-checkpoint", checkpoint_sha256="a" * 64,
        state_sha256="b" * 64, serializer="fixture")
    result = DeepAgentRuntimeResult(output_text="original answer", metadata={"stage": "DA-E3",
        "native_terminal": proof.model_dump(mode="json"), "root_model_budget": {"original": {"calls": 3}}},
        native_checkpoint_timestamp="2026-10-27T00:01:02.123456+00:00",
        todos=[{"content": "password=synthetic-test-marker", "status": "completed"}] if planning else [])
    return preparation, result, prefix


@pytest.mark.parametrize("planning", [False, True])
def test_terminal_intents_use_frozen_identity_checkpoint_time_and_shared_audit_rules(monkeypatch, planning):
    from uuid import NAMESPACE_URL, uuid5
    from src.application.runtime.turn_projection import project_deep_agent_terminal_events

    preparation, result, prefix = terminal_inputs(planning=planning)
    originals = deepcopy((preparation, result, prefix))
    def forbidden(*args, **kwargs):
        pytest.fail("Pure terminal intents cannot allocate random identity or read a current clock")
    monkeypatch.setattr("src.schemas.session.uuid4", forbidden)
    monkeypatch.setattr("src.runtime.execution_logging.datetime", SimpleNamespace(now=forbidden))
    monkeypatch.setattr("src.application.runtime.turn_projection.datetime",
        SimpleNamespace(fromisoformat=datetime.fromisoformat, now=forbidden))
    events = project_deep_agent_terminal_events(preparation, result, event_log=prefix)
    assert events == project_deep_agent_terminal_events(preparation, result, event_log=prefix)
    assert [item.type for item in events] == (["graph.plan_built"] if planning else []) + ["runtime.turn_completed"]
    for index, item in enumerate(events, start=2):
        seed = json.dumps(["framework-terminal-event-v1", preparation.identity.binding.fingerprint,
            "reserved-assistant", "reserved-snapshot", item.type], separators=(",", ":"))
        assert item.id == str(uuid5(NAMESPACE_URL, seed))
        assert item.timestamp.isoformat() == result.native_checkpoint_timestamp
        assert item.payload["timestamp_source"] == "native_checkpoint.ts"
        assert item.payload["native_checkpoint_id"] == "original-checkpoint"
        assert item.payload["terminal_event_projection_version"] == 1
        assert item.payload["step"] == index and item.payload["correlation_id"] == f"trace:{index}"
        assert item.payload["pilot_stage"] == "DA-E4"
    assert events[-1].payload["adapter_stage"] == "DA-E3"
    assert events[-1].payload["root_model_budget"] == result.metadata["root_model_budget"]
    if planning:
        assert "synthetic-test-marker" not in str(events[0].payload)
        assert events[0].payload["todos"][0]["content"] == "password=[REDACTED]"
        events[0].payload["todos"][0]["content"] = "changed consumer copy"
    assert (preparation, result, prefix) == originals


@pytest.mark.parametrize("changed", ["timestamp_missing", "timestamp_invalid", "timestamp_naive", "stage", "binding",
    "native_namespace", "interrupt", "prefix", "missing_rules", "future_rules"])
def test_terminal_intents_reject_missing_or_contradictory_sources_without_mutating_them(changed):
    from src.application.runtime.turn_projection import project_deep_agent_terminal_events

    preparation, result, prefix = terminal_inputs()
    if changed.startswith("timestamp_"):
        result = replace(result, native_checkpoint_timestamp={"timestamp_missing": None, "timestamp_invalid": "bad",
            "timestamp_naive": "2026-10-27T00:01:02"}[changed])
    elif changed == "stage":
        result.metadata["stage"] = "DA-E1"
    elif changed == "binding":
        result.metadata["native_terminal"]["binding_fingerprint"] = "f" * 64
    elif changed == "native_namespace":
        result.metadata["native_terminal"]["checkpoint_namespace"] = "tools:child"
    elif changed == "interrupt":
        result = replace(result, interrupts=[{"id": "pending"}])
    elif changed == "prefix":
        prefix[0]["id"] = "not original"
    elif changed == "missing_rules":
        preparation.initial_state["context_bundle"].pop("framework_terminal_event_preparation")
    else:
        preparation.initial_state["context_bundle"]["framework_terminal_event_preparation"]["schema_version"] = 2
    originals = deepcopy((preparation, result, prefix))
    with pytest.raises(ValueError):
        project_deep_agent_terminal_events(preparation, result, event_log=prefix)
    assert (preparation, result, prefix) == originals


@pytest.mark.parametrize("changed", ["identity", "timestamp", "payload", "order"])
def test_original_terminal_intents_are_compared_not_replaced_by_newly_projected_events(changed):
    from src.application.runtime.turn_projection import project_deep_agent_terminal_events, project_original_completed_turn

    preparation, result, prefix = terminal_inputs(planning=True)
    intents = project_deep_agent_terminal_events(preparation, result, event_log=prefix)
    events = prefix + [{"id": item.id, "timestamp": item.timestamp.isoformat(), "type": item.type, "payload": item.payload}
        for item in intents]
    if changed == "identity":
        events[-1]["id"] = "different-original-identity"
    elif changed == "timestamp":
        events[-1]["timestamp"] = "2026-10-28T00:00:00+00:00"
    elif changed == "payload":
        events[-1]["payload"]["adapter_stage"] = "DA-E1"
    else:
        events[-2:] = list(reversed(events[-2:]))
    before = deepcopy(events)
    with pytest.raises(ValueError, match="Original terminal events"):
        project_original_completed_turn(preparation=preparation, result=result, binding=preparation.identity.binding,
            facts=SimpleNamespace(pilot_stage="DA-E4"), event_log=events, returned_event_offset=0, materials=[])
    assert events == before


@pytest.mark.parametrize("offset", [2, 3, 4])
def test_callback_source_consumes_only_original_invocation_suffix_and_keeps_distinct_stages(offset):
    from src.application.runtime.turn_projection import project_deep_agent_callback_facts
    from src.schemas.framework_approval import approval_value_hash

    events, expected = callback_collection()
    expected = expected[offset - 2:]
    events[-1]["payload"]["callback_events_sha256"] = approval_value_hash(expected)
    before = deepcopy(events)
    facts = project_deep_agent_callback_facts(events, turn_id="turn", returned_event_offset=offset)
    assert facts.pilot_stage == "DA-E4" and facts.adapter_stage == "DA-E3"
    assert facts.subagent_events == expected
    if facts.subagent_events:
        facts.subagent_events[0]["name"] = "changed consumer copy"
    assert events == before


@pytest.mark.parametrize("changed", ["offset", "boolean_offset", "prefix", "boundary", "turn", "pilot_stage",
    "adapter_stage", "stage_type", "missing_stage", "missing_raw", "future", "unknown_field", "type", "value", "digest", "missing_digest"])
def test_callback_source_rejects_missing_changed_or_unsupported_originals(changed):
    from src.application.runtime.turn_projection import project_deep_agent_callback_facts

    events, _ = callback_collection()
    offset = 2
    if changed == "offset":
        offset = len(events)
    elif changed == "boolean_offset":
        offset = True
    elif changed == "prefix":
        offset = 0
    elif changed == "boundary":
        events.pop()
    elif changed == "turn":
        events[2]["payload"]["turn_id"] = "other"
    elif changed in {"pilot_stage", "adapter_stage"}:
        events[-1]["payload"][changed] = "future"
    elif changed == "stage_type":
        events[-1]["payload"]["adapter_stage"] = {"future": "unsupported"}
    elif changed == "missing_stage":
        events[-1]["payload"].pop("adapter_stage")
    elif changed == "missing_raw":
        events[2]["payload"].pop("subagent_event")
    elif changed == "future":
        events[2]["type"] = "graph.subagent_future"
    elif changed == "unknown_field":
        events[2]["payload"]["subagent_event"]["future"] = "unknown"
    elif changed == "type":
        events[2]["payload"]["subagent_event"]["type"] = "completed"
    elif changed == "value":
        events[2]["payload"]["subagent_event"]["run_id"] = None
    elif changed == "digest":
        events[2]["payload"]["subagent_event"]["run_id"] = "changed"
    elif changed == "missing_digest":
        events[-1]["payload"].pop("callback_events_sha256")
    with pytest.raises(ValueError, match="Original Deep Agents callback"):
        project_deep_agent_callback_facts(events, turn_id="turn", returned_event_offset=offset)


def test_completion_state_preserves_original_context_budget_and_conversation_without_aliasing():
    from src.application.runtime.turn_projection import completed_deep_agent_state
    from tests.test_runtime_binding_persistence import make_binding

    binding = make_binding("session", "turn")
    initial = {"session_id": "session", "turn_id": "turn", "pending_turn": {},
        "context_bundle": {"runtime_binding": binding.model_dump(mode="json"), "deep_agent_approval_batch": [{"id": "old"}]},
        "runtime_messages": [{"role": "user", "content": "original"}],
        "model_call_count": 4, "turn_token_usage": {"total_tokens": 30}, "worker_dispatches": []}
    adapter_result = DeepAgentRuntimeResult(output_text="answer", metadata={"stage": "DA-E4"},
        framework_messages=[{"type": "ai", "data": {"content": "answer"}}],
        subagent_events=[{"type": "completed", "name": "reader"}])
    record = {"call_id": "A", "tool_key": "inspect", "tool_name": "Inspect", "status": "completed",
        "summary": "evidence", "output": {"nested": [1]}}
    from src.application.runtime.tool_messages import build_tool_message
    from src.schemas.tool_runtime import ToolExecutionRecord

    initial["tool_messages"] = [build_tool_message(ToolExecutionRecord.model_validate(record))]
    before = deepcopy(initial)
    result = completed_deep_agent_state(initial, adapter_result, tool_records=[record], binding=binding, pilot_stage="DA-E4")
    assert initial == before
    assert result["runtime_messages"] == initial["runtime_messages"]
    assert result["model_call_count"] == 4 and result["turn_token_usage"] == {"total_tokens": 30}
    assert result["context_bundle"]["deep_agent_approval_batch"] == []
    assert result["pending_turn"] == {} and result["pending_approvals"] == []
    assert result["control_state"] == result["termination_reason"] == "completed"
    result["tool_results"][0]["output"]["nested"].append(2)
    result["cognitive_subagent_events"][0]["name"] = "changed"
    result["runtime_messages"][0]["content"] = "changed"
    assert initial == before and record["output"] == {"nested": [1]}
    assert adapter_result.subagent_events[0]["name"] == "reader"


def test_interrupted_adapter_result_cannot_be_projected_as_completed():
    from src.application.runtime.turn_projection import completed_deep_agent_state

    with pytest.raises(ValueError, match="interrupt"):
        completed_deep_agent_state({}, DeepAgentRuntimeResult(output_text="", interrupts=[{"id": "pending"}]),
            tool_records=[], binding=None, pilot_stage="DA-E4")


def test_original_model_tool_message_survives_jsonb_key_reordering_without_reserialization():
    from src.application.runtime.turn_projection import completed_deep_agent_state
    from src.application.runtime.tool_messages import build_tool_message
    from src.schemas.tool_runtime import ToolExecutionRecord

    record = ToolExecutionRecord(call_id="A", tool_key="inspect", tool_name="Inspect", status="completed",
        summary="original evidence", output={"zz": {"b": 1, "a": 2}, "a": 3})
    original = build_tool_message(record)
    # PostgreSQL JSONB preserves object semantics, not insertion order. Keep
    # the exact original application message, not the SDK model_content field.
    restored = json.loads(record.model_dump_json(), object_pairs_hook=lambda pairs: dict(sorted(pairs)))
    state = initial_state()
    state["tool_messages"] = [original]
    result = completed_deep_agent_state(state, DeepAgentRuntimeResult(output_text="done"),
        tool_records=[restored], binding=None, pilot_stage="DA-E4")
    assert result["tool_messages"] == [original]


@pytest.mark.parametrize("changed", ["missing", "count", "entry", "role", "call", "tool", "content"])
def test_missing_or_mismatched_original_tool_slot_is_not_rebuilt(changed):
    from src.application.runtime.turn_projection import completed_deep_agent_state
    from src.application.runtime.tool_messages import build_tool_message
    from src.schemas.tool_runtime import ToolExecutionRecord

    record = ToolExecutionRecord(call_id="A", tool_key="inspect", tool_name="Inspect", status="completed", summary="evidence")
    state = initial_state()
    state["tool_messages"] = [build_tool_message(record)]
    if changed == "missing":
        del state["tool_messages"]
    elif changed == "count":
        state["tool_messages"] = []
    elif changed == "entry":
        state["tool_messages"] = ["not a message"]
    else:
        key = {"role": "role", "call": "tool_call_id", "tool": "name", "content": "content"}[changed]
        state["tool_messages"][0][key] = {} if changed == "content" else "foreign"
    before = deepcopy(state)
    with pytest.raises(ValueError, match="tool message slots"):
        completed_deep_agent_state(state, DeepAgentRuntimeResult(output_text="answer"),
            tool_records=[record.model_dump(mode="python")], binding=None, pilot_stage="DA-E4")
    assert state == before


@pytest.mark.parametrize("changed", ["session", "turn", "binding"])
def test_completion_state_rejects_cross_scope_inputs_without_changing_them(changed):
    from src.application.runtime.turn_projection import completed_deep_agent_state
    from tests.test_runtime_binding_persistence import make_binding

    binding = make_binding("session", "turn")
    state = {"session_id": "session", "turn_id": "turn",
        "context_bundle": {"runtime_binding": binding.model_dump(mode="json")}}
    if changed in {"session", "turn"}:
        state[changed + "_id"] = "foreign"
    else:
        state["context_bundle"]["runtime_binding"] = {}
    before = deepcopy(state)
    with pytest.raises(ValueError, match="binding"):
        completed_deep_agent_state(state, DeepAgentRuntimeResult(output_text="answer"),
            tool_records=[], binding=binding, pilot_stage="DA-E1")
    assert state == before


def initial_state():
    from tests.test_deep_agent_runtime_adapter import _runtime_session, _runtime_request, _ModelRuntimeStub

    runtime = RuntimeService(graph=None, model_runtime_service=_ModelRuntimeStub(), tool_runtime_service=object(),
        tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry())
    session = _runtime_session("projection-session")
    return runtime._build_initial_state(session, _runtime_request(session.id, "projection-turn"))


@pytest.mark.parametrize("reason,approvals,pending,expected", [
    ("interrupted", [{"id": "pending"}], {"stage": "waiting"}, "interrupted"),
    ("", [{"id": "pending"}], {"stage": "waiting"}, "waiting_approval"),
    ("", [], {"stage": "waiting"}, "resumable"),
    ("failed", [], {}, "failed"),
    ("completed", [], {}, "completed"),
])
def test_shared_snapshot_retains_legacy_stage_budget_and_detached_original_values(reason, approvals, pending, expected):
    from src.application.runtime.turn_projection import project_runtime_snapshot

    state = initial_state()
    state.update(termination_reason=reason, pending_approvals=approvals, pending_turn=pending,
        model_call_count=4, turn_token_usage={"total_tokens": 30})
    before = deepcopy(state)
    snapshot = project_runtime_snapshot(session_id=state["session_id"], state=state, snapshot_id="original-snapshot",
        version=7, created_at=datetime(2026, 10, 27))
    assert snapshot.stage == expected and snapshot.version == 7
    assert snapshot.graph_state["model_call_count"] == 4
    assert snapshot.graph_state["turn_token_usage"] == {"total_tokens": 30}
    snapshot.graph_state["context_bundle"]["changed"] = True
    assert state == before


def test_result_assembly_keeps_full_state_but_returns_only_supplied_event_suffix_and_original_tool_messages():
    from src.application.runtime.turn_projection import (
        completed_deep_agent_state, project_completed_turn, project_runtime_snapshot,
    )
    from src.schemas.session import ChatMessage, MessageRole

    state = completed_deep_agent_state(initial_state(), DeepAgentRuntimeResult(output_text="original"),
        tool_records=[], binding=None, pilot_stage="DA-E1")
    state["event_log"] = [{"id": name, "type": "runtime.turn_completed" if index else "runtime.turn_started",
        "timestamp": "2026-10-27T00:00:00+00:00", "payload": {"turn_id": state["turn_id"], "step": index + 1}}
        for index, name in enumerate(("prefix", "suffix"))]
    before = deepcopy(state)
    snapshot = project_runtime_snapshot(session_id=state["session_id"], state=state, snapshot_id="snapshot",
        version=2, created_at=datetime(2026, 10, 27))
    events = [ExecutionEvent(session_id=state["session_id"], **state["event_log"][-1])]
    tool = ChatMessage(id="original-tool", role=MessageRole.tool, content="original evidence",
        created_at=datetime(2026, 10, 27), metadata={"turn_id": state["turn_id"], "original": {"value": 1}})
    result = project_completed_turn(state=state, snapshot=snapshot, events=events, tool_messages=[tool])
    assert project_completed_turn(state=state, snapshot=snapshot, events=events, tool_messages=[tool]) == result
    assert [event.id for event in result.events] == ["suffix"]
    assert [event["id"] for event in result.state["event_log"]] == ["prefix", "suffix"]
    assert result.tool_messages == [tool]
    result.events[0].payload["step"] = 99
    result.state["context_bundle"]["changed"] = True
    result.snapshot.graph_state["context_bundle"]["changed"] = True
    result.tool_messages[0].metadata["original"]["value"] = 2
    assert state == before and events[0].payload["step"] == 2
    assert "changed" not in snapshot.graph_state["context_bundle"] and tool.metadata["original"]["value"] == 1


@pytest.mark.parametrize("changed", ["control", "reason", "approvals", "pending", "snapshot_stage", "snapshot_session", "snapshot_turn"])
def test_result_assembly_rejects_deterministically_contradictory_completion(changed):
    from src.application.runtime.turn_projection import (
        completed_deep_agent_state, project_completed_turn, project_runtime_snapshot,
    )

    state = completed_deep_agent_state(initial_state(), DeepAgentRuntimeResult(output_text="original"),
        tool_records=[], binding=None, pilot_stage="DA-E1")
    snapshot = project_runtime_snapshot(session_id=state["session_id"], state=state, snapshot_id="snapshot",
        version=1, created_at=datetime(2026, 10, 27))
    if changed == "control":
        state["control_state"] = "waiting_approval"
    elif changed == "reason":
        state["termination_reason"] = "interrupted"
    elif changed == "approvals":
        state["pending_approvals"] = [{"id": "pending"}]
    elif changed == "pending":
        state["pending_turn"] = {"stage": "waiting"}
    elif changed == "snapshot_stage":
        snapshot.stage = "waiting_approval"
    elif changed == "snapshot_session":
        snapshot.session_id = "foreign"
    else:
        snapshot.graph_state["turn_id"] = "foreign"
    with pytest.raises(ValueError, match="completion"):
        project_completed_turn(state=state, snapshot=snapshot, events=[], tool_messages=[])
