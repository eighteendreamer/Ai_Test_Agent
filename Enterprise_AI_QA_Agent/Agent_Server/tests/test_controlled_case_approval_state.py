"""Atomic original application waiting slots, not a graph continuation permit."""
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
import asyncio
import json
from threading import Event

import pytest
import pytest_asyncio

from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ApprovalDecisionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_wait import facts, proposals, implementation as waits
from tests.test_controlled_case_approval_submission import implementation as submissions
from tests.test_controlled_case_approval_claim import wire, implementation as continuations
from tests.test_controlled_case_approval_delivery import delivery_caps
from tests.test_controlled_case_executor import execution, executor
from tests.test_controlled_case_graph import business_bridge
from tests.test_controlled_case_runtime import assembly, prepare
from tests.test_framework_chat_models import _config, _provider, _tool_reply


def state_caps():
    return replace(delivery_caps(), controlled_case_approval_state_available=True)


async def park_state(p, **kwargs):
    with framework_owner_scope(p.h.task.runtime_owner):
        return await waits(p).park(p.h.task.runtime_owner, proposals=proposals(p),
            event_log=p.outcome.state["event_log"], capabilities=state_caps(), **kwargs)


@pytest.mark.asyncio
async def test_atomic_wait_retains_private_original_state_slots_not_public_result(pending_formal):
    p = pending_formal
    native, budget = native_originals(p), await p.s.calls.snapshot(p.s.binding)
    before = await p.s.d.runs.get_run(p.s.item.run_id)
    original_attempt = next(a for a in before.attempts if a.id == p.s.attempt.id)
    receipt = await park_state(p)
    assert receipt.schema_version == 2
    detail = await p.s.d.runs.get_run(p.s.item.run_id)
    attempt = next(a for a in detail.attempts if a.id == p.s.attempt.id)
    assert attempt.checkpoint_version == 7 and attempt.checkpoint_payload["admission"] == original_attempt.checkpoint_payload
    assert attempt.checkpoint_at == original_attempt.checkpoint_at
    asset = await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps())
    assert asset.asset_sha256 == receipt.state_asset_sha256 and asset.scope == "controlled_case_wait_state_material_only"
    state = asset.state
    assert state["control_state"] == state["termination_reason"] == "waiting_approval"
    assert [item["input"]["path"] for item in state["tool_results"]] == ["a.py", "b.py"]
    assert [item["status"] for item in state["tool_results"]] == ["waiting_approval"] * 2
    assert len(state["tool_results"]) == len(state["tool_messages"]) == 2
    assert [slot["tool_call_id"] for slot in state["tool_messages"]] == [r["call_id"] for r in state["tool_results"]]
    assert set(state["pending_turn"]["pending_approval_ids"]) == {entry.approval_id for entry in receipt.batch}
    assert asset.model_ledger_sha256 == receipt.model_ledger_sha256
    public = (await p.s.h.tasks.get(p.h.task.id)).result_payload
    assert public == {"controlled_case_approval_wait": receipt.model_dump(mode="json")}
    assert "state" not in public["controlled_case_approval_wait"]
    before = facts(p)
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset
    assert facts(p) == before and native_originals(p) == native and await p.s.calls.snapshot(p.s.binding) == budget
    assert p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
async def test_competing_state_parks_have_one_retained_original_and_no_native_replay(pending_formal):
    p = pending_formal
    native = native_originals(p)
    results = await asyncio.gather(park_state(p), park_state(p), return_exceptions=True)
    assert len([r for r in results if not isinstance(r, BaseException)]) == 1
    assert len([r for r in results if isinstance(r, ContinuationLeaseLostError)]) == 1
    asset = await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps())
    assert len(asset.state['tool_messages']) == 2
    events = await p.h.store.list_events(p.s.session.id)
    assert len([e for e in events if e.type == 'test_run.case_approval_waiting']) == 1
    assert len([e for e in events if e.type == 'agent_task.completed']) == 1
    assert native_originals(p) == native and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('lease', ['session', 'owner', 'formal'])
async def test_final_new_state_wait_write_still_checks_all_original_deadlines(pending_formal, monkeypatch, lease):
    p = pending_formal
    append = p.h.store.append_event_in_transaction
    lock = p.s.h.tasks._lock_session
    def shorten(cur, session_id):
        session = lock(cur, session_id)
        if lease == 'session':
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,'{{turn_lease_expires_at}}',"
                "to_jsonb(clock_timestamp()+interval '2 seconds')) WHERE id=%s", (session_id,))
            session = lock(cur, session_id)
        elif lease == 'owner':
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at=clock_timestamp()+interval '2 seconds' WHERE binding_id=%s",
                (p.s.binding.binding_id,))
        else:
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                "to_jsonb(clock_timestamp()+interval '2 seconds')) WHERE id=%s", (p.s.item.id,))
        return session
    before = facts(p)
    # Shorten before admission captures any deadlines; hold the final write
    # beyond that exact original. All changes must roll back with the wait.
    monkeypatch.setattr(p.s.h.tasks, '_lock_session', shorten)
    def final_delay(*args, **kwargs):
        value = append(*args, **kwargs)
        if args[2].type == 'test_run.case_approval_waiting':
            args[0].execute('SELECT pg_sleep(2.1)')
        return value
    monkeypatch.setattr(p.h.store, 'append_event_in_transaction', final_delay)
    with pytest.raises(ContinuationLeaseLostError, match='locked lease deadline'):
        await park_state(p)
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


def forge_state(p, change):
    """Adversarially rehash envelopes too; hashes are not source authority."""
    from src.runtime.controlled_case_approval_wait import approval_wait_event
    from src.schemas.controlled_case_approval_wait import parse_controlled_case_approval_wait

    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {p.s.d.runs._attempt_table} WHERE id=%s", (p.s.attempt.id,))
        attempt = cur.fetchone()['record']
        asset = attempt['checkpoint_payload']['state_asset']
        if change == 'missing_slot':
            asset['state']['tool_messages'].pop()
        elif change == 'slot_string':
            asset['state']['tool_messages'][1]['content'] += ' not original'
        elif change == 'ordinals':
            asset['state']['tool_results'].reverse()
            asset['state']['tool_messages'].reverse()
        elif change == 'pending':
            asset['state']['pending_turn']['pending_approval_ids'].pop()
        elif change == 'framework_messages':
            asset['state']['framework_messages'] = []
        elif change == 'native_messages':
            asset['native_messages'] = []
        elif change == 'events':
            asset['state']['event_log'].pop()
        elif change == 'root_budget':
            asset['root_model_budget']['snapshot']['policy']['max_calls'] += 1
            asset['root_model_budget']['sha256'] = approval_value_hash({k: v for k, v in asset['root_model_budget'].items() if k != 'sha256'})
        else:
            field = {'producer': 'attempt_id', 'input': 'input_sha256', 'admission': 'admission_sha256'}[change]
            asset[field] = 'foreign' if change == 'producer' else '0' * 64
        asset['asset_sha256'] = approval_value_hash({k: v for k, v in asset.items() if k != 'asset_sha256'})
        cur.execute(f"UPDATE {p.s.d.runs._attempt_table} SET record=%s::jsonb WHERE id=%s", (json.dumps(attempt), p.s.attempt.id))
        cur.execute(f"SELECT result_payload FROM {p.s.h.tasks._tasks} WHERE id=%s", (p.h.task.id,))
        payload = cur.fetchone()['result_payload']
        receipt = payload['controlled_case_approval_wait']
        receipt['state_asset_sha256'] = asset['asset_sha256']
        receipt['receipt_sha256'] = approval_value_hash({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
        cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s", (json.dumps(payload), p.h.task.id))
        event = approval_wait_event(parse_controlled_case_approval_wait(receipt), p.s.binding)
        cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=jsonb_set(payload,'{{wait_sha256}}',%s::jsonb) WHERE id=%s",
            (json.dumps(receipt['receipt_sha256']), event.id))


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing_slot', 'slot_string', 'ordinals', 'pending', 'framework_messages',
    'native_messages', 'events', 'root_budget', 'producer', 'input', 'admission'])
async def test_rehashed_wait_state_cannot_replace_original_projection_sources(pending_formal, change):
    p = pending_formal
    await park_state(p)
    forge_state(p, change)
    before = facts(p)
    with pytest.raises(ValueError):
        await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps())
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_new_wait_state_rejects_omitted_acknowledged_suffix_before_any_parking(pending_formal):
    p = pending_formal
    before = facts(p)
    with framework_owner_scope(p.h.task.runtime_owner):
        with pytest.raises(ValueError, match='omit or add'):
            await waits(p).park(p.h.task.runtime_owner, proposals=proposals(p),
                event_log=p.outcome.state['event_log'][:-1], capabilities=state_caps())
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
async def test_historical_state_observation_does_not_require_later_task_error_to_be_resolved(pending_formal):
    from langgraph.checkpoint.base import WRITES_IDX_MAP
    from src.application.deep_agents.checkpoint_provider import checkpoint_serializer

    p = pending_formal
    await park_state(p)
    raw = wire(p, p.h.task.id)
    asset = await waits(p).read_state(raw, capabilities=state_caps())
    native = asset.natives[0]
    kind, blob = checkpoint_serializer().dumps_typed('isolated later execution error')
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
            "(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel,type,blob,task_path) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (p.s.binding.thread_id, p.s.binding.checkpoint_namespace, native.checkpoint_id, native.graph_task_id,
                WRITES_IDX_MAP['__error__'], '__error__', kind, blob, native.task_path))
    before = facts(p)
    assert await waits(p).read_state(raw, capabilities=state_caps()) == asset
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('field,maximum', [('controlled_case_checkpoint_max_version', 6),
    ('controlled_case_approval_wait_max_version', 1)])
async def test_new_wait_writer_requires_compatible_readers_before_any_write(pending_formal, field, maximum):
    p = pending_formal
    before = facts(p)
    with framework_owner_scope(p.h.task.runtime_owner):
        with pytest.raises(TaskDeferred, match='format_unavailable'):
            await waits(p).park(p.h.task.runtime_owner, proposals=proposals(p),
                event_log=p.outcome.state['event_log'], capabilities=replace(state_caps(), **{field: maximum}))
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
async def test_original_state_survives_decision_claim_with_actual_application_ordinals(pending_formal):
    p = pending_formal
    wait = await park_state(p)
    raw = wire(p, p.h.task.id)
    asset = await waits(p).read_state(raw, capabilities=state_caps())
    second = next(item for item in asset.state["tool_results"] if item["input"]["path"] == "b.py")
    task = await submissions(p).submit(raw, second["approval_id"], ApprovalDecisionRequest(decision="denied"),
        capabilities=state_caps())
    captured = await continuations(p).claim(wire(p, task.id), worker_id="approval-worker", capabilities=state_caps())
    before = facts(p)
    assert await waits(p).read_state(raw, capabilities=state_caps()) == asset
    assert facts(p) == before and captured.runtime_owner.controlled_case_lease is not None
    assert asset.state["tool_results"][1]["approval_id"] == task.reference_id
    assert wait.state_asset_sha256 == asset.asset_sha256


@pytest.mark.asyncio
async def test_old_checkpoint_or_wait_reader_defers_before_taking_a_new_owner(pending_formal):
    p = pending_formal
    wait = await park_state(p)
    for caps in (replace(state_caps(), controlled_case_checkpoint_max_version=6),
                 replace(state_caps(), controlled_case_approval_wait_max_version=1)):
        before = facts(p)
        with pytest.raises(TaskDeferred, match="format_unavailable"):
            await waits(p).acknowledge_delivery(wire(p, p.h.task.id), capabilities=caps)
        assert facts(p) == before
    assert len(p.requests) == 1 and p.effects == []


def test_wait_material_writer_stays_default_off_and_is_not_a_result_checkpoint():
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities

    caps = FrameworkWorkerCapabilities.from_artifact(deepagents_available=True)
    assert not caps.controlled_case_approval_state_available
    assert caps.controlled_case_checkpoint_max_version == 7 and caps.controlled_case_approval_wait_max_version == 2
    from src.schemas.controlled_case_admission import read_result_checkpoint

    with pytest.raises(ValueError, match='no supported controlled result'):
        read_result_checkpoint(SimpleNamespace(checkpoint_version=7))


@pytest.mark.asyncio
@pytest.mark.parametrize('pending_formal', [{'dispatch': True}], indirect=True)
async def test_explicit_initial_runtime_returns_the_same_retained_wait_state(pending_formal):
    from src.application.runtime.controlled_case_executor import ControlledCaseExecutor

    p = pending_formal
    raw = wire(p, p.h.task.id)
    task = await p.s.h.tasks.claim_controlled_case(raw, worker_id='retained-state-runtime', framework_capabilities=state_caps())
    worker = ControlledCaseExecutor(task_store=p.s.h.tasks, graph_adapter=p.h.adapter, framework_capabilities=state_caps())
    runtime = ControlledCaseRuntime(runtime_service=p.bridge.runtime, approval_wait_service=waits(p))
    result = await runtime.execute(task, executor=worker, resource_id='retained-state-runtime', fencing_token=1)
    asset = await waits(p).read_state(raw, capabilities=state_caps())
    assert result.approval_wait_receipt.schema_version == 2
    assert result.state == asset.state and result.tool_records == asset.state['tool_results']
    assert result.runtime_result is result.result_asset is result.assessment_asset is result.settlement_receipt is None
    assert len(result.graph_outcome.native_interrupts) == 2 and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_legacy_wait_never_acquires_fabricated_state_on_read(pending_formal):
    from tests.test_controlled_case_approval_wait import park

    p = pending_formal
    before = facts(p)
    with pytest.raises(TaskDeferred, match='state_writer_unavailable'):
        with framework_owner_scope(p.h.task.runtime_owner):
            await waits(p).park(p.h.task.runtime_owner, proposals=proposals(p),
                event_log=p.outcome.state['event_log'], capabilities=replace(state_caps(), controlled_case_approval_state_available=False))
    assert facts(p) == before
    receipt = await park(p)
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match='no original retained material'):
        await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps())
    assert receipt.schema_version == 1 and facts(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('field,maximum', [('controlled_case_checkpoint_max_version', 6),
    ('controlled_case_approval_wait_max_version', 1)])
async def test_incompatible_successor_reader_defers_before_both_claims(pending_formal, field, maximum):
    p = pending_formal
    wait = await park_state(p)
    task = await submissions(p).submit(wire(p, p.h.task.id), wait.batch[0].approval_id,
        ApprovalDecisionRequest(decision='approved'), capabilities=state_caps())
    before = facts(p)
    with pytest.raises(TaskDeferred, match='format_unavailable'):
        await continuations(p).claim(wire(p, task.id), worker_id='old-reader',
            capabilities=replace(state_caps(), **{field: maximum}))
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest_asyncio.fixture
async def mixed_formal(formal_case, tmp_path, request):
    """Real safe tool result plus real pending sibling in the official graph."""
    s = formal_case
    pending_first = request.param
    reply = _tool_reply('openai_chat_completions')
    calls = reply['choices'][0]['message']['tool_calls']
    calls[0]['id'], calls[0]['function']['arguments'] = 'A', '{"path":"a.py"}'
    other = deepcopy(calls[0])
    other['id'], other['function']['arguments'] = 'B', '{"path":"b.py"}'
    calls.append(other)
    calls[0 if pending_first else 1]['function']['name'] = 'wait_source'
    with _provider('openai_chat_completions', first_reply=reply) as (url, requests):
        async def prepare_input(models):
            p = assembly(s, tmp_path / 'mixed', models)
            tool = p.parts.tool_registry.get('inspect_source').model_copy(update={'key': 'wait_source', 'permission_level': 'ask'})
            p.parts.tool_registry.register_dynamic(tool, 'wait_source')
            # Assembly nodes captured this original registry at construction.
            # Update the fixture descriptor it returns, not a detached registry.
            agent = p.parts.agent_registry.resolve_for_message('')
            agent.supported_tools = ['inspect_source', 'wait_source']
            p.request.context['requested_tool_keys'].append('wait_source')
            asset = await prepare(s, p)
            assert set(asset.runtime_preparation.initial_state['model_visible_tool_keys']) == {'inspect_source', 'wait_source'}
            return asset

        config = _config('openai_chat_completions', url)
        async with execution(s, config, prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                tool = bridge.runtime._tool_registry.get('inspect_source').model_copy(update={'key': 'wait_source', 'permission_level': 'ask'})
                bridge.runtime._tool_registry.register_dynamic(tool, 'wait_source')
                bridge.runtime._tool_runtime_service._handlers['wait_source'] = bridge.runtime._tool_runtime_service._handlers['inspect_source']
                outcome = await ControlledCaseRuntime(runtime_service=bridge.runtime).execute(h.task,
                    executor=executor(s, h), resource_id='isolated-mixed-wait', fencing_token=1)
                assert len(outcome.graph_outcome.native_interrupts) == len(effects) == 1
                yield SimpleNamespace(s=s, h=h, bridge=bridge, outcome=outcome, requests=requests, effects=effects)


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed_formal', [True, False], indirect=True)
async def test_mixed_wait_keeps_completion_ordinal_and_exact_original_terminal_string(mixed_formal):
    p = mixed_formal
    budget = await p.s.calls.snapshot(p.s.binding)
    native = native_originals(p)
    wait = await park_state(p)
    asset = await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps())
    assert len(asset.tool_materials) == 1 and len(wait.batch) == 1
    assert [r['status'] for r in asset.state['tool_results']] == ['completed', 'waiting_approval']
    material, = asset.tool_materials
    assert material.message.metadata['ordinal'] == 1
    assert asset.state['tool_messages'][0] == material.runtime_message
    assert [r['tool_key'] for r in asset.state['tool_results']] == ['inspect_source', 'wait_source']
    before = facts(p)
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps()) == asset
    assert facts(p) == before and native_originals(p) == native
    assert await p.s.calls.snapshot(p.s.binding) == budget and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
async def test_real_b_refusal_uses_frozen_second_ordinal_and_old_state_remains_original(pending_formal):
    from src.application.runtime.controlled_case_executor import ControlledCaseExecutor
    from src.application.runtime.tool_messages import build_rejected_tool_projection

    p = pending_formal
    await park_state(p)
    raw = wire(p, p.h.task.id)
    asset = await waits(p).read_state(raw, capabilities=state_caps())
    b = asset.state['tool_results'][1]
    task = await submissions(p).submit(raw, b['approval_id'], ApprovalDecisionRequest(decision='denied'), capabilities=state_caps())
    task = await continuations(p).claim(wire(p, task.id), worker_id='wait-state-worker', capabilities=state_caps())
    state, records = deepcopy(asset.state), deepcopy(asset.state['tool_results'])

    async def tools(prepared):
        values, _ = await p.bridge.runtime._build_deep_agent_business_tools(state=state, tool_records=records)
        return values

    continuation = continuations(p)
    worker = ControlledCaseExecutor(task_store=p.s.h.tasks, graph_adapter=p.h.adapter,
        approval_continuations=continuation, framework_capabilities=state_caps())
    outcome = await worker.execute(task, build_business_tools=tools, resource_id='wait-state-rejection', fencing_token=2)
    assert outcome.native_terminal is None and len(outcome.native_interrupts) == 1
    native = next(n for n in asset.natives if n.tool_call_id == 'B')
    calls = []
    def refusal(job, message):
        calls.append(job.id)
        return build_rejected_tool_projection(binding=asset.binding, source=native, job=job, approval_id=task.reference_id,
            ordinal=2, hygiene_metadata=asset.hygiene_metadata, model_content=message.content, include_runtime_message=True)

    await continuation.apply_decision(task, capabilities=state_caps(), rejection_projector=refusal)
    result = await continuation.read_tool_projection(task, capabilities=state_caps())
    assert result.message.metadata['ordinal'] == 2 and result.record.input['path'] == 'b.py'
    assert calls == [result.record.job_id] and result.record.status == 'denied'
    before = facts(p)
    assert await waits(p).read_state(raw, capabilities=state_caps()) == asset
    assert facts(p) == before and p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['state_write', 'item', 'return', 'audit', 'ack_loss', 'cancel_waiter'])
async def test_new_wait_state_and_all_parking_facts_share_one_commit(pending_formal, monkeypatch, failure):
    p = pending_formal
    before = facts(p)
    service = waits(p)
    entered, release, finished = Event(), Event(), Event()
    original = type(service)._park
    append = p.h.store.append_event_in_transaction
    def parking(slf, *args, **kwargs):
        try:
            result = original(slf, *args, **kwargs)
            if failure == 'ack_loss':
                raise RuntimeError('isolated state committed receipt loss')
            return result
        finally:
            finished.set()
    def audit(*args, **kwargs):
        value = append(*args, **kwargs)
        if args[2].type == 'test_run.case_approval_waiting':
            if failure == 'audit':
                raise RuntimeError('isolated state rollback')
            if failure == 'cancel_waiter':
                entered.set()
                assert release.wait(30)
        return value
    def failing(function):
        def write(*args, **kwargs):
            function(*args, **kwargs)
            raise RuntimeError('isolated state rollback')
        return write
    with monkeypatch.context() as patch:
        patch.setattr(type(service), '_park', parking)
        patch.setattr(p.h.store, 'append_event_in_transaction', audit)
        if failure in {'state_write', 'item'}:
            cls = type(p.s.d.runs)
            name = '_write_attempt' if failure == 'state_write' else '_write_item'
            patch.setattr(cls, name, failing(getattr(cls, name)))
        elif failure == 'return':
            patch.setattr(p.s.h.tasks, '_update_status', failing(p.s.h.tasks._update_status))
        operation = asyncio.create_task(park_state(p))
        if failure == 'cancel_waiter':
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match='isolated state'):
                await operation
    if failure not in {'ack_loss', 'cancel_waiter'}:
        assert facts(p) == before
    else:
        after = facts(p)
        asset = await waits(p).read_state(wire(p, p.h.task.id), capabilities=state_caps())
        assert asset.state['control_state'] == 'waiting_approval' and facts(p) == after
        with pytest.raises(ContinuationLeaseLostError):
            await park_state(p)
        assert facts(p) == after
    assert p.effects == [] and len(p.requests) == 1
