"""Ordinary store operations compared against the real PostgreSQL contract.

Source: P1-18, store.py and PostgresSessionStore; no injected write failures.
Editing a draft must require an explicit save, irrespective of the backend.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import (
    InMemoryProjectStore,
    PostgresProjectStore,
)
from src.core.config import get_settings
from src.domain.models import SessionImportBundle, SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.store import InMemorySessionStore
from src.schemas.project import ProjectCreateRequest
from src.schemas.session import (
    ChatMessage,
    ExecutionEvent,
    MessageRole,
    RuntimeMode,
    SessionMode,
    SessionSnapshot,
    SessionStatus,
    ToolApprovalRequest,
    ToolApprovalStatus,
)
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_intent_safety_routing import (
    test_coordinator_five_reviewer_campaign_reaches_summary_without_dropping_work as run_campaign,
)
from tests.test_live_postgres_concurrency import _drop_tables

pytest_plugins = ["tests.test_session_event_delivery"]


@pytest_asyncio.fixture(params=["memory", "postgres"])
async def dated_stores(request):
    settings = get_settings().model_copy(deep=True)
    tables = []
    if request.param == "postgres":
        if not LivePostgresTestConfig().run_live_postgres_tests:
            pytest.skip("set RUN_LIVE_POSTGRES_TESTS=1 for date contract integration")
        suffix = uuid4().hex[:12]
        for name in ("session", "message", "event", "snapshot", "approval", "project"):
            table = f"live_dates_{name}_{suffix}"
            setattr(settings.database, f"postgres_{name}_table", table)
            tables.append(table)
        project_store = PostgresProjectStore(settings)
        writer, reader = PostgresSessionStore(settings), PostgresSessionStore(settings)
    else:
        project_store = InMemoryProjectStore()
        writer = reader = InMemorySessionStore()
    try:
        projects = ProjectService(store=project_store)
        await projects.initialize()
        project = await projects.create(ProjectCreateRequest(project_key="date-contract", name="Date contract"))
        await writer.initialize()
        now = datetime.now(UTC)
        session = SessionRecord(id="dates-" + uuid4().hex, title="dated backup", status=SessionStatus.idle,
                                session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
                                mode_key="code_review", created_at=now, updated_at=now, project_id=project.id)
        await writer.save_session(session)
        yield writer, reader, session.id
    finally:
        if tables:
            _drop_tables(settings, tables)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    "list", "task_pool", "project_history", "history", "export", "date_range", "count", "delete",
])
async def test_imported_aware_dates_coexist_with_new_sessions(dated_stores, operation):
    """Valid dated backup plus an ordinary save; no injected errors or clock patches."""
    writer, reader, session_id = dated_stores
    current = await reader.get_session(session_id)
    current.mode_key = "code_review"
    await writer.save_session(current)
    imported = deepcopy(current)
    imported.id += "-backup"
    imported.created_at = imported.updated_at = datetime.now(UTC) - timedelta(hours=1)
    assert await writer.import_session_bundles([SessionImportBundle(session=imported)]) == (1, 0)
    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    if operation == "list":
        assert [s.id for s in await reader.list_sessions()] == [current.id, imported.id]
        assert [s.id for s in await reader.list_sessions(cursor_before=cutoff)] == [imported.id]
    elif operation == "task_pool":
        assert [s.id for s in await reader.list_task_pool_sessions()] == [current.id, imported.id]
    elif operation == "project_history":
        rows = await reader.list_project_history_context(current.project_id, limit=10)
        assert [s["session_id"] for s in rows] == [current.id, imported.id]
    elif operation == "history":
        assert [s["session_id"] for s in await reader.list_history_overviews()] == [current.id, imported.id]
    elif operation == "export":
        assert [s["id"] for s in await reader.bulk_export()] == [current.id, imported.id]
    elif operation == "date_range":
        dates = await reader.get_session_date_range()
        assert ensure_utc_datetime(dates["oldest"]) == ensure_utc_datetime(imported.updated_at)
        assert ensure_utc_datetime(dates["newest"]) > ensure_utc_datetime(cutoff)
    elif operation == "count":
        assert await reader.count_sessions(before=cutoff) == 1
        assert await reader.count_sessions(after=cutoff) == 1
    else:
        # Only this fixture's own sessions/tables are eligible for removal.
        assert await writer.delete_sessions_before(cutoff) == 1
        assert await reader.get_session(imported.id) is None
        assert await reader.get_session(current.id) is not None


@pytest.mark.asyncio
async def test_recent_questions_order_actual_instants_across_timezones(stores):
    writer, reader, session_id, _ = stores
    session = await reader.get_session(session_id)
    base = datetime.now(UTC)
    session.messages = [
        ChatMessage(id="older-local", role=MessageRole.user, content="earlier question",
                    created_at=base.astimezone(timezone(timedelta(hours=8)))),
        ChatMessage(id="newer-utc", role=MessageRole.user, content="later question",
                    created_at=base + timedelta(minutes=1)),
    ]
    await writer.save_session(session)
    assert [item["content"] for item in await reader.list_recent_questions()] == [
        "later question", "earlier question",
    ]


@pytest.mark.skipif(not LivePostgresTestConfig().run_live_postgres_tests,
                    reason="set RUN_LIVE_POSTGRES_TESTS=1 for UTC connection contract")
@pytest.mark.parametrize("zone", [None, UTC, timezone(timedelta(hours=8))])
def test_postgres_connection_interprets_legacy_naive_dates_as_utc(zone):
    """Read-only cast verifies the common pool boundary, without changing server settings."""
    instant = datetime.now(UTC)
    value = instant.astimezone(zone) if zone is not None else instant.replace(tzinfo=None)
    with postgres_connect(get_settings()) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('TimeZone') AS timezone, %s::timestamptz AS instant", (value,))
        result = cursor.fetchone()
    assert result["timezone"] == "UTC"
    assert ensure_utc_datetime(result["instant"]) == instant.replace(tzinfo=None)
