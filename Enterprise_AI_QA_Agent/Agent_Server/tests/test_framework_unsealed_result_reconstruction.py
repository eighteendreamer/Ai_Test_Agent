"""Reuse real original outputs at the missing event-declaration kill windows."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_event_completion import completion_event_id
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.schemas.framework_approval import approval_value_hash
from tests.test_framework_message_result_reconstruction import message_asset_gap, read_original as read_message
from tests.test_framework_approval_result_reconstruction import approval_asset_gap
from tests.test_framework_approval_runtime import fresh_runtime_harness
from tests.test_framework_approval_graph_input import prepared_approval_runtime
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_model_admission import admission
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness


def remove_fixture_seal(h, *, terminal_events):
    # Only fixture-owned rows, with the same original inputs/oracle as the real
    # after_graph_return/after_graph_events hard-kill samples. Do not manufacture
    # replacement callback rows or change the original native checkpoint.
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        ids = [completion_event_id(h.binding)]
        if not terminal_events:
            ids.extend(item.id for item in h.oracle.events if item.type in {"graph.plan_built", "runtime.turn_completed"})
        cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE session_id=%s AND id=ANY(%s)",
            (h.session_id, ids))


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", ["none", "tools", "planning", "api-tester"], indirect=True)
@pytest.mark.parametrize("terminal_events", [False, True])
async def test_unsealed_message_reuses_complete_original_projection_without_writes(message_asset_gap, terminal_events):
    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=terminal_events)
    events = await h.store.list_events(h.session_id)
    budget = await h.calls.snapshot(h.binding)
    stage = h.stages.get(h.initial_task_id, h.session_id)
    tasks = await h.tasks.list_session(h.session_id)
    effects, requests = deepcopy(h.effects), list(h.adapter_requests)
    result = await read_message(h)
    assert result.completion is None, "Read-only derivation must not fabricate an original completion declaration"
    assert result.checkpoint_event_prefix is not None
    # These fixtures deliberately run without a model-call policy: retain the
    # historical v1 prefix/absent budget, never invent an empty budget object.
    assert not budget.calls and result.checkpoint_event_prefix.schema_version == 1
    assert result.checkpoint_event_prefix.root_model_budget_material is None
    assert "root_model_budget_material" not in result.checkpoint_event_prefix.model_dump(mode="json")
    assert result.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert result.verification_results == h.verification_oracle
    assert await read_message(h) == result
    assert await h.store.list_events(h.session_id) == events
    assert await h.calls.snapshot(h.binding) == budget
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert h.effects == effects and h.adapter_requests == requests
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert await h.tasks.list_session(h.session_id) == tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_asset_gap", ["approved", "denied"], indirect=True)
@pytest.mark.parametrize("terminal_events", [False, True])
async def test_unsealed_approval_reconciliation_seals_original_and_only_finalizes(approval_asset_gap, monkeypatch, terminal_events):
    from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
    from tests.test_framework_approval_recovery import run_delivery
    from tests.test_framework_approval_continuation import receipt

    h = approval_asset_gap
    remove_fixture_seal(h, terminal_events=terminal_events)
    budget, effects, requests = await h.calls.snapshot(h.binding), deepcopy(h.effects), list(h.adapter_requests)
    receipts = [receipt(h, key) for key in ("A", "B")]
    jobs = await h.jobs.list_jobs(session_id=h.session_id)
    snapshot_before = await h.store.get_latest_snapshot(h.session_id)
    finalizations, finalize = [], h.sessions._finalize_runtime_result

    def forbidden(*args, **kwargs):
        pytest.fail("Unsealed approval recovery attempted graph/model/tool execution")

    async def finalizer(**kwargs):
        finalizations.append(kwargs)
        return await finalize(**kwargs)

    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monkeypatch.setattr(h.runtime, "resume_framework_approval", forbidden)
    monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "execute", forbidden)
    monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", finalizer)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.a.id)
    claimed = await h.tasks.claim(reconciler.id, worker_id="unsealed-approval", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="unsealed-approval", fencing_token=9)
    successor = await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert successor.input["recovery_proof"].get("scope") == "application_finalization"
    stage = h.stages.get(h.initial_task_id, h.session_id)
    asset = FrameworkRuntimeResultAsset.model_validate(stage.source_manifest["runtime_result_asset"])
    assert asset.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert asset.verification_results == h.verification_oracle
    assert not finalizations and await h.store.get_latest_snapshot(h.session_id) == snapshot_before
    await run_delivery(h, successor.id)
    assert (await h.tasks.get(successor.id)).status == "completed" and len(finalizations) == 1
    events, saved = await h.store.list_events(h.session_id), h.stages.get(h.initial_task_id, h.session_id)
    assert len([item for item in events if item.type == "runtime.event_log_completed"]) == 1
    assert len([item for item in events if item.type == "turn.completed"
        and item.payload.get("session_status") == "completed"]) == 1
    await run_delivery(h, reconciler.id)
    await run_delivery(h, successor.id)
    assert await h.store.list_events(h.session_id) == events
    assert h.stages.get(h.initial_task_id, h.session_id) == saved and len(finalizations) == 1
    assert await h.tasks.get(h.a.id) == h.expired
    assert await h.calls.snapshot(h.binding) == budget and h.effects == effects and h.adapter_requests == requests
    assert [receipt(h, key) for key in ("A", "B")] == receipts
    assert await h.jobs.list_jobs(session_id=h.session_id) == jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_reader", "reader_error"])
async def test_unsealed_approval_read_failure_never_grants_native_replay(approval_asset_gap, failure):
    from contextlib import asynccontextmanager
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
    from src.runtime.framework_approval_recovery import FrameworkApprovalRecovery

    h = approval_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    reconciler = await h.tasks.ensure_framework_reconciliation(h.a.id)
    claimed = await h.tasks.claim(reconciler.id, worker_id="unreadable-approval", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="unreadable-approval", fencing_token=9)
    stage, events = h.stages.get(h.initial_task_id, h.session_id), await h.store.list_events(h.session_id)

    @asynccontextmanager
    async def fail_reader(*args, **kwargs):
        raise ConnectionError("injected source-only reader failure")
        yield  # pragma: no cover

    recovery = FrameworkApprovalRecovery(h.tasks, h.runtime._framework_approval_continuation,
        native_reader=fail_reader if failure == "reader_error" else None)
    error, match = ((ConnectionError, "source-only reader failure") if failure == "reader_error"
        else (FrameworkApprovalRecoveryRequired, "source-only native reader"))
    with pytest.raises(error, match=match):
        await recovery.apply(claimed)
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert await h.store.list_events(h.session_id) == events
    assert not [item for item in await h.tasks.list_session(h.session_id) if item.parent_task_id == reconciler.id]
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", ["none", "tools", "planning"], indirect=True)
@pytest.mark.parametrize("terminal_events", [False, True])
async def test_unsealed_message_admission_seals_once_and_runs_only_existing_finalizer(message_asset_gap, monkeypatch, terminal_events):
    from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
    from tests.test_framework_approval_recovery import run_delivery

    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=terminal_events)
    budget, effects, requests = await h.calls.snapshot(h.binding), deepcopy(h.effects), list(h.adapter_requests)
    reader = h.runtime._deep_agent_bound_runtime_adapter.open_native_reader
    finalizations = []
    finalize = h.sessions._finalize_runtime_result

    def forbidden(*args, **kwargs):
        pytest.fail("Unsealed admission/finalization attempted graph/model/tool execution")

    async def record_finalizer(**kwargs):
        finalizations.append(kwargs)
        return await finalize(**kwargs)

    monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
    monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "execute", forbidden)
    monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", record_finalizer)
    successor = await h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=reader)
    assert successor is not None, "Complete checkpoint originals must atomically seal and admit only finalization"
    stage = h.stages.get(h.initial_task_id, h.session_id)
    asset = FrameworkRuntimeResultAsset.model_validate(stage.source_manifest["runtime_result_asset"])
    assert asset.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert asset.verification_results == h.verification_oracle
    link, = stage.recoveries
    assert link.task_id == successor.id and link.resume_phase == "finalizing"
    assert await h.store.get_latest_snapshot(h.session_id) is None and not finalizations
    events = await h.store.list_events(h.session_id)
    assert len([event for event in events if event.id == completion_event_id(h.binding)]) == 1
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=reader) == successor
    assert await h.store.list_events(h.session_id) == events
    await run_delivery(h, successor.id)
    assert (await h.tasks.get(successor.id)).status == "completed" and len(finalizations) == 1
    saved, events = h.stages.get(h.initial_task_id, h.session_id), await h.store.list_events(h.session_id)
    assert saved.phase == "projected"
    assert len([event for event in events if event.type == "turn.completed"]) == 1
    await run_delivery(h, successor.id)
    assert h.stages.get(h.initial_task_id, h.session_id) == saved
    assert await h.store.list_events(h.session_id) == events and len(finalizations) == 1
    assert await h.tasks.get(h.owner.id) == h.expired
    assert await h.calls.snapshot(h.binding) == budget and h.effects == effects and h.adapter_requests == requests


async def claim_unsealed_approval_reconciler(h):
    reconciler = await h.tasks.ensure_framework_reconciliation(h.a.id)
    claimed = await h.tasks.claim(reconciler.id, worker_id="unsealed-reconciler", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="unsealed-reconciler", fencing_token=9)
    return claimed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["terminal_event", "declaration", "asset", "publication"])
async def test_unsealed_approval_seal_and_publication_roll_back_together(approval_asset_gap, monkeypatch, failure):
    from src.runtime import framework_runtime_results as results

    h = approval_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    claimed = await claim_unsealed_approval_reconciler(h)
    stage, events = h.stages.get(h.initial_task_id, h.session_id), await h.store.list_events(h.session_id)
    tasks, snapshot = await h.tasks.list_session(h.session_id), await h.store.get_latest_snapshot(h.session_id)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT event_key,payload FROM {h.settings.database.postgres_task_outbox_table} ORDER BY event_key")
        outbox = cur.fetchall()
    append, publish, freeze = h.store.append_event_in_transaction, h.tasks._event, results._freeze_runtime_result

    def fail_append(cur, session_id, event, *args, **kwargs):
        inserted = append(cur, session_id, event, *args, **kwargs)
        if event.type == ("runtime.turn_completed" if failure == "terminal_event" else "runtime.event_log_completed"):
            raise ConnectionError("injected approval seal transaction failure")
        return inserted

    def fail_publish(cur, row, name, payload):
        publish(cur, row, name, payload)
        if name == "framework.approval_recover_queued":
            raise ConnectionError("injected approval seal transaction failure")

    def fail_asset(*args, **kwargs):
        freeze(*args, **kwargs)
        raise ConnectionError("injected approval seal transaction failure")

    with monkeypatch.context() as patch:
        if failure in {"terminal_event", "declaration"}:
            patch.setattr(h.store, "append_event_in_transaction", fail_append)
        elif failure == "asset":
            patch.setattr(results, "_freeze_runtime_result", fail_asset)
        else:
            patch.setattr(h.tasks, "_event", fail_publish)
        with pytest.raises(ConnectionError, match="approval seal transaction failure"):
            await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert await h.store.list_events(h.session_id) == events
    assert await h.tasks.list_session(h.session_id) == tasks
    assert await h.store.get_latest_snapshot(h.session_id) == snapshot
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT event_key,payload FROM {h.settings.database.postgres_task_outbox_table} ORDER BY event_key")
        assert cur.fetchall() == outbox
    successor = await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert successor.input["recovery_proof"]["scope"] == "application_finalization"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["native", "prefix", "event", "owner", "event_log", "output", "verification", "rehashed_native"])
async def test_unsealed_approval_admission_rechecks_exact_originals(approval_asset_gap, monkeypatch, changed):
    from psycopg import sql
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
    from src.runtime.framework_runtime_results import FrameworkRuntimeResults
    from src.runtime.store import ContinuationLeaseLostError

    h = approval_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    claimed = await claim_unsealed_approval_reconciler(h)
    before = h.stages.get(h.initial_task_id, h.session_id)
    read = FrameworkRuntimeResults.read_completed_approval

    async def read_then_change(*args, **kwargs):
        original = await read(*args, **kwargs)
        if changed == "event_log":
            original.runtime_result.state["event_log"][-1]["payload"]["output_length"] = 999
        elif changed == "output":
            original = replace(original, runtime_result=original.runtime_result.model_copy(update={"output_text": "foreign output"}))
        elif changed == "verification":
            from datetime import datetime
            from src.schemas.session import VerificationResult

            forged = VerificationResult(id="foreign-verification", session_id=h.session_id, turn_id=h.binding.turn_id,
                trace_id=h.a.trace_id, verifier="foreign", status="passed", summary="foreign result", created_at=datetime(2026, 10, 28))
            original = replace(original, verification_results=[*original.verification_results, forged])
        elif changed == "rehashed_native":
            from src.application.deep_agents.native_terminal import _native_hash
            from src.application.deep_agents.fenced_postgres_saver import BoundPostgresReader

            native = deepcopy(original.native_read)
            native.values["messages"][-1].content = "foreign native output"
            serializer = BoundPostgresReader(None, source=original.originals[-1]).serde
            native = replace(native, proof=native.proof.model_copy(update={"state_sha256": _native_hash(serializer, native.values)}))
            original = replace(original, native_read=native)
        elif changed == "owner":
            assert await h.store.renew_turn_execution(h.session_id, claimed.lease_token, 0)
        else:
            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                if changed in {"native", "prefix"}:
                    expression = ("checkpoint=jsonb_set(checkpoint,'{ts}','\"2020-01-01T00:00:00+00:00\"')"
                        if changed == "native" else "metadata=metadata - 'framework_checkpoint_event_prefix'")
                    query = (sql.SQL("UPDATE {}.checkpoints SET ").format(sql.Identifier(h.settings.deep_agents.checkpoint_schema))
                        + sql.SQL(expression) + sql.SQL(" WHERE thread_id=%s AND checkpoint_id=%s"))
                    cur.execute(query.as_string(), (h.binding.thread_id, original.native_read.proof.checkpoint_id))
                else:
                    cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || %s::jsonb WHERE id=%s",
                        ('{"approval_sealing_drift":true}', h.oracle.events[0].id))
        return original

    monkeypatch.setattr(FrameworkRuntimeResults, "read_completed_approval", read_then_change)
    with pytest.raises((FrameworkMessageStageRecoveryRequired, FrameworkApprovalRecoveryRequired, ContinuationLeaseLostError)):
        await h.runtime.reconcile_framework_task(claimed, tasks=h.tasks)
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    events = await h.store.list_events(h.session_id)
    assert not [item for item in events if item.type in {"runtime.event_log_completed", "runtime.turn_completed"}]
    assert not [item for item in await h.tasks.list_session(h.session_id) if item.parent_task_id == claimed.id]
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
async def test_unsealed_approval_parallel_reconciliations_publish_one_seal(approval_asset_gap, monkeypatch):
    import asyncio
    from src.runtime.framework_runtime_results import FrameworkRuntimeResults
    from src.runtime.store import ContinuationLeaseLostError

    h = approval_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    claimed = await claim_unsealed_approval_reconciler(h)
    read, count, ready = FrameworkRuntimeResults.read_completed_approval, 0, asyncio.Event()

    async def synchronized(*args, **kwargs):
        nonlocal count
        original = await read(*args, **kwargs)
        count += 1
        if count == 3:
            ready.set()
        await asyncio.wait_for(ready.wait(), 30)
        return original

    monkeypatch.setattr(FrameworkRuntimeResults, "read_completed_approval", synchronized)
    outcomes = await asyncio.gather(*(h.runtime.reconcile_framework_task(claimed, tasks=h.tasks) for _ in range(3)),
        return_exceptions=True)
    admitted = [item for item in outcomes if not isinstance(item, Exception)]
    assert len(admitted) == 1
    assert all(isinstance(item, ContinuationLeaseLostError) for item in outcomes if isinstance(item, Exception))
    assert len(h.stages.get(h.initial_task_id, h.session_id).approval_finalizations) == 1
    assert len([item for item in await h.tasks.list_session(h.session_id) if item.parent_task_id == claimed.id]) == 1
    events = await h.store.list_events(h.session_id)
    assert len([item for item in events if item.type == "runtime.event_log_completed"]) == 1
    assert len([item for item in events if item.type == "runtime.turn_completed"]) == 1
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
@pytest.mark.parametrize("reconcile_point,takeover_point", [
    ("none", None), ("reconcile_before_commit", None), ("reconcile_after_commit", None),
    ("none", "after_snapshot"), ("none", "after_application_commit"),
])
async def test_unsealed_original_approval_worker_kill_recovers_without_graph_execution(
    prepared_approval_runtime, reconcile_point, takeover_point,
):
    from tests.test_framework_approval_graph_input import test_original_approval_worker_hard_kill_recovers_only_original_projections

    # Kill a real original Worker after graph return/applied commit, before
    # terminal events/seal; retain the full Redis/PG/finalizer/ACK contract.
    await test_original_approval_worker_hard_kill_recovers_only_original_projections(
        prepared_approval_runtime, "after_applied", takeover_point, reconcile_point=reconcile_point)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["terminal_event", "declaration", "asset", "publication"])
async def test_unsealed_admission_rolls_back_suffix_seal_asset_and_outbox_together(message_asset_gap, monkeypatch, failure):
    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    stage, events = h.stages.get(h.initial_task_id, h.session_id), await h.store.list_events(h.session_id)
    tasks = await h.tasks.list_session(h.session_id)
    append, publish = h.store.append_event_in_transaction, h.tasks._event
    reader = h.runtime._deep_agent_bound_runtime_adapter.open_native_reader

    def fail_append(cur, session_id, event, *args, **kwargs):
        inserted = append(cur, session_id, event, *args, **kwargs)
        if event.type == ("runtime.turn_completed" if failure == "terminal_event" else "runtime.event_log_completed"):
            raise ConnectionError("injected unsealed transaction failure")
        return inserted

    def fail_publish(cur, row, name, payload):
        if name == "framework.message_recover_queued":
            raise ConnectionError("injected unsealed transaction failure")
        return publish(cur, row, name, payload)

    from src.runtime import framework_runtime_results as results
    original_freeze = results._freeze_runtime_result

    def fail_asset(*args, **kwargs):
        original_freeze(*args, **kwargs)
        raise ConnectionError("injected unsealed transaction failure")

    with monkeypatch.context() as patch:
        if failure in {"terminal_event", "declaration"}:
            patch.setattr(h.store, "append_event_in_transaction", fail_append)
        elif failure == "asset":
            patch.setattr(results, "_freeze_runtime_result", fail_asset)
        else:
            patch.setattr(h.tasks, "_event", fail_publish)
        with pytest.raises(ConnectionError, match="unsealed transaction failure"):
            await h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=reader)
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert await h.store.list_events(h.session_id) == events
    assert await h.tasks.list_session(h.session_id) == tasks
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id, native_reader=reader) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["native", "prefix", "event", "owner", "result", "output", "verification"])
async def test_unsealed_admission_rechecks_originals_after_source_only_read(message_asset_gap, monkeypatch, changed):
    from psycopg import sql
    from src.runtime.framework_runtime_results import FrameworkRuntimeResults
    from src.runtime.postgres_runtime_owner import runtime_owner_table

    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    before = h.stages.get(h.initial_task_id, h.session_id)
    read = FrameworkRuntimeResults.read_completed_message

    async def read_then_change(*args, **kwargs):
        original = await read(*args, **kwargs)
        if changed == "result":
            original.runtime_result.state["event_log"][-1]["payload"]["output_length"] = 999
            return original
        if changed == "output":
            return replace(original, runtime_result=original.runtime_result.model_copy(update={"output_text": "foreign output"}))
        if changed == "verification":
            from datetime import datetime
            from src.schemas.session import VerificationResult

            assert original.verification_results == []
            forged = VerificationResult(id="foreign-verification", session_id=h.session_id, turn_id=h.binding.turn_id,
                trace_id=h.owner.trace_id, verifier="foreign", status="passed", summary="foreign result", created_at=datetime(2026, 10, 28))
            return replace(original, verification_results=[forged])
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            if changed in {"native", "prefix"}:
                expression = ("checkpoint=jsonb_set(checkpoint,'{ts}','\"2020-01-01T00:00:00+00:00\"')"
                    if changed == "native" else "metadata=metadata - 'framework_checkpoint_event_prefix'")
                query = (sql.SQL("UPDATE {}.checkpoints SET ").format(sql.Identifier(h.settings.deep_agents.checkpoint_schema))
                    + sql.SQL(expression) + sql.SQL(" WHERE thread_id=%s AND checkpoint_id=%s"))
                cur.execute(query.as_string(), (h.binding.thread_id, original.native_read.proof.checkpoint_id))
            elif changed == "event":
                cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || '{{\"admission_drift\":true}}'::jsonb WHERE id=%s",
                    (h.oracle.events[0].id,))
            else:
                cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET owner_epoch=owner_epoch+1 WHERE binding_id=%s", (h.binding.binding_id,))
        return original

    monkeypatch.setattr(FrameworkRuntimeResults, "read_completed_message", read_then_change)
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader) is None
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    events = await h.store.list_events(h.session_id)
    assert not [event for event in events if event.type in {"runtime.event_log_completed", "runtime.turn_completed"}]
    assert not [task for task in await h.tasks.list_session(h.session_id) if task.parent_task_id == h.owner.id]


@pytest.mark.asyncio
async def test_unsealed_parallel_admissions_publish_one_seal_and_successor(message_asset_gap, monkeypatch):
    import asyncio
    from src.runtime.framework_runtime_results import FrameworkRuntimeResults

    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    read, count, ready = FrameworkRuntimeResults.read_completed_message, 0, asyncio.Event()

    async def synchronized(*args, **kwargs):
        nonlocal count
        original = await read(*args, **kwargs)
        count += 1
        if count == 3:
            ready.set()
        await asyncio.wait_for(ready.wait(), 30)
        return original

    monkeypatch.setattr(FrameworkRuntimeResults, "read_completed_message", synchronized)
    admitted = await asyncio.gather(*(h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader) for _ in range(3)))
    assert all(item is not None for item in admitted) and len({item.id for item in admitted}) == 1
    assert len(h.stages.get(h.initial_task_id, h.session_id).recoveries) == 1
    events = await h.store.list_events(h.session_id)
    assert len([event for event in events if event.type == "runtime.event_log_completed"]) == 1
    assert len([event for event in events if event.type == "runtime.turn_completed"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", ["planning"], indirect=True)
async def test_unsealed_admission_reuses_existing_plan_prefix_without_overwriting_its_row(message_asset_gap):
    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=True)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE session_id=%s AND type='runtime.turn_completed'",
            (h.session_id,))
    before = await h.store.list_events(h.session_id)
    plan, = [event for event in before if event.type == "graph.plan_built"]
    successor = await h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader)
    assert successor is not None
    after = await h.store.list_events(h.session_id)
    assert [event for event in after if event.type == "graph.plan_built"] == [plan]
    assert [event for event in after if event.id in {item.id for item in before}] == before
    assert [event.type for event in after if event.type in {"graph.plan_built", "runtime.turn_completed", "runtime.event_log_completed"}] == [
        "graph.plan_built", "runtime.turn_completed", "runtime.event_log_completed"]


@pytest.mark.asyncio
async def test_native_admission_read_restores_search_path_and_leaves_outer_transaction_usable(message_asset_gap, monkeypatch):
    from src.application.deep_agents.native_terminal import require_native_terminal_original
    from src.application.deep_agents.fenced_postgres_saver import BoundPostgresReader

    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    original = await read_message(h)

    def fail_read(*args, **kwargs):
        raise ConnectionError("injected native original read failure")

    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute("SELECT current_setting('search_path') AS path")
        path = cur.fetchone()["path"]
        with monkeypatch.context() as patch:
            patch.setattr(BoundPostgresReader, "get_tuple", fail_read)
            with pytest.raises(ConnectionError, match="native original read failure"):
                require_native_terminal_original(cur, h.settings, original.source, original.native_read, original.graph_input_asset)
        cur.execute("SELECT current_setting('search_path') AS path")
        assert cur.fetchone()["path"] == path
        require_native_terminal_original(cur, h.settings, original.source, original.native_read, original.graph_input_asset)
        cur.execute("SELECT current_setting('search_path') AS path")
        assert cur.fetchone()["path"] == path


@pytest.mark.asyncio
async def test_rehashed_read_object_cannot_substitute_materialized_native_messages(message_asset_gap):
    from src.application.deep_agents.native_terminal import _native_hash, require_native_terminal_original
    from src.application.deep_agents.fenced_postgres_saver import BoundPostgresReader

    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=False)
    original = await read_message(h)
    native = deepcopy(original.native_read)
    native.values["messages"][-1].content = "foreign"
    serializer = BoundPostgresReader(None, source=original.source).serde
    native = replace(native, proof=native.proof.model_copy(update={"state_sha256": _native_hash(serializer, native.values)}))
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        with pytest.raises(FrameworkMessageStageRecoveryRequired, match="native checkpoint|native state"):
            require_native_terminal_original(cur, h.settings, original.source, native, original.graph_input_asset)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing", "future", "legacy_budget", "prefix", "terminal", "extra", "budget", "producer"])
async def test_unsealed_message_rejects_untrusted_or_changed_originals_without_repair(message_asset_gap, monkeypatch, changed):
    from src.runtime import framework_approval_result_reader as reader

    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=True)
    collect = reader.collect_native_terminal

    async def read_then_change(*args, **kwargs):
        native = await collect(*args, **kwargs)
        if changed in {"missing", "future", "legacy_budget"}:
            prefix = deepcopy(native.checkpoint_event_prefix)
            if changed == "missing":
                prefix = None
            else:
                prefix["schema_version"] = 99 if changed == "future" else 1
                if changed == "legacy_budget":
                    # v1 may be read, but cannot smuggle budget fields even
                    # when its caller recomputes the outer self-digest.
                    prefix["root_model_budget_material"] = {"legacy": True}
                prefix["prefix_sha256"] = approval_value_hash({key: value for key, value in prefix.items()
                    if key != "prefix_sha256"})
            return replace(native, checkpoint_event_prefix=prefix)
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            table = h.settings.database.postgres_event_table
            if changed in {"prefix", "terminal"}:
                event_id = h.oracle.events[0 if changed == "prefix" else -1].id
                cur.execute(f"UPDATE {table} SET payload=payload || %s::jsonb WHERE id=%s",
                    ('{"output_length":999}', event_id))
            elif changed == "extra":
                cur.execute(f"INSERT INTO {table}(id,session_id,type,timestamp,payload) "
                    f"SELECT %s,session_id,type,timestamp,payload FROM {table} WHERE id=%s",
                    (str(uuid4()), h.oracle.events[-1].id))
            elif changed == "budget":
                cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy, '{{max_calls}}', '99') WHERE binding_id=%s",
                    (h.binding.binding_id,))
            elif changed == "producer":
                cur.execute(f"UPDATE {h.settings.database.postgres_agent_attempt_table} SET worker_id='foreign-reader' WHERE id=%s",
                    (h.owner.attempt_id,))
        return native

    monkeypatch.setattr(reader, "collect_native_terminal", read_then_change)
    with pytest.raises((ValueError, FrameworkMessageStageRecoveryRequired)):
        await read_message(h)
    events = await h.store.list_events(h.session_id)
    assert not any(item.id == completion_event_id(h.binding) for item in events)
    assert "runtime_result_asset" not in h.stages.get(h.initial_task_id, h.session_id).source_manifest
    assert await h.tasks.get(h.owner.id) == h.expired


@pytest.mark.asyncio
@pytest.mark.parametrize("message_asset_gap", ["planning"], indirect=True)
async def test_unsealed_result_refuses_completed_row_with_missing_original_plan(message_asset_gap):
    h = message_asset_gap
    remove_fixture_seal(h, terminal_events=True)
    plan, = [item for item in h.oracle.events if item.type == "graph.plan_built"]
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE id=%s", (plan.id,))
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="conflicting or unbound"):
        await read_message(h)
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert await h.tasks.ensure_framework_message_recovery(h.owner.id,
        native_reader=h.runtime._deep_agent_bound_runtime_adapter.open_native_reader) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_asset_gap", ["approved", "denied"], indirect=True)
@pytest.mark.parametrize("terminal_events", [False, True])
async def test_unsealed_approval_uses_same_original_projection_without_sealing(approval_asset_gap, terminal_events):
    h = approval_asset_gap
    remove_fixture_seal(h, terminal_events=terminal_events)
    events = await h.store.list_events(h.session_id)
    stage = h.stages.get(h.initial_task_id, h.session_id)
    budget = await h.calls.snapshot(h.binding)
    effects, requests = deepcopy(h.effects), list(h.adapter_requests)
    result = await h.runtime.read_completed_framework_approval(h.binding, h.a.id, tasks=h.tasks)
    assert result.completion is None and result.checkpoint_event_prefix is not None
    assert result.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert result.verification_results == h.verification_oracle
    assert await h.runtime.read_completed_framework_approval(h.binding, h.a.id, tasks=h.tasks) == result
    assert await h.store.list_events(h.session_id) == events
    assert await h.calls.snapshot(h.binding) == budget
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert h.effects == effects and h.adapter_requests == requests
