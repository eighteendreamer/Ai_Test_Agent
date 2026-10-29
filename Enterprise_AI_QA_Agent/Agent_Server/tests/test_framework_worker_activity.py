"""Durable Worker dispatch boundaries, distinct from external operation stops."""
from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
from src.runtime.store import ContinuationLeaseLostError
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_calls import model_calls_harness, begin, policy


def activity_events(h):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {h.settings.database.postgres_event_table} "
            "WHERE session_id=%s AND payload->>'task_id'=%s ORDER BY event_sequence",
            (h.session_id, h.task.id))
        return [row for row in cur.fetchall() if row["type"].startswith("agent_task.")]


@pytest.mark.asyncio
async def test_worker_activity_excludes_resource_admission_and_survives_rebuild(model_calls_harness):
    h = model_calls_harness
    await h.tasks.defer(h.task, "fixture resource unavailable")
    await asyncio.sleep(0.04)
    h.tasks = PostgresAgentTaskStore(h.settings, h.store)
    h.task = await h.tasks.claim(h.task.id, worker_id="activity-worker", expected_binding=h.binding)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS now")
        before = cur.fetchone()["now"]
    await begin(h)
    await h.tasks.finish(h.task, status="completed", result_status="waiting_approval")
    events = activity_events(h)
    has_facts = all("framework_worker_activity" in event["payload"] for event in events)
    assert has_facts
    journals = await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id)
    first, second = journals.values()
    assert first.origin_observed and second.origin_observed
    assert [event.status for event in first.events] == ["claimed", "waiting_resource"]
    assert first.activity_intervals() == []
    assert [event.status for event in second.events] == ["claimed", "running", "completed"]
    assert second.events[-1].execution_returned
    assert second.events[-1].result_status == "waiting_approval"
    assert second.activity_intervals() == [(second.events[1].observed_at, second.events[2].observed_at)]
    assert second.events[1].observed_at >= before
    assert second.events[0].observed_at - first.events[-1].observed_at >= timedelta(seconds=0.04)
    assert [event["timestamp"] for event in events] == [
        point.observed_at for journal in journals.values() for point in journal.events]


@pytest.mark.asyncio
async def test_revoked_worker_authority_does_not_prove_dispatch_return(model_calls_harness):
    h = model_calls_harness
    await begin(h)
    assert await h.tasks.interrupt(h.session_id)
    has_facts = all("framework_worker_activity" in event["payload"] for event in activity_events(h))
    assert has_facts
    journal = (await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id))[h.task.attempt_id]
    assert [event.status for event in journal.events] == ["claimed", "running", "interrupted"]
    assert not journal.events[-1].execution_returned
    with pytest.raises(ValueError, match="explicit as_of"):
        journal.activity_intervals()
    end = journal.events[-1].observed_at + timedelta(seconds=10)
    assert journal.activity_intervals(as_of=end) == [(journal.events[1].observed_at, end)]


@pytest.mark.asyncio
async def test_expired_claim_has_no_execution_and_does_not_lose_its_final_boundary(model_calls_harness):
    h = model_calls_harness
    first_id = h.task.attempt_id
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set(metadata,"
            "'{turn_lease_expires_at}',to_jsonb(clock_timestamp()-interval '1 second')) WHERE id=%s", (h.session_id,))
    h.task = await h.tasks.claim(h.task.id, worker_id="replacement", expected_binding=h.binding)
    assert h.task.attempt_id != first_id
    first, second = (await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id)).values()
    assert [event.status for event in first.events] == ["claimed", "interrupted"]
    assert first.activity_intervals() == []
    assert [event.status for event in second.events] == ["claimed"]
    assert second.activity_intervals() == []


@pytest.mark.asyncio
async def test_failed_transition_and_heartbeat_do_not_append_activity(model_calls_harness):
    h = model_calls_harness
    results = await asyncio.gather(begin(h), begin(h), return_exceptions=True)
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    before = await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id)
    assert await h.tasks.renew_framework_execution(h.task, 60)
    assert await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id) == before
    await h.tasks.finish(h.task, status="failed", error_code="controlled_dispatch_error")
    after = await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id)
    with pytest.raises(ContinuationLeaseLostError):
        await h.tasks.finish(h.task, status="failed", error_code="duplicate")
    assert await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id) == after
    journal = after[h.task.attempt_id]
    assert [point.status for point in journal.events] == ["claimed", "running", "failed"]
    assert journal.events[-1].execution_returned
    assert journal.activity_intervals() == [(journal.events[1].observed_at, journal.events[2].observed_at)]


@pytest.mark.asyncio
async def test_activity_event_io_failure_rolls_back_task_and_attempt(model_calls_harness, monkeypatch):
    h = model_calls_harness
    before = await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id)
    append = h.store.append_event_in_transaction

    def fail(cur, session_id, event, **kwargs):
        if event.type == "agent_task.running":
            raise ConnectionError("controlled activity event I/O failure")
        return append(cur, session_id, event, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(h.store, "append_event_in_transaction", fail)
        with pytest.raises(ConnectionError, match="activity event I/O"):
            await begin(h)
    assert (await h.tasks.get(h.task.id)).status == "claimed"
    assert await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id) == before
    await begin(h)
    assert len(activity_events(h)) == 2


@pytest.mark.asyncio
async def test_missing_old_history_is_not_backfilled_from_attempt_timestamps(model_calls_harness):
    h = model_calls_harness
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload-'framework_worker_activity' "
            "WHERE session_id=%s", (h.session_id,))
    assert (await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id))[h.task.attempt_id] is None
    await begin(h)
    journal = (await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id))[h.task.attempt_id]
    assert not journal.origin_observed
    assert [point.status for point in journal.events] == ["running"]
    with pytest.raises(ValueError, match="explicit as_of"):
        journal.activity_intervals()
    with pytest.raises(KeyError):
        await h.tasks.get_framework_worker_activity(h.task.id, session_id=str(uuid4()))


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["binding_id", "attempt_id", "worker_id", "observed_at", "schema_version",
    "scope", "status", "execution_returned", "attempt_state", "task_state"])
async def test_activity_drift_is_visible_and_does_not_block_legal_task_execution(model_calls_harness, caplog, changed):
    h = model_calls_harness
    caplog.set_level(logging.WARNING)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed in {"attempt_state", "task_state"}:
            table = h.settings.database.postgres_agent_attempt_table if changed == "attempt_state" else h.settings.database.postgres_agent_task_table
            identity = h.task.attempt_id if changed == "attempt_state" else h.task.id
            cur.execute(f"UPDATE {table} SET status='failed' WHERE id=%s", (identity,))
        else:
            path = ["framework_worker_activity", "event", changed] if changed in {"observed_at", "status", "execution_returned"} else ["framework_worker_activity", changed]
            value = {"schema_version": 99, "status": "running", "execution_returned": True,
                "observed_at": "2000-01-01T00:00:00"}.get(changed, "foreign")
            cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=jsonb_set(payload,%s,%s::jsonb) "
                "WHERE session_id=%s AND type='agent_task.claimed'", (path, json.dumps(value), h.session_id))
    with pytest.raises(ValueError):
        await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id)
    assert any(record.message == "framework_worker_activity_read_rejected" for record in caplog.records)
    if changed not in {"attempt_state", "task_state"}:
        # Diagnostic corruption is not an execution gate; retain the raw event.
        before = activity_events(h)[0]
        await begin(h)
        assert activity_events(h)[0] == before


@pytest.mark.parametrize("changed", ["naive", "reverse", "false_return", "after_return"])
def test_worker_activity_schema_rejects_contradictory_boundaries(changed):
    from src.schemas.framework_worker_activity import FrameworkWorkerActivity

    now = datetime.now(timezone.utc)
    events = [dict(status="claimed", execution_returned=False, observed_at=now),
        dict(status="running", execution_returned=False, observed_at=now + timedelta(seconds=1))]
    if changed == "naive":
        events[0]["observed_at"] = now.replace(tzinfo=None)
    elif changed == "reverse":
        events[1]["observed_at"] = now - timedelta(seconds=1)
    elif changed == "false_return":
        events[0]["execution_returned"] = True
    else:
        events[0].update(status="completed", execution_returned=True)
    with pytest.raises(ValueError):
        FrameworkWorkerActivity(binding_id="binding", session_id="session", turn_id="turn", task_id="task",
            attempt_id="attempt", worker_id="worker", events=events)


def worker_paused_in_dispatch(settings_data, binding_data, prefix):
    """Actual Redis Worker/dispatcher/Session; Runtime is a controlled gate."""
    from redis.asyncio import Redis
    from src.core.config import Settings
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.redis_task_worker import RedisTaskWorker
    from src.runtime.resource_lease_manager import RedisResourceLeaseManager
    from src.schemas.runtime_binding import RuntimeBinding
    from tests.test_agent_task_dispatch import Runtime, build_services

    async def run():
        settings = Settings.model_validate(settings_data)
        binding = RuntimeBinding.model_validate(binding_data)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
        await resources.connect()
        runtime = Runtime()
        async def blocked_runtime(*args, **kwargs):
            await redis.incr(prefix + ":activity:calls")
            await redis.rpush(prefix + ":activity:entered", "runtime_entered")
            await asyncio.Event().wait()
        runtime.execute_turn = blocked_runtime
        _, _, _, dispatcher = build_services(settings, runtime, resources)
        dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities(binding.dependency_lock_sha256, True))
        queue = RedisTaskQueue(settings.database.redis_url, stream=settings.orchestration.agent_task_v2_stream,
            group="activity-worker", client=redis)
        await queue.connect()
        worker = RedisTaskWorker(queue, consumer="activity-worker", block_ms=100,
            handler=lambda delivery: dispatcher.handle_v2(delivery, worker_id="activity-worker"))
        await worker.run_once()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_independent_worker_kill_preserves_open_tail_after_owner_expiry(model_calls_harness):
    from src.infrastructure.redis_task_queue import RedisTaskQueue
    from tests.test_framework_task_control import wait_gate

    h = model_calls_harness
    await h.calls.freeze(h.owner, policy(h))
    await h.tasks.defer(h.task, "hand off to independent Worker")
    h.settings.orchestration.turn_execution_lease_seconds = 120
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="activity-worker", client=h.redis)
    await queue.connect()
    assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
    process = multiprocessing.get_context("spawn").Process(target=worker_paused_in_dispatch,
        args=(h.settings.model_dump(mode="json"), h.binding.model_dump(mode="json"), h.prefix))
    process.start()
    try:
        reached = await wait_gate(h.redis, h.prefix + ":activity:entered", 60, process=process)
        assert reached[1] == "runtime_entered"
        running = await h.tasks.get(h.task.id)
        assert running.status == "running" and running.worker_id == "activity-worker"
        before = await h.tasks.get_framework_worker_activity(h.task.id, session_id=h.session_id)
        process.terminate()
        await asyncio.to_thread(process.join, 15)
        assert not process.is_alive() and process.exitcode != 0
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set(metadata,"
                "'{turn_lease_expires_at}',to_jsonb(clock_timestamp()-interval '1 second')) WHERE id=%s", (h.session_id,))
        recovered = await h.tasks.claim(h.task.id, worker_id="replacement", expected_binding=h.binding)
        assert recovered.status == "recovery_required" and recovered.attempt_id == running.attempt_id
        restarted = PostgresAgentTaskStore(h.settings, h.store)
        journals = await restarted.get_framework_worker_activity(h.task.id, session_id=h.session_id)
        journal = journals[running.attempt_id]
        assert journal.events[:-1] == before[running.attempt_id].events
        assert [point.status for point in journal.events] == ["claimed", "running", "recovery_required"]
        assert not journal.events[-1].execution_returned
        end = journal.events[-1].observed_at + timedelta(seconds=30)
        assert journal.activity_intervals(as_of=end) == [(journal.events[1].observed_at, end)]
        with pytest.raises(ValueError, match="explicit as_of"):
            journal.activity_intervals()
        assert await h.redis.get(h.prefix + ":activity:calls") == "1"
    finally:
        if process.is_alive():
            process.terminate()
        await asyncio.to_thread(process.join, 15)
