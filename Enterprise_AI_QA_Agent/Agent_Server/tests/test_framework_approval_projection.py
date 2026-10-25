from __future__ import annotations

from uuid import uuid4
from types import SimpleNamespace
import asyncio

import pytest
import pytest_asyncio

from src.application.deep_agents.native_interrupts import collect_pending_tool_interrupts
from src.application.permissions.permission_service import PermissionPolicyContext, PermissionService
from src.application.runtime.tool_governance_service import GovernedToolCallContext, ToolGovernanceService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.session import MessageKind, RuntimeMode, SessionMode
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config
from tests.test_framework_approval_receipts import native_approvals
from tests.test_live_postgres_concurrency import _drop_tables


@pytest_asyncio.fixture
async def projection_harness(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    suffix = uuid4().hex[:12]
    h.settings.database.postgres_tool_job_table = f"projection_job_{suffix}"
    h.settings.database.postgres_tool_artifact_table = f"projection_artifact_{suffix}"
    h.job_store = PostgresToolJobStore(h.settings)
    await h.job_store.initialize()
    h.jobs = ToolJobService(h.job_store)
    registry = ToolRegistry()
    h.registry = registry
    registry.register_dynamic(ToolDescriptor(key="inspect_value", name="Inspect value", category="execution",
        description="Inspect a test value", permission_level="ask", input_schema={"type": "object",
            "properties": {"value": {"type": "string"}}, "required": ["value"]}), "inspect_value")
    h.governance = ToolGovernanceService(tool_registry=registry, permission_service=PermissionService(),
        tool_runtime_service=object(), tool_job_service=h.jobs)
    h.governance_context = GovernedToolCallContext(execution=ToolExecutionContext(
        session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="projection-trace",
        user_message="Inspect A/B", normalized_input="Inspect A/B", context_bundle={}),
        policy=PermissionPolicyContext(session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
            selected_agent_key="coordinator", message_kind=MessageKind.user_input,
            submit_mode="immediate", execution_lane="conversation_turn"), active_mode_key="default",
        available_tool_keys=frozenset({"inspect_value"}))
    try:
        h.native = await native_approvals(h, monkeypatch)
        async with h.provider.open(owner=h.owner.runtime_owner) as saver:
            h.identities = await collect_pending_tool_interrupts(h.native.agent, saver, config(h))
        yield h
    finally:
        _drop_tables(h.settings, [h.settings.database.postgres_tool_artifact_table,
            h.settings.database.postgres_tool_job_table])


def prepare(h, index=0):
    return h.governance.prepare_framework_approval(h.binding, h.identities[index], h.governance_context)


def projector(h):
    from src.runtime.framework_approval_projection import FrameworkApprovalProjection

    return FrameworkApprovalProjection(h.settings, sessions=h.store, jobs=h.jobs)


def counts(h):
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        output = {}
        for key, table in {"jobs": h.settings.database.postgres_tool_job_table,
            "approvals": h.settings.database.postgres_approval_table,
            "links": h.settings.database.postgres_agent_task_table + "_interrupt",
            "events": h.settings.database.postgres_event_table}.items():
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            output[key] = cur.fetchone()["n"]
        return output


@pytest.mark.asyncio
async def test_native_approval_preparation_has_stable_identity_and_no_writes(projection_harness):
    h = projection_harness
    before = counts(h)
    first, repeated = prepare(h), prepare(h)
    assert counts(h) == before
    assert first.approval.id == repeated.approval.id
    assert first.job.id == repeated.job.id and first.link == repeated.link
    assert first.job.call_id != first.link.tool_call_id
    assert first.approval.metadata["call_id"] == first.link.tool_call_id
    assert first.approval.metadata["framework_call_id"] == first.job.call_id
    assert first.job.status.value == "waiting_approval"


@pytest.mark.asyncio
async def test_projection_stores_job_approval_link_and_event_together(projection_harness):
    h = projection_harness
    proposal = prepare(h)
    before = counts(h)
    result = await projector(h).project(h.owner.runtime_owner, proposal)
    after = counts(h)
    assert after == {key: value + 1 for key, value in before.items()}
    assert result.approval.id == proposal.approval.id and result.link == proposal.link
    assert (await h.jobs.get_job(proposal.job.id)).status.value == "waiting_approval"
    assert h.native.calls == []


@pytest.mark.asyncio
async def test_reprojection_preserves_human_decision_and_terminal_tool_job(projection_harness):
    h = projection_harness
    proposal = prepare(h)
    service = projector(h)
    await service.project(h.owner.runtime_owner, proposal)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='approved', "
            "decision_note='original choice', resolved_at=clock_timestamp() WHERE id=%s", (proposal.approval.id,))
        cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET status='completed', "
            "started_at=clock_timestamp(), completed_at=clock_timestamp(), output_payload=%s::jsonb WHERE id=%s",
            ('{"evidence":"original"}', proposal.job.id))
    before = counts(h)
    result = await service.project(h.owner.runtime_owner, prepare(h))
    assert counts(h) == before
    assert result.approval.status.value == "approved" and result.approval.decision_note == "original choice"
    job = await h.jobs.get_job(proposal.job.id)
    assert job.status.value == "completed" and job.output_payload == {"evidence": "original"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["link", "event"])
async def test_projection_failure_rolls_back_all_business_records(projection_harness, monkeypatch, failure):
    from src.runtime.framework_approval_journal import FrameworkApprovalJournal

    h = projection_harness
    proposal = prepare(h)
    if failure == "link":
        target, method = FrameworkApprovalJournal, "link_interrupt_in_transaction"
    else:
        target, method = h.store, "append_event_in_transaction"
    original = getattr(target, method)

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected projection failure")

    monkeypatch.setattr(target, method, fail)
    before = counts(h)
    with pytest.raises(RuntimeError, match="injected projection"):
        await projector(h).project(h.owner.runtime_owner, proposal)
    assert counts(h) == before


@pytest.mark.asyncio
async def test_runtime_uses_native_identity_and_atomic_projection_without_display_messages(projection_harness):
    from src.application.deep_agents import DeepAgentRuntimeResult
    from src.application.runtime.runtime_service import RuntimeService
    from src.runtime.control import RuntimeControlRegistry
    from src.runtime.execution_context import framework_owner_scope

    h = projection_harness
    runtime = RuntimeService(graph=None, model_runtime_service=object(), tool_runtime_service=object(),
        tool_registry=h.registry, runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=h.jobs, framework_approval_projection=projector(h))
    state = {"session_id": h.session_id, "turn_id": h.binding.turn_id, "trace_id": "projection-trace",
        "user_message": "Inspect A/B", "normalized_input": "Inspect A/B", "mode_key": "default",
        "selected_agent_key": "coordinator", "selected_model_key": "fake", "session_mode": "normal",
        "runtime_mode": "interactive", "available_tool_keys": ["inspect_value"],
        "context_bundle": {"runtime_binding": h.binding.model_dump(mode="json")}}
    result = DeepAgentRuntimeResult(output_text="", messages=[], native_interrupts=h.identities,
        interrupts=[{"id": item.interrupt_id, "value": h.native.tasks[item.tool_call_id].interrupt.value}
            for item in h.identities])
    with framework_owner_scope(h.owner.runtime_owner):
        approvals = await runtime._record_deep_agent_interrupts(state, result)
    assert len(approvals) == 2 and counts(h)["links"] == counts(h)["jobs"] == 2
    assert {item["metadata"]["call_id"] for item in approvals} == {"A", "B"}
    before = counts(h)
    # Rebuild from checkpoint after a projection-only failure, without relying
    # on the process-local saved approval batch.
    state["context_bundle"].pop("deep_agent_approval_batch")
    with framework_owner_scope(h.owner.runtime_owner):
        rebuilt = await runtime._record_deep_agent_interrupts(state, result)
    assert [item["id"] for item in rebuilt] == [item["id"] for item in approvals]
    assert counts(h) == before


@pytest.mark.asyncio
async def test_session_finalization_does_not_overwrite_decision_after_native_projection(projection_harness):
    from src.application.runtime.runtime_service import RuntimeService
    from src.runtime.control import RuntimeControlRegistry
    from src.runtime.execution_context import framework_owner_scope

    h = projection_harness
    h.sessions._runtime_service = RuntimeService(graph=None, model_runtime_service=object(), tool_runtime_service=object(),
        tool_registry=h.registry, runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=h.jobs, framework_approval_projection=projector(h))
    proposal = await projector(h).project(h.owner.runtime_owner, prepare(h))
    session = await h.store.get_session(h.session_id)
    result = await h.runtime.execute_turn(session, SimpleNamespace(turn_id=h.binding.turn_id))
    result.approvals = [proposal.approval.model_dump(mode="python")]
    result.state["context_bundle"] = {"runtime_binding": h.binding.model_dump(mode="json")}
    result.state["termination_reason"] = "waiting_approval"
    result.pending_turn = {"turn_id": h.binding.turn_id, "pending_approval_ids": [proposal.approval.id],
        "context_bundle": result.state["context_bundle"]}
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='denied', "
            "decision_note='concurrent choice', resolved_at=clock_timestamp() WHERE id=%s", (proposal.approval.id,))
    with framework_owner_scope(h.owner.runtime_owner):
        await h.sessions._finalize_runtime_result(session, result, assistant_message_id=str(uuid4()),
            user_message_override="Inspect A/B", turn_lease_token=h.owner.lease_token)
    saved = next(item for item in await h.store.list_approvals(h.session_id) if item.id == proposal.approval.id)
    assert saved.status.value == "denied" and saved.decision_note == "concurrent choice"
    events = await h.store.list_events(h.session_id)
    assert len([event for event in events if event.type == "approval.created"
        and event.payload.get("approval_id") == proposal.approval.id]) == 1


@pytest.mark.asyncio
async def test_native_projection_cannot_downgrade_to_legacy_metadata(projection_harness):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    h = projection_harness
    service = projector(h)
    proposal = await service.project(h.owner.runtime_owner, prepare(h))
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET metadata=metadata "
            "- 'framework_native_identity' - 'framework_binding_id' - 'framework_call_id' WHERE id=%s",
            (proposal.approval.id,))
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="identity"):
        await service.load(h.owner.runtime_owner, proposal.approval.id)


@pytest.mark.asyncio
async def test_concurrent_projection_keeps_one_business_identity(projection_harness):
    h = projection_harness
    before = counts(h)
    service = projector(h)
    results = await asyncio.gather(*(service.project(h.owner.runtime_owner, prepare(h)) for _ in range(5)))
    assert len({item.approval.id for item in results}) == len({item.job.id for item in results}) == 1
    assert counts(h) == {key: value + 1 for key, value in before.items()}


@pytest.mark.asyncio
async def test_expired_owner_cannot_publish_or_reload_native_projection(projection_harness):
    from src.runtime.store import ContinuationLeaseLostError

    h = projection_harness
    service = projector(h)
    proposal = await service.project(h.owner.runtime_owner, prepare(h))
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    before = counts(h)
    with pytest.raises(ContinuationLeaseLostError):
        await service.project(h.owner.runtime_owner, prepare(h, 1))
    with pytest.raises(ContinuationLeaseLostError):
        await service.load(h.owner.runtime_owner, proposal.approval.id)
    assert counts(h) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["scope", "job_id", "task", "arguments"])
async def test_inconsistent_projection_rolls_back_without_publishing(projection_harness, corruption):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    h = projection_harness
    proposal = prepare(h)
    if corruption == "scope":
        proposal.approval.metadata["approval_scope_hash"] = "changed"
    elif corruption == "job_id":
        changed = "different-job"
        proposal.job.id = changed
        proposal.approval.metadata["tool_job_id"] = changed
        proposal = proposal.model_copy(update={"link": proposal.link.model_copy(update={"tool_job_id": changed})})
    else:
        changes = {"graph_task_id": "missing-task"} if corruption == "task" else {"arguments": {"value": "changed"}}
        link = proposal.link.model_copy(update=changes)
        native = h.identities[0].model_copy(update=changes).model_dump(mode="json")
        proposal.approval.metadata["framework_native_identity"] = native
        proposal.job.metadata["framework_native_identity"] = native
        if corruption == "arguments":
            proposal.job.input_payload = changes["arguments"]
            proposal.approval.metadata["arguments"] = changes["arguments"]
        proposal = proposal.model_copy(update={"link": link})
    before = counts(h)
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await projector(h).project(h.owner.runtime_owner, proposal)
    assert counts(h) == before


@pytest.mark.asyncio
async def test_runtime_projection_reuses_terminal_job_record(projection_harness):
    from src.application.deep_agents import DeepAgentRuntimeResult
    from src.application.runtime.runtime_service import RuntimeService
    from src.application.runtime.tool_runtime_service import ToolRuntimeService
    from src.runtime.control import RuntimeControlRegistry
    from src.runtime.execution_context import framework_owner_scope

    h = projection_harness
    proposal = await projector(h).project(h.owner.runtime_owner, prepare(h))
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='approved', "
            "resolved_at=clock_timestamp() WHERE id=%s", (proposal.approval.id,))
        cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET status='completed', "
            "started_at=clock_timestamp(), completed_at=clock_timestamp(), output_payload=%s::jsonb WHERE id=%s",
            ('{"evidence":"persisted tool output"}', proposal.job.id))
    runtime = RuntimeService(graph=None, model_runtime_service=object(),
        tool_runtime_service=ToolRuntimeService(tool_job_service=h.jobs), tool_registry=h.registry,
        runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=h.jobs, framework_approval_projection=projector(h))
    state = {"session_id": h.session_id, "turn_id": h.binding.turn_id, "trace_id": "projection-trace",
        "user_message": "Inspect A/B", "normalized_input": "Inspect A/B", "mode_key": "default",
        "selected_agent_key": "coordinator", "selected_model_key": "fake", "session_mode": "normal",
        "runtime_mode": "interactive", "available_tool_keys": ["inspect_value"],
        "context_bundle": {"runtime_binding": h.binding.model_dump(mode="json")}}
    result = DeepAgentRuntimeResult(output_text="", native_interrupts=h.identities,
        interrupts=[{"id": item.interrupt_id, "value": h.native.tasks[item.tool_call_id].interrupt.value}
            for item in h.identities])
    with framework_owner_scope(h.owner.runtime_owner):
        await runtime._record_deep_agent_interrupts(state, result)
    record = next(item for item in state["_deep_agent_waiting_records"] if item["job_id"] == proposal.job.id)
    assert record["status"] == "completed" and record["output"]["evidence"] == "persisted tool output"
    assert h.native.calls == []
