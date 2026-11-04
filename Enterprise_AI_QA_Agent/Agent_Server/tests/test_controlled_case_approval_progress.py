"""Retain successor application material without parking or replay authority."""
from dataclasses import replace
from copy import deepcopy
import asyncio
import importlib
import json
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope, graph_event_scope
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_runtime import owned, ports
from tests.test_controlled_case_approval_state import state_caps
from tests.test_controlled_case_approval_wait import facts, implementation as waits
from tests.test_controlled_case_approval_claim import implementation as continuations, wire
from tests.test_controlled_case_approval_busy_submission import decide_a


def implementation(p):
    return importlib.import_module("src.runtime.controlled_case_approval_progress").ControlledCaseApprovalProgress(continuations(p))


def progress_caps():
    return replace(state_caps(), controlled_case_approval_progress_available=True)


async def applied(p):
    root, b = await owned(p)
    runtime, worker = ports(p)
    outcome = await runtime.execute(b, executor=worker, resource_id="progress-controlled-runtime", fencing_token=2)
    return root, b, outcome


async def retain(p, b, outcome, *, caps=None, log=None):
    with framework_owner_scope(b.runtime_owner):
        return await implementation(p).retain(b, event_log=outcome.state["event_log"] if log is None else log,
            capabilities=caps or progress_caps())


def test_progress_readers_are_not_writers_or_completed_result_readers():
    from types import SimpleNamespace
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.schemas.controlled_case_admission import read_result_checkpoint
    from src.schemas.controlled_case_approval_progress import ControlledCaseApprovalProgressAsset

    caps = FrameworkWorkerCapabilities.from_artifact(deepagents_available=True)
    assert caps.controlled_case_checkpoint_max_version == 8 and not caps.controlled_case_approval_progress_available
    with pytest.raises(ValueError, match="no supported controlled result"):
        read_result_checkpoint(SimpleNamespace(checkpoint_version=8))
    with pytest.raises(ValueError, match="explicit integer version"):
        ControlledCaseApprovalProgressAsset.model_validate({"schema_version": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_retained_progress_preserves_root_and_exact_runtime_slots_without_parking(pending_formal, decision):
    p = pending_formal
    root, b = await owned(p, decision)
    runtime, worker = ports(p)
    outcome = await runtime.execute(b, executor=worker, resource_id="progress-original-effect", fencing_token=2)
    before = await p.s.h.tasks.get(b.id), native_originals(p), await p.s.calls.snapshot(p.s.binding)
    service = implementation(p)
    caps = replace(state_caps(), controlled_case_approval_progress_available=True)
    with framework_owner_scope(b.runtime_owner):
        asset = await service.retain(b, event_log=outcome.state["event_log"], capabilities=caps)
    assert asset.scope == "controlled_case_approval_progress_material_only"
    assert asset.parent_state_sha256 == root.asset_sha256 and asset.origin_task_id == p.h.task.id
    assert (asset.task_id, asset.attempt_id, asset.owner_epoch) == (b.id, b.attempt_id, b.runtime_owner.owner_epoch)
    assert asset.state == outcome.state
    assert asset.tool_materials == (*root.tool_materials, outcome.approval_tool_projection)
    assert tuple(asset.natives) == tuple(outcome.graph_outcome.native_interrupts)
    assert before == (await p.s.h.tasks.get(b.id), native_originals(p), await p.s.calls.snapshot(p.s.binding))
    detail = await p.s.d.runs.get_run(p.s.item.run_id)
    attempt = next(a for a in detail.attempts if a.id == p.s.attempt.id)
    assert attempt.checkpoint_version == 8 and attempt.status == "running" and detail.results == []
    assert attempt.checkpoint_payload["initial_wait"]["state_asset"] == root.model_dump(mode="json")
    assert attempt.checkpoint_payload["progress_assets"] == [asset.model_dump(mode="json")]
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=caps) == root
    saved = facts(p)
    assert await service.read(wire(p, b.id), capabilities=caps) == asset and facts(p) == saved
    with framework_owner_scope(b.runtime_owner):
        assert await service.retain(b, event_log=outcome.state["event_log"], capabilities=caps) == asset
    assert facts(p) == saved and len(p.requests) == 1 and len(p.effects) == (decision == "approved")
    with pytest.raises(TaskDeferred, match="checkpoint_format_unavailable"):
        await waits(p).read_state(wire(p, p.h.task.id), capabilities=replace(caps, controlled_case_checkpoint_max_version=7))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disabled", "codec", "owner", "sink", "serialized", "claimed", "unapplied"])
async def test_retention_requires_explicit_writer_captured_owner_and_applied_material(pending_formal, change):
    p = pending_formal
    root, b = await owned(p)
    if change != "claimed":
        runtime, worker = ports(p)
        outcome = await runtime.execute(b, executor=worker, resource_id="progress-prerequisites", fencing_token=2)
        log = outcome.state['event_log']
    else:
        log = root.state['event_log']
    caps = progress_caps()
    if change == 'disabled':
        caps = state_caps()
    elif change == 'codec':
        caps = replace(caps, controlled_case_checkpoint_max_version=7)
    elif change == 'serialized':
        b = type(b).model_validate(b.model_dump(mode='json'))
    elif change == 'unapplied':
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET applied_at=NULL WHERE approval_id=%s", (b.reference_id,))
    before = facts(p)
    with framework_owner_scope(None if change == 'owner' else b.runtime_owner):
        with graph_event_scope(object() if change == 'sink' else None):
            with pytest.raises((ValueError, TaskDeferred, ContinuationLeaseLostError)):
                await implementation(p).retain(b, event_log=log, capabilities=caps)
    assert facts(p) == before and len(p.requests) == 1 and len(p.effects) == (change != 'claimed')


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ['before_execution', 'after_retention'])
@pytest.mark.parametrize("decision", ['approved', 'denied'])
async def test_queued_intents_do_not_change_the_retained_source_or_allow_parallel_claim(pending_formal, when, decision):
    p = pending_formal
    root, b = await owned(p)
    if when == 'before_execution':
        a = await decide_a(p, root, decision)
    runtime, worker = ports(p)
    outcome = await runtime.execute(b, executor=worker, resource_id='progress-with-queued-intent', fencing_token=2)
    asset = await retain(p, b, outcome)
    if when == 'after_retention':
        a = await decide_a(p, root, decision)
    before = facts(p)
    assert await implementation(p).read(wire(p, b.id), capabilities=state_caps()) == asset
    with pytest.raises(TaskDeferred, match='already owned'):
        await continuations(p).claim(wire(p, a.id), worker_id='no-progress-execution-right', capabilities=state_caps())
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('lease', ['session', 'owner', 'formal'])
async def test_last_progress_audit_crossing_a_lease_rolls_back_only_new_material(pending_formal, monkeypatch, lease):
    from tests.test_controlled_case_approval_runtime import expire_lease

    p = pending_formal
    _, b, outcome = await applied(p)
    before = facts(p)
    append = p.s.h.tasks._sessions.append_event_in_transaction
    def cross(cur, session_id, event, **kwargs):
        result = append(cur, session_id, event, **kwargs)
        if event.type == 'test_run.case_approval_progress_retained':
            expire_lease(p, cur, lease, delay='0.05 seconds')
            cur.execute('SELECT pg_sleep(0.1)')
        return result
    monkeypatch.setattr(p.s.h.tasks._sessions, 'append_event_in_transaction', cross)
    with pytest.raises(ContinuationLeaseLostError):
        await retain(p, b, outcome)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['rollback', 'reply_loss', 'cancel_waiter'])
async def test_transaction_outcome_never_implies_reexecution_or_reallocation(pending_formal, monkeypatch, failure):
    p = pending_formal
    _, b, outcome = await applied(p)
    service = implementation(p)
    before = facts(p)
    entered, release, finished = Event(), Event(), Event()
    original, append = type(service)._retain, p.s.h.tasks._sessions.append_event_in_transaction
    def event(*args, **kwargs):
        result = append(*args, **kwargs)
        if args[2].type == 'test_run.case_approval_progress_retained':
            if failure == 'rollback':
                raise RuntimeError('isolated progress rollback')
            if failure == 'cancel_waiter':
                entered.set()
                assert release.wait(30)
        return result
    def complete(slf, *args):
        try:
            result = original(slf, *args)
            if failure == 'reply_loss':
                raise RuntimeError('isolated progress reply loss')
            return result
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(type(service), '_retain', complete)
        patch.setattr(p.s.h.tasks._sessions, 'append_event_in_transaction', event)
        waiter = asyncio.create_task(retain(p, b, outcome))
        if failure == 'cancel_waiter':
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match='isolated progress'):
                await waiter
    if failure == 'rollback':
        assert facts(p) == before
    asset = await retain(p, b, outcome)
    saved = facts(p)
    assert await retain(p, b, outcome) == asset and facts(p) == saved
    assert len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('lease', ['session', 'owner', 'formal', 'cancelled_run'])
async def test_historical_material_reads_survive_expiry_without_adopting_ownership(pending_formal, lease):
    from tests.test_controlled_case_approval_runtime import expire_lease

    p = pending_formal
    root, b, outcome = await applied(p)
    asset = await retain(p, b, outcome)
    if lease == 'cancelled_run':
        await p.s.d.runs.cancel_run(p.s.item.run_id, 'isolated historical progress read', asset.retained_at)
    else:
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            expire_lease(p, cur, lease)
    before = facts(p)
    assert await implementation(p).read(wire(p, b.id), capabilities=state_caps()) == asset
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == root
    with pytest.raises(ContinuationLeaseLostError):
        await retain(p, b, outcome)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['slot', 'framework_messages', 'source_messages', 'events', 'event_refs',
    'epoch', 'parent', 'material', 'decision', 'native', 'budget', 'hygiene', 'duplicate', 'drop'])
async def test_rehashed_progress_is_not_an_authority_for_changed_originals(pending_formal, change):
    p = pending_formal
    _, b, outcome = await applied(p)
    await retain(p, b, outcome)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT record FROM {p.s.d.runs._attempt_table} WHERE id=%s', (p.s.attempt.id,))
        record = cur.fetchone()['record']
        asset = record['checkpoint_payload']['progress_assets'][0]
        if change == 'slot':
            asset['state']['tool_messages'][1]['content'] = 'foreign display'
        elif change == 'framework_messages':
            asset['state']['framework_messages'][0]['data']['content'] = 'foreign message'
        elif change == 'source_messages':
            asset['native_messages'][0]['data']['content'] = 'foreign source'
        elif change == 'events':
            asset['state']['event_log'][-1]['payload']['message'] = 'foreign event'
        elif change == 'event_refs':
            asset['event_references'][0]['event_sequence'] += 1
        elif change == 'epoch':
            asset['owner_epoch'] += 1
        elif change == 'parent':
            asset['parent_state_sha256'] = '0' * 64
        elif change == 'material':
            m = asset['tool_materials'][-1]
            m['runtime_message']['content'] = 'foreign material'
            m['sha256'] = approval_value_hash({k: v for k, v in m.items() if k != 'sha256'})
        elif change == 'decision':
            asset['decision_receipt']['last_error'] = 'foreign receipt'
        elif change == 'native':
            asset['natives'][0]['tool_name'] = 'foreign_tool'
        elif change == 'budget':
            asset['root_model_budget']['sha256'] = '0' * 64
        elif change == 'hygiene':
            asset['hygiene_metadata']['foreign_policy'] = True
        elif change == 'duplicate':
            record['checkpoint_payload']['progress_assets'].append(deepcopy(asset))
        else:
            record['checkpoint_payload']['progress_assets'] = []
        asset['asset_sha256'] = approval_value_hash({k: v for k, v in asset.items() if k != 'asset_sha256'})
        # Rehash the outer audit too. A matching envelope digest must not hide
        # foreign slots/messages or replace original native/decision evidence.
        cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=jsonb_set(payload,"
            "'{state_asset_sha256}',to_jsonb(%s::text)) WHERE type='test_run.case_approval_progress_retained' "
            "AND payload->>'task_id'=%s", (asset['asset_sha256'], b.id))
        cur.execute(f'UPDATE {p.s.d.runs._attempt_table} SET record=%s::jsonb WHERE id=%s',
            (json.dumps(record), p.s.attempt.id))
    before = facts(p)
    with pytest.raises((ValueError, FrameworkMessageStageRecoveryRequired)):
        await implementation(p).read(wire(p, b.id), capabilities=state_caps())
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['omit', 'reorder'])
async def test_retention_cannot_drop_or_reorder_acknowledged_application_events(pending_formal, change):
    p = pending_formal
    _, b, outcome = await applied(p)
    log = deepcopy(outcome.state['event_log'])
    if change == 'omit':
        log.pop()
    else:
        log[-2], log[-1] = log[-1], log[-2]
    before = facts(p)
    with pytest.raises((ValueError, FrameworkMessageStageRecoveryRequired)):
        await retain(p, b, outcome, log=log)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
async def test_historical_progress_does_not_require_the_sibling_to_remain_pending_or_error_free(pending_formal):
    from langgraph.checkpoint.base import WRITES_IDX_MAP
    from src.application.deep_agents.checkpoint_provider import checkpoint_serializer
    from src.schemas.session import ExecutionEvent

    p = pending_formal
    root, b, outcome = await applied(p)
    asset = await retain(p, b, outcome)
    sibling, = asset.natives
    kind, blob = checkpoint_serializer().dumps_typed('isolated later sibling task error')
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
            "(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel,type,blob,task_path) "
            "VALUES (%s,%s,%s,%s,%s,'__error__',%s,%s,%s)",
            (p.s.binding.thread_id, p.s.binding.checkpoint_namespace, sibling.checkpoint_id, sibling.graph_task_id,
             WRITES_IDX_MAP['__error__'], kind, blob, sibling.task_path))
        cur.execute('SELECT clock_timestamp() AS now')
        p.s.h.tasks._sessions.append_event_in_transaction(cur, p.s.session.id,
            ExecutionEvent(session_id=p.s.session.id, timestamp=cur.fetchone()['now'], type='runtime.fixture_later_step', payload={
                'turn_id': b.turn_id, 'trace_id': b.trace_id, 'step': len(outcome.state['event_log']) + 1}))
    before = facts(p)
    assert await implementation(p).read(wire(p, b.id), capabilities=state_caps()) == asset
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == root
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
async def test_competing_retention_has_one_original_asset_and_one_audit(pending_formal):
    p = pending_formal
    _, b, outcome = await applied(p)
    first, second = await asyncio.gather(retain(p, b, outcome), retain(p, b, outcome))
    assert first == second
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS count FROM {p.h.settings.database.postgres_event_table} "
            "WHERE type='test_run.case_approval_progress_retained' AND payload->>'task_id'=%s", (b.id,))
        assert cur.fetchone()['count'] == 1
    assert len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
async def test_read_does_not_construct_material_before_retention(pending_formal):
    p = pending_formal
    _, b, _ = await applied(p)
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match='no unique original retained material'):
        await implementation(p).read(wire(p, b.id), capabilities=state_caps())
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
async def test_progress_checkpoint_cannot_downgrade_its_root_wait_receipt(pending_formal):
    from src.runtime.controlled_case_approval_wait import approval_wait_event
    from src.schemas.controlled_case_approval_wait import parse_controlled_case_approval_wait

    p = pending_formal
    _, b, outcome = await applied(p)
    await retain(p, b, outcome)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT result_payload FROM {p.s.h.tasks._tasks} WHERE id=%s', (p.h.task.id,))
        payload = cur.fetchone()['result_payload']
        receipt = payload['controlled_case_approval_wait']
        receipt['schema_version'] = 1
        receipt.pop('state_asset_sha256')
        receipt['receipt_sha256'] = approval_value_hash({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
        cur.execute(f'UPDATE {p.s.h.tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s',
            (json.dumps(payload), p.h.task.id))
        event = approval_wait_event(parse_controlled_case_approval_wait(receipt), p.s.binding)
        cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=%s::jsonb WHERE id=%s",
            (json.dumps(event.payload), event.id))
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match='no matching original receipt'):
        await waits(p).acknowledge_delivery(wire(p, p.h.task.id), capabilities=state_caps())
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1
