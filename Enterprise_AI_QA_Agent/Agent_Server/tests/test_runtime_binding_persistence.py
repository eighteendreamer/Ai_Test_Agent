from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ChatMessage, MessageRole, SendMessageRequest
from tests.test_agent_task_dispatch import harness, prepare_pending_approval, prepare_resumable_session


def make_binding(session_id: str, turn_id: str, **overrides) -> RuntimeBinding:
    value = {
        "binding_id": f"binding-{uuid4().hex}",
        "session_id": session_id,
        "turn_id": turn_id,
        "runtime_kind": "deepagents",
        "runtime_version": "v2.0.0",
        "state_schema_version": 2,
        "thread_id": turn_id,
        "checkpoint_namespace": "qa:v2",
        "prompt_version": "prompt-2026-10-25",
        "skill_version": "skills-1",
        "tool_schema_version": "tools-1",
        "model_config_fingerprint": "model-sha256",
        "dependency_lock_sha256": "a" * 64,
    }
    value.update(overrides)
    return RuntimeBinding(**value)


def make_command(harness, binding: RuntimeBinding, *, task_id: str | None = None) -> AgentTaskCommandV2:
    return AgentTaskCommandV2(
        task_id=task_id or f"task-{uuid4().hex}",
        session_id=harness.session_id,
        turn_id=binding.turn_id,
        request_id=f"request-{uuid4().hex}",
        trace_id=f"trace-{uuid4().hex}",
        runtime_binding=binding,
    )


def make_payload() -> SendMessageRequest:
    return SendMessageRequest(content="v2 persistence", metadata={"client_message_id": uuid4().hex})


def make_message(harness, binding: RuntimeBinding) -> ChatMessage:
    return ChatMessage(
        id=f"message-{uuid4().hex}",
        role=MessageRole.user,
        content="v2 persistence",
        created_at=datetime.now(timezone.utc),
        metadata={"turn_id": binding.turn_id, "context_eligible": False},
    )


@pytest.mark.asyncio
async def test_v2_submission_persists_binding_in_task_and_outbox(harness):
    binding = make_binding(harness.session_id, f"turn-{uuid4().hex}")
    command = make_command(harness, binding)
    task = await harness.tasks.submit_v2(
        command,
        make_payload(),
        make_message(harness, binding),
        idempotency_key=f"client-{uuid4().hex}",
    )

    assert task.runtime_binding == binding
    loaded = await harness.tasks.get(task.id)
    assert loaded.runtime_binding == binding

    with postgres_connect(harness.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT runtime_binding FROM {harness.settings.database.postgres_agent_task_table} WHERE id=%s",
            (task.id,),
        )
        assert cur.fetchone()["runtime_binding"] == binding.model_dump(mode="json")
        cur.execute(
            f"SELECT payload FROM {harness.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"agent_turn:{task.id}",),
        )
        outbox_payload = cur.fetchone()["payload"]
    assert outbox_payload["schema_version"] == 2
    assert outbox_payload["runtime_binding"] == binding.model_dump(mode="json")


@pytest.mark.asyncio
async def test_v2_retry_with_conflicting_binding_cannot_overwrite_persisted_binding(harness):
    turn_id = f"turn-{uuid4().hex}"
    first_binding = make_binding(harness.session_id, turn_id)
    first_command = make_command(harness, first_binding)
    idempotency_key = f"client-{uuid4().hex}"
    first_payload = make_payload()
    await harness.tasks.submit_v2(
        first_command,
        first_payload,
        make_message(harness, first_binding),
        idempotency_key=idempotency_key,
    )

    second_binding = make_binding(
        harness.session_id,
        turn_id,
        runtime_version="v2.0.1",
    )
    second_command = make_command(harness, second_binding, task_id=first_command.task_id)
    with pytest.raises(AgentSubmissionConflict):
        await harness.tasks.submit_v2(
            second_command,
            first_payload,
            make_message(harness, second_binding),
            idempotency_key=idempotency_key,
        )

    loaded = await harness.tasks.get(first_command.task_id)
    assert loaded.runtime_binding == first_binding


@pytest.mark.asyncio
async def test_v2_message_submission_cannot_encode_unimplemented_continuation(harness):
    selected = make_binding(harness.session_id, f"turn-{uuid4().hex}")
    command = make_command(harness, selected).model_copy(update={"operation": "manual_resume"})
    with pytest.raises(ValueError, match="only message"):
        await harness.tasks.submit_v2(command, make_payload(), make_message(harness, selected),
            idempotency_key=f"client-{uuid4().hex}")
    assert await harness.tasks.list_session(harness.session_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["manual_resume", "approval_resume"])
async def test_legacy_submission_cannot_continue_a_framework_bound_checkpoint(harness, operation):
    from src.schemas.session import ApprovalDecisionRequest, ResumeSessionRequest, ToolApprovalStatus

    if operation == "manual_resume":
        snapshot = await prepare_resumable_session(harness)
        turn_id = snapshot.graph_state["turn_id"]
        selected = make_binding(harness.session_id, turn_id)
        snapshot.graph_state["context_bundle"] = {"runtime_binding": selected.model_dump(mode="json")}
        await harness.store.save_snapshot(harness.session_id, snapshot)
        submit = harness.dispatcher.submit_resume(harness.session_id, ResumeSessionRequest())
    else:
        approval = await prepare_pending_approval(harness)
        session = await harness.store.get_session(harness.session_id)
        selected = make_binding(harness.session_id, session.metadata["pending_turn"]["turn_id"])
        session.metadata["pending_turn"]["context_bundle"] = {"runtime_binding": selected.model_dump(mode="json")}
        await harness.store.save_session(session)
        submit = harness.dispatcher.submit_approval(harness.session_id, approval.id,
            ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
    with pytest.raises(AgentSubmissionConflict, match="v2 continuation"):
        await submit
    assert await harness.tasks.list_session(harness.session_id) == []
    if operation == "approval_resume":
        approvals = await harness.store.list_approvals(harness.session_id)
        assert approvals[0].status == ToolApprovalStatus.pending
