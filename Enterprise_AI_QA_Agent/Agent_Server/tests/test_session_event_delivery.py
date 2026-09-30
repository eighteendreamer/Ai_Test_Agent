from __future__ import annotations

import asyncio
import json
import multiprocessing
import socket
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
import httpx
import uvicorn
from fastapi import FastAPI

from src.api.routes.sessions import router, stream_events
from src.application.sessions.session_service import SessionService
from src.application.runtime.runtime_service import RuntimeService
from src.core.config import get_settings
from src.domain.models import SessionRecord
from src.runtime.execution_logging import append_graph_event
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.store import InMemorySessionStore
from src.runtime.session_event_sink import SessionEventSink
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.session import ChatMessage, ExecutionEvent, MessageRole, RuntimeMode, SessionMode, SessionSnapshot, SessionStatus
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _drop_tables


@pytest_asyncio.fixture(params=["memory", "postgres"])
async def stores(request):
    settings = get_settings().model_copy(deep=True)
    tables = []
    if request.param == "postgres":
        if not LivePostgresTestConfig().run_live_postgres_tests:
            pytest.skip("set RUN_LIVE_POSTGRES_TESTS=1 for event delivery integration")
        suffix = uuid4().hex[:12]
        for name in ("session", "message", "event", "snapshot", "approval"):
            table = f"live_delivery_{name}_{suffix}"
            setattr(settings.database, f"postgres_{name}_table", table)
            tables.append(table)
        writer, reader = PostgresSessionStore(settings), PostgresSessionStore(settings)
    else:
        writer = reader = InMemorySessionStore()
    try:
        await writer.initialize()
        now = datetime.now(timezone.utc)
        session = SessionRecord(id="session-" + uuid4().hex, title="event delivery", status=SessionStatus.idle,
            session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive, mode_key="default",
            created_at=now, updated_at=now)
        await writer.save_session(session)
        yield writer, reader, session.id, settings
    finally:
        if tables:
            _drop_tables(settings, tables)


def event(session_id, number, *, timestamp=None):
    return ExecutionEvent(id=f"{session_id}-e{number}", type="test.event", session_id=session_id,
        timestamp=timestamp or datetime.now(timezone.utc), payload={"number": number})


@pytest.mark.asyncio
async def test_cursor_follows_append_order_even_with_late_and_equal_timestamps(stores):
    writer, reader, session_id, _ = stores
    now = datetime.now(timezone.utc)
    await writer.append_event(session_id, event(session_id, 9, timestamp=now))
    await writer.append_event(session_id, event(session_id, 1, timestamp=now - timedelta(hours=1)))
    await writer.append_event(session_id, event(session_id, 0, timestamp=now))
    result = await reader.list_events(session_id, after_event_id=f"{session_id}-e9", limit=1)
    assert [item.id for item in result] == [f"{session_id}-e1"]


@pytest.mark.asyncio
async def test_repeated_event_id_is_idempotent_and_does_not_inflate_counts(stores):
    writer, reader, session_id, _ = stores
    value = event(session_id, 1)
    await writer.append_event(session_id, value)
    await writer.append_event(session_id, value)
    assert len(await reader.list_events(session_id)) == 1
    assert (await reader.get_session(session_id)).event_count == 1


@pytest.mark.asyncio
async def test_final_response_commit_failure_cannot_publish_completion_events():
    class DetachedFailStore(InMemorySessionStore):
        fail_final = False

        async def get_session(self, session_id):
            session = await super().get_session(session_id)
            return deepcopy(session) if session is not None else None

        async def save_session(self, session, **fence):
            if self.fail_final:
                raise RuntimeError("injected final session write failure")
            return await super().save_session(deepcopy(session), **fence)

        async def save_session_with_events(self, session, events, **fence):
            if self.fail_final:
                raise RuntimeError("injected final session write failure")
            return await super().save_session_with_events(deepcopy(session), events, **fence)

    store = DetachedFailStore()
    now = datetime.now(timezone.utc)
    original = SessionRecord(
        id="session-atomic-final", title="atomic final", status=SessionStatus.running,
        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
        mode_key="default", created_at=now, updated_at=now,
    )
    await store.save_session(original)
    session = await store.get_session(original.id)
    service = SessionService(store, object(), object(), object())
    runtime_result = SimpleNamespace(
        events=[], tool_messages=[], approvals=[], pending_turn={}, output_text="final answer",
        snapshot=SessionSnapshot(
            id="snapshot-final", session_id=original.id, version=1,
            stage="completed", created_at=now, graph_state={},
        ),
        state={
            "turn_id": "turn-final", "trace_id": "trace-final",
            "selected_agent_key": "agent", "selected_agent_name": "Agent",
            "selected_model_key": "model", "selected_model_name": "Model",
            "tool_results": [], "context_bundle": {},
            "model_response_summary": {}, "termination_reason": "assistant_final",
        },
    )
    store.fail_final = True

    with pytest.raises(RuntimeError, match="injected final session write failure"):
        await service._finalize_runtime_result(
            session, runtime_result, "assistant-final", "user request"
        )

    persisted = await store.get_session(original.id)
    assert persisted.messages == []
    assert not {
        "assistant.stream.completed", "assistant.response_generated", "turn.completed"
    }.intersection(event.type for event in await store.list_events(original.id))


@pytest.mark.asyncio
async def test_final_response_and_completion_events_commit_together(stores):
    writer, reader, session_id, _ = stores
    session = deepcopy(await writer.get_session(session_id))
    session.status = SessionStatus.completed
    session.messages.append(ChatMessage(
        id="assistant-atomic", role=MessageRole.assistant,
        content="committed answer", created_at=datetime.now(timezone.utc),
    ))
    final_events = [
        ExecutionEvent(
            id=f"{session_id}-final-{number}", type=event_type,
            session_id=session_id, timestamp=datetime.now(timezone.utc), payload={},
        )
        for number, event_type in enumerate((
            "assistant.stream.completed", "assistant.response_generated", "turn.completed"
        ))
    ]

    await writer.save_session_with_events(session, final_events)

    persisted = await reader.get_session(session_id)
    assert persisted.status == SessionStatus.completed
    assert [(item.role, item.content) for item in persisted.messages] == [
        (MessageRole.assistant, "committed answer")
    ]
    assert [item.type for item in await reader.list_events(session_id)] == [
        item.type for item in final_events
    ]
    assert [item.id for item in await reader.read_event_page(session_id, limit=10)] == [
        item.id for item in final_events
    ]
    assert persisted.event_count == len(final_events)


@pytest.mark.asyncio
async def test_final_response_transaction_rolls_back_when_second_event_fails(stores, monkeypatch):
    writer, reader, session_id, _ = stores
    session = deepcopy(await writer.get_session(session_id))
    session.status = SessionStatus.completed
    session.messages.append(ChatMessage(
        id="assistant-rollback", role=MessageRole.assistant,
        content="must roll back", created_at=datetime.now(timezone.utc),
    ))
    final_events = [event(session_id, number) for number in (1, 2)]
    if isinstance(writer, PostgresSessionStore):
        original_append = writer.append_event_in_transaction

        def append_with_failure(cur, sid, value, *fence):
            if value.id == final_events[1].id:
                raise RuntimeError("injected second event failure")
            return original_append(cur, sid, value, *fence)

        monkeypatch.setattr(writer, "append_event_in_transaction", append_with_failure)
    else:
        original_append = writer._append_event_locked

        def append_with_failure(sid, value):
            if value.id == final_events[1].id:
                raise RuntimeError("injected second event failure")
            return original_append(sid, value)

        monkeypatch.setattr(writer, "_append_event_locked", append_with_failure)

    with pytest.raises(RuntimeError, match="injected second event failure"):
        await writer.save_session_with_events(session, final_events)

    persisted = await reader.get_session(session_id)
    assert persisted.status == SessionStatus.idle
    assert persisted.messages == []
    assert persisted.event_count == 0
    assert await reader.list_events(session_id) == []


@pytest.mark.asyncio
async def test_projection_status_and_notification_event_commit_once(stores, monkeypatch):
    writer, reader, session_id, _ = stores
    session = deepcopy(await writer.get_session(session_id))
    session.metadata["worker_dispatches"] = [{"task_id": "worker-1", "status": "running"}]
    await writer.save_session(session)
    notification = ExecutionEvent(
        id=f"{session_id}-worker-notification", type="worker.task_notification_received",
        session_id=session_id, timestamp=datetime.now(timezone.utc), payload={"task_id": "worker-1"},
    )

    def mark_completed(metadata):
        record = metadata["worker_dispatches"][0]
        if record["status"] == "completed":
            return False
        record["status"] = "completed"
        return True

    if isinstance(writer, PostgresSessionStore):
        original_append = writer.append_event_in_transaction

        def fail_append(*args, **kwargs):
            raise RuntimeError("injected projection event failure")

        monkeypatch.setattr(writer, "append_event_in_transaction", fail_append)
    else:
        original_append = writer._append_event_locked

        def fail_append(*args, **kwargs):
            raise RuntimeError("injected projection event failure")

        monkeypatch.setattr(writer, "_append_event_locked", fail_append)

    with pytest.raises(RuntimeError, match="injected projection event failure"):
        await writer.update_metadata_and_append_event(session_id, mark_completed, notification)
    assert (await reader.get_session(session_id)).metadata["worker_dispatches"][0]["status"] == "running"
    assert await reader.list_events(session_id) == []

    monkeypatch.setattr(
        writer,
        "append_event_in_transaction" if isinstance(writer, PostgresSessionStore) else "_append_event_locked",
        original_append,
    )
    assert await writer.update_metadata_and_append_event(session_id, mark_completed, notification)
    assert not await writer.update_metadata_and_append_event(session_id, mark_completed, notification)
    assert (await reader.get_session(session_id)).metadata["worker_dispatches"][0]["status"] == "completed"
    assert [item.type for item in await reader.list_events(session_id)] == [notification.type]


def request_for(reader, session_id, settings, cursor=""):
    class Service:
        async def get_session(self, sid): return await reader.get_session(sid)
        async def list_events(self, sid, **kwargs): return await reader.list_events(sid, **kwargs)
        async def list_event_page(self, sid, **kwargs): return await reader.read_event_page(sid, **kwargs)

    async def disconnected(): return False
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_service=Service(), settings=settings)),
        headers={"last-event-id": cursor}, query_params={}, is_disconnected=disconnected)


async def next_event(iterator):
    while True:
        frame = await anext(iterator)
        if frame.startswith("data:") or "\ndata:" in frame:
            return json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))


@pytest.mark.asyncio
async def test_two_sse_clients_each_receive_events_from_another_store_instance(stores):
    writer, reader, session_id, settings = stores
    first = await stream_events(session_id, request_for(reader, session_id, settings))
    second = await stream_events(session_id, request_for(reader, session_id, settings))
    pending = [asyncio.create_task(next_event(response.body_iterator)) for response in (first, second)]
    try:
        await asyncio.sleep(.03)
        await writer.append_event(session_id, event(session_id, 1))
        received = await asyncio.wait_for(asyncio.gather(*pending), 2)
        assert [item["id"] for item in received] == [f"{session_id}-e1"] * 2
    finally:
        for task in pending: task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await first.body_iterator.aclose()
        await second.body_iterator.aclose()


@pytest.mark.asyncio
async def test_graph_live_event_and_finalized_event_have_the_same_identity():
    queue = asyncio.Queue()
    state = {"session_id": "s", "turn_id": "t", "_event_queue": queue}
    append_graph_event(state, "tool.started", "tool", "executing")
    live = queue.get_nowait()
    final = RuntimeService._events_from_log(object(), "s", state["event_log"])[0]
    assert final.id == live.id and final.timestamp == live.timestamp


@pytest.mark.asyncio
async def test_model_deltas_cross_real_sse_http_and_match_persisted_transcript(stores):
    writer, _reader, session_id, settings = stores
    now = datetime.now(UTC)
    session = deepcopy(await writer.get_session(session_id))
    session.status = SessionStatus.running
    session.messages = [ChatMessage(
        id="user-assistant-stream",
        role=MessageRole.user,
        content="生成一段用于验证流传输的回答。",
        created_at=now,
    )]
    await writer.save_session(session)

    service = SessionService(writer, object(), object(), object())
    turn_id = "turn-assistant-stream"
    assistant_message_id = "assistant-stream-result"
    chunks = ["模型", "分片", "传输正常。"]
    on_text_delta = service._build_stream_chunk_handler(
        session_id,
        turn_id,
        assistant_message_id,
    )

    async def deterministic_model_stream():
        for chunk in chunks:
            await on_text_delta(chunk)

    await deterministic_model_stream()
    final_text = "".join(chunks)
    runtime_result = SimpleNamespace(
        events=[],
        tool_messages=[],
        approvals=[],
        pending_turn={},
        output_text=final_text,
        snapshot=SessionSnapshot(
            id="snapshot-assistant-stream",
            session_id=session_id,
            version=1,
            stage="completed",
            created_at=now,
            graph_state={"turn_id": turn_id},
        ),
        state={
            "turn_id": turn_id,
            "trace_id": "trace-assistant-stream",
            "selected_agent_key": "coordinator",
            "selected_agent_name": "Coordinator",
            "selected_model_key": "deterministic-stream-test",
            "selected_model_name": "Deterministic stream test",
            "model_response_summary": {"mode": "ok"},
            "tool_results": [],
            "context_bundle": {},
            "termination_reason": "assistant_final",
        },
    )
    await service._finalize_runtime_result(
        session,
        runtime_result,
        assistant_message_id,
        "生成一段用于验证流传输的回答。",
    )

    app = FastAPI()
    app.state.session_service = service
    app.state.settings = settings
    app.include_router(router, prefix="/api/v1")
    settings.orchestration.session_sse_poll_seconds = 0.02
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    received = []
    try:
        async def wait_for_server():
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_server(), 5)
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            async with client.stream(
                "GET",
                f"http://127.0.0.1:{port}/api/v1/sessions/{session_id}/events",
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event_value = json.loads(line[5:].strip())
                    received.append(event_value)
                    if event_value["type"] == "turn.completed":
                        break

            detail_response = await client.get(
                f"http://127.0.0.1:{port}/api/v1/sessions/{session_id}"
            )
        assert detail_response.status_code == 200
        final_detail = detail_response.json()
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        listener.close()

    stream_types = [item["type"] for item in received]
    assert stream_types[:4] == [
        "assistant.stream.started",
        "assistant.stream.delta",
        "assistant.stream.delta",
        "assistant.stream.delta",
    ]
    assert [
        item["payload"]["delta"]
        for item in received
        if item["type"] == "assistant.stream.delta"
    ] == chunks
    assert stream_types[-3:] == [
        "assistant.stream.completed",
        "assistant.response_generated",
        "turn.completed",
    ]
    final_message = next(
        item for item in final_detail["messages"] if item["id"] == assistant_message_id
    )
    assert final_message["content"] == final_text
    assert final_message["metadata"]["response_mode"] == "ok"


@pytest.mark.asyncio
async def test_sse_reconnect_pages_all_events_and_keeps_session_scope(stores):
    writer, reader, session_id, settings = stores
    settings.orchestration.session_sse_batch_size = 23
    for number in range(1005):
        await writer.append_event(session_id, event(session_id, number))
    response = await stream_events(session_id, request_for(reader, session_id, settings, f"{session_id}-e0"))
    try:
        received = [await asyncio.wait_for(next_event(response.body_iterator), 2) for _ in range(1004)]
        assert [item["payload"]["number"] for item in received] == list(range(1, 1005))
        assert {item["session_id"] for item in received} == {session_id}
    finally:
        await response.body_iterator.aclose()
    # Unknown/foreign cursor resets only this session's history; never stalls.
    reset = await stream_events(session_id, request_for(reader, session_id, settings, "foreign-session-event"))
    try:
        page = [await next_event(reset.body_iterator) for _ in range(2)]
        assert [item["payload"]["number"] for item in page] == [0, 1]
    finally:
        await reset.body_iterator.aclose()


@pytest.mark.asyncio
async def test_runtime_events_are_durable_while_runner_is_still_active(stores):
    writer, reader, session_id, _ = stores
    finish = asyncio.Event()

    async def runtime(*, event_queue):
        state = {"session_id": session_id, "turn_id": "live-turn", "_event_queue": event_queue}
        append_graph_event(state, "tool.started", "tool", "live durable event")
        await finish.wait()
        return RuntimeService._events_from_log(object(), session_id, state["event_log"])

    sink = SessionEventSink(writer, session_id, capacity=10, fence={})
    task = asyncio.create_task(sink.run(runtime))
    try:
        async def wait_for_persisted():
            while not (values := await reader.read_event_page(session_id, limit=10)):
                await asyncio.sleep(.01)
            return values
        persisted = await asyncio.wait_for(wait_for_persisted(), 3)
        assert not task.done()
    finally:
        finish.set()
        final = await task
    await writer.append_event(session_id, final[0])
    assert persisted[0].id == final[0].id
    assert len(await reader.list_events(session_id)) == 1


@pytest.mark.asyncio
async def test_event_persistence_failure_cancels_runner_and_is_not_silenced():
    cancelled = asyncio.Event()

    class Store:
        async def append_event(self, *args, **kwargs): raise RuntimeError("injected event database outage")

    async def runtime(*, event_queue):
        event_queue.put_nowait(event("s", 1))
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(RuntimeError, match="injected event database outage"):
        await asyncio.wait_for(SessionEventSink(Store(), "s", capacity=2, fence={}).run(runtime), 2)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_event_buffer_overflow_is_visible_and_flushes_accepted_events():
    store = InMemorySessionStore()

    async def runtime(*, event_queue):
        state = {"session_id": "s", "_event_queue": event_queue}
        append_graph_event(state, "first", "tool", "one")
        append_graph_event(state, "second", "tool", "two")

    with pytest.raises(RuntimeError, match="persistence buffer is full"):
        await SessionEventSink(store, "s", capacity=1, fence={}).run(runtime)
    assert [item.type for item in await store.list_events("s")] == ["first"]


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_pending_event_write():
    writing, finish, runner_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    persisted = []

    class Store:
        async def append_event(self, session_id, value, **fence):
            assert fence == {"turn_lease_token": "test-owner"}
            writing.set()
            await finish.wait()
            persisted.append(value.id)

    async def runtime(*, event_queue):
        event_queue.put_nowait(event("s", 1))
        try:
            await asyncio.Event().wait()
        finally:
            runner_cancelled.set()

    sink = SessionEventSink(Store(), "s", capacity=10, fence={"turn_lease_token": "test-owner"})
    task = asyncio.create_task(sink.run(runtime))
    try:
        await asyncio.wait_for(writing.wait(), 2)
        task.cancel()
        await asyncio.wait_for(runner_cancelled.wait(), 2)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
    finally:
        finish.set()
        result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert persisted == ["s-e1"]


@pytest.mark.asyncio
async def test_event_sequence_cannot_jump_an_uncommitted_writer(stores):
    writer, reader, session_id, settings = stores
    if not isinstance(writer, PostgresSessionStore):
        pytest.skip("transaction ordering requires PostgreSQL")
    await writer.append_event(session_id, event(session_id, 0))
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {settings.database.postgres_session_table} WHERE id=%s FOR UPDATE", (session_id,))
        first = event(session_id, 1)
        cur.execute(f"INSERT INTO {settings.database.postgres_event_table} (id,session_id,type,timestamp,payload) "
            "VALUES (%s,%s,%s,%s,%s::jsonb)", (first.id, session_id, first.type, first.timestamp, json.dumps(first.payload)))
        pending = asyncio.create_task(writer.append_event(session_id, event(session_id, 2)))
        try:
            await asyncio.sleep(.05)
            assert not pending.done()
            assert await reader.read_event_page(session_id, limit=10, after_event_id=f"{session_id}-e0") == []
        except BaseException:
            pending.cancel()
            raise
    await asyncio.wait_for(pending, 3)
    assert [item.payload["number"] for item in await reader.read_event_page(
        session_id, limit=10, after_event_id=f"{session_id}-e0")] == [1, 2]


def _worker_publish_events(settings, session_id):
    async def run():
        store = PostgresSessionStore(settings)
        async def execute(*, event_queue):
            for number in range(3):
                event_queue.put_nowait(event(session_id, number))
                await asyncio.sleep(.01)
        await SessionEventSink(store, session_id, capacity=10, fence={}).run(execute)
    asyncio.run(run())


@pytest.mark.asyncio
async def test_real_http_clients_receive_events_from_separate_worker_process(stores):
    _, reader, session_id, settings = stores
    if not isinstance(reader, PostgresSessionStore):
        pytest.skip("cross-process delivery requires PostgreSQL")
    settings.orchestration.session_sse_poll_seconds = .05
    request = request_for(reader, session_id, settings)
    app = FastAPI()
    app.state.session_service = request.app.state.session_service
    app.state.settings = settings
    app.include_router(router, prefix="/api/v1")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    process = multiprocessing.get_context("spawn").Process(target=_worker_publish_events, args=(settings, session_id))
    clients = []
    try:
        async def started():
            while not server.started:
                if serving.done(): await serving
                await asyncio.sleep(.01)
        await asyncio.wait_for(started(), 5)
        connected = [asyncio.Event(), asyncio.Event()]
        async def receive(index):
            async with httpx.AsyncClient(timeout=15) as client:
                async with client.stream("GET", f"http://127.0.0.1:{port}/api/v1/sessions/{session_id}/events") as response:
                    assert response.status_code == 200
                    assert response.headers["x-accel-buffering"] == "no"
                    connected[index].set()
                    received = []
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            received.append(json.loads(line[6:]))
                            if len(received) == 3: return received
        clients = [asyncio.create_task(receive(index)) for index in range(2)]
        await asyncio.wait_for(asyncio.gather(*(item.wait() for item in connected)), 5)
        process.start()
        received = await asyncio.wait_for(asyncio.gather(*clients), 15)
        assert received[0] == received[1]
        assert [row["payload"]["number"] for row in received[0]] == [0, 1, 2]
        await asyncio.to_thread(process.join, 5)
        assert process.exitcode == 0
    finally:
        for client in clients: client.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        if process.pid is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        listener.close()
