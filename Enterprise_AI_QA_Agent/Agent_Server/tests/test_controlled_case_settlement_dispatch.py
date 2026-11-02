"""Real v2 delivery uses scoped settlement, never the graph execution path."""
import asyncio
import ast
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.controlled_case_settlement_recovery import ControlledCaseSettlementRecovery
from src.runtime.redis_task_worker import RedisTaskWorker
from src.schemas.runtime_binding import RuntimeExecutionOwner
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case
from tests.test_controlled_case_settlement_recovery import recovery_case, ensure
from tests.test_controlled_case_recovery_settlement import origin_identity, wire, writer_caps
from tests.test_controlled_case_settlement_ack import originals_digest
from tests.test_controlled_case_settlement_recovery import successor_row
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.task_deferred import TaskDeferred
from redis.exceptions import ConnectionError as RedisConnectionError
from tests.test_controlled_case_recovery_settlement import expire
from src.runtime.store import ContinuationLeaseLostError
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


def compose(p, *, caps=None, port=True):
    service = ControlledCaseSettlementRecovery(p.s.h.tasks)
    dispatcher = AgentTaskDispatcher(p.s.h.settings, p.s.h.tasks, p.s.h.sessions, p.s.h.resources,
        framework_capabilities=caps or writer_caps(), controlled_case_settlements=service if port else None)
    return dispatcher, service


async def queue_for(p, raw):
    queue = RedisTaskQueue(p.s.h.settings.database.redis_url,
        stream=p.s.h.settings.orchestration.agent_task_v2_stream, group="scoped-settlement", client=p.s.h.redis)
    await queue.connect()
    await queue.enqueue(raw)
    return queue


def worker(p, dispatcher, queue, name="scoped-worker"):
    async def handle(task):
        await dispatcher.handle_v2(task, worker_id=name)
    return RedisTaskWorker(queue, consumer=name, handler=handle, block_ms=0,
        reclaim_idle_ms=300, task_timeout_seconds=30)


@pytest.mark.asyncio
async def test_existing_v2_worker_commits_scoped_result_before_ack_and_duplicate_is_read_only(recovery_case, monkeypatch, caplog):
    p = recovery_case
    task = await ensure(p)
    dispatcher, service = compose(p)
    original = origin_identity(p)
    def forbidden(*args, **kwargs):
        raise AssertionError("settlement dispatcher cannot acquire graph authority or execute pipeline")
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    for name in ("claim", "begin", "finish", "_update_status", "ensure_framework_message_recovery", "ensure_framework_reconciliation"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    raw = wire(p, task.id)
    queue = await queue_for(p, raw)
    assert await worker(p, dispatcher, queue).run_once() == 1, [(r.message, getattr(r, "error_type", None),
        getattr(r, "defer_reason", None)) for r in caplog.records if r.message.startswith("agent_case_")]
    receipt = await service.acknowledge(raw, capabilities=writer_caps())
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id == receipt.result_id
    assert origin_identity(p) == original and len(p.requests) == 2 and len(p.effects) == 1
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 0
    assert await p.s.h.resources.list_active() == []
    before = originals_digest(p)
    await queue.enqueue(raw)
    assert await worker(p, dispatcher, queue, "duplicate-worker").run_once() == 1
    assert originals_digest(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["queued", "waiting_resource", "claimed"])
async def test_real_interrupt_only_revokes_scoped_successor_and_duplicate_control_is_read_only(recovery_case, state):
    p = recovery_case
    task = await ensure(p)
    dispatcher, service = compose(p)
    raw = wire(p, task.id)
    if state != "queued":
        lease = await service.claim(raw, worker_id="controlled-cancel-worker", capabilities=writer_caps())
        if state == "waiting_resource":
            await service.defer(lease, reason="isolated_resource_wait", capabilities=writer_caps())
    original = origin_identity(p)
    assert await p.s.h.tasks.interrupt(p.s.session.id, reason="isolated user stop", source="test")
    assert origin_identity(p) == original
    current = successor_row(p, task.id)
    assert current["status"] == ("interrupted" if state == "claimed" else "cancelled")
    assert current["interrupt_requested"] and current["result_payload"] is None
    session = await p.s.h.store.get_session(p.s.session.id)
    assert "turn_lease_token" not in session.metadata
    assert session.metadata["control"]["is_resumable"] is False
    before = originals_digest(p)
    assert await p.s.h.tasks.interrupt(p.s.session.id, reason="repeat stop", source="test")
    assert originals_digest(p) == before
    with pytest.raises((RuntimeError, TaskDeferred)):
        await service.claim(raw, worker_id="must-not-restart-cancelled", capabilities=writer_caps())
    queue = await queue_for(p, raw)
    assert await worker(p, dispatcher, queue).run_once() == 0
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    assert originals_digest(p) == before
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_resource_shortage_releases_scoped_turn_then_reclaims_new_attempt_without_resetting_original(recovery_case):
    p = recovery_case
    task = await ensure(p)
    dispatcher, service = compose(p)
    original = origin_identity(p)
    raw = wire(p, task.id)
    queue = await queue_for(p, raw)
    occupied = await p.s.h.resources.acquire(resource_type="agent", resource_id="isolated-occupied-slot",
        worker_id="isolated-occupant", lease_seconds=120)
    try:
        assert await worker(p, dispatcher, queue).run_once() == 0
        current = successor_row(p, task.id)
        assert current["status"] == "waiting_resource" and current["attempt_no"] == 1
        assert current["waiting_reason"] and current["result_payload"] is None
        session = await p.s.h.store.get_session(p.s.session.id)
        assert "turn_lease_token" not in session.metadata
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {p.s.h.tasks._attempts} WHERE task_id=%s", (task.id,))
            attempt, = cur.fetchall()
            assert attempt["status"] == "waiting_resource" and attempt["finished_at"] is not None
    finally:
        await p.s.h.resources.retire(occupied)
    await asyncio.sleep(.35)
    assert await worker(p, dispatcher, queue, "replacement-worker").run_once() == 1
    assert successor_row(p, task.id)["attempt_no"] == 2
    assert origin_identity(p) == original and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["writer", "port", "capabilities", "receipt_reader", "admission_reader", "dependency", "runtime"])
async def test_unavailable_scoped_delivery_stays_pending_without_claim_or_transfer(recovery_case, change, monkeypatch):
    p = recovery_case
    task = await ensure(p)
    changes = {"writer": {"controlled_case_settlement_available": False},
        "receipt_reader": {"controlled_case_recovery_receipt_max_version": 0},
        "admission_reader": {"controlled_case_settlement_max_version": 0},
        "dependency": {"dependency_lock_sha256": "b"*64}, "runtime": {"runtime_version": "future"}}
    caps = replace(writer_caps(), **changes.get(change, {}))
    dispatcher, _ = compose(p, caps=caps, port=change != "port")
    if change == "capabilities":
        dispatcher.set_framework_capabilities(None)
    forbidden_calls = []
    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("unavailable scope must not claim, transfer or allocate")
    monkeypatch.setattr(p.s.h.tasks, "_claim_task_attempt", forbidden)
    monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
    queue = await queue_for(p, wire(p, task.id))
    monkeypatch.setattr(queue, "transfer", forbidden)
    before = originals_digest(p)
    assert await worker(p, dispatcher, queue).run_once() == 0
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    assert originals_digest(p) == before
    assert forbidden_calls == []


@pytest.mark.asyncio
async def test_monitor_renews_real_resource_and_scoped_session_without_graph_control(recovery_case, monkeypatch):
    p = recovery_case
    task = await ensure(p)
    dispatcher, service = compose(p)
    renewed = asyncio.Event()
    counts = {"resource": 0, "session": 0}
    graph_controls = []
    resource_renew, session_renew, complete = p.s.h.resources.renew, service.renew, service.complete
    async def renew_resource(*args, **kwargs):
        result = await resource_renew(*args, **kwargs)
        counts["resource"] += int(result)
        return result
    async def renew_session(*args, **kwargs):
        result = await session_renew(*args, **kwargs)
        counts["session"] += int(result)
        if counts["session"] >= 3:
            renewed.set()
        return result
    async def wait_for_monitor(*args, **kwargs):
        await asyncio.wait_for(renewed.wait(), 10)
        return await complete(*args, **kwargs)
    def forbidden(*args, **kwargs):
        graph_controls.append(True)
        raise AssertionError("settlement monitor cannot request graph interruption")
    monkeypatch.setattr(p.s.h.resources, "renew", renew_resource)
    monkeypatch.setattr(service, "renew", renew_session)
    monkeypatch.setattr(service, "complete", wait_for_monitor)
    monkeypatch.setattr(p.s.h.sessions._runtime_service, "request_interrupt", forbidden)
    queue = await queue_for(p, wire(p, task.id))
    assert await worker(p, dispatcher, queue).run_once() == 1
    assert counts["resource"] >= 3 and counts["session"] >= 3
    assert graph_controls == []
    assert await p.s.h.resources.list_active() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["resource", "session", "cancel"])
async def test_monitor_loss_cancels_unstarted_commit_without_failing_original_or_ack(recovery_case, loss, monkeypatch):
    p = recovery_case
    task = await ensure(p)
    dispatcher, service = compose(p)
    entered, stopped = asyncio.Event(), asyncio.Event()
    leases = []
    forbidden_calls = []
    acquire = p.s.h.resources.acquire
    async def capture_resource(*args, **kwargs):
        lease = await acquire(*args, **kwargs)
        leases.append(lease)
        return lease
    async def unstarted_complete(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("monitor uncertainty cannot run graph or transfer")
    monkeypatch.setattr(p.s.h.resources, "acquire", capture_resource)
    monkeypatch.setattr(service, "complete", unstarted_complete)
    monkeypatch.setattr(p.s.h.sessions._runtime_service, "request_interrupt", forbidden)
    queue = await queue_for(p, wire(p, task.id))
    monkeypatch.setattr(queue, "transfer", forbidden)
    original = origin_identity(p)
    running = asyncio.create_task(worker(p, dispatcher, queue).run_once())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if loss == "resource":
            await p.s.h.resources.retire(leases[0])
        elif loss == "session":
            expire(p)
        else:
            assert await p.s.h.tasks.interrupt(p.s.session.id, reason="monitor cancel", source="test")
        assert await asyncio.wait_for(running, 15) == 0
        assert stopped.is_set()
        current = successor_row(p, task.id)
        assert current["status"] == ("interrupted" if loss == "cancel" else "claimed")
        assert current["result_payload"] is None and origin_identity(p) == original
        assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert await p.s.h.resources.list_active() == []
        assert forbidden_calls == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["reply", "ack_owner", "ack_transport"])
async def test_committed_receipt_survives_reply_or_ack_loss_and_writer_disabled_worker_only_reads(recovery_case, fault, monkeypatch):
    p = recovery_case
    task = await ensure(p)
    dispatcher, service = compose(p)
    complete = service.complete
    async def lost_reply(*args, **kwargs):
        await complete(*args, **kwargs)
        raise RedisConnectionError("isolated settlement reply lost")
    if fault == "reply":
        monkeypatch.setattr(service, "complete", lost_reply)
    queue = await queue_for(p, wire(p, task.id))
    ack = queue.ack_owned
    async def lost_ack(*args, **kwargs):
        if fault == "ack_transport":
            raise RedisConnectionError("isolated ack transport lost")
        return 0
    if fault != "reply":
        monkeypatch.setattr(queue, "ack_owned", lost_ack)
    def forbidden(*args, **kwargs):
        raise AssertionError("durable ACK must not write, allocate, transfer or re-execute")
    monkeypatch.setattr(queue, "transfer", forbidden)
    if fault == "ack_transport":
        with pytest.raises(RedisConnectionError):
            await worker(p, dispatcher, queue).run_once()
    else:
        assert await worker(p, dispatcher, queue).run_once() == 0
    assert successor_row(p, task.id)["status"] == "completed"
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
    assert await p.s.h.resources.list_active() == []
    before = originals_digest(p)
    monkeypatch.setattr(queue, "ack_owned", ack)
    monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
    monkeypatch.setattr(p.s.h.tasks, "_claim_task_attempt", forbidden)
    monkeypatch.setattr(type(p.s.d.runs), "_complete_locked_item_in_transaction", forbidden)
    reader, _ = compose(p, caps=writer_caps(controlled_case_available=False))
    reader.set_framework_capabilities(replace(writer_caps(), controlled_case_settlement_available=False))
    await asyncio.sleep(.35)
    assert await worker(p, reader, queue, "read-only-recovery").run_once() == 1
    assert originals_digest(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_cancelled_worker_waiter_does_not_prove_sql_thread_stopped_and_next_worker_only_acks(recovery_case, monkeypatch, caplog):
    p = recovery_case
    task = await ensure(p)
    dispatcher, _ = compose(p)
    entered, release, finished = Event(), Event(), Event()
    append = p.s.h.store.append_event_in_transaction
    def paused_transaction(*args, **kwargs):
        result = append(*args, **kwargs)
        if args[2].type == "test_run.case_recovery_settled":
            entered.set()
            try:
                assert release.wait(20), "Isolated SQL commit gate timed out"
            finally:
                finished.set()
        return result
    monkeypatch.setattr(p.s.h.store, "append_event_in_transaction", paused_transaction)
    queue = await queue_for(p, wire(p, task.id))
    original = origin_identity(p)
    running = asyncio.create_task(worker(p, dispatcher, queue).run_once())
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        running.cancel()
        await asyncio.sleep(0)
        assert not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 15)
        assert await asyncio.to_thread(finished.wait, 5)
        # Join the actual SQL transaction; a thread gate is not a commit receipt.
        def join_transaction():
            with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
                p.s.h.tasks._lock_session(cur, p.s.session.id)
        await asyncio.to_thread(join_transaction)
        assert successor_row(p, task.id)["status"] == "completed"
        assert origin_identity(p) == original
        assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1
        assert any(r.message == "agent_case_settlement_receipt_unknown" for r in caplog.records)
        before = originals_digest(p)
        reader, _ = compose(p, caps=replace(writer_caps(), controlled_case_settlement_available=False))
        await asyncio.sleep(.35)
        assert await worker(p, reader, queue, "after-cancel-reader").run_once() == 1
        assert originals_digest(p) == before
        assert len(p.requests) == 2 and len(p.effects) == 1
    finally:
        release.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        if entered.is_set():
            assert await asyncio.to_thread(finished.wait, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["empty_reason", "foreign_lease", "cancel", "origin"])
async def test_scoped_resource_deferral_rejects_stale_or_changed_facts_without_mutation(recovery_case, change):
    p = recovery_case
    task = await ensure(p)
    _, service = compose(p)
    lease = await service.claim(wire(p, task.id), worker_id="deferral-worker", capabilities=writer_caps())
    reason = "isolated_resource_wait"
    if change == "empty_reason":
        reason = ""
    elif change == "foreign_lease":
        lease = lease.model_copy(update={"lease_token": "foreign-isolated-lease"})
    elif change == "cancel":
        assert await p.s.h.tasks.interrupt(p.s.session.id, reason="deferral cancelled", source="test")
    else:
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET error_code='isolated-drift' WHERE id=%s", (p.h.task.id,))
    before = originals_digest(p)
    with pytest.raises((RuntimeError, ContinuationLeaseLostError)):
        await service.defer(lease, reason=reason, capabilities=writer_caps())
    assert originals_digest(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["origin", "outbox"])
async def test_changed_source_defers_real_worker_without_auto_repair_or_delivery_transfer(recovery_case, change, monkeypatch):
    p = recovery_case
    task = await ensure(p)
    raw = wire(p, task.id)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if change == "origin":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET error_code='isolated-drift' WHERE id=%s", (p.h.task.id,))
        else:
            cur.execute(f"UPDATE {p.s.h.settings.database.postgres_task_outbox_table} "
                "SET payload=jsonb_set(payload,'{priority}','99') WHERE event_key=%s", ("agent_turn:" + task.id,))
    dispatcher, _ = compose(p)
    queue = await queue_for(p, raw)
    def forbidden(*args, **kwargs):
        raise AssertionError("source drift must not auto-transfer delivery")
    monkeypatch.setattr(queue, "transfer", forbidden)
    before = originals_digest(p)
    assert await worker(p, dispatcher, queue).run_once() == 0
    assert originals_digest(p) == before
    assert (await p.s.h.redis.xpending(queue.stream, queue.group))["pending"] == 1


def test_main_installs_only_explicit_scoped_port_and_does_not_enable_writer():
    """Composition audit, not a full application lifespan or E4 release test."""
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    source = Path(__file__).resolve().parents[1] / "src" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "AgentTaskDispatcher"]
    assert len(calls) == 1
    keywords = {item.arg: ast.unparse(item.value) for item in calls[0].keywords}
    assert keywords["controlled_case_settlements"] == "ControlledCaseSettlementRecovery(agent_task_store)"
    assert FrameworkWorkerCapabilities("a"*64, True).controlled_case_settlement_available is False


@pytest.mark.asyncio
async def test_new_queued_turn_after_scoped_cancellation_does_not_revoke_old_producer_on_next_interrupt(recovery_case):
    p = recovery_case
    await ensure(p)
    assert await p.s.h.tasks.interrupt(p.s.session.id, reason="stop settlement", source="test")
    original = origin_identity(p)
    h = SimpleNamespace(session_id=p.s.session.id)
    binding = make_binding(h.session_id, "after-scoped-cancel-" + uuid4().hex)
    command = make_command(h, binding).model_copy(update={"project_id": p.s.session.project_id})
    future = await p.s.h.tasks.submit_v2(command,
        make_payload().model_copy(update={"submit_mode": "queued"}), make_message(h, binding),
        idempotency_key="after-scoped-cancel")
    assert future.status == "queued" and origin_identity(p) == original
    assert await p.s.h.tasks.interrupt(p.s.session.id, reason="stop new turn", source="test")
    assert origin_identity(p) == original
    assert (await p.s.h.tasks.get(future.id)).status == "cancelled"
    before = originals_digest(p)
    assert await p.s.h.tasks.interrupt(p.s.session.id, reason="repeat new stop", source="test")
    assert originals_digest(p) == before
