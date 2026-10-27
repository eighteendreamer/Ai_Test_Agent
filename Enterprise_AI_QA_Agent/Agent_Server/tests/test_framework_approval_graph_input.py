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
from src.runtime.framework_message_stages import (
    FrameworkMessageStages, FrameworkMessageStageRecoveryRequired, message_source_manifest, validate_message_stage,
)
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_graph_input import FrameworkProjectionIdentity, parse_graph_input_asset
from src.schemas.runtime_binding import RuntimeExecutionOwner
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
    from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset

    original = FrameworkApprovalGraphInput.model_validate(saved.source_manifest["approval_graph_inputs"][a.id])
    asset = FrameworkRuntimeResultAsset.model_validate(saved.source_manifest["runtime_result_asset"])
    assert asset.task_id == a.id and asset.attempt_id == a.attempt_id
    assert asset.assistant_message_id == original.graph_input_asset.runtime_preparation.identity.assistant_message_id
    assert saved.phase == "runtime_submitted"
    assert original.attempt_id == a.attempt_id and original.owner_epoch == a.runtime_owner.owner_epoch
    assert asset.runtime_result.model_dump(mode="json") == oracle[0]
    from src.runtime.framework_runtime_results import FrameworkRuntimeResults

    results = FrameworkRuntimeResults(h.tasks)
    assert saved.schema_version == 17
    assert await results.load(a.runtime_owner) == asset
    assert await results.freeze(a.runtime_owner, runtime_result=asset.runtime_result.restore(),
        assistant_message_id=asset.assistant_message_id, user_message=asset.user_message,
        verification_results=asset.verification_results) == asset
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


@pytest_asyncio.fixture
async def frozen_approval_result(prepared_approval_runtime, monkeypatch):
    from src.runtime.framework_runtime_results import FrameworkRuntimeResults

    h = prepared_approval_runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        b = await claim_decision(h)
        await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
        h.result_task = await claim_decision(h, key="A")

        async def pause(**kwargs):
            raise ConnectionError("injected before original approval finalization")

        with monkeypatch.context() as patch:
            patch.setattr(h.sessions, "_finalize_runtime_result", pause)
            with pytest.raises(ConnectionError, match="before original approval finalization"):
                await h.sessions.execute_claimed_approval(h.result_task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    h.results = FrameworkRuntimeResults(h.tasks)
    h.result_asset = await h.results.load(h.result_task.runtime_owner)
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["task", "attempt", "epoch", "assistant", "snapshot", "decision", "format"])
async def test_original_approval_result_rejects_rehashed_identity_drift(frozen_approval_result, changed):
    h = frozen_approval_result
    stage = h.stages.get(h.initial_task_id, h.session_id)
    record = stage.model_dump(mode="json")
    manifest = record["source_manifest"]
    raw = manifest["runtime_result_asset"]
    if changed == "task":
        raw["task_id"] = h.initial_task_id
    elif changed == "attempt":
        raw["attempt_id"] += "-foreign"
    elif changed == "epoch":
        raw["owner_epoch"] += 1
    elif changed == "assistant":
        raw["assistant_message_id"] += "-foreign"
    elif changed == "snapshot":
        raw["runtime_result"]["snapshot"]["id"] += "-foreign"
    elif changed == "decision":
        preparation = manifest["approval_graph_inputs"][h.result_task.id]
        preparation["decision_id"] += "-foreign"
        graph = preparation["graph_input_asset"]
        graph["runtime_preparation"]["initial_state"]["event_log"][-1]["payload"]["decision_id"] = preparation["decision_id"]
        graph["asset_sha256"] = approval_value_hash({key: value for key, value in graph.items() if key != "asset_sha256"})
    else:
        record["schema_version"] = 16
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    record["source_sha256"] = approval_value_hash(manifest)
    replace_stage(h, record)
    with pytest.raises((FrameworkMessageStageRecoveryRequired, ValueError)):
        await h.results.load(h.result_task.runtime_owner)
    # No finalizer ran and no original output was replaced on rejection.
    assert (await h.store.get_latest_snapshot(h.session_id)).stage == "waiting_approval"
    assert h.stages._get_record(h.initial_task_id, h.session_id) == record


@pytest.mark.asyncio
async def test_original_approval_result_is_fenced_after_owner_expiry(frozen_approval_result):
    from src.runtime.store import ContinuationLeaseLostError

    h = frozen_approval_result
    task, asset = h.result_task, h.result_asset
    before = h.stages.get(h.initial_task_id, h.session_id)
    assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await h.results.load(task.runtime_owner)
    with pytest.raises(ContinuationLeaseLostError):
        await h.results.freeze(task.runtime_owner, runtime_result=asset.runtime_result.restore(),
            assistant_message_id=asset.assistant_message_id, user_message=asset.user_message,
            verification_results=asset.verification_results)
    assert h.stages.get(h.initial_task_id, h.session_id) == before


@pytest.mark.asyncio
async def test_stage17_does_not_serialize_new_finalization_fields(frozen_approval_result):
    import json

    h = frozen_approval_result
    stage = h.stages.get(h.initial_task_id, h.session_id)
    assert stage.schema_version == 17
    assert "approval_finalizations" not in stage.model_dump(mode="json")
    assert "approval_finalizations" not in json.loads(stage.model_dump_json())


async def queue_approval_finalization(h):
    from tests.test_framework_approval_recovery import expire, run_delivery

    terminal = await expire(h, h.result_task)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.result_task.id)
    assert reconciler is not None
    await run_delivery(h, reconciler.id)
    reconciled = await h.tasks.get(reconciler.id)
    assert reconciled.status == "completed", reconciled.error_code
    return terminal, await h.tasks.get(reconciled.result_payload["successor_task_id"])


@pytest.mark.asyncio
async def test_finalization_proof_cannot_be_used_at_the_native_runtime_port(frozen_approval_result):
    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    task = await h.tasks.claim(successor.id, worker_id="narrow-proof", expected_binding=h.binding)
    await h.tasks.begin(task, resource_id="finalization-only", fencing_token=7)
    requests, effects = list(h.adapter_requests), list(h.effects)
    with framework_owner_scope(task.runtime_owner), pytest.raises(FrameworkApprovalRecoveryRequired):
        await h.runtime.resume_framework_approval(await h.store.get_session(h.session_id),
            task.reference_id, task.input["decision_id"])
    assert h.adapter_requests == requests and h.effects == effects
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
async def test_finalization_proof_rejects_unknown_authority_before_claim(frozen_approval_result):
    import json
    from tests.test_framework_approval_recovery import run_delivery

    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    original = h.stages.get(h.initial_task_id, h.session_id)
    proof = {**successor.input["recovery_proof"], "native_consumed": True}
    # Preserve parent/child equality to exercise the scoped format contract,
    # not merely the durable-parent comparison.
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        payload = {**successor.input, "recovery_proof": proof}
        cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=%s::jsonb,input_hash=%s WHERE id=%s",
            (json.dumps(payload), approval_value_hash(payload), successor.id))
        cur.execute(f"UPDATE {h.tasks._tasks} SET result_payload=jsonb_set(result_payload,'{{proof}}',%s::jsonb) WHERE id=%s",
            (json.dumps(proof), successor.parent_task_id))
    requests, effects = list(h.adapter_requests), list(h.effects)
    await run_delivery(h, successor.id)
    rejected = await h.tasks.get(successor.id)
    assert rejected.status == "recovery_required" and rejected.attempt_no == 0
    assert h.stages.get(h.initial_task_id, h.session_id) == original
    assert h.adapter_requests == requests and h.effects == effects
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["project_id", "trace_id", "approval", "input_hash"])
async def test_finalization_successor_preserves_the_original_task_envelope(frozen_approval_result, changed):
    import json

    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    original = h.stages.get(h.initial_task_id, h.session_id)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if changed == "approval":
            payload = deepcopy(successor.input)
            payload["approval"]["reason"] = "not the original approval"
            cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=%s::jsonb,input_hash=%s WHERE id=%s",
                (json.dumps(payload), approval_value_hash(payload), successor.id))
        elif changed == "input_hash":
            cur.execute(f"UPDATE {h.tasks._tasks} SET input_hash=%s WHERE id=%s", ("0" * 64, successor.id))
        else:
            cur.execute(f"UPDATE {h.tasks._tasks} SET {changed}=%s WHERE id=%s", ("foreign-envelope", successor.id))
    requests, effects = list(h.adapter_requests), list(h.effects)
    # Also cover direct claim callers; a matching outbox command is checked
    # separately by the existing dispatcher contract.
    rejected = await h.tasks.claim(successor.id, worker_id="envelope-check", expected_binding=h.binding)
    assert rejected.status == "recovery_required" and rejected.attempt_no == 0
    assert h.stages.get(h.initial_task_id, h.session_id) == original
    assert h.adapter_requests == requests and h.effects == effects
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
async def test_finalization_cannot_overtake_an_explicit_interrupt(frozen_approval_result):
    from tests.test_framework_approval_recovery import run_delivery

    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    original = h.stages.get(h.initial_task_id, h.session_id)
    await h.store.update_status_and_mutate_metadata(h.session_id, status=None,
        mutate=lambda metadata, status: metadata.setdefault("control", {}).update(last_interrupt_binding_id=h.binding.binding_id))
    requests, effects = list(h.adapter_requests), list(h.effects)
    await run_delivery(h, successor.id)
    rejected = await h.tasks.get(successor.id)
    assert rejected.status == "recovery_required" and rejected.attempt_no == 0
    assert h.stages.get(h.initial_task_id, h.session_id) == original
    assert h.adapter_requests == requests and h.effects == effects
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
async def test_finalization_rejects_a_later_claim_before_publication(frozen_approval_result):
    from tests.test_framework_approval_recovery import expire
    from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload

    h = frozen_approval_result
    terminal = await expire(h, h.result_task)
    original = h.stages.get(h.initial_task_id, h.session_id)
    # Simulate a Session advanced by another entry point; the later task
    # itself is submitted/claimed normally, not an invented task row.
    await h.store.update_status_and_mutate_metadata(h.session_id, status="completed",
        mutate=lambda metadata, status: metadata.pop("pending_turn", None))
    binding = make_binding(h.session_id, "later-claimed-turn")
    later = await h.tasks.submit_v2(make_command(h, binding), make_payload().model_copy(update={"submit_mode": "queued"}),
        make_message(h, binding), idempotency_key="later-claimed-turn")
    later = await h.tasks.claim(later.id, worker_id="later-owner", expected_binding=binding)
    assert later.status == "claimed"
    await h.tasks.finish(later, status="completed", result_status="completed")
    assert await h.tasks.ensure_framework_reconciliation(h.result_task.id) is None
    assert h.stages.get(h.initial_task_id, h.session_id) == original
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
async def test_finalization_commit_stays_readable_after_a_later_turn_claim(frozen_approval_result):
    from src.runtime.framework_application_projection import require_application_commit
    from tests.test_framework_approval_recovery import run_delivery
    from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload

    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    await run_delivery(h, successor.id)
    completed = await h.tasks.get(successor.id)
    assert completed.status == "completed"
    stage = h.stages.get(h.initial_task_id, h.session_id)
    binding = make_binding(h.session_id, "later-history-turn")
    later = await h.tasks.submit_v2(make_command(h, binding), make_payload(), make_message(h, binding),
        idempotency_key="later-history-turn")
    later = await h.tasks.claim(later.id, worker_id="later-history-owner", expected_binding=binding)
    assert later.status == "claimed"
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT * FROM {h.settings.database.postgres_session_table} WHERE id=%s", (h.session_id,))
        proof = require_application_commit(cur, h.settings, cur.fetchone(), h.binding, stage)
    assert proof.task_id == successor.id and proof.output_message_id == h.result_asset.assistant_message_id
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert await h.tasks.get(successor.id) == completed
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["result", "attempt", "epoch", "budget", "format", "owner", "pending"])
async def test_finalization_admission_drift_is_rejected_before_claim(frozen_approval_result, changed):
    from tests.test_framework_approval_recovery import run_delivery

    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    stage = h.stages.get(h.initial_task_id, h.session_id)
    record = stage.model_dump(mode="json")
    link = record["approval_finalizations"][-1]
    if changed == "result":
        link["result_sha256"] = "0" * 64
    elif changed == "attempt":
        link["predecessor_attempt_id"] += "-foreign"
    elif changed == "epoch":
        link["predecessor_owner_epoch"] += 1
    elif changed == "budget":
        link["budget_sha256"] = "0" * 64
    elif changed == "format":
        record["schema_version"] = 17
    elif changed == "owner":
        link["owner_attempt_id"], link["owner_epoch"] = "foreign-attempt", 99
    else:
        await h.store.update_status_and_mutate_metadata(h.session_id, status=None,
            mutate=lambda metadata, status: metadata.update(pending_turn={"turn_id": "foreign-turn"}))
    replace_stage(h, record)
    requests, effects = list(h.adapter_requests), list(h.effects)
    await run_delivery(h, successor.id)
    rejected = await h.tasks.get(successor.id)
    assert rejected.status == "recovery_required" and rejected.attempt_no == 0
    assert h.stages._get_record(h.initial_task_id, h.session_id) == record
    assert h.adapter_requests == requests and h.effects == effects
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
async def test_finalization_repeated_expiry_keeps_each_original_attempt(frozen_approval_result, monkeypatch):
    from tests.test_framework_approval_recovery import expire, run_delivery
    from src.runtime.store import ContinuationLeaseLostError

    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    history = [terminal]
    requests, effects = list(h.adapter_requests), list(h.effects)
    for index in range(3):
        task = await h.tasks.claim(successor.id, worker_id=f"lost-finalizer-{index}", expected_binding=h.binding)
        await h.tasks.begin(task, resource_id="missing-projection", fencing_token=index + 7)
        assert await h.results.load(task.runtime_owner) == h.result_asset
        history.append(await expire(h, task))
        with pytest.raises(ContinuationLeaseLostError):
            await h.results.load(task.runtime_owner)
        reconciler = await h.tasks.ensure_framework_reconciliation(task.id)
        assert reconciler is not None
        await run_delivery(h, reconciler.id)
        reconciled = await h.tasks.get(reconciler.id)
        assert reconciled.status == "completed", reconciled.error_code
        successor = await h.tasks.get(reconciled.result_payload["successor_task_id"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("Repeated takeover cannot rerun native execution or verification")
    monkeypatch.setattr(h.runtime, "resume_framework_approval", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    await run_delivery(h, successor.id)
    assert (await h.tasks.get(successor.id)).status == "completed"
    for original in history:
        assert await h.tasks.get(original.id) == original
    stage = h.stages.get(h.initial_task_id, h.session_id)
    assert len(stage.approval_finalizations) == 4 and stage.phase == "projected"
    assert stage.source_manifest["runtime_result_asset"] == h.result_asset.model_dump(mode="json")
    assert h.adapter_requests == requests and h.effects == effects


@pytest.mark.asyncio
async def test_finalization_resource_deferral_reclaims_only_an_unstarted_attempt(frozen_approval_result):
    from tests.test_framework_approval_recovery import run_delivery

    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    first = await h.tasks.claim(successor.id, worker_id="waiting-finalizer", expected_binding=h.binding)
    await h.tasks.defer(first, "controlled unavailable resource")
    await run_delivery(h, successor.id)
    completed = await h.tasks.get(successor.id)
    assert completed.status == "completed" and completed.attempt_no == 2
    assert completed.attempt_id != first.attempt_id
    assert await h.tasks.get(h.result_task.id) == terminal
    assert h.stages.get(h.initial_task_id, h.session_id).approval_finalizations[-1].owner_attempt_id == completed.attempt_id


@pytest.mark.asyncio
async def test_finalization_publication_rolls_back_its_stage_grant_and_outbox(frozen_approval_result, monkeypatch):
    from tests.test_framework_approval_recovery import expire

    h = frozen_approval_result
    terminal = await expire(h, h.result_task)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.result_task.id)
    task = await h.tasks.claim(reconciler.id, worker_id="atomic-finalizer", expected_binding=h.binding)
    await h.tasks.begin(task, resource_id="atomic-publication", fencing_token=5)
    before = h.stages.get(h.initial_task_id, h.session_id)
    append = h.tasks._event

    def fail(cur, row, event_type, payload):
        if event_type == "framework.recovery_succeeded":
            raise ConnectionError("injected finalization publication rollback")
        return append(cur, row, event_type, payload)

    with monkeypatch.context() as patch:
        patch.setattr(h.tasks, "_event", fail)
        with pytest.raises(ConnectionError, match="publication rollback"):
            await h.runtime.reconcile_framework_task(task, tasks=h.tasks)
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert not [item for item in await h.tasks.list_session(h.session_id) if item.operation == "approval_recover"]
    assert (await h.tasks.get(task.id)).status == "running"
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
async def test_independent_worker_consumes_only_the_original_approval_asset(frozen_approval_result):
    from tests.test_framework_partial_projection_recovery import complete_in_new_process

    h = frozen_approval_result
    terminal, successor = await queue_approval_finalization(h)
    requests, effects = list(h.adapter_requests), list(h.effects)
    await complete_in_new_process(h, successor)
    completed = await h.tasks.get(successor.id)
    assert completed.status == "completed"
    assert await h.tasks.get(h.result_task.id) == terminal
    stage = h.stages.get(h.initial_task_id, h.session_id)
    assert stage.phase == "projected" and stage.source_manifest["runtime_result_asset"] == h.result_asset.model_dump(mode="json")
    assert h.adapter_requests == requests and h.effects == effects


@pytest.mark.asyncio
@pytest.mark.parametrize("point,takeover_point", [
    ("after_asset", None), ("after_snapshot", None), ("after_application_commit", None),
    ("after_asset", "after_snapshot"), ("after_asset", "after_application_commit"),
])
async def test_original_approval_worker_hard_kill_recovers_only_original_projections(prepared_approval_runtime, point, takeover_point):
    import multiprocessing
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.framework_application_projection import COMMIT_KEY, require_application_commit
    from src.runtime.framework_runtime_results import FrameworkRuntimeResults
    from src.runtime.store import ContinuationLeaseLostError
    from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
    from src.schemas.session import ApprovalDecisionRequest
    from tests.test_framework_approval_recovery import expire, run_delivery
    from tests.test_framework_partial_projection_recovery import reap_dead_worker_resources
    from tests.test_framework_task_control import framework_worker_process, wait_gate

    h = prepared_approval_runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        b = await claim_decision(h)
        await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    # A fresh stream isolates the last approval from old fixture outbox rows.
    h.settings.orchestration.agent_task_v2_stream = f"{h.prefix}:asset-kill:v2"
    h.settings.orchestration.resource_default_lease_seconds = 3
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="control-v2", client=h.redis)
    await queue.connect()
    accepted = await h.dispatcher.submit_approval(h.session_id, h.proposals["A"].approval.id,
        ApprovalDecisionRequest(decision="approved"))
    assert await h.outbox.relay_once(queue) == 1
    processes = []

    async def worker(pause_at, recovery_mode=None):
        process = multiprocessing.get_context("spawn").Process(target=framework_worker_process,
            args=(h.settings, h.prefix, h.binding, pause_at, recovery_mode))
        processes.append(process)
        process.start()
        if pause_at == "none":
            await asyncio.to_thread(process.join, 45)
            assert not process.is_alive() and process.exitcode == 0
        else:
            phase = await wait_gate(h.redis, f"{h.prefix}:control:entered", 30)
            assert phase[1] == pause_at, f"Worker exited {process.exitcode} before reaching {pause_at}"
        return process

    try:
        original_process = await worker(point)
        original = await h.tasks.get(accepted.task.id)
        assert original.status == "running"
        stages_before = h.stages.get(h.initial_task_id, h.session_id)
        asset = FrameworkRuntimeResultAsset.model_validate(stages_before.source_manifest["runtime_result_asset"])
        assert asset.task_id == original.id and asset.attempt_id == original.attempt_id
        old_owner = RuntimeExecutionOwner(binding=h.binding, task_id=original.id, attempt_id=original.attempt_id,
            owner_epoch=asset.owner_epoch, owner_token=original.lease_token, worker_id=original.worker_id)
        assert stages_before.phase == "runtime_submitted"
        assert (COMMIT_KEY in stages_before.source_manifest) is (point == "after_application_commit")
        snapshot_before = await h.store.get_latest_snapshot(h.session_id)
        events_before = await h.store.list_events(h.session_id)
        budget_before = await h.calls.snapshot(h.binding)
        job_before = await h.jobs.get_job(h.proposals["A"].job.id)
        receipt_before = receipt(h, "A")
        assert await h.redis.get(f"{h.prefix}:framework-adapter-calls") == "1"
        assert await h.redis.get(f"{h.prefix}:framework-effects") == "1"
        original_process.terminate()
        await asyncio.to_thread(original_process.join, 10)
        assert not original_process.is_alive() and original_process.exitcode != 0
        terminal = await expire(h, original)
        with pytest.raises(ContinuationLeaseLostError):
            await FrameworkRuntimeResults(h.tasks).load(old_owner)
        await reap_dead_worker_resources(h)
        await run_delivery(h, original.id)
        reconciler = next(item for item in await h.tasks.list_session(h.session_id)
            if item.operation == "framework_reconcile" and item.reference_id == original.id)
        assert await h.outbox.relay_once(queue) == 1
        await worker("none", "finalize")
        reconciled = await h.tasks.get(reconciler.id)
        assert reconciled.status == "completed"
        successor = await h.tasks.get(reconciled.result_payload["successor_task_id"])
        assert successor.input["recovery_proof"]["scope"] == "application_finalization"
        assert await h.outbox.relay_once(queue) == 1
        takeover_terminal = None
        if takeover_point is not None:
            takeover = await worker(takeover_point, "finalize")
            interrupted_task = await h.tasks.get(successor.id)
            assert interrupted_task.status == "running"
            snapshot_before = await h.store.get_latest_snapshot(h.session_id)
            events_before = await h.store.list_events(h.session_id)
            takeover.terminate()
            await asyncio.to_thread(takeover.join, 10)
            assert not takeover.is_alive() and takeover.exitcode != 0
            takeover_terminal = await expire(h, interrupted_task)
            await reap_dead_worker_resources(h)
            await run_delivery(h, interrupted_task.id)
            reconciler = next(item for item in await h.tasks.list_session(h.session_id)
                if item.operation == "framework_reconcile" and item.reference_id == interrupted_task.id)
            assert await h.outbox.relay_once(queue) == 1
            await worker("none", "finalize")
            reconciled = await h.tasks.get(reconciler.id)
            assert reconciled.status == "completed"
            successor = await h.tasks.get(reconciled.result_payload["successor_task_id"])
            assert await h.outbox.relay_once(queue) == 1
        committed_at = takeover_point or point
        await worker("none", "ack" if committed_at == "after_application_commit" else "finalize")
        completed = await h.tasks.get(successor.id)
        assert completed.status == "completed"
        saved = h.stages.get(h.initial_task_id, h.session_id)
        assert saved.phase == "projected" and saved.source_manifest["runtime_result_asset"] == asset.model_dump(mode="json")
        assert saved.source_manifest["approval_graph_inputs"] == stages_before.source_manifest["approval_graph_inputs"]
        assert len(saved.approval_finalizations) == (2 if takeover_point is not None else 1)
        snapshot = await h.store.get_latest_snapshot(h.session_id)
        if committed_at != "after_asset":
            assert snapshot == snapshot_before
        assert snapshot.id == asset.runtime_result.snapshot.id
        session = await h.store.get_session(h.session_id)
        assert next(item for item in session.messages if item.id == asset.assistant_message_id).content == asset.runtime_result.output_text
        assert await h.jobs.get_job(job_before.id) == job_before and receipt(h, "A") == receipt_before
        assert await h.calls.snapshot(h.binding) == budget_before
        assert await h.tasks.get(original.id) == terminal
        if takeover_terminal is not None:
            assert await h.tasks.get(takeover_terminal.id) == takeover_terminal
        assert await h.redis.get(f"{h.prefix}:framework-adapter-calls") == "1"
        assert await h.redis.get(f"{h.prefix}:framework-effects") == "1"
        events = await h.store.list_events(h.session_id)
        for event in events_before:
            assert next(item for item in events if item.id == event.id) == event
        assert len([event for event in events if event.type == "turn.completed"
            and event.payload.get("session_status") == "completed"]) == 1
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"SELECT * FROM {h.settings.database.postgres_session_table} WHERE id=%s", (h.session_id,))
            commit = require_application_commit(cur, h.settings, cur.fetchone(), h.binding, saved)
        commit_owner = (takeover_terminal.id if takeover_point == "after_application_commit" else
            original.id if point == "after_application_commit" else successor.id)
        assert commit.task_id == commit_owner
        # Redeliver via Redis after task completion, not merely a local method.
        from tests.test_framework_approval_recovery import outbox
        await queue.enqueue(outbox(h, successor.id)["payload"])
        await worker("none", "drain")
        assert await queue.pending() == []
        assert await h.tasks.get(successor.id) == completed
        assert await h.store.list_events(h.session_id) == events
        assert h.stages.get(h.initial_task_id, h.session_id) == saved
        with pytest.raises(ContinuationLeaseLostError):
            await FrameworkRuntimeResults(h.tasks).load(old_owner)
    finally:
        await h.redis.rpush(f"{h.prefix}:control:release", "continue")
        for process in processes:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 10)
        await queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["asset", "snapshot", "application_commit"])
async def test_approval_asset_successor_only_finalizes_the_original_result(frozen_approval_result, monkeypatch, point):
    from tests.test_framework_approval_recovery import expire, run_delivery
    from src.runtime.framework_application_projection import require_application_commit

    h = frozen_approval_result
    asset = h.result_asset
    original_snapshot = None
    if point != "asset":
        values = dict(session=await h.store.get_session(h.session_id), runtime_result=asset.runtime_result.restore(),
            assistant_message_id=asset.assistant_message_id, user_message_override=asset.user_message,
            turn_lease_token=h.result_task.lease_token, projection_owner=h.result_task.runtime_owner, projection_asset=asset)
        with framework_owner_scope(h.result_task.runtime_owner), monkeypatch.context() as patch:
            if point == "snapshot":
                async def fail(*args, **kwargs):
                    raise ConnectionError("injected before final Session transaction")
                patch.setattr(h.store, "save_session_with_events", fail)
                with pytest.raises(ConnectionError, match="before final Session transaction"):
                    await h.sessions._finalize_runtime_result(**values)
            else:
                await h.sessions._finalize_runtime_result(**values)
        original_snapshot = await h.store.get_latest_snapshot(h.session_id)
    original = h.stages.get(h.initial_task_id, h.session_id)
    requests, effects = list(h.adapter_requests), list(h.effects)
    budget = await h.calls.snapshot(h.binding)
    original_events = await h.store.list_events(h.session_id)
    terminal = await expire(h, h.result_task)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Original approval result cannot rerun graph/model/verification/orchestration")

    monkeypatch.setattr(h.runtime, "resume_framework_approval", forbidden)
    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", forbidden)
    if point == "application_commit":
        monkeypatch.setattr(h.sessions, "_finalize_runtime_result", forbidden)
    reconciliation = await h.tasks.ensure_framework_reconciliation(h.result_task.id)
    assert reconciliation is not None
    await run_delivery(h, reconciliation.id)
    reconciled = await h.tasks.get(reconciliation.id)
    assert reconciled.status == "completed", reconciled.error_code
    successor_id = reconciled.result_payload["successor_task_id"]
    successor = await h.tasks.get(successor_id)
    assert successor.input["recovery_proof"]["scope"] == "application_finalization"
    await run_delivery(h, successor_id)
    completed = await h.tasks.get(successor_id)
    assert completed.status == "completed", completed.error_code
    saved = h.stages.get(h.initial_task_id, h.session_id)
    assert saved.phase == "projected" and saved.schema_version == 18
    assert saved.source_manifest["runtime_result_asset"] == original.source_manifest["runtime_result_asset"]
    assert saved.source_manifest["approval_graph_inputs"] == original.source_manifest["approval_graph_inputs"]
    snapshot = await h.store.get_latest_snapshot(h.session_id)
    session = await h.store.get_session(h.session_id)
    assert snapshot.id == asset.runtime_result.snapshot.id and snapshot.stage == "completed"
    if original_snapshot is not None:
        assert snapshot == original_snapshot
    assert next(item for item in session.messages if item.id == asset.assistant_message_id).content == asset.runtime_result.output_text
    assert len({item.id for item in session.messages}) == len(session.messages)
    assert await h.tasks.get(h.result_task.id) == terminal
    assert await h.calls.snapshot(h.binding) == budget
    assert h.adapter_requests == requests and h.effects == effects
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT * FROM {h.settings.database.postgres_session_table} WHERE id=%s", (h.session_id,))
        commit = require_application_commit(cur, h.settings, cur.fetchone(), h.binding, saved)
    assert commit.task_id == (h.result_task.id if point == "application_commit" else successor_id)
    assert commit.output_message_id == asset.assistant_message_id
    events = await h.store.list_events(h.session_id)
    assert len([event for event in events if event.type == "turn.completed"
        and event.payload.get("session_status") == "completed"]) == 1
    for event in original_events:
        assert next(item for item in events if item.id == event.id) == event
    await run_delivery(h, successor_id)
    assert h.stages.get(h.initial_task_id, h.session_id) == saved
    assert await h.store.list_events(h.session_id) == events
    assert await h.tasks.get(successor_id) == completed


@pytest.mark.asyncio
async def test_frozen_approval_result_never_receives_a_native_replay_successor(frozen_approval_result):
    from tests.test_framework_approval_recovery import expire, run_delivery

    h = frozen_approval_result
    task = h.result_task
    original = h.stages.get(h.initial_task_id, h.session_id)
    requests, effects = list(h.adapter_requests), list(h.effects)
    terminal = await expire(h, task)
    # Actual terminal delivery must reconcile, but its native-decision proof
    # has no authority to turn an already-frozen result into another graph run.
    await run_delivery(h, task.id)
    reconciliation = next(item for item in await h.tasks.list_session(h.session_id)
        if item.operation == "framework_reconcile" and item.reference_id == task.id)
    await run_delivery(h, reconciliation.id)
    reconciled = await h.tasks.get(reconciliation.id)
    assert reconciled.status == "completed", reconciled.error_code
    successor = await h.tasks.get(reconciled.result_payload["successor_task_id"])
    assert successor.input["recovery_proof"]["scope"] == "application_finalization"
    assert "native_consumed" not in successor.input["recovery_proof"]
    assert await h.tasks.get(task.id) == terminal
    assert h.stages.get(h.initial_task_id, h.session_id).source_manifest == original.source_manifest
    assert h.adapter_requests == requests and h.effects == effects
    # Safety rejection is not a retry instruction or permission to invent a
    # second reconciliation lineage.
    assert await h.tasks.ensure_framework_reconciliation(reconciliation.id) is None


@pytest.mark.asyncio
async def test_previously_queued_native_successor_cannot_claim_a_frozen_result(frozen_approval_result, monkeypatch):
    import src.runtime.framework_approval_recovery as recovery
    from tests.test_framework_approval_recovery import expire, run_delivery

    h = frozen_approval_result
    terminal = await expire(h, h.result_task)
    reconciliation = await h.tasks.ensure_framework_reconciliation(h.result_task.id)
    # Reproduce a queued proof issued by the preceding implementation, using
    # its real publication transaction rather than inventing task/proof rows.
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "require_native_recovery_scope", lambda *args: None, raising=False)
        import src.runtime.framework_approval_finalization as finalization
        patch.setattr(finalization, "prepare_approval_finalization", lambda *args: None)
        await run_delivery(h, reconciliation.id)
    successor_id = (await h.tasks.get(reconciliation.id)).result_payload["successor_task_id"]
    original = h.stages.get(h.initial_task_id, h.session_id)
    requests, effects = list(h.adapter_requests), list(h.effects)

    async def forbidden(*args, **kwargs):
        raise AssertionError("A frozen result cannot enter native continuation")

    monkeypatch.setattr(h.runtime, "resume_framework_approval", forbidden)
    await run_delivery(h, successor_id)
    rejected = await h.tasks.get(successor_id)
    assert rejected.attempt_no == 0, "Native proof must be rejected before acquiring execution"
    assert rejected.status == "recovery_required" and rejected.error_code == "approval_continuation_changed"
    assert await h.tasks.get(h.result_task.id) == terminal
    assert h.stages.get(h.initial_task_id, h.session_id) == original
    assert h.adapter_requests == requests and h.effects == effects


@pytest.mark.asyncio
async def test_recovered_approval_freezes_its_invocation_without_replacing_original_identity(prepared_approval_runtime, monkeypatch):
    from tests.test_framework_approval_recovery import run_delivery
    from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset

    h = prepared_approval_runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        b = await claim_decision(h)
        await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
        a = await claim_decision(h, key="A")
        continuation = h.runtime._framework_approval_continuation
        record = continuation.record_graph_input

        async def fail_after_preparation(*args, **kwargs):
            await record(*args, **kwargs)
            raise ConnectionError("injected after approval preparation before graph")

        with monkeypatch.context() as patch:
            patch.setattr(continuation, "record_graph_input", fail_after_preparation)
            with pytest.raises(ConnectionError, match="before graph"):
                await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
        original = h.stages.get(h.initial_task_id, h.session_id).source_manifest["approval_graph_inputs"][a.id]
        await h.tasks.finish(a, status="failed", result_status="failed")
        failed = await h.tasks.get(a.id)
        reconciler = await h.tasks.ensure_framework_reconciliation(a.id)
        await run_delivery(h, reconciler.id)
        successor_id = (await h.tasks.get(reconciler.id)).result_payload["successor_task_id"]
        await run_delivery(h, successor_id)
        successor = await h.tasks.get(successor_id)
        assert successor.status == "completed", successor.error_message
        before = h.stages.get(h.initial_task_id, h.session_id)
        asset = FrameworkRuntimeResultAsset.model_validate(before.source_manifest["runtime_result_asset"])
        assert asset.task_id == successor_id and asset.attempt_id == successor.attempt_id
        assert before.source_manifest["approval_graph_inputs"][a.id] == original
        assert before.source_manifest["approval_graph_inputs"][successor_id]["attempt_id"] == successor.attempt_id
        identity = parse_graph_input_asset(original["graph_input_asset"]).runtime_preparation.identity
        assert asset.assistant_message_id == identity.assistant_message_id
        assert asset.runtime_result.snapshot.id == identity.snapshot_id
        requests = list(h.adapter_requests)
        events = await h.store.list_events(h.session_id)
        await run_delivery(h, successor_id)
        assert h.stages.get(h.initial_task_id, h.session_id) == before
        assert h.adapter_requests == requests and await h.store.list_events(h.session_id) == events
        assert await h.tasks.get(a.id) == failed
        assert h.effects == [("B", h.proposals["B"].job.id), ("A", h.proposals["A"].job.id)]
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


@pytest.mark.asyncio
async def test_claimed_message_and_approval_share_the_original_turn_projection(fresh_runtime_harness):
    from src.runtime.framework_application_projection import require_application_commit

    h = fresh_runtime_harness
    build_runtime(h, responses=[AIMessage(id="worker-source", content="", tool_calls=[
        {"name": "inspect_value", "args": {"value": key}, "id": key, "type": "tool_call"}
        for key in ("A", "B")]), AIMessage(id="worker-final", content="finished")])
    h.sessions._runtime_service = h.runtime
    h.stages = FrameworkMessageStages(h.tasks)
    initial = h.owner
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        first = await h.sessions.execute_claimed_message(initial, ownership_monitor=monitor)
        assert first.session.status.value == "waiting_approval"
        waiting = h.stages.get(initial.id, h.session_id)
        assert waiting.phase == "runtime_submitted", "Waiting for decisions is not a completed Turn projection"
        assert "runtime_result_asset" not in waiting.source_manifest
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"SELECT id,metadata FROM {h.settings.database.postgres_approval_table} WHERE session_id=%s", (h.session_id,))
            h.proposals = {row["metadata"]["call_id"]: SimpleNamespace(approval=SimpleNamespace(id=row["id"]))
                for row in cur.fetchall()}
        await h.tasks.finish(initial, status="completed", result_status="waiting_approval")
        for key in ("B", "A"):
            task = await claim_decision(h, key=key)
            final = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
            await h.tasks.finish(task, status="completed", result_status=final.session.status.value)
        assert final.session.status.value == "completed" and final.output.content == "finished"
        stage = h.stages.get(initial.id, h.session_id)
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"SELECT * FROM {h.settings.database.postgres_session_table} WHERE id=%s", (h.session_id,))
            commit = require_application_commit(cur, h.settings, cur.fetchone(), h.binding, stage)
        assert commit.task_id == task.id and commit.output_message_id == final.output.id
        assert stage.source_manifest["runtime_result_asset"]["task_id"] == task.id
        assert [value for value, job_id in h.effects] == ["B", "A"]
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
