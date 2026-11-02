"""Worker uncertainty across real scoped SQL, Redis ownership and durable ACK."""
import asyncio
from dataclasses import replace
from threading import Event

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.runtime_binding import RuntimeExecutionOwner
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_assessment import assessment_case
from tests.test_controlled_case_recovery_settlement import origin_identity, wire, writer_caps, expire
from tests.test_controlled_case_settlement_ack import originals_digest
from tests.test_controlled_case_settlement_dispatch import compose, queue_for, worker
from tests.test_controlled_case_settlement_recovery import recovery_case, ensure, successor_row


def join_sql(p):
    # A released thread gate is not a receipt. Join the actual Session row lock
    # before inspecting the result or allowing the fixture to remove its tables.
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '15s'")
        p.s.h.tasks._lock_session(cur, p.s.session.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["before_sql", "inside_sql", "after_commit"])
@pytest.mark.parametrize("fault", ["deadline", "owner", "heartbeat_reply"])
async def test_uncertain_delivery_never_transfers_or_replays_and_recovers_only_scoped_facts(
    recovery_case, monkeypatch, caplog, window, fault,
):
    p = recovery_case
    if window == "after_commit":
        # Keep this window before the next ordinary scoped-renew poll. Otherwise
        # that monitor correctly observes the terminal Task first, so it would
        # test monitor loss rather than the requested Redis/deadline fault.
        config = p.s.h.settings.orchestration
        monkeypatch.setattr(config, "agent_task_control_poll_seconds", 10)
        monkeypatch.setattr(config, "resource_default_lease_seconds", 60)
        monkeypatch.setattr(config, "turn_execution_lease_seconds", 60)
    task = await ensure(p)
    raw = wire(p, task.id)
    queue = await queue_for(p, raw)
    entries = await queue._client.xrange(queue.stream)
    message_id = entries[0][0]
    dispatcher, service = compose(p)
    protected = origin_identity(p)
    reached, interrupted = asyncio.Event(), asyncio.Event()
    sql_entered, sql_release, sql_left = Event(), Event(), Event()
    fault_requested = asyncio.Event()
    captured, forbidden_calls = [], []
    complete = service.complete
    append = p.s.h.store.append_event_in_transaction

    def paused_sql(*args, **kwargs):
        result = append(*args, **kwargs)
        if window == "inside_sql" and args[2].type == "test_run.case_recovery_settled":
            sql_entered.set()
            try:
                assert sql_release.wait(30), "Isolated SQL delivery fault gate timed out"
            finally:
                sql_left.set()
        return result

    async def gated_complete(lease, **kwargs):
        captured.append(lease)
        try:
            if window == "before_sql":
                reached.set()
                await asyncio.Event().wait()
            receipt = await complete(lease, **kwargs)
            if window == "after_commit":
                reached.set()
                await asyncio.Event().wait()
            return receipt
        except asyncio.CancelledError:
            interrupted.set()
            raise

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("Uncertain settlement must not transfer, adopt graph authority or replay")

    monkeypatch.setattr(service, "complete", gated_complete)
    monkeypatch.setattr(p.s.h.store, "append_event_in_transaction", paused_sql)
    monkeypatch.setattr(queue, "transfer", forbidden)
    monkeypatch.setattr(RuntimeExecutionOwner, "__init__", forbidden)
    for name in ("claim", "begin", "finish", "_update_status"):
        monkeypatch.setattr(p.s.h.tasks, name, forbidden)
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    touch = queue.touch
    lost_reply = []
    async def controlled_touch(delivery, **kwargs):
        result = await touch(delivery, **kwargs)
        if fault == "heartbeat_reply" and fault_requested.is_set() and not lost_reply:
            assert result
            lost_reply.append(True)
            raise RedisConnectionError("private-delivery-heartbeat-reply-probe")
        return result
    monkeypatch.setattr(queue, "touch", controlled_touch)
    async def handle(delivery):
        await dispatcher.handle_v2(delivery, worker_id="uncertain-worker")
    # This value is captured by asyncio.wait before any test gate is reached.
    # Use the real Worker deadline, not cancellation or a field changed in flight.
    current = RedisTaskWorker(queue, consumer="uncertain-worker", handler=handle,
        block_ms=0, reclaim_idle_ms=300, max_retries=0, task_timeout_seconds=5 if fault == "deadline" else 30)
    running = asyncio.create_task(current.run_once())
    try:
        if window == "inside_sql":
            assert await asyncio.to_thread(sql_entered.wait, 10)
        else:
            await asyncio.wait_for(reached.wait(), 10)
        if fault == "owner":
            await queue._client.xclaim(queue.stream, queue.group, "rival-worker", 0, [message_id], justid=True)
        fault_requested.set()
        await asyncio.wait_for(interrupted.wait(), 12)
        assert len(captured) == 1
        if window == "inside_sql":
            assert not sql_left.is_set()  # Cancelled waiter != stopped SQL.
        sql_release.set()
        assert await asyncio.wait_for(running, 20) == 0
        if sql_entered.is_set():
            assert await asyncio.to_thread(sql_left.wait, 10)
            await asyncio.to_thread(join_sql, p)
        pending = await queue.pending()
        assert len(pending) == 1 and pending[0]["message_id"] == message_id
        assert pending[0]["consumer"] == ("rival-worker" if fault == "owner" else "uncertain-worker")
        assert await queue._client.xrange(queue.stream) == entries
        assert await queue._client.xlen(queue.stream + ":dead_letter") == 0
        assert await p.s.h.resources.list_active() == []
        assert origin_identity(p) == protected
        if fault == "deadline":
            record, = [r for r in caplog.records if r.message == "redis_task_execution_deadline_unconfirmed"]
            assert record.recovery_reason == "execution_deadline_receipt_unknown" and record.delivery_message_id == message_id
        else:
            record, = [r for r in caplog.records if r.message == "redis_task_heartbeat_failed"]
            assert record.heartbeat_failure_reason == ("owner_changed" if fault == "owner" else "transport_unavailable")
            assert bool(lost_reply) == (fault == "heartbeat_reply")
        assert "private-" not in caplog.text
        if window == "before_sql":
            row = successor_row(p, task.id)
            assert row["status"] == "claimed" and row["attempt_no"] == 1
            assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
            expire(p)
            monkeypatch.setattr(service, "complete", complete)
            await asyncio.sleep(.35)
            assert await worker(p, dispatcher, queue, "new-scoped-attempt").run_once() == 1
            assert successor_row(p, task.id)["attempt_no"] == 2
            before = originals_digest(p)
            with pytest.raises(ContinuationLeaseLostError):
                await complete(captured[0], capabilities=writer_caps())
            assert originals_digest(p) == before
            # Redelivery is only a compatible receipt read, even with writer off.
            await queue.enqueue(raw)
        else:
            assert successor_row(p, task.id)["status"] == "completed"
        before = originals_digest(p)
        monkeypatch.setattr(p.s.h.resources, "acquire", forbidden)
        monkeypatch.setattr(p.s.h.tasks, "_claim_task_attempt", forbidden)
        monkeypatch.setattr(type(p.s.d.runs), "_complete_locked_item_in_transaction", forbidden)
        reader, _ = compose(p, caps=replace(writer_caps(), controlled_case_settlement_available=False))
        await asyncio.sleep(.35)
        assert await worker(p, reader, queue, "receipt-only-reader").run_once() == 1
        assert originals_digest(p) == before and origin_identity(p) == protected
        assert not await queue.pending() and await p.s.h.resources.list_active() == []
        with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {p.s.d.runs._result_table}")
            assert len(cur.fetchall()) == 1
            cur.execute(f"SELECT id FROM {p.s.h.settings.database.postgres_event_table} WHERE type='test_run.case_recovery_settled'")
            assert len(cur.fetchall()) == 1
        assert len(p.requests) == 2 and len(p.effects) == 1 and forbidden_calls == []
    finally:
        sql_release.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        if sql_entered.is_set():
            assert await asyncio.to_thread(sql_left.wait, 10)
            await asyncio.to_thread(join_sql, p)
