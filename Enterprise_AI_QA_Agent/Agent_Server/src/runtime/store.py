from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from src.runtime.framework_application_projection import FrameworkProjectionWrite
    from src.schemas.runtime_binding import RuntimeExecutionOwner

from src.domain.models import SessionImportBundle, SessionRecord
from src.infrastructure.storage_utils import ensure_utc_datetime
from src.schemas.session import (
    ChatMessage,
    ExecutionEvent,
    SessionMode,
    SessionSnapshot,
    SessionStatus,
    ToolApprovalRequest,
    ToolApprovalStatus,
)
from src.schemas.task_pool import TaskPoolSessionSummary


class ContinuationLeaseLostError(RuntimeError):
    """Raised when a continuation worker no longer owns its fencing lease."""


class SessionWriteConflictError(RuntimeError):
    """Raised when a detached session snapshot is older than stored state."""


class EventCursorNotFoundError(ValueError):
    """The SSE cursor is absent from the requested session's durable history."""


def validate_session_import_bundle(bundle: SessionImportBundle) -> None:
    session_id = bundle.session.id
    if not isinstance(session_id, str):
        raise TypeError("Imported session ID must be a string.")
    if not session_id.strip():
        raise ValueError("Imported session ID must be a non-empty string.")

    children = (
        ("message", bundle.session.messages, False),
        ("event", bundle.events, True),
        ("snapshot", bundle.snapshots, True),
        ("approval", bundle.approvals, True),
    )
    for label, records, has_session_id in children:
        seen: set[str] = set()
        for record in records:
            if has_session_id and record.session_id != session_id:
                raise ValueError(f"Imported {label} belongs to a different session.")
            record_id = record.id
            if not isinstance(record_id, str):
                raise TypeError(f"Imported {label} ID must be a string.")
            if not record_id.strip():
                raise ValueError(f"Imported {label} ID must be a non-empty string.")
            if record_id in seen:
                raise ValueError(f"Imported session contains duplicate {label} IDs.")
            seen.add(record_id)

    versions = [snapshot.version for snapshot in bundle.snapshots]
    if any(type(version) is not int for version in versions):
        raise TypeError("Imported snapshot versions must be integers.")
    if any(version < 1 or version > 2_147_483_647 for version in versions):
        raise ValueError("Imported snapshot versions must be positive PostgreSQL integers.")
    if len(versions) != len(set(versions)):
        raise ValueError("Imported session contains duplicate snapshot versions.")
    for field_name, count in (
        ("event_count", bundle.session.event_count),
        ("snapshot_count", bundle.session.snapshot_count),
    ):
        if type(count) is not int:
            raise TypeError(f"Imported {field_name} must be an integer.")
        if count < 0 or count > 2_147_483_647:
            raise ValueError(f"Imported {field_name} must be a non-negative PostgreSQL integer.")


_SESSION_REVISION_OWNED_METADATA_KEYS = frozenset({
    "turn_lease_turn_id",
    "turn_lease_owner",
    "turn_lease_token",
    "turn_lease_expires_at",
    "coordinator_dispatch_claims",
    "worker_dispatches",
})


def session_revision_metadata(metadata: dict) -> dict:
    """Return only metadata fields replaced by a whole-session snapshot."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in _SESSION_REVISION_OWNED_METADATA_KEYS
    }


def session_revision_metadata_changed(before: dict, after: dict) -> bool:
    return session_revision_metadata(before) != session_revision_metadata(after)


def session_snapshot_can_merge_append(snapshot: SessionRecord, current: SessionRecord) -> bool:
    """Allow a stale snapshot to merge only new transcript rows.

    All replaceable session fields must still match, and any persisted message
    already present in the stale snapshot must be byte-for-byte equivalent to
    the current row. This preserves append-only message concurrency without
    permitting stale status, metadata, or delivery-state overwrites.
    """
    replaceable_fields = (
        "title",
        "status",
        "session_mode",
        "runtime_mode",
        "mode_key",
        "project_id",
        "created_at",
        "preferred_model",
        "selected_agent",
    )
    if any(getattr(snapshot, name) != getattr(current, name) for name in replaceable_fields):
        return False
    if session_revision_metadata(snapshot.metadata) != session_revision_metadata(current.metadata):
        return False

    current_messages = {
        message.id: message
        for message in current.messages
        if bool(message.metadata.get("persist_transcript", True))
    }
    return all(
        current_messages.get(message.id) in (None, message)
        for message in snapshot.messages
        if bool(message.metadata.get("persist_transcript", True))
    )


class SessionStore(Protocol):
    """Records are detached values; changes require a store write operation.

    Save methods may update and return their input (as PostgreSQL does), but
    neither input nor output may expose the backend's stored mutable state.
    """
    async def initialize(self) -> None: ...
    async def save_session(
        self,
        session: SessionRecord,
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> SessionRecord: ...
    async def save_session_with_events(
        self,
        session: SessionRecord,
        events: list[ExecutionEvent],
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
    ) -> SessionRecord: ...
    async def import_session_bundles(
        self, bundles: list[SessionImportBundle]
    ) -> tuple[int, int]: ...
    async def get_session(self, session_id: str) -> SessionRecord | None: ...
    async def mutate_metadata(
        self, session_id: str, mutate: Callable[[dict], None]
    ) -> bool: ...
    async def update_status_and_mutate_metadata(
        self,
        session_id: str,
        *,
        status: SessionStatus | None,
        mutate: Callable[[dict, SessionStatus], None],
        allowed_current_statuses: set[SessionStatus] | None = None,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> bool: ...
    async def update_status_and_append_messages_and_events(
        self,
        session_id: str,
        *,
        status: SessionStatus | None,
        messages: list[ChatMessage],
        events: list[ExecutionEvent],
    ) -> bool: ...
    async def list_sessions(
        self,
        limit: int | None = None,
        offset: int = 0,
        mode_key: str | None = None,
        cursor_before: datetime | None = None,
        mode_keys: list[str] | None = None,
    ) -> list[SessionRecord]: ...
    async def list_sessions_with_pending_input_queue(self) -> list[SessionRecord]: ...
    async def list_task_pool_sessions(
        self,
        limit: int = 24,
        offset: int = 0,
    ) -> list[TaskPoolSessionSummary]: ...
    async def list_history_overviews(self, limit: int = 10) -> list[dict]: ...
    async def list_recent_questions(
        self,
        session_limit: int = 10,
        question_limit: int = 10,
        include_assistant: bool = False,
    ) -> list[dict]: ...
    async def append_event(
        self,
        session_id: str,
        event: ExecutionEvent,
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
        runtime_owner: RuntimeExecutionOwner | None = None,
    ) -> None: ...
    async def list_events(
        self,
        session_id: str,
        limit: int | None = None,
        after_event_id: str | None = None,
    ) -> list[ExecutionEvent]: ...
    async def read_event_page(self, session_id: str, *, limit: int, after_event_id: str | None = None) -> list[ExecutionEvent]: ...
    async def save_snapshot(
        self,
        session_id: str,
        snapshot: SessionSnapshot,
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
    ) -> SessionSnapshot | None: ...
    async def update_metadata_and_append_event(
        self,
        session_id: str,
        update: Callable[[dict], bool],
        event: ExecutionEvent,
    ) -> bool: ...
    async def list_snapshots(
        self,
        session_id: str,
        limit: int | None = None,
        include_graph_state: bool = True,
    ) -> list[SessionSnapshot]: ...
    async def get_latest_snapshot(self, session_id: str) -> SessionSnapshot | None: ...
    async def save_approval(self, session_id: str, approval: ToolApprovalRequest) -> None: ...
    async def list_approvals(self, session_id: str) -> list[ToolApprovalRequest]: ...
    async def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest: ...
    async def claim_turn_execution(
        self,
        session_id: str,
        turn_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int,
        approval_id: str | None = None,
    ) -> SessionRecord | None: ...
    async def renew_turn_execution(
        self,
        session_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool: ...
    async def complete_turn_execution(
        self,
        session_id: str,
        lease_token: str,
    ) -> bool: ...
    async def claim_coordinator_dispatch(
        self,
        session_id: str,
        turn_id: str,
        dispatch_key: str,
        owner_id: str,
        request_fingerprint: str | None = None,
        worker_task_ids: list[str] | None = None,
    ) -> bool: ...
    async def release_coordinator_dispatch_claim_if_no_child(
        self,
        session_id: str,
        turn_id: str,
        dispatch_key: str,
        owner_id: str,
        request_fingerprint: str,
        worker_task_ids: list[str] | None = None,
        require_settled_parent: bool = False,
    ) -> bool: ...
    async def recover_expired_turn_executions(self) -> list[str]: ...
    async def assert_turn_execution(self, session_id: str, lease_token: str) -> None: ...
    async def claim_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool: ...
    async def renew_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool: ...
    async def complete_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        lease_token: str,
    ) -> bool: ...
    async def delete_session(self, session_id: str) -> bool: ...
    async def count_sessions(self, before: datetime | None = None, after: datetime | None = None) -> int: ...
    async def count_sessions_by_status(self) -> dict[str, int]: ...
    async def count_project_sessions(self, project_id: str) -> int: ...
    async def list_project_history_context(
        self,
        project_id: str,
        *,
        limit: int,
    ) -> list[dict]: ...
    async def delete_sessions_before(self, cutoff: datetime | None = None) -> int: ...
    async def get_session_date_range(self) -> dict[str, datetime | None]: ...
    async def bulk_export(self, progress_fn: object = None) -> list[dict]: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._events: dict[str, list[ExecutionEvent]] = defaultdict(list)
        self._snapshots: dict[str, list[SessionSnapshot]] = defaultdict(list)
        self._approvals: dict[str, dict[str, ToolApprovalRequest]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def import_session_bundles(
        self, bundles: list[SessionImportBundle]
    ) -> tuple[int, int]:
        async with self._lock:
            staged: list[SessionImportBundle] = []
            skipped = 0
            seen_sessions: set[str] = set()
            seen_child_ids: dict[str, set[str]] = {
                "message": set(),
                "event": set(),
                "snapshot": set(),
                "approval": set(),
            }

            for bundle in bundles:
                session_id = bundle.session.id
                if session_id in self._sessions or session_id in seen_sessions:
                    skipped += 1
                    continue

                validate_session_import_bundle(bundle)
                for label, records in (
                    ("message", bundle.session.messages),
                    ("event", bundle.events),
                    ("snapshot", bundle.snapshots),
                    ("approval", bundle.approvals),
                ):
                    record_ids = {record.id for record in records}
                    if record_ids & seen_child_ids[label]:
                        raise ValueError(f"Imported backup contains duplicate {label} IDs.")
                    seen_child_ids[label].update(record_ids)

                seen_sessions.add(session_id)
                bundle.session.event_count = max(
                    bundle.session.event_count,
                    len(bundle.events),
                )
                bundle.session.snapshot_count = max(
                    bundle.session.snapshot_count,
                    max((snapshot.version for snapshot in bundle.snapshots), default=0),
                )
                staged.append(deepcopy(bundle))

            # All validation and duplicate checks finish before any visible state changes.
            for bundle in staged:
                session_id = bundle.session.id
                bundle.session.state_revision = 1
                self._sessions[session_id] = bundle.session
                self._events[session_id] = list(bundle.events)
                self._snapshots[session_id] = list(bundle.snapshots)
                self._approvals[session_id] = {
                    approval.id: approval for approval in bundle.approvals
                }

            return len(staged), skipped

    async def save_session(
        self,
        session: SessionRecord,
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> SessionRecord:
        async with self._lock:
            if continuation_approval_id or continuation_lease_token:
                self._assert_continuation_lease_locked(
                    session.id, continuation_approval_id, continuation_lease_token
                )
            if turn_lease_token:
                self._assert_turn_lease_locked(session.id, turn_lease_token)
            return self._save_session_locked(session)

    async def save_session_with_events(
        self,
        session: SessionRecord,
        events: list[ExecutionEvent],
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
    ) -> SessionRecord:
        if framework_projection is not None:
            raise ValueError("Framework application commits require a PostgreSQL owner transaction")
        async with self._lock:
            if continuation_approval_id or continuation_lease_token:
                self._assert_continuation_lease_locked(
                    session.id, continuation_approval_id, continuation_lease_token
                )
            if turn_lease_token:
                self._assert_turn_lease_locked(session.id, turn_lease_token)
            previous_session = deepcopy(self._sessions.get(session.id))
            previous_events = list(self._events[session.id])
            candidate = deepcopy(session)
            try:
                self._save_session_locked(candidate)
                for event in events:
                    if event.session_id != candidate.id:
                        raise ValueError("Final event session_id does not match the session being saved.")
                    self._append_event_locked(candidate.id, event)
            except Exception:
                if previous_session is None:
                    self._sessions.pop(session.id, None)
                else:
                    self._sessions[session.id] = previous_session
                self._events[session.id] = previous_events
                raise
            session.event_count = candidate.event_count
            session.snapshot_count = candidate.snapshot_count
            session.metadata = deepcopy(candidate.metadata)
            session.state_revision = candidate.state_revision
            session.updated_at = candidate.updated_at
            return session

    def _save_session_locked(self, session: SessionRecord) -> SessionRecord:
        current = self._sessions.get(session.id)
        if current is not None:
            if (session.state_revision != current.state_revision
                    and not session_snapshot_can_merge_append(session, current)):
                raise SessionWriteConflictError(
                    f"Session {session.id} changed since revision {session.state_revision}; "
                    "reload it and apply the intended update to current state."
                )
            # Snapshot saves cannot replace lease ownership or SQL-style
            # coordinator claims advanced by another operation.
            for key in (
                "turn_lease_turn_id", "turn_lease_owner", "turn_lease_token",
                "turn_lease_expires_at", "coordinator_dispatch_claims",
            ):
                if key in current.metadata:
                    session.metadata[key] = deepcopy(current.metadata[key])
                else:
                    session.metadata.pop(key, None)
            if "worker_dispatches" in current.metadata:
                session.metadata["worker_dispatches"] = deepcopy(current.metadata["worker_dispatches"])
            session.event_count = max(current.event_count, session.event_count)
            session.snapshot_count = max(current.snapshot_count, session.snapshot_count)
            session.state_revision = current.state_revision + 1
        else:
            if session.state_revision != 0:
                raise SessionWriteConflictError(
                    f"Session {session.id} no longer exists at revision {session.state_revision}."
                )
            session.state_revision = 1
        session.updated_at = datetime.utcnow()
        stored = deepcopy(session)
        # PostgreSQL upserts transcript rows by message ID; a stale document
        # must not delete messages saved by another operation. Context-only
        # messages remain in the caller's working value, never the transcript.
        messages = {item.id: deepcopy(item) for item in current.messages} if current else {}
        for item in stored.messages:
            if bool(item.metadata.get("persist_transcript", True)):
                messages[item.id] = item
        stored.messages = sorted(messages.values(), key=lambda item: ensure_utc_datetime(item.created_at))
        self._sessions[session.id] = stored
        return session

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return deepcopy(self._sessions.get(session_id))

    async def mutate_metadata(
        self, session_id: str, mutate: Callable[[dict], None]
    ) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            metadata = deepcopy(session.metadata)
            mutate(metadata)
            changed = session_revision_metadata_changed(session.metadata, metadata)
            session.metadata = deepcopy(metadata)
            if changed:
                session.state_revision += 1
            session.updated_at = datetime.now(UTC).replace(tzinfo=None)
            return True

    async def update_status_and_mutate_metadata(
        self,
        session_id: str,
        *,
        status: SessionStatus | None,
        mutate: Callable[[dict, SessionStatus], None],
        allowed_current_statuses: set[SessionStatus] | None = None,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> bool:
        async with self._lock:
            if continuation_approval_id or continuation_lease_token:
                self._assert_continuation_lease_locked(
                    session_id, continuation_approval_id, continuation_lease_token
                )
            if turn_lease_token:
                self._assert_turn_lease_locked(session_id, turn_lease_token)
            session = self._sessions.get(session_id)
            if session is None:
                return False
            metadata = deepcopy(session.metadata)
            current_status = session.status
            mutate(metadata, current_status)
            changed = (
                session_revision_metadata_changed(session.metadata, metadata)
                or (status is not None and current_status != status
                    and current_status in (allowed_current_statuses or set()))
            )
            session.metadata = deepcopy(metadata)
            if status is not None and current_status in (allowed_current_statuses or set()):
                session.status = status
            if changed:
                session.state_revision += 1
            session.updated_at = datetime.now(UTC).replace(tzinfo=None)
            return True

    async def update_status_and_append_messages_and_events(
        self,
        session_id: str,
        *,
        status: SessionStatus | None,
        messages: list[ChatMessage],
        events: list[ExecutionEvent],
    ) -> bool:
        if any(event.session_id != session_id for event in events):
            raise ValueError("Projection event session_id does not match the session.")
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            old_status = session.status
            if status is not None:
                session.status = status
            existing_message_ids = {item.id for item in session.messages}
            messages_changed = False
            for message in messages:
                if bool(message.metadata.get("persist_transcript", True)) and message.id not in existing_message_ids:
                    session.messages.append(deepcopy(message))
                    existing_message_ids.add(message.id)
                    messages_changed = True
            if (status is not None and status != old_status) or messages_changed:
                session.state_revision += 1
            session.updated_at = datetime.utcnow()
            for event in events:
                self._append_event_locked(session_id, event)
            return True

    async def list_sessions(
        self,
        limit: int | None = None,
        offset: int = 0,
        mode_key: str | None = None,
        cursor_before: datetime | None = None,
        mode_keys: list[str] | None = None,
    ) -> list[SessionRecord]:
        sessions = sorted(self._sessions.values(), key=lambda item: ensure_utc_datetime(item.updated_at), reverse=True)
        if mode_key:
            sessions = [item for item in sessions if item.mode_key == mode_key]
        if mode_keys is not None:
            sessions = [item for item in sessions if item.mode_key in mode_keys]
        if cursor_before is not None:
            sessions = [s for s in sessions if ensure_utc_datetime(s.updated_at) < ensure_utc_datetime(cursor_before)]
        start = max(int(offset or 0), 0)
        if limit is None:
            return deepcopy(sessions[start:])
        size = max(int(limit or 0), 0)
        if size <= 0:
            return []
        return deepcopy(sessions[start : start + size])

    async def list_sessions_with_pending_input_queue(self) -> list[SessionRecord]:
        return deepcopy([
            session
            for session in self._sessions.values()
            if isinstance(session.metadata, dict)
            and isinstance(session.metadata.get("pending_input_queue"), list)
            and bool(session.metadata.get("pending_input_queue"))
        ])

    async def list_task_pool_sessions(
        self,
        limit: int = 24,
        offset: int = 0,
    ) -> list[TaskPoolSessionSummary]:
        sessions = sorted(
            (
                item
                for item in self._sessions.values()
                if item.mode_key == "code_review"
                or item.session_mode == SessionMode.background_task
            ),
            key=lambda item: ensure_utc_datetime(item.updated_at),
            reverse=True,
        )
        start = max(int(offset or 0), 0)
        size = max(int(limit or 0), 0)
        visible = sessions[start : start + size]
        rows = []
        for session in visible:
            metadata = session.metadata if isinstance(session.metadata, dict) else {}
            workers = [
                deepcopy(item)
                for item in metadata.get("worker_dispatches", [])
                if isinstance(item, dict)
            ]
            rows.append(
                TaskPoolSessionSummary(
                    id=session.id,
                    title=session.title,
                    status=session.status,
                    session_mode=session.session_mode,
                    runtime_mode=session.runtime_mode,
                    mode_key=session.mode_key,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    selected_agent=session.selected_agent,
                    worker_dispatches=workers,
                    parent_session_id=str(
                        metadata.get("parent_session_id") or ""
                    ).strip(),
                )
            )
        return rows

    async def list_history_overviews(self, limit: int = 10) -> list[dict]:
        sessions = await self.list_sessions(limit=limit)
        return [
            {
                "session_id": session.id,
                "title": session.title,
                "status": session.status.value,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "message_count": len(session.messages),
                "event_count": session.event_count,
                "snapshot_count": session.snapshot_count,
                "selected_agent": session.selected_agent,
                "preferred_model": session.preferred_model,
            }
            for session in sessions
        ]

    async def list_recent_questions(
        self,
        session_limit: int = 10,
        question_limit: int = 10,
        include_assistant: bool = False,
    ) -> list[dict]:
        sessions = await self.list_sessions(limit=session_limit)
        items = []
        for session in sessions:
            for message in session.messages:
                role = message.role.value
                if role != "user" and not (include_assistant and role == "assistant"):
                    continue
                if not message.content.strip():
                    continue
                items.append(
                    {
                        "session_id": session.id,
                        "role": role,
                        "content": message.content.strip(),
                        "created_at": message.created_at.isoformat(),
                    }
                )
        items.sort(key=lambda item: ensure_utc_datetime(item["created_at"]), reverse=True)
        return items[: max(int(question_limit), 1)]

    async def append_event(
        self,
        session_id: str,
        event: ExecutionEvent,
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
        runtime_owner: RuntimeExecutionOwner | None = None,
    ) -> None:
        if framework_projection is not None or runtime_owner is not None:
            raise ValueError("Framework event receipts require a PostgreSQL owner transaction")
        async with self._lock:
            if continuation_approval_id or continuation_lease_token:
                self._assert_continuation_lease_locked(
                    session_id, continuation_approval_id, continuation_lease_token
                )
            if turn_lease_token:
                self._assert_turn_lease_locked(session_id, turn_lease_token)
            self._append_event_locked(session_id, event)

    async def update_metadata_and_append_event(
        self,
        session_id: str,
        update: Callable[[dict], bool],
        event: ExecutionEvent,
    ) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if event.session_id != session_id:
                raise ValueError("Projection event session_id does not match the session.")
            metadata = deepcopy(session.metadata)
            if not update(metadata):
                return False
            if any(item.id == event.id for item in self._events[session_id]):
                raise ValueError("Projection event ID already exists.")
            previous_session = deepcopy(session)
            previous_events = list(self._events[session_id])
            try:
                if session_revision_metadata_changed(session.metadata, metadata):
                    session.state_revision += 1
                session.metadata = deepcopy(metadata)
                self._append_event_locked(session_id, event)
            except Exception:
                self._sessions[session_id] = previous_session
                self._events[session_id] = previous_events
                raise
            return True

    def _append_event_locked(self, session_id: str, event: ExecutionEvent) -> None:
        if any(item.id == event.id for item in self._events[session_id]):
            return
        session = self._sessions.get(session_id)
        if session is not None:
            session.event_count += 1
            session.updated_at = datetime.utcnow()
        self._events[session_id].append(event.model_copy(deep=True))

    async def list_events(
        self,
        session_id: str,
        limit: int | None = None,
        after_event_id: str | None = None,
    ) -> list[ExecutionEvent]:
        events = list(self._events.get(session_id, []))
        has_cursor = bool(after_event_id)
        if after_event_id:
            cursor_index = next(
                (index for index, event in enumerate(events) if str(event.id) == after_event_id),
                -1,
            )
            events = events[cursor_index + 1 :] if cursor_index >= 0 else []
        if limit is not None:
            size = max(int(limit), 0)
            if size <= 0:
                return []
            events = events[:size] if has_cursor else events[-size:]
        return deepcopy(events)

    async def read_event_page(self, session_id: str, *, limit: int, after_event_id: str | None = None) -> list[ExecutionEvent]:
        events = self._events.get(session_id, [])
        start = 0
        if after_event_id:
            index = next((index for index, item in enumerate(events) if item.id == after_event_id), None)
            if index is None:
                raise EventCursorNotFoundError(session_id)
            else:
                start = index + 1
        return deepcopy(events[start:start + max(0, limit)])

    async def save_snapshot(
        self,
        session_id: str,
        snapshot: SessionSnapshot,
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
    ) -> SessionSnapshot | None:
        if framework_projection is not None:
            raise ValueError("Framework snapshot receipts require a PostgreSQL owner transaction")
        async with self._lock:
            if continuation_approval_id or continuation_lease_token:
                self._assert_continuation_lease_locked(
                    session_id, continuation_approval_id, continuation_lease_token
                )
            if turn_lease_token:
                self._assert_turn_lease_locked(session_id, turn_lease_token)
            session = self._sessions.get(session_id)
            if session is not None:
                session.snapshot_count = max(session.snapshot_count, snapshot.version)
                session.updated_at = datetime.utcnow()
            self._snapshots[session_id].append(snapshot.model_copy(deep=True))

    def _assert_turn_lease_locked(self, session_id: str, lease_token: str) -> None:
        session = self._sessions.get(session_id)
        if session is None or session.metadata.get("turn_lease_token") != lease_token:
            raise ContinuationLeaseLostError("Turn execution lease is no longer owned by this worker.")
        expires_at = _parse_datetime(session.metadata.get("turn_lease_expires_at"))
        if expires_at is None or expires_at <= datetime.utcnow():
            raise ContinuationLeaseLostError("Turn execution lease has expired.")

    def _assert_continuation_lease_locked(
        self,
        session_id: str,
        approval_id: str | None,
        lease_token: str | None,
    ) -> None:
        if not approval_id or not lease_token:
            raise ContinuationLeaseLostError("Continuation fencing requires approval id and lease token.")
        approval = self._approvals.get(session_id, {}).get(approval_id)
        if approval is None or approval.metadata.get("continuation_completed_at"):
            raise ContinuationLeaseLostError("Continuation lease is no longer active.")
        if approval.metadata.get("continuation_lease_token") != lease_token:
            raise ContinuationLeaseLostError("Continuation lease token is no longer owned by this worker.")
        expires_at = _parse_datetime(approval.metadata.get("continuation_lease_expires_at"))
        if expires_at is None or expires_at <= datetime.utcnow():
            raise ContinuationLeaseLostError("Continuation lease has expired.")

    async def list_snapshots(
        self,
        session_id: str,
        limit: int | None = None,
        include_graph_state: bool = True,
    ) -> list[SessionSnapshot]:
        snapshots = list(self._snapshots.get(session_id, []))
        if limit is not None:
            size = max(int(limit), 0)
            if size <= 0:
                return []
            snapshots = snapshots[-size:]
        if not include_graph_state:
            snapshots = [snapshot.model_copy(update={"graph_state": {}}, deep=True) for snapshot in snapshots]
            return snapshots
        return deepcopy(snapshots)

    async def get_latest_snapshot(self, session_id: str) -> SessionSnapshot | None:
        snapshots = self._snapshots.get(session_id, [])
        return snapshots[-1].model_copy(deep=True) if snapshots else None

    async def save_approval(self, session_id: str, approval: ToolApprovalRequest) -> None:
        async with self._lock:
            self._approvals[session_id][approval.id] = approval.model_copy(deep=True)
            session = self._sessions.get(session_id)
            if session is not None:
                session.updated_at = datetime.utcnow()

    async def list_approvals(self, session_id: str) -> list[ToolApprovalRequest]:
        return deepcopy(sorted(self._approvals.get(session_id, {}).values(), key=lambda item: item.created_at))

    async def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest:
        async with self._lock:
            if session_id not in self._approvals or approval_id not in self._approvals[session_id]:
                raise KeyError(approval_id)
            approval = self._approvals[session_id][approval_id]
            if approval.status != ToolApprovalStatus.pending:
                if approval.status == status:
                    return approval.model_copy(deep=True)
                raise ValueError(f"Approval already resolved: {approval_id}")
            approval.status = status
            approval.decision_note = reason
            approval.resolved_at = datetime.utcnow()
            session = self._sessions.get(session_id)
            if session is not None:
                session.updated_at = approval.resolved_at
            return approval.model_copy(deep=True)

    async def claim_turn_execution(
        self,
        session_id: str,
        turn_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int,
        approval_id: str | None = None,
    ) -> SessionRecord | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            metadata = session.metadata
            expires_at = _parse_datetime(metadata.get("turn_lease_expires_at"))
            if expires_at is not None and expires_at > datetime.utcnow():
                return None
            pending = metadata.get("pending_turn") or {}
            owned_approval = (session.status.value == "waiting_approval" and approval_id is not None
                and pending.get("turn_id") == turn_id and approval_id in pending.get("pending_approval_ids", []))
            if (
                session.status.value in {"running", "waiting_approval"}
                and metadata.get("turn_lease_turn_id") != turn_id
                and not owned_approval
            ):
                return None
            metadata.update({
                "turn_lease_turn_id": turn_id,
                "turn_lease_owner": owner_id,
                "turn_lease_token": lease_token,
                "turn_lease_expires_at": (
                    datetime.utcnow() + timedelta(seconds=lease_seconds)
                ).isoformat(),
            })
            session.status = type(session.status).running
            session.state_revision += 1
            session.updated_at = datetime.utcnow()
            return deepcopy(session)

    async def renew_turn_execution(
        self,
        session_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.metadata.get("turn_lease_token") != lease_token:
                return False
            expires_at = _parse_datetime(session.metadata.get("turn_lease_expires_at"))
            if expires_at is None or expires_at <= datetime.utcnow():
                return False
            session.metadata["turn_lease_expires_at"] = (
                datetime.utcnow() + timedelta(seconds=lease_seconds)
            ).isoformat()
            return True

    async def complete_turn_execution(
        self,
        session_id: str,
        lease_token: str,
    ) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.metadata.get("turn_lease_token") != lease_token:
                return False
            expires_at = _parse_datetime(session.metadata.get("turn_lease_expires_at"))
            if expires_at is None or expires_at <= datetime.utcnow():
                return False
            for key in (
                "turn_lease_turn_id",
                "turn_lease_owner",
                "turn_lease_token",
                "turn_lease_expires_at",
            ):
                session.metadata.pop(key, None)
            session.state_revision += 1
            session.updated_at = datetime.utcnow()
            return True

    async def claim_coordinator_dispatch(
        self,
        session_id: str,
        turn_id: str,
        dispatch_key: str,
        owner_id: str,
        request_fingerprint: str | None = None,
        worker_task_ids: list[str] | None = None,
    ) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            claims = session.metadata.get("coordinator_dispatch_claims", [])
            if not isinstance(claims, list):
                claims = []
            if any(
                isinstance(item, dict)
                and item.get("turn_id") == turn_id
                and item.get("dispatch_key") == dispatch_key
                for item in claims
            ):
                return False
            claims.append(
                {
                    "turn_id": turn_id,
                    "dispatch_key": dispatch_key,
                    "owner_id": owner_id,
                    "claimed_at": datetime.utcnow().isoformat(),
                    "request_fingerprint": request_fingerprint,
                    "worker_task_ids": list(worker_task_ids or []),
                }
            )
            session.metadata["coordinator_dispatch_claims"] = claims
            session.updated_at = datetime.utcnow()
            return True

    async def release_coordinator_dispatch_claim_if_no_child(
        self,
        session_id: str,
        turn_id: str,
        dispatch_key: str,
        owner_id: str,
        request_fingerprint: str,
        worker_task_ids: list[str] | None = None,
        require_settled_parent: bool = False,
    ) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if require_settled_parent:
                status = getattr(session.status, "value", str(session.status))
                if status not in {
                    SessionStatus.interrupted.value,
                    SessionStatus.completed.value,
                    SessionStatus.failed.value,
                } or str(session.metadata.get("turn_lease_token") or "").strip():
                    return False
            claims = session.metadata.get("coordinator_dispatch_claims", [])
            if not isinstance(claims, list):
                return False
            claim_index = next(
                (
                    index
                    for index, item in enumerate(claims)
                    if isinstance(item, dict)
                    and item.get("turn_id") == turn_id
                    and item.get("dispatch_key") == dispatch_key
                    and item.get("owner_id") == owner_id
                    and item.get("request_fingerprint") == request_fingerprint
                ),
                None,
            )
            if claim_index is None:
                return False
            has_child = any(
                child.id != session_id
                and isinstance(child.metadata, dict)
                and child.metadata.get("parent_session_id") == session_id
                and child.metadata.get("parent_turn_id") == turn_id
                and (
                    child.metadata.get("coordinator_dispatch_key") == dispatch_key
                    or str(child.metadata.get("task_id") or "")
                    in set(worker_task_ids or [])
                )
                for child in self._sessions.values()
            )
            if has_child:
                return False
            del claims[claim_index]
            session.metadata["coordinator_dispatch_claims"] = claims
            session.updated_at = datetime.utcnow()
            return True

    async def recover_expired_turn_executions(self) -> list[str]:
        async with self._lock:
            recovered: list[str] = []
            now = datetime.utcnow()
            for session in self._sessions.values():
                if session.status.value != "running":
                    continue
                expires_at = _parse_datetime(session.metadata.get("turn_lease_expires_at"))
                if expires_at is None or expires_at > now:
                    continue
                for key in (
                    "turn_lease_turn_id",
                    "turn_lease_owner",
                    "turn_lease_token",
                    "turn_lease_expires_at",
                ):
                    session.metadata.pop(key, None)
                control = session.metadata.get("control", {})
                if not isinstance(control, dict):
                    control = {}
                has_pending_turn = bool(session.metadata.get("pending_turn"))
                control.update(
                    {
                        "control_state": "interrupted",
                        "is_interrupted": True,
                        "is_resumable": has_pending_turn,
                        "preserve_resources": has_pending_turn,
                        "last_interrupt_reason": "Turn execution lease expired while the service was unavailable.",
                    }
                )
                session.metadata["control"] = control
                session.status = type(session.status).interrupted
                session.state_revision += 1
                session.updated_at = now
                recovered.append(session.id)
            return recovered

    async def assert_turn_execution(self, session_id: str, lease_token: str) -> None:
        async with self._lock:
            self._assert_turn_lease_locked(session_id, lease_token)

    async def claim_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        async with self._lock:
            approval = self._approvals.get(session_id, {}).get(approval_id)
            if approval is None or approval.status == ToolApprovalStatus.pending:
                return False
            metadata = approval.metadata
            if metadata.get("continuation_completed_at"):
                return False
            expires_at = _parse_datetime(metadata.get("continuation_lease_expires_at"))
            if expires_at is not None and expires_at > datetime.utcnow():
                return False
            metadata.update({
                "continuation_lease_owner": owner_id,
                "continuation_lease_token": lease_token,
                "continuation_lease_expires_at": (
                    datetime.utcnow() + timedelta(seconds=lease_seconds)
                ).isoformat(),
            })
            return True

    async def renew_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        async with self._lock:
            approval = self._approvals.get(session_id, {}).get(approval_id)
            if approval is None or approval.metadata.get("continuation_completed_at"):
                return False
            if approval.metadata.get("continuation_lease_token") != lease_token:
                return False
            approval.metadata["continuation_lease_expires_at"] = (
                datetime.utcnow() + timedelta(seconds=lease_seconds)
            ).isoformat()
            return True

    async def complete_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        lease_token: str,
    ) -> bool:
        async with self._lock:
            approval = self._approvals.get(session_id, {}).get(approval_id)
            if approval is None:
                return False
            if approval.metadata.get("continuation_lease_token") != lease_token:
                return False
            expires_at = _parse_datetime(approval.metadata.get("continuation_lease_expires_at"))
            if expires_at is None or expires_at <= datetime.utcnow():
                return False
            approval.metadata.pop("continuation_lease_owner", None)
            approval.metadata.pop("continuation_lease_token", None)
            approval.metadata.pop("continuation_lease_expires_at", None)
            approval.metadata["continuation_completed_at"] = datetime.utcnow().isoformat()
            return True

    async def delete_session(self, session_id: str) -> bool:
        async with self._lock:
            if session_id not in self._sessions:
                return False
            del self._sessions[session_id]
            self._events.pop(session_id, None)
            self._snapshots.pop(session_id, None)
            self._approvals.pop(session_id, None)
            return True

    async def count_sessions(self, before: datetime | None = None, after: datetime | None = None) -> int:
        if before is None and after is None:
            return len(self._sessions)
        count = 0
        for s in self._sessions.values():
            t = ensure_utc_datetime(s.updated_at or s.created_at)
            if after and t < ensure_utc_datetime(after):
                continue
            if before and t >= ensure_utc_datetime(before):
                continue
            count += 1
        return count

    async def count_sessions_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self._sessions.values():
            key = s.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    async def count_project_sessions(self, project_id: str) -> int:
        return sum(1 for session in self._sessions.values() if session.project_id == project_id)

    async def list_project_history_context(
        self,
        project_id: str,
        *,
        limit: int,
    ) -> list[dict]:
        sessions = sorted(
            (session for session in self._sessions.values() if session.project_id == project_id),
            key=lambda session: ensure_utc_datetime(session.updated_at),
            reverse=True,
        )[: max(1, min(limit, 50))]
        return [_session_history_context(session) for session in sessions]

    async def delete_sessions_before(self, cutoff: datetime | None = None) -> int:
        async with self._lock:
            if cutoff is None:
                ids = list(self._sessions.keys())
            else:
                ids = [sid for sid, s in self._sessions.items()
                       if ensure_utc_datetime(s.updated_at or s.created_at) < ensure_utc_datetime(cutoff)]
            for sid in ids:
                del self._sessions[sid]
                self._events.pop(sid, None)
                self._snapshots.pop(sid, None)
                self._approvals.pop(sid, None)
            return len(ids)

    async def get_session_date_range(self) -> dict[str, datetime | None]:
        if not self._sessions:
            return {"oldest": None, "newest": None}
        dates = [s.updated_at or s.created_at for s in self._sessions.values()]
        return {"oldest": min(dates, key=ensure_utc_datetime), "newest": max(dates, key=ensure_utc_datetime)}

    async def bulk_export(self, progress_fn=None) -> list[dict]:
        sessions = sorted(self._sessions.values(), key=lambda s: ensure_utc_datetime(s.updated_at), reverse=True)
        bundle: list[dict] = []
        for s in sessions:
            def _iso(v: object) -> str | None:
                return v.isoformat() if hasattr(v, "isoformat") else None
            sid = s.id
            bundle.append({
                "id": sid,
                "title": s.title,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "session_mode": s.session_mode.value if hasattr(s.session_mode, "value") else str(s.session_mode),
                "runtime_mode": s.runtime_mode.value if hasattr(s.runtime_mode, "value") else str(s.runtime_mode),
                "mode_key": s.mode_key,
                "created_at": _iso(s.created_at),
                "updated_at": _iso(s.updated_at),
                "preferred_model": s.preferred_model,
                "selected_agent": s.selected_agent,
                "project_id": s.project_id,
                "metadata": s.metadata,
                "event_count": s.event_count,
                "snapshot_count": s.snapshot_count,
                "messages": [
                    {"id": m.id, "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                     "content": m.content, "created_at": _iso(m.created_at), "metadata": m.metadata}
                    for m in (s.messages or [])
                ],
                "events": [
                    {"type": e.type, "session_id": sid, "timestamp": _iso(e.timestamp), "payload": e.payload}
                    for e in self._events.get(sid, [])
                ],
                "snapshots": [
                    {"id": sn.id, "session_id": sid, "version": sn.version, "stage": sn.stage,
                     "created_at": _iso(sn.created_at), "graph_state": sn.graph_state}
                    for sn in self._snapshots.get(sid, [])
                ],
                "approvals": [
                    {"id": a.id, "session_id": sid, "tool_key": a.tool_key, "tool_name": a.tool_name,
                     "reason": a.reason, "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                     "created_at": _iso(a.created_at), "resolved_at": _iso(a.resolved_at),
                     "decision_note": a.decision_note, "metadata": a.metadata}
                    for a in sorted(self._approvals.get(sid, {}).values(), key=lambda x: x.created_at)
                ],
            })
        return deepcopy(bundle)


def _session_history_context(session: SessionRecord) -> dict:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    failure_summary = str(
        metadata.get("last_error")
        or metadata.get("failure_summary")
        or metadata.get("report_summary")
        or ""
    ).strip()
    if not failure_summary:
        for result in metadata.get("verification_results", []):
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "") in {"failed", "partial"}:
                failure_summary = str(result.get("summary") or "").strip()
                if failure_summary:
                    break
    return {
        "session_id": session.id,
        "title": session.title,
        "mode_key": session.mode_key,
        "status": session.status.value,
        "updated_at": session.updated_at.isoformat(),
        "failure_summary": failure_summary[:500],
    }


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
