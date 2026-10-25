from __future__ import annotations

import asyncio
from datetime import datetime
import json
import threading
from uuid import uuid4

import pytest

from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeError
from src.runtime.execution_context import framework_owner_scope
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.session import SessionStatus
from src.schemas.tool_job import ToolArtifactRecord, ToolJobStatus
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, receipt
from tests.test_framework_approval_runtime import runtime_harness, resume
from tests.test_framework_task_control import interrupt
from tests.test_framework_tool_bridge import Model


@pytest.mark.asyncio
async def test_revoked_owner_cannot_claim_original_tool(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    assert (await interrupt(h)).status_code == 200
    with framework_owner_scope(task.runtime_owner), pytest.raises(ContinuationLeaseLostError):
        await h.jobs.claim_execution(h.proposals["B"].job.id)
    saved = await h.jobs.get_job(h.proposals["B"].job.id)
    assert saved.status == ToolJobStatus.waiting_approval and saved.started_at is None


@pytest.mark.asyncio
async def test_bound_tool_cannot_be_mutated_without_framework_authority(runtime_harness):
    h = runtime_harness
    await claim_decision(h)
    with pytest.raises(ContinuationLeaseLostError):
        await h.jobs.claim_execution(h.proposals["B"].job.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["heartbeat", "observe", "finish", "save", "mark", "artifact", "dispatch"])
async def test_revoked_tool_attempt_cannot_write_authoritative_state(runtime_harness, operation):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    assert (await interrupt(h)).status_code == 200
    before = await h.jobs.get_job(job.id)
    with framework_owner_scope(task.runtime_owner), pytest.raises(ContinuationLeaseLostError):
        if operation == "heartbeat":
            await h.jobs.heartbeat_execution(job.id)
        elif operation == "observe":
            await h.jobs.observe_output(job.id, status="completed", output_payload={"result": "late"})
        elif operation == "finish":
            await h.jobs.finish_execution(job.id, status=ToolJobStatus.completed, output_payload={"result": "late"}, summary="late")
        elif operation == "save":
            await h.job_store.save_job(job.model_copy(update={"status": ToolJobStatus.completed}))
        elif operation == "mark":
            await h.jobs.mark_completed(job.id, "late", {"result": "late"})
        elif operation == "artifact":
            await h.job_store.save_artifact(ToolArtifactRecord(id=str(uuid4()), tool_job_id=job.id,
                session_id=job.session_id, turn_id=job.turn_id, trace_id=job.trace_id, tool_key=job.tool_key,
                artifact_type="text", path="late.txt", created_at=datetime.utcnow()))
        else:
            await h.jobs.assert_dispatch_authority(job.id)
    assert await h.jobs.get_job(job.id) == before
    assert receipt(h).applied_at is None


@pytest.mark.asyncio
async def test_current_owner_claim_and_output_settlement_preserve_execution_identity(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        job = await h.jobs.claim_execution(h.proposals["B"].job.id)
        execution = job.metadata["framework_execution_owner"]
        assert execution["owner_epoch"] == task.runtime_owner.owner_epoch
        assert execution["attempt_id"] == task.attempt_id and "owner_token" not in execution
        await h.jobs.assert_dispatch_authority(job.id)
        await h.jobs.observe_output(job.id, status="completed", output_payload={"evidence": "known"})
        saved = await h.jobs.finish_output_processing(job.id, status=ToolJobStatus.completed,
            output_payload={"evidence": "known"}, summary="known")
    assert saved.status == ToolJobStatus.completed and saved.metadata["framework_execution_owner"] == execution


@pytest.mark.asyncio
async def test_native_tool_return_after_interrupt_is_append_only_evidence(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    entered, release = asyncio.Event(), asyncio.Event()

    async def tool(arguments, context):
        h.effects.append((arguments["value"], context.tool_job_id))
        entered.set()
        await release.wait()
        return {"status": "completed", "summary": "late tool response", "evidence": "effect occurred"}

    h.runtime._tool_runtime_service._handlers["inspect_value"] = tool
    running = asyncio.create_task(resume(h, task))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert (await interrupt(h)).status_code == 200
        release.set()
        with pytest.raises(DeepAgentRuntimeError, match="ContinuationLeaseLostError"):
            await running
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)
    job = await h.jobs.get_job(h.proposals["B"].job.id)
    assert job.status == ToolJobStatus.running and job.output_payload == {}
    assert len(h.effects) == 1 and receipt(h).applied_at is None
    observations = [item for item in await h.jobs.list_artifacts(tool_job_id=job.id)
        if item.artifact_type == "framework_late_output"]
    assert len(observations) == 1
    metadata = observations[0].metadata
    assert metadata["authoritative"] is False
    assert metadata["framework_execution_owner"]["owner_epoch"] == task.runtime_owner.owner_epoch
    assert json.loads(metadata["__content_text"])["evidence"] == "effect occurred"
    with framework_owner_scope(task.runtime_owner):
        with pytest.raises(ContinuationLeaseLostError):
            await h.jobs.observe_output(job.id, status="completed", output_payload=json.loads(metadata["__content_text"]))
    assert len(await h.jobs.list_artifacts(tool_job_id=job.id)) == 1


@pytest.mark.asyncio
async def test_framework_runtime_claims_unclaimed_jobs_atomically(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    tool = h.registry.get("inspect_value")
    context = ToolExecutionContext(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="direct",
        user_message="inspect", normalized_input="inspect", context_bundle={})
    with framework_owner_scope(task.runtime_owner):
        record = await h.runtime._tool_runtime_service.execute(tool,
            ModelToolCall(id="fresh-unclaimed", name=tool.key, arguments={"value": "fresh"}), context)
    assert record.status == "completed" and len(h.effects) == 1
    job = await h.jobs.get_job(record.job_id)
    assert job.metadata["framework_execution_owner"]["attempt_id"] == task.attempt_id


@pytest.mark.asyncio
async def test_revocation_between_governance_claim_and_dispatch_does_not_enter_handler(runtime_harness, monkeypatch):
    h = runtime_harness
    task = await claim_decision(h)
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.jobs.assert_dispatch_authority

    async def wait_dispatch(job_id):
        entered.set()
        await release.wait()
        await original(job_id)

    monkeypatch.setattr(h.jobs, "assert_dispatch_authority", wait_dispatch)
    running = asyncio.create_task(resume(h, task))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert (await interrupt(h)).status_code == 200
        release.set()
        with pytest.raises(DeepAgentRuntimeError, match="ContinuationLeaseLostError"):
            await running
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)
    assert h.effects == []
    job = await h.jobs.get_job(h.proposals["B"].job.id)
    assert job.status == ToolJobStatus.running and job.output_payload == {}
    assert receipt(h).applied_at is None and await h.jobs.list_artifacts(tool_job_id=job.id) == []


@pytest.mark.asyncio
async def test_next_valid_coordinator_owner_cannot_settle_the_previous_tool_attempt(runtime_harness):
    h = runtime_harness
    first = await claim_decision(h)
    with framework_owner_scope(first.runtime_owner):
        job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    await h.tasks.finish(first, status="completed", result_status="waiting_approval")
    next_task = await claim_decision(h, key="A")
    assert next_task.runtime_owner.owner_epoch > first.runtime_owner.owner_epoch
    with framework_owner_scope(next_task.runtime_owner), pytest.raises(ContinuationLeaseLostError, match="different framework attempt"):
        await h.jobs.finish_execution(job.id, status=ToolJobStatus.completed, output_payload={"result": "substituted"}, summary="substituted")
    assert await h.jobs.get_job(job.id) == job


@pytest.mark.asyncio
async def test_cancelled_to_thread_settlement_retains_the_revoked_owner(runtime_harness, monkeypatch):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    errors = []
    original = h.job_store._finish_job_execution_sync

    def delayed(*args):
        entered.set()
        try:
            assert release.wait(10)
            return original(*args)
        except Exception as exc:
            errors.append(exc)
            raise
        finally:
            ended.set()

    monkeypatch.setattr(h.job_store, "_finish_job_execution_sync", delayed)
    with framework_owner_scope(task.runtime_owner):
        pending = asyncio.create_task(h.jobs.finish_execution(job.id, status=ToolJobStatus.completed,
            output_payload={"evidence": "late thread"}, summary="late thread"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert (await interrupt(h)).status_code == 200
    finally:
        release.set()
        assert await asyncio.to_thread(ended.wait, 10)
    assert len(errors) == 1 and isinstance(errors[0], ContinuationLeaseLostError)
    assert await h.jobs.get_job(job.id) == job


@pytest.mark.asyncio
async def test_observed_output_is_not_settled_by_a_revoked_worker(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    entered, release = asyncio.Event(), asyncio.Event()

    class ArtifactGate:
        async def store_output_artifacts(self, output, **kwargs):
            entered.set()
            await release.wait()
            return output

    h.runtime._tool_runtime_service._artifact_storage_service = ArtifactGate()
    pending = asyncio.create_task(resume(h, task))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        known = await h.jobs.get_job(h.proposals["B"].job.id)
        assert known.metadata["tool_output_observation"]["status"] == "completed"
        assert (await interrupt(h)).status_code == 200
        release.set()
        with pytest.raises(DeepAgentRuntimeError, match="ContinuationLeaseLostError"):
            await pending
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
    assert await h.jobs.get_job(known.id) == known
    assert known.status == ToolJobStatus.running and len(h.effects) == 1
    assert receipt(h).applied_at is None


@pytest.mark.asyncio
async def test_framework_runtime_does_not_convert_an_unknown_claim_to_failure(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        original = await h.jobs.claim_execution(h.proposals["B"].job.id)
        context = ToolExecutionContext(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="unknown",
            user_message="inspect", normalized_input="inspect", context_bundle={}, tool_job_id=original.id)
        with pytest.raises(FrameworkApprovalRecoveryRequired):
            await h.runtime._tool_runtime_service.execute(h.registry.get("inspect_value"),
                ModelToolCall(id=original.call_id, name=original.tool_key, arguments=original.input_payload), context)
    assert await h.jobs.get_job(original.id) == original and h.effects == []


@pytest.mark.asyncio
async def test_native_tool_exception_after_effect_preserves_unknown_outcome(runtime_harness, monkeypatch):
    h = runtime_harness
    a = await claim_decision(h, key="A", decision="denied")
    first = await resume(h, a)

    def preserve(metadata, status):
        metadata["pending_turn"] = first.pending_turn

    await h.store.update_status_and_mutate_metadata(h.session_id, status=SessionStatus.waiting_approval,
        mutate=preserve, turn_lease_token=a.lease_token)
    await h.tasks.finish(a, status="completed", result_status="waiting_approval")
    task = await claim_decision(h)
    model_calls = []
    original = Model._agenerate

    async def count_model(self, *args, **kwargs):
        model_calls.append(True)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(Model, "_agenerate", count_model)

    async def fail_after_effect(arguments, context):
        h.effects.append((arguments["value"], context.tool_job_id))
        raise TimeoutError("controlled response lost after external effect")

    h.runtime._tool_runtime_service._handlers["inspect_value"] = fail_after_effect
    with pytest.raises(DeepAgentRuntimeError, match="FrameworkApprovalRecoveryRequired"):
        await resume(h, task)
    job = await h.jobs.get_job(h.proposals["B"].job.id)
    assert len(h.effects) == 1 and job.status == ToolJobStatus.running and job.output_payload == {}
    assert receipt(h).applied_at is None and model_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["call_id", "input_payload", "framework_binding_id", "framework_execution_owner"])
async def test_whole_row_write_preserves_frozen_tool_identity(runtime_harness, changed):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        job = await h.jobs.claim_execution(h.proposals["B"].job.id)
        candidate = job.model_copy(deep=True)
        if changed == "call_id":
            candidate.call_id = "another-call"
        elif changed == "input_payload":
            candidate.input_payload = {"value": "another-input"}
        else:
            candidate.metadata[changed] = "another-identity"
        with pytest.raises(ContinuationLeaseLostError):
            await h.job_store.save_job(candidate)
    assert await h.jobs.get_job(job.id) == job


@pytest.mark.asyncio
async def test_legacy_stale_reaper_does_not_change_bound_framework_execution(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    with framework_owner_scope(task.runtime_owner):
        job = await h.jobs.claim_execution(h.proposals["B"].job.id)
    legacy = await h.jobs.create_job(tool=h.registry.get("inspect_value"), call_id="legacy", session_id=h.session_id,
        turn_id="legacy-turn", trace_id="legacy", input_payload={})
    await h.jobs.mark_running(legacy)
    changed = await h.job_store.mark_stale_running_jobs(-1)
    assert {item.id for item in changed} == {legacy.id}
    assert (await h.jobs.get_job(legacy.id)).status == ToolJobStatus.resume_requested
    assert await h.jobs.get_job(job.id) == job
