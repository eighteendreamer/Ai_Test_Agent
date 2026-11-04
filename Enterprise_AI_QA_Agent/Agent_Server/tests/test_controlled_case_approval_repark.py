"""Atomic successor parking consumes retained originals, never reexecutes B."""
from dataclasses import replace
from copy import deepcopy
from datetime import timedelta
import asyncio
import importlib
import json
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope, graph_event_scope
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_runtime import owned, ports, expire_lease
from tests.test_controlled_case_approval_claim import wire, implementation as continuations
from tests.test_controlled_case_approval_busy_submission import decide_a
from tests.test_controlled_case_approval_progress import retain, implementation as progress, progress_caps
from tests.test_controlled_case_approval_wait import facts, clock, implementation as waits


def implementation(p):
    from tests.test_controlled_case_approval_claim import implementation as continuations

    return importlib.import_module("src.runtime.controlled_case_approval_repark").ControlledCaseApprovalReparks(
        continuations(p))


def repark_caps():
    return replace(progress_caps(), controlled_case_approval_repark_available=True)


async def prepared(p, *, decision="approved", queued=True):
    root, b = await owned(p, decision)
    a = await decide_a(p, root) if queued else None
    runtime, worker = ports(p)
    outcome = await runtime.execute(b, executor=worker, resource_id="successor-repark", fencing_token=2)
    asset = await retain(p, b, outcome)
    return root, b, a, asset


async def repark(p, b, *, caps=None):
    with framework_owner_scope(b.runtime_owner):
        return await implementation(p).repark(b, capabilities=caps or repark_caps())


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_repark_closes_only_original_b_and_preserves_root_and_queued_a(pending_formal, decision):
    p = pending_formal
    root, b, a, asset = await prepared(p, decision=decision)
    before = await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(a.id), native_originals(p)
    budget = await p.s.calls.snapshot(p.s.binding)
    receipt = await repark(p, b)
    assert receipt.scope == "controlled_case_atomic_approval_repark_only"
    assert receipt.origin_task_id == p.h.task.id and receipt.state_asset_sha256 == asset.asset_sha256
    assert receipt.parent_wait_sha256 == b.input["controlled_case_approval_admission"]["wait_sha256"]
    assert receipt.task_id == b.id and receipt.attempt_id == b.attempt_id
    assert receipt.owner_epoch == b.runtime_owner.owner_epoch
    assert {entry.approval_id for entry in receipt.batch} == {a.reference_id}
    assert before == (await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(a.id), native_originals(p))
    actual = await p.s.h.tasks.get(b.id)
    assert actual.status == "completed" and actual.result_status == "waiting_approval"
    detail = await p.s.d.runs.get_run(p.s.item.run_id)
    item = next(i for i in detail.items if i.id == p.s.item.id)
    attempt = next(i for i in detail.attempts if i.id == p.s.attempt.id)
    assert item.status == attempt.status == "waiting_approval" and item.lease_expires_at is None
    assert detail.results == [] and attempt.checkpoint_version == 8
    assert attempt.checkpoint_payload["initial_wait"]["state_asset"] == root.model_dump(mode="json")
    assert attempt.checkpoint_payload["progress_assets"] == [asset.model_dump(mode="json")]
    session = await p.h.store.get_session(p.s.session.id)
    assert session.status == "waiting_approval"
    assert session.metadata["pending_turn"]["controlled_case_wait_sha256"] == receipt.receipt_sha256
    assert session.metadata["pending_turn"]["pending_approval_ids"] == [a.reference_id]
    assert all(k not in session.metadata for k in (
        "turn_lease_token", "turn_lease_owner", "turn_lease_turn_id", "turn_lease_expires_at"))
    assert await p.s.calls.snapshot(p.s.binding) == budget
    now = clock(p)
    assert await p.s.calls.root_activity_seconds(p.s.binding, as_of=now) == (
        await p.s.calls.root_activity_seconds(p.s.binding, as_of=now + timedelta(seconds=60)))
    saved = facts(p)
    assert await implementation(p).acknowledge_delivery(wire(p, b.id), capabilities=progress_caps()) == receipt
    assert await repark(p, b) == receipt and facts(p) == saved
    assert await progress(p).read(wire(p, b.id), capabilities=progress_caps()) == asset
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=progress_caps()) == root
    with pytest.raises(TaskDeferred, match="repark_format_unavailable"):
        await implementation(p).acknowledge_delivery(wire(p, b.id), capabilities=replace(
            progress_caps(), controlled_case_approval_repark_max_version=0))
    assert len(p.requests) == 1 and len(p.effects) == (decision == "approved")
    assert all(word not in json.dumps(receipt.model_dump(mode="json"))
        for word in ("lease_token", "owner_token", "arguments", "b.py"))
    # Format support/parking alone cannot enable the default-off successor.
    # An explicitly enabled current-wait reader is exercised separately.
    with pytest.raises(TaskDeferred, match="case_approval_successor_unavailable"):
        await continuations(p).claim(wire(p, a.id), worker_id="no-old-root-bypass", capabilities=progress_caps())
    assert facts(p) == saved


@pytest.mark.asyncio
async def test_repark_retry_requires_exact_captured_envelope_even_after_commit(pending_formal):
    p = pending_formal
    _, b, _, _ = await prepared(p)
    await repark(p, b)
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await repark(p, b.model_copy(update={"reference_id": "foreign-approval"}))
    assert facts(p) == before


def test_repark_has_independent_default_off_writer_and_historical_codec():
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities

    caps = FrameworkWorkerCapabilities.from_artifact(deepagents_available=True)
    assert not caps.controlled_case_approval_repark_available
    assert caps.controlled_case_approval_repark_max_version == 1
    with pytest.raises(TaskDeferred, match="repark_format_unavailable"):
        caps.require_case_approval_repark_format({"schema_version": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["writer", "codec", "owner", "sink", "serialized", "unretained"])
async def test_repark_requires_writer_captured_owner_and_original_retention(pending_formal, change):
    p = pending_formal
    if change == "unretained":
        _, b = await owned(p)
        runtime, worker = ports(p)
        await runtime.execute(b, executor=worker, resource_id="no-repark-material", fencing_token=2)
    else:
        _, b, _, _ = await prepared(p)
    caps = repark_caps()
    if change == "writer":
        caps = progress_caps()
    elif change == "codec":
        caps = replace(caps, controlled_case_approval_repark_max_version=0)
    elif change == "serialized":
        b = type(b).model_validate(b.model_dump(mode="json"))
    before = facts(p)
    with framework_owner_scope(None if change == "owner" else b.runtime_owner):
        with graph_event_scope(object() if change == "sink" else None):
            with pytest.raises((ValueError, TaskDeferred, ContinuationLeaseLostError)):
                await implementation(p).repark(b, capabilities=caps)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
async def test_historical_repark_ack_survives_cancelled_run_and_cannot_mutate_or_replay(pending_formal, monkeypatch):
    p = pending_formal
    _, b, _, _ = await prepared(p)
    receipt = await repark(p, b)
    await p.s.d.runs.cancel_run(p.s.item.run_id, "isolated historical repark ACK", receipt.parked_at)
    before = facts(p)

    def forbidden(*args, **kwargs):
        raise AssertionError("Historical re-park ACK must not dispatch or mutate")

    monkeypatch.setattr(p.h.adapter, "execute_frozen", forbidden)
    for name in ("_claim_task_attempt", "_adopt_runtime_owner", "_update_status", "_clear_lease"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    assert await implementation(p).acknowledge_delivery(wire(p, b.id), capabilities=progress_caps()) == receipt
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_repark_rejects_expired_dual_authority_without_changing_originals(pending_formal, lease):
    p = pending_formal
    _, b, _, _ = await prepared(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        expire_lease(p, cur, lease)
    before = facts(p)
    with pytest.raises(ContinuationLeaseLostError):
        await repark(p, b)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_final_repark_audit_crossing_locked_deadline_rolls_back_every_write(pending_formal, monkeypatch, lease):
    p = pending_formal
    _, b, _, _ = await prepared(p)
    service = implementation(p)
    settled = service._waits._require_settled_tool_activity
    append = p.h.store.append_event_in_transaction
    reached = []

    def shorten(cur, *args):
        result = settled(cur, *args)
        expire_lease(p, cur, lease, delay="1 second")
        return result

    def cross(cur, session_id, event, **kwargs):
        result = append(cur, session_id, event, **kwargs)
        if event.type == "test_run.case_approval_reparked":
            reached.append(True)
            cur.execute("SELECT pg_sleep(1.1)")
        return result

    monkeypatch.setattr(service._waits, "_require_settled_tool_activity", shorten)
    monkeypatch.setattr(p.h.store, "append_event_in_transaction", cross)
    before = facts(p)
    with framework_owner_scope(b.runtime_owner), pytest.raises(ContinuationLeaseLostError):
        await service.repark(b, capabilities=repark_caps())
    assert reached == [True] and facts(p) == before
    assert len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "reply_loss", "cancel_waiter"])
async def test_repark_unknown_transaction_receipt_never_reexecutes_work(pending_formal, monkeypatch, failure):
    p = pending_formal
    _, b, _, _ = await prepared(p)
    service = implementation(p)
    entered, release, finished = Event(), Event(), Event()
    original, append = type(service)._repark, p.h.store.append_event_in_transaction
    before = facts(p)

    def event(*args, **kwargs):
        result = append(*args, **kwargs)
        if args[2].type == "test_run.case_approval_reparked":
            if failure == "rollback":
                raise RuntimeError("isolated repark rollback")
            if failure == "cancel_waiter":
                entered.set()
                assert release.wait(30)
        return result

    def complete(slf, *args):
        try:
            result = original(slf, *args)
            if failure == "reply_loss":
                raise RuntimeError("isolated repark reply loss")
            return result
        finally:
            finished.set()

    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_repark", complete)
        patch.setattr(p.h.store, "append_event_in_transaction", event)
        async def invoke():
            with framework_owner_scope(b.runtime_owner):
                return await service.repark(b, capabilities=repark_caps())
        waiter = asyncio.create_task(invoke())
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match="isolated repark"):
                await waiter
    if failure == "rollback":
        assert facts(p) == before
        with pytest.raises(FrameworkApprovalRecoveryRequired):
            await service.acknowledge_delivery(wire(p, b.id), capabilities=progress_caps())
    receipt = await repark(p, b)
    saved = facts(p)
    assert await service.acknowledge_delivery(wire(p, b.id), capabilities=progress_caps()) == receipt
    assert await repark(p, b) == receipt and facts(p) == saved
    assert len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
async def test_repark_competitors_share_one_receipt_and_audit(pending_formal):
    p = pending_formal
    _, b, _, _ = await prepared(p, queued=False)
    first, second = await asyncio.gather(repark(p, b), repark(p, b))
    assert first == second
    events = await p.h.store.list_events(p.s.session.id)
    assert len([e for e in events if e.type == "test_run.case_approval_reparked"]) == 1
    assert len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["state_asset_sha256", "parent_wait_sha256", "owner_epoch", "task_return_sha256"])
async def test_rehashed_repark_receipt_and_audit_do_not_replace_original_facts(pending_formal, field):
    from src.runtime.controlled_case_approval_repark import repark_event, REPARK_KEY
    from src.schemas.controlled_case_approval_repark import ControlledCaseApprovalReparkReceipt
    from src.schemas.framework_approval import approval_value_hash

    p = pending_formal
    _, b, _, _ = await prepared(p)
    receipt = await repark(p, b)
    raw = deepcopy(receipt.model_dump(mode="json"))
    raw[field] = raw[field] + 1 if field == "owner_epoch" else "0" * 64
    raw["receipt_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "receipt_sha256"})
    event = repark_event(ControlledCaseApprovalReparkReceipt.model_validate(raw), b.runtime_binding)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s",
            (json.dumps({REPARK_KEY: raw}), b.id))
        cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=%s::jsonb WHERE id=%s",
            (json.dumps(event.payload), event.id))
    before = facts(p)
    with pytest.raises(ValueError):
        await implementation(p).acknowledge_delivery(wire(p, b.id), capabilities=progress_caps())
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["native_error", "pending_ids"])
async def test_new_repark_requires_current_pending_facts_not_only_historical_material(pending_formal, change):
    from langgraph.checkpoint.base import WRITES_IDX_MAP
    from src.application.deep_agents.checkpoint_provider import checkpoint_serializer

    p = pending_formal
    _, b, _, asset = await prepared(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if change == "native_error":
            sibling, = asset.natives
            kind, blob = checkpoint_serializer().dumps_typed("isolated later sibling error")
            cur.execute(f"INSERT INTO {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
                "(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel,type,blob,task_path) "
                "VALUES (%s,%s,%s,%s,%s,'__error__',%s,%s,%s)",
                (p.s.binding.thread_id, p.s.binding.checkpoint_namespace, sibling.checkpoint_id,
                    sibling.graph_task_id, WRITES_IDX_MAP['__error__'], kind, blob, sibling.task_path))
        else:
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{pending_turn,pending_approval_ids}','[]'::jsonb) WHERE id=%s", (p.s.session.id,))
    before = facts(p)
    assert await progress(p).read(wire(p, b.id), capabilities=progress_caps()) == asset
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await repark(p, b)
    assert facts(p) == before and len(p.requests) == len(p.effects) == 1
