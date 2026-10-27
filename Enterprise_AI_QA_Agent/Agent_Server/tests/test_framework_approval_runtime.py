from __future__ import annotations

import asyncio
from datetime import timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage

from src.application.deep_agents import DeepAgentRuntimeAdapter
from src.application.permissions.permission_service import PermissionService
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_context import framework_owner_scope
from src.runtime.execution_context import current_framework_owner
from src.runtime.session_event_sink import SessionEventSink
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.session import ExecutionRequest, SessionStatus
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness, projector
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, receipt, service
from tests.test_framework_tool_bridge import Model, state_for
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


def build_runtime(h, *, responses=None, model_call_policy=None):
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
        deep_agent_runtime_adapter=Adapter(model_resolver=resolve_model, checkpointer_factory=h.provider.open,
            model_call_policy=model_call_policy),
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


@pytest.fixture
def root_budget_provider():
    from tests.test_framework_chat_models import _provider, _tool_reply

    reply = _tool_reply("openai_chat_completions")
    reply["choices"][0]["message"]["tool_calls"] = [{"id": key, "type": "function", "function": {
        "name": "inspect_value", "arguments": json.dumps({"value": key})}} for key in ("A", "B")]
    with _provider("openai_chat_completions", first_reply=reply) as provider:
        yield provider


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_waiting_and_completed_invocations_preserve_the_same_root_budget(projection_harness, root_budget_provider, decision):
    from datetime import datetime, timedelta
    from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
    from src.application.models.framework_model_budget_projection import read_model_budget_material
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.schemas.framework_model_call import FrameworkModelBudget
    from tests.test_framework_chat_models import _config

    h = projection_harness
    url, requests = root_budget_provider
    config = _config("openai_chat_completions", url, key="fake")
    fingerprint = model_config_fingerprint(config)
    await h.tasks.finish(h.owner, status="completed", result_status="completed")
    session = await h.store.get_session(h.session_id)
    session.status = SessionStatus.idle
    await h.store.save_session(session)
    h.binding = make_binding(h.session_id, f"budget-turn-{uuid4().hex}", model_config_fingerprint=fingerprint)
    submitted = await h.tasks.submit_v2(make_command(h, h.binding), make_payload(), make_message(h, h.binding),
        idempotency_key=uuid4().hex)
    h.owner = await h.tasks.claim(submitted.id, worker_id="root-budget-runtime", expected_binding=h.binding)
    await h.tasks.begin(h.owner, resource_id="runtime-test", fencing_token=3)
    h.effects = []
    calls = PostgresFrameworkModelCalls(h.settings)
    await calls.initialize()
    try:
        await calls.freeze(h.owner.runtime_owner, FrameworkModelBudget(max_calls=8, max_tokens=500_000,
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5), model_config_fingerprints={"fake": fingerprint}))
        policy = FrameworkModelCallPolicy(calls=calls, model_config_resolver=lambda key: config)
        build_runtime(h, model_call_policy=policy)
        models = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), h.settings)
        h.runtime._model_runtime_service = models
        adapter = h.runtime._deep_agent_bound_runtime_adapter
        adapter._model_resolver = models.resolve_langchain_chat_model
        request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="budget-trace",
            user_message="Inspect A/B", normalized_input="Inspect A/B", mode_key="default", agent_key="coordinator",
            model_key="fake", runtime_binding=h.binding, context={"requested_tool_keys": ["inspect_value"]})
        with framework_owner_scope(h.owner.runtime_owner):
            first = await h.runtime.execute_turn(await h.store.get_session(h.session_id), request)
        expected = await calls.snapshot(h.binding)
        assert len(first.approvals) == 2 and expected.reserved_calls == 1
        by_call = {item["metadata"]["call_id"]: item for item in first.approvals}
        h.proposals = {key: SimpleNamespace(approval=SimpleNamespace(id=item["id"])) for key, item in by_call.items()}
        for key, previous in (("B", first), ("A", None)):
            if previous is not None:
                pending = previous.pending_turn
            assert read_model_budget_material(h.binding, pending["graph_state"]["model_response_summary"]["root_model_budget"]) == expected
            assert previous is None or previous.snapshot.graph_state["model_response_summary"]["root_model_budget"] == pending["graph_state"]["model_response_summary"]["root_model_budget"]
            await h.store.update_status_and_mutate_metadata(h.session_id, status=None,
                mutate=lambda metadata, status: metadata.update(pending_turn=pending), turn_lease_token=h.owner.lease_token)
            await h.tasks.finish(h.owner, status="completed", result_status="waiting_approval")
            h.owner = await claim_decision(h, key=key, decision=decision if key == "B" else "approved")
            result = await resume(h, h.owner)
            if key == "B":
                pending = result.pending_turn
                assert await calls.snapshot(h.binding) == expected
            else:
                final = await calls.snapshot(h.binding)
                assert len(requests) == final.reserved_calls == 2
                assert final.actual_tokens == 20 and final.unmeasured_calls == 0
                assert read_model_budget_material(h.binding, result.state["model_response_summary"]["root_model_budget"]) == final
                assert read_model_budget_material(h.binding, first.state["model_response_summary"]["root_model_budget"]) == expected
        assert [value for value, job in h.effects] == (["B", "A"] if decision == "approved" else ["A"])
        assert result.output_text == "fixture answer"
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE {calls.calls_table}")
            cur.execute(f"DROP TABLE {calls.budgets_table}")


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
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_runtime_replay_then_successor_preserves_results_and_transcript_ids(runtime_harness, decision):
    h = runtime_harness
    b = await claim_decision(h, decision=decision)
    first = await resume(h, b)
    replay = await resume(h, b)
    assert h.adapter_requests[-1].continue_from_checkpoint and h.adapter_requests[-1].resume is None
    assert replay.tool_messages == first.tool_messages
    assert len(replay.state["tool_results"]) == 2
    expected_effects = [("B", h.proposals["B"].job.id)] if decision == "approved" else []
    assert h.effects == expected_effects

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
    assert first.tool_messages[0] == next(m for m in final.tool_messages if m.id == first.tool_messages[0].id)
    assert h.effects == expected_effects + [("A", h.proposals["A"].job.id)]
    assert receipt(h, "A").applied_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("offloaded", [False, True])
async def test_prepared_approval_consumes_original_tool_material_without_reprocessing_output(runtime_harness, monkeypatch, offloaded):
    from langchain_core.messages import ToolMessage
    from src.application.deep_agents.framework_transcript import deserialize_messages
    from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY
    from src.schemas.tool_runtime import ToolExecutionRecord

    h = runtime_harness
    if offloaded:
        from deepagents import create_deep_agent
        from deepagents.middleware.filesystem import FilesystemMiddleware

        def factory(**kwargs):
            return create_deep_agent(**{**kwargs, "middleware": [*kwargs.get("middleware", []),
                FilesystemMiddleware(tool_token_limit_before_evict=1)]})

        monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "_resolve_factory", lambda: factory)
    session = await h.store.get_session(h.session_id)
    pending = session.metadata["pending_turn"]
    # Exercise the prepared material contract on the existing real approval
    # harness, without changing its source AI/interrupt/Job identities.
    pending["context_bundle"]["framework_tool_projection_version"] = 1
    pending["graph_state"]["context_bundle"]["framework_tool_projection_version"] = 1
    await h.store.save_session(session)

    async def forbidden(*args, **kwargs):
        pytest.fail("settled output was processed again instead of consuming its original native material")

    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    task = await claim_decision(h)
    result = await resume(h, task)
    native, = [message for message in deserialize_messages(result.state["framework_messages"])
        if isinstance(message, ToolMessage)]
    material = FrameworkToolProjection.model_validate(native.artifact[TOOL_PROJECTION_KEY])
    assert (native.content != material.model_content) is offloaded
    assert result.tool_messages == [material.message]
    record, = [item for item in result.state["tool_results"] if item["status"] != "waiting_approval"]
    assert ToolExecutionRecord.model_validate(record) == material.record
    assert h.effects == [("B", h.proposals["B"].job.id)] and receipt(h).applied_at is not None
    replay = await resume(h, task)
    assert replay.tool_messages == result.tool_messages and h.effects == [("B", h.proposals["B"].job.id)]
    events, original_receipt = await h.store.list_events(h.session_id), receipt(h)
    with framework_owner_scope(task.runtime_owner):
        read = await h.runtime._framework_approval_continuation.read_tool_projection(
            task.runtime_owner, task.reference_id, task.input["decision_id"])
    assert read == material and receipt(h) == original_receipt
    assert await h.store.list_events(h.session_id) == events


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["before_applied", "native", "job", "owner", "approval"])
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_original_tool_material_read_is_bound_to_current_applied_facts(runtime_harness, changed, decision):
    from src.infrastructure.postgres_runtime import postgres_connect

    h = runtime_harness
    session = await h.store.get_session(h.session_id)
    pending = session.metadata["pending_turn"]
    pending["context_bundle"]["framework_tool_projection_version"] = 2
    pending["graph_state"]["context_bundle"]["framework_tool_projection_version"] = 2
    await h.store.save_session(session)
    task = await claim_decision(h, decision=decision)
    if changed != "before_applied":
        await resume(h, task)
    proposal = h.proposals["B"]
    if changed == "owner":
        assert await h.store.renew_turn_execution(h.session_id, task.lease_token, 0)
    elif changed in {"native", "job"}:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            if changed == "native":
                cur.execute(f"DELETE FROM {h.settings.deep_agents.checkpoint_schema}.checkpoint_writes "
                    "WHERE thread_id=%s AND checkpoint_id=%s AND task_id=%s AND channel='messages'",
                    (h.binding.thread_id, proposal.link.checkpoint_id, proposal.link.graph_task_id))
            else:
                cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET output_payload=%s::jsonb WHERE id=%s",
                    ('{"evidence":"changed after settlement"}', proposal.job.id))
    original_receipt, events = receipt(h), await h.store.list_events(h.session_id)
    with framework_owner_scope(task.runtime_owner), pytest.raises(
        ContinuationLeaseLostError if changed == "owner" else FrameworkApprovalRecoveryRequired):
        await h.runtime._framework_approval_continuation.read_tool_projection(task.runtime_owner,
            h.proposals["A"].approval.id if changed == "approval" else task.reference_id, task.input["decision_id"])
    assert receipt(h) == original_receipt and await h.store.list_events(h.session_id) == events
    assert h.effects == ([] if changed == "before_applied" or decision == "denied" else [("B", proposal.job.id)])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "after_commit"])
@pytest.mark.parametrize("projection_version", [2, 3])
async def test_rejection_material_and_applied_share_one_transaction_and_replay_original(runtime_harness, monkeypatch, failure, projection_version):
    from src.application.runtime.tool_messages import build_tool_message
    from src.schemas.framework_tool_projection import parse_tool_projection, TOOL_PROJECTION_KEY
    from src.schemas.tool_runtime import ToolExecutionRecord

    h = runtime_harness
    session = await h.store.get_session(h.session_id)
    for state in (session.metadata["pending_turn"], session.metadata["pending_turn"]["graph_state"]):
        state["context_bundle"]["framework_tool_projection_version"] = projection_version
        if projection_version == 3:
            state["tool_messages"] = [build_tool_message(ToolExecutionRecord.model_validate(record))
                for record in state["tool_results"]]
    await h.store.save_session(session)
    task = await claim_decision(h, decision="denied")
    continuation = h.runtime._framework_approval_continuation
    if failure == "rollback":
        append = h.store.append_event_in_transaction

        def fail(cur, session_id, event, **kwargs):
            if event.type == "approval.decision_applied":
                raise ConnectionError("injected rejection application rollback")
            return append(cur, session_id, event, **kwargs)

        monkeypatch.setattr(h.store, "append_event_in_transaction", fail)
    else:
        read = continuation.read_tool_projection

        async def fail(*args):
            raise ConnectionError("injected after rejection application commit")

        monkeypatch.setattr(continuation, "read_tool_projection", fail)
    with pytest.raises(ConnectionError, match="injected"):
        await resume(h, task)
    saved = receipt(h)
    job = await h.jobs.get_job(h.proposals["B"].job.id)
    assert saved.state == "delivered" and h.effects == []
    if failure == "rollback":
        assert saved.applied_at is None and "application" not in saved.proof
        assert job.status.value == "waiting_approval" and job.completed_at is None
        monkeypatch.setattr(h.store, "append_event_in_transaction", append)
    else:
        assert saved.applied_at is not None and job.status.value == "denied"
        original = parse_tool_projection(saved.proof["application"][TOOL_PROJECTION_KEY])
        assert original.record.completed_at == original.message.created_at == job.completed_at
        monkeypatch.setattr(continuation, "read_tool_projection", read)

        def forbidden(*args, **kwargs):
            pytest.fail("an applied rejection must read its original material, not project again")

        monkeypatch.setattr("src.application.runtime.runtime_service.build_rejected_tool_projection", forbidden)
    result = await resume(h, task)
    assert h.adapter_requests[-1].continue_from_checkpoint and h.adapter_requests[-1].resume is None
    material = parse_tool_projection(receipt(h).proof["application"][TOOL_PROJECTION_KEY])
    assert result.tool_messages == [material.message] and h.effects == []
    if projection_version == 3:
        assert material.schema_version == 2
        assert next(slot for slot in result.state["tool_messages"]
            if slot["tool_call_id"] == material.record.call_id) == material.runtime_message
    if failure == "after_commit":
        assert material == original and receipt(h) == saved
    events = await h.store.list_events(h.session_id)
    assert len([event for event in events if event.type == "approval.decision_applied"]) == 1
    replay = await resume(h, task)
    assert replay.tool_messages == result.tool_messages and await h.store.list_events(h.session_id) == events


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["digest", "source", "record", "job", "missing"])
async def test_applied_rejection_material_cannot_be_substituted_or_regenerated(runtime_harness, monkeypatch, changed):
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.schemas.framework_approval import approval_value_hash
    from src.schemas.framework_tool_projection import TOOL_PROJECTION_KEY

    h = runtime_harness
    session = await h.store.get_session(h.session_id)
    for state in (session.metadata["pending_turn"], session.metadata["pending_turn"]["graph_state"]):
        state["context_bundle"]["framework_tool_projection_version"] = 2
    await h.store.save_session(session)
    task = await claim_decision(h, decision="denied")
    await resume(h, task)
    proof = receipt(h).model_dump(mode="json")["proof"]
    application = proof["application"]
    material = application[TOOL_PROJECTION_KEY]
    if changed == "missing":
        application.pop(TOOL_PROJECTION_KEY)
    elif changed == "job":
        application["projection_job_sha256"] = "0" * 64
    else:
        if changed == "digest":
            material["message"]["content"] += "drift"
        elif changed == "source":
            material["source"]["source_message_id"] = "another-ai"
        else:
            material["record"]["summary"] = "another-result"
        if changed != "digest":
            material["sha256"] = approval_value_hash({k: v for k, v in material.items() if k != "sha256"})
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table}_decision SET proof=%s::jsonb WHERE approval_id=%s",
            (json.dumps(proof), task.reference_id))
    before, events = receipt(h), await h.store.list_events(h.session_id)

    def forbidden(*args, **kwargs):
        pytest.fail("missing/corrupt original material is not authority to regenerate")

    monkeypatch.setattr("src.application.runtime.runtime_service.build_rejected_tool_projection", forbidden)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="projection|native tool result|native result"):
        await resume(h, task)
    assert receipt(h) == before and await h.store.list_events(h.session_id) == events and h.effects == []


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


@pytest_asyncio.fixture
async def fresh_runtime_harness(projection_harness):
    from datetime import datetime, timedelta
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.schemas.framework_model_call import FrameworkModelBudget

    h = projection_harness
    # The resource fixture also observes an independent native graph. Start a
    # fresh bound Turn so this test imports input only through RuntimeService.
    await h.tasks.finish(h.owner, status="completed", result_status="completed")
    session = await h.store.get_session(h.session_id)
    session.status = SessionStatus.idle
    await h.store.save_session(session)
    h.binding = make_binding(h.session_id, f"runtime-turn-{uuid4().hex}")
    h.calls = PostgresFrameworkModelCalls(h.settings)
    await h.calls.initialize()
    h.tables.extend([h.calls.budgets_table, h.calls.calls_table])
    # Mirror application admission, not a late ledger invented at finalization.
    # Model itself remains controlled; these tests do not measure provider usage.
    budget = FrameworkModelBudget(max_calls=8, max_tokens=500_000,
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        model_config_fingerprints={"fake": h.binding.model_config_fingerprint})
    submitted = await h.tasks.submit_v2(make_command(h, h.binding),
        make_payload().model_copy(update={"content": "Inspect A/B", "model_key": "fake",
            "context": {"requested_tool_keys": ["inspect_value"]}}),
        make_message(h, h.binding).model_copy(update={"content": "Inspect A/B"}),
        idempotency_key=uuid4().hex, model_budget=budget)
    h.owner = await h.tasks.claim(submitted.id, worker_id="initial-runtime", expected_binding=h.binding)
    await h.tasks.begin(h.owner, resource_id="runtime-test", fencing_token=3)
    h.effects = []
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("decision", ["approved", "denied"])
@pytest.mark.parametrize("acknowledged", [False, True])
async def test_initial_runtime_and_session_continuations_share_the_official_harness(fresh_runtime_harness, prepared, decision, acknowledged, monkeypatch):
    from datetime import datetime
    from src.schemas.framework_graph_input import FrameworkProjectionIdentity, RESULT_PROJECTION_VERSION_KEY
    from src.runtime.execution_logging import GRAPH_EVENT_LOG_VERSION_KEY

    h = fresh_runtime_harness
    build_runtime(h, responses=[AIMessage(id="runtime-source", content="", tool_calls=[
        {"name": "inspect_value", "args": {"value": key}, "id": key, "type": "tool_call"}
        for key in ("A", "B")]), AIMessage(content="finished")])
    if prepared:
        async def nested_output(arguments, context):
            h.effects.append((arguments["value"], context.tool_job_id))
            return {"status": "completed", "summary": "runtime evidence", "z": {"z": 1, "a": 2},
                "a": arguments["value"]}
        h.runtime._tool_runtime_service._handlers["inspect_value"] = nested_output
    session = await h.store.get_session(h.session_id)
    request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="runtime-trace",
        user_message="Inspect A/B", normalized_input="Inspect A/B", mode_key="default", agent_key="coordinator",
        model_key="fake", runtime_binding=h.binding, context={"requested_tool_keys": ["inspect_value"],
            RESULT_PROJECTION_VERSION_KEY: 999, GRAPH_EVENT_LOG_VERSION_KEY: 1})
    inputs = []
    initial_task_id = h.owner.id
    if prepared:
        from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest
        from src.schemas.session import SendMessageRequest

        request.trace_id = h.owner.trace_id
        stages = FrameworkMessageStages(h.tasks)
        await stages.begin(h.owner.runtime_owner, message_source_manifest(session, h.binding,
            SendMessageRequest.model_validate(h.owner.input)))
        await stages.freeze_execution(h.owner.runtime_owner, request)
        await stages.submit_runtime(h.owner.runtime_owner)

    async def freeze(raw):
        inputs.append(raw)
        if prepared:
            await stages.record_graph_input(h.owner.runtime_owner, raw)

    identity = FrameworkProjectionIdentity(binding=h.binding, assistant_message_id="prepared-assistant",
        snapshot_id="prepared-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 27))
    with framework_owner_scope(h.owner.runtime_owner):
        if acknowledged:
            sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
            first = await sink.run(h.runtime.execute_turn, session, request, on_graph_input=freeze,
                projection_identity=identity if prepared else None)
        else:
            first = await h.runtime.execute_turn(session, request, on_graph_input=freeze,
                projection_identity=identity if prepared else None)
    assert len(first.approvals) == 2 and h.effects == []
    result_version = 1 if prepared and acknowledged else None
    assert inputs[0]["feature_flags"].get("result_projection_version") == result_version
    assert first.state["context_bundle"].get(RESULT_PROJECTION_VERSION_KEY) == result_version
    assert not [item for item in await h.store.list_events(h.session_id) if item.type == "runtime.event_log_completed"]
    if prepared:
        async def forbidden(*args, **kwargs):
            pytest.fail("prepared approval must consume original output without processing ToolJob again")

        monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
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
        b = await claim_decision(h, decision=decision)
        partial = await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        assert partial.session.status.value == "waiting_approval"
        assert [value for value, job in h.effects] == (["B"] if decision == "approved" else [])
        published = await h.store.get_session(h.session_id)
        prefix_length = len(published.metadata["pending_turn"]["graph_state"]["event_log"])
        assert not [item for item in await h.store.list_events(h.session_id) if item.type == "runtime.event_log_completed"]
        original_tool, = [message for message in published.messages if message.metadata.get("tool_call_id")]
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
        a = await claim_decision(h, key="A")
        final = await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    assert final.output.content == "finished" and final.session.status.value == "completed"
    assert [value for value, job in h.effects] == (["B", "A"] if decision == "approved" else ["A"])
    saved = await h.store.get_session(h.session_id)
    assert saved.metadata["pending_turn"] == {}
    assert len([message for message in saved.messages if message.metadata.get("tool_call_id")]) == 2
    assert next(message for message in saved.messages if message.id == original_tool.id) == original_tool
    if prepared:
        from src.schemas.framework_graph_input import parse_graph_input_asset

        original_stage = stages.get(initial_task_id, h.session_id)
        approval_inputs = original_stage.source_manifest.get("approval_graph_inputs")
        assert approval_inputs is not None, "Approval invocations lose their original application preparation before graph execution"
        assert set(approval_inputs) == {b.id, a.id}
        last = approval_inputs[a.id]
        assert last["decision_id"] == a.input["decision_id"] and last["attempt_id"] == a.attempt_id
        frozen = parse_graph_input_asset(last["graph_input_asset"])
        # A later acknowledged suffix cannot upgrade a no-sink original prefix.
        assert frozen.feature_flags.get("result_projection_version") == result_version
        assert frozen.runtime_preparation.initial_state["context_bundle"].get(RESULT_PROJECTION_VERSION_KEY) == result_version
        latest_snapshot = await h.store.get_latest_snapshot(h.session_id)
        identity = frozen.runtime_preparation.identity
        assert final.output.id == identity.assistant_message_id
        assert latest_snapshot.id == identity.snapshot_id
        from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
        from src.runtime.framework_application_projection import require_application_commit

        result_asset = FrameworkRuntimeResultAsset.model_validate(original_stage.source_manifest["runtime_result_asset"])
        assert result_asset.task_id == a.id and result_asset.attempt_id == a.attempt_id
        assert result_asset.assistant_message_id == final.output.id
        assert result_asset.runtime_result.snapshot.id == latest_snapshot.id
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"SELECT * FROM {h.settings.database.postgres_session_table} WHERE id=%s", (h.session_id,))
            commit = require_application_commit(cur, h.settings, cur.fetchone(), h.binding, original_stage)
        assert commit.task_id == a.id and commit.output_message_id == final.output.id
        assert frozen.runtime_preparation.initial_state["event_log"][-1]["type"] == "turn.resumed"
        assert original_stage.source_manifest["graph_input_asset"]["runtime_preparation"]["identity"]["snapshot_id"] == "prepared-snapshot"
    completions = [item for item in await h.store.list_events(h.session_id) if item.type == "runtime.event_log_completed"]
    assert len(completions) == int(acknowledged)
    if acknowledged:
        snapshot = await h.store.get_latest_snapshot(h.session_id)
        read = await h.store.read_runtime_event_completion(h.binding,
            snapshot.graph_state["model_response_summary"]["native_terminal"])
        assert read.event_log == snapshot.graph_state["event_log"]
        assert read.completion.returned_event_offset == prefix_length
        assert read.completion.task_id == a.id and read.completion.attempt_id == a.attempt_id
        assert read.event_log[prefix_length]["type"] == "turn.resumed"
        from src.application.runtime.turn_projection import project_deep_agent_callback_facts
        facts = project_deep_agent_callback_facts(read.event_log, turn_id=h.binding.turn_id,
            returned_event_offset=read.completion.returned_event_offset)
        assert facts.pilot_stage == facts.adapter_stage == "DA-E4" and facts.subagent_events == []
    if prepared:
        from langchain_core.messages import ToolMessage
        from src.application.deep_agents.framework_transcript import deserialize_messages
        from src.schemas.framework_tool_projection import parse_tool_projection, TOOL_PROJECTION_KEY

        # The server-owned feature survives pending_turn and separate approval
        # tasks. Partial sibling results retain their original display slots.
        assert inputs[0]["feature_flags"]["tool_projection_version"] == 3
        assert await h.tasks.get_framework_message_stage_version(a.id, session_id=h.session_id) == (20 if acknowledged else 19)
        snapshot = await h.store.get_latest_snapshot(h.session_id)
        native = deserialize_messages(snapshot.graph_state["framework_messages"])
        material = [parse_tool_projection(message.artifact[TOOL_PROJECTION_KEY])
            for message in native if isinstance(message, ToolMessage) and message.status == "success"]
        if decision == "denied":
            rejected, = [message for message in native if isinstance(message, ToolMessage) and message.status == "error"]
            assert rejected.artifact is None
            original = parse_tool_projection(receipt(h).proof["application"][TOOL_PROJECTION_KEY])
            assert original.model_content == rejected.content and original.record.started_at is None
            material.append(original)
        assert len(material) == 2
        assert all(item.schema_version == 2 for item in material)
        by_call = {item.record.call_id: item.runtime_message for item in material}
        assert snapshot.graph_state["tool_messages"] == [by_call[item["call_id"]]
            for item in snapshot.graph_state["tool_results"]]
        published = {message.id: message for message in saved.messages}
        assert all(published[item.message.id] == item.message for item in material)


@pytest.mark.asyncio
async def test_jobless_governance_refusal_projects_original_acknowledged_event_time(fresh_runtime_harness):
    h = fresh_runtime_harness
    build_runtime(h, responses=[AIMessage(id="refusal-source", content="", tool_calls=[
        {"name": "inspect_value", "args": {"value": "denied"}, "id": "refused", "type": "tool_call"}]),
        AIMessage(content="refused by current policy")])
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    execute = adapter.execute

    async def revoke_after_exposure(request):
        # A real policy change after tool exposure but before invocation. Do not
        # replace governance with a mock or create a fake denial ToolJob.
        h.registry.get("inspect_value").permission_level = "restricted"
        return await execute(request)

    adapter.execute = revoke_after_exposure
    session = await h.store.get_session(h.session_id)
    request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="refusal-trace",
        user_message="Inspect", normalized_input="Inspect", mode_key="default", agent_key="coordinator",
        model_key="fake", runtime_binding=h.binding, context={"requested_tool_keys": ["inspect_value"]})
    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with framework_owner_scope(h.owner.runtime_owner):
        result = await sink.run(h.runtime.execute_turn, session, request)
    item, = result.state["tool_results"]
    assert item["status"] == "denied" and item["job_id"] is None and item["completed_at"] is None
    assert h.effects == [] and await h.jobs.list_jobs(session_id=h.session_id) == []
    saved = await h.store.list_events(h.session_id)
    event, = [event for event in saved if event.type == "tool.execution_denied"]
    message, = result.tool_messages
    timestamp = event.timestamp
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    assert message.created_at == timestamp
    # The app has not frozen a Runtime result or written display messages. A
    # read-only projection from original records/events uses no graph/tool call.
    assert await h.store.get_latest_snapshot(h.session_id) is None
    serialized = json.loads(json.dumps(result.state["tool_results"]))
    assert h.runtime._to_chat_messages(h.binding.turn_id, serialized, binding_id=h.binding.binding_id,
        event_log=[event.model_dump(mode="json") for event in saved]) == result.tool_messages
    assert len(h.adapter_requests) == 1 and h.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("offloaded", [False, True])
async def test_prepared_turn_checkpoints_complete_tool_projection_before_result_asset(fresh_runtime_harness, denied, prepared, offloaded, monkeypatch):
    from datetime import datetime
    from deepagents import create_deep_agent
    from langchain_core.messages import ToolMessage
    from src.application.deep_agents.framework_transcript import deserialize_messages
    from src.schemas.framework_graph_input import FrameworkProjectionIdentity
    from tests.test_framework_checkpoint_fencing import config

    h = fresh_runtime_harness
    h.registry.get("inspect_value").permission_level = "safe"
    build_runtime(h, responses=[AIMessage(id="projection-source", content="", tool_calls=[{
        "name": "inspect_value", "args": {"value": "original"}, "id": "original-call", "type": "tool_call"}]),
        AIMessage(content="finished")])
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    if offloaded:
        from deepagents.middleware.filesystem import FilesystemMiddleware

        def factory(**kwargs):
            return create_deep_agent(**{**kwargs, "middleware": [*kwargs.get("middleware", []),
                FilesystemMiddleware(tool_token_limit_before_evict=1)]})

        monkeypatch.setattr(adapter, "_resolve_factory", lambda: factory)
    execute = adapter.execute

    async def revoke_after_exposure(request):
        if denied:
            h.registry.get("inspect_value").permission_level = "restricted"
        return await execute(request)

    adapter.execute = revoke_after_exposure
    session = await h.store.get_session(h.session_id)
    request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="projection-trace",
        user_message="Inspect", normalized_input="Inspect", mode_key="default", agent_key="coordinator",
        model_key="fake", runtime_binding=h.binding, context={"requested_tool_keys": ["inspect_value"],
            "framework_tool_projection_version": 1})
    identity = FrameworkProjectionIdentity(binding=h.binding, assistant_message_id="original-assistant",
        snapshot_id="original-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 27))
    inputs = []

    async def freeze(value):
        inputs.append(value)

    sink = SessionEventSink(h.store, h.session_id, capacity=1, fence={}, runtime_owner=h.owner.runtime_owner)
    with framework_owner_scope(h.owner.runtime_owner):
        result = await sink.run(h.runtime.execute_turn, session, request,
            projection_identity=identity if prepared else None, on_graph_input=freeze)
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            # Rebuild only for a read, using a fresh saver connection. No graph,
            # model or tool invocation is used to recover the original material.
            reader = create_deep_agent(model=Model(responses=[AIMessage(content="must not run")]), checkpointer=saver)
            persisted = await reader.aget_state(config(h))
    native, = [message for message in persisted.values["messages"] if isinstance(message, ToolMessage)]
    if not prepared:
        assert native.artifact is None and "tool_projection_version" not in inputs[0]["feature_flags"]
        assert "framework_tool_projection_version" not in result.state["context_bundle"]
        return
    assert isinstance(native.artifact, dict), "governed callback loses the complete application projection before checkpoint"
    projection = native.artifact["enterprise_qa_tool_projection"]
    from src.schemas.tool_runtime import ToolExecutionRecord
    assert projection["schema_version"] == 2
    assert projection["runtime_message"] == result.state["tool_messages"][0]
    assert projection["record"] == ToolExecutionRecord.model_validate(result.state["tool_results"][0]).model_dump(mode="json")
    assert projection["message"] == result.tool_messages[0].model_dump(mode="json")
    assert (projection["model_content"] != native.content) is offloaded
    assert projection["source"]["source_message_id"] == "projection-source"
    assert projection["source"]["tool_call_id"] == "original-call"
    assert projection["binding_fingerprint"] == h.binding.fingerprint
    assert inputs[0]["feature_flags"]["tool_projection_version"] == 3
    restored, = [message for message in deserialize_messages(result.state["framework_messages"]) if isinstance(message, ToolMessage)]
    assert restored == native
    assert await h.store.get_latest_snapshot(h.session_id) is None
    assert len(h.adapter_requests) == 1 and len(h.effects) == (0 if denied else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_version,reader_version", [(13, 12), (14, 13), (15, 14)])
async def test_old_reader_defers_new_tool_material_approval_before_claim(runtime_harness, monkeypatch, stage_version, reader_version):
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_message_stages import message_stage_table
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred
    from src.schemas.agent_task import AgentTaskCommandV2
    from src.schemas.session import ApprovalDecisionRequest

    h = runtime_harness
    command = make_command(h, h.binding).model_copy(update={"operation": "approval_resume",
        "reference_id": h.proposals["B"].approval.id})
    queued = await h.tasks.submit_approval_v2(command, command.reference_id,
        ApprovalDecisionRequest(decision="approved"))
    # The format reader must inspect the original message's binding, not look
    # for a message-stage row owned by this separate approval task.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {message_stage_table(h.settings)} SET record=jsonb_set(record,'{{schema_version}}',%s::jsonb) "
            "WHERE binding_id=%s", (json.dumps(stage_version), h.binding.binding_id))

    async def forbidden(*args, **kwargs):
        pytest.fail("old format capability must defer before claiming the approval")

    monkeypatch.setattr(h.tasks, "claim", forbidden)
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True, message_stage_max_version=reader_version))
    wire = AgentTaskCommandV2.model_validate(command.model_dump(mode="json"))
    with pytest.raises(TaskDeferred, match="framework_message_stage_unavailable"):
        await h.dispatcher.handle_v2(QueuedTask("old-reader", wire.model_dump(mode="json")), worker_id="old-reader")
    assert (await h.tasks.get(queued.id)).attempt_no == 0
