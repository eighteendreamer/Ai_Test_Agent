from __future__ import annotations

import asyncio
import threading
from typing import Annotated, TypedDict
from uuid import uuid4

import pytest
import pytest_asyncio
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.channels import DeltaChannel

from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from tests.test_agent_task_dispatch import harness
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


def append_batches(current, writes):
    return (current or []) + [entry for batch in writes for entry in batch]


class DeltaState(TypedDict):
    events: Annotated[list[str], DeltaChannel(append_batches, snapshot_frequency=10)]


@pytest_asyncio.fixture
async def checkpoint_harness(harness):
    h = harness
    h.settings.deep_agents.checkpoint_schema = f"fence_test_{uuid4().hex}"
    selected = make_binding(h.session_id, f"turn-{uuid4().hex}")
    task = await h.tasks.submit_v2(make_command(h, selected), make_payload(),
        make_message(h, selected), idempotency_key=uuid4().hex)
    owner = await h.tasks.claim(task.id, worker_id="owner-1", expected_binding=selected)
    provider = DeepAgentCheckpointProvider(h.settings)
    h.owner, h.binding, h.provider = owner, selected, provider
    try:
        yield h
    finally:
        await provider.close()
        # Exact UUID schema created above; no production checkpoint namespace.
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")


def config(h, namespace=""):
    return {"configurable": {"thread_id": h.binding.thread_id, "checkpoint_ns": namespace}}


async def start(h, owner=None):
    await h.tasks.begin(owner or h.owner, resource_id="fence-test", fencing_token=1)


@pytest.mark.asyncio
async def test_expired_owner_cannot_write_checkpoint_or_pending_writes(checkpoint_harness):
    h = checkpoint_harness
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        saved = await saver.aput(config(h), empty_checkpoint(), {}, {})
        await saver.aput_writes(saved, [("result", "first")], "tool-1")
        assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
        with pytest.raises(ContinuationLeaseLostError):
            await saver.aput(config(h), empty_checkpoint(), {}, {})
        with pytest.raises(ContinuationLeaseLostError):
            await saver.aput_writes(saved, [("result", "stale")], "tool-1")
        assert (await saver.aget_tuple(saved)).pending_writes == [("tool-1", "result", "first")]


@pytest.mark.asyncio
async def test_replacement_epoch_rejects_old_saver_and_preserves_nested_namespaces(checkpoint_harness):
    h = checkpoint_harness
    async with h.provider.open(owner=h.owner.runtime_owner) as old:
        assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
        replacement = await h.tasks.claim(h.owner.id, worker_id="owner-2", expected_binding=h.binding)
        await start(h, replacement)
        async with h.provider.open(owner=replacement.runtime_owner) as current:
            saved = await current.aput(config(h, "tools:child|nested:task"), empty_checkpoint(), {}, {})
            await current.aput_writes(saved, [("result", "current")], "child-tool")
            with pytest.raises(ContinuationLeaseLostError):
                old.put(config(h), empty_checkpoint(), {}, {})
            with pytest.raises(ContinuationLeaseLostError):
                old.put_writes(saved, [("result", "stale")], "child-tool")
            assert (await current.aget_tuple(saved)).pending_writes == [("child-tool", "result", "current")]
            assert saved["configurable"]["checkpoint_ns"] == "tools:child|nested:task"
            assert len([item async for item in current.alist(config(h, "tools:child|nested:task"))]) == 1
            await current.aput(config(h), empty_checkpoint(), {}, {})
            assert {item.config["configurable"]["checkpoint_ns"] async for item in current.alist(None)} == {
                "", "tools:child|nested:task"}
            wrong_thread = {"configurable": {"thread_id": "another-turn", "checkpoint_ns": ""}}
            with pytest.raises(ValueError, match="thread"):
                await current.aput(wrong_thread, empty_checkpoint(), {}, {})
            with pytest.raises(PermissionError):
                await current.adelete_thread(h.binding.thread_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["put", "put_writes"])
async def test_official_pipeline_write_rolls_back_with_its_owner_transaction(checkpoint_harness, monkeypatch, operation):
    h = checkpoint_harness
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        saved = await saver.aput(config(h), empty_checkpoint(), {}, {})
        original = getattr(PostgresSaver, operation)

        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected failure after official pipeline")

        monkeypatch.setattr(PostgresSaver, operation, fail_after_write)
        candidate = empty_checkpoint()
        with pytest.raises(RuntimeError, match="injected"):
            if operation == "put":
                await saver.aput(saved, candidate, {}, {})
            else:
                await saver.aput_writes(saved, [("result", "must-rollback")], "tool-rollback")
        monkeypatch.setattr(PostgresSaver, operation, original)
        assert (await saver.aget_tuple(config(h))).checkpoint["id"] == saved["configurable"]["checkpoint_id"]
        assert (await saver.aget_tuple(saved)).pending_writes == []


@pytest.mark.asyncio
async def test_cancelled_to_thread_writer_cannot_adopt_replacement_owner(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    errors = []
    async with h.provider.open(owner=h.owner.runtime_owner) as old:
        original = old.put

        def delayed(*args):
            entered.set()
            try:
                if not release.wait(5):
                    raise TimeoutError("test writer release missing")
                return original(*args)
            except Exception as exc:
                errors.append(exc)
            finally:
                settled.set()

        monkeypatch.setattr(old, "put", delayed)
        running = asyncio.create_task(old.aput(config(h), empty_checkpoint(), {}, {}))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
            replacement = await h.tasks.claim(h.owner.id, worker_id="replacement", expected_binding=h.binding)
            await start(h, replacement)
        finally:
            release.set()
            assert await asyncio.to_thread(settled.wait, 3)
        assert len(errors) == 1 and isinstance(errors[0], ContinuationLeaseLostError)
        assert await old.aget_tuple(config(h)) is None


@pytest.mark.asyncio
async def test_official_deepagents_per_call_resume_uses_fenced_postgres_storage(checkpoint_harness):
    from dataclasses import replace
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeRequest
    from src.runtime.execution_context import framework_owner_scope, current_framework_owner

    h = checkpoint_harness
    await start(h)
    calls = []

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def inspect_value(value: str) -> str:
        """Return a controlled test value."""
        calls.append(value)
        return value

    model = Model(responses=[AIMessage(content="", tool_calls=[
        {"name": "inspect_value", "args": {"value": name}, "id": name, "type": "tool_call"}
        for name in ("A", "B")]), AIMessage(content="finished")])

    async def resolve(_key):
        return model

    adapter = DeepAgentRuntimeAdapter(model_resolver=resolve, checkpointer_factory=h.provider.open)
    request = DeepAgentRuntimeRequest(session_id=h.session_id, turn_id=h.binding.turn_id,
        trace_id="fenced-test", model_key="controlled", system_prompt="Inspect the test values.",
        checkpoint_namespace=h.binding.checkpoint_namespace,
        messages=[{"role": "user", "content": "Inspect A and B"}], business_tools=[inspect_value],
        interrupt_on={"inspect_value": {"allowed_decisions": ["approve", "reject"]}})
    with framework_owner_scope(h.owner.runtime_owner):
        first = await adapter.execute(request)
        interrupts = {item["value"]["tool_call_id"]: item["id"] for item in first.interrupts}
        assert set(interrupts) == {"A", "B"} and calls == []
        second = await adapter.execute(replace(request, resume={interrupts["B"]: {"type": "approve"}}))
        assert calls == ["B"] and {i["id"] for i in second.interrupts} == {interrupts["A"]}
        third = await adapter.execute(replace(request, resume={interrupts["A"]: {"type": "reject", "message": "denied"}}))
        assert calls == ["B"] and third.output_text == "finished" and not third.interrupts
        async with h.provider.open() as saver:
            persisted = await saver.aget_tuple(config(h))
            assert persisted is not None
            with saver.conn.connection() as connection, connection.cursor() as cur:
                cur.execute("SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id=%s", (h.binding.thread_id,))
                assert all(row["checkpoint_ns"] == h.binding.checkpoint_namespace
                    or row["checkpoint_ns"].startswith(h.binding.checkpoint_namespace + "|") for row in cur.fetchall())
    assert current_framework_owner() is None


@pytest.mark.asyncio
async def test_framework_heartbeat_and_terminal_settlement_revoke_write_authority(checkpoint_harness):
    h = checkpoint_harness
    await start(h)
    assert await h.tasks.renew_framework_execution(h.owner, 60)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT expires_at=(SELECT (metadata->>'turn_lease_expires_at')::timestamptz "
            f"FROM {h.settings.database.postgres_session_table} WHERE id=%s) AS same "
            f"FROM {h.settings.database.postgres_agent_task_table}_runtime WHERE binding_id=%s",
            (h.session_id, h.binding.binding_id))
        assert cur.fetchone()["same"]
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        await h.tasks.finish(h.owner, status="completed")
        assert not await h.tasks.renew_framework_execution(h.owner, 60)
        with pytest.raises(ContinuationLeaseLostError):
            await saver.aput(config(h), empty_checkpoint(), {}, {})


@pytest.mark.asyncio
async def test_official_write_holds_owner_lock_until_commit(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    await start(h)
    entered, release = threading.Event(), threading.Event()
    original = PostgresSaver.put

    def delayed(saver, *args):
        result = original(saver, *args)
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test commit release missing")
        return result

    monkeypatch.setattr(PostgresSaver, "put", delayed)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        write = asyncio.create_task(saver.aput(config(h), empty_checkpoint(), {}, {}))
        settlement = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            settlement = asyncio.create_task(h.tasks.finish(h.owner, status="cancelled"))
            await asyncio.sleep(.15)
            assert not settlement.done()
            # Another connection cannot observe the checkpoint before its owner
            # transaction commits, even though official put has returned.
            assert await saver.aget_tuple(config(h)) is None
        finally:
            release.set()
            await asyncio.wait_for(write, 3)
            if settlement is not None:
                await asyncio.wait_for(settlement, 3)
        with pytest.raises(ContinuationLeaseLostError):
            await saver.aput(config(h), empty_checkpoint(), {}, {})


@pytest.mark.asyncio
async def test_saver_rechecks_expiry_after_waiting_for_session_lock(checkpoint_harness, monkeypatch):
    from src.application.deep_agents import fenced_postgres_saver as module

    h = checkpoint_harness
    await start(h)
    entered = threading.Event()
    original = module.assert_runtime_owner

    def entered_guard(*args, **kwargs):
        entered.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "assert_runtime_owner", entered_guard)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=metadata || "
                "jsonb_build_object('turn_lease_expires_at', to_jsonb(clock_timestamp() + INTERVAL '150 milliseconds')) "
                "WHERE id=%s", (h.session_id,))
            write = asyncio.create_task(saver.aput(config(h), empty_checkpoint(), {}, {}))
            assert await asyncio.to_thread(entered.wait, 3)
            await asyncio.sleep(.25)
            assert not write.done()
        with pytest.raises(ContinuationLeaseLostError):
            await asyncio.wait_for(write, 3)
        assert await saver.aget_tuple(config(h)) is None


@pytest.mark.asyncio
async def test_lock_timeout_returns_clean_pool_connection(checkpoint_harness):
    from psycopg.errors import LockNotAvailable

    h = checkpoint_harness
    h.settings.deep_agents.checkpoint_lock_timeout_ms = 100
    h.settings.deep_agents.checkpoint_pool_size = 1
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {h.settings.database.postgres_session_table} WHERE id=%s FOR UPDATE", (h.session_id,))
            with pytest.raises(LockNotAvailable):
                await saver.aput(config(h), empty_checkpoint(), {}, {})
        assert await saver.aget_tuple(config(h)) is None
        await saver.aput(config(h), empty_checkpoint(), {}, {})
        assert await saver.aget_tuple(config(h)) is not None


@pytest.mark.asyncio
async def test_statement_timeout_rolls_back_and_reuses_pool(checkpoint_harness, monkeypatch):
    from psycopg.errors import QueryCanceled

    h = checkpoint_harness
    h.settings.deep_agents.checkpoint_statement_timeout_ms = 100
    await start(h)
    original = PostgresSaver.put

    def stalled(saver, *args):
        saver.conn.execute("SELECT pg_sleep(0.3)")
        return original(saver, *args)

    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        monkeypatch.setattr(PostgresSaver, "put", stalled)
        with pytest.raises(QueryCanceled):
            await saver.aput(config(h), empty_checkpoint(), {}, {})
        monkeypatch.setattr(PostgresSaver, "put", original)
        assert await saver.aget_tuple(config(h)) is None
        await saver.aput(config(h), empty_checkpoint(), {}, {})


@pytest.mark.asyncio
async def test_official_langgraph_delta_state_survives_provider_recreation(checkpoint_harness):
    from langgraph.graph import StateGraph, START, END

    h = checkpoint_harness
    await start(h)
    builder = StateGraph(DeltaState)
    builder.add_node("append", lambda state: {"events": ["node"]})
    builder.add_edge(START, "append")
    builder.add_edge("append", END)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        graph = builder.compile(checkpointer=saver)
        assert (await graph.ainvoke({"events": ["first"]}, config(h)))["events"] == ["first", "node"]
    await h.provider.close()
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        graph = builder.compile(checkpointer=saver)
        assert (await graph.aget_state(config(h))).values["events"] == ["first", "node"]
        assert (await graph.ainvoke({"events": ["second"]}, config(h)))["events"] == ["first", "node", "second", "node"]
        assert "events" in await saver.aget_delta_channel_history(config=config(h), channels=["events"])
