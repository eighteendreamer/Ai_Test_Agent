import asyncio
from datetime import datetime, timedelta
from importlib import import_module

import pytest

from src.core.config import Settings
from src.runtime.store import InMemorySessionStore
from src.schemas.session import (
    ExecutionEvent,
    SessionSnapshot,
    ToolApprovalRequest,
    ToolApprovalStatus,
)


@pytest.mark.asyncio
async def test_in_memory_store_limits_and_replays_events_after_cursor():
    store = InMemorySessionStore()
    session_id = "session-1"
    now = datetime.utcnow()

    events = [
        ExecutionEvent(
            id=f"event-{index}",
            type=f"event.{index}",
            session_id=session_id,
            timestamp=now + timedelta(seconds=index),
            payload={"index": index},
        )
        for index in range(5)
    ]
    for event in events:
        await store.append_event(session_id, event)

    recent = await store.list_events(session_id, limit=2)
    replay = await store.list_events(session_id, after_event_id="event-1", limit=2)

    assert [event.id for event in recent] == ["event-3", "event-4"]
    assert [event.id for event in replay] == ["event-2", "event-3"]


@pytest.mark.asyncio
async def test_in_memory_store_can_flush_already_persisted_event_idempotently():
    store = InMemorySessionStore()
    session_id = "session-1"
    event = ExecutionEvent(
        id="already-streamed",
        type="security.tool_bootstrap.denied",
        session_id=session_id,
        timestamp=datetime.utcnow(),
        payload={"error": "target_out_of_scope"},
    )

    await store.append_event(session_id, event)
    await store.append_event(session_id, event)

    persisted = await store.list_events(session_id)
    assert [item.id for item in persisted] == [event.id]
    assert await store.read_event_page(session_id, limit=10, after_event_id=event.id) == []


@pytest.mark.asyncio
async def test_in_memory_store_can_return_snapshot_metadata_without_graph_state():
    store = InMemorySessionStore()
    session_id = "session-1"

    for version in range(1, 4):
        await store.save_snapshot(
            session_id,
            SessionSnapshot(
                id=f"snapshot-{version}",
                session_id=session_id,
                version=version,
                stage="completed",
                created_at=datetime.utcnow(),
                graph_state={"large": "x" * 1024},
            ),
        )

    snapshots = await store.list_snapshots(
        session_id,
        limit=2,
        include_graph_state=False,
    )

    assert [snapshot.version for snapshot in snapshots] == [2, 3]
    assert [snapshot.graph_state for snapshot in snapshots] == [{}, {}]


@pytest.mark.asyncio
async def test_in_memory_approval_resolution_is_compare_and_set_and_idempotent():
    store = InMemorySessionStore()
    approval = ToolApprovalRequest(
        id="approval-cas-1",
        session_id="session-cas-1",
        tool_key="security-scan-runner",
        tool_name="Security Scan Runner",
        reason="high risk test",
        created_at=datetime.utcnow(),
    )
    await store.save_approval(approval.session_id, approval)

    resolved = await store.resolve_approval(
        approval.session_id,
        approval.id,
        ToolApprovalStatus.approved,
        "approved",
    )
    repeated = await store.resolve_approval(
        approval.session_id,
        approval.id,
        ToolApprovalStatus.approved,
        "duplicate approval callback",
    )

    assert resolved.status == ToolApprovalStatus.approved
    assert repeated.status == ToolApprovalStatus.approved
    with pytest.raises(ValueError, match="already resolved"):
        await store.resolve_approval(
            approval.session_id,
            approval.id,
            ToolApprovalStatus.denied,
            "conflicting callback",
        )


@pytest.mark.asyncio
async def test_postgres_approval_resolution_rejects_conflicting_final_decision(monkeypatch):
    store_module = import_module("src.runtime.postgres_session_store")
    now = datetime.utcnow()
    approval_row = {
        "id": "approval-postgres-cas-1",
        "session_id": "session-postgres-cas-1",
        "tool_key": "security-scan-runner",
        "tool_name": "Security Scan Runner",
        "reason": "high risk test",
        "status": "approved",
        "created_at": now,
        "resolved_at": now,
        "decision_note": "approved",
        "metadata": {},
    }

    class Cursor:
        def __init__(self):
            self.calls = []
            self.row = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            normalized = " ".join(statement.split())
            self.calls.append((normalized, parameters))
            self.row = None if normalized.startswith("UPDATE") else approval_row

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

    class Context:
        def __init__(self, connection):
            self._connection = connection

        def __enter__(self):
            return self._connection

        def __exit__(self, exc_type, exc, tb):
            return False

    cursor = Cursor()
    monkeypatch.setattr(
        store_module,
        "postgres_connect",
        lambda settings: Context(Connection(cursor)),
    )
    store = store_module.PostgresSessionStore(Settings())

    with pytest.raises(ValueError, match="already resolved"):
        await store.resolve_approval(
            approval_row["session_id"],
            approval_row["id"],
            ToolApprovalStatus.denied,
            "conflicting callback",
        )

    assert "status = 'pending'" in cursor.calls[0][0]
