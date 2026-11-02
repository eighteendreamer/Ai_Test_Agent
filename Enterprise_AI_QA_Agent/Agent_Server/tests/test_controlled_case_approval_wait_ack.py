"""Historical atomic wait ACK, without current owner or continuation rights."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.controlled_case_approval_wait import ControlledCaseApprovalWaits
from src.schemas.framework_approval import approval_value_hash
from src.schemas.runtime_binding import RuntimeExecutionOwner
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_approval_wait import park, implementation, facts, clock
from tests.test_controlled_case_pending_approvals import pending_formal
from tests.test_controlled_case_execution_lease import delivery, capabilities
from tests.test_controlled_case_assessment import assessment_case
from tests.test_controlled_case_dispatch import compose, DISPATCH
from tests.test_controlled_case_settlement_dispatch import queue_for, worker


def reader_caps():
    return replace(capabilities(), controlled_case_available=False)


@pytest.mark.asyncio
async def test_parked_delivery_ack_is_read_only_and_needs_no_live_owner_or_writer(pending_formal, monkeypatch):
    p = pending_formal
    receipt = await park(p)
    before = facts(p)
    def forbidden(*args, **kwargs):
        pytest.fail("Wait ACK acquired authority, wrote originals or executed")
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    for name in ("claim_controlled_case", "begin", "finish", "_update_status", "_clear_lease"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    monkeypatch.setattr(p.bridge.projection, "project_many_in_transaction", forbidden)
    monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
    service = implementation(p)
    assert await service.acknowledge_delivery(delivery(p.s), capabilities=reader_caps()) == receipt
    assert await service.acknowledge_delivery(delivery(p.s), capabilities=reader_caps()) == receipt
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["audit", "claim", "returned", "task", "attempt", "batch", "link",
    "approval", "job", "native", "native_source", "admission", "budget", "ledger", "wire"])
async def test_wait_ack_rejects_changed_or_missing_originals_without_repair(pending_formal, drift, caplog):
    p = pending_formal
    receipt = await park(p)
    entry = receipt.batch[0]
    raw = delivery(p.s)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift in {"audit", "claim", "returned"}:
            kind = {"audit": "test_run.case_approval_waiting", "claim": "agent_task.claimed",
                "returned": "agent_task.completed"}[drift]
            cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=payload || "
                "'{\"fixture_drift\":true}'::jsonb WHERE type=%s AND payload->>'task_id'=%s", (kind, p.h.task.id))
        elif drift in {"task", "attempt"}:
            table = p.s.h.tasks._tasks if drift == "task" else p.s.h.tasks._attempts
            identity = p.h.task.id if drift == "task" else p.h.task.attempt_id
            cur.execute(f"UPDATE {table} SET error_code='fixture_drift' WHERE id=%s", (identity,))
        elif drift == "batch":
            value = receipt.model_dump(mode="json")
            value["batch"].pop()
            value["receipt_sha256"] = approval_value_hash({k: v for k, v in value.items() if k != "receipt_sha256"})
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s",
                (json.dumps({"controlled_case_approval_wait": value}), p.h.task.id))
        elif drift == "link":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_interrupt SET link=jsonb_set(link,'{{task_path}}','\"foreign\"') "
                "WHERE approval_id=%s", (entry.approval_id,))
        elif drift in {"approval", "job"}:
            table, identity = ((p.h.settings.database.postgres_approval_table, entry.approval_id) if drift == "approval"
                else (p.h.settings.database.postgres_tool_job_table, entry.tool_job_id))
            cur.execute(f"UPDATE {table} SET metadata=jsonb_set(metadata,'{{framework_binding_id}}','\"foreign\"') WHERE id=%s",
                (identity,))
        elif drift == "native":
            cur.execute(f"UPDATE {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes SET task_path='foreign' "
                "WHERE channel='__interrupt__' AND task_id=%s", (p.outcome.graph_outcome.native_interrupts[0].graph_task_id,))
        elif drift == "native_source":
            cur.execute(f"UPDATE {p.h.settings.deep_agents.checkpoint_schema}.checkpoints SET "
                "metadata=jsonb_set(metadata,'{source}','\"update\"') WHERE checkpoint_id=%s",
                (p.outcome.graph_outcome.native_interrupts[0].checkpoint_id,))
        elif drift == "admission":
            cur.execute(f"UPDATE {p.s.d.runs._attempt_table} SET record=jsonb_set(record,"
                "'{checkpoint_payload,submission_sha256}',%s::jsonb) WHERE id=%s", (json.dumps("0"*64), p.s.attempt.id))
        elif drift == "budget":
            cur.execute(f"UPDATE {p.s.calls.budgets_table} SET policy=jsonb_set(policy,'{{max_calls}}','999') WHERE binding_id=%s",
                (p.s.binding.binding_id,))
        elif drift == "ledger":
            cur.execute(f"UPDATE {p.s.calls.calls_table} SET record=jsonb_set(record,'{{outcome_code}}','\"foreign\"') WHERE binding_id=%s",
                (p.s.binding.binding_id,))
        else:
            raw["trace_id"] = "foreign"
    before = facts(p)
    with pytest.raises((RuntimeError, ValueError)):
        await implementation(p).acknowledge_delivery(raw, capabilities=reader_caps())
    rejected, = [r for r in caplog.records if r.message == "controlled_case_approval_wait_original_rejected"]
    assert rejected.task_id == receipt.task_id and rejected.original_stage and rejected.automatic_replay is False
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_old_wait_ack_does_not_require_current_session_owner_or_pending_native_root(pending_formal):
    """Isolated successors may change mutable state, not the parked originals."""
    p = pending_formal
    receipt = await park(p)
    from langgraph.checkpoint.base import WRITES_IDX_MAP
    from src.application.deep_agents.checkpoint_provider import checkpoint_serializer
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET owner_epoch=owner_epoch+1,execution_state='recovery_required' "
            "WHERE binding_id=%s", (p.s.binding.binding_id,))
        cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET status='completed',metadata=metadata-'pending_turn' WHERE id=%s",
            (p.s.session.id,))
        checkpoint_id = p.outcome.graph_outcome.native_interrupts[0].checkpoint_id
        # Official 3.1.2 checkpoint schema (postgres/base.py MIGRATIONS).
        # An isolated later root is deliberately NOT a valid pending source.
        cur.execute(f"INSERT INTO {p.h.settings.deep_agents.checkpoint_schema}.checkpoints "
            "(thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,type,checkpoint,metadata) "
            "SELECT thread_id,checkpoint_ns,%s,checkpoint_id,type,jsonb_set(checkpoint,'{id}',to_jsonb(%s::text)),"
            "jsonb_set(metadata,'{source}','\"update\"') FROM "
            f"{p.h.settings.deep_agents.checkpoint_schema}.checkpoints WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s",
            ("ffffffff-ffff-ffff-ffff-ffffffffffff", "ffffffff-ffff-ffff-ffff-ffffffffffff",
                p.s.binding.thread_id, p.s.binding.checkpoint_namespace, checkpoint_id))
        assert cur.rowcount == 1
        for native in p.outcome.graph_outcome.native_interrupts:
            # Retained historical interrupt can have a later durable resume;
            # this is NOT a decision or continuation authorization test.
            kind, blob = checkpoint_serializer().dumps_typed([{"type": "reject", "message": "fixture later resume"}])
            cur.execute(f"INSERT INTO {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
                "(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel,type,blob,task_path) "
                "VALUES (%s,%s,%s,%s,%s,'__resume__',%s,%s,%s)",
                (p.s.binding.thread_id, p.s.binding.checkpoint_namespace, native.checkpoint_id, native.graph_task_id,
                    WRITES_IDX_MAP['__resume__'], kind, blob, native.task_path))
    before = facts(p)
    assert await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=reader_caps()) == receipt
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_old_wait_ack_survives_real_run_cancel_and_retained_human_decision(pending_formal):
    from src.schemas.session import ToolApprovalStatus

    p = pending_formal
    receipt = await park(p)
    approval = next(a for a in await p.h.store.list_approvals(p.s.session.id) if a.id == receipt.batch[0].approval_id)
    approval.status, approval.resolved_at = ToolApprovalStatus.denied, clock(p)
    approval.decision_note = "fixture later human decision, not permission to resume"
    await p.h.store.save_approval(p.s.session.id, approval)
    cancelled = await p.s.d.runs.cancel_run(p.s.detail.run.id, "fixture later cancellation", clock(p))
    assert cancelled.run.status == "cancelled"
    assert next(i for i in cancelled.items if i.id == p.s.item.id).status == "cancelled"
    before = facts(p)
    assert await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=reader_caps()) == receipt
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_old_wait_ack_ignores_only_later_model_reservations(pending_formal):
    p = pending_formal
    receipt = await park(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {p.s.calls.calls_table} WHERE binding_id=%s", (p.s.binding.binding_id,))
        later = deepcopy(cur.fetchone()["record"])
        later["intent"]["attempt_id"] = "fixture-later-reservation"
        later["intent"]["logical_call_id"] = "fixture-later-call"
        later["created_at"] = clock(p).isoformat()
        cur.execute(f"INSERT INTO {p.s.calls.calls_table}(binding_id,attempt_id,record) VALUES (%s,%s,%s::jsonb)",
            (p.s.binding.binding_id, later["intent"]["attempt_id"], json.dumps(later)))
    before = facts(p)
    assert await implementation(p).acknowledge_delivery(delivery(p.s), capabilities=reader_caps()) == receipt
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{**DISPATCH, "approval": True}], indirect=True)
async def test_first_worker_parks_then_acks_and_duplicate_only_reads_originals(assessment_case, monkeypatch, caplog):
    p = assessment_case
    p.runtime._approval_waits = ControlledCaseApprovalWaits(p.s.h.tasks, projection=p.bridge.projection)
    dispatcher = compose(p)
    queue = await queue_for(p, delivery(p.s))
    assert await worker(p, dispatcher, queue).run_once() == 1, [(r.message, getattr(r, 'defer_reason', None),
        getattr(r, 'error_type', None)) for r in caplog.records]
    assert (await p.s.h.tasks.get(p.h.task.id)).result_status == "waiting_approval"
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 0
    assert await p.s.h.resources.list_active() == []
    before = facts(p)
    def forbidden(*args, **kwargs):
        pytest.fail("Duplicate wait executed, acquired or claimed")
    for name in ("claim_controlled_case", "begin", "finish"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    monkeypatch.setattr(p.executor, "execute", forbidden)
    monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    dispatcher.set_framework_capabilities(reader_caps())
    await queue.enqueue(delivery(p.s))
    assert await worker(p, dispatcher, queue, "wait-read-only").run_once() == 1
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{**DISPATCH, "approval": True}], indirect=True)
@pytest.mark.parametrize("fault", ["sql_receipt_loss", "redis_ack_loss", "cancel_sql_waiter"])
async def test_committed_wait_with_lost_reply_only_acks_on_redelivery(assessment_case, fault, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    p = assessment_case
    service = ControlledCaseApprovalWaits(p.s.h.tasks, projection=p.bridge.projection)
    p.runtime._approval_waits = service
    dispatcher = compose(p)
    queue = await queue_for(p, delivery(p.s))
    entered, release, finished = Event(), Event(), Event()
    cancelled_waiter = asyncio.Event()
    original_park, original_append, original_await = service._park, p.s.h.store.append_event_in_transaction, service.park
    def material(*args):
        try:
            value = original_park(*args)
            if fault == "sql_receipt_loss":
                raise RuntimeError("fixture committed wait reply lost")
            return value
        finally:
            finished.set()
    def append(*args, **kwargs):
        value = original_append(*args, **kwargs)
        if fault == "cancel_sql_waiter" and args[2].type == "test_run.case_approval_waiting":
            entered.set()
            assert release.wait(30)
        return value
    async def awaited(*args, **kwargs):
        try:
            return await original_await(*args, **kwargs)
        except asyncio.CancelledError:
            cancelled_waiter.set()
            raise
    async def lost_ack(*args, **kwargs):
        raise RedisConnectionError("fixture ACK reply lost before XACK")
    with monkeypatch.context() as patch:
        patch.setattr(service, '_park', material)
        patch.setattr(service, 'park', awaited)
        patch.setattr(p.s.h.store, 'append_event_in_transaction', append)
        if fault == "redis_ack_loss":
            patch.setattr(queue, 'ack_owned', lost_ack)
        running = asyncio.create_task(worker(p, dispatcher, queue).run_once())
        try:
            if fault == "cancel_sql_waiter":
                assert await asyncio.to_thread(entered.wait, 20)
                running.cancel()
                await asyncio.wait_for(cancelled_waiter.wait(), 10)
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await running
            elif fault == "redis_ack_loss":
                with pytest.raises(RedisConnectionError):
                    await running
            else:
                assert await running == 0
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            assert await asyncio.to_thread(finished.wait, 30)
    assert (await p.s.h.tasks.get(p.h.task.id)).result_status == "waiting_approval"
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    assert await p.s.h.resources.list_active() == []
    before = facts(p)
    def forbidden(*args, **kwargs):
        pytest.fail("Unconfirmed reply replayed execution instead of reading the committed wait")
    monkeypatch.setattr(service, 'park', forbidden)
    monkeypatch.setattr(p.executor, 'execute', forbidden)
    monkeypatch.setattr(p.s.h.tasks, 'claim_controlled_case', forbidden)
    dispatcher.set_framework_capabilities(reader_caps())
    await asyncio.sleep(.35)
    assert await worker(p, dispatcher, queue, "wait-reply-replacement").run_once() == 1
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 0
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{**DISPATCH, "approval": True}], indirect=True)
async def test_worker_without_wait_codec_does_not_claim_the_formal_input(assessment_case, monkeypatch):
    from tests.test_controlled_case_settlement_ack import originals_digest

    p = assessment_case
    p.runtime._approval_waits = ControlledCaseApprovalWaits(p.s.h.tasks, projection=p.bridge.projection)
    dispatcher = compose(p, caps=replace(capabilities(), controlled_case_approval_wait_max_version=0))
    def forbidden(*args, **kwargs):
        pytest.fail("Incompatible wait codec claimed or acquired")
    monkeypatch.setattr(p.s.h.tasks, 'claim_controlled_case', forbidden)
    monkeypatch.setattr(p.s.h.resources, 'acquire', forbidden)
    queue = await queue_for(p, delivery(p.s))
    before = originals_digest(p)
    assert await worker(p, dispatcher, queue).run_once() == 0
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    assert originals_digest(p) == before and p.requests == p.effects == []


@pytest.mark.asyncio
async def test_historical_native_wait_read_uses_callers_connection_without_authority_or_commit(pending_formal, monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver
    from src.application.deep_agents.native_approval_batch import require_historical_tool_interrupts_original
    from src.runtime.framework_approval_journal import FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired
    import src.infrastructure.postgres_runtime as pg

    p = pending_formal
    await park(p)
    before = facts(p)
    observed = []
    read = FrameworkApprovalJournal.pending_task_in_transaction
    def observe(slf, cur, *args):
        cur.execute("SELECT pg_backend_pid() AS pid")
        observed.append(cur.fetchone()["pid"])
        return read(slf, cur, *args)
    def forbidden(*args, **kwargs):
        pytest.fail("Historical read opened a second connection, deployed or acquired authority")
    class RollbackProbe(Exception):
        pass
    with pytest.raises(RollbackProbe):
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_backend_pid() AS pid,current_setting('search_path') AS path")
            identity = cur.fetchone()
            monkeypatch.setattr(pg, 'postgres_connect', forbidden)
            monkeypatch.setattr(PostgresSaver, 'setup', forbidden)
            monkeypatch.setattr(RuntimeExecutionOwner, '__init__', forbidden)
            monkeypatch.setattr(FrameworkApprovalJournal, 'pending_task_in_transaction', observe)
            def read_original():
                return require_historical_tool_interrupts_original(cur, p.h.settings, p.source,
                    p.s.asset.graph_input, p.outcome.graph_outcome.native_interrupts)
            read_original()
            assert observed == [identity["pid"]]*2
            native = p.outcome.graph_outcome.native_interrupts[0]
            cur.execute(f"UPDATE {p.h.settings.deep_agents.checkpoint_schema}.checkpoint_writes SET task_path='fixture foreign' "
                "WHERE task_id=%s AND channel='__interrupt__'", (native.graph_task_id,))
            assert cur.rowcount == 1
            with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconciliation"):
                read_original()
            cur.execute("SELECT pg_backend_pid() AS pid,current_setting('search_path') AS path")
            assert cur.fetchone() == identity
            raise RollbackProbe
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["codec", "version", "scope", "extra", "hash"])
async def test_incompatible_or_drifted_wait_receipt_remains_redis_pending(pending_formal, change, caplog):
    p = pending_formal
    receipt = await park(p)
    caps = replace(reader_caps(), controlled_case_approval_wait_max_version=0) if change == "codec" else reader_caps()
    if change != "codec":
        value = receipt.model_dump(mode="json")
        if change == "version":
            value["schema_version"] = 2
        elif change == "scope":
            value["scope"] = "controlled_case_atomic_settlement_only"
        elif change == "extra":
            value["future"] = True
        else:
            value["receipt_sha256"] = "0"*64
        with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s",
                (json.dumps({"controlled_case_approval_wait": value}), p.h.task.id))
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime
    from src.runtime.controlled_case_settlement import ControlledCaseSettlements
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    runtime = ControlledCaseRuntime(runtime_service=p.bridge.runtime, session_service=p.s.h.sessions,
        assessment_service=object(), settlement_service=ControlledCaseSettlements(p.s.h.tasks), approval_wait_service=implementation(p))
    dispatcher = AgentTaskDispatcher(p.s.h.settings, p.s.h.tasks, p.s.h.sessions, p.s.h.resources,
        framework_capabilities=caps, controlled_case_runtime=runtime)
    queue = await queue_for(p, delivery(p.s))
    before = facts(p)
    assert await worker(p, dispatcher, queue).run_once() == 0
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    if change != "hash":
        assert any(getattr(r, 'defer_reason', None) == 'framework_case_approval_wait_format_unavailable' for r in caplog.records)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []
