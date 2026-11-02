"""Persist a settlement-only successor on the original task/outbox mechanism."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from threading import Event

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.framework_approval import approval_value_hash
from src.runtime.task_deferred import TaskDeferred
from src.schemas.runtime_binding import RuntimeExecutionOwner
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case, freeze
from tests.test_controlled_case_assessment_reconciliation import lost
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_projection_parts import checkpoint
from tests.test_controlled_case_settlement import implementation, complete
from tests.test_controlled_case_settlement_ack import originals_digest


def producer_facts(p):
    """Do not print leases; admission may add only its new task/outbox/audit."""
    h, runs = p.s.h, p.s.d.runs
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        state = {}
        for table, condition, value in (
            (runs._run_table, "id=%s", p.s.item.run_id),
            (runs._item_table, "id=%s", p.s.item.id),
            (runs._attempt_table, "id=%s", p.s.attempt.id),
            (h.tasks._tasks, "id=%s", p.h.task.id),
            (h.tasks._attempts, "task_id=%s", p.h.task.id),
            (h.tasks._tasks + "_runtime", "binding_id=%s", p.s.binding.binding_id),
            (h.settings.database.postgres_session_table, "id=%s", p.s.session.id),
            (h.settings.database.postgres_message_table, "session_id=%s", p.s.session.id),
            (h.settings.database.postgres_snapshot_table, "session_id=%s", p.s.session.id),
            (h.settings.database.postgres_event_table, "session_id=%s AND type<>'test_run.case_settlement_admitted'", p.s.session.id),
            (p.s.calls.calls_table, "binding_id=%s", p.s.binding.binding_id),
        ):
            # The original event writer advances these two audit-head columns;
            # its single increment is asserted separately, never normalized in
            # the original task/owner/RunItem or application proof.
            raw = "to_jsonb(t)-'event_count'-'updated_at'" if table == h.settings.database.postgres_session_table else "to_jsonb(t)"
            cur.execute(f"SELECT COALESCE(jsonb_agg({raw} ORDER BY ({raw})::text),'[]'::jsonb) AS rows FROM {table} t WHERE {condition}", (value,))
            state[table] = cur.fetchone()["rows"]
        return approval_value_hash(state)


def session_head(p):
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT event_count,updated_at FROM {p.s.h.settings.database.postgres_session_table} WHERE id=%s", (p.s.session.id,))
        return dict(cur.fetchone())


@pytest.mark.asyncio
async def test_expired_formal_case_admits_one_identity_only_settlement_successor_without_mutating_producer(assessment_case):
    p = assessment_case
    asset = await lost(p)
    before = producer_facts(p)
    head = session_head(p)
    ensure = implementation(p).ensure_recovery
    caps = FrameworkWorkerCapabilities("a" * 64, False, controlled_case_settlement_admission_available=True)
    task = await ensure(delivery(p.s), capabilities=caps)
    assert task.operation == "case_settle" and task.status == "queued" and task.attempt_no == 0
    assert task.parent_task_id == p.h.task.id and task.runtime_binding == p.s.binding
    assert task.reference_id == p.s.item.case_version_id and task.id != p.h.task.id
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {p.s.h.tasks._tasks} WHERE id=%s", (task.id,))
        row = cur.fetchone()
        proof = row["input_payload"]["case_settlement_admission"]
        assert set(row["input_payload"]) == {"case_settlement_admission"} and row["user_message_id"] is None
        assert proof["scope"] == "controlled_case_settlement_admission_only"
        assert proof["assessment_sha256"] == asset.asset_sha256
        assert proof["origin_task_id"] == p.h.task.id and proof["origin_attempt_id"] == p.h.task.attempt_id
        cur.execute(f"SELECT * FROM {p.s.h.settings.database.postgres_task_outbox_table} WHERE payload->>'task_id'=%s", (task.id,))
        outbox, = cur.fetchall()
        command = AgentTaskCommandV2.model_validate(outbox["payload"])
        assert command.operation == "case_settle" and command.parent_task_id == p.h.task.id
        assert command.case_settlement_reference.admission_sha256 == proof["asset_sha256"]
        assert command.case_execution_input is command.case_execution_reference is None and not command.payload
        cur.execute(f"SELECT payload FROM {p.s.h.settings.database.postgres_event_table} WHERE type='test_run.case_settlement_admitted'")
        event, = cur.fetchall()
        assert event["payload"]["admission_sha256"] == proof["asset_sha256"]
    admitted = session_head(p)
    # Existing Session audit heads use the application clock, whereas owner
    # transitions use PostgreSQL. Their wall-clock values need not be ordered.
    assert admitted["event_count"] == head["event_count"] + 1
    assert await ensure(delivery(p.s), capabilities=caps) == task
    assert session_head(p) == admitted
    assert producer_facts(p) == before and checkpoint(p).checkpoint_version == 5
    assert len(p.requests) == 2 and len(p.effects) == 1


def capabilities(**values):
    return FrameworkWorkerCapabilities("a" * 64, False, controlled_case_settlement_admission_available=True, **values)


async def ensure(p, caps=None):
    return await implementation(p).ensure_recovery(delivery(p.s), capabilities=caps or capabilities())


@pytest_asyncio.fixture
async def recovery_case(assessment_case):
    await lost(assessment_case)
    return assessment_case


def successor_row(p, task_id):
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {p.s.h.tasks._tasks} WHERE id=%s", (task_id,))
        return dict(cur.fetchone())


@pytest.mark.asyncio
async def test_admission_uses_no_execution_owner_or_result_writers(recovery_case, monkeypatch, caplog):
    p = recovery_case
    before = producer_facts(p)
    def forbidden(*args, **kwargs):
        raise AssertionError("settlement admission cannot acquire execution authority or replay effects")
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    for name in ("claim", "claim_controlled_case", "begin", "finish", "renew_framework_execution", "_update_status"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    for name in ("_write_item", "_write_attempt", "_complete_locked_item_in_transaction"):
        monkeypatch.setattr(type(p.s.d.runs), name, forbidden)
    caplog.set_level("INFO")
    task = await ensure(p)
    assert task.status == "queued" and task.attempt_id is None
    assert producer_facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1
    proof = successor_row(p, task.id)["input_payload"]
    assert p.h.task.lease_token not in str(proof) and p.s.item.lease_token not in str(proof)
    assert any(r.message == "controlled_case_settlement_admitted" and r.automatic_replay is False for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["writer_disabled", "old_reader", "dependency", "runtime", "checkpoint_reader"])
async def test_new_admission_requires_compatible_explicit_writer_without_mutation(recovery_case, changed, caplog):
    p = recovery_case
    changes = {"writer_disabled": {"controlled_case_settlement_admission_available": False},
        "old_reader": {"controlled_case_settlement_max_version": 0}, "dependency": {"dependency_lock_sha256": "b"*64},
        "runtime": {"runtime_version": "future-runtime"}, "checkpoint_reader": {"controlled_case_checkpoint_max_version": 4}}
    caps = replace(capabilities(), **changes[changed])
    before = originals_digest(p)
    with pytest.raises(TaskDeferred):
        await ensure(p, caps)
    assert originals_digest(p) == before
    assert any(r.message == "controlled_case_settlement_admission_rejected" and r.defer_reason for r in caplog.records)


@pytest.mark.asyncio
async def test_disabled_writer_can_read_only_the_existing_compatible_admission(recovery_case):
    p = recovery_case
    first = await ensure(p)
    before = originals_digest(p)
    assert await ensure(p, replace(capabilities(), controlled_case_settlement_admission_available=False)) == first
    with pytest.raises(TaskDeferred):
        await ensure(p, replace(capabilities(), controlled_case_settlement_max_version=0))
    assert originals_digest(p) == before


@pytest.mark.asyncio
async def test_concurrent_admission_commits_only_one_successor_outbox_and_audit(recovery_case):
    p = recovery_case
    head = session_head(p)
    tasks = await asyncio.gather(*(ensure(p) for _ in range(4)))
    assert tasks == [tasks[0]]*4
    assert session_head(p)["event_count"] == head["event_count"] + 1
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {p.s.h.tasks._tasks} WHERE operation='case_settle'")
        assert [r["id"] for r in cur.fetchall()] == [tasks[0].id]
        cur.execute(f"SELECT id FROM {p.s.h.settings.database.postgres_task_outbox_table} WHERE payload->>'task_id'=%s", (tasks[0].id,))
        assert len(cur.fetchall()) == 1
    before = originals_digest(p)
    assert await ensure(p) == tasks[0] and originals_digest(p) == before


@pytest.mark.asyncio
async def test_atomic_audit_failure_rolls_back_successor_outbox_and_session_head(recovery_case, monkeypatch):
    p = recovery_case
    original = p.s.h.store.append_event_in_transaction
    def fail(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2].type == "test_run.case_settlement_admitted":
            assert result
            raise RuntimeError("isolated failure after settlement admission audit before commit")
        return result
    before = originals_digest(p)
    with monkeypatch.context() as patch:
        patch.setattr(p.s.h.store, "append_event_in_transaction", fail)
        with pytest.raises(RuntimeError, match="isolated failure"):
            await ensure(p)
    assert originals_digest(p) == before
    assert (await ensure(p)).status == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["future_field", "future_version", "proof", "origin", "envelope", "outbox", "missing_outbox", "missing_audit"])
async def test_admission_reuse_rejects_changed_lineage_or_missing_atomic_facts_without_repair(recovery_case, changed):
    p = recovery_case
    task = await ensure(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if changed in {"future_field", "future_version", "proof"}:
            payload = deepcopy(successor_row(p, task.id)["input_payload"])
            proof = payload["case_settlement_admission"]
            if changed == "future_field":
                proof["future_field"] = True
            elif changed == "future_version":
                proof["schema_version"] = 99
            else:
                proof["assessment_sha256"] = "f"*64
            proof["asset_sha256"] = approval_value_hash({k: v for k, v in proof.items() if k != "asset_sha256"})
            import json
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_payload=%s::jsonb WHERE id=%s", (json.dumps(payload), task.id))
        elif changed == "origin":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET priority=priority+1 WHERE id=%s", (p.h.task.id,))
        elif changed == "envelope":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET trace_id='foreign' WHERE id=%s", (task.id,))
        elif changed == "outbox":
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_task_outbox_table} SET payload=jsonb_set(payload,'{{trace_id}}','\"foreign\"') WHERE payload->>'task_id'=%s", (task.id,))
        elif changed == "missing_outbox":
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_task_outbox_table} WHERE payload->>'task_id'=%s", (task.id,))
        else:
            cur.execute(f"DELETE FROM {p.s.h.settings.database.postgres_event_table} WHERE type='test_run.case_settlement_admitted'")
        assert cur.rowcount == 1
    before = originals_digest(p)
    with pytest.raises((RuntimeError, ValueError, TaskDeferred)):
        await ensure(p)
    assert originals_digest(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["running", "unprepared", "manual_reconciliation", "settled"])
async def test_only_original_expired_assessment_may_create_settlement_lineage(assessment_case, phase):
    p = assessment_case
    if phase != "unprepared":
        await freeze(p)
    if phase == "manual_reconciliation":
        await p.s.h.tasks.finish(p.h.task, status="recovery_required", error_code="explicit_reconciliation")
    elif phase == "settled":
        await complete(p)
    before = originals_digest(p)
    with pytest.raises((RuntimeError, ValueError)):
        await ensure(p)
    assert originals_digest(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["running", "waiting_approval", "cancelled", "live_session_lease", "cancelled_run"])
async def test_new_admission_requires_inactive_session_and_uncompleted_run(recovery_case, changed):
    p = recovery_case
    if changed == "cancelled_run":
        from datetime import datetime, timezone

        await p.s.d.runs.cancel_run(p.s.item.run_id, "isolated settlement admission cancellation", datetime.now(timezone.utc))
    else:
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            table = p.s.h.settings.database.postgres_session_table
            if changed == "live_session_lease":
                cur.execute(f"UPDATE {table} SET metadata=jsonb_set(metadata,'{{turn_lease_expires_at}}',"
                    "to_jsonb(clock_timestamp()+INTERVAL '1 minute')) WHERE id=%s", (p.s.session.id,))
            else:
                cur.execute(f"UPDATE {table} SET status=%s WHERE id=%s", (changed, p.s.session.id))
            assert cur.rowcount == 1
    before = originals_digest(p)
    with pytest.raises(RuntimeError):
        await ensure(p)
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_ordinary_claim_and_dispatcher_cannot_treat_admission_as_graph_execution(recovery_case):
    from src.infrastructure.redis_task_queue import QueuedTask

    p = recovery_case
    task = await ensure(p)
    before = originals_digest(p)
    for caps in (None, capabilities()):
        with pytest.raises(ValueError, match="scoped settlement claim"):
            await p.s.h.tasks.claim(task.id, worker_id="not-a-settlement-owner", expected_binding=p.s.binding, framework_capabilities=caps)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {p.s.h.settings.database.postgres_task_outbox_table} WHERE payload->>'task_id'=%s", (task.id,))
        raw = cur.fetchone()["payload"]
    p.s.h.dispatcher.set_framework_capabilities(capabilities())
    with pytest.raises(TaskDeferred):
        await p.s.h.dispatcher.handle_v2(QueuedTask("isolated-settlement-admission", raw), worker_id="ordinary-worker")
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_existing_real_redis_relay_transports_only_settlement_identity_reference(recovery_case):
    import json
    from src.infrastructure.redis_task_queue import RedisTaskQueue

    p = recovery_case
    task = await ensure(p)
    queue = RedisTaskQueue(p.s.h.settings.database.redis_url, stream=p.s.h.settings.orchestration.agent_task_v2_stream,
        group="isolated-settlement-admission", client=p.s.h.redis)
    await queue.connect()
    assert await p.s.h.outbox.relay_once(queue) == 2  # original execution + new settlement
    assert await ensure(p) == task and await p.s.h.outbox.relay_once(queue) == 0
    rows = await p.s.h.redis.xrange(queue.stream)
    wires = [json.loads(row[1]["payload"]) for row in rows]
    wire, = [r for r in wires if r["task_id"] == task.id]
    assert len(wires) == 2 and wire["operation"] == "case_settle"
    assert set(wire["case_settlement_reference"]) == {"schema_version", "origin_task_id", "origin_attempt_id", "run_item_execution_attempt_id", "admission_sha256"}
    assert "case_settlement_admission" not in wire and "case_execution_input" not in wire
    assert p.s.graph_request.system_prompt not in str(wire)
    assert p.h.task.lease_token not in str(wire) and p.s.item.lease_token not in str(wire)
    assert AgentTaskCommandV2.model_validate(wire).runtime_binding == p.s.binding


@pytest.mark.asyncio
async def test_cancelled_waiter_observes_unknown_commit_then_reuses_original_admission(recovery_case, monkeypatch, caplog):
    p = recovery_case
    entered, release, finished = Event(), Event(), Event()
    original = p.s.h.store.append_event_in_transaction
    def hold(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2].type == "test_run.case_settlement_admitted":
            entered.set()
            assert release.wait(10)
        return result
    import src.runtime.controlled_case_settlement_recovery as module
    material = module.ControlledCaseSettlementRecovery._ensure
    def observed(*args, **kwargs):
        try:
            return material(*args, **kwargs)
        finally:
            finished.set()
    before = producer_facts(p)
    with monkeypatch.context() as patch:
        patch.setattr(p.s.h.store, "append_event_in_transaction", hold)
        patch.setattr(module.ControlledCaseSettlementRecovery, "_ensure", observed)
        waiting = asyncio.create_task(ensure(p))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 10)
    task = await ensure(p)
    after = originals_digest(p)
    assert await ensure(p) == task and originals_digest(p) == after
    assert producer_facts(p) == before
    assert any(r.message == "controlled_case_settlement_admission_unknown" and r.recovery_reason == "admission_receipt_unknown" for r in caplog.records)
