"""Read original approval output across the terminal-to-asset gap, not execution."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
import pytest_asyncio
from deepagents import create_deep_agent

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_runtime import fresh_runtime_harness
from tests.test_framework_approval_graph_input import prepared_approval_runtime, replace_stage
from tests.test_framework_approval_continuation import claim_decision, receipt, service
from tests.test_framework_approval_recovery import expire
from tests.test_framework_tool_bridge import Model


@pytest_asyncio.fixture
async def approval_asset_gap(prepared_approval_runtime, monkeypatch, request):
    h = prepared_approval_runtime
    selected = getattr(request, "param", "approved")
    decision = selected if isinstance(selected, str) else selected["decision"]
    if isinstance(selected, dict) and selected.get("capture_budget"):
        from src.application.models.framework_model_budget_projection import capture_model_budget_material

        execute = h.runtime._deep_agent_bound_runtime_adapter.execute

        async def capture(request):
            result = await execute(request)
            # Use the production codec on the original (empty, controlled-model)
            # root ledger during execution, not a material invented by recovery.
            material = capture_model_budget_material(h.binding, await h.calls.snapshot(h.binding))
            return replace(result, metadata={**result.metadata, "root_model_budget": material})

        monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "execute", capture)
    monitor = asyncio.create_task(asyncio.Event().wait())
    captured = []

    async def fail_before_asset(self, owner, **kwargs):
        # Comparison oracle only. The reader receives neither this result nor
        # any caller-supplied proof, event collection or preparation.
        captured.append(FrameworkRuntimeResult.from_result(kwargs["runtime_result"]))
        raise ConnectionError("injected after event seal before approval result asset")

    try:
        h.b = await claim_decision(h, decision=decision)
        await h.sessions.execute_claimed_approval(h.b, ownership_monitor=monitor)
        await h.tasks.finish(h.b, status="completed", result_status="waiting_approval")
        h.a = await claim_decision(h, key="A")
        with monkeypatch.context() as patch:
            patch.setattr(FrameworkRuntimeResults, "freeze", fail_before_asset)
            with pytest.raises(ConnectionError, match="after event seal before approval result asset"):
                await h.sessions.execute_claimed_approval(h.a, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    h.oracle, = captured
    h.expired = await expire(h, h.a)
    assert "runtime_result_asset" not in h.stages.get(h.initial_task_id, h.session_id).source_manifest
    h.result_reader = FrameworkRuntimeResults(h.tasks)
    return h


async def read_original(h, monkeypatch, *, owner=None):
    reader = getattr(h.result_reader, "read_completed_approval", None)
    assert callable(reader), "The asset gap needs a production read-only original-result assembly port"
    raw = h.stages.get(h.initial_task_id, h.session_id).source_manifest["graph_input_asset"]
    flags = raw["feature_flags"]
    model = Model(responses=[])
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    options, cleanup = adapter._configure_harness(model,
        read_only_filesystem_enabled=flags["read_only_filesystem"], read_only_max_file_size_mb=10,
        context=raw["context"], cognitive_planning_enabled=flags["cognitive_planning"],
        cognitive_subagents_enabled=flags["cognitive_subagents"])

    async def forbidden(*args, **kwargs):
        pytest.fail("Read-only result assembly attempted graph/model/tool/finalization execution")

    monkeypatch.setattr(adapter, "execute", forbidden)
    monkeypatch.setattr(adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(model, "_agenerate", forbidden)
    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    try:
        async with h.provider.open(owner=owner or h.a.runtime_owner) as saver:
            graph = create_deep_agent(model=model, tools=[], checkpointer=saver, **options)
            monkeypatch.setattr(graph, "ainvoke", forbidden)
            selected = {"configurable": {"thread_id": h.binding.thread_id}}
            before = await saver.aget_tuple(selected)
            try:
                return await reader(h.binding, h.a.id, agent=graph, saver=saver, continuation=service(h))
            finally:
                assert await saver.aget_tuple(selected) == before
    finally:
        if cleanup is not None:
            cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_asset_gap", ["approved", "denied",
    {"decision": "approved", "capture_budget": True}], indirect=True)
async def test_assetless_approval_result_is_assembled_from_durable_originals(approval_asset_gap, monkeypatch):
    h = approval_asset_gap
    stage = h.stages.get(h.initial_task_id, h.session_id)
    events = await h.store.list_events(h.session_id)
    snapshot = await h.store.get_latest_snapshot(h.session_id)
    budget = await h.calls.snapshot(h.binding)
    receipts = [receipt(h, key) for key in ("A", "B")]
    jobs = [await h.jobs.get_job(h.proposals[key].job.id) for key in ("A", "B")]
    effects, adapter_calls = deepcopy(h.effects), len(h.adapter_requests)
    original = await read_original(h, monkeypatch)
    assert original.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert original.preparation.task_id == h.a.id
    assert original.completion.task_id == h.a.id and original.completion.attempt_id == h.a.attempt_id
    assert (await read_original(h, monkeypatch)) == original
    assert await h.tasks.get(h.a.id) == h.expired
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert await h.store.list_events(h.session_id) == events
    assert await h.store.get_latest_snapshot(h.session_id) == snapshot
    assert await h.calls.snapshot(h.binding) == budget
    assert [receipt(h, key) for key in ("A", "B")] == receipts
    assert [await h.jobs.get_job(h.proposals[key].job.id) for key in ("A", "B")] == jobs
    assert h.effects == effects and len(h.adapter_requests) == adapter_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["completion", "event", "producer", "tool", "preparation", "budget"])
async def test_assetless_reader_refuses_changed_original_material(approval_asset_gap, monkeypatch, changed):
    h = approval_asset_gap
    events = await h.store.list_events(h.session_id)
    declaration, = [event for event in events if event.type == "runtime.event_log_completed"]
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if changed == "completion":
            cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE id=%s", (declaration.id,))
        elif changed == "event":
            event_id = declaration.payload["completion"]["events"][-1]["event_id"]
            cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || %s::jsonb WHERE id=%s",
                ('{"adapter_stage":"DA-E1"}', event_id))
        elif changed == "producer":
            cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET attempt_id=%s WHERE id=%s",
                (h.b.attempt_id, h.a.id))
        elif changed == "tool":
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET output_payload=%s::jsonb WHERE id=%s",
                ('{"status":"completed","value":"foreign"}', h.proposals["A"].job.id))
        elif changed == "budget":
            cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy, '{{max_calls}}', '99') WHERE binding_id=%s",
                (h.binding.binding_id,))
    if changed == "preparation":
        stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
        stage["source_manifest"]["approval_graph_inputs"].pop(h.a.id)
        from src.schemas.framework_approval import approval_value_hash

        stage["source_sha256"] = approval_value_hash(stage["source_manifest"])
        replace_stage(h, stage)
    before = await h.tasks.get(h.a.id)
    with pytest.raises((FrameworkMessageStageRecoveryRequired, FrameworkApprovalRecoveryRequired, ValueError)):
        await read_original(h, monkeypatch)
    assert await h.tasks.get(h.a.id) == before
    assert "runtime_result_asset" not in h.stages.get(h.initial_task_id, h.session_id).source_manifest


@pytest.mark.asyncio
async def test_assetless_reader_rechecks_material_after_native_io(approval_asset_gap, monkeypatch):
    from src.runtime import framework_approval_result_reader as reader
    from src.schemas.framework_approval import approval_value_hash

    h = approval_asset_gap
    collect = reader.collect_native_terminal

    async def read_then_change(*args, **kwargs):
        original = await collect(*args, **kwargs)
        stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
        raw = stage["source_manifest"]["approval_graph_inputs"][h.a.id]["graph_input_asset"]
        raw["runtime_preparation"]["identity"]["snapshot_version"] += 1
        raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
        stage["source_sha256"] = approval_value_hash(stage["source_manifest"])
        replace_stage(h, stage)
        return original

    monkeypatch.setattr(reader, "collect_native_terminal", read_then_change)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="changed during native read"):
        await read_original(h, monkeypatch)
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
async def test_assetless_reader_requires_exact_original_native_producer(approval_asset_gap, monkeypatch):
    h = approval_asset_gap
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="original bound producer"):
        await read_original(h, monkeypatch, owner=h.b.runtime_owner)
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
async def test_assetless_reader_cannot_omit_unprepared_business_job(approval_asset_gap, monkeypatch):
    h = approval_asset_gap
    original = await h.jobs.get_job(h.proposals["A"].job.id)
    # An extra settled business fact cannot silently disappear merely because
    # its original application material is absent from this preparation.
    extra = original.model_copy(update={"id": original.id + "-extra", "call_id": original.call_id + "-extra", "metadata": {}})
    await h.jobs._store.save_job(extra)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="unprepared business tool"):
        await read_original(h, monkeypatch)
    assert await h.tasks.get(h.a.id) == h.expired
