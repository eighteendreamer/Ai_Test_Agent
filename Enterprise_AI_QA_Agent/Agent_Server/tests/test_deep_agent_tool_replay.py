from __future__ import annotations

import asyncio

import pytest

from src.application.permissions.permission_service import PermissionPolicyContext, PermissionService
from src.application.runtime.tool_governance_service import GovernedToolCallContext, ToolGovernanceService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.registry.tools import ToolRegistry
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.session import MessageKind, RuntimeMode, SessionMode
from src.schemas.tool_runtime import ModelToolCall


def _context() -> GovernedToolCallContext:
    return GovernedToolCallContext(
        execution=ToolExecutionContext(
            session_id="replay-session", turn_id="replay-turn", trace_id="replay-trace",
            user_message="Read evidence", normalized_input="Read evidence", context_bundle={},
        ),
        policy=PermissionPolicyContext(
            session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
            selected_agent_key="coordinator", message_kind=MessageKind.user_input,
            submit_mode="immediate", execution_lane="conversation_turn",
        ),
        active_mode_key="code_review", available_tool_keys=frozenset({"knowledge-rag"}),
    )


def _service(handler):
    store = InMemoryToolJobStore()
    jobs = ToolJobService(store)
    runtime = ToolRuntimeService(tool_job_service=jobs)
    runtime._handlers["knowledge-rag"] = handler
    governance = ToolGovernanceService(
        tool_registry=ToolRegistry(), permission_service=PermissionService(),
        tool_runtime_service=runtime, tool_job_service=jobs,
    )
    return governance, jobs


@pytest.mark.asyncio
async def test_completed_tool_call_replay_reuses_persisted_result_without_executing():
    executions = []

    async def handler(arguments, context):
        executions.append(context.tool_job_id)
        return {"status": "completed", "summary": "persisted evidence", "value": 42}

    service, jobs = _service(handler)
    call = ModelToolCall(id="stable-call", name="knowledge-rag", arguments={"query": "evidence"})
    first = await service.execute(call, _context())
    second = await service.execute(call, _context())

    assert len(executions) == 1
    assert first.record.job_id == second.record.job_id
    assert first.record.output == second.record.output
    assert first.replayed is False
    assert second.replayed is True
    assert len(await jobs.list_jobs(session_id="replay-session")) == 1


@pytest.mark.asyncio
async def test_failed_tool_call_replay_preserves_the_persisted_failure_output():
    executions = []

    async def handler(arguments, context):
        executions.append(context.tool_job_id)
        raise RuntimeError("captured failure")

    service, _ = _service(handler)
    call = ModelToolCall(id="failed-call", name="knowledge-rag", arguments={"query": "x"})
    first = await service.execute(call, _context())
    second = await service.execute(call, _context())

    assert first.record.status == "failed"
    assert second.replayed is True
    assert first.record.summary == second.record.summary
    assert first.record.output == second.record.output
    assert executions == [first.record.job_id]


@pytest.mark.asyncio
async def test_same_call_id_with_changed_arguments_is_rejected_without_executing_again():
    executions = []

    async def handler(arguments, context):
        executions.append((arguments, context.tool_job_id))
        return {"status": "completed", "summary": "first result"}

    service, _ = _service(handler)
    await service.execute(
        ModelToolCall(id="stable-call", name="knowledge-rag", arguments={"query": "first"}),
        _context(),
    )

    with pytest.raises(RuntimeError, match="identity or arguments changed"):
        await service.execute(
            ModelToolCall(id="stable-call", name="knowledge-rag", arguments={"query": "changed"}),
            _context(),
        )

    assert len(executions) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_call_does_not_execute_while_first_owner_is_active():
    entered = asyncio.Event()
    release = asyncio.Event()
    executions = []

    async def handler(arguments, context):
        executions.append(context.tool_job_id)
        entered.set()
        await release.wait()
        return {"status": "completed", "summary": "single owner"}

    service, _ = _service(handler)
    call = ModelToolCall(id="concurrent-call", name="knowledge-rag", arguments={"query": "x"})
    first_task = asyncio.create_task(service.execute(call, _context()))
    await asyncio.wait_for(entered.wait(), timeout=2)

    with pytest.raises(RuntimeError, match="prior execution may still be active"):
        await service.execute(call, _context())

    release.set()
    first = await first_task
    replay = await service.execute(call, _context())
    assert first.record.status == "completed"
    assert replay.replayed is True
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_claimed_call_without_terminal_result_requires_reconciliation_not_retry():
    executions = []

    async def handler(arguments, context):
        executions.append(context.tool_job_id)
        return {"status": "completed", "summary": "must not run"}

    service, jobs = _service(handler)
    descriptor = ToolRegistry().get("knowledge-rag")
    job = await jobs.create_job(
        tool=descriptor,
        call_id="crashed-call",
        session_id="replay-session",
        turn_id="replay-turn",
        trace_id="replay-trace",
        input_payload={"query": "x"},
        once_per_call=True,
    )
    assert await jobs.claim_execution(job.id) is not None

    with pytest.raises(RuntimeError, match="Reconcile its evidence before any retry"):
        await service.execute(
            ModelToolCall(id="crashed-call", name="knowledge-rag", arguments={"query": "x"}),
            _context(),
        )

    assert executions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persisted_status", "expected_status"),
    [("failed", "failed"), ("denied", "denied"), ("cancelled", "denied")],
)
async def test_terminal_non_success_job_is_replayed_without_execution(
    persisted_status, expected_status
):
    executions = []

    async def handler(arguments, context):
        executions.append(context.tool_job_id)
        return {"status": "completed", "summary": "must not run"}

    service, jobs = _service(handler)
    descriptor = ToolRegistry().get("knowledge-rag")
    job = await jobs.create_job(
        tool=descriptor,
        call_id=f"{persisted_status}-call",
        session_id="replay-session",
        turn_id="replay-turn",
        trace_id="replay-trace",
        input_payload={"query": "x"},
        once_per_call=True,
    )
    assert await jobs.claim_execution(job.id) is not None
    if persisted_status == "failed":
        await jobs.mark_failed(
            job.id, summary="persisted failure", error_message="failure",
            output_payload={"error": "failure"},
        )
    elif persisted_status == "denied":
        await jobs.mark_denied(
            job.id, summary="persisted denial", output_payload={"reason": "denied"},
        )
    else:
        await jobs.cancel_job(job.id, reason="persisted cancellation")

    replay = await service.execute(
        ModelToolCall(
            id=f"{persisted_status}-call",
            name="knowledge-rag",
            arguments={"query": "x"},
        ),
        _context(),
    )

    assert replay.replayed is True
    assert replay.record.status == expected_status
    assert executions == []
