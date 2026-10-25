from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ChatMessage, MessageRole, SendMessageRequest
from tests.test_agent_task_dispatch import harness


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
