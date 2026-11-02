"""A lost producer is settled through its scoped successor, never replayed."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_settlement_recovery import ControlledCaseSettlementRecovery
from src.runtime.controlled_case_recovery_settlement import ControlledCaseRecoverySettlement, RECEIPT_KEY
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from src.schemas.controlled_case_admission import read_result_checkpoint
from src.schemas.runtime_binding import RuntimeExecutionOwner
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_projection_parts import checkpoint
from tests.test_controlled_case_settlement import implementation
from tests.test_controlled_case_settlement_ack import originals_digest
from tests.test_controlled_case_settlement_recovery import recovery_case, ensure, capabilities, successor_row
from tests.test_controlled_case_assessment_reconciliation import lost
from tests.test_controlled_case_admission import wait_for_session_sql_lock


def origin_identity(p):
    h = p.s.h
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        facts = []
        for table, column, identity in ((h.tasks._tasks, "id", p.h.task.id),
            (h.tasks._attempts, "id", p.h.task.attempt_id),
            (h.tasks._tasks + "_runtime", "binding_id", p.s.binding.binding_id)):
            cur.execute(f"SELECT to_jsonb(t) AS row FROM {table} t WHERE {column}=%s", (identity,))
            facts.append(cur.fetchone()["row"])
        return approval_value_hash(facts)


def wire(p, task_id):
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {p.s.h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            ("agent_turn:" + task_id,))
        return cur.fetchone()["payload"]


@pytest.mark.asyncio
async def test_scoped_successor_atomically_settles_original_assessment_and_preserves_lost_producer(recovery_case, monkeypatch):
    p = recovery_case
    task = await ensure(p)
    original, prepared = origin_identity(p), checkpoint(p)
    assessment = read_result_checkpoint(prepared).assessment
    service = ControlledCaseSettlementRecovery(p.s.h.tasks)
    claim = service.claim
    caps = replace(capabilities(), controlled_case_settlement_available=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("settlement must not construct graph execution authority")
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    lease = await claim(wire(p, task.id), worker_id="isolated-settlement-worker", capabilities=caps)
    assert not hasattr(lease, "owner_epoch") and not hasattr(lease, "controlled_case_lease")
    receipt = await service.complete(lease, capabilities=caps)
    item = await p.s.d.runs.get_item(p.s.item.id)
    result = await p.s.d.runs.get_result(receipt.result_id)
    assert item.status == result.status == assessment.completion.status
    assert result.attempt_id == p.s.attempt.id and item.result_id == result.id
    assert result.model_dump(mode="json", include=set(type(assessment.completion).model_fields)) == assessment.completion.model_dump(mode="json")
    assert origin_identity(p) == original
    current = checkpoint(p)
    for name in ("checkpoint_key", "checkpoint_version", "checkpoint_payload", "checkpoint_at"):
        assert getattr(current, name) == getattr(prepared, name)
    assert (await p.s.h.tasks.get(task.id)).status == "completed"
    session = await p.s.h.store.get_session(p.s.session.id)
    assert session.status.value == "completed"
    control = read_result_checkpoint(prepared).projection_receipts.application_projection_commit.control
    assert all(session.metadata["control"][key] == value for key, value in control.items())
    assert session.metadata["control"]["queued_input_count"] == 0
    assert len(p.requests) == 2 and len(p.effects) == 1
    before_ack = originals_digest(p)
    assert await service.acknowledge(wire(p, task.id), capabilities=capabilities()) == receipt
    assert await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=capabilities()) == receipt
    assert await service.complete(lease, capabilities=caps) == receipt
    reread = await ensure(p)
    assert reread.id == task.id and reread.status == "completed"
    assert originals_digest(p) == before_ack


def writer_caps(**changes):
    return replace(capabilities(), controlled_case_settlement_available=True, **changes)


async def claimed(p):
    task = await ensure(p)
    service = ControlledCaseSettlementRecovery(p.s.h.tasks)
    raw = wire(p, task.id)
    lease = await service.claim(raw, worker_id="settlement-only-worker", capabilities=writer_caps())
    return service, raw, lease


def expire(p):
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
            "'{turn_lease_expires_at}',to_jsonb(clock_timestamp()-interval '1 second')) WHERE id=%s", (p.s.session.id,))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["writer", "receipt_reader", "admission_reader", "checkpoint_reader", "dependency", "runtime"])
async def test_claim_and_complete_require_scoped_writer_and_compatible_original_readers(recovery_case, change):
    p = recovery_case
    service, raw, lease = await claimed(p)
    changes = {"writer": {"controlled_case_settlement_available": False},
        "receipt_reader": {"controlled_case_recovery_receipt_max_version": 0},
        "admission_reader": {"controlled_case_settlement_max_version": 0},
        "checkpoint_reader": {"controlled_case_checkpoint_max_version": 4},
        "dependency": {"dependency_lock_sha256": "b"*64}, "runtime": {"runtime_version": "future"}}
    caps = replace(writer_caps(), **changes[change])
    before = originals_digest(p)
    with pytest.raises(TaskDeferred):
        await service.claim(raw, worker_id="unsupported-worker", capabilities=caps)
    with pytest.raises(TaskDeferred):
        await service.complete(lease, capabilities=caps)
    assert originals_digest(p) == before


@pytest.mark.asyncio
async def test_concurrent_claim_and_complete_leave_one_result_and_no_graph_authority(recovery_case, monkeypatch):
    p = recovery_case
    task = await ensure(p)
    service, raw = ControlledCaseSettlementRecovery(p.s.h.tasks), wire(p, task.id)
    protected = origin_identity(p)
    def forbidden(*args, **kwargs):
        raise AssertionError("scoped settlement cannot run graph/finalizer or adopt its owner")
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    for name in ("claim", "claim_controlled_case", "begin", "finish", "_update_status"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    claims = await asyncio.gather(*(service.claim(raw, worker_id=f"isolated-worker-{i}", capabilities=writer_caps())
        for i in range(4)), return_exceptions=True)
    winners = [value for value in claims if not isinstance(value, Exception)]
    assert len(winners) == 1 and sum(isinstance(value, TaskDeferred) for value in claims) == 3
    receipts = await asyncio.gather(*(service.complete(winners[0], capabilities=writer_caps()) for _ in range(4)))
    assert all(value == receipts[0] for value in receipts)
    events = await p.s.h.store.list_events(p.s.session.id)
    assert len([e for e in events if e.type == "test_run.case_recovery_settled"]) == 1
    assert len([e for e in events if e.type == "agent_task.completed"]) == 1
    assert origin_identity(p) == protected and len(p.requests) == 2 and len(p.effects) == 1
    before = originals_digest(p)
    with pytest.raises(RuntimeError):
        await service.claim(raw, worker_id="terminal-must-not-reclaim", capabilities=writer_caps())
    assert originals_digest(p) == before


@pytest.mark.asyncio
async def test_expired_lease_is_fenced_and_only_new_attempt_can_settle(recovery_case):
    p = recovery_case
    service, raw, old = await claimed(p)
    protected = origin_identity(p)
    assert await service.renew(old, lease_seconds=120, capabilities=writer_caps()) is True
    expire(p)
    before = originals_digest(p)
    for operation in (service.complete(old, capabilities=writer_caps()),
        service.renew(old, lease_seconds=120, capabilities=writer_caps())):
        with pytest.raises(ContinuationLeaseLostError):
            await operation
    assert originals_digest(p) == before
    new = await service.claim(raw, worker_id="replacement-settlement-worker", capabilities=writer_caps())
    assert new.attempt_no == old.attempt_no + 1 and new.attempt_id != old.attempt_id
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {p.s.h.tasks._attempts} WHERE id=%s", (old.attempt_id,))
        previous = cur.fetchone()
        assert previous["status"] == "interrupted" and previous["finished_at"] is not None
    before = originals_digest(p)
    with pytest.raises(ContinuationLeaseLostError):
        await service.complete(old, capabilities=writer_caps())
    assert originals_digest(p) == before and origin_identity(p) == protected
    receipt = await service.complete(new, capabilities=writer_caps())
    before = originals_digest(p)
    with pytest.raises(ContinuationLeaseLostError):
        await service.complete(old, capabilities=writer_caps())
    assert await service.acknowledge(raw, capabilities=capabilities()) == receipt
    assert originals_digest(p) == before


@pytest.mark.asyncio
async def test_scoped_task_attempt_cannot_begin_or_finish_using_a_forged_graph_owner(recovery_case):
    p = recovery_case
    service, raw, lease = await claimed(p)
    tasks = p.s.h.tasks
    worker_task = tasks._record(successor_row(p, lease.task_id))
    forged = RuntimeExecutionOwner(binding=lease.binding, owner_epoch=p.h.task.runtime_owner.owner_epoch,
        owner_token=lease.lease_token, task_id=lease.task_id, attempt_id=lease.attempt_id, worker_id=lease.worker_id)
    before = originals_digest(p)
    for execution in (worker_task, worker_task.model_copy(update={"runtime_owner": forged})):
        with pytest.raises(ContinuationLeaseLostError):
            await tasks.begin(execution, resource_id="not-allocated", fencing_token=1)
        with pytest.raises(ContinuationLeaseLostError):
            await tasks.finish(execution, status="completed", result_status="completed")
    with pytest.raises(RuntimeError):
        await service.acknowledge(raw, capabilities=capabilities())
    assert originals_digest(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel", "session", "run", "attempt", "origin", "admission", "outbox"])
async def test_changed_pending_facts_never_settle_or_repair(recovery_case, change):
    p = recovery_case
    service, raw, lease = await claimed(p)
    tasks = p.s.h.tasks
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if change == "cancel":
            cur.execute(f"UPDATE {tasks._tasks} SET interrupt_requested=true WHERE id=%s", (lease.task_id,))
        elif change == "session":
            cur.execute(f"UPDATE {tasks._session_table} SET status='cancelled' WHERE id=%s", (p.s.session.id,))
        elif change == "run":
            cur.execute(f"UPDATE {p.s.d.runs._run_table} SET record=jsonb_set(record,'{{status}}','\"cancelled\"') WHERE id=%s", (p.s.item.run_id,))
        elif change == "attempt":
            cur.execute(f"UPDATE {tasks._attempts} SET worker_id='foreign' WHERE id=%s", (lease.attempt_id,))
        elif change == "origin":
            cur.execute(f"UPDATE {tasks._tasks} SET error_code='foreign' WHERE id=%s", (p.h.task.id,))
        elif change == "admission":
            cur.execute(f"UPDATE {tasks._tasks} SET input_hash=%s WHERE id=%s", ("b"*64, lease.task_id))
        else:
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_task_outbox_table} SET stream='foreign' WHERE event_key=%s",
                ("agent_turn:" + lease.task_id,))
    before = originals_digest(p)
    with pytest.raises((RuntimeError, ValueError, ContinuationLeaseLostError)):
        await service.complete(lease, capabilities=writer_caps())
    assert originals_digest(p) == before and checkpoint(p).checkpoint_version == 5
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["session", "result", "task", "clear_lease", "audit"])
async def test_actual_writes_rollback_session_result_totals_task_and_receipt_together(recovery_case, monkeypatch, window):
    p = recovery_case
    service, raw, lease = await claimed(p)
    before = originals_digest(p)
    tasks = p.s.h.tasks
    target, method = (type(p.s.d.runs), "_complete_locked_item_in_transaction") if window == "result" else (
        (tasks._sessions, "append_event_in_transaction") if window == "audit" else
        (tasks, {"session": "_confirm_application_settlement", "task": "_update_task_status", "clear_lease": "_clear_lease"}[window]))
    original = getattr(target, method)
    def fail_after_write(*args, **kwargs):
        result = original(*args, **kwargs)
        if window != "audit" or args[2].type == "test_run.case_recovery_settled":
            raise RuntimeError("isolated actual recovery settlement rollback")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(target, method, fail_after_write)
        with pytest.raises(RuntimeError, match="isolated actual"):
            await service.complete(lease, capabilities=writer_caps())
    assert originals_digest(p) == before
    receipt = await service.complete(lease, capabilities=writer_caps())
    assert await service.acknowledge(raw, capabilities=capabilities()) == receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["receipt_field", "receipt_version", "receipt_identity", "result", "origin_owner",
    "task_event", "audit", "outbox", "admission_audit"])
async def test_all_ack_ports_reject_drift_without_writers_or_repair(recovery_case, monkeypatch, change):
    p = recovery_case
    service, raw, lease = await claimed(p)
    receipt = await service.complete(lease, capabilities=writer_caps())
    tasks = p.s.h.tasks
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if change.startswith("receipt_"):
            payload = deepcopy(successor_row(p, lease.task_id)["result_payload"])
            value = payload[RECEIPT_KEY]
            if change == "receipt_field":
                value["future_receipt_field"] = True
            elif change == "receipt_version":
                value["schema_version"] = 99
            else:
                value["result_id"] = "foreign"
            value["receipt_sha256"] = approval_value_hash({key: v for key, v in value.items() if key != "receipt_sha256"})
            import json
            cur.execute(f"UPDATE {tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s", (json.dumps(payload), lease.task_id))
        elif change == "result":
            cur.execute(f"UPDATE {p.s.d.runs._result_table} SET record=jsonb_set(record,'{{summary}}','\"changed\"') WHERE id=%s", (receipt.result_id,))
        elif change == "origin_owner":
            cur.execute(f"UPDATE {tasks._tasks}_runtime SET worker_id='foreign' WHERE binding_id=%s", (lease.binding.binding_id,))
        elif change == "outbox":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_task_outbox_table} SET stream='foreign' WHERE event_key=%s", ("agent_turn:"+lease.task_id,))
        else:
            kind = {"task_event": "agent_task.completed", "audit": "test_run.case_recovery_settled", "admission_audit": "test_run.case_settlement_admitted"}[change]
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_event_table} WHERE type=%s", (kind,))
    before = originals_digest(p)
    def forbidden(*args, **kwargs):
        raise AssertionError("ACK must not execute a write or construct graph authority")
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    monkeypatch.setattr(type(p.s.d.runs), "_complete_locked_item_in_transaction", forbidden)
    monkeypatch.setattr(tasks, "_update_task_status", forbidden)
    for operation in (service.acknowledge(raw, capabilities=capabilities()), ensure(p),
        implementation(p).acknowledge_delivery(delivery(p.s), capabilities=capabilities())):
        with pytest.raises((RuntimeError, ValueError, TaskDeferred)):
            await operation
    assert originals_digest(p) == before


@pytest.mark.asyncio
async def test_waiter_cancellation_is_unknown_until_actual_transaction_exits(recovery_case, monkeypatch, caplog):
    p = recovery_case
    service, raw, lease = await claimed(p)
    entered, release, finished = Event(), Event(), Event()
    original = p.s.h.store.append_event_in_transaction
    transaction = ControlledCaseRecoverySettlement._transaction
    def hold(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2].type == "test_run.case_recovery_settled":
            entered.set()
            assert release.wait(30), "isolated cancellation gate timed out"
        return result
    def track(*args, **kwargs):
        try:
            return transaction(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(p.s.h.store, "append_event_in_transaction", hold)
    monkeypatch.setattr(ControlledCaseRecoverySettlement, "_transaction", track)
    operation = asyncio.create_task(service.complete(lease, capabilities=writer_caps()))
    try:
        assert await asyncio.to_thread(entered.wait, 30)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert not finished.is_set()
        assert any(r.message == "controlled_case_recovery_settlement_unknown" and r.automatic_replay is False for r in caplog.records)
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 30)
        await asyncio.gather(operation, return_exceptions=True)
    receipt = await service.acknowledge(raw, capabilities=capabilities())
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id == receipt.result_id
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_successor_row_lock_wait_rechecks_expiry_on_database_clock(recovery_case):
    p = recovery_case
    service, raw, lease = await claimed(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
            "'{turn_lease_expires_at}',to_jsonb(clock_timestamp()+interval '700 milliseconds')) WHERE id=%s", (p.s.session.id,))
    before = originals_digest(p)
    operation = None
    try:
        with postgres_connect(p.s.h.settings) as holder, holder.cursor() as cur:
            cur.execute(f"SELECT id FROM {p.s.h.tasks._tasks} WHERE id=%s FOR UPDATE", (lease.task_id,))
            operation = asyncio.create_task(service.complete(lease, capabilities=writer_caps()))
            await wait_for_session_sql_lock(p.s, p.s.h.tasks._tasks)
            await asyncio.sleep(.8)
        with pytest.raises(ContinuationLeaseLostError):
            await operation
    finally:
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
    assert originals_digest(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{"receipt": kind} for kind in ["false_actual", "empty_checks"]], indirect=True)
async def test_recovery_preserves_failed_or_blocked_original_assessment(assessment_case):
    p = assessment_case
    await lost(p)
    service, raw, lease = await claimed(p)
    original = read_result_checkpoint(checkpoint(p)).assessment
    receipt = await service.complete(lease, capabilities=writer_caps())
    result = await p.s.d.runs.get_result(receipt.result_id)
    assert result.status == original.completion.status in {"failed", "blocked"}
    assert result.model_dump(mode="json", include=set(type(original.completion).model_fields)) == original.completion.model_dump(mode="json")
