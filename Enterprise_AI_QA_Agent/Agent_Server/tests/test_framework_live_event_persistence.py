"""Acknowledged graph facts use the existing fenced Session event transaction."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.runtime.runtime_service import RuntimeService
from src.application.sessions.session_service import SessionService
from src.core.request_context import bind_request_context
from src.runtime.execution_context import framework_owner_scope, graph_event_scope
from src.runtime.execution_logging import append_graph_event, append_graph_event_durable
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.session_event_sink import SessionEventSink
from src.runtime.store import ContinuationLeaseLostError, InMemorySessionStore
from src.schemas.session import ExecutionEvent
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, start


def original_event(h):
    return ExecutionEvent(id=uuid4().hex, session_id=h.session_id, type="runtime.audit",
        timestamp=datetime.now(timezone.utc), payload={"turn_id": h.binding.turn_id, "fact": "original"})


@pytest.mark.asyncio
async def test_supplied_intents_keep_original_identity_time_redaction_and_owner_ack(checkpoint_harness, monkeypatch):
    from src.runtime import execution_logging
    from src.infrastructure.storage_utils import ensure_utc_datetime

    h = checkpoint_harness
    await start(h)
    state = {"session_id": h.session_id, "turn_id": h.binding.turn_id, "trace_id": "original-trace", "event_log": []}
    before = deepcopy(state)
    stamp = datetime(2026, 10, 27, 0, 1, 2, tzinfo=timezone.utc)
    def forbidden(*args, **kwargs):
        pytest.fail("Supplied event intents must not read a new clock or generate a random identity")
    monkeypatch.setattr("src.schemas.session.uuid4", forbidden)
    monkeypatch.setattr(execution_logging, "datetime", type("NoClock", (), {"now": staticmethod(forbidden)}))
    event = execution_logging.build_graph_event_intent(state, "runtime.audit", "runtime", "original intent",
        event_id="original-event-intent", timestamp=stamp, detail="password=synthetic-test-marker")
    assert state == before
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with graph_event_scope(sink):
        await execution_logging.append_graph_event_intent_durable(state, event)
        saved = next(item for item in await h.store.list_events(h.session_id) if item.id == event.id)
        assert event.timestamp == stamp and saved.timestamp == ensure_utc_datetime(stamp) and saved.type == event.type
        assert all(saved.payload[key] == value for key, value in event.payload.items())
        assert state["event_log"] == [{"id": event.id, "timestamp": stamp.isoformat(), "type": event.type, "payload": event.payload}]
        assert saved.payload["detail"] == "password=[REDACTED]"
        assert not sink._pending and not sink.deferred_to_final_flush


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["session", "turn", "trace", "step"])
async def test_supplied_intent_cannot_cross_scope_or_reorder_the_original_log(changed):
    from src.runtime.execution_logging import build_graph_event_intent, append_graph_event_intent_durable

    state = {"session_id": "session", "turn_id": "turn", "trace_id": "trace", "event_log": []}
    event = build_graph_event_intent(state, "runtime.audit", "runtime", "original", event_id="original-intent",
        timestamp=datetime(2026, 10, 27, tzinfo=timezone.utc))
    if changed == "session":
        event.session_id = "foreign"
    else:
        event.payload[{"turn": "turn_id", "trace": "trace_id", "step": "step"}[changed]] = "foreign"
    before = deepcopy(state)
    with pytest.raises(ValueError, match="scope or next step"):
        await append_graph_event_intent_durable(state, event)
    assert state == before


@pytest.mark.asyncio
async def test_bound_events_are_committed_in_order_before_producer_continues(checkpoint_harness):
    h = checkpoint_harness
    await start(h)
    service = SessionService(h.store, object(), object(), object())
    with framework_owner_scope(h.owner.runtime_owner):
        sink = service._event_sink(h.session_id, turn_lease_token=h.owner.lease_token)
    assert sink.runtime_owner == h.owner.runtime_owner
    state = {"session_id": h.session_id, "turn_id": h.binding.turn_id}

    async def produce(*, event_queue):
        with graph_event_scope(event_queue):
            for number in range(8):
                await append_graph_event_durable(state, "runtime.audit", "runtime", "original", number=number)
                saved = await h.store.list_events(h.session_id)
                assert saved[-1].id == state["event_log"][-1]["id"]
            # Parallel tool producers share the original event order, rather
            # than relying on PostgreSQL timestamps or random IDs to sort it.
            await asyncio.gather(*(append_graph_event_durable(state, "runtime.audit", "runtime",
                "concurrent original", number=number) for number in range(8, 16)))
        return RuntimeService._events_from_log(object(), h.session_id, state["event_log"])

    sink._capacity = 1
    expected = await sink.run(produce)
    saved = [item for item in await h.store.list_events(h.session_id) if item.id in {e.id for e in expected}]
    assert [item.id for item in saved] == [item.id for item in expected]
    assert [item.payload["step"] for item in saved] == list(range(1, 17))
    assert [item.payload["number"] for item in saved] == list(range(16))
    assert not sink.deferred_to_final_flush and not sink._pending


@pytest.mark.asyncio
async def test_official_subagent_events_are_acknowledged_in_postgres(checkpoint_harness, tmp_path):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeRequest

    h = checkpoint_harness
    await start(h)

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(responses=[AIMessage(content="", tool_calls=[{"id": "delegate", "name": "task",
        "args": {"description": "Read the controlled project", "subagent_type": "code-review-researcher"},
        "type": "tool_call"}]), AIMessage(content="controlled evidence"), AIMessage(content="done")])

    async def resolve(key):
        return model

    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve, checkpointer_factory=h.provider.open)
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    state = {"session_id": h.session_id, "turn_id": h.binding.turn_id}

    async def persist(item):
        await append_graph_event_durable(state, "graph.subagent_" + item["type"], "deepagents.subagent",
            "official framework event", subagent_event=item)
        assert (await h.store.list_events(h.session_id))[-1].id == state["event_log"][-1]["id"]

    async def execute(*, event_queue):
        with graph_event_scope(event_queue):
            return await adapter.execute(DeepAgentRuntimeRequest(session_id=h.session_id,
                turn_id=h.binding.turn_id, trace_id="subagent-event-test", model_key="fixture",
                checkpoint_namespace=h.binding.checkpoint_namespace,
                system_prompt="Delegate one bounded read-only task.", messages=[{"role": "user", "content": "review"}],
                context={"project_root": str(tmp_path)}, read_only_filesystem_enabled=True,
                cognitive_subagents_enabled=True, on_subagent_event=persist))

    with framework_owner_scope(h.owner.runtime_owner):
        result = await sink.run(execute)
    expected = result.subagent_events
    assert [item["type"] for item in expected] == ["started", "completed"]
    assert expected[0]["run_id"] == expected[1]["run_id"] and expected[0]["parent_run_id"]
    stored = [item.payload["subagent_event"] for item in await h.store.list_events(h.session_id)
        if item.type.startswith("graph.subagent_")]
    assert stored == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["payload", "type", "timestamp", "correlation"])
async def test_bound_event_reuses_only_exact_original_intent(checkpoint_harness, changed):
    h = checkpoint_harness
    await start(h)
    event = original_event(h).model_copy(update={"trace_id": "original-trace"})
    with bind_request_context(worker_id="first-delivery"):
        await h.store.append_event(h.session_id, event, runtime_owner=h.owner.runtime_owner)
    with bind_request_context(worker_id="different-delivery"):
        await h.store.append_event(h.session_id, event, runtime_owner=h.owner.runtime_owner)
    updates = {"payload": {"payload": {**event.payload, "fact": "changed"}},
        "type": {"type": "foreign.event"}, "timestamp": {"timestamp": event.timestamp + timedelta(seconds=1)},
        "correlation": {"trace_id": "foreign-trace"}}[changed]
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="differs"):
        await h.store.append_event(h.session_id, event.model_copy(update=updates), runtime_owner=h.owner.runtime_owner)
    values = [item for item in await h.store.list_events(h.session_id) if item.id == event.id]
    assert len(values) == 1 and values[0].payload == event.payload
    assert values[0].trace_id == "original-trace" and values[0].worker_id == "first-delivery"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["session", "turn", "explicit_turn", "owner_token", "owner_epoch", "expired"])
async def test_live_event_cannot_outlive_or_cross_captured_owner(checkpoint_harness, scope):
    h = checkpoint_harness
    await start(h)
    event, owner = original_event(h), h.owner.runtime_owner
    if scope == "session":
        event = event.model_copy(update={"session_id": "foreign-session"})
    elif scope == "turn":
        event = event.model_copy(update={"payload": {"turn_id": "foreign-turn"}})
    elif scope == "explicit_turn":
        event = event.model_copy(update={"turn_id": "foreign-turn"})
    elif scope == "owner_token":
        owner = owner.model_copy(update={"owner_token": "foreign-owner"})
    elif scope == "owner_epoch":
        owner = owner.model_copy(update={"owner_epoch": owner.owner_epoch + 1})
    else:
        assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    with pytest.raises((ValueError, ContinuationLeaseLostError)):
        await h.store.append_event(h.session_id, event, runtime_owner=owner)
    assert not [item for item in await h.store.list_events(h.session_id) if item.id == event.id]


@pytest.mark.asyncio
async def test_old_event_sink_cannot_adopt_replacement_owner(checkpoint_harness):
    h = checkpoint_harness
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    replacement = await h.tasks.claim(h.owner.id, worker_id="event-replacement", expected_binding=h.binding)
    await start(h, replacement)
    event = original_event(h)
    with framework_owner_scope(replacement.runtime_owner), pytest.raises(ContinuationLeaseLostError):
        await sink.persist(event)
    assert not [item for item in await h.store.list_events(h.session_id) if item.id == event.id]
    await h.store.append_event(h.session_id, event, runtime_owner=replacement.runtime_owner)
    assert [item.id for item in await h.store.list_events(h.session_id) if item.id == event.id] == [event.id]


@pytest.mark.asyncio
async def test_bound_sink_waits_for_to_thread_commit_despite_repeated_cancellation(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    await start(h)
    entered, release = threading.Event(), threading.Event()
    sync = h.store._append_event_sync
    event = original_event(h)

    def delayed(*args):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test event writer release missing")
        return sync(*args)

    monkeypatch.setattr(h.store, "_append_event_sync", delayed)
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)

    async def produce(*, event_queue):
        await event_queue.persist(event)

    task = asyncio.create_task(sink.run(produce))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done(), "execution lease cannot be released while the SQL write is still live"
    finally:
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert [item.id for item in await h.store.list_events(h.session_id) if item.id == event.id] == [event.id]


@pytest.mark.asyncio
async def test_failed_event_transaction_rolls_back_and_does_not_acknowledge(checkpoint_harness, monkeypatch):
    import src.runtime.framework_application_projection as projection

    h = checkpoint_harness
    await start(h)
    event = original_event(h)
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)

    def fail_after_insert(row, original):
        assert row["id"] == original.id
        raise ConnectionError("injected live event transaction failure")

    monkeypatch.setattr(projection, "_require_event_intent", fail_after_insert)
    with pytest.raises(ConnectionError, match="live event transaction failure"):
        await sink.persist(event)
    assert not [item for item in await h.store.list_events(h.session_id) if item.id == event.id]


@pytest.mark.asyncio
async def test_bound_sink_does_not_offer_unacknowledged_or_fake_durable_writes(checkpoint_harness):
    h = checkpoint_harness
    event = original_event(h)
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with graph_event_scope(sink), pytest.raises(RuntimeError, match="awaited persistence"):
        append_graph_event({"session_id": h.session_id, "turn_id": h.binding.turn_id}, "audit", "runtime", "fact")
    with pytest.raises(ValueError, match="PostgreSQL"):
        await InMemorySessionStore().append_event(h.session_id, event, runtime_owner=h.owner.runtime_owner)
