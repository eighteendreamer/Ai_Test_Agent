from __future__ import annotations

import pytest
from langchain_core.messages import ToolMessage

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, service, receipt, run_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_native_result_applies_decision_and_settles_rejection_once(continuation_harness, decision):
    h = continuation_harness
    task = await claim_decision(h, decision=decision)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    # Establish the pinned channel shape using the official saver observer.
    assert any(channel == "messages" and any(isinstance(item, ToolMessage) and item.tool_call_id == "B"
        for item in value) for _, writes, task_id, _ in h.native.observed
        if task_id == plan.link.graph_task_id for channel, value in writes)
    settled = await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert settled.receipt.applied_at is not None
    assert settled.receipt.proof["application"]["tool_call_id"] == "B"
    assert settled.job.status.value == ("completed" if decision == "approved" else "denied")
    assert len(h.effects) == (1 if decision == "approved" else 0)
    await run_plan(h, settled)
    assert await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"]) == settled
    events = await h.store.list_events(h.session_id)
    assert len([item for item in events if item.type == "approval.decision_applied"]) == 1
    assert receipt(h, "A") is None


@pytest.mark.asyncio
async def test_a_business_decision_alone_is_not_applied(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h, decision="denied")
    await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).state == "sending" and receipt(h).applied_at is None and h.effects == []
    assert (await h.jobs.get_job(h.proposals["B"].job.id)).status.value == "waiting_approval"


@pytest.mark.asyncio
async def test_delivery_without_native_result_cannot_apply_a_rejection(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h, decision="denied")
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"DELETE FROM {h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
            "WHERE thread_id=%s AND checkpoint_id=%s AND task_id=%s AND channel='messages'",
            (h.binding.thread_id, plan.link.checkpoint_id, plan.link.graph_task_id))
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="result"):
        await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).state == "delivered" and receipt(h).applied_at is None
    assert (await h.jobs.get_job(plan.job.id)).status.value == "waiting_approval" and h.effects == []


@pytest.mark.asyncio
async def test_applied_projection_job_and_event_roll_back_together(continuation_harness, monkeypatch):
    h = continuation_harness
    task = await claim_decision(h, decision="denied")
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    original = h.store.append_event_in_transaction
    def fail_after_applied(cur, session_id, event, **kwargs):
        result = original(cur, session_id, event, **kwargs)
        if event.type == "approval.decision_applied":
            raise RuntimeError("injected applied failure")
        return result
    monkeypatch.setattr(h.store, "append_event_in_transaction", fail_after_applied)
    with pytest.raises(RuntimeError, match="injected"):
        await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).applied_at is None and "application" not in receipt(h).proof
    assert (await h.jobs.get_job(plan.job.id)).status.value == "waiting_approval"
    assert not any(item.type == "approval.decision_applied" for item in await h.store.list_events(h.session_id))
    monkeypatch.setattr(h.store, "append_event_in_transaction", original)
    assert (await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])).job.status.value == "denied"
    assert h.effects == []


@pytest.mark.asyncio
async def test_replayed_delivery_preserves_applied_proof(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    settled = await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        await saver.aput_writes({"configurable": {**config(h)["configurable"], "checkpoint_id": plan.link.checkpoint_id}},
            [("__resume__", [plan.receipt.decision])], plan.link.graph_task_id, plan.link.task_path)
    assert receipt(h) == settled.receipt
    assert (await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])).continue_from_checkpoint
    assert len(h.effects) == 1


@pytest.mark.asyncio
async def test_current_governance_denial_is_an_applied_approval_with_no_tool_execution(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    h.registry.get("inspect_value").permission_level = "restricted"
    await run_plan(h, plan)
    settled = await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert settled.approval.status.value == "approved" and settled.job.status.value == "denied"
    assert settled.job.started_at is None and settled.receipt.applied_at is not None and h.effects == []


def replace_native_message(h, plan, change):
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    serde = JsonPlusSerializer()
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        location = (h.binding.thread_id, plan.link.checkpoint_id, plan.link.graph_task_id)
        cur.execute(f"SELECT type,blob FROM {h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
            "WHERE thread_id=%s AND checkpoint_id=%s AND task_id=%s AND channel='messages'", location)
        row = cur.fetchone()
        messages = serde.loads_typed((row["type"], bytes(row["blob"])))
        assert len(messages) == 1
        wire_type, blob = serde.dumps_typed([messages[0].model_copy(update=change)])
        cur.execute(f"UPDATE {h.settings.deep_agents.checkpoint_schema}.checkpoint_writes SET type=%s,blob=%s "
            "WHERE thread_id=%s AND checkpoint_id=%s AND task_id=%s AND channel='messages'", (wire_type, blob, *location))


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["call", "status", "error"])
async def test_conflicting_native_rejection_result_cannot_settle_job(continuation_harness, changed):
    h = continuation_harness
    task = await claim_decision(h, decision="denied")
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    if changed == "error":
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            await saver.aput_writes({"configurable": {**config(h)["configurable"], "checkpoint_id": plan.link.checkpoint_id}},
                [("__error__", "native task failed")], plan.link.graph_task_id, plan.link.task_path)
    else:
        replace_native_message(h, plan, {"tool_call_id": "A"} if changed == "call" else {"status": "success"})
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="result"):
        await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).applied_at is None and h.effects == []
    assert (await h.jobs.get_job(plan.job.id)).status.value == "waiting_approval"


@pytest.mark.asyncio
async def test_native_governed_status_must_match_terminal_job(continuation_harness):
    import json
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    replace_native_message(h, plan, {"content": json.dumps({"status": "denied", "instruction_authority": "none"})})
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="terminal ToolJob"):
        await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).applied_at is None and (await h.jobs.get_job(plan.job.id)).status.value == "completed"
    assert h.effects == [("B", plan.job.id)]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["message", "job"])
async def test_applied_evidence_cannot_change_its_original_result(continuation_harness, changed):
    h = continuation_harness
    task = await claim_decision(h, decision="denied")
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    settled = await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    if changed == "message":
        replace_native_message(h, plan, {"content": "different native rejection result"})
    else:
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET output_payload=%s::jsonb WHERE id=%s",
                ('{"native_feedback":"changed"}', plan.job.id))
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="identity changed"):
        await service(h).settle(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h) == settled.receipt and h.effects == []
