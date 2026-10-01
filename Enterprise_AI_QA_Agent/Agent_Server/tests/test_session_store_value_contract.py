"""Ordinary store operations compared against the real PostgreSQL contract.

Source: P1-18, store.py and PostgresSessionStore; no injected write failures.
Editing a draft must require an explicit save, irrespective of the backend.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.models import SessionImportBundle
from src.schemas.session import (
    ChatMessage,
    ExecutionEvent,
    MessageRole,
    SessionSnapshot,
    ToolApprovalRequest,
    ToolApprovalStatus,
)
from tests.test_intent_safety_routing import (
    test_coordinator_five_reviewer_campaign_reaches_summary_without_dropping_work as run_campaign,
)

pytest_plugins = ["tests.test_session_event_delivery"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["input", "saved", "get", "list", "queue", "claim"])
async def test_session_edits_require_explicit_save(stores, source):
    writer, reader, session_id, _ = stores
    session = await reader.get_session(session_id)
    session.metadata = {"draft": {"text": "saved"}, "pending_input_queue": [{"id": "input"}]}
    saved = await writer.save_session(session)
    if source == "input":
        draft = session
    elif source == "saved":
        draft = saved
    elif source == "get":
        draft = await reader.get_session(session_id)
    elif source == "list":
        draft = (await reader.list_sessions())[0]
    elif source == "queue":
        draft = (await reader.list_sessions_with_pending_input_queue())[0]
    else:
        draft = await writer.claim_turn_execution(session_id, "turn", "owner", "lease", 30)
        assert draft is not None
    draft.title = "unsaved title"
    draft.metadata["draft"]["text"] = "unsaved draft"
    persisted = await reader.get_session(session_id)
    assert persisted.title != "unsaved title"
    assert persisted.metadata["draft"]["text"] == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["event", "snapshot", "approval"])
@pytest.mark.parametrize("source", ["input", "read"])
async def test_child_payload_edits_require_explicit_save(stores, kind, source):
    writer, reader, session_id, _ = stores
    now = datetime.now(UTC)
    if kind == "event":
        record = ExecutionEvent(id="value-event", type="test.event", session_id=session_id,
                                timestamp=now, payload={"draft": {"text": "saved"}})
        await writer.append_event(session_id, record)
        async def read():
            return (await reader.read_event_page(session_id, limit=1))[0]
        field = "payload"
    elif kind == "snapshot":
        record = SessionSnapshot(id="value-snapshot", session_id=session_id, version=1,
                                 stage="completed", created_at=now,
                                 graph_state={"draft": {"text": "saved"}})
        await writer.save_snapshot(session_id, record)
        async def read():
            return await reader.get_latest_snapshot(session_id)
        field = "graph_state"
    else:
        record = ToolApprovalRequest(id="value-approval", session_id=session_id,
                                     tool_key="test", tool_name="test", reason="value contract",
                                     created_at=now, metadata={"draft": {"text": "saved"}})
        await writer.save_approval(session_id, record)
        async def read():
            return (await reader.list_approvals(session_id))[0]
        field = "metadata"
    draft = record if source == "input" else await read()
    getattr(draft, field)["draft"]["text"] = "unsaved draft"
    assert getattr(await read(), field)["draft"]["text"] == "saved"


@pytest.mark.asyncio
async def test_stale_session_save_preserves_durable_counts_and_worker_dispatches(stores):
    writer, reader, session_id, _ = stores
    stale = deepcopy(await reader.get_session(session_id))
    await writer.append_event(session_id, ExecutionEvent(
        id="counter-event", type="test.event", session_id=session_id,
        timestamp=datetime.now(UTC), payload={},
    ))
    await writer.save_snapshot(session_id, SessionSnapshot(
        id="counter-snapshot", session_id=session_id, version=1,
        stage="completed", created_at=datetime.now(UTC), graph_state={},
    ))
    assert await writer.mutate_metadata(session_id, lambda metadata: metadata.update(
        worker_dispatches=[{"task_id": "worker-task"}],
    ))
    stale.title = "updated title"
    saved = await writer.save_session(stale)
    persisted = await reader.get_session(session_id)
    for record in (saved, persisted):
        assert record.event_count == 1
        assert record.snapshot_count == 1
        assert record.metadata["worker_dispatches"] == [{"task_id": "worker-task"}]


@pytest.mark.asyncio
async def test_resolved_approval_is_a_detached_result(stores):
    writer, reader, session_id, _ = stores
    approval = ToolApprovalRequest(id="resolved-value", session_id=session_id,
                                  tool_key="test", tool_name="test", reason="value contract",
                                  created_at=datetime.now(UTC))
    await writer.save_approval(session_id, approval)
    resolved = await writer.resolve_approval(session_id, approval.id, ToolApprovalStatus.approved)
    resolved.status = ToolApprovalStatus.denied
    assert (await reader.list_approvals(session_id))[0].status == ToolApprovalStatus.approved


@pytest.mark.asyncio
async def test_imported_bundle_is_detached_from_caller(stores):
    writer, reader, session_id, _ = stores
    session = deepcopy(await reader.get_session(session_id))
    session.id += "-imported"
    session.metadata = {"draft": {"text": "saved"}}
    bundle = SessionImportBundle(session=session)
    assert await writer.import_session_bundles([bundle]) == (1, 0)
    bundle.session.metadata["draft"]["text"] = "unsaved draft"
    assert (await reader.get_session(session.id)).metadata["draft"]["text"] == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("projection", [False, True])
async def test_metadata_update_does_not_retain_caller_payload(stores, projection):
    writer, reader, session_id, _ = stores
    payload = {"draft": {"text": "saved"}}
    if projection:
        def update(metadata):
            metadata.update(payload)
            return True
        assert await writer.update_metadata_and_append_event(session_id, update, ExecutionEvent(
            id="value-projection", type="test.event", session_id=session_id,
            timestamp=datetime.now(UTC), payload={},
        ))
    else:
        assert await writer.mutate_metadata(session_id, lambda metadata: metadata.update(payload))
    payload["draft"]["text"] = "unsaved draft"
    assert (await reader.get_session(session_id)).metadata["draft"]["text"] == "saved"


@pytest.mark.asyncio
async def test_export_and_task_pool_views_are_detached(stores):
    writer, reader, session_id, _ = stores
    session = await reader.get_session(session_id)
    session.mode_key = "code_review"
    session.metadata = {"worker_dispatches": [{"task_id": "worker", "details": {"text": "saved"}}]}
    await writer.save_session(session)
    export = (await reader.bulk_export())[0]
    view = (await reader.list_task_pool_sessions())[0]
    export["metadata"]["worker_dispatches"][0]["details"]["text"] = "edited export"
    view.worker_dispatches[0]["details"]["text"] = "edited view"
    assert (await reader.get_session(session_id)).metadata["worker_dispatches"][0]["details"]["text"] == "saved"


@pytest.mark.asyncio
async def test_normal_coordinator_campaign_persists_summary_receipt(stores):
    writer, _, _, _ = stores
    await run_campaign(store_override=writer)


@pytest.mark.asyncio
async def test_stale_message_save_keeps_other_persisted_messages(stores):
    writer, reader, session_id, _ = stores
    first = await reader.get_session(session_id)
    second = await reader.get_session(session_id)
    now = datetime.now(UTC)
    first.messages.append(ChatMessage(id="first-message", role=MessageRole.user,
                                      content="first input", created_at=now))
    second.messages.append(ChatMessage(id="second-message", role=MessageRole.user,
                                       content="second input", created_at=now + timedelta(seconds=1)))
    await writer.save_session(first)
    await writer.save_session(second)
    assert [message.id for message in (await reader.get_session(session_id)).messages] == [
        "first-message", "second-message",
    ]


@pytest.mark.asyncio
async def test_context_only_message_is_not_added_to_transcript(stores):
    writer, reader, session_id, _ = stores
    session = await reader.get_session(session_id)
    session.messages.append(ChatMessage(id="context-message", role=MessageRole.user,
                                        content="runtime context", created_at=datetime.now(UTC),
                                        metadata={"persist_transcript": False, "context_eligible": False}))
    await writer.save_session(session)
    assert (await reader.get_session(session_id)).messages == []
