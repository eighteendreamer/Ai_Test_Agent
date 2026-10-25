from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage

from src.application.deep_agents import DeepAgentRuntimeAdapter
from src.application.permissions.permission_service import PermissionService
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_context import framework_owner_scope
from src.runtime.execution_context import current_framework_owner
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.session import ExecutionRequest, SessionStatus
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness, projector
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, receipt, service
from tests.test_framework_tool_bridge import Model, state_for
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


def build_runtime(h, *, responses=None):
    tools = ToolRuntimeService(tool_job_service=h.jobs)

    async def handle(arguments, context):
        h.effects.append((arguments["value"], context.tool_job_id))
        return {"status": "completed", "summary": "runtime evidence", "value": arguments["value"]}

    tools._handlers["inspect_value"] = handle
    h.adapter_requests = []

    class Adapter(DeepAgentRuntimeAdapter):
        async def execute(self, request):
            h.adapter_requests.append(request)
            return await super().execute(request)

    model = Model(responses=responses or [AIMessage(content="finished")])

    async def resolve_model(key):
        return model

    h.runtime = RuntimeService(graph=None,
        model_runtime_service=SimpleNamespace(get_model_config=lambda key: None, get_default_model_config=lambda: None),
        tool_runtime_service=tools, tool_registry=h.registry, runtime_control=RuntimeControlRegistry(),
        permission_service=PermissionService(), tool_job_service=h.jobs,
        framework_approval_projection=projector(h), framework_approval_continuation=service(h),
        deep_agent_runtime_adapter=Adapter(model_resolver=resolve_model, checkpointer_factory=h.provider.open),
        deep_agent_enabled=True, deep_agent_governed_tools_enabled=True)


@pytest_asyncio.fixture
async def runtime_harness(continuation_harness):
    h = continuation_harness
    build_runtime(h)
    session = await h.store.get_session(h.session_id)
    state = h.runtime._state_from_graph_data(session, state_for(h), {})
    state["context_bundle"].update(checkpoint_namespace=h.binding.checkpoint_namespace,
        deep_agent_harness="deepagents")
    state["pending_approvals"] = [item.approval.model_dump(mode="python") for item in h.proposals.values()]
    for item in h.proposals.values():
        state["tool_results"].append({"call_id": item.job.call_id, "job_id": item.job.id,
            "tool_key": "inspect_value", "tool_name": "Inspect value", "status": "waiting_approval",
            "summary": item.approval.reason, "input": item.link.arguments, "output": {},
            "approval_id": item.approval.id, "trace_id": "runtime-test"})
    session.metadata["pending_turn"] = h.runtime._build_pending_turn(state, stage="waiting_approval")
    await h.store.save_session(session)
    yield h


async def resume(h, task):
    with framework_owner_scope(task.runtime_owner):
        return await h.runtime.resume_framework_approval(await h.store.get_session(h.session_id),
            task.reference_id, task.input["decision_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_runtime_settles_selected_result_and_preserves_sibling_identity(runtime_harness, decision):
    h = runtime_harness
    task = await claim_decision(h, decision=decision)
    result = await resume(h, task)
    assert len(result.state["tool_results"]) == 2
    records = {item["approval_id"]: item for item in result.state["tool_results"]}
    assert records[h.proposals["A"].approval.id]["status"] == "waiting_approval"
    assert records[h.proposals["B"].approval.id]["status"] == ("completed" if decision == "approved" else "denied")
    assert result.pending_turn["pending_approval_ids"] == [h.proposals["A"].approval.id]
    assert result.approvals[0]["metadata"] == h.proposals["A"].approval.metadata
    assert receipt(h).applied_at is not None and receipt(h, "A") is None
    assert h.effects == ([("B", h.proposals["B"].job.id)] if decision == "approved" else [])
    assert len(result.tool_messages) == 1
    assert h.adapter_requests[-1].resume == {h.proposals["B"].link.interrupt_id: receipt(h).decision}
    assert not h.adapter_requests[-1].continue_from_checkpoint


@pytest.mark.asyncio
async def test_runtime_replay_then_successor_preserves_results_and_transcript_ids(runtime_harness):
    h = runtime_harness
    b = await claim_decision(h)
    first = await resume(h, b)
    replay = await resume(h, b)
    assert h.adapter_requests[-1].continue_from_checkpoint and h.adapter_requests[-1].resume is None
    assert [m.id for m in replay.tool_messages] == [m.id for m in first.tool_messages]
    assert len(replay.state["tool_results"]) == 2 and h.effects == [("B", h.proposals["B"].job.id)]

    def persist(metadata, status):
        metadata["pending_turn"] = replay.pending_turn
        metadata["control"] = {"control_state": "waiting_approval"}

    await h.store.update_status_and_mutate_metadata(h.session_id, status=None, mutate=persist,
        turn_lease_token=b.lease_token)
    await h.tasks.finish(b, status="completed", result_status="waiting_approval")
    a = await claim_decision(h, key="A")
    final = await resume(h, a)
    assert final.pending_turn == {} and final.output_text == "finished"
    assert final.state["context_bundle"]["deep_agent_approval_batch"] == []
    assert len(final.state["tool_results"]) == 2 and len(final.tool_messages) == 2
    assert first.tool_messages[0].id in {m.id for m in final.tool_messages}
    assert h.effects == [("B", h.proposals["B"].job.id), ("A", h.proposals["A"].job.id)]
    assert receipt(h, "A").applied_at is not None


@pytest.mark.asyncio
async def test_runtime_unknown_effect_stops_before_adapter(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        await h.jobs.claim_execution(h.proposals["B"].job.id)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconcil"):
        await resume(h, task)
    assert h.adapter_requests == [] and h.effects == [] and receipt(h).state == "queued"


@pytest.mark.asyncio
async def test_runtime_reconciles_native_progress_after_settlement_failure(runtime_harness, monkeypatch):
    h = runtime_harness
    task = await claim_decision(h)
    continuation = h.runtime._framework_approval_continuation
    settle = continuation.settle

    async def fail(*args):
        raise ConnectionError("injected settlement outage after native result")

    monkeypatch.setattr(continuation, "settle", fail)
    with pytest.raises(ConnectionError, match="settlement outage"):
        await resume(h, task)
    assert receipt(h).state == "delivered" and receipt(h).applied_at is None
    assert h.effects == [("B", h.proposals["B"].job.id)]
    monkeypatch.setattr(continuation, "settle", settle)
    result = await resume(h, task)
    assert h.adapter_requests[-1].continue_from_checkpoint and h.adapter_requests[-1].resume is None
    assert receipt(h).applied_at is not None
    assert len(result.tool_messages) == 1 and len(result.state["tool_results"]) == 2
    assert h.effects == [("B", h.proposals["B"].job.id)]


@pytest.mark.asyncio
async def test_runtime_interrupt_during_preparation_prevents_native_execution(runtime_harness, monkeypatch):
    h = runtime_harness
    task = await claim_decision(h)
    entered, release = asyncio.Event(), asyncio.Event()
    continuation = h.runtime._framework_approval_continuation
    prepare = continuation.prepare

    async def pause(*args):
        plan = await prepare(*args)
        entered.set()
        await release.wait()
        return plan

    monkeypatch.setattr(continuation, "prepare", pause)
    running = asyncio.create_task(resume(h, task))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        h.runtime.request_interrupt(h.session_id, "cancel during native continuation preparation")
        observed = h.runtime.is_interrupt_requested(h.session_id)
        release.set()
        result = await asyncio.wait_for(running, 3)
    finally:
        release.set()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert observed
    assert result.state["termination_reason"] == "interrupted"
    assert h.adapter_requests == [] and h.effects == [] and receipt(h).applied_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["turn", "binding", "namespace"])
async def test_runtime_restored_graph_identity_cannot_override_pending_binding(runtime_harness, changed):
    h = runtime_harness
    task = await claim_decision(h)
    session = await h.store.get_session(h.session_id)
    graph = session.metadata["pending_turn"]["graph_state"]
    if changed == "turn":
        graph["turn_id"] = "different-turn"
    elif changed == "binding":
        graph["context_bundle"]["runtime_binding"]["runtime_version"] = "different-version"
    else:
        graph["context_bundle"]["checkpoint_namespace"] = "different-namespace"
    with framework_owner_scope(task.runtime_owner), pytest.raises(FrameworkApprovalRecoveryRequired):
        await h.runtime.resume_framework_approval(session, task.reference_id, task.input["decision_id"])
    assert h.adapter_requests == [] and receipt(h).state == "queued"


@pytest.mark.asyncio
async def test_bound_runtime_cannot_resume_through_legacy_entry(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner), pytest.raises(FrameworkApprovalRecoveryRequired):
        await h.runtime.resume_after_approval(await h.store.get_session(h.session_id), task.input["approval"])
    assert h.adapter_requests == [] and h.effects == [] and receipt(h).state == "queued"


@pytest.mark.asyncio
async def test_claimed_session_approval_binds_owner_renews_it_and_deduplicates_transcript(runtime_harness, monkeypatch):
    h = runtime_harness
    task = await claim_decision(h)
    h.sessions._runtime_service = h.runtime
    renewed = asyncio.Event()
    owners = []
    original_resume = h.runtime.resume_framework_approval
    original_renew = h.tasks.renew_framework_execution

    async def renew(selected, seconds):
        assert selected == task
        value = await original_renew(selected, seconds)
        renewed.set()
        return value

    async def observe(*args, **kwargs):
        owners.append(current_framework_owner())
        await asyncio.wait_for(renewed.wait(), 2)
        return await original_resume(*args, **kwargs)

    monkeypatch.setattr(h.tasks, "renew_framework_execution", renew)
    monkeypatch.setattr(h.runtime, "resume_framework_approval", observe)
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        first = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
        first_owner = task.runtime_owner
        await h.tasks.finish(task, status="completed", result_status="waiting_approval")
        task = await claim_decision(h, key="A")
        again = await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert owners == [first_owner, task.runtime_owner]
    assert current_framework_owner() is None
    saved = await h.store.get_session(h.session_id)
    tool_messages = [message for message in saved.messages if message.metadata.get("tool_call_id")]
    assert len(tool_messages) == 2 and len({message.id for message in tool_messages}) == 2
    assert saved.metadata["pending_turn"] == {}
    assert first.session.status.value == "waiting_approval" and again.session.status.value == "completed"
    assert h.effects == [("B", h.proposals["B"].job.id), ("A", h.proposals["A"].job.id)]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["owner", "decision"])
async def test_claimed_framework_approval_rejects_missing_authority_before_runtime(runtime_harness, missing):
    h = runtime_harness
    task = await claim_decision(h)
    h.sessions._runtime_service = h.runtime
    if missing == "owner":
        task = task.model_copy(update={"runtime_owner": None})
    else:
        task = task.model_copy(update={"input": {"approval": task.input["approval"]}})
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        with pytest.raises((ContinuationLeaseLostError, ValueError), match="owner|decision"):
            await h.sessions.execute_claimed_approval(task, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert h.adapter_requests == [] and receipt(h).state == "queued"


@pytest.mark.asyncio
async def test_initial_runtime_and_session_continuations_share_the_official_harness(projection_harness):
    h = projection_harness
    # The resource fixture also observes an independent native graph. Start a
    # fresh bound Turn so this test imports input only through RuntimeService.
    await h.tasks.finish(h.owner, status="completed", result_status="completed")
    session = await h.store.get_session(h.session_id)
    session.status = SessionStatus.idle
    await h.store.save_session(session)
    h.binding = make_binding(h.session_id, f"runtime-turn-{uuid4().hex}")
    submitted = await h.tasks.submit_v2(make_command(h, h.binding), make_payload(),
        make_message(h, h.binding), idempotency_key=uuid4().hex)
    h.owner = await h.tasks.claim(submitted.id, worker_id="initial-runtime", expected_binding=h.binding)
    await h.tasks.begin(h.owner, resource_id="runtime-test", fencing_token=3)
    h.effects = []
    build_runtime(h, responses=[AIMessage(id="runtime-source", content="", tool_calls=[
        {"name": "inspect_value", "args": {"value": key}, "id": key, "type": "tool_call"}
        for key in ("A", "B")]), AIMessage(content="finished")])
    session = await h.store.get_session(h.session_id)
    request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="runtime-trace",
        user_message="Inspect A/B", normalized_input="Inspect A/B", mode_key="default", agent_key="coordinator",
        model_key="fake", runtime_binding=h.binding, context={"requested_tool_keys": ["inspect_value"]})
    with framework_owner_scope(h.owner.runtime_owner):
        first = await h.runtime.execute_turn(session, request)
    assert len(first.approvals) == 2 and h.effects == []
    assert len(first.state["tool_results"]) == 2
    by_call = {item["metadata"]["call_id"]: item for item in first.approvals}
    h.proposals = {key: SimpleNamespace(approval=SimpleNamespace(id=value["id"])) for key, value in by_call.items()}

    def persist(metadata, status):
        metadata["pending_turn"] = first.pending_turn
        metadata["control"] = {"control_state": "waiting_approval"}

    await h.store.update_status_and_mutate_metadata(h.session_id, status=None, mutate=persist,
        turn_lease_token=h.owner.lease_token)
    await h.tasks.finish(h.owner, status="completed", result_status="waiting_approval")
    h.sessions._runtime_service = h.runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        b = await claim_decision(h)
        partial = await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        assert partial.session.status.value == "waiting_approval"
        assert len(h.effects) == 1 and h.effects[0][0] == "B"
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
        a = await claim_decision(h, key="A")
        final = await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert final.output.content == "finished" and final.session.status.value == "completed"
    assert [value for value, job in h.effects] == ["B", "A"]
    saved = await h.store.get_session(h.session_id)
    assert saved.metadata["pending_turn"] == {}
    assert len([message for message in saved.messages if message.metadata.get("tool_call_id")]) == 2
