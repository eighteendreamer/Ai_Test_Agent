from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.api.routes.settings import router as settings_router
from src.core.config import Settings
from src.domain.models import SessionImportBundle, SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.store import InMemorySessionStore
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


def _app(
    store: InMemorySessionStore, *, max_import_bytes: int = 1024 * 1024
) -> FastAPI:
    app = FastAPI()
    app.include_router(settings_router, prefix="/api/v1")
    app.state.store = store
    app.state.settings = SimpleNamespace(
        security=SimpleNamespace(data_import_max_bytes=max_import_bytes),
    )
    return app


def _client(
    store: InMemorySessionStore, *, max_import_bytes: int = 1024 * 1024
) -> TestClient:
    app = _app(store, max_import_bytes=max_import_bytes)
    return TestClient(app, raise_server_exceptions=False)


def _exported_session() -> dict:
    async def build() -> dict:
        store = InMemorySessionStore()
        created_at = datetime(2026, 9, 30, 10, 11, 12, tzinfo=UTC)
        await store.save_session(
            SessionRecord(
                id="backup-session-1",
                title="备份会话",
                status=SessionStatus.completed,
                session_mode=SessionMode.coordinator,
                runtime_mode=RuntimeMode.headless,
                mode_key="code_review",
                created_at=created_at,
                updated_at=created_at,
                preferred_model="model-a",
                selected_agent="agent-a",
                project_id="project-a",
                metadata={"source": "backup-test"},
                messages=[
                    ChatMessage(
                        id="message-1",
                        role=MessageRole.user,
                        content="检查提交",
                        created_at=created_at,
                        metadata={"origin": "test"},
                    )
                ],
            )
        )
        await store.append_event(
            "backup-session-1",
            ExecutionEvent(
                type="turn.completed",
                session_id="backup-session-1",
                timestamp=created_at,
                payload={"result": "passed"},
            ),
        )
        await store.save_snapshot(
            "backup-session-1",
            SessionSnapshot(
                id="snapshot-1",
                session_id="backup-session-1",
                version=1,
                stage="completed",
                created_at=created_at,
                graph_state={"node": "finalizer"},
            ),
        )
        await store.save_approval(
            "backup-session-1",
            ToolApprovalRequest(
                id="approval-1",
                session_id="backup-session-1",
                tool_key="test-tool",
                tool_name="Test Tool",
                reason="requires review",
                status=ToolApprovalStatus.approved,
                created_at=created_at,
                resolved_at=created_at,
                decision_note="approved in test",
                metadata={"reviewer": "operator"},
            ),
        )
        return (await store.bulk_export())[0]

    return asyncio.run(build())


def _import(client: TestClient, sessions: object) -> object:
    payload = {"version": "1.0", "sessions": sessions}
    return client.post(
        "/api/v1/settings/data/import",
        files={
            "file": (
                "backup.json",
                json.dumps(payload).encode("utf-8"),
                "application/json",
            )
        },
    )


def _expected_import_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def test_import_round_trips_a_complete_store_export() -> None:
    source = _exported_session()
    store = InMemorySessionStore()

    with _client(store) as client:
        response = _import(client, [source])

    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 1
    assert response.json()["skipped_count"] == 0

    async def assert_imported() -> None:
        session = await store.get_session("backup-session-1")
        assert session is not None
        assert session.created_at == _expected_import_datetime(source["created_at"])
        assert session.updated_at == _expected_import_datetime(source["updated_at"])
        assert session.session_mode == SessionMode.coordinator
        assert session.runtime_mode == RuntimeMode.headless
        assert session.project_id == "project-a"
        assert session.messages[0].created_at == _expected_import_datetime(
            source["messages"][0]["created_at"]
        )
        assert session.event_count == source["event_count"]
        assert session.snapshot_count == source["snapshot_count"]

        events = await store.list_events("backup-session-1")
        assert len(events) == 1
        assert events[0].timestamp == _expected_import_datetime(
            source["events"][0]["timestamp"]
        )
        assert events[0].payload == {"result": "passed"}

        snapshots = await store.list_snapshots("backup-session-1")
        assert len(snapshots) == 1
        assert snapshots[0].created_at == _expected_import_datetime(
            source["snapshots"][0]["created_at"]
        )
        assert snapshots[0].graph_state == {"node": "finalizer"}

        approvals = await store.list_approvals("backup-session-1")
        assert len(approvals) == 1
        assert approvals[0].created_at == _expected_import_datetime(
            source["approvals"][0]["created_at"]
        )
        assert approvals[0].status == ToolApprovalStatus.approved

    asyncio.run(assert_imported())


def test_invalid_session_is_counted_as_skipped_without_aborting_other_rows() -> None:
    invalid = _exported_session()
    invalid["id"] = "invalid-session"
    invalid["session_mode"] = "unsupported-mode"
    valid = _exported_session()
    valid["id"] = "valid-session"
    store = InMemorySessionStore()

    with _client(store) as client:
        response = _import(client, [invalid, valid])

    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 1
    assert response.json()["skipped_count"] == 1
    assert asyncio.run(store.get_session("invalid-session")) is None
    assert asyncio.run(store.get_session("valid-session")) is not None


def test_invalid_entry_does_not_reserve_child_ids_for_later_rows() -> None:
    first = _exported_session()
    first["id"] = "first-session"
    invalid = _exported_session()
    invalid["id"] = "invalid-session"
    invalid["messages"][0]["id"] = "message-from-invalid-entry"
    invalid["snapshots"][0]["id"] = "snapshot-invalid"
    invalid["approvals"][0]["id"] = "approval-invalid"
    conflicting_message = dict(first["messages"][0])
    invalid["messages"].append(conflicting_message)
    last = _exported_session()
    last["id"] = "last-session"
    last["messages"][0]["id"] = "message-from-invalid-entry"
    last["snapshots"][0]["id"] = "snapshot-last"
    last["approvals"][0]["id"] = "approval-last"
    store = InMemorySessionStore()

    with _client(store) as client:
        response = _import(client, [first, invalid, last])

    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 2
    assert response.json()["skipped_count"] == 1
    assert asyncio.run(store.get_session("invalid-session")) is None
    assert asyncio.run(store.get_session("last-session")) is not None


def test_invalid_root_shape_is_a_client_error_and_does_not_write() -> None:
    store = InMemorySessionStore()

    with _client(store) as client:
        response = client.post(
            "/api/v1/settings/data/import",
            files={"file": ("backup.json", b"null", "application/json")},
        )

    assert response.status_code == 400
    assert asyncio.run(store.count_sessions()) == 0


def test_invalid_json_does_not_return_parser_details() -> None:
    store = InMemorySessionStore()

    with _client(store) as client:
        response = client.post(
            "/api/v1/settings/data/import",
            files={"file": ("backup.json", b"{secret fragment", "application/json")},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON file."
    assert "secret fragment" not in response.text


def test_import_rejects_files_larger_than_the_configured_limit() -> None:
    store = InMemorySessionStore()

    with _client(store, max_import_bytes=64) as client:
        response = client.post(
            "/api/v1/settings/data/import",
            files={"file": ("backup.json", b" " * 65, "application/json")},
        )

    assert response.status_code == 413
    assert asyncio.run(store.count_sessions()) == 0


def test_multipart_body_is_limited_before_the_file_is_parsed() -> None:
    store = InMemorySessionStore()
    valid_backup = json.dumps(
        {"version": "1.0", "sessions": [_exported_session()]}
    ).encode("utf-8")

    with _client(store, max_import_bytes=1024) as client:
        response = client.post(
            "/api/v1/settings/data/import",
            data={"padding": "x" * (80 * 1024)},
            files={"file": ("backup.json", valid_backup, "application/json")},
        )

    assert response.status_code == 413
    assert asyncio.run(store.count_sessions()) == 0


@pytest.mark.asyncio
async def test_chunked_multipart_body_is_limited_during_receive() -> None:
    store = InMemorySessionStore()
    app = _app(store, max_import_bytes=1024)
    boundary = "qa-import-boundary"
    backup = json.dumps(
        {
            "version": "1.0",
            "sessions": [
                {
                    "id": "chunked-valid-session",
                    "title": "valid backup",
                    "status": "completed",
                    "session_mode": "normal",
                    "runtime_mode": "interactive",
                    "mode_key": "default",
                    "created_at": "2026-10-01T00:00:00Z",
                    "updated_at": "2026-10-01T00:00:00Z",
                }
            ],
        }
    ).encode("utf-8")
    body = b"".join(
        (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="backup.json"\r\n'
                "Content-Type: application/json\r\n\r\n"
            ).encode("ascii"),
            backup,
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="padding"\r\n\r\n'.encode(
                "ascii"
            ),
            b"x" * (80 * 1024),
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        )
    )

    async def request_body():
        for offset in range(0, len(body), 8192):
            yield body[offset : offset + 8192]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/settings/data/import",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            content=request_body(),
        )

    assert response.status_code == 413, response.text
    assert await store.count_sessions() == 0


def test_duplicate_import_is_idempotently_skipped() -> None:
    session = _exported_session()
    store = InMemorySessionStore()

    with _client(store) as client:
        first = _import(client, [session])
        second = _import(client, [session])

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["imported_count"] == 1
    assert second.json()["imported_count"] == 0
    assert second.json()["skipped_count"] == 1
    assert asyncio.run(store.count_sessions()) == 1
    assert len(asyncio.run(store.list_events("backup-session-1"))) == 1


def test_store_failure_returns_a_sanitized_service_error() -> None:
    class FailingImportStore(InMemorySessionStore):
        async def import_session_bundles(self, bundles):
            raise RuntimeError("database parameters must not reach the client")

    store = FailingImportStore()
    with _client(store) as client:
        response = _import(client, [_exported_session()])

    assert response.status_code == 503
    assert "database parameters" not in response.text


def test_legacy_entries_default_to_supported_session_modes() -> None:
    legacy = _exported_session()
    legacy.pop("session_mode")
    legacy.pop("runtime_mode")
    legacy.pop("mode_key")
    store = InMemorySessionStore()

    with _client(store) as client:
        response = _import(client, [legacy])

    assert response.status_code == 200, response.text
    imported = asyncio.run(store.get_session("backup-session-1"))
    assert imported is not None
    assert imported.session_mode == SessionMode.normal
    assert imported.runtime_mode == RuntimeMode.interactive
    assert imported.mode_key == "default"


@pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use isolated PostgreSQL tables",
)
@pytest.mark.asyncio
async def test_postgres_import_rolls_back_all_rows_when_a_child_insert_conflicts() -> (
    None
):
    suffix = uuid4().hex[:10]
    tables = {
        "postgres_session_table": f"live_import_session_{suffix}",
        "postgres_message_table": f"live_import_message_{suffix}",
        "postgres_event_table": f"live_import_event_{suffix}",
        "postgres_snapshot_table": f"live_import_snapshot_{suffix}",
        "postgres_approval_table": f"live_import_approval_{suffix}",
    }
    settings = Settings()
    settings = settings.model_copy(
        update={
            "database": settings.database.model_copy(update=tables),
        }
    )
    store = PostgresSessionStore(settings)
    now = datetime.now(UTC).replace(tzinfo=None)
    conflicting_message_id = f"message-{uuid4().hex}"
    first_session_id = f"import-first-{uuid4().hex}"
    second_session_id = f"import-second-{uuid4().hex}"

    try:
        await store.initialize()
        await store.save_session(
            SessionRecord(
                id=f"existing-{uuid4().hex}",
                title="existing row",
                status=SessionStatus.completed,
                session_mode=SessionMode.normal,
                runtime_mode=RuntimeMode.interactive,
                mode_key="default",
                created_at=now,
                updated_at=now,
                messages=[
                    ChatMessage(
                        id=conflicting_message_id,
                        role=MessageRole.user,
                        content="existing message",
                        created_at=now,
                    )
                ],
            )
        )

        first = SessionImportBundle(
            session=SessionRecord(
                id=first_session_id,
                title="first imported session",
                status=SessionStatus.completed,
                session_mode=SessionMode.normal,
                runtime_mode=RuntimeMode.interactive,
                mode_key="default",
                created_at=now,
                updated_at=now,
                messages=[
                    ChatMessage(
                        id=f"message-{uuid4().hex}",
                        role=MessageRole.user,
                        content="first imported message",
                        created_at=now,
                    )
                ],
            ),
        )
        second = SessionImportBundle(
            session=SessionRecord(
                id=second_session_id,
                title="second imported session",
                status=SessionStatus.completed,
                session_mode=SessionMode.normal,
                runtime_mode=RuntimeMode.interactive,
                mode_key="default",
                created_at=now,
                updated_at=now,
                messages=[
                    ChatMessage(
                        id=conflicting_message_id,
                        role=MessageRole.user,
                        content="conflicting imported message",
                        created_at=now,
                    )
                ],
            ),
        )

        with pytest.raises(IntegrityError):
            await store.import_session_bundles([first, second])

        assert await store.get_session(first_session_id) is None
        assert await store.get_session(second_session_id) is None

        success_session_id = f"import-success-{uuid4().hex}"
        success = SessionImportBundle(
            session=SessionRecord(
                id=success_session_id,
                title="successful imported session",
                status=SessionStatus.completed,
                session_mode=SessionMode.coordinator,
                runtime_mode=RuntimeMode.headless,
                mode_key="code_review",
                created_at=now,
                updated_at=now,
                messages=[
                    ChatMessage(
                        id=f"message-{uuid4().hex}",
                        role=MessageRole.user,
                        content="restored message",
                        created_at=now,
                        metadata={"origin": "live-test"},
                    )
                ],
            ),
            events=[
                ExecutionEvent(
                    id=f"event-{uuid4().hex}",
                    type="turn.completed",
                    session_id=success_session_id,
                    timestamp=now,
                    payload={"result": "passed"},
                )
            ],
            snapshots=[
                SessionSnapshot(
                    id=f"snapshot-{uuid4().hex}",
                    session_id=success_session_id,
                    version=2,
                    stage="completed",
                    created_at=now,
                    graph_state={"node": "finalizer"},
                )
            ],
            approvals=[
                ToolApprovalRequest(
                    id=f"approval-{uuid4().hex}",
                    session_id=success_session_id,
                    tool_key="test-tool",
                    tool_name="Test Tool",
                    reason="requires review",
                    status=ToolApprovalStatus.approved,
                    created_at=now,
                    resolved_at=now,
                    decision_note="approved in test",
                    metadata={"reviewer": "operator"},
                )
            ],
        )
        assert await store.import_session_bundles([success]) == (1, 0)
        assert await store.import_session_bundles([success]) == (0, 1)

        restored = await store.get_session(success_session_id)
        assert restored is not None
        assert restored.session_mode == SessionMode.coordinator
        assert restored.runtime_mode == RuntimeMode.headless
        assert restored.event_count == 1
        assert restored.snapshot_count == 2
        assert [message.content for message in restored.messages] == [
            "restored message"
        ]
        restored_events = await store.list_events(success_session_id)
        assert len(restored_events) == 1
        assert restored_events[0].payload == {"result": "passed"}
        restored_snapshots = await store.list_snapshots(success_session_id)
        assert len(restored_snapshots) == 1
        assert restored_snapshots[0].graph_state == {"node": "finalizer"}
        restored_approvals = await store.list_approvals(success_session_id)
        assert len(restored_approvals) == 1
        assert restored_approvals[0].status == ToolApprovalStatus.approved
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            for table_name in reversed(list(tables.values())):
                cur.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
