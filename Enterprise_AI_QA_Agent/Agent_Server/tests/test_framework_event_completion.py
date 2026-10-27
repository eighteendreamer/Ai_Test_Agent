"""A complete graph collection is explicit material, not a prefix or recovery grant."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
import pytest_asyncio

from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope, graph_event_scope
from src.runtime.execution_logging import append_graph_event_durable, complete_graph_event_log, prepare_graph_event_log
from src.runtime.framework_event_completion import EVENT_TYPE, completion_event_id
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.session_event_sink import SessionEventSink
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ExecutionEvent
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, start
from tests.test_framework_native_terminal import Model


@pytest_asyncio.fixture
async def completion_harness(checkpoint_harness):
    from langchain_core.messages import AIMessage
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeRequest

    h = checkpoint_harness
    await start(h)
    async def resolve(key):
        return Model(responses=[AIMessage(content="original")])

    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve, checkpointer_factory=h.provider.open)
    with framework_owner_scope(h.owner.runtime_owner):
        result = await adapter.execute(DeepAgentRuntimeRequest(session_id=h.session_id, turn_id=h.binding.turn_id,
            trace_id="completion-test", model_key="fixture", system_prompt="inspect",
            checkpoint_namespace=h.binding.checkpoint_namespace, messages=[{"role": "user", "content": "review"}]))
    h.terminal = result.metadata["native_terminal"]
    h.sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    h.state = {"session_id": h.session_id, "turn_id": h.binding.turn_id, "context_bundle": {}}
    with graph_event_scope(h.sink), bind_request_context(worker_id="original-enrichment"):
        prepare_graph_event_log(h.state)
        for event_type in ("runtime.turn_started", "graph.subagent_completed", "runtime.turn_completed"):
            await append_graph_event_durable(h.state, event_type, "runtime", "original", original={"b": 1, "a": "content"})
    return h


async def complete(h, **kwargs):
    return await h.sink.complete_runtime_events(h.state["event_log"], h.terminal, **kwargs)


@pytest.mark.asyncio
async def test_complete_material_round_trips_original_intents_order_enrichment_and_resume_offset(completion_harness):
    h = completion_harness
    original = json.loads(json.dumps(h.state["event_log"]))
    # Other Session/approval events are not graph-log members.
    unrelated = ExecutionEvent(type="approval.journal", session_id=h.session_id,
        timestamp=original[-1]["timestamp"], payload={"turn_id": h.binding.turn_id})
    await h.store.append_event(h.session_id, unrelated, runtime_owner=h.owner.runtime_owner)
    with bind_request_context(worker_id="completion-enrichment"):
        event = await complete(h, returned_event_offset=2)
    assert event.id == completion_event_id(h.binding) and event.type == EVENT_TYPE
    assert h.state["event_log"] == original
    before = await h.store.list_events(h.session_id)
    with bind_request_context(worker_id="different-reader"):
        read = await h.store.read_runtime_event_completion(h.binding, h.terminal)
        assert await complete(h, returned_event_offset=2) == event
    assert read.event_log == original and read.completion.returned_event_offset == 2
    assert read.completion.scope == "runtime_graph_event_log_only"
    assert read.completion.task_id == h.owner.id and read.completion.attempt_id == h.owner.attempt_id
    assert read.completion.owner_epoch == h.owner.runtime_owner.owner_epoch
    assert [item.event_id for item in read.completion.events] == [item["id"] for item in original]
    assert await h.store.list_events(h.session_id) == before
    assert all(item.worker_id == "original-enrichment" for item in before if item.id in {e["id"] for e in original})
    with pytest.raises(RuntimeError, match="already completed"):
        await h.sink.persist(unrelated.model_copy(update={"id": "late-event"}))
    assert not [item for item in await h.store.list_events(h.session_id) if item.id == "late-event"]
    # Reading settled material after lease loss does not acquire a new owner.
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    assert (await h.store.read_runtime_event_completion(h.binding, h.terminal)).event_log == original
    with pytest.raises(ContinuationLeaseLostError):
        await complete(h, returned_event_offset=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing", "payload", "type", "time", "order", "duplicate", "offset", "waiting", "recursive", "foreign", "terminal"])
async def test_cannot_declare_partial_foreign_or_unordered_graph_collection_complete(completion_harness, changed):
    h = completion_harness
    values = json.loads(json.dumps(h.state["event_log"]))
    terminal, offset = dict(h.terminal), 0
    if changed == "missing":
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {h.settings.database.postgres_event_table} WHERE id=%s", (values[1]["id"],))
    elif changed == "payload":
        values[1]["payload"]["original"]["a"] = "changed"
    elif changed == "type":
        values[1]["type"] = "foreign.event"
    elif changed == "time":
        values[1]["timestamp"] = "2020-01-01T00:00:00+00:00"
    elif changed == "order":
        values[0], values[1] = values[1], values[0]
    elif changed == "duplicate":
        values[1] = values[0]
    elif changed == "offset":
        offset = len(values)
    elif changed == "waiting":
        values[-1]["type"] = "graph.waiting_for_approval"
    elif changed == "recursive":
        values[1]["id"] = completion_event_id(h.binding)
    elif changed == "foreign":
        values[1]["payload"]["turn_id"] = "foreign-turn"
    else:
        terminal["thread_id"] = "foreign-thread"
    with pytest.raises((FrameworkMessageStageRecoveryRequired, ValueError)):
        await h.sink.complete_runtime_events(values, terminal, returned_event_offset=offset)
    assert not [item for item in await h.store.list_events(h.session_id) if item.type == EVENT_TYPE]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing", "payload", "context", "sequence", "terminal", "digest", "future",
    "source_task", "source_attempt", "source_epoch", "source_worker"])
async def test_read_requires_original_rows_and_exact_terminal_without_repair(completion_harness, changed):
    h = completion_harness
    event = await complete(h)
    terminal = dict(h.terminal)
    table = h.settings.database.postgres_event_table
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "missing":
            cur.execute(f"DELETE FROM {table} WHERE id=%s", (h.state["event_log"][1]["id"],))
        elif changed in {"payload", "context"}:
            path = "{original,a}" if changed == "payload" else "{_harness_context,worker_id}"
            cur.execute(f"UPDATE {table} SET payload=jsonb_set(payload,%s,'\"changed\"'::jsonb) WHERE id=%s",
                (path, h.state["event_log"][1]["id"]))
        elif changed == "sequence":
            cur.execute(f"UPDATE {table} SET event_sequence=event_sequence+10000 WHERE id=%s", (h.state["event_log"][1]["id"],))
        elif changed == "terminal":
            terminal["checkpoint_id"] = "different-exact-checkpoint"
        elif changed in {"digest", "future"}:
            payload = event.model_dump(mode="json")["payload"]
            payload["completion"]["schema_version" if changed == "future" else "completion_sha256"] = 2 if changed == "future" else "0" * 64
            cur.execute(f"UPDATE {table} SET payload=%s::jsonb WHERE id=%s", (json.dumps(payload), event.id))
    before = await h.store.list_events(h.session_id)
    if changed.startswith("source_"):
        # A digest is not authentication: changing source with a recalculated
        # digest still has to refer to a real bound task/attempt.
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            payload = event.model_dump(mode="json")["payload"]
            field = {"source_task": "task_id", "source_attempt": "attempt_id", "source_epoch": "owner_epoch",
                "source_worker": "worker_id"}[changed]
            payload["completion"][field] = payload["completion"][field] + 1 if field == "owner_epoch" else "foreign-source"
            payload["completion"]["completion_sha256"] = approval_value_hash({k: v for k, v in payload["completion"].items()
                if k != "completion_sha256"})
            cur.execute(f"UPDATE {table} SET payload=%s::jsonb WHERE id=%s", (json.dumps(payload), event.id))
        before = await h.store.list_events(h.session_id)
    with pytest.raises(FrameworkMessageStageRecoveryRequired):
        await h.store.read_runtime_event_completion(h.binding, terminal)
    assert await h.store.list_events(h.session_id) == before


@pytest.mark.asyncio
async def test_completion_insert_and_existing_event_transaction_are_atomic(completion_harness, monkeypatch):
    import src.runtime.framework_application_projection as projection

    h = completion_harness
    original = projection._require_event_intent
    before = await h.store.list_events(h.session_id)
    def fail_after_insert(row, event):
        original(row, event)
        if event.type == EVENT_TYPE:
            raise ConnectionError("injected completion rollback")

    monkeypatch.setattr(projection, "_require_event_intent", fail_after_insert)
    with pytest.raises(ConnectionError, match="completion rollback"):
        await complete(h)
    assert await h.store.list_events(h.session_id) == before and not h.sink._runtime_events_completed
    monkeypatch.setattr(projection, "_require_event_intent", original)
    await complete(h)
    assert len([item for item in await h.store.list_events(h.session_id) if item.type == EVENT_TYPE]) == 1


@pytest.mark.asyncio
async def test_completion_waits_for_transaction_on_repeated_cancellation(completion_harness, monkeypatch):
    h = completion_harness
    entered, release = threading.Event(), threading.Event()
    original = h.store.append_event_in_transaction
    def delayed(cur, session_id, event, *args, **kwargs):
        if event.type == EVENT_TYPE:
            entered.set()
            if not release.wait(8):
                raise TimeoutError("completion test release missing")
        return original(cur, session_id, event, *args, **kwargs)

    monkeypatch.setattr(h.store, "append_event_in_transaction", delayed)
    task = asyncio.create_task(complete(h))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
    finally:
        release.set()
        result, = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result, asyncio.CancelledError) and h.sink._runtime_events_completed
    assert (await h.store.read_runtime_event_completion(h.binding, h.terminal)).event_log == h.state["event_log"]


@pytest.mark.asyncio
async def test_no_sink_and_legacy_prefix_cannot_claim_completion_material(completion_harness):
    h = completion_harness
    state = {**h.state, "context_bundle": {"framework_event_log_version": 999}}
    with graph_event_scope(None):
        prepare_graph_event_log(state)
        assert "framework_event_log_version" not in state["context_bundle"]
        await complete_graph_event_log(state, h.terminal)
    with graph_event_scope(h.sink):
        # A new durable suffix does not upgrade an old/no-sink prefix.
        await complete_graph_event_log(state, h.terminal)
    assert not [item for item in await h.store.list_events(h.session_id) if item.type == EVENT_TYPE]
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="missing"):
        await h.store.read_runtime_event_completion(h.binding, h.terminal)
