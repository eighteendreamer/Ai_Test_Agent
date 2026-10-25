from __future__ import annotations

import pytest
from types import SimpleNamespace
from dataclasses import replace
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from src.application.deep_agents.native_interrupts import collect_pending_tool_interrupts
from src.application.model_adapters.tool_adapter import LangChainToolAdapter
from src.application.permissions.permission_service import PermissionService
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_context import framework_owner_scope
from src.schemas.framework_approval import framework_tool_call_id
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config, start
from tests.test_framework_approval_projection import projection_harness, prepare, projector


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def state_for(h):
    return {"session_id": h.session_id, "turn_id": h.binding.turn_id, "trace_id": "projection-trace",
        "user_message": "Inspect A/B", "normalized_input": "Inspect A/B", "mode_key": "default",
        "selected_agent_key": "coordinator", "selected_model_key": "fake", "session_mode": "normal",
        "runtime_mode": "interactive", "available_tool_keys": ["inspect_value"],
        "model_visible_tool_keys": ["inspect_value"],
        "context_bundle": {"runtime_binding": h.binding.model_dump(mode="json")}}


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_framework_injected_tool_identity_matches_saved_interrupt(checkpoint_harness, nested):
    from deepagents import create_deep_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langgraph.graph import StateGraph, MessagesState, START, END
    from src.schemas.agent import ToolDescriptor

    h = checkpoint_harness
    await start(h)
    received = []

    async def invoke(descriptor, runtime, arguments):
        from src.application.deep_agents.native_interrupts import executing_tool_identity
        received.append(executing_tool_identity(h.binding, runtime, descriptor.key, arguments))
        return "observed"

    descriptor = ToolDescriptor(key="inspect_value", name="Inspect value", description="Inspect a test value",
        category="test", input_schema={"type": "object", "properties": {"value": {"type": "string"}},
            "required": ["value"]})
    wrapped = LangChainToolAdapter().to_governed_tools([descriptor], invoke=invoke)
    call = {"name": "inspect_value", "args": {"value": "same"}, "id": "reused", "type": "tool_call"}
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        agent = create_deep_agent(model=Model(responses=[AIMessage(id="source-1", content="", tool_calls=[call]),
            AIMessage(id="source-2", content="", tool_calls=[call]), AIMessage(content="finished")]), tools=wrapped,
            middleware=[HumanInTheLoopMiddleware(interrupt_on={"inspect_value": {
                "allowed_decisions": ["approve", "reject"]}}, interrupt_mode="per_call")], checkpointer=saver)
        if nested:
            parent = StateGraph(MessagesState)
            parent.add_node("review", agent)
            parent.add_edge(START, "review")
            parent.add_edge("review", END)
            agent = parent.compile(checkpointer=saver)
        await agent.ainvoke({"messages": [{"role": "user", "content": "inspect"}]}, config(h), durability="sync")
        for index in range(2):
            identities = await collect_pending_tool_interrupts(agent, saver, config(h))
            assert len(identities) == 1
            await agent.ainvoke(Command(resume={identities[0].interrupt_id: {"type": "approve"}}),
                config(h), durability="sync")
            assert len(received) == index + 1
            native = identities[0]
            assert framework_tool_call_id(h.binding, received[index]) == framework_tool_call_id(h.binding, native)
            assert received[index].source_message_id == native.source_message_id
            assert received[index].checkpoint_namespace == native.checkpoint_namespace
        assert framework_tool_call_id(h.binding, received[0]) != framework_tool_call_id(h.binding, received[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_provider_id", [False, True])
async def test_bound_bridge_loads_current_decision_without_batch_and_executes_original_job(projection_harness, reuse_provider_id):
    from deepagents import create_deep_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from src.runtime.framework_approval_journal import FrameworkApprovalJournal

    h = projection_harness
    proposals = [await projector(h).project(h.owner.runtime_owner, prepare(h, index)) for index in range(2)]
    selected = next(item for item in proposals if item.link.tool_call_id == "B")
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='approved', "
            "resolved_at=clock_timestamp() WHERE id=%s", (selected.approval.id,))
        receipt_b = FrameworkApprovalJournal(h.settings).queue_decision_in_transaction(cur, h.binding,
            selected.approval.id, decision_id="bridge-B")

    calls = []
    tools = ToolRuntimeService(tool_job_service=h.jobs)

    async def handle(arguments, context):
        calls.append((arguments, context.call_id, context.tool_job_id))
        return {"status": "completed", "summary": "inspected B", "evidence": "B"}

    tools._handlers["inspect_value"] = handle
    runtime = RuntimeService(graph=None, model_runtime_service=object(), tool_runtime_service=tools,
        tool_registry=h.registry, runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=h.jobs, framework_approval_projection=projector(h), deep_agent_governed_tools_enabled=True)
    state, records = state_for(h), []
    model = Model(responses=([AIMessage(id="second-source", content="", tool_calls=[{
        "name": "inspect_value", "args": {"value": "second B"}, "id": "B", "type": "tool_call"}])]
        if reuse_provider_id else []) + [AIMessage(content="finished")])
    # This graph is rebuilt without the process-local approval batch. Its only
    # decision authority is the saved business approval and native checkpoint.
    with framework_owner_scope(h.owner.runtime_owner):
        wrapped, gating = await runtime._build_deep_agent_business_tools(state=state, tool_records=records)
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            agent = create_deep_agent(model=model, tools=wrapped,
                middleware=[HumanInTheLoopMiddleware(interrupt_on=gating, interrupt_mode="per_call")], checkpointer=saver)
            await agent.ainvoke(Command(resume={selected.link.interrupt_id: receipt_b.decision}),
                config(h), durability="sync")
            assert calls == [({"value": "B"}, selected.job.call_id, selected.job.id)]
            remaining = await collect_pending_tool_interrupts(agent, saver, config(h))
            assert [item.tool_call_id for item in remaining] == ["A"]
            await agent.ainvoke(None, config(h), durability="sync")
            assert len(calls) == 1
            native = await agent.aget_state(config(h))
            if reuse_provider_id:
                a = next(item for item in proposals if item.link.tool_call_id == "A")
                with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                    cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='denied', "
                        "resolved_at=clock_timestamp() WHERE id=%s", (a.approval.id,))
                    receipt_a = FrameworkApprovalJournal(h.settings).queue_decision_in_transaction(cur, h.binding,
                        a.approval.id, decision_id="bridge-A")
                h.registry.get("inspect_value").permission_level = "safe"
                # Existing A must still consume reject; the next AI's reused B
                # is a separate allowed call, with no inherited old approval.
                wrapped, gating = await runtime._build_deep_agent_business_tools(state=state, tool_records=records)
                agent = create_deep_agent(model=model, tools=wrapped,
                    middleware=[HumanInTheLoopMiddleware(interrupt_on=gating, interrupt_mode="per_call")], checkpointer=saver)
                await agent.ainvoke(Command(resume={a.link.interrupt_id: receipt_a.decision}), config(h), durability="sync")
                assert len(calls) == 2 and calls[1][0] == {"value": "second B"}
                assert calls[1][1] != calls[0][1] and calls[1][2] != calls[0][2]
                assert records[1]["approval_id"] is None
    assert (await h.jobs.get_job(selected.job.id)).status.value == "completed"
    assert len(await h.jobs.list_jobs(session_id=h.session_id)) == (3 if reuse_provider_id else 2)
    assert records[0]["call_id"] == selected.job.call_id and records[0]["approval_id"] == selected.approval.id
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        delivered = FrameworkApprovalJournal(h.settings).get_receipt_in_transaction(cur, selected.approval.id)
        assert delivered.state == "delivered" and delivered.applied_at is None
    b_task = next(task for task in native.tasks if task.id == selected.link.graph_task_id)
    assert any(isinstance(item, ToolMessage) and item.tool_call_id == "B" for item in b_task.result["messages"])


@pytest.mark.asyncio
async def test_rebuilt_gate_honors_rejection_after_policy_is_relaxed(projection_harness, monkeypatch):
    from deepagents import create_deep_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    h = projection_harness
    proposals = [await projector(h).project(h.owner.runtime_owner, prepare(h, index)) for index in range(2)]
    selected = next(item for item in proposals if item.link.tool_call_id == "B")
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='denied', "
            "resolved_at=clock_timestamp() WHERE id=%s", (selected.approval.id,))
    runtime = RuntimeService(graph=None, model_runtime_service=object(), tool_runtime_service=object(),
        tool_registry=h.registry, runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=h.jobs, framework_approval_projection=projector(h), deep_agent_governed_tools_enabled=True)
    # A dynamic predicate alone would now skip interrupt(), ignoring the saved
    # reject. Durable full call identities must keep the native gate in place.
    monkeypatch.setattr(runtime._tool_governance_service, "requires_approval", lambda *args: False)
    async def must_not_execute(*args, **kwargs):
        pytest.fail("native reject must not enter business execution")
    monkeypatch.setattr(runtime._tool_governance_service, "execute", must_not_execute)
    state = state_for(h)
    with framework_owner_scope(h.owner.runtime_owner):
        wrapped, gating = await runtime._build_deep_agent_business_tools(state=state, tool_records=[])
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            agent = create_deep_agent(model=Model(responses=[AIMessage(content="finished")]), tools=wrapped,
                middleware=[HumanInTheLoopMiddleware(interrupt_on=gating, interrupt_mode="per_call")], checkpointer=saver)
            await agent.ainvoke(Command(resume={selected.link.interrupt_id: {"type": "reject", "message": "human no"}}),
                config(h), durability="sync")
            pending = await collect_pending_tool_interrupts(agent, saver, config(h))
            assert [item.tool_call_id for item in pending] == ["A"]
            snapshot = await agent.aget_state(config(h))
            result = next(task.result for task in snapshot.tasks if task.id == selected.link.graph_task_id)
            assert any(isinstance(item, ToolMessage) and item.tool_call_id == "B" and item.status == "error"
                for item in result["messages"])
    # Native rejection skips the callback. Business denied-result projection is
    # a separate continuation responsibility and is not claimed by this test.
    assert (await h.jobs.get_job(selected.job.id)).status.value == "waiting_approval"


@pytest.mark.parametrize("corruption", ["thread", "namespace", "missing_info", "missing_source", "args", "duplicate"])
def test_execution_identity_rejects_mismatched_injected_facts(corruption):
    from langgraph.runtime import ExecutionInfo
    from src.application.deep_agents.native_interrupts import executing_tool_identity
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    call = {"name": "inspect_value", "args": {"value": "x"}, "id": "B", "type": "tool_call"}
    message = AIMessage(id="source", content="", tool_calls=[call])
    info = ExecutionInfo(thread_id="turn", checkpoint_id="checkpoint", checkpoint_ns="tools:task", task_id="task")
    if corruption == "thread":
        info = replace(info, thread_id="another")
    elif corruption == "namespace":
        info = replace(info, checkpoint_ns="tools:another")
    elif corruption == "missing_info":
        info = None
    elif corruption == "missing_source":
        message.id = None
    elif corruption == "args":
        message.tool_calls[0]["args"] = {"value": "another"}
    else:
        message.tool_calls.append(dict(call))
    runtime = SimpleNamespace(execution_info=info, tool_call_id="B", state={"messages": [message]})
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        executing_tool_identity(SimpleNamespace(thread_id="turn"), runtime, "inspect_value", {"value": "x"})


@pytest.mark.asyncio
async def test_tool_approval_lookup_checks_arguments_and_owner(projection_harness):
    from src.schemas.framework_approval import FrameworkToolCallIdentity
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
    from src.runtime.store import ContinuationLeaseLostError

    h = projection_harness
    service = projector(h)
    proposal = await service.project(h.owner.runtime_owner, prepare(h))
    identity = FrameworkToolCallIdentity.model_validate(proposal.link.model_dump(include=set(FrameworkToolCallIdentity.model_fields)))
    assert await service.gate_call_ids(h.owner.runtime_owner) == {proposal.job.call_id}
    assert (await service.find_for_call(h.owner.runtime_owner, identity)).id == proposal.approval.id
    # Same stable ID with changed input cannot inherit the earlier permission.
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="differs"):
        await service.find_for_call(h.owner.runtime_owner, identity.model_copy(update={"arguments": {"value": "different"}}))
    # A different AI source is a new call, not a lookup by reused provider ID.
    assert await service.find_for_call(h.owner.runtime_owner,
        identity.model_copy(update={"source_message_id": "another-source"})) is None
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await service.gate_call_ids(h.owner.runtime_owner)
    with pytest.raises(ContinuationLeaseLostError):
        await service.find_for_call(h.owner.runtime_owner, identity)
    assert (await h.jobs.get_job(proposal.job.id)).status.value == "waiting_approval"


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", ["completed", "waiting_approval", "running"])
async def test_governance_denial_cannot_replace_a_prior_terminal_result(projection_harness, job_status):
    from deepagents import create_deep_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    h = projection_harness
    proposals = [await projector(h).project(h.owner.runtime_owner, prepare(h, index)) for index in range(2)]
    b = next(item for item in proposals if item.link.tool_call_id == "B")
    # A tool result exists but its framework task has not committed. Subsequent
    # policy revocation must not rewrite this original result as non-execution.
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='approved', "
            "resolved_at=clock_timestamp() WHERE id=%s", (b.approval.id,))
        if job_status != "waiting_approval":
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET status=%s, "
                "started_at=clock_timestamp(), summary='original result', output_payload=%s::jsonb WHERE id=%s",
                (job_status, '{"evidence":"original"}' if job_status == "completed" else '{}', b.job.id))
    h.registry.get("inspect_value").permission_level = "restricted"
    tools = ToolRuntimeService(tool_job_service=h.jobs)
    async def must_not_execute(*args):
        pytest.fail("a terminal call must not dispatch again")
    tools._handlers["inspect_value"] = must_not_execute
    runtime = RuntimeService(graph=None, model_runtime_service=object(), tool_runtime_service=tools,
        tool_registry=h.registry, runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=h.jobs, framework_approval_projection=projector(h), deep_agent_governed_tools_enabled=True)
    state, records = state_for(h), []
    with framework_owner_scope(h.owner.runtime_owner):
        wrapped, gating = await runtime._build_deep_agent_business_tools(state=state, tool_records=records)
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            agent = create_deep_agent(model=Model(responses=[AIMessage(content="finished")]), tools=wrapped,
                middleware=[HumanInTheLoopMiddleware(interrupt_on=gating, interrupt_mode="per_call")], checkpointer=saver)
            if job_status == "running":
                from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
                with pytest.raises(FrameworkApprovalRecoveryRequired, match="reconciled"):
                    await agent.ainvoke(Command(resume={b.link.interrupt_id: {"type": "approve"}}), config(h), durability="sync")
            else:
                await agent.ainvoke(Command(resume={b.link.interrupt_id: {"type": "approve"}}), config(h), durability="sync")
    saved = await h.jobs.get_job(b.job.id)
    if job_status == "completed":
        assert saved.status.value == "completed" and saved.output_payload == {"evidence": "original"}
        assert records[0]["status"] == "completed" and records[0]["output"]["evidence"] == "original"
    elif job_status == "waiting_approval":
        assert saved.status.value == "denied" and saved.started_at is None
        assert records[0]["status"] == "denied" and records[0]["output"]["error"] == "permission_denied"
    else:
        assert saved.status.value == "running" and saved.output_payload == {} and records == []
