from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from src.core.config import Settings
from src.domain.models import SessionRecord
from src.infrastructure.arango_runtime import (
    ArangoRuntimeProvider,
    day_bucket,
    ensure_utc_datetime,
    make_json_safe,
    serialize_datetime,
)
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


class SessionStore(Protocol):
    async def initialize(self) -> None: ...
    async def save_session(self, session: SessionRecord) -> SessionRecord: ...
    async def get_session(self, session_id: str) -> SessionRecord | None: ...
    async def list_sessions(self) -> list[SessionRecord]: ...
    async def append_event(self, session_id: str, event: ExecutionEvent) -> None: ...
    async def list_events(self, session_id: str) -> list[ExecutionEvent]: ...
    def get_queue(self, session_id: str) -> asyncio.Queue[ExecutionEvent]: ...
    async def save_snapshot(self, session_id: str, snapshot: SessionSnapshot) -> None: ...
    async def list_snapshots(self, session_id: str) -> list[SessionSnapshot]: ...
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


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._events: dict[str, list[ExecutionEvent]] = defaultdict(list)
        self._queues: dict[str, asyncio.Queue[ExecutionEvent]] = defaultdict(asyncio.Queue)
        self._snapshots: dict[str, list[SessionSnapshot]] = defaultdict(list)
        self._approvals: dict[str, dict[str, ToolApprovalRequest]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def save_session(self, session: SessionRecord) -> SessionRecord:
        async with self._lock:
            session.updated_at = datetime.utcnow()
            self._sessions[session.id] = session
            return session

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[SessionRecord]:
        return sorted(self._sessions.values(), key=lambda item: item.updated_at, reverse=True)

    async def append_event(self, session_id: str, event: ExecutionEvent) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.event_count += 1
            session.updated_at = datetime.utcnow()
        self._events[session_id].append(event)
        await self._queues[session_id].put(event)

    async def list_events(self, session_id: str) -> list[ExecutionEvent]:
        return list(self._events.get(session_id, []))

    def get_queue(self, session_id: str) -> asyncio.Queue[ExecutionEvent]:
        return self._queues[session_id]

    async def save_snapshot(self, session_id: str, snapshot: SessionSnapshot) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.snapshot_count = max(session.snapshot_count, snapshot.version)
            session.updated_at = datetime.utcnow()
        self._snapshots[session_id].append(snapshot)

    async def list_snapshots(self, session_id: str) -> list[SessionSnapshot]:
        return list(self._snapshots.get(session_id, []))

    async def get_latest_snapshot(self, session_id: str) -> SessionSnapshot | None:
        snapshots = self._snapshots.get(session_id, [])
        return snapshots[-1] if snapshots else None

    async def save_approval(self, session_id: str, approval: ToolApprovalRequest) -> None:
        self._approvals[session_id][approval.id] = approval
        session = self._sessions.get(session_id)
        if session is not None:
            session.updated_at = datetime.utcnow()

    async def list_approvals(self, session_id: str) -> list[ToolApprovalRequest]:
        return sorted(self._approvals.get(session_id, {}).values(), key=lambda item: item.created_at)

    async def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest:
        if session_id not in self._approvals or approval_id not in self._approvals[session_id]:
            raise KeyError(approval_id)
        approval = self._approvals[session_id][approval_id]
        approval.status = status
        approval.decision_note = reason
        approval.resolved_at = datetime.utcnow()
        return approval


class ArangoSessionStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = ArangoRuntimeProvider(settings)
        self._queues: dict[str, asyncio.Queue[ExecutionEvent]] = defaultdict(asyncio.Queue)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._provider.initialize)

    async def save_session(self, session: SessionRecord) -> SessionRecord:
        return await asyncio.to_thread(self._save_session_sync, session)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return await asyncio.to_thread(self._get_session_sync, session_id, True)

    async def list_sessions(self) -> list[SessionRecord]:
        return await asyncio.to_thread(self._list_sessions_sync)

    async def append_event(self, session_id: str, event: ExecutionEvent) -> None:
        await asyncio.to_thread(self._append_event_sync, session_id, event)
        await self._queues[session_id].put(event)

    async def list_events(self, session_id: str) -> list[ExecutionEvent]:
        return await asyncio.to_thread(self._list_events_sync, session_id)

    def get_queue(self, session_id: str) -> asyncio.Queue[ExecutionEvent]:
        return self._queues[session_id]

    async def save_snapshot(self, session_id: str, snapshot: SessionSnapshot) -> None:
        await asyncio.to_thread(self._save_snapshot_sync, session_id, snapshot)

    async def list_snapshots(self, session_id: str) -> list[SessionSnapshot]:
        return await asyncio.to_thread(self._list_snapshots_sync, session_id)

    async def get_latest_snapshot(self, session_id: str) -> SessionSnapshot | None:
        return await asyncio.to_thread(self._get_latest_snapshot_sync, session_id)

    async def save_approval(self, session_id: str, approval: ToolApprovalRequest) -> None:
        await asyncio.to_thread(self._save_approval_sync, session_id, approval)

    async def list_approvals(self, session_id: str) -> list[ToolApprovalRequest]:
        return await asyncio.to_thread(self._list_approvals_sync, session_id)

    async def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest:
        return await asyncio.to_thread(
            self._resolve_approval_sync,
            session_id,
            approval_id,
            status,
            reason,
        )

    def _save_session_sync(self, session: SessionRecord) -> SessionRecord:
        collection = self._provider.collection(self._settings.arango_session_collection)
        existing = collection.get(session.id)
        now = datetime.utcnow()
        event_count = max(int(session.event_count), int((existing or {}).get("event_count") or 0))
        snapshot_count = max(int(session.snapshot_count), int((existing or {}).get("snapshot_count") or 0))
        session.updated_at = now
        document = {
            "_key": session.id,
            "id": session.id,
            "title": session.title,
            "status": session.status.value,
            "session_mode": session.session_mode.value,
            "runtime_mode": session.runtime_mode.value,
            "created_at": serialize_datetime(session.created_at),
            "updated_at": serialize_datetime(now),
            "preferred_model": session.preferred_model,
            "selected_agent": session.selected_agent,
            "metadata": make_json_safe(session.metadata),
            "event_count": event_count,
            "snapshot_count": snapshot_count,
            "created_day_bucket": day_bucket(session.created_at),
            "updated_day_bucket": day_bucket(now),
            "day_bucket_tz": self._settings.arango_timezone,
        }
        if existing:
            collection.replace(document)
        else:
            collection.insert(document)

        message_collection = self._provider.collection(self._settings.arango_message_collection)
        for item in session.messages:
            if not bool(item.metadata.get("persist_transcript", True)):
                continue
            message_document = _message_to_document(self._settings, session.id, item)
            if message_collection.has(item.id):
                message_collection.replace(message_document)
            else:
                message_collection.insert(message_document)

        session.event_count = event_count
        session.snapshot_count = snapshot_count
        return session

    def _get_session_sync(self, session_id: str, include_messages: bool) -> SessionRecord | None:
        row = self._provider.collection(self._settings.arango_session_collection).get(session_id)
        if row is None:
            return None
        messages: list[ChatMessage] = []
        if include_messages:
            rows = self._provider.execute(
                """
                FOR doc IN @@collection
                    FILTER doc.session_id == @session_id
                    SORT doc.created_at ASC
                    RETURN doc
                """,
                {"@collection": self._settings.arango_message_collection, "session_id": session_id},
            )
            messages = [_document_to_message(item) for item in rows]
        return _document_to_session(row, messages)

    def _list_sessions_sync(self) -> list[SessionRecord]:
        rows = self._provider.execute(
            """
            FOR doc IN @@collection
                SORT doc.updated_at DESC
                RETURN doc
            """,
            {"@collection": self._settings.arango_session_collection},
        )
        return [_document_to_session(item, []) for item in rows]

    def _append_event_sync(self, session_id: str, event: ExecutionEvent) -> None:
        event_collection = self._provider.collection(self._settings.arango_event_collection)
        event_collection.insert(
            {
                "_key": str(uuid4()),
                "session_id": session_id,
                "type": event.type,
                "timestamp": serialize_datetime(event.timestamp),
                "payload": make_json_safe(event.payload),
                "day_bucket": day_bucket(event.timestamp),
                "day_bucket_tz": self._settings.arango_timezone,
            }
        )
        session_collection = self._provider.collection(self._settings.arango_session_collection)
        session_document = session_collection.get(session_id)
        if session_document is not None:
            now = datetime.utcnow()
            session_document["event_count"] = int(session_document.get("event_count") or 0) + 1
            session_document["updated_at"] = serialize_datetime(now)
            session_document["updated_day_bucket"] = day_bucket(now)
            session_collection.replace(session_document)

    def _list_events_sync(self, session_id: str) -> list[ExecutionEvent]:
        rows = self._provider.execute(
            """
            FOR doc IN @@collection
                FILTER doc.session_id == @session_id
                SORT doc.timestamp ASC
                RETURN doc
            """,
            {"@collection": self._settings.arango_event_collection, "session_id": session_id},
        )
        return [
            ExecutionEvent(
                type=row["type"],
                session_id=session_id,
                timestamp=ensure_utc_datetime(row["timestamp"]) or datetime.utcnow(),
                payload=dict(row.get("payload") or {}),
            )
            for row in rows
        ]

    def _save_snapshot_sync(self, session_id: str, snapshot: SessionSnapshot) -> None:
        collection = self._provider.collection(self._settings.arango_snapshot_collection)
        document = {
            "_key": snapshot.id,
            "id": snapshot.id,
            "session_id": session_id,
            "version": snapshot.version,
            "stage": snapshot.stage,
            "created_at": serialize_datetime(snapshot.created_at),
            "graph_state": make_json_safe(snapshot.graph_state),
            "day_bucket": day_bucket(snapshot.created_at),
            "day_bucket_tz": self._settings.arango_timezone,
        }
        if collection.has(snapshot.id):
            collection.replace(document)
        else:
            collection.insert(document)

        session_collection = self._provider.collection(self._settings.arango_session_collection)
        session_document = session_collection.get(session_id)
        if session_document is not None:
            now = datetime.utcnow()
            session_document["snapshot_count"] = max(int(session_document.get("snapshot_count") or 0), snapshot.version)
            session_document["updated_at"] = serialize_datetime(now)
            session_document["updated_day_bucket"] = day_bucket(now)
            session_collection.replace(session_document)

    def _list_snapshots_sync(self, session_id: str) -> list[SessionSnapshot]:
        rows = self._provider.execute(
            """
            FOR doc IN @@collection
                FILTER doc.session_id == @session_id
                SORT doc.version ASC
                RETURN doc
            """,
            {"@collection": self._settings.arango_snapshot_collection, "session_id": session_id},
        )
        return [_document_to_snapshot(item) for item in rows]

    def _get_latest_snapshot_sync(self, session_id: str) -> SessionSnapshot | None:
        rows = self._provider.execute(
            """
            FOR doc IN @@collection
                FILTER doc.session_id == @session_id
                SORT doc.version DESC
                LIMIT 1
                RETURN doc
            """,
            {"@collection": self._settings.arango_snapshot_collection, "session_id": session_id},
        )
        return _document_to_snapshot(rows[0]) if rows else None

    def _save_approval_sync(self, session_id: str, approval: ToolApprovalRequest) -> None:
        collection = self._provider.collection(self._settings.arango_approval_collection)
        document = {
            "_key": approval.id,
            "id": approval.id,
            "session_id": session_id,
            "tool_key": approval.tool_key,
            "tool_name": approval.tool_name,
            "reason": approval.reason,
            "status": approval.status.value,
            "created_at": serialize_datetime(approval.created_at),
            "resolved_at": serialize_datetime(approval.resolved_at),
            "decision_note": approval.decision_note,
            "metadata": make_json_safe(approval.metadata),
            "day_bucket": day_bucket(approval.created_at),
            "day_bucket_tz": self._settings.arango_timezone,
        }
        if collection.has(approval.id):
            collection.replace(document)
        else:
            collection.insert(document)

        session_collection = self._provider.collection(self._settings.arango_session_collection)
        session_document = session_collection.get(session_id)
        if session_document is not None:
            now = datetime.utcnow()
            session_document["updated_at"] = serialize_datetime(now)
            session_document["updated_day_bucket"] = day_bucket(now)
            session_collection.replace(session_document)

    def _list_approvals_sync(self, session_id: str) -> list[ToolApprovalRequest]:
        rows = self._provider.execute(
            """
            FOR doc IN @@collection
                FILTER doc.session_id == @session_id
                SORT doc.created_at ASC
                RETURN doc
            """,
            {"@collection": self._settings.arango_approval_collection, "session_id": session_id},
        )
        return [_document_to_approval(item) for item in rows]

    def _resolve_approval_sync(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest:
        collection = self._provider.collection(self._settings.arango_approval_collection)
        document = collection.get(approval_id)
        if document is None or document.get("session_id") != session_id:
            raise KeyError(approval_id)
        resolved_at = datetime.utcnow()
        document["status"] = status.value
        document["decision_note"] = reason
        document["resolved_at"] = serialize_datetime(resolved_at)
        collection.replace(document)

        session_collection = self._provider.collection(self._settings.arango_session_collection)
        session_document = session_collection.get(session_id)
        if session_document is not None:
            session_document["updated_at"] = serialize_datetime(resolved_at)
            session_document["updated_day_bucket"] = day_bucket(resolved_at)
            session_collection.replace(session_document)
        return _document_to_approval(document)


def _message_to_document(settings: Settings, session_id: str, message: ChatMessage) -> dict:
    return {
        "_key": message.id,
        "id": message.id,
        "session_id": session_id,
        "role": message.role.value,
        "content": message.content,
        "created_at": serialize_datetime(message.created_at),
        "metadata": make_json_safe(message.metadata),
        "day_bucket": day_bucket(message.created_at),
        "day_bucket_tz": settings.arango_timezone,
    }


def _document_to_session(row: dict, messages: list[ChatMessage]) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        title=row["title"],
        status=SessionStatus(row["status"]),
        session_mode=SessionMode(row["session_mode"]),
        runtime_mode=RuntimeMode(row["runtime_mode"]),
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
        preferred_model=row.get("preferred_model"),
        selected_agent=row.get("selected_agent"),
        metadata=dict(row.get("metadata") or {}),
        messages=messages,
        event_count=int(row.get("event_count") or 0),
        snapshot_count=int(row.get("snapshot_count") or 0),
    )


def _document_to_message(row: dict) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        role=MessageRole(row["role"]),
        content=row["content"],
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        metadata=dict(row.get("metadata") or {}),
    )


def _document_to_snapshot(row: dict) -> SessionSnapshot:
    return SessionSnapshot(
        id=row["id"],
        session_id=row["session_id"],
        version=int(row["version"]),
        stage=row["stage"],
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        graph_state=dict(row.get("graph_state") or {}),
    )


def _document_to_approval(row: dict) -> ToolApprovalRequest:
    return ToolApprovalRequest(
        id=row["id"],
        session_id=row["session_id"],
        tool_key=row["tool_key"],
        tool_name=row["tool_name"],
        reason=row["reason"],
        status=ToolApprovalStatus(row["status"]),
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        resolved_at=ensure_utc_datetime(row.get("resolved_at")),
        decision_note=row.get("decision_note"),
        metadata=dict(row.get("metadata") or {}),
    )
