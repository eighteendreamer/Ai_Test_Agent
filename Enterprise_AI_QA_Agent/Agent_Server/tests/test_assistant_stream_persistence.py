from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from scripts.assistant_stream_browser_probe import (
    EXPECTED_ANSWER,
    SESSION_ID,
    create_app,
)
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.sessions.session_service import SessionService
from src.infrastructure.redis_hot_memory_store import RedisHotMemoryStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.session_assistant_stream import SessionAssistantStream
from src.runtime.store import ContinuationLeaseLostError, InMemorySessionStore
from src.schemas.session import SessionStatus
from tests import test_session_event_delivery

stores = test_session_event_delivery.stores


def test_fragmented_model_output_coalesces_slow_store_writes(monkeypatch):
    """Replay the existing formal model/tool probe as character-sized SDK chunks."""
    emit = ModelRuntimeService._emit_stream_chunk
    append = InMemorySessionStore.append_event
    delta_writes = []
    callback_seconds = []

    async def fragmented_emit(self, text):
        before = time.perf_counter()
        for character in text:
            await emit(self, character)
        callback_seconds.append(time.perf_counter() - before)

    async def slow_append(self, session_id, event, **fence):
        if event.type == "assistant.stream.delta":
            await asyncio.sleep(0.005)
            delta_writes.append(event)
        return await append(self, session_id, event, **fence)

    monkeypatch.setattr(ModelRuntimeService, "_emit_stream_chunk", fragmented_emit)
    monkeypatch.setattr(InMemorySessionStore, "append_event", slow_append)
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/__test__/assistant-stream/start").status_code == 202
        deadline = time.monotonic() + 10
        while True:
            health = client.get("/api/v1/__test__/health").json()
            if health["status"] in {"completed", "failed"} or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert health["status"] == "completed", health
        events = client.get(f"/api/v1/sessions/{SESSION_ID}/events/history?limit=500").json()
        deltas = [item for item in events if item["type"] == "assistant.stream.delta"]
        assert "".join(item["payload"]["delta"] for item in deltas) == EXPECTED_ANSWER
        assert len(deltas) <= 10, {
            "provider_characters": len(EXPECTED_ANSWER),
            "delta_writes": len(delta_writes), "callback_seconds": callback_seconds,
        }
        print({"provider_characters": len(EXPECTED_ANSWER), "delta_writes": len(delta_writes),
               "callback_seconds": callback_seconds})


@pytest.mark.asyncio
async def test_burst_flushes_before_return_and_replays_exact_unicode_text(stores):
    writer, reader, session_id, _ = stores
    service = SessionService(writer, object(), object(), object())
    stream = service._build_stream_chunk_handler(session_id, "stream-turn", "stream-answer")
    chunks = ["首片", *[f"{index}🙂汉e\u0301" * 8 for index in range(300)]]

    async def model():
        await stream(chunks[0])
        first = await reader.list_events(session_id)
        assert [event.type for event in first] == ["assistant.stream.started", "assistant.stream.delta"]
        assert first[1].payload["delta"] == chunks[0]
        for chunk in chunks[1:]:
            await stream(chunk)
        return "model-result"

    assert await stream.run(model) == "model-result"
    events = await reader.list_events(session_id)
    deltas = [event for event in events if event.type == "assistant.stream.delta"]
    assert "".join(event.payload["delta"] for event in deltas) == "".join(chunks)
    assert len(deltas) <= 10
    assert all(len(event.payload["delta"].encode("utf-8")) <= 16384 for event in deltas)
    assert {(event.payload["turn_id"], event.payload["message_id"]) for event in events} == {
        ("stream-turn", "stream-answer"),
    }
    replay = await reader.read_event_page(session_id, limit=100, after_event_id=deltas[0].id)
    assert [event.id for event in replay] == [event.id for event in deltas[1:]]
    assert (await reader.get_session(session_id)).event_count == len(events)


@pytest.mark.asyncio
async def test_idle_model_flushes_a_tail_while_execution_remains_running(stores, monkeypatch):
    writer, reader, session_id, _ = stores
    persisted = asyncio.Event()
    finish = asyncio.Event()
    append = writer.append_event

    async def observed_append(session, event, **fence):
        await append(session, event, **fence)
        if event.payload.get("delta") == "tail":
            persisted.set()

    monkeypatch.setattr(writer, "append_event", observed_append)
    service = SessionService(writer, object(), object(), object())
    stream = service._build_stream_chunk_handler(session_id, "idle-turn", "idle-answer")

    async def model():
        await stream("first")
        await stream("tail")
        await finish.wait()

    running = asyncio.create_task(stream.run(model))
    try:
        await asyncio.wait_for(persisted.wait(), 3)
        assert not running.done()
        events = await reader.list_events(session_id)
        assert "".join(event.payload.get("delta", "") for event in events) == "firsttail"
    finally:
        finish.set()
        await running


@pytest.mark.asyncio
async def test_storage_failure_cancels_an_idle_model_and_is_visible(caplog):
    stopped = asyncio.Event()

    class FailingStore:
        async def append_event(self, session_id, event, **fence):
            if event.payload.get("delta") == "tail":
                raise RuntimeError("injected streaming persistence failure")

    service = SessionService(FailingStore(), object(), object(), object())
    stream = service._build_stream_chunk_handler("s", "t", "m")

    async def model():
        try:
            await stream("first")
            await stream("tail")
            await asyncio.Event().wait()
        finally:
            stopped.set()

    with pytest.raises(RuntimeError, match="injected streaming persistence failure"):
        await asyncio.wait_for(stream.run(model), 3)
    assert stopped.is_set()
    assert any(record.message == "assistant_stream_persistence_failed" and record.session_id == "s"
               for record in caplog.records)


@pytest.mark.asyncio
async def test_slow_store_backpressure_is_bounded_and_preserves_all_chunks():
    blocked = asyncio.Event()
    release = asyncio.Event()
    committed = []

    class SlowStore:
        async def append_event(self, session_id, event, **fence):
            if event.payload.get("delta") != "first" and event.type == "assistant.stream.delta":
                blocked.set()
                await release.wait()
            committed.append(event)

    service = SessionService(SlowStore(), object(), object(), object())
    stream = SessionAssistantStream(
        service._store, "s", "t", "m", service._make_event,
        fence={}, batch_bytes=16, pending_bytes=32, capacity=4,
    )
    produced = []

    async def model():
        await stream("first")
        for index in range(100):
            await stream("汉" * 8)
            produced.append(index)

    running = asyncio.create_task(stream.run(model))
    try:
        await asyncio.wait_for(blocked.wait(), 3)
        assert not running.done() and len(produced) < 100
        assert len(stream._pending) <= 4
        assert stream._pending_bytes < 32 + len(("汉" * 8).encode("utf-8"))
    finally:
        release.set()
        await running
    assert "".join(event.payload.get("delta", "") for event in committed) == "first" + "汉" * 800


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_accepted_tail_and_writer_cleanup():
    writing = asyncio.Event()
    release = asyncio.Event()
    stopped = asyncio.Event()
    committed = []

    class SlowStore:
        async def append_event(self, session_id, event, **fence):
            assert fence["turn_lease_token"] == "owner-token"
            if event.payload.get("delta") == "tail":
                writing.set()
                await release.wait()
            committed.append(event)

    service = SessionService(SlowStore(), object(), object(), object())
    stream = service._build_stream_chunk_handler("s", "t", "m", turn_lease_token="owner-token")

    async def model():
        try:
            await stream("first")
            await stream("tail")
            await asyncio.Event().wait()
        finally:
            stopped.set()

    running = asyncio.create_task(stream.run(model))
    await asyncio.wait_for(writing.wait(), 3)
    running.cancel()
    await asyncio.wait_for(stopped.wait(), 3)
    running.cancel()
    await asyncio.sleep(0)
    assert not running.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert "".join(event.payload.get("delta", "") for event in committed) == "firsttail"
    assert not stream._running and not stream._pending


@pytest.mark.asyncio
async def test_provider_error_flushes_accepted_prefix_without_publishing_completion(stores):
    writer, reader, session_id, _ = stores
    service = SessionService(writer, object(), object(), object())
    stream = service._build_stream_chunk_handler(session_id, "error-turn", "error-answer")

    async def model():
        await stream("first")
        await stream("tail")
        raise ValueError("injected provider disconnect")

    with pytest.raises(ValueError, match="injected provider disconnect"):
        await stream.run(model)
    events = await reader.list_events(session_id)
    assert "".join(event.payload.get("delta", "") for event in events) == "firsttail"
    assert not any(event.type.endswith("completed") for event in events)


@pytest.mark.asyncio
async def test_empty_stream_creates_no_events_and_rejects_late_chunks():
    store = InMemorySessionStore()
    service = SessionService(store, object(), object(), object())
    stream = service._build_stream_chunk_handler("s", "t", "m")

    async def model():
        await stream("")
        return "empty"

    assert await stream.run(model) == "empty"
    with pytest.raises(RuntimeError, match="execution scope"):
        await stream("late")
    with pytest.raises(RuntimeError, match="reused"):
        await stream.run(model)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_type", ["assistant.stream.started", "assistant.stream.delta"])
async def test_first_commit_failure_stops_execution_and_cannot_be_replayed(failed_type):
    stopped = asyncio.Event()

    class FailingStore:
        async def append_event(self, session_id, event, **fence):
            if event.type == failed_type:
                raise RuntimeError("injected first commit failure")

    service = SessionService(FailingStore(), object(), object(), object())
    stream = service._build_stream_chunk_handler("s", "t", "m")

    async def model():
        try:
            await stream("first")
            pytest.fail("First failed write must never report callback success")
        finally:
            stopped.set()

    with pytest.raises(RuntimeError, match="injected first commit failure"):
        await asyncio.wait_for(stream.run(model), 3)
    assert stopped.is_set() and not stream._running
    with pytest.raises(RuntimeError, match="reused"):
        await stream.run(model)


@pytest.mark.asyncio
async def test_buffered_tail_cannot_write_after_execution_lease_is_replaced(stores):
    writer, reader, session_id, _ = stores
    assert await writer.claim_turn_execution(session_id, "old-turn", "old-worker", "old-token", 30)
    service = SessionService(writer, object(), object(), object())
    stream = SessionAssistantStream(
        writer, session_id, "old-turn", "old-answer", service._make_event,
        fence={"turn_lease_token": "old-token"}, interval_seconds=30,
    )
    queued = asyncio.Event()
    finish = asyncio.Event()

    async def model():
        await stream("first")
        await stream("stale-tail")
        queued.set()
        await finish.wait()

    running = asyncio.create_task(stream.run(model))
    try:
        await asyncio.wait_for(queued.wait(), 3)
        assert await reader.complete_turn_execution(session_id, "old-token")
        session = await reader.get_session(session_id)
        session.status = SessionStatus.idle
        await reader.save_session(session)
        assert await reader.claim_turn_execution(session_id, "new-turn", "new-worker", "new-token", 30)
    finally:
        finish.set()
    with pytest.raises(ContinuationLeaseLostError):
        await asyncio.wait_for(running, 3)
    events = await reader.list_events(session_id)
    assert "".join(event.payload.get("delta", "") for event in events) == "first"
    assert (await reader.get_session(session_id)).metadata["turn_lease_token"] == "new-token"


@pytest.mark.asyncio
async def test_postgres_and_redis_publish_the_same_coalesced_facts(stores):
    writer, reader, session_id, settings = stores
    if not isinstance(writer, PostgresSessionStore):
        pytest.skip("requires the opt-in PostgreSQL fixture and live Redis")
    hot = RedisHotMemoryStore(settings.database.redis_url, ttl_seconds=120)
    await hot.connect()
    writer._hot_memory_store = hot
    try:
        service = SessionService(writer, object(), object(), object())
        stream = service._build_stream_chunk_handler(session_id, "hot-turn", "hot-answer")
        chunks = ["first", *["后续🙂" for _ in range(500)]]

        async def model():
            for chunk in chunks:
                await stream(chunk)

        await stream.run(model)
        facts = await reader.list_events(session_id)
        cached = await hot.list_events(session_id)
        assert [event.id for event in facts] == [event["id"] for event in cached]
        assert [event.payload for event in facts] == [event["payload"] for event in cached]
        assert "".join(event["payload"].get("delta", "") for event in cached) == "".join(chunks)
        assert sum(event.type == "assistant.stream.delta" for event in facts) <= 10
    finally:
        writer._hot_memory_store = None
        await hot.clear_session(session_id)
        await hot.close()
