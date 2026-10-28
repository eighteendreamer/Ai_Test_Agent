"""Seal the original completed approval, never replay its native execution."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
from tests.test_framework_approval_result_reconstruction import (
    harness, checkpoint_harness, projection_harness, fresh_runtime_harness,
    prepared_approval_runtime, approval_asset_gap,
)
from tests.test_framework_approval_graph_input import frozen_approval_result
from tests.test_framework_approval_recovery import run_delivery
from tests.test_framework_approval_continuation import receipt


@pytest.mark.parametrize("changed", [None, "missing_flag", "missing_marker", "bool_flag", "future_flag", "bool_marker"])
def test_result_time_contract_requires_prepared_stage20_without_changing_legacy(changed):
    from src.schemas.framework_approval import approval_value_hash
    from src.schemas.framework_graph_input import parse_graph_input_asset
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred
    from tests.test_framework_graph_input_asset import prepared_graph_input
    from tests.test_runtime_binding_persistence import make_binding

    legacy = prepared_graph_input(make_binding("session-1", "turn-1"), "trace-1")
    raw = legacy.model_dump(mode="json")
    assert parse_graph_input_asset(raw).model_dump(mode="json") == raw
    assert legacy.required_message_stage_version == 12
    context = raw["runtime_preparation"]["initial_state"]["context_bundle"]
    raw["feature_flags"]["result_projection_version"] = 1
    context["framework_result_projection_version"] = 1
    if changed == "missing_flag":
        raw["feature_flags"].pop("result_projection_version")
    elif changed == "missing_marker":
        context.pop("framework_result_projection_version")
    elif changed in {"bool_flag", "future_flag"}:
        raw["feature_flags"]["result_projection_version"] = True if changed == "bool_flag" else 2
    elif changed == "bool_marker":
        context["framework_result_projection_version"] = True
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    if changed is not None:
        with pytest.raises(ValueError):
            parse_graph_input_asset(raw)
    else:
        assert parse_graph_input_asset(raw).required_message_stage_version == 20
        with pytest.raises(TaskDeferred):
            FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=19).require_message_stage(20)
        FrameworkWorkerCapabilities("a" * 64, True).require_message_stage(20)
        with pytest.raises(TaskDeferred):
            FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=20).require_message_stage(21)
        with pytest.raises(TaskDeferred):
            FrameworkWorkerCapabilities("a" * 64, True).require_message_stage(22)


@pytest.mark.asyncio
async def test_new_result_time_is_the_original_durable_graph_boundary(frozen_approval_result):
    h = frozen_approval_result
    events = await h.store.list_events(h.session_id)
    completion, = [event for event in events if event.type == "runtime.event_log_completed"]
    original_time = datetime.fromisoformat(completion.payload["completion"]["events"][-1]["timestamp"])
    assert h.result_asset.projection_at == original_time
    preparation = h.stages.get(h.initial_task_id, h.session_id).source_manifest["approval_graph_inputs"][h.result_task.id]
    assert preparation["graph_input_asset"]["feature_flags"]["result_projection_version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", ["api-tester", {"tool_key": "api-tester", "legacy_terminal": True}], indirect=True)
@pytest.mark.parametrize("approval_asset_gap", ["approved", "denied", {"decision": "approved", "capture_budget": True}], indirect=True)
async def test_owned_reconciliation_seals_original_and_delivers_only_finalization(approval_asset_gap, monkeypatch):
    h = approval_asset_gap
    before = h.stages.get(h.initial_task_id, h.session_id)
    effects, calls = deepcopy(h.effects), len(h.adapter_requests)
    budget = await h.calls.snapshot(h.binding)
    receipts = [receipt(h, key) for key in ("A", "B")]
    jobs = [await h.jobs.get_job(h.proposals[key].job.id) for key in ("A", "B")]
    finalizations = []
    finalize = h.sessions._finalize_runtime_result

    def forbidden(*args, **kwargs):
        pytest.fail("Completed approval recovery attempted graph/model/tool execution")

    async def original_finalizer(**kwargs):
        finalizations.append(kwargs)
        return await finalize(**kwargs)

    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monkeypatch.setattr(h.runtime, "resume_framework_approval", forbidden)
    monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "execute", forbidden)
    monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", original_finalizer)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.a.id)
    assert reconciler is not None
    await run_delivery(h, reconciler.id)
    reconciled = await h.tasks.get(reconciler.id)
    assert reconciled.status == "completed"
    assert reconciled.result_payload["proof"].get("scope") == "application_finalization"
    current = h.stages.get(h.initial_task_id, h.session_id)
    asset = FrameworkRuntimeResultAsset.model_validate(current.source_manifest["runtime_result_asset"])
    assert asset.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert asset.verification_results == h.verification_oracle and asset.verification_results
    preparation = before.source_manifest["approval_graph_inputs"][h.a.id]
    assert (asset.task_id, asset.attempt_id, asset.owner_epoch) == (h.a.id, h.a.attempt_id, preparation["owner_epoch"])
    assert asset.assistant_message_id == preparation["graph_input_asset"]["runtime_preparation"]["identity"]["assistant_message_id"]
    completed, = [event for event in await h.store.list_events(h.session_id) if event.type == "runtime.event_log_completed"]
    assert asset.projection_at == datetime.fromisoformat(completed.payload["completion"]["events"][-1]["timestamp"])
    successor_id = reconciled.result_payload["successor_task_id"]
    assert not finalizations
    await run_delivery(h, successor_id)
    assert (await h.tasks.get(successor_id)).status == "completed"
    assert len(finalizations) == 1
    assert finalizations[0]["runtime_result"].state == asset.runtime_result.restore().state
    saved = h.stages.get(h.initial_task_id, h.session_id)
    all_events = await h.store.list_events(h.session_id)
    assert len([event for event in all_events if event.type == "turn.completed"
        and event.payload.get("session_status") == "completed"]) == 1
    await run_delivery(h, reconciler.id)
    await run_delivery(h, successor_id)
    assert h.stages.get(h.initial_task_id, h.session_id) == saved
    assert await h.store.list_events(h.session_id) == all_events
    assert len(finalizations) == 1
    assert await h.tasks.get(h.a.id) == h.expired
    assert await h.calls.snapshot(h.binding) == budget
    assert [receipt(h, key) for key in ("A", "B")] == receipts
    assert [await h.jobs.get_job(h.proposals[key].job.id) for key in ("A", "B")] == jobs
    assert h.effects == effects and len(h.adapter_requests) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("contract", ["result", "terminal_event"])
async def test_approval_cannot_drop_the_original_result_projection_contract(frozen_approval_result, contract):
    from src.runtime.framework_message_stages import validate_message_stage
    from src.schemas.framework_approval import approval_value_hash

    h = frozen_approval_result
    stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
    graph = stage["source_manifest"]["approval_graph_inputs"][h.result_task.id]["graph_input_asset"]
    graph["feature_flags"].pop("terminal_event_projection_version")
    graph["runtime_preparation"]["initial_state"]["context_bundle"].pop("framework_terminal_event_preparation")
    if contract == "result":
        graph["feature_flags"].pop("result_projection_version")
        graph["runtime_preparation"]["initial_state"]["context_bundle"].pop("framework_result_projection_version")
    graph["asset_sha256"] = approval_value_hash({key: value for key, value in graph.items() if key != "asset_sha256"})
    stage["source_sha256"] = approval_value_hash(stage["source_manifest"])
    with pytest.raises(FrameworkMessageStageRecoveryRequired,
        match="original " + ("result" if contract == "result" else "terminal event") + " projection"):
        validate_message_stage(stage)


@pytest.mark.asyncio
async def test_result_cannot_rehash_a_different_original_projection_time(frozen_approval_result):
    from src.schemas.framework_approval import approval_value_hash
    from tests.test_framework_approval_graph_input import replace_stage

    h = frozen_approval_result
    raw = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
    asset = raw["source_manifest"]["runtime_result_asset"]
    asset["projection_at"] = "2026-10-01T00:00:00Z"
    asset["asset_sha256"] = approval_value_hash({key: value for key, value in asset.items() if key != "asset_sha256"})
    raw["source_sha256"] = approval_value_hash(raw["source_manifest"])
    replace_stage(h, raw)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="projection time"):
        await h.results.load(h.result_task.runtime_owner)


@pytest.mark.asyncio
async def test_missing_completion_declaration_does_not_fall_back_to_native_replay(approval_asset_gap):
    h = approval_asset_gap
    from src.runtime.framework_event_completion import completion_event_id

    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE session_id=%s AND id=%s",
            (h.session_id, completion_event_id(h.binding)))
    before = h.stages.get(h.initial_task_id, h.session_id)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.a.id)
    claimed = await h.tasks.claim(reconciler.id, worker_id="missing-original-owner", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="missing-original", fencing_token=9)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="event completion is missing"):
        await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert not [task for task in await h.tasks.list_session(h.session_id) if task.parent_task_id == reconciler.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", [{"tool_key": "api-tester", "legacy_result": True}], indirect=True)
async def test_legacy_gap_remains_readable_but_cannot_invent_its_projection_time(approval_asset_gap):
    h = approval_asset_gap
    read = await h.runtime.read_completed_framework_approval(h.binding, h.a.id, tasks=h.tasks)
    assert read.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert read.verification_results == h.verification_oracle and read.verification_results
    before = h.stages.get(h.initial_task_id, h.session_id)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.a.id)
    claimed = await h.tasks.claim(reconciler.id, worker_id="legacy-gap-owner", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="legacy-gap", fencing_token=9)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="deterministic result preparation"):
        await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert not [task for task in await h.tasks.list_session(h.session_id) if task.parent_task_id == reconciler.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", [{"legacy_result": True}, {"legacy_verification": True}], indirect=True)
async def test_preexisting_result_keeps_its_original_time_and_finalizer_authority(frozen_approval_result):
    from tests.test_framework_approval_recovery import expire

    h = frozen_approval_result
    original = h.result_asset
    assert original.projection_at.tzinfo is None  # Historical allocation, not rewritten as graph completion.
    terminal = await expire(h, h.result_task)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.result_task.id)
    await run_delivery(h, reconciler.id)
    reconciled = await h.tasks.get(reconciler.id)
    assert reconciled.result_payload["proof"]["scope"] == "application_finalization"
    await run_delivery(h, reconciled.result_payload["successor_task_id"])
    stage = h.stages.get(h.initial_task_id, h.session_id)
    assert stage.phase == "projected"
    assert FrameworkRuntimeResultAsset.model_validate(stage.source_manifest["runtime_result_asset"]) == original
    assert await h.tasks.get(h.result_task.id) == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["preparation", "event", "owner"])
async def test_seal_rechecks_originals_and_live_owner_after_read(approval_asset_gap, monkeypatch, changed):
    from src.schemas.framework_approval import approval_value_hash
    from tests.test_framework_approval_graph_input import replace_stage

    h = approval_asset_gap
    reconciler = await h.tasks.ensure_framework_reconciliation(h.a.id)
    claimed = await h.tasks.claim(reconciler.id, worker_id="sealing-owner", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="sealing-original", fencing_token=9)
    read = FrameworkRuntimeResults.read_completed_approval
    reads = []

    async def read_then_change(*args, **kwargs):
        original = await read(*args, **kwargs)
        reads.append(original)
        if changed == "preparation":
            raw = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
            raw["source_manifest"]["sealing_drift"] = True
            raw["source_sha256"] = approval_value_hash(raw["source_manifest"])
            replace_stage(h, raw)
        elif changed == "event":
            event_id = original.completion.events[-1].event_id
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || %s::jsonb WHERE id=%s",
                    ('{"sealing_drift":true}', event_id))
        else:
            assert await h.store.renew_turn_execution(h.session_id, claimed.lease_token, 0)
        return original

    monkeypatch.setattr(FrameworkRuntimeResults, "read_completed_approval", read_then_change)
    with pytest.raises((FrameworkMessageStageRecoveryRequired, FrameworkApprovalRecoveryRequired, ContinuationLeaseLostError)):
        await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert len(reads) == 1
    stage = h.stages.get(h.initial_task_id, h.session_id)
    assert "runtime_result_asset" not in stage.source_manifest and not stage.approval_finalizations
    assert not [task for task in await h.tasks.list_session(h.session_id) if task.parent_task_id == reconciler.id]
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
async def test_seal_and_finalizer_publication_roll_back_as_one_owned_transaction(approval_asset_gap, monkeypatch):
    from src.runtime.framework_approval_recovery import FrameworkApprovalRecovery

    h = approval_asset_gap
    reconciler = await h.tasks.ensure_framework_reconciliation(h.a.id)
    claimed = await h.tasks.claim(reconciler.id, worker_id="sealing-owner", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="atomic-seal", fencing_token=9)
    before = h.stages.get(h.initial_task_id, h.session_id)
    events = await h.store.list_events(h.session_id)
    publish = FrameworkApprovalRecovery._publish

    def fail_publication(self, *args, **kwargs):
        if kwargs["operation"] == "approval_recover":
            raise ConnectionError("injected original seal publication rollback")
        return publish(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(FrameworkApprovalRecovery, "_publish", fail_publication)
        with pytest.raises(ConnectionError, match="seal publication rollback"):
            await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert await h.store.list_events(h.session_id) == events
    assert (await h.tasks.get(reconciler.id)).status == "running"
    assert not [task for task in await h.tasks.list_session(h.session_id) if task.parent_task_id == reconciler.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("reconcile_point,takeover_point", [
    ("none", None), ("reconcile_before_commit", None), ("reconcile_after_commit", None),
    ("none", "after_snapshot"), ("none", "after_application_commit"),
])
async def test_assetless_original_worker_kill_seals_without_execution_and_survives_takeover(
    prepared_approval_runtime, reconcile_point, takeover_point,
):
    from tests.test_framework_approval_graph_input import test_original_approval_worker_hard_kill_recovers_only_original_projections

    # Reuse the complete existing original-facts/PG/outbox/Redis/finalizer/ACK
    # contract, adding the earlier asset gap rather than copying a weaker path.
    await test_original_approval_worker_hard_kill_recovers_only_original_projections(
        prepared_approval_runtime, "before_asset", takeover_point, reconcile_point=reconcile_point)
