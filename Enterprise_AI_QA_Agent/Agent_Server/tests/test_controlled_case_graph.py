"""Direct StateGraph kernel; not the production RunItem/Worker migration."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from langgraph.types import Command

from src.application.deep_agents.native_interrupts import collect_pending_tool_interrupts, executing_tool_identity
from src.application.model_adapters.tool_adapter import LangChainToolAdapter
from src.runtime.execution_context import framework_owner_scope
from src.runtime.postgres_framework_model_calls import FrameworkModelBudgetExceeded, FrameworkModelRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.agent import ToolDescriptor
from tests.test_agent_task_dispatch import harness
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _tool_reply
from tests.test_framework_model_policy import governed


def build(h, model, saver, *, tools=(), interrupt_on=None):
    from src.graph.controlled_case import build_controlled_case_graph

    return build_controlled_case_graph(model=model, model_key=h.request.model_key,
        model_call_policy=h.policy, owner=h.owner.runtime_owner, checkpointer=saver,
        system_prompt="FIXED_CASE_CONTEXT: execute only the supplied frozen steps.",
        tools=list(tools), interrupt_on=interrupt_on or {})


def config(h):
    return {"configurable": {"thread_id": h.binding.thread_id}}


def initial():
    return {"messages": [HumanMessage(id="frozen-input", content="Execute the fixed fixture case.")]}


def wrapped_tools(h, effects, *, fail=False, entered=None, block=None):
    descriptor = ToolDescriptor(key="inspect_source", name="Inspect Source", description="Read fixture source",
        category="test", input_schema={"type": "object", "properties": {"path": {"type": "string"}},
            "required": ["path"]})

    async def invoke(descriptor, runtime, arguments):
        identity = executing_tool_identity(h.binding, runtime, descriptor.key, arguments)
        effects.append(identity)
        if entered is not None:
            entered.set()
        if block is not None:
            await block.wait()
        if fail:
            raise ContinuationLeaseLostError("controlled tool execution authority lost")
        return ToolMessage(content="fixture source evidence", tool_call_id=runtime.tool_call_id,
            name=descriptor.key)

    return LangChainToolAdapter().to_governed_tools([descriptor], invoke=invoke)


@asynccontextmanager
async def business_bridge(h, effects, *, approval_required, table_names=None):
    # Same original application services as test_framework_tool_bridge; only
    # the registered handler is synthetic. No external target or real command.
    from src.application.permissions.permission_service import PermissionService
    from src.application.runtime.runtime_service import RuntimeService
    from src.application.runtime.tool_job_service import ToolJobService
    from src.application.runtime.tool_runtime_service import ToolRuntimeService
    from src.registry.tools import ToolRegistry
    from src.runtime.control import RuntimeControlRegistry
    from src.runtime.framework_approval_projection import FrameworkApprovalProjection
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
    from tests.test_framework_tool_bridge import state_for
    from tests.test_live_postgres_concurrency import _drop_tables

    suffix = uuid4().hex
    job_table, artifact_table = table_names or (f"controlled_job_{suffix}", f"controlled_artifact_{suffix}")
    h.settings.database.postgres_tool_job_table = job_table
    h.settings.database.postgres_tool_artifact_table = artifact_table
    job_store = PostgresToolJobStore(h.settings)
    await job_store.initialize()
    jobs = ToolJobService(job_store)
    registry = ToolRegistry()
    registry.register_dynamic(ToolDescriptor(key="inspect_source", name="Inspect source", category="execution",
        description="Read only the fixture source", permission_level="ask" if approval_required else "safe",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
        "inspect_source")
    tools = ToolRuntimeService(tool_job_service=jobs)

    async def handle(arguments, context):
        effects.append((arguments, context.call_id, context.tool_job_id))
        return {"status": "completed", "summary": "fixture source evidence", "evidence": "fixture-only"}

    tools._handlers["inspect_source"] = handle
    projection = FrameworkApprovalProjection(h.settings, sessions=h.store, jobs=jobs)
    runtime = RuntimeService(graph=None, model_runtime_service=h.models, tool_runtime_service=tools,
        tool_registry=registry, runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=jobs, framework_approval_projection=projection, deep_agent_governed_tools_enabled=True)
    state = state_for(h)
    state.update(available_tool_keys=["inspect_source"], model_visible_tool_keys=["inspect_source"], event_log=[])
    state["context_bundle"]["framework_tool_projection_version"] = 1
    try:
        yield SimpleNamespace(runtime=runtime, projection=projection, jobs=jobs, state=state, records=[])
    finally:
        # Explicit names borrow the parent's tables in process-loss tests.
        # Only their creator may clean them up, including on child failures.
        if table_names is None:
            _drop_tables(h.settings, [h.settings.database.postgres_tool_artifact_table,
                h.settings.database.postgres_tool_job_table])


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_direct_graph_owns_the_tool_backedge_with_official_models_and_one_shared_root(harness, transport):
    reply = _tool_reply(transport)
    # The fixture's terminal response has a constant ID. A fresh Responses
    # request must not alias the first response and replace its source message.
    if transport == "openai_responses":
        reply["id"] = "resp_fixture_tool_call"
    with _provider(transport, first_reply=reply) as (url, requests):
        async with governed(harness, _config(transport, url),
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            effects = []
            with framework_owner_scope(h.owner.runtime_owner):
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                async with h.provider.open() as saver:
                    graph = build(h, model, saver, tools=wrapped_tools(h, effects))
                    assert set(graph.get_graph().nodes) == {"__start__", "model", "tools", "__end__"}
                    assert any(edge.source == "tools" and edge.target == "model" for edge in graph.get_graph().edges)
                    result = await graph.ainvoke(initial(), config(h), durability="sync")
                    snapshot = await graph.aget_state(config(h))
            assert result["messages"][-1].text == "fixture answer" and len(requests) == 2
            assert len(effects) == 1 and effects[0].arguments == {"path": "main.py"}
            assert effects[0].source_message_id == result["messages"][1].id
            assert messages_to_dict(snapshot.values["messages"]) == messages_to_dict(result["messages"])
            assert snapshot.next == () and len(result["messages"]) == 4
            assert isinstance(result["messages"][1], AIMessage) and isinstance(result["messages"][2], ToolMessage)
            assert "fixture source evidence" in str(requests[1]["body"])
            assert "FIXED_CASE_CONTEXT" in str(requests[0]["body"])
            assert "FIXED_CASE_CONTEXT" in str(requests[1]["body"])
            if transport == "anthropic_messages":
                assert requests[1]["body"]["messages"][-2]["content"][0]["signature"] == "fixture-signature"
            elif transport == "google_gemini_generate_content":
                assert requests[1]["body"]["contents"][-2]["parts"][0]["thoughtSignature"] == "Zml4dHVyZS1zaWduYXR1cmU="
            ledger = await h.calls.snapshot(h.binding)
            assert ledger.reserved_calls == 2 and all(call.phase == "completed" for call in ledger.calls)
            assert len({call.intent.logical_call_id for call in ledger.calls}) == 2
            assert {call.intent.branch_id for call in ledger.calls} == {"root"}
            assert {call.intent.purpose for call in ledger.calls} == {"agent"}
            assert ledger.actual_tokens == 20 and ledger.unknown_calls == 0
            # A tested kernel is not permission to claim it in the old Worker.
            with pytest.raises(TaskDeferred):
                FrameworkWorkerCapabilities.from_artifact(deepagents_available=True).require(h.binding, operation="message")


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_direct_graph_per_call_resume_rebuild_preserves_the_sibling_and_native_message_order(harness, decision):
    reply = _tool_reply("openai_chat_completions")
    call = reply["choices"][0]["message"]["tool_calls"][0]
    other = deepcopy(call)
    call["id"], call["function"]["arguments"] = "A", '{"path":"a.py"}'
    other["id"], other["function"]["arguments"] = "B", '{"path":"b.py"}'
    reply["choices"][0]["message"]["tool_calls"].append(other)
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            effects = []
            gating = {"inspect_source": {"allowed_decisions": ["approve", "reject"]}}
            with framework_owner_scope(h.owner.runtime_owner):
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                async with h.provider.open() as saver:
                    graph = build(h, model, saver, tools=wrapped_tools(h, effects), interrupt_on=gating)
                    waiting = await graph.ainvoke(initial(), config(h), durability="sync")
                    pending = await collect_pending_tool_interrupts(graph, saver, config(h))
                    assert len(waiting["__interrupt__"]) == len(pending) == 2
                    source = {entry.tool_call_id: entry for entry in pending}
                    assert len(requests) == 1 and effects == []
                    # Rebuild the same schema, then resume just B. A must remain native-pending.
                    graph = build(h, model, saver, tools=wrapped_tools(h, effects), interrupt_on=gating)
                    await graph.ainvoke(Command(resume={source["B"].interrupt_id: {"type": decision}}),
                        config(h), durability="sync")
                    remaining = await collect_pending_tool_interrupts(graph, saver, config(h))
                    assert remaining == [source["A"]] and len(requests) == 1
                    assert len(effects) == (1 if decision == "approve" else 0)
                    if effects:
                        assert effects[0].source_message_id == source["B"].source_message_id
                        assert effects[0].tool_call_id == "B"
                    graph = build(h, model, saver, tools=wrapped_tools(h, effects), interrupt_on=gating)
                    result = await graph.ainvoke(Command(resume={source["A"].interrupt_id: {"type": "reject"}}),
                        config(h), durability="sync")
                    saved = await graph.aget_state(config(h))
                    assert await collect_pending_tool_interrupts(graph, saver, config(h)) == []
            assert len(requests) == 2 and result["messages"][-1].text == "fixture answer"
            assert messages_to_dict(saved.values["messages"]) == messages_to_dict(result["messages"])
            assert [item.tool_call_id for item in result["messages"] if isinstance(item, ToolMessage)] == ["A", "B"]
            assert len(result["messages"]) == 5 and len(effects) == (1 if decision == "approve" else 0)
            assert (await h.calls.snapshot(h.binding)).reserved_calls == 2


@pytest.mark.asyncio
async def test_direct_graph_duplicate_response_identity_keeps_the_source_ai_and_does_not_retry(harness):
    # Actual first failing sample: both replies are resp_fixture; the official
    # message reducer would replace the source AI and orphan its ToolMessage.
    with _provider("openai_responses", first_reply=_tool_reply("openai_responses")) as (url, requests):
        async with governed(harness, _config("openai_responses", url),
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            effects = []
            with framework_owner_scope(h.owner.runtime_owner):
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                async with h.provider.open() as saver:
                    graph = build(h, model, saver, tools=wrapped_tools(h, effects))
                    with pytest.raises(ValueError, match="reused a message identity"):
                        await graph.ainvoke(initial(), config(h), durability="sync")
                    original = await graph.aget_state(config(h))
                    messages = original.values["messages"]
                    assert len(messages) == 3 and isinstance(messages[1], AIMessage)
                    assert messages[1].tool_calls[0]["id"] == messages[2].tool_call_id
                    assert messages[1].id == effects[0].source_message_id
                    ledger = await h.calls.snapshot(h.binding)
                    assert ledger.reserved_calls == 2 and all(call.phase == "completed" for call in ledger.calls)
                    with pytest.raises(FrameworkModelRecoveryRequired):
                        await graph.ainvoke(None, config(h), durability="sync")
                    saved = await graph.aget_state(config(h))
            assert messages_to_dict(saved.values["messages"]) == messages_to_dict(messages)
            assert len(requests) == 2 and len(effects) == 1
            assert await h.calls.snapshot(h.binding) == ledger


@pytest.mark.parametrize("missing", ["owner", "kind", "policy", "saver", "saver_owner"])
def test_controlled_graph_composition_requires_its_matching_owner_policy_and_saver(missing, caplog):
    from src.graph.controlled_case import build_controlled_case_graph

    owner = SimpleNamespace(binding=SimpleNamespace(runtime_kind="langgraph"))
    values = dict(model=object(), model_key="test", system_prompt="frozen", tools=[],
        model_call_policy=object(), owner=owner, checkpointer=SimpleNamespace(runtime_owner=owner), interrupt_on={})
    if missing == "kind":
        owner.binding.runtime_kind = "deepagents"
    elif missing == "saver_owner":
        values["checkpointer"] = SimpleNamespace(runtime_owner=object())
    else:
        values[{"policy": "model_call_policy", "saver": "checkpointer"}.get(missing, missing)] = None
    with pytest.raises(ValueError, match="LangGraph owner, model policy and captured saver"):
        build_controlled_case_graph(**values)
    assert "controlled_case_graph_composition_rejected" in caplog.text


def test_existing_model_middleware_import_path_is_the_same_shared_implementation():
    from src.application.deep_agents.model_call_middleware import FrameworkModelCallMiddleware as previous
    from src.application.models.framework_model_middleware import FrameworkModelCallMiddleware as shared

    assert previous is shared


def test_current_worker_defers_the_graph_kind_even_with_a_supported_version_and_artifact():
    from tests.test_runtime_binding_persistence import make_binding

    capabilities = FrameworkWorkerCapabilities.from_artifact(deepagents_available=True)
    binding = make_binding("controlled-session", "controlled-turn", runtime_kind="langgraph",
        runtime_version=capabilities.runtime_version, dependency_lock_sha256=capabilities.dependency_lock_sha256)
    with pytest.raises(TaskDeferred, match="framework_runtime_kind_unavailable"):
        capabilities.require(binding, operation="message")
    capabilities.require(binding.model_copy(update={"runtime_kind": "deepagents"}), operation="message")


@pytest.mark.asyncio
async def test_direct_graph_budget_exhaustion_stops_the_backedge_before_another_http_request(harness):
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url), max_calls=1,
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            effects = []
            with framework_owner_scope(h.owner.runtime_owner):
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                async with h.provider.open() as saver:
                    graph = build(h, model, saver, tools=wrapped_tools(h, effects))
                    with pytest.raises(FrameworkModelBudgetExceeded):
                        await graph.ainvoke(initial(), config(h), durability="sync")
            assert len(requests) == 1 and len(effects) == 1
            assert (await h.calls.snapshot(h.binding)).reserved_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_direct_graph_unknown_provider_request_is_not_reissued_on_native_continue(harness, transport):
    with _provider(transport, status=503) as (url, requests):
        async with governed(harness, _config(transport, url),
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            with framework_owner_scope(h.owner.runtime_owner):
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                async with h.provider.open() as saver:
                    graph = build(h, model, saver)
                    with pytest.raises(Exception, match="controlled fixture failure"):
                        await graph.ainvoke(initial(), config(h), durability="sync")
                    before = await h.calls.snapshot(h.binding)
                    with pytest.raises(FrameworkModelRecoveryRequired):
                        await graph.ainvoke(None, config(h), durability="sync")
            assert len(requests) == 1 and before.unknown_calls == before.reserved_calls == 1
            assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_direct_graph_does_not_convert_tool_authority_loss_into_a_model_observation(harness):
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            effects = []
            with framework_owner_scope(h.owner.runtime_owner):
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                async with h.provider.open() as saver:
                    graph = build(h, model, saver, tools=wrapped_tools(h, effects, fail=True))
                    with pytest.raises(ContinuationLeaseLostError, match="authority lost"):
                        await graph.ainvoke(initial(), config(h), durability="sync")
            assert len(requests) == len(effects) == 1


@pytest.mark.asyncio
async def test_direct_graph_cancellation_does_not_reenter_the_model_from_a_cancelled_tool(harness):
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            entered, block, effects = asyncio.Event(), asyncio.Event(), []
            with framework_owner_scope(h.owner.runtime_owner):
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                async with h.provider.open() as saver:
                    graph = build(h, model, saver, tools=wrapped_tools(h, effects, entered=entered, block=block))
                    pending = asyncio.create_task(graph.ainvoke(initial(), config(h), durability="sync"))
                    try:
                        await asyncio.wait_for(entered.wait(), 15)
                        pending.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await pending
                    finally:
                        if not pending.done():
                            pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
            assert len(requests) == len(effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_required", [False, True])
async def test_direct_graph_uses_original_governance_job_approval_and_projection(harness, approval_required):
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.runtime.framework_approval_journal import FrameworkApprovalJournal
    from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY

    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=approval_required) as bridge:
                with framework_owner_scope(h.owner.runtime_owner):
                    wrapped, gating = await bridge.runtime._build_deep_agent_business_tools(
                        state=bridge.state, tool_records=bridge.records)
                    model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                    async with h.provider.open() as saver:
                        graph = build(h, model, saver, tools=wrapped, interrupt_on=gating)
                        result = await graph.ainvoke(initial(), config(h), durability="sync")
                        if approval_required:
                            pending, = await collect_pending_tool_interrupts(graph, saver, config(h))
                            assert effects == [] and len(requests) == 1
                            assert await bridge.jobs.list_jobs(session_id=h.session_id) == []
                            proposal = bridge.runtime._tool_governance_service.prepare_framework_approval(
                                h.binding, pending, bridge.runtime._deep_agent_governance_context(bridge.state))
                            saved = await bridge.projection.project(h.owner.runtime_owner, proposal)
                            assert (await bridge.jobs.get_job(saved.job.id)).status.value == "waiting_approval"
                            with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                                cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='approved', "
                                    "resolved_at=clock_timestamp() WHERE id=%s", (saved.approval.id,))
                                receipt = FrameworkApprovalJournal(h.settings).queue_decision_in_transaction(cur,
                                    h.binding, saved.approval.id, decision_id="controlled-kernel-approval")
                            # Rebuild from original native/business records, not an in-memory approval batch.
                            wrapped, gating = await bridge.runtime._build_deep_agent_business_tools(
                                state=bridge.state, tool_records=bridge.records)
                            graph = build(h, model, saver, tools=wrapped, interrupt_on=gating)
                            result = await graph.ainvoke(Command(resume={pending.interrupt_id: receipt.decision}),
                                config(h), durability="sync")
                        native = await graph.aget_state(config(h))
                        assert native.next == () and await collect_pending_tool_interrupts(graph, saver, config(h)) == []
                        assert messages_to_dict(native.values["messages"]) == messages_to_dict(result["messages"])
                assert len(requests) == 2 and result["messages"][-1].text == "fixture answer"
                job, = await bridge.jobs.list_jobs(session_id=h.session_id)
                assert job.status.value == "completed" and job.started_at is not None and job.completed_at is not None
                assert effects == [({"path": "main.py"}, job.call_id, job.id)]
                tool_message, = [item for item in result["messages"] if isinstance(item, ToolMessage)]
                material = FrameworkToolProjection.model_validate(tool_message.artifact[TOOL_PROJECTION_KEY])
                material.require_binding(h.binding, material.source)
                assert material.source.source_message_id == result["messages"][1].id
                assert material.record.call_id == job.call_id and material.record.job_id == job.id
                assert material.record.started_at == job.started_at and material.record.completed_at == job.completed_at
                assert material.record.approval_id == (saved.approval.id if approval_required else None)
                assert bridge.records == [material.record.model_dump(mode="python")]
                assert (await h.calls.snapshot(h.binding)).reserved_calls == 2


@pytest.mark.asyncio
async def test_direct_graph_captured_saver_does_not_authorize_an_expired_owner(harness):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url),
                binding_overrides={"runtime_kind": "langgraph", "runtime_version": "controlled-kernel-test"}) as h:
            with framework_owner_scope(h.owner.runtime_owner):
                model = await h.models.resolve_langchain_chat_model(h.request.model_key)
                async with h.provider.open() as saver:
                    graph = build(h, model, saver)
                    before = await h.calls.snapshot(h.binding)
                    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
                    with pytest.raises(ContinuationLeaseLostError):
                        await graph.ainvoke(initial(), config(h), durability="sync")
            assert requests == [] and await h.calls.snapshot(h.binding) == before
