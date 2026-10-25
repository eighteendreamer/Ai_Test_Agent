from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres import PostgresSaver

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.session import ToolApprovalRequest
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config, start
from tests.test_agent_task_dispatch import harness


async def native_approvals(h, monkeypatch):
    """Observe actual official per-call task writes, not an invented resume map."""
    from deepagents import create_deep_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    await start(h)
    observed, calls = [], []
    original = PostgresSaver.put_writes

    def observe(saver, selected, writes, task_id, task_path=""):
        observed.append((selected, list(writes), task_id, task_path))
        return original(saver, selected, writes, task_id, task_path)

    monkeypatch.setattr(PostgresSaver, "put_writes", observe)

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def inspect_value(value: str) -> str:
        """Return a controlled receipt test value."""
        calls.append(value)
        return value

    model = Model(responses=[AIMessage(id="receipt-source", content="", tool_calls=[
        {"name": "inspect_value", "args": {"value": key}, "id": key, "type": "tool_call"}
        for key in ("A", "B")]), AIMessage(content="finished")])
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        agent = create_deep_agent(model=model, tools=[inspect_value], checkpointer=saver,
            middleware=[HumanInTheLoopMiddleware(interrupt_on={"inspect_value": {
                "allowed_decisions": ["approve", "reject"]}}, interrupt_mode="per_call")])
        first = await agent.ainvoke({"messages": [{"role": "user", "content": "Inspect A and B"}]},
            config(h), durability="sync")
        assert len(first["__interrupt__"]) == 2 and calls == []
        tasks = {}
        for selected, writes, task_id, task_path in observed:
            for channel, value in writes:
                if channel == "__interrupt__":
                    for interrupt in value:
                        tasks[interrupt.value["tool_call_id"]] = SimpleNamespace(
                            config=selected, task_id=task_id, task_path=task_path, interrupt=interrupt)
        assert set(tasks) == {"A", "B"}
    return SimpleNamespace(agent=agent, calls=calls, observed=observed, tasks=tasks)


async def link_pending_approval(h, native, key="B", *, resume_index=0):
    # Import after native_approvals so the first red run also checks the real
    # framework shape before the as-yet-unimplemented application boundary.
    from src.runtime.framework_approval_journal import FrameworkApprovalJournal
    from src.schemas.framework_approval import FrameworkInterruptLink

    item = native.tasks[key]
    link = FrameworkInterruptLink(
        approval_id=f"approval-{uuid4().hex}", binding_id=h.binding.binding_id,
        checkpoint_namespace="", checkpoint_id=item.config["configurable"]["checkpoint_id"],
        graph_task_id=item.task_id, task_path=item.task_path, interrupt_id=item.interrupt.id,
        resume_index=resume_index, source_message_id="receipt-source", tool_call_id=key,
        tool_name="inspect_value", tool_key="inspect_value", tool_job_id=f"job-{key}",
        arguments={"value": key}, scope_hash="test-scope", tool_schema_version=h.binding.tool_schema_version)
    approval = ToolApprovalRequest(id=link.approval_id, session_id=h.session_id,
        tool_key=link.tool_key, tool_name="Inspect value", reason="receipt test",
        created_at=datetime.now(timezone.utc), metadata={"tool_job_id": link.tool_job_id,
            "turn_id": h.binding.turn_id, "call_id": key, "arguments": link.arguments,
            "approval_scope_hash": link.scope_hash})
    await h.store.save_approval(h.session_id, approval)
    journal = FrameworkApprovalJournal(h.settings)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        journal.link_interrupt_in_transaction(cur, h.owner.runtime_owner, link)
    return SimpleNamespace(journal=journal, link=link)


async def queue_decision(h, native, key="B", decision="approved", *, resume_index=0):
    queued = await link_pending_approval(h, native, key, resume_index=resume_index)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status=%s, "
            "decision_note=%s, resolved_at=clock_timestamp() WHERE id=%s",
            (decision, "human choice", queued.link.approval_id))
        receipt = queued.journal.queue_decision_in_transaction(cur, h.binding,
            queued.link.approval_id, decision_id=str(uuid4()))
    return SimpleNamespace(journal=queued.journal, link=queued.link, receipt=receipt)


def receipt_record(h, queued):
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        return queued.journal.get_receipt_in_transaction(cur, queued.link.approval_id)


@pytest.mark.asyncio
async def test_native_per_call_resume_commits_matching_receipt_with_task_write(checkpoint_harness, monkeypatch):
    from langgraph.types import Command

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    assert receipt_record(h, queued).state == "queued"
    result = await native.agent.ainvoke(Command(resume={queued.link.interrupt_id: {"type": "approve"}}),
        config(h), durability="sync")
    assert native.calls == ["B"] and len(result["__interrupt__"]) == 1
    stored = receipt_record(h, queued)
    assert stored.state == "delivered" and stored.applied_at is None
    assert stored.proof["graph_task_id"] == queued.link.graph_task_id
    assert stored.proof["resume_index"] == 0
    assert stored.proof["decision_hash"] == stored.decision_hash
    assert stored.proof["owner_epoch"] == h.owner.runtime_owner.owner_epoch
    writes = [value for _, batch, task_id, _ in native.observed
        if task_id == queued.link.graph_task_id for channel, value in batch if channel == "__resume__"]
    assert writes and writes[-1] == [{"type": "approve"}]


def task_config(native, key="B", **changes):
    selected = native.tasks[key].config
    return {**selected, "configurable": {**selected["configurable"], "checkpoint_ns": "", **changes}}


async def write_resume(h, native, *, value=None, task_id=None, task_path=None, selected=None):
    item = native.tasks["B"]
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        await saver.aput_writes(selected or task_config(native),
            [("__resume__", value if value is not None else [{"type": "approve"}])],
            task_id or item.task_id, item.task_path if task_path is None else task_path)


@pytest.mark.asyncio
async def test_proof_failure_rolls_back_official_pending_write(checkpoint_harness, monkeypatch):
    from src.runtime.framework_approval_journal import FrameworkApprovalJournal

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    original = FrameworkApprovalJournal._save_proof

    def fail_after_proof(*args):
        original(*args)
        raise RuntimeError("injected receipt failure")

    monkeypatch.setattr(FrameworkApprovalJournal, "_save_proof", fail_after_proof)
    with pytest.raises(RuntimeError, match="injected receipt"):
        await write_resume(h, native)
    assert receipt_record(h, queued).state == "queued"
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        saved = await saver.aget_tuple(task_config(native))
        assert not any(task == native.tasks["B"].task_id and channel == "__resume__"
            for task, channel, _ in saved.pending_writes)


@pytest.mark.asyncio
async def test_official_write_failure_cannot_create_delivery_proof(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    original = PostgresSaver.put_writes

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected pending write failure")

    monkeypatch.setattr(PostgresSaver, "put_writes", fail_after_write)
    with pytest.raises(RuntimeError, match="injected pending"):
        await write_resume(h, native)
    assert receipt_record(h, queued).state == "queued"


@pytest.mark.asyncio
async def test_repeated_proof_preserves_first_epoch_and_timestamp(checkpoint_harness, monkeypatch):
    import asyncio

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    await write_resume(h, native)
    first = receipt_record(h, queued)
    await asyncio.gather(*(write_resume(h, native) for _ in range(4)))
    assert receipt_record(h, queued) == first
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        duplicate = queued.journal.queue_decision_in_transaction(cur, h.binding,
            queued.link.approval_id, decision_id="duplicate-client-request")
        assert duplicate == first


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate", ["task", "namespace", "checkpoint"])
async def test_unrelated_resume_does_not_deliver_receipt(checkpoint_harness, monkeypatch, coordinate):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    await write_resume(h, native,
        task_id="unrelated-task" if coordinate == "task" else None,
        selected=task_config(native, **({"checkpoint_ns": "child:unrelated"} if coordinate == "namespace"
            else {"checkpoint_id": str(uuid4())} if coordinate == "checkpoint" else {})))
    assert receipt_record(h, queued).state == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["decision", "task_path"])
async def test_matching_task_with_conflicting_resume_rolls_back(checkpoint_harness, monkeypatch, mismatch):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await write_resume(h, native,
            value=[{"type": "reject", "message": "changed"}] if mismatch == "decision" else None,
            task_path="wrong-path" if mismatch == "task_path" else None)
    assert receipt_record(h, queued).state == "queued"


@pytest.mark.asyncio
async def test_do_nothing_conflict_must_check_saved_value(checkpoint_harness, monkeypatch):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    item = native.tasks["B"]
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        # Simulate a preexisting conflicting native write. All-special writes
        # upsert; the subsequent mixed batch deliberately uses DO NOTHING.
        PostgresSaver(saver.conn, serde=saver.serde).put_writes(item.config,
            [("__resume__", [{"type": "reject", "message": "old conflict"}])], item.task_id, item.task_path)
        with pytest.raises(FrameworkApprovalRecoveryRequired):
            await saver.aput_writes(task_config(native),
                [("__resume__", [{"type": "approve"}]), ("test-result", "rollback")],
                item.task_id, item.task_path)
        saved = await saver.aget_tuple(task_config(native))
        assert not any(channel == "test-result" for _, channel, _ in saved.pending_writes)
    assert receipt_record(h, queued).state == "queued"


@pytest.mark.asyncio
async def test_expired_owner_cannot_deliver_receipt(checkpoint_harness, monkeypatch):
    from src.runtime.store import ContinuationLeaseLostError

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await write_resume(h, native)
    assert receipt_record(h, queued).state == "queued"


@pytest.mark.asyncio
async def test_native_rejection_is_delivered_without_tool_effect(checkpoint_harness, monkeypatch):
    from langgraph.types import Command

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native, decision="denied")
    result = await native.agent.ainvoke(Command(resume={queued.link.interrupt_id: queued.receipt.decision}),
        config(h), durability="sync")
    assert native.calls == [] and len(result["__interrupt__"]) == 1
    assert receipt_record(h, queued).state == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{"call_id": "wrong"}, {"turn_id": "wrong"},
    {"approval_scope_hash": "wrong"}, {"arguments": {"value": "wrong"}}])
async def test_link_cannot_rebind_changed_business_scope(checkpoint_harness, monkeypatch, metadata):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        import json

        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET metadata=metadata || %s::jsonb WHERE id=%s",
            (json.dumps(metadata), queued.link.approval_id))
        with pytest.raises(FrameworkApprovalRecoveryRequired):
            queued.journal.link_interrupt_in_transaction(cur, h.owner.runtime_owner, queued.link)


@pytest.mark.asyncio
async def test_unmapped_global_resume_keeps_official_format(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    # Native scalar resumes use a global task write. With no application link,
    # the journal must preserve the official saver contract unchanged.
    await write_resume(h, native, task_id="00000000-0000-0000-0000-000000000000", value={"global": True})


@pytest.mark.asyncio
async def test_two_interrupts_in_one_native_task_keep_distinct_resume_positions(checkpoint_harness, monkeypatch):
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import Command, interrupt

    h = checkpoint_harness
    await start(h)
    observed = []
    original = PostgresSaver.put_writes

    def observe(saver, selected, writes, task_id, task_path=""):
        observed.append((selected, list(writes), task_id, task_path))
        return original(saver, selected, writes, task_id, task_path)

    monkeypatch.setattr(PostgresSaver, "put_writes", observe)

    def node(state):
        # This is a native controlled workflow with sequential approval points.
        # Its task has one ID but stores TWO positions in the resume list.
        decisions = [interrupt({"type": "tool_approval", "tool_call_id": key,
            "name": "inspect_value", "args": {"value": key}}) for key in ("B", "A")]
        return {"decisions": decisions}

    builder = StateGraph(dict)
    builder.add_node("review", node)
    builder.add_edge(START, "review")
    builder.add_edge("review", END)

    def capture(native, key):
        for selected, writes, task_id, task_path in reversed(observed):
            for channel, value in writes:
                if channel == "__interrupt__" and value[0].value["tool_call_id"] == key:
                    native.tasks[key] = SimpleNamespace(config=selected, task_id=task_id,
                        task_path=task_path, interrupt=value[0])
                    return
        raise AssertionError(f"Native interrupt {key} was not saved")

    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        graph = builder.compile(checkpointer=saver)
        first = await graph.ainvoke({}, config(h), durability="sync")
        assert first["__interrupt__"][0].value["tool_call_id"] == "B"
        native = SimpleNamespace(tasks={})
        capture(native, "B")
        first_decision = await queue_decision(h, native)
        second = await graph.ainvoke(Command(resume={first_decision.link.interrupt_id: {"type": "approve"}}),
            config(h), durability="sync")
        assert second["__interrupt__"][0].value["tool_call_id"] == "A"
        capture(native, "A")
        second_decision = await queue_decision(h, native, key="A", decision="denied", resume_index=1)
        assert native.tasks["A"].task_id == native.tasks["B"].task_id
        final = await graph.ainvoke(Command(resume={second_decision.link.interrupt_id: second_decision.receipt.decision}),
            config(h), durability="sync")
        assert final["decisions"] == [{"type": "approve"}, {"type": "reject", "message": "human choice"}]
        assert receipt_record(h, first_decision).state == "delivered"
        proof = receipt_record(h, second_decision).proof
        assert proof["resume_index"] == 1 and proof["write_index"] == -4
        # The native runner emits BOTH prefixes in one mixed-channel batch.
        batches = [writes for _, writes, task_id, _ in observed if task_id == native.tasks["A"].task_id]
        assert any([len(value) for channel, value in batch if channel == "__resume__"] == [1, 2]
            for batch in batches)
        # Replay the ACTUAL emitted batch through the unmodified pinned saver
        # under an isolated probe task. It keeps the first short prefix, proving
        # why the application adapter must make linked resumes monotonic.
        selected, batch, _, path = next((selected, batch, task_id, path)
            for selected, batch, task_id, path in observed
            if task_id == native.tasks["A"].task_id
            and [len(value) for channel, value in batch if channel == "__resume__"] == [1, 2])
        plain = PostgresSaver(saver.conn, serde=saver.serde)
        plain.put_writes(selected, batch, "upstream-prefix-probe", path)
        raw = plain.get_tuple(selected)
        assert next(value for task, channel, value in raw.pending_writes
            if task == "upstream-prefix-probe" and channel == "__resume__") == [{"type": "approve"}]


@pytest.mark.asyncio
async def test_new_owner_preserves_proof_and_old_saver_rejects_writes(checkpoint_harness, monkeypatch):
    from langgraph.types import Command
    from src.runtime.store import ContinuationLeaseLostError

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    await write_resume(h, native)
    first = receipt_record(h, queued)
    # Simulate transfer between attempts before execution, keeping old attempt
    # terminal/recovery semantics intact. Cross-process recovery is a later gate.
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        owner_table = h.settings.database.postgres_agent_task_table + "_runtime"
        cur.execute(f"UPDATE {owner_table} SET owner_epoch=owner_epoch+1 WHERE binding_id=%s",
            (h.binding.binding_id,))
    replacement = h.owner.runtime_owner.model_copy(update={"owner_epoch": h.owner.runtime_owner.owner_epoch + 1})
    async with h.provider.open(owner=replacement) as saver:
        item = native.tasks["B"]
        await saver.aput_writes(task_config(native), [("__resume__", [{"type": "approve"}])],
            item.task_id, item.task_path)
        assert receipt_record(h, queued) == first
        # The agent compiled above holds the old saver, which must never adopt
        # a newer owner. The fake tool here is NOT application-governed; this
        # assertion proves checkpoint fencing, not external side-effect fencing.
        with pytest.raises(ContinuationLeaseLostError, match="execution owner"):
            await native.agent.ainvoke(Command(resume={queued.link.interrupt_id: queued.receipt.decision}),
                config(h), durability="sync")


@pytest.mark.asyncio
async def test_business_decision_and_receipt_are_rolled_back_together(checkpoint_harness, monkeypatch):
    from src.runtime.framework_approval_journal import framework_approval_tables

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    _, receipts = framework_approval_tables(h.settings)
    # Return this fixture approval to pending to exercise a fresh submission
    # transaction, rather than mistaking an already queued receipt for success.
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"DELETE FROM {receipts} WHERE approval_id=%s", (queued.link.approval_id,))
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='pending' WHERE id=%s",
            (queued.link.approval_id,))
    with pytest.raises(RuntimeError, match="submission rolled back"):
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='approved' WHERE id=%s",
                (queued.link.approval_id,))
            queued.journal.queue_decision_in_transaction(cur, h.binding, queued.link.approval_id,
                decision_id=str(uuid4()))
            raise RuntimeError("submission rolled back")
    assert receipt_record(h, queued) is None
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT status FROM {h.settings.database.postgres_approval_table} WHERE id=%s",
            (queued.link.approval_id,))
        assert cur.fetchone()["status"] == "pending"


@pytest.mark.asyncio
async def test_different_approval_cannot_claim_the_same_native_call(checkpoint_harness, monkeypatch):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    await queue_decision(h, native)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="different approval"):
        await queue_decision(h, native)


@pytest.mark.asyncio
async def test_replayed_short_prefix_cannot_erase_delivered_later_position(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    complete = [{"type": "approve"}, {"type": "reject", "message": "later"}]
    await write_resume(h, native, value=complete)
    first = receipt_record(h, queued)
    await write_resume(h, native, value=[{"type": "approve"}])
    assert receipt_record(h, queued) == first
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        saved = await saver.aget_tuple(task_config(native))
        assert next(value for task, channel, value in saved.pending_writes
            if task == native.tasks["B"].task_id and channel == "__resume__") == complete


@pytest.mark.asyncio
async def test_changed_business_scope_cannot_queue_another_decision(checkpoint_harness, monkeypatch):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET metadata=metadata || "
            "'{\"approval_scope_hash\":\"changed\"}'::jsonb WHERE id=%s", (queued.link.approval_id,))
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="business approval"):
            queued.journal.queue_decision_in_transaction(cur, h.binding, queued.link.approval_id,
                decision_id=str(uuid4()))


@pytest.mark.asyncio
async def test_reconcile_saved_resume_without_rewriting_checkpoint(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    item = native.tasks["B"]
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        # Existing pending write without a receipt proof, as an upgrade/recovery
        # input; current atomic writer must never produce this window itself.
        PostgresSaver(saver.conn, serde=saver.serde).put_writes(item.config,
            [("__resume__", [{"type": "approve"}])], item.task_id, item.task_path)
        before = await saver.aget_tuple(task_config(native))
        assert receipt_record(h, queued).state == "queued"
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            repaired = queued.journal.reconcile_delivery_in_transaction(cur, h.owner.runtime_owner,
                queued.link.approval_id)
        assert repaired.state == "delivered" and repaired.proof["evidence_kind"] == "reconciled"
        assert await saver.aget_tuple(task_config(native)) == before
        assert native.calls == []


@pytest.mark.asyncio
async def test_reconcile_without_saved_resume_does_not_infer_delivery(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        unchanged = queued.journal.reconcile_delivery_in_transaction(cur, h.owner.runtime_owner,
            queued.link.approval_id)
    assert unchanged.state == "queued" and unchanged.proof is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_existing_session_deletion_cleans_its_journal(checkpoint_harness, monkeypatch, bulk):
    from src.runtime.framework_approval_journal import framework_approval_tables

    h = checkpoint_harness
    native = await native_approvals(h, monkeypatch)
    queued = await queue_decision(h, native)
    await write_resume(h, native)
    await h.tasks.finish(h.owner, status="completed")
    # h.store uses UUID-named fixture tables containing only this test Session.
    if bulk:
        assert await h.store.delete_sessions_before() == 1
    else:
        assert await h.store.delete_session(h.session_id)
    for table in framework_approval_tables(h.settings):
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"SELECT count(*) AS count FROM {table}")
            assert cur.fetchone()["count"] == 0
    assert receipt_record(h, queued) is None
