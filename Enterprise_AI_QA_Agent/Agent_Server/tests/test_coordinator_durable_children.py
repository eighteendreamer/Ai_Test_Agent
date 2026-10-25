"""Durable coordinator children: submission, worker execution, parent resume.

The behavior only exists behind orchestration.coordinator_durable_children, so
every case here needs the real PostgreSQL task/Outbox tables and the Redis agent
stream to prove the parent/child facts survive a process boundary.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from src.application.orchestration.coordinator_runtime_service import (
    CoordinatorRuntimeService,
    WorkerDispatchSpec,
)
from src.core.config import get_settings
from src.domain.models import SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.registry.agents import AgentRegistry
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.session import (
    RuntimeMode,
    SendMessageRequest,
    SessionMode,
    SessionStatus,
)
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_agent_task_dispatch import Runtime, build_services
from tests.test_live_postgres_concurrency import _drop_tables


class RecordingCoordinator:
    """Stands in for CoordinatorRuntimeService and records child completions."""

    def __init__(self):
        self.finished = []

    async def on_child_task_finished(self, *, parent_task_id, child_task_id, child_session_id, notification):
        self.finished.append({
            "parent_task_id": parent_task_id,
            "child_task_id": child_task_id,
            "child_session_id": child_session_id,
            "notification": dict(notification),
        })


class RaisingRuntime(Runtime):
    async def execute_turn(self, session, request, **kwargs):
        self.calls.append(request.turn_id)
        raise RuntimeError("injected child execution failure")


async def make_session(store, title, **kwargs) -> str:
    session_id = str(uuid4())
    await store.save_session(SessionRecord(
        id=session_id,
        title=title,
        status=SessionStatus.idle,
        session_mode=kwargs.get("session_mode", SessionMode.background_task),
        runtime_mode=kwargs.get("runtime_mode", RuntimeMode.background),
        mode_key="default",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        metadata=kwargs.get("metadata", {}),
    ))
    return session_id


def worker(task_id, **kwargs) -> WorkerDispatchSpec:
    return WorkerDispatchSpec(
        task_id=task_id,
        description=kwargs.get("description", "Plan the regression suite"),
        prompt=kwargs.get("prompt", "produce the case plan"),
        agent_key=kwargs.get("agent_key", "qa-planner"),
        model_key=kwargs.get("model_key"),
        skill_keys=kwargs.get("skill_keys", []),
        context=kwargs.get("context", {}),
    )


async def submit_child(h, parent_task_id, spec, child_session_id, *, root_trace_id="root-trace-1", turn_id=None):
    return await h.dispatcher.submit_coordinator_child(
        parent_task_id=parent_task_id,
        root_trace_id=root_trace_id,
        child_session_id=child_session_id,
        turn_id=turn_id or str(uuid4()),
        project_id=None,
        request_id=f"req-{spec.task_id}",
        trace_id=f"trace-{spec.task_id}",
        run_id=None,
        worker=spec,
        mode_key="default",
        parent_session_id=h.parent_id,
        parent_turn_id=h.parent_turn_id,
    )


async def outbox_command(h, task_id, prefix):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT payload FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"{prefix}:{task_id}",),
        )
        row = cur.fetchone()
    assert row is not None, f"missing outbox command {prefix}:{task_id}"
    return QueuedTask("durable-delivery", row["payload"])


async def execute_command(h, task_id, prefix, *, worker_id="durable-worker"):
    """Execute one durable command exactly as a Worker would, then reload the row."""
    await h.dispatcher.handle(await outbox_command(h, task_id, prefix), worker_id=worker_id)
    return await h.tasks.get(task_id)


async def resume_parent(h, *, child_task_id, attempt_id="na", content="<task-notification/>"):
    return await h.dispatcher.submit_coordinator_resume(
        parent_session_id=h.parent_id,
        parent_turn_id=h.parent_turn_id,
        project_id=None,
        request_id=f"req-resume-{child_task_id}",
        trace_id=f"trace-resume-{child_task_id}",
        run_id=None,
        run_item_id=None,
        parent_task_id=h.parent_task_id,
        notification_task_id=f"{child_task_id}:{attempt_id}",
        content=content,
        context={"message_source": "task_notification"},
        metadata={"message_kind": "task_notification", "task_id": child_task_id},
        agent_key="coordinator",
    )


def build_coordinator(h) -> CoordinatorRuntimeService:
    coordinator = CoordinatorRuntimeService(
        settings=h.settings,
        store=h.store,
        session_service=h.sessions,
        agent_registry=AgentRegistry(),
    )
    coordinator.set_agent_task_store(h.tasks)
    coordinator.set_agent_task_dispatcher(h.dispatcher)
    return coordinator


@pytest_asyncio.fixture
async def harness():
    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires real PostgreSQL and Redis")
    settings = get_settings().model_copy(deep=True)
    suffix = uuid4().hex[:12]
    fields = ("session", "message", "event", "snapshot", "approval", "task_outbox", "agent_task", "agent_attempt")
    tables = [f"durable_test_{field}_{suffix}" for field in fields]
    for field, table in zip(fields, tables):
        setattr(settings.database, f"postgres_{field}_table", table)
    tables.append(f"{settings.database.postgres_agent_task_table}_runtime")
    prefix = f"durable_test_{suffix}"
    settings.orchestration.agent_task_stream = f"{prefix}:tasks:agent"
    settings.orchestration.agent_task_control_poll_seconds = .05
    settings.orchestration.coordinator_durable_children = True
    settings.orchestration.coordinator_child_timeout_seconds = 1
    settings.orchestration.coordinator_max_workers = 4
    redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
    resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
    await resources.connect()
    await resources.configure_quota(scope="resource_type", identifier="agent", limit=1)
    runtime = Runtime()
    store, tasks, sessions, dispatcher = build_services(settings, runtime, resources)
    coordinator = RecordingCoordinator()
    dispatcher.set_coordinator_service(coordinator)
    outbox = PostgresTaskOutbox(settings)
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=settings.orchestration.agent_task_stream,
        group="durable-test",
        client=redis,
    )
    try:
        await store.initialize()
        await outbox.initialize()
        await tasks.initialize()
        await queue.connect()
        parent_id = await make_session(
            store,
            "Coordinator parent",
            session_mode=SessionMode.coordinator,
            runtime_mode=RuntimeMode.interactive,
        )
        receipt = await dispatcher.submit(
            parent_id,
            SendMessageRequest(content="start the campaign", metadata={"client_message_id": "parent-input-1"}),
        )
        yield SimpleNamespace(
            settings=settings,
            prefix=prefix,
            redis=redis,
            resources=resources,
            runtime=runtime,
            store=store,
            tasks=tasks,
            sessions=sessions,
            dispatcher=dispatcher,
            coordinator=coordinator,
            outbox=outbox,
            queue=queue,
            parent_id=parent_id,
            parent_task_id=receipt.task.id,
            parent_turn_id=receipt.task.turn_id,
        )
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        _drop_tables(settings, list(reversed(tables)))


@pytest.mark.asyncio
async def test_coordinator_child_submission_is_durable_and_idempotent(harness):
    h = harness
    child_session_id = await make_session(h.store, "Durable child")
    spec = worker("worker-durable-1")

    first = await submit_child(h, h.parent_task_id, spec, child_session_id)
    second = await submit_child(h, h.parent_task_id, spec, child_session_id)

    assert first.id == second.id
    assert first.operation == "coordinator_child"
    assert first.session_id == child_session_id
    assert first.parent_task_id == h.parent_task_id
    assert first.root_trace_id == "root-trace-1"
    assert first.status == "queued"
    children = await h.tasks.list_children(h.parent_task_id)
    assert [item.id for item in children] == [first.id]
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT event_key FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"coordinator_child:{first.id}",),
        )
        assert cur.fetchone() is not None
        cur.execute(
            f"SELECT idempotency_key FROM {h.settings.database.postgres_agent_task_table} WHERE id=%s",
            (first.id,),
        )
        assert cur.fetchone()["idempotency_key"] == "coordinator_child:worker-durable-1"
    assert h.runtime.calls == []
    assert h.coordinator.finished == []

    completed = await execute_command(h, first.id, "coordinator_child")
    assert completed.status == "completed"
    assert completed.attempt_id
    assert completed.fencing_token is not None
    assert h.runtime.calls == [first.turn_id]
    assert [m.content for m in (await h.store.get_session(child_session_id)).messages] == [
        spec.prompt,
        "worker-result",
    ]

    notification = completed.result_payload
    assert notification["status"] == "completed"
    assert notification["worker_task_id"] == "worker-durable-1"
    assert notification["worker_agent_key"] == spec.agent_key
    assert notification["child_session_id"] == child_session_id
    assert notification["child_task_id"] == first.id
    assert notification["parent_task_id"] == h.parent_task_id
    assert notification["root_trace_id"] == "root-trace-1"
    assert notification["attempt_id"] == completed.attempt_id
    assert notification["result_text"] == "worker-result"
    assert notification["usage"]["total_messages"] >= 1

    settled = h.coordinator.finished
    assert len(settled) == 1
    assert settled[0]["child_task_id"] == first.id
    assert settled[0]["parent_task_id"] == h.parent_task_id
    assert settled[0]["child_session_id"] == child_session_id
    assert settled[0]["notification"]["status"] == "completed"

    resume = await resume_parent(h, child_task_id=first.id, attempt_id=completed.attempt_id)
    again = await resume_parent(h, child_task_id=first.id, attempt_id=completed.attempt_id)
    assert resume.id == again.id
    assert resume.operation == "coordinator_resume"
    assert resume.session_id == h.parent_id
    assert resume.turn_id == h.parent_turn_id
    assert resume.parent_task_id == h.parent_task_id
    parent_record = await h.tasks.get(h.parent_task_id)
    assert resume.root_trace_id == parent_record.trace_id

    # The parent session keeps FIFO ordering, so its own input settles first.
    assert (await execute_command(h, h.parent_task_id, "agent_turn")).status == "completed"
    executed = await execute_command(h, resume.id, "coordinator_resume")
    assert executed.status == "completed"
    assert executed.operation == "coordinator_resume"
    assert h.runtime.calls[0] == first.turn_id
    assert h.runtime.calls[-1] == h.parent_turn_id
    assert (await h.store.get_session(h.parent_id)).messages[-1].content == "worker-result"


@pytest.mark.asyncio
async def test_coordinator_child_delivers_through_redis_stream(harness):
    h = harness
    child_session_id = await make_session(h.store, "Streamed child")
    record = await submit_child(h, h.parent_task_id, worker("worker-stream-1"), child_session_id)
    assert await h.outbox.relay_once(h.queue) >= 1
    worker_id = "stream-worker"
    task_worker = RedisTaskWorker(
        h.queue,
        consumer=worker_id,
        handler=lambda item: h.dispatcher.handle(item, worker_id=worker_id),
        block_ms=0,
    )
    handled = 0
    for _ in range(4):
        count = await task_worker.run_once()
        handled += count
        if not count:
            break
    assert handled == 2
    assert (await h.tasks.get(h.parent_task_id)).status == "completed"
    assert (await h.tasks.get(record.id)).status == "completed"
    assert [call["child_task_id"] for call in h.coordinator.finished] == [record.id]
    assert (await h.redis.xpending(h.queue.stream, h.queue.group))["pending"] == 0


@pytest.mark.asyncio
async def test_duplicate_child_completion_does_not_requeue_parent_resume(harness):
    h = harness
    child_session_id = await make_session(h.store, "Watched child")
    spec = worker("worker-duplicate-1")
    record = await submit_child(h, h.parent_task_id, spec, child_session_id)
    coordinator = build_coordinator(h)
    parent = await h.store.get_session(h.parent_id)
    parent.metadata["worker_dispatches"] = [{
        "task_id": spec.task_id,
        "child_session_id": child_session_id,
        "agent_key": spec.agent_key,
        "model_key": "",
        "description": spec.description,
        "status": "running",
    }]
    await h.store.save_session(parent)
    notification = {
        "child_task_id": record.id,
        "parent_task_id": h.parent_task_id,
        "worker_task_id": spec.task_id,
        "worker_agent_key": spec.agent_key,
        "child_trace_id": record.trace_id,
        "root_trace_id": "root-trace-1",
        "attempt_id": "attempt-1",
        "status": "completed",
        "summary": "Worker completed.",
        "result_text": "worker-result",
        "usage": {},
    }

    await coordinator.on_child_task_finished(
        parent_task_id=h.parent_task_id,
        child_task_id=record.id,
        child_session_id=child_session_id,
        notification=notification,
    )
    await coordinator.on_child_task_finished(
        parent_task_id=h.parent_task_id,
        child_task_id=record.id,
        child_session_id=child_session_id,
        notification=notification,
    )

    resumes = [item for item in await h.tasks.list_session(h.parent_id) if item.operation == "coordinator_resume"]
    assert len(resumes) == 1
    assert resumes[0].turn_id == h.parent_turn_id
    assert resumes[0].parent_task_id == h.parent_task_id
    settled = next(
        item
        for item in (await h.store.get_session(h.parent_id)).metadata["worker_dispatches"]
        if item.get("task_id") == spec.task_id
    )
    assert settled["status"] == "completed"
    events = await h.store.list_events(h.parent_id)
    assert [event.type for event in events].count("worker.task_notification_received") == 1


@pytest.mark.asyncio
async def test_terminal_child_projection_can_resume_after_submit_failure(harness, monkeypatch):
    h = harness
    child_session_id = await make_session(h.store, "Resume failure child")
    spec = worker("worker-resume-failure")
    child = await submit_child(h, h.parent_task_id, spec, child_session_id)
    coordinator = build_coordinator(h)
    parent = await h.store.get_session(h.parent_id)
    parent.metadata["worker_dispatches"] = [{
        "task_id": spec.task_id,
        "child_session_id": child_session_id,
        "agent_key": spec.agent_key,
        "model_key": "",
        "description": spec.description,
        "status": "running",
    }]
    await h.store.save_session(parent)
    notification = {
        "child_task_id": child.id,
        "parent_task_id": h.parent_task_id,
        "worker_task_id": spec.task_id,
        "worker_agent_key": spec.agent_key,
        "child_trace_id": child.trace_id,
        "root_trace_id": "root-trace-1",
        "attempt_id": "attempt-1",
        "status": "completed",
        "summary": "Worker completed.",
        "result_text": "worker-result",
        "usage": {},
    }
    submit_resume = h.dispatcher.submit_coordinator_resume

    async def fail_submit_once(**kwargs):
        monkeypatch.setattr(h.dispatcher, "submit_coordinator_resume", submit_resume)
        raise RuntimeError("injected parent resume submission failure")

    monkeypatch.setattr(h.dispatcher, "submit_coordinator_resume", fail_submit_once)
    with pytest.raises(RuntimeError, match="injected parent resume submission failure"):
        await coordinator.on_child_task_finished(
            parent_task_id=h.parent_task_id, child_task_id=child.id,
            child_session_id=child_session_id, notification=notification,
        )
    assert (await h.store.get_session(h.parent_id)).metadata["worker_dispatches"][0]["status"] == "completed"
    assert not [item for item in await h.tasks.list_session(h.parent_id) if item.operation == "coordinator_resume"]

    await coordinator.on_child_task_finished(
        parent_task_id=h.parent_task_id, child_task_id=child.id,
        child_session_id=child_session_id, notification=notification,
    )
    resumes = [item for item in await h.tasks.list_session(h.parent_id) if item.operation == "coordinator_resume"]
    assert len(resumes) == 1


@pytest.mark.asyncio
async def test_settled_child_recovery_scan_requeues_missing_parent_resume(harness, monkeypatch):
    h = harness
    child_session_id = await make_session(h.store, "Recovery scan child")
    spec = worker("worker-recovery-scan")
    child = await submit_child(h, h.parent_task_id, spec, child_session_id)
    coordinator = build_coordinator(h)
    h.dispatcher.set_coordinator_service(coordinator)
    parent = await h.store.get_session(h.parent_id)
    parent.metadata["worker_dispatches"] = [{
        "task_id": spec.task_id,
        "child_session_id": child_session_id,
        "agent_key": spec.agent_key,
        "model_key": "",
        "description": spec.description,
        "status": "running",
    }]
    await h.store.save_session(parent)
    submit_resume = h.dispatcher.submit_coordinator_resume

    async def fail_submit_once(**kwargs):
        monkeypatch.setattr(h.dispatcher, "submit_coordinator_resume", submit_resume)
        raise RuntimeError("injected parent resume submission failure")

    monkeypatch.setattr(h.dispatcher, "submit_coordinator_resume", fail_submit_once)
    completed = await execute_command(h, child.id, "coordinator_child")
    assert completed.status == "completed"
    assert not [item for item in await h.tasks.list_session(h.parent_id) if item.operation == "coordinator_resume"]
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET updated_at = NOW() - INTERVAL '2 seconds' WHERE id=%s",
            (child.id,),
        )

    restarted_store, restarted_tasks, restarted_sessions, restarted_dispatcher = build_services(
        h.settings, Runtime(), h.resources
    )
    restarted_coordinator = CoordinatorRuntimeService(
        settings=h.settings, store=restarted_store,
        session_service=restarted_sessions, agent_registry=AgentRegistry(),
    )
    restarted_coordinator.set_agent_task_store(restarted_tasks)
    restarted_coordinator.set_agent_task_dispatcher(restarted_dispatcher)
    restarted_dispatcher.set_coordinator_service(restarted_coordinator)

    assert await restarted_dispatcher.recover_settled_coordinator_children() == 1
    assert await restarted_dispatcher.recover_settled_coordinator_children() == 0
    resumes = [item for item in await h.tasks.list_session(h.parent_id) if item.operation == "coordinator_resume"]
    assert len(resumes) == 1
    assert resumes[0].parent_task_id == h.parent_task_id


@pytest.mark.asyncio
async def test_timed_out_child_recovery_uses_durable_result_after_notify_failure(harness, monkeypatch):
    h = harness
    child_session_id = await make_session(h.store, "Timed out recovery child")
    spec = worker("worker-timeout-recovery")
    child = await submit_child(h, h.parent_task_id, spec, child_session_id)
    await h.tasks.claim(child.id, worker_id="expired-worker")
    coordinator = build_coordinator(h)
    h.dispatcher.set_coordinator_service(coordinator)
    parent = await h.store.get_session(h.parent_id)
    parent.metadata["worker_dispatches"] = [{
        "task_id": spec.task_id,
        "child_session_id": child_session_id,
        "agent_key": spec.agent_key,
        "model_key": "",
        "description": spec.description,
        "status": "running",
    }]
    await h.store.save_session(parent)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET created_at=NOW()-INTERVAL '3600 seconds', "
            "updated_at=NOW()-INTERVAL '3600 seconds' WHERE id=%s",
            (child.id,),
        )
        cur.execute(
            f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set("
            "metadata,'{turn_lease_expires_at}',to_jsonb(NOW()-INTERVAL '3600 seconds')) WHERE id=%s",
            (child_session_id,),
        )
    submit_resume = h.dispatcher.submit_coordinator_resume

    async def fail_submit_once(**kwargs):
        monkeypatch.setattr(h.dispatcher, "submit_coordinator_resume", submit_resume)
        raise RuntimeError("injected timeout resume submission failure")

    monkeypatch.setattr(h.dispatcher, "submit_coordinator_resume", fail_submit_once)
    assert await h.dispatcher.reap_coordinator_children() == 1
    assert (await h.tasks.get(child.id)).result_payload["status"] == "failed"
    assert not [item for item in await h.tasks.list_session(h.parent_id) if item.operation == "coordinator_resume"]
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET updated_at = NOW() - INTERVAL '2 seconds' WHERE id=%s",
            (child.id,),
        )
    assert await h.dispatcher.recover_settled_coordinator_children() == 1
    assert len([item for item in await h.tasks.list_session(h.parent_id) if item.operation == "coordinator_resume"]) == 1


@pytest.mark.asyncio
async def test_two_coordinators_do_not_project_the_same_child_twice(harness, monkeypatch):
    h = harness
    child_session_id = await make_session(h.store, "Concurrent notification child")
    spec = worker("worker-concurrent-notification")
    child = await submit_child(h, h.parent_task_id, spec, child_session_id)
    parent = await h.store.get_session(h.parent_id)
    parent.metadata["worker_dispatches"] = [{
        "task_id": spec.task_id,
        "child_session_id": child_session_id,
        "agent_key": spec.agent_key,
        "model_key": "",
        "description": spec.description,
        "status": "running",
    }]
    await h.store.save_session(parent)
    notification = {
        "child_task_id": child.id,
        "parent_task_id": h.parent_task_id,
        "worker_task_id": spec.task_id,
        "worker_agent_key": spec.agent_key,
        "child_trace_id": child.trace_id,
        "root_trace_id": "root-trace-1",
        "attempt_id": "attempt-1",
        "status": "failed",
        "summary": "Worker failed.",
        "result_text": "injected child failure",
        "usage": {},
    }
    coordinators = [build_coordinator(h), build_coordinator(h)]
    both_read = asyncio.Event()
    entered = 0
    for coordinator in coordinators:
        project = coordinator._project_worker_notification

        async def pause_after_read(*, _project=project, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), 5)
            return await _project(**kwargs)

        monkeypatch.setattr(coordinator, "_project_worker_notification", pause_after_read)

    await asyncio.gather(*(
        coordinator.on_child_task_finished(
            parent_task_id=h.parent_task_id, child_task_id=child.id,
            child_session_id=child_session_id, notification=notification,
        )
        for coordinator in coordinators
    ))
    parent = await h.store.get_session(h.parent_id)
    assert parent.metadata["worker_failure_guard"]["count"] == 1
    assert [event.type for event in await h.store.list_events(h.parent_id)].count(
        "worker.task_notification_received"
    ) == 1
    assert len([item for item in await h.tasks.list_session(h.parent_id) if item.operation == "coordinator_resume"]) == 1


@pytest.mark.asyncio
async def test_child_execution_failure_still_notifies_parent(harness):
    h = harness
    runtime = RaisingRuntime()
    _, _, _, dispatcher = build_services(h.settings, runtime, h.resources)
    recorder = RecordingCoordinator()
    dispatcher.set_coordinator_service(recorder)
    child_session_id = await make_session(h.store, "Failing child")
    spec = worker("worker-failing-1")
    record = await submit_child(h, h.parent_task_id, spec, child_session_id, turn_id=None)
    child_task_id = record.id

    await dispatcher.handle(await outbox_command(h, child_task_id, "coordinator_child"), worker_id="failing-worker")

    failed = await h.tasks.get(child_task_id)
    assert failed.status == "failed"
    assert failed.error_code == "RuntimeError"
    assert failed.result_payload["status"] == "failed"
    assert "injected child execution failure" in failed.result_payload["result_text"]
    assert failed.result_payload["child_task_id"] == child_task_id
    assert [call["notification"]["status"] for call in recorder.finished] == ["failed"]
    assert h.coordinator.finished == []


@pytest.mark.asyncio
async def test_child_depth_and_count_guard(harness):
    h = harness
    h.settings.orchestration.coordinator_max_workers = 2
    filled = [await make_session(h.store, f"Sibling {index}") for index in range(2)]
    for index, child in enumerate(filled):
        record = await submit_child(h, h.parent_task_id, worker(f"worker-sibling-{index}"), child)
        assert record.operation == "coordinator_child"
    overflow = await make_session(h.store, "Sibling overflow")
    with pytest.raises(ValueError, match="coordinator_child_limit_exceeded"):
        await submit_child(h, h.parent_task_id, worker("worker-sibling-overflow"), overflow)
    # The limit is concurrent child work, not all children ever created by the
    # parent. A settled sibling must free one slot for a later review round.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET status='completed' WHERE parent_task_id=%s AND session_id=%s",
            (h.parent_task_id, filled[0]),
        )
    replacement = await submit_child(
        h, h.parent_task_id, worker("worker-sibling-after-completion"), overflow
    )
    assert replacement.operation == "coordinator_child"

    # The chain grows one level per child, so it needs its own clean tree.
    chain_parent_session = await make_session(h.store, "Chain root parent")
    chain_root = await h.dispatcher.submit(
        chain_parent_session,
        SendMessageRequest(
            content="chain root",
            metadata={"client_message_id": "chain-root-1"},
        ),
    )
    chain_parent = chain_root.task.id
    for depth in range(4):
        child = await make_session(h.store, f"Chain {depth}")
        record = await submit_child(h, chain_parent, worker(f"worker-chain-{depth}"), child)
        assert record.operation == "coordinator_child"
        chain_parent = record.id

    deepest = await make_session(h.store, "Too deep")
    with pytest.raises(ValueError, match="coordinator_depth_exceeded"):
        await submit_child(h, chain_parent, worker("worker-chain-deep"), deepest)


@pytest.mark.asyncio
async def test_two_coordinators_claim_one_followup_batch_without_duplicate_workers(harness):
    h = harness
    h.settings.orchestration.coordinator_max_workers = 4
    parent = await h.store.get_session(h.parent_id)
    parent.metadata["worker_dispatches"] = [
        {
            "task_id": f"source-{index}",
            "parent_turn_id": h.parent_turn_id,
            "status": "completed",
            "debate_stage": "independent_findings",
            "debate_round_index": 1,
        }
        for index in range(5)
    ]
    parent.metadata["pending_followup_workers"] = [
        {
            "task_id": f"followup-{index}",
            "description": f"Follow-up {index}",
            "prompt": "Review peer findings",
            "agent_key": "qa-planner",
            "skill_keys": [],
            "context": {
                "debate_stage": "cross_review",
                "debate_round_index": 2,
                "debate_total_round_count": 2,
                "source_stage": "independent_findings",
                "source_round_index": 1,
            },
        }
        for index in range(5)
    ]
    parent.metadata["followup_workers_parent_turn_id"] = h.parent_turn_id
    parent.metadata["followup_workers_parent_trace_id"] = "root-trace-1"
    await h.store.save_session(parent)

    created: list[str] = []

    class Sessions:
        async def create_session(self, request):
            session_id = await make_session(h.store, request.title)
            created.append(session_id)
            return await h.store.get_session(session_id)

    coordinators = [build_coordinator(h), build_coordinator(h)]
    for coordinator in coordinators:
        coordinator._session_service = Sessions()
        coordinator._start_child_worker = AsyncMock(return_value=None)
        coordinator._build_peer_review_bundle = AsyncMock(return_value="peer findings")
    snapshots = [await h.store.get_session(h.parent_id) for _ in coordinators]
    await asyncio.gather(*(
        coordinator._maybe_launch_followup_workers(snapshot, h.parent_turn_id)
        for coordinator, snapshot in zip(coordinators, snapshots)
    ))

    updated = await h.store.get_session(h.parent_id)
    launched = [
        record for record in updated.metadata["worker_dispatches"]
        if str(record.get("task_id") or "").startswith("followup-")
    ]
    assert len(created) == 4
    assert [record["task_id"] for record in launched] == [f"followup-{index}" for index in range(4)]
    assert [item["task_id"] for item in updated.metadata["pending_followup_workers"]] == ["followup-4"]

    await coordinators[0]._register_worker_dispatches(
        h.parent_id, h.parent_turn_id, [{"task_id": "followup-0", "status": "completed"}]
    )
    await coordinators[1]._maybe_launch_followup_workers(updated, h.parent_turn_id)
    updated = await h.store.get_session(h.parent_id)
    assert len(created) == 5
    assert updated.metadata["pending_followup_workers"] == []
    assert sorted(
        record["task_id"] for record in updated.metadata["worker_dispatches"]
        if str(record.get("task_id") or "").startswith("followup-")
    ) == [f"followup-{index}" for index in range(5)]


@pytest.mark.asyncio
async def test_two_coordinators_claim_initial_overflow_once(harness):
    h = harness
    h.settings.orchestration.coordinator_max_workers = 4
    parent = await h.store.get_session(h.parent_id)
    parent.metadata["worker_dispatches"] = [
        {
            "task_id": f"initial-{index}",
            "parent_turn_id": h.parent_turn_id,
            "status": "completed" if index == 0 else "running",
            "agent_key": "qa-planner",
            "description": f"Reviewer {index}",
            "dispatch_role": "worker",
            "debate_stage": "independent_findings",
            "debate_round_index": 1,
        }
        for index in range(4)
    ] + [{
        "task_id": "initial-4",
        "parent_turn_id": h.parent_turn_id,
        "status": "queued",
        "agent_key": "qa-planner",
        "description": "Reviewer 4",
        "dispatch_role": "worker",
        "debate_stage": "independent_findings",
        "debate_round_index": 1,
    }]
    parent.metadata["pending_initial_workers"] = [{
        "task_id": "initial-4",
        "description": "Reviewer 4",
        "prompt": "Review the project",
        "agent_key": "qa-planner",
        "skill_keys": [],
        "context": {"debate_stage": "independent_findings", "debate_round_index": 1},
    }]
    parent.metadata["pending_initial_workers_parent_turn_id"] = h.parent_turn_id
    parent.metadata["pending_initial_workers_parent_trace_id"] = "root-trace-1"
    await h.store.save_session(parent)

    created: list[str] = []

    class Sessions:
        async def create_internal_session(self, request):
            session_id = await make_session(h.store, request.title)
            created.append(session_id)
            return await h.store.get_session(session_id)

    coordinators = [build_coordinator(h), build_coordinator(h)]
    for coordinator in coordinators:
        coordinator._session_service = Sessions()
        coordinator._start_child_worker = AsyncMock(return_value=None)
    snapshots = [await h.store.get_session(h.parent_id) for _ in coordinators]
    await asyncio.gather(*(
        coordinator._maybe_launch_initial_workers(snapshot, h.parent_turn_id)
        for coordinator, snapshot in zip(coordinators, snapshots)
    ))

    updated = await h.store.get_session(h.parent_id)
    assert len(created) == 1
    assert updated.metadata["pending_initial_workers"] == []
    records = [record for record in updated.metadata["worker_dispatches"] if record["task_id"] == "initial-4"]
    assert len(records) == 1
    assert records[0]["status"] == "running"


@pytest.mark.asyncio
async def test_postgres_duplicate_dispatch_returns_original_worker_receipt(harness):
    h = harness
    created: list[str] = []

    class Sessions:
        async def create_internal_session(self, request):
            session_id = await make_session(h.store, request.title, metadata=request.metadata)
            created.append(session_id)
            return await h.store.get_session(session_id)

    first_coordinator = build_coordinator(h)
    second_coordinator = build_coordinator(h)
    for coordinator in (first_coordinator, second_coordinator):
        coordinator._session_service = Sessions()
        coordinator._start_child_worker = AsyncMock(return_value=None)
    payload = {"workers": [{
        "description": "Review the project", "prompt": "Find defects",
        "agent_key": "qa-planner",
    }]}
    context = {
        "session_id": h.parent_id, "turn_id": h.parent_turn_id,
        "trace_id": "root-trace-1",
    }

    first = await first_coordinator.dispatch(payload, context)
    second = await second_coordinator.dispatch(payload, context)

    assert len(created) == 1
    assert second["status"] == "duplicate"
    assert second["ok"] is True
    assert second["workers"] == first["workers"]
    assert second["workers"][0]["child_session_id"] == created[0]
    parent = await h.store.get_session(h.parent_id)
    assert len(parent.metadata["coordinator_dispatch_claims"]) == 1


@pytest.mark.asyncio
async def test_child_parent_cycle_and_missing_parent_guard(harness):
    h = harness
    child_session_id = await make_session(h.store, "Cyclic child")
    record = await submit_child(h, h.parent_task_id, worker("worker-cycle-1"), child_session_id)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} SET parent_task_id=id WHERE id=%s",
            (record.id,),
        )
    looped = await make_session(h.store, "Cyclic grandchild")
    with pytest.raises(ValueError, match="coordinator_parent_cycle"):
        await submit_child(h, record.id, worker("worker-cycle-2"), looped)
    orphan = await make_session(h.store, "Orphan child")
    with pytest.raises(ValueError, match="coordinator_parent_not_found"):
        await submit_child(h, f"task-{uuid4()}", worker("worker-cycle-3"), orphan)


@pytest.mark.asyncio
async def test_cancel_parent_children_only_cancels_unstarted_rows(harness):
    h = harness
    sessions = [await make_session(h.store, f"Queued child {index}") for index in range(3)]
    records = [
        await submit_child(h, h.parent_task_id, worker(f"worker-cancel-{index}"), child)
        for index, child in enumerate(sessions)
    ]
    await h.tasks.claim(records[2].id, worker_id="claimed-worker")
    coordinator = build_coordinator(h)

    # The parent input is still queued, so no durable turn owns it yet.
    assert await coordinator.cancel_parent_children(h.parent_id, h.parent_turn_id) == 0

    claimed = await h.tasks.claim(h.parent_task_id, worker_id="parent-worker")
    assert claimed.status == "claimed"
    assert await coordinator.cancel_parent_children(h.parent_id, h.parent_turn_id) == 2

    statuses = sorted(item.status for item in await h.tasks.list_children(h.parent_task_id))
    assert statuses == ["cancelled", "cancelled", "claimed"]
    for child_session_id in sessions[:2]:
        events = await h.store.list_events(child_session_id)
        assert "agent_task.cancelled" in [event.type for event in events]
    assert "agent_task.cancelled" not in [
        event.type for event in await h.store.list_events(sessions[2])
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "waiting_resource", "claimed", "running"])
async def test_child_reaper_preserves_queue_wait_and_live_execution_lease(harness, status):
    h = harness
    child_session_id = await make_session(h.store, f"Old {status} child")
    record = await submit_child(h, h.parent_task_id, worker(f"worker-old-{status}"), child_session_id)
    if status != "queued":
        claim = await h.tasks.claim(record.id, worker_id="live-worker")
        if status == "waiting_resource":
            await h.tasks.defer(claim, "agent quota occupied")
        elif status == "running":
            await h.tasks.begin(claim, resource_id=f"turn:{record.id}", fencing_token=1)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET created_at=NOW()-INTERVAL '3600 seconds', "
            "updated_at=NOW()-INTERVAL '3600 seconds' WHERE id=%s",
            (record.id,),
        )
    if status in {"claimed", "running"}:
        assert await h.store.renew_turn_execution(child_session_id, claim.lease_token, 300)

    assert await h.dispatcher.reap_coordinator_children() == 0
    assert (await h.tasks.get(record.id)).status == status
    assert h.coordinator.finished == []
    assert not any(event.type == "agent_task.failed" for event in await h.store.list_events(child_session_id))
    if status == "running":
        await h.tasks.finish(claim, status="completed")
        assert (await h.tasks.get(record.id)).status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_reap_expired_children_fails_and_notifies(harness, started):
    h = harness
    child_session_id = await make_session(h.store, "Overdue child")
    record = await submit_child(h, h.parent_task_id, worker("worker-overdue-1"), child_session_id)
    claim = await h.tasks.claim(record.id, worker_id="expired-worker")
    if started:
        await h.tasks.begin(claim, resource_id=f"turn:{record.id}", fencing_token=1)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET created_at=NOW()-INTERVAL '3600 seconds', "
            "updated_at=NOW()-INTERVAL '3600 seconds' WHERE id=%s",
            (record.id,),
        )
        cur.execute(
            f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set("
            "metadata,'{turn_lease_expires_at}',to_jsonb(NOW()-INTERVAL '3600 seconds')) WHERE id=%s",
            (child_session_id,),
        )

    counts = await asyncio.gather(
        h.dispatcher.reap_coordinator_children(), h.dispatcher.reap_coordinator_children(),
    )
    assert sum(counts) == 1
    assert await h.dispatcher.reap_coordinator_children() == 0

    failed = await h.tasks.get(record.id)
    assert failed.status == "failed"
    assert failed.error_code == "coordinator_child_timeout"
    assert failed.result_payload["status"] == "failed"
    assert (await h.store.get_session(child_session_id)).messages[0].metadata["delivery_status"] == "failed"
    assert [call["child_task_id"] for call in h.coordinator.finished] == [record.id]
    notification = h.coordinator.finished[0]["notification"]
    assert notification["status"] == "failed"
    assert notification["worker_task_id"] == "worker-overdue-1"
    assert notification["parent_task_id"] == h.parent_task_id
    assert "Timeout" in notification["summary"]
    session = await h.store.get_session(child_session_id)
    assert session.status == SessionStatus.interrupted
    assert "turn_lease_token" not in session.metadata
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT status,finished_at FROM {h.settings.database.postgres_agent_attempt_table} WHERE id=%s",
            (claim.attempt_id,),
        )
        attempt = cur.fetchone()
    assert attempt["status"] == "failed" and attempt["finished_at"] is not None
    # An old worker cannot overwrite the timeout, nor can redelivery replay it.
    with pytest.raises(ContinuationLeaseLostError):
        await h.tasks.finish(claim, status="completed")
    await execute_command(h, record.id, "coordinator_child", worker_id="late-worker")
    assert (await h.tasks.get(record.id)).status == "failed"
    assert h.runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("recent", ["progress", "lease"])
async def test_child_reaper_grants_timeout_from_recent_activity_not_creation(harness, recent):
    h = harness
    child_session_id = await make_session(h.store, "Recently active child")
    record = await submit_child(h, h.parent_task_id, worker("worker-recent"), child_session_id)
    claim = await h.tasks.claim(record.id, worker_id="recent-worker")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET created_at=NOW()-INTERVAL '3600 seconds', updated_at=%s WHERE id=%s",
            (claim.updated_at if recent == "progress" else claim.updated_at - timedelta(hours=1), record.id),
        )
    assert await h.store.renew_turn_execution(child_session_id, claim.lease_token, 0)
    if recent == "progress":
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set("
                "metadata,'{turn_lease_expires_at}',to_jsonb(NOW()-INTERVAL '3600 seconds')) WHERE id=%s",
                (child_session_id,),
            )
    # Use a 60s grace so a slow CI machine cannot consume the test window.
    h.settings.orchestration.coordinator_child_timeout_seconds = 60

    assert await h.dispatcher.reap_coordinator_children() == 0
    assert (await h.tasks.get(record.id)).status == "claimed"
    assert h.coordinator.finished == []


@pytest.mark.asyncio
async def test_child_reaper_skips_locked_session_and_preserves_concurrent_renewal(harness):
    h = harness
    child_session_id = await make_session(h.store, "Renewing child")
    record = await submit_child(h, h.parent_task_id, worker("worker-renewing"), child_session_id)
    claim = await h.tasks.claim(record.id, worker_id="renewing-worker")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET created_at=NOW()-INTERVAL '3600 seconds', updated_at=NOW()-INTERVAL '3600 seconds' WHERE id=%s",
            (record.id,),
        )
        cur.execute(
            f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set("
            "metadata,'{turn_lease_expires_at}',to_jsonb(NOW()-INTERVAL '3600 seconds')) WHERE id=%s",
            (child_session_id,),
        )
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {h.settings.database.postgres_session_table} WHERE id=%s FOR UPDATE", (child_session_id,))
        assert await asyncio.wait_for(h.dispatcher.reap_coordinator_children(), timeout=3) == 0
        cur.execute(
            f"UPDATE {h.settings.database.postgres_session_table} SET metadata=jsonb_set("
            "metadata,'{turn_lease_expires_at}',to_jsonb(NOW()+INTERVAL '300 seconds')) WHERE id=%s",
            (child_session_id,),
        )

    assert await h.dispatcher.reap_coordinator_children() == 0
    session = await h.store.get_session(child_session_id)
    assert session.metadata["turn_lease_token"] == claim.lease_token
    assert (await h.tasks.get(record.id)).status == "claimed"
    assert h.coordinator.finished == []


@pytest.mark.asyncio
async def test_child_reaper_does_not_clear_another_turns_lease(harness):
    h = harness
    child_session_id = await make_session(h.store, "Old child with another owner")
    record = await submit_child(h, h.parent_task_id, worker("worker-old-owner"), child_session_id)
    await h.tasks.claim(record.id, worker_id="old-worker")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET updated_at=NOW()-INTERVAL '3600 seconds' WHERE id=%s",
            (record.id,),
        )
        cur.execute(
            f"UPDATE {h.settings.database.postgres_session_table} SET status='running', "
            "metadata=metadata || jsonb_build_object('turn_lease_token','other-token', "
            "'turn_lease_turn_id','other-turn', 'turn_lease_owner','other-worker', "
            "'turn_lease_expires_at',to_jsonb(NOW()+INTERVAL '300 seconds')) WHERE id=%s",
            (child_session_id,),
        )

    assert await h.dispatcher.reap_coordinator_children() == 1
    assert (await h.tasks.get(record.id)).status == "failed"
    session = await h.store.get_session(child_session_id)
    assert session.status == SessionStatus.running
    assert session.metadata["turn_lease_token"] == "other-token"
    assert session.metadata["turn_lease_turn_id"] == "other-turn"


@pytest.mark.asyncio
async def test_child_reaper_loop_starts_and_stops(harness):
    h = harness
    h.settings.orchestration.coordinator_child_timeout_seconds = 600
    child_session_id = await make_session(h.store, "Reaper child")
    await submit_child(h, h.parent_task_id, worker("worker-reaper-1"), child_session_id)

    await h.dispatcher.start_child_reaper()
    assert h.dispatcher._child_reaper_task is not None
    try:
        await asyncio.sleep(.3)
        assert not h.dispatcher._child_reaper_task.done()
    finally:
        await h.dispatcher.stop_child_reaper()
    assert h.dispatcher._child_reaper_task is None
    assert h.coordinator.finished == []
    assert (await h.tasks.list_children(h.parent_task_id))[0].status == "queued"


@pytest.mark.asyncio
async def test_start_child_reaper_stays_off_without_the_flag(harness):
    h = harness
    h.settings.orchestration.coordinator_durable_children = False
    try:
        await h.dispatcher.start_child_reaper()
        assert h.dispatcher._child_reaper_task is None
        await h.dispatcher.stop_child_reaper()
    finally:
        h.settings.orchestration.coordinator_durable_children = True


@pytest.mark.asyncio
async def test_coordinator_launch_prefers_durable_child_task(harness):
    h = harness
    coordinator = build_coordinator(h)
    claimed = await h.tasks.claim(h.parent_task_id, worker_id="parent-worker")
    await h.tasks.begin(claimed, resource_id=f"turn:{h.parent_task_id}", fencing_token=1)
    child_session_id = await make_session(h.store, "Dispatched child")
    spec = worker("worker-dispatch-1")

    child_task_id = await coordinator._start_child_worker(
        parent_session=await h.store.get_session(h.parent_id),
        parent_session_id=h.parent_id,
        parent_turn_id=h.parent_turn_id,
        parent_trace_id="trace-parent",
        child_session_id=child_session_id,
        worker=spec,
        mode_key="default",
        register_pool=False,
    )

    assert child_task_id
    assert coordinator._active_tasks == {}
    record = await h.tasks.get(child_task_id)
    assert record.operation == "coordinator_child"
    assert record.parent_task_id == h.parent_task_id
    assert record.root_trace_id == "trace-parent"
    assert record.input["source"] == "coordinator.dispatch"
    assert record.input["submit_mode"] == "background"
    assert record.input["context"]["parent_turn_id"] == h.parent_turn_id
    assert record.input["context"]["parent_task_id"] == h.parent_task_id
    assert record.input["metadata"]["task_id"] == spec.task_id
    events = await h.store.list_events(child_session_id)
    assert [event.type for event in events][-1] == "input.queued"


@pytest.mark.asyncio
async def test_coordinator_launch_keeps_in_process_path_when_flag_off(harness):
    h = harness
    h.settings.orchestration.coordinator_durable_children = False
    try:
        coordinator = build_coordinator(h)
        child_session_id = await make_session(h.store, "In-process child")
        spec = worker("worker-legacy-1")

        child_task_id = await coordinator._start_child_worker(
            parent_session=await h.store.get_session(h.parent_id),
            parent_session_id=h.parent_id,
            parent_turn_id="turn-without-durable-task",
            parent_trace_id="trace-parent",
            child_session_id=child_session_id,
            worker=spec,
            mode_key="default",
            register_pool=False,
        )
        assert child_task_id is None
        assert spec.task_id in coordinator._active_tasks
        await asyncio.wait_for(coordinator._active_tasks[spec.task_id], 20)

        assert await h.tasks.list_children(h.parent_task_id) == []
        assert h.coordinator.finished == []
        assert [m.content for m in (await h.store.get_session(child_session_id)).messages] == [
            spec.prompt,
            "worker-result",
        ]
    finally:
        h.settings.orchestration.coordinator_durable_children = True
