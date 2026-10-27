"""Approval preparation is private original material, never recovery authority."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest, validate_message_stage
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_graph_input import FrameworkProjectionIdentity, parse_graph_input_asset
from src.schemas.session import ExecutionRequest, SendMessageRequest
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_runtime import fresh_runtime_harness, build_runtime
from tests.test_framework_approval_continuation import claim_decision, service, receipt


@pytest_asyncio.fixture
async def prepared_approval_runtime(fresh_runtime_harness):
    h = fresh_runtime_harness
    build_runtime(h, responses=[AIMessage(id="prepared-source", content="", tool_calls=[
        {"name": "inspect_value", "args": {"value": key}, "id": key, "type": "tool_call"}
        for key in ("A", "B")]), AIMessage(id="prepared-final", content="finished")])
    session = await h.store.get_session(h.session_id)
    h.initial_task_id = h.owner.id
    h.stages = FrameworkMessageStages(h.tasks)
    request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id=h.owner.trace_id,
        user_message="Inspect A/B", normalized_input="Inspect A/B", mode_key="default", agent_key="coordinator",
        model_key="fake", runtime_binding=h.binding, context={"requested_tool_keys": ["inspect_value"]})
    await h.stages.begin(h.owner.runtime_owner, message_source_manifest(session, h.binding,
        SendMessageRequest.model_validate(h.owner.input)))
    await h.stages.freeze_execution(h.owner.runtime_owner, request)
    await h.stages.submit_runtime(h.owner.runtime_owner)
    identity = FrameworkProjectionIdentity(binding=h.binding, assistant_message_id="initial-assistant",
        snapshot_id="initial-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 27))
    from src.runtime.session_event_sink import SessionEventSink

    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with framework_owner_scope(h.owner.runtime_owner):
        first = await sink.run(h.runtime.execute_turn, session, request, projection_identity=identity,
            on_graph_input=lambda raw: h.stages.record_graph_input(h.owner.runtime_owner, raw))
    assert len(first.approvals) == 2 and h.effects == []
    h.proposals = {item["metadata"]["call_id"]: SimpleNamespace(approval=SimpleNamespace(id=item["id"]),
        job=SimpleNamespace(id=next(record["job_id"] for record in first.state["tool_results"]
            if record["approval_id"] == item["id"])))
        for item in first.approvals}
    await h.store.update_status_and_mutate_metadata(h.session_id, status=None,
        mutate=lambda metadata, status: metadata.update(pending_turn=first.pending_turn),
        turn_lease_token=h.owner.lease_token)
    await h.tasks.finish(h.owner, status="completed", result_status="waiting_approval")
    h.sessions._runtime_service = h.runtime
    h.adapter_requests.clear()
    yield h


def approval_input(h, task):
    """Synthetic input for the material transaction, not a recovery oracle."""
    raw = deepcopy(h.stages.get(h.initial_task_id, h.session_id).source_manifest["graph_input_asset"])
    preparation = raw["runtime_preparation"]
    preparation["identity"]["assistant_message_id"] = "approval-assistant"
    preparation["identity"]["snapshot_id"] = "approval-snapshot"
    preparation["initial_state"]["event_log"].append({"type": "turn.resumed", "payload": {
        "approval_id": task.reference_id, "decision_id": task.input["decision_id"], "continue_from_checkpoint": False}})
    return rehash(raw)


def rehash(raw):
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    return raw


def replace_stage(h, raw):
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        import json
        cur.execute(f"UPDATE {h.stages._table} SET record=%s::jsonb WHERE task_id=%s",
            (json.dumps(raw), h.initial_task_id))


@pytest.mark.asyncio
async def test_approval_input_is_frozen_before_adapter_and_cannot_be_replaced(prepared_approval_runtime):
    h = prepared_approval_runtime
    task = await claim_decision(h)
    execute = h.runtime._deep_agent_bound_runtime_adapter.execute
    observed = []

    async def inspect_before_graph(request):
        stage = h.stages.get(task.id, h.session_id, include_continuations=True)
        saved = stage.source_manifest["approval_graph_inputs"][task.id]
        assert stage.schema_version == 16 and saved["attempt_id"] == task.attempt_id
        assert saved["owner_epoch"] == task.runtime_owner.owner_epoch
        assert (await h.jobs.get_job(h.proposals["B"].job.id)).status.value == "waiting_approval"
        raw = saved["graph_input_asset"]
        assert (await service(h).record_graph_input(task.runtime_owner, task.reference_id,
            task.input["decision_id"], raw)).model_dump(mode="json") == saved
        assert h.stages.get(h.initial_task_id, h.session_id) == stage
        changed = deepcopy(raw)
        changed["runtime_preparation"]["identity"]["snapshot_id"] = "replacement"
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="immutable"):
            await service(h).record_graph_input(task.runtime_owner, task.reference_id,
                task.input["decision_id"], rehash(changed))
        observed.append(saved)
        return await execute(request)

    h.runtime._deep_agent_bound_runtime_adapter.execute = inspect_before_graph
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        response = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert len(observed) == 1 and response.session.status.value == "waiting_approval"
    assert h.effects == [("B", h.proposals["B"].job.id)]
    assert h.stages.get(h.initial_task_id, h.session_id).source_manifest["approval_graph_inputs"][task.id] == observed[0]
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT payload FROM {h.settings.database.postgres_event_table} WHERE session_id=%s",
            (h.session_id,))
        assert all("approval_graph_inputs" not in row["payload"] and "runtime_preparation" not in row["payload"]
            for row in cur.fetchall())


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing_original", "foreign_original", "source_binding", "stage_binding"])
async def test_recording_requires_original_prepared_binding(prepared_approval_runtime, changed):
    h = prepared_approval_runtime
    task = await claim_decision(h)
    raw = approval_input(h, task)
    stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
    manifest = stage["source_manifest"]
    if changed == "missing_original":
        manifest.pop("graph_input_asset")
    elif changed == "foreign_original":
        original = manifest["graph_input_asset"]
        binding = h.binding.model_copy(update={"runtime_version": "foreign-runtime"}).model_dump(mode="json")
        original["runtime_preparation"]["identity"]["binding"] = deepcopy(binding)
        original["runtime_preparation"]["initial_state"]["context_bundle"]["runtime_binding"] = deepcopy(binding)
        rehash(original)
    elif changed == "source_binding":
        manifest["binding_fingerprint"] = "a" * 64
    else:
        stage["binding_id"] = "foreign-binding"
        manifest.pop("graph_input_asset")
    stage["source_sha256"] = approval_value_hash(manifest)
    replace_stage(h, stage)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="original.*binding|original.*prepared"):
        await service(h).record_graph_input(task.runtime_owner, task.reference_id, task.input["decision_id"], raw)
    assert h.adapter_requests == [] and h.effects == [] and receipt(h).state == "queued"
    assert "approval_graph_inputs" not in manifest


@pytest.mark.asyncio
async def test_material_rejects_foreign_owner_decision_and_invalid_payload(prepared_approval_runtime):
    from src.runtime.store import ContinuationLeaseLostError

    h = prepared_approval_runtime
    task = await claim_decision(h)
    raw = approval_input(h, task)
    owner = task.runtime_owner
    before = h.stages.get(h.initial_task_id, h.session_id)
    for changed in ("decision", "attempt", "binding", "digest", "unknown"):
        supplied, selected = deepcopy(raw), owner
        decision = task.input["decision_id"]
        if changed == "decision":
            decision = "foreign-decision"
        elif changed == "attempt":
            selected = owner.model_copy(update={"attempt_id": "foreign-attempt"})
        elif changed == "binding":
            binding = h.binding.model_copy(update={"runtime_version": "foreign-runtime"}).model_dump(mode="json")
            supplied["runtime_preparation"]["identity"]["binding"] = deepcopy(binding)
            supplied["runtime_preparation"]["initial_state"]["context_bundle"]["runtime_binding"] = deepcopy(binding)
            rehash(supplied)
        elif changed == "digest":
            supplied["system_prompt"] = "drift"
        else:
            supplied["future"] = True
            rehash(supplied)
        with pytest.raises((FrameworkApprovalRecoveryRequired, ContinuationLeaseLostError, ValueError)):
            await service(h).record_graph_input(selected, task.reference_id, decision, supplied)
        assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await service(h).record_graph_input(owner, task.reference_id, task.input["decision_id"], raw)
    assert h.stages.get(h.initial_task_id, h.session_id) == before


@pytest.mark.asyncio
async def test_stage16_reads_original_inputs_but_never_grants_message_write_authority(prepared_approval_runtime):
    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

    h = prepared_approval_runtime
    task = await claim_decision(h)
    await service(h).record_graph_input(task.runtime_owner, task.reference_id, task.input["decision_id"], approval_input(h, task))
    stage = h.stages.get(task.id, h.session_id, include_continuations=True)
    assert h.stages.get(task.id, h.session_id) is None
    assert stage == validate_message_stage(stage.model_dump(mode="json"))
    downgraded = stage.model_dump(mode="json")
    downgraded["schema_version"] = 15
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="compatible message stage"):
        validate_message_stage(downgraded)
    for changed in ("task", "binding", "unknown", "digest", "missing_original"):
        supplied = stage.model_dump(mode="json")
        manifest = supplied["source_manifest"]
        original = manifest["approval_graph_inputs"][task.id]
        if changed == "task":
            original["task_id"] = "foreign-task"
        elif changed == "binding":
            raw = original["graph_input_asset"]
            binding = h.binding.model_copy(update={"runtime_version": "foreign-runtime"}).model_dump(mode="json")
            raw["runtime_preparation"]["identity"]["binding"] = deepcopy(binding)
            raw["runtime_preparation"]["initial_state"]["context_bundle"]["runtime_binding"] = deepcopy(binding)
            rehash(raw)
        elif changed == "unknown":
            original["future"] = True
        elif changed == "digest":
            original["graph_input_asset"]["system_prompt"] = "drift"
        else:
            manifest.pop("graph_input_asset")
        supplied["source_sha256"] = approval_value_hash(manifest)
        with pytest.raises((FrameworkMessageStageRecoveryRequired, ValueError)):
            validate_message_stage(supplied)
    with pytest.raises(FrameworkMessageStageRecoveryRequired):
        await h.stages.record_graph_input(task.runtime_owner, approval_input(h, task))
    assert h.stages.get(h.initial_task_id, h.session_id) == stage


@pytest.mark.asyncio
async def test_existing_preparation_identity_is_selected_before_stream_handler(prepared_approval_runtime, monkeypatch):
    h = prepared_approval_runtime
    task = await claim_decision(h)
    raw = approval_input(h, task)
    await service(h).record_graph_input(task.runtime_owner, task.reference_id, task.input["decision_id"], raw)
    built, forwarded = [], []
    build_handler = h.sessions._build_stream_chunk_handler

    def build(**kwargs):
        built.append(kwargs["assistant_message_id"])
        return build_handler(**kwargs)

    async def no_graph(*args, projection_identity=None, **kwargs):
        forwarded.append(projection_identity)
        return None

    monkeypatch.setattr(h.sessions, "_build_stream_chunk_handler", build)
    monkeypatch.setattr(h.runtime, "resume_framework_approval", no_graph)
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        response = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    identity = parse_graph_input_asset(raw).runtime_preparation.identity
    assert built == [identity.assistant_message_id] and forwarded == [identity]
    assert response.output.id == identity.assistant_message_id and h.adapter_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_applied_tool_original_is_readable_after_owner_settlement_without_new_authority(prepared_approval_runtime, decision):
    from src.runtime.store import ContinuationLeaseLostError

    h = prepared_approval_runtime
    task = await claim_decision(h, decision=decision)
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        response = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    await h.tasks.finish(task, status="completed", result_status="waiting_approval")
    before = h.stages.get(h.initial_task_id, h.session_id)
    events = await h.store.list_events(h.session_id)
    applied = receipt(h)
    continuation = service(h)
    with pytest.raises(ContinuationLeaseLostError):
        await continuation.read_tool_projection(task.runtime_owner, task.reference_id, task.input["decision_id"])
    reader = getattr(continuation, "read_applied_tool_projection_in_transaction", None)
    assert callable(reader), "Original applied materials need a read-only port independent of current execution ownership"
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        material = reader(cur, h.binding, task.id)
        assert reader(cur, h.binding, task.id) == material
        for binding, task_id in ((h.binding, "unknown-task"), (h.binding, h.initial_task_id),
            (h.binding.model_copy(update={"runtime_version": "foreign"}), task.id)):
            with pytest.raises(FrameworkApprovalRecoveryRequired, match="original.*task"):
                reader(cur, binding, task_id)
    message, = [item for item in response.session.messages if item.metadata.get("tool_call_id")]
    assert material.message == message and material.record.status == ("completed" if decision == "approved" else "denied")
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert await h.store.list_events(h.session_id) == events and receipt(h) == applied


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_approval_result_before_finalization_is_read_only_reproducible(prepared_approval_runtime, decision, monkeypatch):
    from deepagents import create_deep_agent
    from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER
    from src.application.deep_agents.native_terminal import NativeTerminalProof, collect_native_terminal
    from src.application.deep_agents.result_projection import project_deep_agent_result
    from src.application.runtime.tool_messages import build_runtime_tool_messages, build_chat_tool_messages
    from src.application.runtime.turn_projection import (
        completed_deep_agent_state, project_completed_turn, project_deep_agent_callback_facts, project_runtime_snapshot,
    )
    from src.schemas.framework_graph_input import FrameworkApprovalGraphInput
    from src.schemas.framework_runtime_result import FrameworkRuntimeResult
    from src.schemas.session import ExecutionEvent
    from tests.test_framework_tool_bridge import Model

    h = prepared_approval_runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    oracle = []

    async def fail_before_finalization(**kwargs):
        # A comparison oracle only: reconstruction below never reads this.
        oracle.append(FrameworkRuntimeResult.from_result(kwargs["runtime_result"]).model_dump(mode="json"))
        raise RuntimeError("injected before approval finalization")

    try:
        b = await claim_decision(h, decision=decision)
        partial = await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
        a = await claim_decision(h, key="A")
        monkeypatch.setattr(h.sessions, "_finalize_runtime_result", fail_before_finalization)
        with pytest.raises(RuntimeError, match="injected before approval finalization"):
            await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)

    saved = h.stages.get(a.id, h.session_id, include_continuations=True)
    assert "runtime_result_asset" not in saved.source_manifest
    original = FrameworkApprovalGraphInput.model_validate(saved.source_manifest["approval_graph_inputs"][a.id])
    assert original.attempt_id == a.attempt_id and original.owner_epoch == a.runtime_owner.owner_epoch
    preparation = original.graph_input_asset.runtime_preparation
    before_events = await h.store.list_events(h.session_id)
    declaration, = [event for event in before_events if event.type == "runtime.event_log_completed"]
    proof = NativeTerminalProof.model_validate(declaration.payload["completion"]["native_terminal"])
    read = await h.store.read_runtime_event_completion(h.binding, proof.model_dump(mode="json"))
    assert read.completion.task_id == a.id and read.completion.attempt_id == original.attempt_id
    assert read.event_log[:len(preparation.initial_state["event_log"])] == preparation.initial_state["event_log"]
    model = Model(responses=[])
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    flags = original.graph_input_asset.feature_flags
    options, cleanup = adapter._configure_harness(model, read_only_filesystem_enabled=flags["read_only_filesystem"],
        read_only_max_file_size_mb=10, context=original.graph_input_asset.context,
        cognitive_planning_enabled=flags["cognitive_planning"], cognitive_subagents_enabled=flags["cognitive_subagents"])

    async def forbidden(*args, **kwargs):
        pytest.fail("Read-only preparation reconstruction must not execute graph/model/tool/finalizer")

    monkeypatch.setattr(adapter, "execute", forbidden)
    monkeypatch.setattr(model, "_agenerate", forbidden)
    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    try:
        async with h.provider.open(owner=a.runtime_owner) as saver:
            graph = create_deep_agent(model=model, tools=[], checkpointer=saver, **options)
            monkeypatch.setattr(graph, "ainvoke", forbidden)
            selected = {"configurable": {"thread_id": h.binding.thread_id, "checkpoint_id": proof.checkpoint_id,
                CONFIG_KEY_CHECKPOINTER: saver}}
            before_native = await saver.aget_tuple(selected)
            native = await collect_native_terminal(graph, saver, selected)
            assert native.proof == proof
            facts = project_deep_agent_callback_facts(read.event_log, turn_id=h.binding.turn_id,
                returned_event_offset=read.completion.returned_event_offset)
            result = project_deep_agent_result(native.values, stage=facts.adapter_stage,
                subagent_events=facts.subagent_events, native_terminal=proof.model_dump(mode="json"))

            # Read the applied originals through their durable receipt/link,
            # not from the comparison oracle, today's renderer or a new clock.
            materials = {}
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                for task in (b, a):
                    material = service(h).read_applied_tool_projection_in_transaction(cur, h.binding, task.id)
                    materials[material.record.call_id] = material
            records = [materials[item["call_id"]].record.model_dump(mode="python")
                for item in preparation.initial_state["tool_results"]]
            initial = deepcopy(preparation.initial_state)
            initial["tool_messages"] = build_runtime_tool_messages(h.binding.turn_id, records,
                binding_id=h.binding.binding_id, original_materials=materials.values(), require_original=True)
            state = completed_deep_agent_state(initial, result, tool_records=records,
                binding=h.binding, pilot_stage=facts.pilot_stage)
            state["event_log"] = read.event_log
            identity = preparation.identity
            snapshot = project_runtime_snapshot(session_id=h.session_id, state=state, snapshot_id=identity.snapshot_id,
                version=identity.snapshot_version, created_at=identity.snapshot_created_at)
            projected = project_completed_turn(state=state, snapshot=snapshot,
                events=[ExecutionEvent(session_id=h.session_id, **item) for item in read.event_log[read.completion.returned_event_offset:]],
                tool_messages=build_chat_tool_messages(h.binding.turn_id, records, binding_id=h.binding.binding_id,
                    hygiene_metadata={}, original_materials=materials.values()))
            assert FrameworkRuntimeResult.from_result(projected).model_dump(mode="json") == oracle[0]
            assert await saver.aget_tuple(selected) == before_native
    finally:
        if cleanup is not None:
            cleanup()
    assert await h.store.list_events(h.session_id) == before_events
    assert h.stages.get(h.initial_task_id, h.session_id) == saved
    assert (await h.store.get_latest_snapshot(h.session_id)).id == partial.session.last_snapshot.id
    assert h.effects == ([("B", h.proposals["B"].job.id), ("A", h.proposals["A"].job.id)]
        if decision == "approved" else [("A", h.proposals["A"].job.id)])
