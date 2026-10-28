"""A complete graph collection is explicit material, not a prefix or recovery grant."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

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


@pytest_asyncio.fixture
async def prefix_harness(checkpoint_harness, monkeypatch):
    from deepagents import create_deep_agent
    from langchain_core.messages import AIMessage
    from src.application.deep_agents.native_terminal import collect_native_terminal
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.application.runtime.runtime_service import RuntimeService
    from src.registry.tools import ToolRegistry
    from src.runtime.control import RuntimeControlRegistry
    from src.schemas.framework_graph_input import FrameworkProjectionIdentity, parse_graph_input_asset
    from src.schemas.session import ExecutionRequest
    from tests.test_framework_checkpoint_fencing import config

    h = checkpoint_harness
    await start(h)
    graphs, inputs = [], []
    async def resolve(key):
        return Model(responses=[AIMessage(content="prefix original")])
    def factory(**kwargs):
        graph = create_deep_agent(**kwargs)
        graphs.append(graph)
        return graph
    async def remember(raw):
        inputs.append(parse_graph_input_asset(raw))
    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve, checkpointer_factory=h.provider.open)
    monkeypatch.setattr(adapter, "_resolve_factory", lambda: factory)
    runtime = RuntimeService(graph=None,
        model_runtime_service=SimpleNamespace(get_model_config=lambda key: None, get_default_model_config=lambda: None),
        tool_runtime_service=object(), tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry(),
        deep_agent_runtime_adapter=adapter, deep_agent_enabled=True)
    identity = FrameworkProjectionIdentity(binding=h.binding, assistant_message_id="prefix-assistant",
        snapshot_id="prefix-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 28))
    h.prefix_sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with framework_owner_scope(h.owner.runtime_owner), bind_request_context(worker_id="prefix-original-enrichment"):
        h.prefix_result = await runtime.execute_turn(await h.store.get_session(h.session_id),
            ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="prefix-trace",
                user_message="inspect", normalized_input="inspect", mode_key="code_review", model_key="fixture",
                runtime_binding=h.binding), event_queue=h.prefix_sink, on_graph_input=remember, projection_identity=identity)
    h.prepared, = inputs
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        h.native = await collect_native_terminal(graphs[0], saver, config(h))
        h.checkpoint = await saver.aget_tuple(config(h))
        h.source = saver.runtime_source
    return h


def read_prefix(h, *, native=None, preparation=None, source=None):
    from src.runtime.framework_event_completion import read_checkpoint_event_prefix

    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        return read_checkpoint_event_prefix(cur, h.settings, source or h.source,
            preparation or h.prepared, native or h.native)


async def fresh_prefix_sink(h):
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    for item in h.prepared.runtime_preparation.initial_state["event_log"]:
        await sink.persist(ExecutionEvent(session_id=h.session_id, **item))
    sink.bind_checkpoint_preparation(h.prepared)
    return sink


@pytest.mark.asyncio
async def test_checkpoint_prefix_read_is_independent_after_lease_loss_and_never_grants_writes(prefix_harness, caplog):
    from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY

    h = prefix_harness
    before = await h.store.list_events(h.session_id)
    caplog.set_level("INFO", logger="src.runtime.framework_event_completion")
    expected = deepcopy(h.prefix_result.state["event_log"][:-1])
    assert read_prefix(h) == expected
    changed = read_prefix(h)
    changed[0]["payload"]["message"] = "caller changed the returned copy"
    assert read_prefix(h) == expected
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    await h.provider.close()
    async with h.provider.open(source=h.source) as reader:
        original = await reader.aget_tuple(h.checkpoint.config)
        assert original == h.checkpoint and not hasattr(reader, "_event_prefix")
        with pytest.raises(PermissionError):
            await reader.aput(original.config, original.checkpoint, original.metadata, {})
        assert read_prefix(h) == expected
        assert (await reader.aget_tuple(original.config)).metadata[CHECKPOINT_PREFIX_KEY] == h.native.checkpoint_event_prefix
    assert await h.store.list_events(h.session_id) == before
    assert "framework_checkpoint_event_prefix_read" in [item.message for item in caplog.records]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing", "future", "bool_version", "extra", "digest", "binding",
    "task", "attempt", "epoch", "worker", "checkpoint_id", "checkpoint_hash", "timestamp", "naive_time",
    "offset", "order", "duplicate", "input_hash", "row_missing", "row_payload", "row_context", "row_time",
    "row_sequence", "claim_missing", "preparation_content", "native_time", "native_namespace",
    "source_task", "source_attempt", "source_epoch", "source_worker"])
async def test_checkpoint_prefix_rejects_changed_originals_without_repair(prefix_harness, changed, caplog):
    h = prefix_harness
    receipt = deepcopy(h.native.checkpoint_event_prefix)
    preparation, native, source = deepcopy(h.prepared), h.native, h.source
    fields = {"binding": "binding_fingerprint", "task": "task_id", "attempt": "attempt_id", "epoch": "owner_epoch",
        "worker": "worker_id", "checkpoint_id": "checkpoint_id", "checkpoint_hash": "checkpoint_sha256",
        "input_hash": "graph_input_sha256", "timestamp": "checkpoint_timestamp"}
    if changed in fields:
        field = fields[changed]
        receipt[field] = receipt[field] + 1 if field == "owner_epoch" else (
            "f" * 64 if field.endswith("sha256") or field == "binding_fingerprint" else (
                "2020-01-01T00:00:00+00:00" if field == "checkpoint_timestamp" else "foreign"))
    elif changed in {"future", "bool_version"}:
        receipt["schema_version"] = 99 if changed == "future" else True
    elif changed == "extra":
        receipt["future"] = "unsupported"
    elif changed == "naive_time":
        receipt["checkpoint_timestamp"] = "2020-01-01T00:00:00"
    elif changed == "offset":
        receipt["returned_event_offset"] = len(receipt["events"])
    elif changed == "order":
        # This small real graph has one preterminal event. Add a distinct
        # reference before reversing, so the negative actually is unordered.
        second = {**receipt["events"][-1], "event_id": "distinct-unordered-reference",
            "event_sequence": receipt["events"][-1]["event_sequence"] + 1}
        receipt["events"].append(second)
        receipt["events"].reverse()
    elif changed == "duplicate":
        receipt["events"].append(deepcopy(receipt["events"][0]))
    elif changed == "preparation_content":
        preparation.context["changed_without_updating_frozen_digest"] = True
    elif changed == "native_time":
        native = replace(native, checkpoint_timestamp="2020-01-01T00:00:00+00:00")
    elif changed == "native_namespace":
        native = replace(native, proof=native.proof.model_copy(update={"checkpoint_namespace": "child"}))
    elif changed.startswith("source_"):
        field = {"source_task": "task_id", "source_attempt": "attempt_id", "source_epoch": "owner_epoch",
            "source_worker": "worker_id"}[changed]
        receipt[field] = receipt[field] + 1 if field == "owner_epoch" else "foreign-source"
        source = source.model_copy(update={field: receipt[field]})
    table = h.settings.database.postgres_event_table
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        event_id = receipt["events"][0]["event_id"]
        if changed == "row_missing":
            cur.execute(f"DELETE FROM {table} WHERE id=%s", (event_id,))
        elif changed in {"row_payload", "row_context"}:
            path = "{message}" if changed == "row_payload" else "{_harness_context,worker_id}"
            cur.execute(f"UPDATE {table} SET payload=jsonb_set(payload,%s,'\"changed\"'::jsonb) WHERE id=%s", (path, event_id))
        elif changed == "row_time":
            cur.execute(f"UPDATE {table} SET timestamp=timestamp+interval '1 second' WHERE id=%s", (event_id,))
        elif changed == "row_sequence":
            cur.execute(f"UPDATE {table} SET event_sequence=event_sequence+10000 WHERE id=%s", (event_id,))
        elif changed == "claim_missing":
            cur.execute(f"DELETE FROM {table} WHERE session_id=%s AND type='agent_task.claimed'", (h.session_id,))
    receipt["prefix_sha256"] = approval_value_hash({key: value for key, value in receipt.items() if key != "prefix_sha256"})
    if changed == "digest":
        receipt["prefix_sha256"] = "0" * 64
    native = replace(native, checkpoint_event_prefix=None if changed == "missing" else receipt)
    before = await h.store.list_events(h.session_id)
    with pytest.raises((FrameworkMessageStageRecoveryRequired, ValueError)):
        read_prefix(h, native=native, preparation=preparation, source=source)
    assert await h.store.list_events(h.session_id) == before
    async with h.provider.open(source=h.source) as reader:
        assert await reader.aget_tuple(h.checkpoint.config) == h.checkpoint
    rejected, = [item for item in caplog.records if item.message == "framework_checkpoint_event_prefix_read_rejected"]
    assert rejected.binding_id == h.binding.binding_id and rejected.task_id == source.task_id
    assert not hasattr(rejected, "owner_token") and not hasattr(rejected, "event_log")


@pytest.mark.asyncio
async def test_checkpoint_prefix_second_official_put_failure_rolls_back_checkpoint_blobs_and_metadata(prefix_harness, monkeypatch):
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.postgres import PostgresSaver
    from tests.test_framework_checkpoint_fencing import config
    from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY

    h = prefix_harness
    sink = await fresh_prefix_sink(h)
    original_put = PostgresSaver.put
    calls = []
    def fail_second(saver, selected, checkpoint, metadata, versions):
        result = original_put(saver, selected, checkpoint, metadata, versions)
        calls.append((saver.conn, CHECKPOINT_PREFIX_KEY in metadata, dict(versions)))
        if CHECKPOINT_PREFIX_KEY in metadata:
            raise ConnectionError("injected metadata put rollback")
        return result
    monkeypatch.setattr(PostgresSaver, "put", fail_second)
    checkpoint = empty_checkpoint()
    version = uuid4().hex
    checkpoint["channel_values"] = {"prefix_probe": {"value": "new blob"}}
    checkpoint["channel_versions"] = {"prefix_probe": version}
    before = await h.store.list_events(h.session_id)
    with graph_event_scope(sink):
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            with pytest.raises(ConnectionError, match="metadata put rollback"):
                await saver.aput(config(h), checkpoint, {}, {"prefix_probe": version})
            selected = {"configurable": {**config(h)["configurable"], "checkpoint_id": checkpoint["id"]}}
            assert await saver.aget_tuple(selected) is None
            assert await saver.aget_tuple(h.checkpoint.config) == h.checkpoint
    assert len(calls) == 2 and calls[0][0] is calls[1][0]
    assert [item[1] for item in calls] == [False, True] and calls[1][2] == {}
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM {h.settings.deep_agents.checkpoint_schema}.checkpoint_blobs WHERE version=%s", (version,))
        assert cur.fetchone()["total"] == 0
    assert await h.store.list_events(h.session_id) == before


@pytest.mark.asyncio
async def test_checkpoint_prefix_excludes_unacknowledged_callback_and_getter_has_no_alias(prefix_harness, monkeypatch):
    from langgraph.checkpoint.base import empty_checkpoint
    from tests.test_framework_checkpoint_fencing import config
    from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY

    h = prefix_harness
    sink = await fresh_prefix_sink(h)
    original_preparation, acknowledged = sink.checkpoint_event_prefix()
    original_preparation.context["caller_change"] = True
    acknowledged[0]["payload"]["message"] = "caller change"
    assert sink.checkpoint_event_prefix()[0] == h.prepared
    assert sink.checkpoint_event_prefix()[1] == h.prepared.runtime_preparation.initial_state["event_log"]
    entered, release = asyncio.Event(), asyncio.Event()
    append = h.store.append_event
    event = ExecutionEvent(session_id=h.session_id, type="graph.subagent_completed",
        timestamp=h.prefix_result.events[0].timestamp, payload={"turn_id": h.binding.turn_id,
        "trace_id": h.prepared.trace_id, "step": len(acknowledged) + 1, "message": "callback awaiting ACK"})
    async def delayed(session_id, item, **kwargs):
        if item.id == event.id:
            entered.set()
            await release.wait()
        return await append(session_id, item, **kwargs)
    monkeypatch.setattr(h.store, "append_event", delayed)
    pending = asyncio.create_task(sink.persist(event))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        with graph_event_scope(sink):
            async with h.provider.open(owner=h.owner.runtime_owner) as saver:
                first = await saver.aput(config(h), empty_checkpoint(), {}, {})
                first_receipt = (await saver.aget_tuple(first)).metadata[CHECKPOINT_PREFIX_KEY]
                assert event.id not in [item["event_id"] for item in first_receipt["events"]]
                release.set()
                await pending
                second = await saver.aput(config(h), empty_checkpoint(), {}, {})
                second_receipt = (await saver.aget_tuple(second)).metadata[CHECKPOINT_PREFIX_KEY]
                assert [item["event_id"] for item in second_receipt["events"]] == [
                    *[item["event_id"] for item in first_receipt["events"]], event.id]
                assert (await saver.aget_tuple(first)).metadata[CHECKPOINT_PREFIX_KEY] == first_receipt
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace,with_sink", [("child:task", True), ("", False), ("", True)])
async def test_checkpoint_prefix_is_server_owned_root_metadata_not_caller_or_child_receipt(prefix_harness, namespace, with_sink, caplog):
    from langgraph.checkpoint.base import empty_checkpoint
    from tests.test_framework_checkpoint_fencing import config
    from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY

    h = prefix_harness
    sink = await fresh_prefix_sink(h)
    caplog.set_level("DEBUG", logger="src.application.deep_agents.fenced_postgres_saver")
    selected = config(h, namespace)
    selected["metadata"] = {CHECKPOINT_PREFIX_KEY: "foreign metadata"}
    selected["configurable"][CHECKPOINT_PREFIX_KEY] = "foreign config"
    original = deepcopy(selected)
    with graph_event_scope(sink if with_sink else None):
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            saved = await saver.aput(selected, empty_checkpoint(), {CHECKPOINT_PREFIX_KEY: {"foreign": True}}, {})
            stored = await saver.aget_tuple(saved)
    if with_sink and not namespace:
        assert stored.metadata[CHECKPOINT_PREFIX_KEY]["scope"] == "checkpoint_graph_event_prefix_only"
    else:
        assert CHECKPOINT_PREFIX_KEY not in stored.metadata
    assert selected == original
    discarded, = [item for item in caplog.records if item.message == "framework_checkpoint_event_prefix_metadata_discarded"]
    assert discarded.binding_id == h.binding.binding_id
    assert set(discarded.metadata_sources) == {"metadata", "configurable", "checkpoint_metadata"}


@pytest.mark.asyncio
async def test_checkpoint_prefix_preparation_is_validated_once_and_foreign_sink_cannot_supply_it(prefix_harness):
    from langgraph.checkpoint.base import empty_checkpoint
    from tests.test_framework_checkpoint_fencing import config
    from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY

    h = prefix_harness
    sink = await fresh_prefix_sink(h)
    with pytest.raises(ValueError, match="already bound"):
        sink.bind_checkpoint_preparation(h.prepared)
    mutated = deepcopy(h.prepared)
    mutated.context["uncommitted_change"] = True
    other = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with pytest.raises(ValueError, match="digest"):
        other.bind_checkpoint_preparation(mutated)
    assert other.checkpoint_event_prefix() is None
    foreign = SessionEventSink(h.store, h.session_id, capacity=1, fence={},
        runtime_owner=h.owner.runtime_owner.model_copy(update={"worker_id": "not-the-captured-writer"}))
    foreign.bind_checkpoint_preparation(h.prepared)
    with graph_event_scope(foreign):
        async with h.provider.open(owner=h.owner.runtime_owner) as writer:
            saved = await writer.aput(config(h), empty_checkpoint(), {}, {})
            assert CHECKPOINT_PREFIX_KEY not in (await writer.aget_tuple(saved)).metadata


@pytest.mark.asyncio
async def test_checkpoint_budget_capture_is_copied_and_atomically_uses_the_v2_prefix(prefix_harness):
    from langgraph.checkpoint.base import empty_checkpoint
    from src.application.models.framework_model_budget_projection import capture_model_budget_material, read_model_budget_material
    from src.runtime.framework_event_completion import CHECKPOINT_PREFIX_KEY, RuntimeCheckpointEventPrefix
    from tests.test_framework_checkpoint_fencing import config
    from tests.test_framework_model_budget_projection import original_budget

    h = prefix_harness
    _, budget = original_budget()
    budget = budget.model_copy(update={"calls": [], "policy": budget.policy.model_copy(update={
        "model_config_fingerprints": {"fixture": h.binding.model_config_fingerprint}})})
    material = capture_model_budget_material(h.binding, budget)
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    for item in h.prepared.runtime_preparation.initial_state["event_log"]:
        await sink.persist(ExecutionEvent(session_id=h.session_id, **item))
    sink.bind_checkpoint_preparation(h.prepared, model_budget_source="ledger")
    assert sink.checkpoint_event_prefix().root_model_budget_material is None
    sink.bind_root_model_budget_material(material)
    material["snapshot"]["policy"]["max_calls"] += 1
    capture = sink.checkpoint_event_prefix()
    preparation, events = capture
    assert len(capture) == 2 and preparation == h.prepared
    assert events == h.prepared.runtime_preparation.initial_state["event_log"]
    assert read_model_budget_material(h.binding, capture.root_model_budget_material) == budget
    capture.root_model_budget_material["snapshot"]["policy"]["max_calls"] += 2
    assert read_model_budget_material(h.binding, sink.checkpoint_event_prefix().root_model_budget_material) == budget
    with graph_event_scope(sink):
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            saved = await saver.aput(config(h), empty_checkpoint(), {}, {})
            receipt = (await saver.aget_tuple(saved)).metadata[CHECKPOINT_PREFIX_KEY]
            prefix = RuntimeCheckpointEventPrefix.model_validate(receipt)
            assert prefix.schema_version == 2 and prefix.model_dump(mode="json") == receipt
            assert read_model_budget_material(h.binding, prefix.root_model_budget_material) == budget
            assert (await saver.aget_tuple(h.checkpoint.config)) == h.checkpoint
