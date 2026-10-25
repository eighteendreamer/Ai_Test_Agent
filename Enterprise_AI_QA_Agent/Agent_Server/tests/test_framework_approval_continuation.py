from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from langgraph.types import Command

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired
from src.runtime.execution_context import framework_owner_scope
from src.schemas.session import ApprovalDecisionRequest, SessionStatus
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config
from tests.test_framework_approval_projection import projection_harness, prepare, projector
from tests.test_framework_tool_bridge import Model, state_for
from tests.test_runtime_binding_persistence import make_command


@pytest_asyncio.fixture
async def continuation_harness(projection_harness):
    h = projection_harness
    h.proposals = {}
    for index in range(2):
        proposal = await projector(h).project(h.owner.runtime_owner, prepare(h, index))
        h.proposals[proposal.link.tool_call_id] = proposal

    def wait(metadata, status):
        metadata["pending_turn"] = {"turn_id": h.binding.turn_id,
            "pending_approval_ids": [item.approval.id for item in h.proposals.values()],
            "context_bundle": {"runtime_binding": h.binding.model_dump(mode="json")}}
        metadata["control"] = {"control_state": "waiting_approval"}

    assert await h.store.update_status_and_mutate_metadata(h.session_id,
        status=SessionStatus.waiting_approval, mutate=wait, turn_lease_token=h.owner.lease_token)
    await h.tasks.finish(h.owner, status="completed", result_status="waiting_approval")
    h.effects, h.records = [], []
    yield h


async def claim_decision(h, *, key="B", decision="approved"):
    proposal = h.proposals[key]
    command = make_command(h, h.binding).model_copy(update={
        "operation": "approval_resume", "reference_id": proposal.approval.id})
    task = await h.tasks.submit_approval_v2(command, proposal.approval.id,
        ApprovalDecisionRequest(decision=decision, reason="continuation decision"))
    h.owner = await h.tasks.claim(task.id, worker_id="continuation-worker", expected_binding=h.binding)
    await h.tasks.begin(h.owner, resource_id="continuation-test", fencing_token=2)
    return h.owner


def service(h):
    from src.runtime.framework_approval_continuation import FrameworkApprovalContinuation
    return FrameworkApprovalContinuation(h.settings, sessions=h.store, jobs=h.jobs)


def receipt(h, key="B"):
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        return FrameworkApprovalJournal(h.settings).get_receipt_in_transaction(cur, h.proposals[key].approval.id)


async def run_plan(h, plan):
    from deepagents import create_deep_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langchain_core.messages import AIMessage
    from src.application.permissions.permission_service import PermissionService
    from src.application.runtime.runtime_service import RuntimeService
    from src.application.runtime.tool_runtime_service import ToolRuntimeService
    from src.runtime.control import RuntimeControlRegistry

    tools = ToolRuntimeService(tool_job_service=h.jobs)
    async def handle(arguments, context):
        h.effects.append((arguments["value"], context.tool_job_id))
        return {"status": "completed", "summary": "continuation evidence", "value": arguments["value"]}
    tools._handlers["inspect_value"] = handle
    runtime = RuntimeService(graph=None, model_runtime_service=object(), tool_runtime_service=tools,
        tool_registry=h.registry, runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=h.jobs, framework_approval_projection=projector(h), deep_agent_governed_tools_enabled=True)
    with framework_owner_scope(h.owner.runtime_owner):
        wrapped, gating = await runtime._build_deep_agent_business_tools(state=state_for(h), tool_records=h.records)
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            agent = create_deep_agent(model=Model(responses=[AIMessage(content="finished")]), tools=wrapped,
                middleware=[HumanInTheLoopMiddleware(interrupt_on=gating, interrupt_mode="per_call")], checkpointer=saver)
            return await agent.ainvoke(None if plan.continue_from_checkpoint else Command(resume=plan.resume),
                config(h), durability="sync")


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_prepares_only_the_owned_decision_and_records_sending(continuation_harness, decision):
    h = continuation_harness
    task = await claim_decision(h, decision=decision)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert plan.resume == {h.proposals["B"].link.interrupt_id: receipt(h).decision}
    assert plan.continue_from_checkpoint is False and plan.receipt.state == "sending"
    assert plan.approval.status.value == decision and plan.job.id == h.proposals["B"].job.id
    again = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert again == plan and h.effects == []
    assert (await h.jobs.get_job(plan.job.id)).status.value == "waiting_approval"
    assert receipt(h, "A") is None


@pytest.mark.asyncio
async def test_delivered_decision_uses_none_and_never_reexecutes_finished_tool(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    result = await run_plan(h, plan)
    assert len(result["__interrupt__"]) == 1 and h.effects == [("B", plan.job.id)]
    continued = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert continued.continue_from_checkpoint and continued.resume is None
    assert continued.receipt.state == "delivered" and continued.receipt.applied_at is None
    await run_plan(h, continued)
    assert h.effects == [("B", plan.job.id)]
    events = await h.store.list_events(h.session_id)
    assert len([item for item in events if item.type == "approval.decision_delivered"]) == 1


@pytest.mark.asyncio
async def test_missing_delivery_projection_is_repaired_from_native_write(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table}_decision SET state='sending', "
            "delivered_at=NULL,proof=NULL WHERE approval_id=%s", (task.reference_id,))
    assert receipt(h).state == "sending"
    continued = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert continued.continue_from_checkpoint and continued.receipt.proof["evidence_kind"] == "reconciled"
    assert h.effects == [("B", plan.job.id)]


@pytest.mark.asyncio
async def test_started_unknown_tool_blocks_resume_and_preserves_receipt(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h)
    job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    assert job.status.value == "running"
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcil"):
        await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).state == "queued" and h.effects == []
    assert (await h.jobs.get_job(job.id)).status.value == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["approval", "decision"])
async def test_continuation_cannot_substitute_task_references(continuation_harness, changed):
    h = continuation_harness
    task = await claim_decision(h)
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await service(h).prepare(h.owner.runtime_owner,
            h.proposals["A"].approval.id if changed == "approval" else task.reference_id,
            str(uuid4()) if changed == "decision" else task.input["decision_id"])
    assert receipt(h).state == "queued" and h.effects == []


@pytest.mark.asyncio
async def test_native_rejection_continues_without_dispatching_business_tool(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h, decision="denied")
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    result = await run_plan(h, plan)
    assert len(result["__interrupt__"]) == 1 and h.effects == []
    continued = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert continued.continue_from_checkpoint and continued.receipt.applied_at is None
    await run_plan(h, continued)
    # Native reject skips our callback; business denial/applied projection is a
    # separate next boundary, never fabricated by continuation preparation.
    assert (await h.jobs.get_job(plan.job.id)).status.value == "waiting_approval"
    assert h.effects == [] and receipt(h, "A") is None


@pytest.mark.asyncio
async def test_terminal_job_before_native_delivery_replays_its_result(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h)
    job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    await h.jobs.mark_completed(job.id, "previous result", {"evidence": "previous result"})
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert not plan.continue_from_checkpoint
    await run_plan(h, plan)
    assert h.effects == [] and h.records[0]["output"] == {"evidence": "previous result"}
    assert receipt(h).state == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["__resume__", "__interrupt__"])
async def test_delivered_receipt_without_native_facts_requires_reconciliation(continuation_harness, channel):
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"DELETE FROM {h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
            "WHERE thread_id=%s AND checkpoint_id=%s AND task_id=%s AND channel=%s",
            (h.binding.thread_id, plan.link.checkpoint_id, plan.link.graph_task_id, channel))
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcil"):
        await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).state == "delivered" and h.effects == [("B", plan.job.id)]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["job", "receipt", "task"])
async def test_changed_persisted_identity_cannot_mark_decision_sending(continuation_harness, changed):
    h = continuation_harness
    task = await claim_decision(h)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if changed == "job":
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET input_payload=%s::jsonb WHERE id=%s",
                ('{"value":"changed"}', h.proposals["B"].job.id))
        elif changed == "receipt":
            cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table}_decision SET decision=%s::jsonb "
                "WHERE approval_id=%s", ('{"type":"reject"}', task.reference_id))
        else:
            cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET input_payload="
                "jsonb_set(input_payload,'{approval,metadata,approval_scope_hash}',%s::jsonb) WHERE id=%s",
                ('"changed"', task.id))
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).state == "queued" and h.effects == []


@pytest.mark.asyncio
async def test_delivery_repair_and_event_share_the_preparation_transaction(continuation_harness, monkeypatch):
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table}_decision SET state='sending', "
            "delivered_at=NULL,proof=NULL WHERE approval_id=%s", (task.reference_id,))
    original = h.store.append_event_in_transaction
    def fail_after_event(cur, session_id, event, **kwargs):
        original(cur, session_id, event, **kwargs)
        raise RuntimeError("injected projection failure")
    monkeypatch.setattr(h.store, "append_event_in_transaction", fail_after_event)
    with pytest.raises(RuntimeError, match="injected"):
        await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).state == "sending"
    assert not any(item.type == "approval.decision_delivered" for item in await h.store.list_events(h.session_id))
    monkeypatch.setattr(h.store, "append_event_in_transaction", original)
    assert (await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])).continue_from_checkpoint
    assert h.effects == [("B", plan.job.id)]


@pytest.mark.asyncio
async def test_expired_owner_cannot_mark_decision_sending(continuation_harness):
    from src.runtime.store import ContinuationLeaseLostError
    h = continuation_harness
    task = await claim_decision(h)
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).state == "queued" and h.effects == []


@pytest.mark.asyncio
async def test_sibling_continuation_finishes_with_paired_results_and_no_repeated_effect(continuation_harness):
    from langchain_core.messages import ToolMessage
    h = continuation_harness
    task_b = await claim_decision(h)
    plan_b = await service(h).prepare(h.owner.runtime_owner, task_b.reference_id, task_b.input["decision_id"])
    await run_plan(h, plan_b)
    await h.tasks.finish(h.owner, status="completed", result_status="waiting_approval")
    task_a = await claim_decision(h, key="A")
    plan_a = await service(h).prepare(h.owner.runtime_owner, task_a.reference_id, task_a.input["decision_id"])
    result = await run_plan(h, plan_a)
    assert not result.get("__interrupt__")
    assert h.effects == [("B", plan_b.job.id), ("A", plan_a.job.id)]
    assert sorted(item.tool_call_id for item in result["messages"] if isinstance(item, ToolMessage)) == ["A", "B"]
    assert receipt(h).state == receipt(h, "A").state == "delivered"


@pytest.mark.asyncio
async def test_rejection_cannot_hide_a_previously_executed_tool(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h, decision="denied")
    job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    await h.jobs.mark_completed(job.id, "unexpected execution", {"evidence": "needs reconciliation"})
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcil"):
        await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert receipt(h).state == "queued" and h.effects == []


@pytest.mark.asyncio
async def test_delivered_approval_cannot_claim_a_waiting_job_is_completed(continuation_harness):
    h = continuation_harness
    task = await claim_decision(h)
    plan = await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    await run_plan(h, plan)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET status='waiting_approval',started_at=NULL "
            "WHERE id=%s", (plan.job.id,))
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcil"):
        await service(h).prepare(h.owner.runtime_owner, task.reference_id, task.input["decision_id"])
    assert h.effects == [("B", plan.job.id)] and receipt(h).applied_at is None
