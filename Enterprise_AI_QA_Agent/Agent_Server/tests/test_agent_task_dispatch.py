from __future__ import annotations

import asyncio
import multiprocessing
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from redis.asyncio import Redis

from src.api.routes.sessions import router
from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.runtime.runtime_service import RuntimeTurnResult
from src.application.sessions.session_service import SessionService
from src.core.config import get_settings
from src.core.request_context import bind_request_context
from src.domain.models import SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.registry.modes import ModeRegistry
from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.runtime.task_deferred import TaskDeferred
from src.schemas.session import (
    ApprovalDecisionRequest,
    InterruptSessionRequest,
    HeadlessExecutionRequest,
    ResumeSessionRequest,
    RuntimeMode,
    SendMessageRequest,
    SessionMode,
    SessionSnapshot,
    SessionStatus,
    ToolApprovalRequest,
    ToolApprovalStatus,
)
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _drop_tables


class Runtime:
    def __init__(self):
        self.calls = []
        self.messages = []
        self.entered = asyncio.Event()
        self.finish = None
        self.interrupted = False
        self.resume_calls = []
        self.approval_calls = []

    def request_interrupt(self, *args):
        self.interrupted = True
        if self.finish is not None:
            self.finish.set()

    async def execute_turn(self, session, request, **kwargs):
        self.calls.append(request.turn_id)
        self.messages.append([m.content for m in session.messages])
        self.entered.set()
        if self.finish is not None:
            await self.finish.wait()
        return RuntimeTurnResult(
            output_text="worker-result",
            events=[],
            tool_messages=[],
            approvals=[],
            pending_turn={},
            snapshot=SessionSnapshot(
                id=str(uuid4()),
                session_id=session.id,
                version=len(self.calls),
                stage="completed",
                created_at=datetime.now(timezone.utc),
                graph_state={"turn_id": request.turn_id},
            ),
            state={"turn_id": request.turn_id, "trace_id": "test-trace", "selected_agent_key": "default",
                "selected_agent_name": "Default", "selected_model_key": "test", "selected_model_name": "Test",
                "termination_reason": "interrupted" if self.interrupted else "completed"})

    async def resume_turn(self, session, snapshot, **kwargs):
        turn_id = str(snapshot.graph_state.get("turn_id") or f"resume_{snapshot.id}")
        self.resume_calls.append((snapshot.id, kwargs.get("resume_reason")))
        return RuntimeTurnResult(
            output_text="worker-resume-result",
            events=[],
            tool_messages=[],
            approvals=[],
            pending_turn={},
            snapshot=SessionSnapshot(
                id=str(uuid4()),
                session_id=session.id,
                version=snapshot.version + 1,
                stage="completed",
                created_at=datetime.now(timezone.utc),
                graph_state={"turn_id": turn_id},
            ),
            state={
                "turn_id": turn_id,
                "trace_id": "test-trace",
                "selected_agent_key": "default",
                "selected_agent_name": "Default",
                "selected_model_key": "test",
                "selected_model_name": "Test",
                "termination_reason": "completed",
                "user_message": "original turn",
            },
        )

    async def resume_after_approval(self, session, approval, **kwargs):
        self.approval_calls.append(approval["id"])
        turn_id = str((session.metadata.get("pending_turn") or {}).get("turn_id") or "approval-turn")
        return RuntimeTurnResult(
            output_text="worker-approval-result",
            events=[],
            tool_messages=[],
            approvals=[],
            pending_turn={},
            snapshot=SessionSnapshot(
                id=str(uuid4()),
                session_id=session.id,
                version=2,
                stage="completed",
                created_at=datetime.now(timezone.utc),
                graph_state={"turn_id": turn_id},
            ),
            state={
                "turn_id": turn_id,
                "trace_id": "test-trace",
                "selected_agent_key": "default",
                "selected_agent_name": "Default",
                "selected_model_key": "test",
                "selected_model_name": "Test",
                "termination_reason": "completed",
                "user_message": "approved continuation",
            },
        )


def build_services(settings, runtime, resources):
    store = PostgresSessionStore(settings)
    tasks = PostgresAgentTaskStore(settings, store)
    modes = ModeRegistry()
    sessions = SessionService(store, InputOrchestratorService(modes), runtime, modes,
        turn_execution_heartbeat_seconds=.1)
    sessions.set_agent_task_store(tasks)
    dispatcher = AgentTaskDispatcher(settings, tasks, sessions, resources)
    sessions.set_agent_task_dispatcher(dispatcher)
    return store, tasks, sessions, dispatcher


@pytest_asyncio.fixture
async def harness():
    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires real PostgreSQL and Redis")
    settings = get_settings().model_copy(deep=True)
    suffix = uuid4().hex[:12]
    fields = ("session", "message", "event", "snapshot", "approval", "task_outbox", "agent_task", "agent_attempt")
    tables = [f"agent_test_{field}_{suffix}" for field in fields]
    for field, table in zip(fields, tables):
        setattr(settings.database, f"postgres_{field}_table", table)
    prefix = f"agent_test_{suffix}"
    settings.orchestration.agent_task_stream = f"{prefix}:tasks:agent"
    settings.orchestration.agent_task_control_poll_seconds = .05
    redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
    resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix, client=redis)
    await resources.connect()
    await resources.configure_quota(scope="resource_type", identifier="agent", limit=1)
    runtime = Runtime()
    store, tasks, sessions, dispatcher = build_services(settings, runtime, resources)
    outbox = PostgresTaskOutbox(settings)
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=settings.orchestration.agent_task_stream,
        group="test",
        client=redis,
    )
    try:
        await store.initialize()
        await outbox.initialize()
        await tasks.initialize()
        await queue.connect()
        session_id = str(uuid4())
        await store.save_session(SessionRecord(
            id=session_id,
            title="Agent queue integration",
            status=SessionStatus.idle,
            session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,
            mode_key="default",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
        app = FastAPI()
        app.state.session_service, app.state.agent_task_dispatcher, app.state.settings = sessions, dispatcher, settings
        app.include_router(router)
        yield SimpleNamespace(**locals())
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        _drop_tables(settings, list(reversed(tables)))


def payload(content="first", key="client-1", **kwargs):
    return SendMessageRequest(content=content, metadata={"client_message_id": key}, **kwargs)


async def delivery(h, receipt):
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"agent_turn:{receipt.task.id}",))
        return QueuedTask("test-delivery", cur.fetchone()["payload"])


async def prepare_resumable_session(h):
    turn_id = f"turn-resume-{uuid4().hex[:8]}"
    session = await h.store.get_session(h.session_id)
    session.status = SessionStatus.interrupted
    session.metadata["pending_turn"] = {"turn_id": turn_id, "checkpoint": "durable"}
    session.metadata["control"] = {
        "control_state": "interrupted",
        "is_interrupted": True,
        "is_resumable": True,
    }
    await h.store.save_session(session)
    snapshot = SessionSnapshot(
        id=str(uuid4()),
        session_id=h.session_id,
        version=1,
        stage="interrupted",
        created_at=datetime.now(timezone.utc),
        graph_state={"turn_id": turn_id},
    )
    await h.store.save_snapshot(h.session_id, snapshot)
    return snapshot


async def prepare_pending_approval(h):
    turn_id = f"turn-approval-{uuid4().hex[:8]}"
    session = await h.store.get_session(h.session_id)
    session.status = SessionStatus.waiting_approval
    session.metadata["pending_turn"] = {
        "turn_id": turn_id,
        "pending_approval_ids": [],
    }
    session.metadata["control"] = {
        "control_state": "waiting_approval",
        "is_interrupted": False,
        "is_resumable": False,
    }
    await h.store.save_session(session)
    approval = ToolApprovalRequest(
        id=str(uuid4()),
        session_id=h.session_id,
        tool_key="browser.navigate",
        tool_name="Browser Navigate",
        reason="Navigate to the approved test page",
        status=ToolApprovalStatus.pending,
        created_at=datetime.now(timezone.utc),
        metadata={"approval_type": "tool"},
    )
    await h.store.save_approval(h.session_id, approval)
    return approval


@pytest.mark.asyncio
async def test_http_submission_is_atomic_idempotent_and_does_not_execute(harness):
    h = harness
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        responses = await asyncio.gather(*(
            client.post(
                f"/sessions/{h.session_id}/messages",
                json=payload().model_dump(mode="json"),
            )
            for _ in range(8)
        ))
        assert {r.status_code for r in responses} == {202}
        assert len({r.json()["task"]["id"] for r in responses}) == 1
        assert responses[0].json()["session"]["control_state"] == "queued"
        assert "lease_token" not in responses[0].json()["task"]
        conflict = await client.post(
            f"/sessions/{h.session_id}/messages",
            json=payload("different").model_dump(mode="json"),
        )
        assert conflict.status_code == 409
    assert h.runtime.calls == []
    assert len((await h.store.get_session(h.session_id)).messages) == 1
    assert len(await h.store.list_events(h.session_id)) == 1
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS count FROM {h.settings.database.postgres_task_outbox_table}")
        assert cur.fetchone()["count"] == 1


@pytest.mark.asyncio
async def test_http_resume_is_atomic_idempotent_and_runs_only_in_worker(harness):
    h = harness
    snapshot = await prepare_resumable_session(h)
    resume = ResumeSessionRequest(
        reason="continue the checkpoint",
        source="test",
        client_request_id="resume-request-1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="http://test",
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    f"/sessions/{h.session_id}/resume",
                    json=resume.model_dump(mode="json"),
                )
                for _ in range(6)
            )
        )
    assert {response.status_code for response in responses} == {202}
    task_ids = {response.json()["task"]["id"] for response in responses}
    assert len(task_ids) == 1
    body = responses[0].json()
    assert body["task"]["operation"] == "manual_resume"
    assert body["task"]["reference_id"] == snapshot.id
    assert body["session"]["control_state"] == "queued"
    assert body["session"]["is_resumable"] is False
    assert h.runtime.resume_calls == []
    assert (await h.store.get_session(h.session_id)).messages == []
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS count FROM {h.settings.database.postgres_task_outbox_table}"
        )
        assert cur.fetchone()["count"] == 1

    receipt = SimpleNamespace(task=SimpleNamespace(id=next(iter(task_ids))))
    assert await h.outbox.relay_once(h.queue) == 1
    worker = RedisTaskWorker(
        h.queue,
        consumer="resume-worker",
        handler=lambda item: h.dispatcher.handle(item, worker_id="resume-worker"),
        block_ms=0,
    )
    assert await worker.run_once() == 1
    completed = await h.tasks.get(receipt.task.id)
    assert completed.status == "completed"
    assert completed.operation == "manual_resume"
    assert completed.user_message_id is None
    assert h.runtime.resume_calls == [(snapshot.id, "continue the checkpoint")]
    session = await h.store.get_session(h.session_id)
    assert [message.content for message in session.messages] == ["worker-resume-result"]
    assert "turn_lease_token" not in session.metadata


@pytest.mark.asyncio
async def test_resume_checkpoint_change_fails_without_replaying_runtime(harness):
    h = harness
    original = await prepare_resumable_session(h)
    receipt = await h.dispatcher.submit_resume(
        h.session_id,
        ResumeSessionRequest(client_request_id="resume-stale"),
    )
    await h.store.save_snapshot(
        h.session_id,
        SessionSnapshot(
            id=str(uuid4()),
            session_id=h.session_id,
            version=original.version + 1,
            stage="completed",
            created_at=datetime.now(timezone.utc),
            graph_state={"turn_id": original.graph_state["turn_id"]},
        ),
    )
    await h.dispatcher.handle(await delivery(h, receipt), worker_id="resume-worker")
    failed = await h.tasks.get(receipt.task.id)
    assert failed.status == "failed"
    assert failed.error_code == "resume_checkpoint_changed"
    assert failed.attempt_no == 0
    assert h.runtime.resume_calls == []


@pytest.mark.asyncio
async def test_duplicate_running_resume_cannot_fail_the_active_owner(harness):
    h = harness
    original = await prepare_resumable_session(h)
    receipt = await h.dispatcher.submit_resume(
        h.session_id,
        ResumeSessionRequest(client_request_id="resume-running-duplicate"),
    )
    claimed = await h.tasks.claim(receipt.task.id, worker_id="active-resume-worker")
    await h.tasks.begin(
        claimed,
        resource_id=f"turn:{receipt.task.id}",
        fencing_token=1,
    )
    await h.store.save_snapshot(
        h.session_id,
        SessionSnapshot(
            id=str(uuid4()),
            session_id=h.session_id,
            version=original.version + 1,
            stage="completed",
            created_at=datetime.now(timezone.utc),
            graph_state={"turn_id": original.graph_state["turn_id"]},
        ),
    )
    with pytest.raises(TaskDeferred, match="owned by another worker"):
        await h.dispatcher.handle(
            await delivery(h, receipt),
            worker_id="duplicate-worker",
        )
    still_running = await h.tasks.get(receipt.task.id)
    assert still_running.status == "running"
    assert still_running.worker_id == "active-resume-worker"


@pytest.mark.asyncio
async def test_http_approval_resolution_is_atomic_idempotent_and_worker_owned(harness):
    h = harness
    approval = await prepare_pending_approval(h)
    payload = ApprovalDecisionRequest(
        decision=ToolApprovalStatus.approved,
        reason="approved for the controlled test",
        client_request_id="approval-request-1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="http://test",
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    f"/sessions/{h.session_id}/approvals/{approval.id}",
                    json=payload.model_dump(mode="json"),
                )
                for _ in range(6)
            )
        )
    assert {response.status_code for response in responses} == {202}
    task_ids = {
        response.json()["task"]["id"]
        for response in responses
        if "task" in response.json()
    }
    assert len(task_ids) == 1
    task_id = next(iter(task_ids))
    body = responses[0].json()
    assert body["task"]["operation"] == "approval_resume"
    assert body["task"]["reference_id"] == approval.id
    assert h.runtime.approval_calls == []
    approvals = await h.store.list_approvals(h.session_id)
    assert approvals[0].status == ToolApprovalStatus.approved
    events = await h.store.list_events(h.session_id)
    assert [event.type for event in events].count("approval.resolved") == 1
    assert [event.type for event in events].count("approval.continuation_queued") == 1
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS count FROM {h.settings.database.postgres_task_outbox_table}"
        )
        assert cur.fetchone()["count"] == 1
    assert await h.outbox.relay_once(h.queue) == 1
    worker = RedisTaskWorker(
        h.queue,
        consumer="approval-worker",
        handler=lambda item: h.dispatcher.handle(item, worker_id="approval-worker"),
        block_ms=0,
    )
    assert await worker.run_once() == 1
    completed = await h.tasks.get(task_id)
    assert completed.status == "completed"
    assert completed.operation == "approval_resume"
    assert completed.user_message_id is None
    assert h.runtime.approval_calls == [approval.id]
    session = await h.store.get_session(h.session_id)
    assert session.messages[-1].content == "worker-approval-result"
    assert "turn_lease_token" not in session.metadata


@pytest.mark.asyncio
async def test_headless_http_submission_creates_session_task_and_outbox_atomically(harness):
    h = harness
    request = HeadlessExecutionRequest(
        title="Headless durable task",
        content="execute in an independent worker",
        client_request_id="headless-request-1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="http://test",
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/sessions/headless/execute",
                    json=request.model_dump(mode="json"),
                )
                for _ in range(6)
            )
        )
        conflict = await client.post(
            "/sessions/headless/execute",
            json=request.model_copy(update={"content": "different"}).model_dump(mode="json"),
        )
    assert {response.status_code for response in responses} == {202}
    assert conflict.status_code == 409
    task_ids = {response.json()["task"]["id"] for response in responses}
    session_ids = {response.json()["task"]["session_id"] for response in responses}
    assert len(task_ids) == len(session_ids) == 1
    task_id, session_id = next(iter(task_ids)), next(iter(session_ids))
    assert session_id != h.session_id
    assert responses[0].json()["session"]["runtime_mode"] == "headless"
    assert h.runtime.calls == []
    session = await h.store.get_session(session_id)
    assert [message.content for message in session.messages] == [request.content]
    events = await h.store.list_events(session_id)
    assert [event.type for event in events] == ["session.created", "input.queued"]
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS count FROM {h.settings.database.postgres_task_outbox_table} "
            "WHERE event_key=%s",
            (f"agent_turn:{task_id}",),
        )
        assert cur.fetchone()["count"] == 1

    assert await h.outbox.relay_once(h.queue) == 1
    worker = RedisTaskWorker(
        h.queue,
        consumer="headless-worker",
        handler=lambda item: h.dispatcher.handle(item, worker_id="headless-worker"),
        block_ms=0,
    )
    assert await worker.run_once() == 1
    completed = await h.tasks.get(task_id)
    assert completed.status == "completed"
    assert h.runtime.calls == [completed.turn_id]
    persisted = await h.store.get_session(session_id)
    assert [message.content for message in persisted.messages] == [
        request.content,
        "worker-result",
    ]


@pytest.mark.asyncio
async def test_headless_transaction_failure_rolls_back_new_session(harness, monkeypatch):
    h = harness

    def fail(*args, **kwargs):
        raise RuntimeError("injected headless event failure")

    monkeypatch.setattr(h.store, "append_event_in_transaction", fail)
    with pytest.raises(RuntimeError, match="headless event failure"):
        await h.dispatcher.submit_headless(
            HeadlessExecutionRequest(
                content="must roll back",
                client_request_id="headless-rollback",
            )
        )
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS count FROM {h.settings.database.postgres_session_table}")
        assert cur.fetchone()["count"] == 1
        cur.execute(f"SELECT count(*) AS count FROM {h.settings.database.postgres_agent_task_table}")
        assert cur.fetchone()["count"] == 0
        cur.execute(f"SELECT count(*) AS count FROM {h.settings.database.postgres_task_outbox_table}")
        assert cur.fetchone()["count"] == 0


@pytest.mark.asyncio
async def test_submission_failure_rolls_back_input_event_and_outbox(harness, monkeypatch):
    h = harness
    def fail(*args, **kwargs):
        raise RuntimeError("injected event transaction failure")
    monkeypatch.setattr(h.store, "append_event_in_transaction", fail)
    with pytest.raises(RuntimeError, match="injected"):
        await h.dispatcher.submit(h.session_id, payload())
    assert await h.tasks.list_session(h.session_id) == []
    assert (await h.store.get_session(h.session_id)).messages == []
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS count FROM {h.settings.database.postgres_task_outbox_table}")
        assert cur.fetchone()["count"] == 0


@pytest.mark.asyncio
async def test_finalizing_old_session_snapshot_does_not_rewind_renewed_lease(harness):
    h = harness
    receipt = await h.dispatcher.submit(h.session_id, payload())
    claim = await h.tasks.claim(receipt.task.id, worker_id="worker-a")
    old = await h.store.get_session(h.session_id)
    assert await h.store.renew_turn_execution(h.session_id, claim.lease_token, 300)
    renewed = (await h.store.get_session(h.session_id)).metadata["turn_lease_expires_at"]
    await h.store.save_session(old, turn_lease_token=claim.lease_token)
    assert (await h.store.get_session(h.session_id)).metadata["turn_lease_expires_at"] == renewed


@pytest.mark.asyncio
async def test_worker_commits_result_before_ack_and_ignores_duplicate_delivery(harness):
    h = harness
    with bind_request_context(
        request_id="req-agent-test",
        trace_id="trace-agent-test",
        run_id="run-agent-test",
        run_item_id="item-agent-test",
    ):
        receipt = await h.dispatcher.submit(h.session_id, payload())
    assert receipt.task.request_id == "req-agent-test"
    assert receipt.task.trace_id == "trace-agent-test"
    assert receipt.task.run_id == "run-agent-test"
    assert receipt.task.run_item_id == "item-agent-test"
    assert receipt.task.priority == 50
    assert await h.outbox.relay_once(h.queue) == 1
    worker = RedisTaskWorker(
        h.queue,
        consumer="worker-a",
        handler=lambda task: h.dispatcher.handle(task, worker_id="worker-a"),
        block_ms=0,
    )
    assert await worker.run_once() == 1
    await h.dispatcher.handle(await delivery(h, receipt), worker_id="worker-b")
    assert h.runtime.calls == [receipt.task.turn_id]
    completed = await h.tasks.get(receipt.task.id)
    assert completed.status == "completed"
    assert completed.resource_id == f"turn:{receipt.task.id}"
    assert completed.fencing_token is not None
    assert (await h.queue._client.xpending(h.queue.stream, h.queue.group))["pending"] == 0
    session = await h.store.get_session(h.session_id)
    assert [m.content for m in session.messages] == ["first", "worker-result"]
    assert "turn_lease_token" not in session.metadata
    events = await h.store.list_events(h.session_id)
    assert all(
        e.attempt_id
        and e.worker_id == "worker-a"
        and e.run_id == "run-agent-test"
        and e.run_item_id == "item-agent-test"
        and e.resource_id == f"turn:{receipt.task.id}"
        for e in events
        if e.type == "turn.started"
    )


@pytest.mark.asyncio
async def test_admission_wait_creates_new_attempt_and_fifo_excludes_future_input(harness):
    h = harness
    first = await h.dispatcher.submit(h.session_id, payload())
    second = await h.dispatcher.submit(h.session_id, payload("second", "client-2", submit_mode="queued"))
    second_delivery = await delivery(h, second)
    with pytest.raises(TaskDeferred):
        await h.dispatcher.handle(second_delivery, worker_id="worker-b")
    occupied = await h.resources.acquire(
        resource_type="agent",
        resource_id="occupied",
        worker_id="other-worker",
        lease_seconds=30,
    )
    command = await delivery(h, first)
    with pytest.raises(TaskDeferred):
        await h.dispatcher.handle(command, worker_id="worker-a")
    waiting = await h.tasks.get(first.task.id)
    assert waiting.status == "waiting_resource" and waiting.waiting_reason
    assert (await h.store.get_session(h.session_id)).status == SessionStatus.idle
    assert h.runtime.calls == []
    await h.resources.retire(occupied)
    await h.dispatcher.handle(command, worker_id="worker-a")
    completed = await h.tasks.get(first.task.id)
    assert completed.attempt_id != waiting.attempt_id and completed.attempt_no == 2
    await h.dispatcher.handle(second_delivery, worker_id="worker-b")
    assert "second" not in h.runtime.messages[0]
    assert "second" in h.runtime.messages[1]
    assert not h.sessions._queue_drain_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_expired_owner_retries_only_before_runtime_started(harness, started):
    h = harness
    receipt = await h.dispatcher.submit(h.session_id, payload())
    claim = await h.tasks.claim(receipt.task.id, worker_id="dead-worker")
    if started:
        await h.tasks.begin(
            claim,
            resource_id=f"turn:{receipt.task.id}",
            fencing_token=1,
        )
    assert await h.store.renew_turn_execution(h.session_id, claim.lease_token, 0)
    await h.dispatcher.handle(await delivery(h, receipt), worker_id="recovery-worker")
    result = await h.tasks.get(receipt.task.id)
    assert result.status == ("recovery_required" if started else "completed")
    assert len(h.runtime.calls) == (0 if started else 1)
    assert result.attempt_no == (1 if started else 2)


@pytest.mark.asyncio
async def test_other_api_instance_can_interrupt_worker_and_cancel_queued_input(harness):
    h = harness
    receipt = await h.dispatcher.submit(h.session_id, payload())
    h.runtime.finish = asyncio.Event()
    running = asyncio.create_task(h.dispatcher.handle(await delivery(h, receipt), worker_id="worker-a"))
    try:
        await asyncio.wait_for(h.runtime.entered.wait(), 3)
        _, _, _, api = build_services(h.settings, Runtime(), h.resources)
        await api.interrupt(h.session_id, InterruptSessionRequest())
        await asyncio.wait_for(running, 3)
        assert (await h.tasks.get(receipt.task.id)).status == "interrupted"
        queued = await api.submit(h.session_id, payload("next", "client-2", submit_mode="queued"))
        await api.interrupt(h.session_id, InterruptSessionRequest())
        assert (await h.tasks.get(queued.task.id)).status == "cancelled"
        await h.dispatcher.handle(await delivery(h, queued), worker_id="worker-a")
        assert len(h.runtime.calls) == 1
    finally:
        h.runtime.finish.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


def worker_process(settings, prefix, entered=None):
    async def run():
        resources = RedisResourceLeaseManager(settings.database.redis_url, key_prefix=prefix)
        await resources.connect()
        runtime = Runtime()
        if entered is not None:
            class BlockingRuntime(Runtime):
                async def execute_turn(self, *args, **kwargs):
                    entered.set()
                    await asyncio.Event().wait()
            runtime = BlockingRuntime()
        _, _, _, dispatcher = build_services(settings, runtime, resources)
        queue = RedisTaskQueue(
            settings.database.redis_url,
            stream=settings.orchestration.agent_task_stream,
            group="test",
        )
        await queue.connect()
        try:
            worker = RedisTaskWorker(
                queue,
                consumer="separate-worker",
                handler=lambda task: dispatcher.handle(
                    task,
                    worker_id="separate-worker",
                ),
                block_ms=1,
            )
            assert await worker.run_once() == 1
        finally:
            await queue.close()
            await resources.close()
    asyncio.run(run())


@pytest.mark.asyncio
async def test_separate_worker_process_executes_persisted_api_input(harness):
    h = harness
    receipt = await h.dispatcher.submit(h.session_id, payload())
    assert await h.outbox.relay_once(h.queue) == 1
    process = multiprocessing.get_context("spawn").Process(target=worker_process, args=(h.settings, h.prefix))
    try:
        process.start()
        await asyncio.to_thread(process.join, 15)
        assert process.exitcode == 0
        result = await h.tasks.get(receipt.task.id)
        assert result.status == "completed" and result.worker_id == "separate-worker"
        assert (await h.store.get_session(h.session_id)).messages[-1].content == "worker-result"
        assert h.runtime.calls == []
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)


@pytest.mark.asyncio
async def test_separate_worker_process_executes_persisted_resume(harness):
    h = harness
    snapshot = await prepare_resumable_session(h)
    receipt = await h.dispatcher.submit_resume(
        h.session_id,
        ResumeSessionRequest(
            reason="cross-process resume",
            client_request_id="resume-separate-worker",
        ),
    )
    assert await h.outbox.relay_once(h.queue) == 1
    process = multiprocessing.get_context("spawn").Process(
        target=worker_process,
        args=(h.settings, h.prefix),
    )
    try:
        process.start()
        await asyncio.to_thread(process.join, 15)
        assert process.exitcode == 0
        result = await h.tasks.get(receipt.task.id)
        assert result.status == "completed"
        assert result.operation == "manual_resume"
        assert result.reference_id == snapshot.id
        assert result.worker_id == "separate-worker"
        session = await h.store.get_session(h.session_id)
        assert session.messages[-1].content == "worker-resume-result"
        assert h.runtime.resume_calls == []
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)


@pytest.mark.asyncio
async def test_separate_worker_process_executes_persisted_approval(harness):
    h = harness
    approval = await prepare_pending_approval(h)
    receipt = await h.dispatcher.submit_approval(
        h.session_id,
        approval.id,
        ApprovalDecisionRequest(
            decision=ToolApprovalStatus.approved,
            reason="cross-process approval",
            client_request_id="approval-separate-worker",
        ),
    )
    assert await h.outbox.relay_once(h.queue) == 1
    process = multiprocessing.get_context("spawn").Process(
        target=worker_process,
        args=(h.settings, h.prefix),
    )
    try:
        process.start()
        await asyncio.to_thread(process.join, 15)
        assert process.exitcode == 0
        result = await h.tasks.get(receipt.task.id)
        assert result.status == "completed"
        assert result.operation == "approval_resume"
        assert result.reference_id == approval.id
        assert result.worker_id == "separate-worker"
        session = await h.store.get_session(h.session_id)
        assert session.messages[-1].content == "worker-approval-result"
        assert h.runtime.approval_calls == []
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)


@pytest.mark.asyncio
async def test_killed_worker_pending_is_reclaimed_without_replaying_started_agent(harness):
    h = harness
    receipt = await h.dispatcher.submit(h.session_id, payload())
    assert await h.outbox.relay_once(h.queue) == 1
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=worker_process, args=(h.settings, h.prefix, entered))
    try:
        process.start()
        assert await asyncio.to_thread(entered.wait, 15)
        running = await h.tasks.get(receipt.task.id)
        assert running.status == "running"
        process.terminate()
        await asyncio.to_thread(process.join, 5)
        assert process.exitcode is not None
        assert await h.store.renew_turn_execution(h.session_id, running.lease_token, 0)
        recovery = RedisTaskWorker(h.queue, consumer="recovery-worker", reclaim_idle_ms=1,
            handler=lambda task: h.dispatcher.handle(task, worker_id="recovery-worker"), block_ms=0)
        assert await recovery.run_once() == 1
        result = await h.tasks.get(receipt.task.id)
        assert result.status == "recovery_required" and result.attempt_no == 1
        assert h.runtime.calls == []
        assert (await h.redis.xpending(h.queue.stream, h.queue.group))["pending"] == 0
        assert (await h.store.get_session(h.session_id)).status == SessionStatus.interrupted
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)
