from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.runtime.framework_application_projection import FrameworkProjectionWrite
    from src.schemas.runtime_binding import RuntimeExecutionOwner

from src.core.config import Settings
from src.core.request_context import get_request_context
from src.domain.models import SessionImportBundle, SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime, make_json_safe
from src.runtime.store import (
    ContinuationLeaseLostError,
    EventCursorNotFoundError,
    SessionWriteConflictError,
    session_revision_metadata_changed,
    session_snapshot_can_merge_append,
    validate_session_import_bundle,
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
from src.schemas.task_pool import TaskPoolSessionSummary

LOGGER = logging.getLogger(__name__)


class PostgresSessionStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._hot_memory_store = None
        self._compaction_outbox_table: str | None = None
        self._compaction_task_stream: str | None = None

    def set_hot_memory_store(self, store) -> None:
        """Attach the optional Redis staging store after startup initialization."""
        self._hot_memory_store = store

    def set_compaction_task_outbox(self, *, table_name: str, stream: str) -> None:
        """Enable atomic terminal-turn compaction intents in the PG outbox."""
        if not table_name or not stream:
            raise ValueError("Compaction outbox table and stream are required")
        self._compaction_outbox_table = table_name
        self._compaction_task_stream = stream

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def save_session(
        self,
        session: SessionRecord,
        *,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> SessionRecord:
        return await asyncio.to_thread(
            self._save_session_sync,
            session,
            continuation_approval_id,
            continuation_lease_token,
            turn_lease_token,
        )

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
        try:
            inserted = await asyncio.to_thread(
                self._save_session_with_events_sync,
                session,
                events,
                continuation_approval_id,
                continuation_lease_token,
                turn_lease_token,
                framework_projection,
            )
        except Exception as exc:
            if framework_projection is not None:
                LOGGER.warning("framework_application_projection_rejected", extra={"session_id": session.id,
                    "binding_id": framework_projection.owner.binding.binding_id,
                    "task_id": framework_projection.owner.task_id, "error_type": type(exc).__name__})
            raise
        for event in inserted:
            await self._publish_hot_event(session.id, event)
        if framework_projection is not None:
            LOGGER.info("framework_application_projection_committed", extra={"session_id": session.id,
                "binding_id": framework_projection.owner.binding.binding_id,
                "task_id": framework_projection.owner.task_id, "snapshot_id": framework_projection.snapshot_id})
        return session

    async def import_session_bundles(
        self, bundles: list[SessionImportBundle]
    ) -> tuple[int, int]:
        return await asyncio.to_thread(self._import_session_bundles_sync, bundles)

    def create_session_in_transaction(self, cur, session: SessionRecord) -> bool:
        """Insert a new session as part of a caller-owned PostgreSQL transaction."""
        cur.execute(
            f"""
            INSERT INTO {self._settings.database.postgres_session_table} (
                id, title, status, session_mode, runtime_mode, mode_key, project_id,
                created_at, updated_at, preferred_model, selected_agent,
                metadata, event_count, snapshot_count, state_revision
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s::jsonb, %s, %s, 1
            )
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """,
            (
                session.id,
                session.title,
                session.status.value,
                session.session_mode.value,
                session.runtime_mode.value,
                session.mode_key,
                session.project_id,
                session.created_at,
                session.updated_at,
                session.preferred_model,
                session.selected_agent,
                json.dumps(make_json_safe(session.metadata), ensure_ascii=False),
                int(session.event_count),
                int(session.snapshot_count),
            ),
        )
        inserted = cur.fetchone() is not None
        if inserted:
            session.state_revision = 1
        return inserted

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return await asyncio.to_thread(self._get_session_sync, session_id, True)

    async def list_sessions(
        self,
        limit: int | None = None,
        offset: int = 0,
        mode_key: str | None = None,
        cursor_before: datetime | None = None,
        mode_keys: list[str] | None = None,
    ) -> list[SessionRecord]:
        return await asyncio.to_thread(self._list_sessions_sync, limit, offset, mode_key, cursor_before, mode_keys)

    async def list_sessions_with_pending_input_queue(self) -> list[SessionRecord]:
        return await asyncio.to_thread(self._list_sessions_with_pending_input_queue_sync)

    async def list_task_pool_sessions(
        self,
        limit: int = 24,
        offset: int = 0,
    ) -> list[TaskPoolSessionSummary]:
        return await asyncio.to_thread(
            self._list_task_pool_sessions_sync,
            limit,
            offset,
        )

    async def list_history_overviews(self, limit: int = 10) -> list[dict]:
        return await asyncio.to_thread(self._list_history_overviews_sync, limit)

    async def list_recent_questions(
        self,
        session_limit: int = 10,
        question_limit: int = 10,
        include_assistant: bool = False,
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._list_recent_questions_sync,
            session_limit,
            question_limit,
            include_assistant,
        )

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
        try:
            inserted = await asyncio.to_thread(
                self._append_event_sync,
                session_id,
                event,
                continuation_approval_id,
                continuation_lease_token,
                turn_lease_token,
                framework_projection,
                runtime_owner,
            )
        except Exception as exc:
            if runtime_owner is not None:
                LOGGER.warning("framework_live_event_rejected", extra={"session_id": session_id,
                    "binding_id": runtime_owner.binding.binding_id, "event_id": event.id,
                    "event_type": event.type, "error_type": type(exc).__name__})
            if framework_projection is not None:
                LOGGER.warning("framework_event_projection_rejected", extra={"session_id": session_id,
                    "binding_id": framework_projection.owner.binding.binding_id, "event_id": event.id,
                    "event_type": event.type, "error_type": type(exc).__name__})
            raise
        if framework_projection is not None:
            LOGGER.info("framework_event_projection_saved", extra={"session_id": session_id,
                "binding_id": framework_projection.owner.binding.binding_id, "event_id": event.id,
                "event_type": event.type, "reused": not inserted})
        if runtime_owner is not None:
            LOGGER.debug("framework_live_event_saved", extra={"session_id": session_id,
                "binding_id": runtime_owner.binding.binding_id, "event_id": event.id,
                "event_type": event.type, "reused": not inserted})
        if not inserted:
            return
        await self._publish_hot_event(session_id, event)

    async def _publish_hot_event(self, session_id: str, event: ExecutionEvent) -> None:
        if self._hot_memory_store is not None:
            try:
                await self._hot_memory_store.append_event(
                    session_id,
                    event.model_dump(mode="json"),
                )
            except Exception:
                # PostgreSQL has already committed the fact; Redis is only a
                # rebuildable acceleration/compaction staging area.
                LOGGER.exception(
                    "session_hot_memory_write_failed",
                    extra={"session_id": session_id, "event_id": event.id, "event_type": event.type},
                )

    async def list_events(
        self,
        session_id: str,
        limit: int | None = None,
        after_event_id: str | None = None,
    ) -> list[ExecutionEvent]:
        return await asyncio.to_thread(self._list_events_sync, session_id, limit, after_event_id)

    async def read_event_page(self, session_id: str, *, limit: int, after_event_id: str | None = None) -> list[ExecutionEvent]:
        return await asyncio.to_thread(self._list_events_sync, session_id, limit, after_event_id, True)

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
        try:
            actual = await asyncio.to_thread(
                self._save_snapshot_sync,
                session_id,
                snapshot,
                continuation_approval_id,
                continuation_lease_token,
                turn_lease_token,
                framework_projection,
            )
            if framework_projection is not None:
                LOGGER.info("framework_snapshot_projection_saved", extra={
                    "session_id": session_id, "snapshot_id": actual.id, "snapshot_version": actual.version,
                    "binding_id": framework_projection.owner.binding.binding_id,
                    "task_id": framework_projection.owner.task_id,
                })
            return actual
        except Exception:
            if framework_projection is not None:
                LOGGER.warning("framework_snapshot_projection_rejected", extra={
                    "session_id": session_id, "snapshot_id": snapshot.id,
                    "binding_id": framework_projection.owner.binding.binding_id,
                    "task_id": framework_projection.owner.task_id,
                })
            raise

    async def list_snapshots(
        self,
        session_id: str,
        limit: int | None = None,
        include_graph_state: bool = True,
    ) -> list[SessionSnapshot]:
        return await asyncio.to_thread(
            self._list_snapshots_sync,
            session_id,
            limit,
            include_graph_state,
        )

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

    async def claim_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._claim_approval_continuation_sync,
            session_id,
            approval_id,
            owner_id,
            lease_token,
            lease_seconds,
        )

    async def claim_turn_execution(
        self,
        session_id: str,
        turn_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int,
        approval_id: str | None = None,
    ) -> SessionRecord | None:
        return await asyncio.to_thread(
            self._claim_turn_execution_sync,
            session_id,
            turn_id,
            owner_id,
            lease_token,
            lease_seconds,
            approval_id,
        )

    async def renew_turn_execution(
        self,
        session_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._renew_turn_execution_sync,
            session_id,
            lease_token,
            lease_seconds,
        )

    async def complete_turn_execution(
        self,
        session_id: str,
        lease_token: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._complete_turn_execution_sync,
            session_id,
            lease_token,
        )

    async def claim_coordinator_dispatch(
        self,
        session_id: str,
        turn_id: str,
        dispatch_key: str,
        owner_id: str,
        request_fingerprint: str | None = None,
        worker_task_ids: list[str] | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._claim_coordinator_dispatch_sync,
            session_id,
            turn_id,
            dispatch_key,
            owner_id,
            request_fingerprint,
            worker_task_ids,
        )

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
        return await asyncio.to_thread(
            self._release_coordinator_dispatch_claim_if_no_child_sync,
            session_id,
            turn_id,
            dispatch_key,
            owner_id,
            request_fingerprint,
            worker_task_ids,
            require_settled_parent,
        )

    async def mutate_metadata(self, session_id: str, mutate) -> bool:
        """Apply ``mutate(metadata)`` under a row lock so concurrent writers
        cannot overwrite each other's whole-document metadata."""
        return await asyncio.to_thread(self._mutate_metadata_sync, session_id, mutate)

    async def update_status_and_mutate_metadata(
        self,
        session_id: str,
        *,
        status: SessionStatus | None,
        mutate,
        allowed_current_statuses: set[SessionStatus] | None = None,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._update_status_and_mutate_metadata_sync,
            session_id,
            status,
            mutate,
            allowed_current_statuses,
            continuation_approval_id,
            continuation_lease_token,
            turn_lease_token,
        )

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
        found, inserted_events = await asyncio.to_thread(
            self._update_status_and_append_messages_and_events_sync,
            session_id,
            status,
            messages,
            events,
        )
        for event in inserted_events:
            await self._publish_hot_event(session_id, event)
        return found

    def _update_status_and_append_messages_and_events_sync(
        self,
        session_id: str,
        status: SessionStatus | None,
        messages: list[ChatMessage],
        events: list[ExecutionEvent],
    ) -> tuple[bool, list[ExecutionEvent]]:
        inserted_events: list[ExecutionEvent] = []
        now = datetime.utcnow()
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT status FROM {self._settings.database.postgres_session_table} "
                    "WHERE id = %s FOR UPDATE",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return False, inserted_events
                current_status = SessionStatus(row["status"])
                status_changed = status is not None and status != current_status
                transcript_changed = False
                for message in messages:
                    if not bool(message.metadata.get("persist_transcript", True)):
                        continue
                    cur.execute(
                        f"""
                        INSERT INTO {self._settings.database.postgres_message_table} (
                            id, session_id, role, content, created_at, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            message.id,
                            session_id,
                            message.role.value,
                            message.content,
                            message.created_at,
                            json.dumps(make_json_safe(message.metadata), ensure_ascii=False),
                        ),
                    )
                    transcript_changed = transcript_changed or cur.rowcount > 0
                for event in events:
                    if self.append_event_in_transaction(cur, session_id, event):
                        inserted_events.append(event)
                cur.execute(
                    f"""UPDATE {self._settings.database.postgres_session_table}
                    SET status = %s, updated_at = %s,
                        state_revision = state_revision + %s
                    WHERE id = %s""",
                    (
                        (status or current_status).value,
                        now,
                        int(status_changed or transcript_changed),
                        session_id,
                    ),
                )
        return True, inserted_events

    def _update_status_and_mutate_metadata_sync(
        self,
        session_id: str,
        status: SessionStatus | None,
        mutate,
        allowed_current_statuses: set[SessionStatus] | None = None,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT status, metadata FROM {self._settings.database.postgres_session_table} "
                    "WHERE id = %s FOR UPDATE",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                self._assert_continuation_lease_sync(
                    cur, session_id, continuation_approval_id, continuation_lease_token
                )
                self._assert_turn_lease_sync(cur, session_id, turn_lease_token)
                stored = row["metadata"]
                metadata = dict(json.loads(stored) if isinstance(stored, str) else stored or {})
                original_metadata = deepcopy(metadata)
                current_status = SessionStatus(row["status"])
                mutate(metadata, current_status)
                next_status = status if current_status in (allowed_current_statuses or set()) and status is not None else current_status
                changed = (
                    session_revision_metadata_changed(original_metadata, metadata)
                    or next_status != current_status
                )
                cur.execute(
                    f"UPDATE {self._settings.database.postgres_session_table} "
                    "SET status = %s, metadata = %s::jsonb, updated_at = now(), "
                    "state_revision = state_revision + %s WHERE id = %s",
                    (next_status.value, json.dumps(make_json_safe(metadata), ensure_ascii=False),
                     int(changed), session_id),
                )
                return True

    async def update_metadata_and_append_event(
        self,
        session_id: str,
        update: Callable[[dict], bool],
        event: ExecutionEvent,
    ) -> bool:
        applied = await asyncio.to_thread(
            self._update_metadata_and_append_event_sync, session_id, update, event
        )
        if applied:
            await self._publish_hot_event(session_id, event)
        return applied

    def _update_metadata_and_append_event_sync(
        self,
        session_id: str,
        update: Callable[[dict], bool],
        event: ExecutionEvent,
    ) -> bool:
        if event.session_id != session_id:
            raise ValueError("Projection event session_id does not match the session.")
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT metadata FROM {self._settings.database.postgres_session_table} "
                    "WHERE id = %s FOR UPDATE",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                stored = row["metadata"]
                metadata = dict(json.loads(stored) if isinstance(stored, str) else stored or {})
                original_metadata = deepcopy(metadata)
                if not update(metadata):
                    return False
                changed = session_revision_metadata_changed(original_metadata, metadata)
                cur.execute(
                    f"UPDATE {self._settings.database.postgres_session_table} "
                    "SET metadata = %s::jsonb, updated_at = now(), "
                    "state_revision = state_revision + %s WHERE id = %s",
                    (json.dumps(make_json_safe(metadata), ensure_ascii=False), int(changed), session_id),
                )
                if not self.append_event_in_transaction(cur, session_id, event):
                    raise ValueError("Projection event ID already exists.")
                return True

    def _mutate_metadata_sync(self, session_id: str, mutate) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT metadata FROM {self._settings.database.postgres_session_table} "
                    "WHERE id = %s FOR UPDATE",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                stored = row["metadata"]
                metadata = dict(json.loads(stored) if isinstance(stored, str) else stored or {})
                original_metadata = deepcopy(metadata)
                mutate(metadata)
                changed = session_revision_metadata_changed(original_metadata, metadata)
                cur.execute(
                    f"UPDATE {self._settings.database.postgres_session_table} "
                    "SET metadata = %s::jsonb, updated_at = now(), "
                    "state_revision = state_revision + %s WHERE id = %s",
                    (json.dumps(make_json_safe(metadata), ensure_ascii=False), int(changed), session_id),
                )
                return True

    async def recover_expired_turn_executions(self) -> list[str]:
        return await asyncio.to_thread(self._recover_expired_turn_executions_sync)

    async def assert_turn_execution(self, session_id: str, lease_token: str) -> None:
        await asyncio.to_thread(self._assert_turn_execution_sync, session_id, lease_token)

    async def renew_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._renew_approval_continuation_sync,
            session_id,
            approval_id,
            lease_token,
            lease_seconds,
        )

    async def complete_approval_continuation(
        self,
        session_id: str,
        approval_id: str,
        lease_token: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._complete_approval_continuation_sync,
            session_id,
            approval_id,
            lease_token,
        )

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_session_table} (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL,
                        session_mode TEXT NOT NULL,
                        runtime_mode TEXT NOT NULL,
                        mode_key TEXT NOT NULL DEFAULT 'default',
                        project_id UUID NULL REFERENCES {self._settings.database.postgres_project_table}(id),
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        preferred_model TEXT NULL,
                        selected_agent TEXT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        event_count INTEGER NOT NULL DEFAULT 0,
                        snapshot_count INTEGER NOT NULL DEFAULT 0,
                        state_revision BIGINT NOT NULL DEFAULT 0
                    )
                    """
                )
                cur.execute(
                    f"ALTER TABLE {self._settings.database.postgres_session_table} "
                    f"ADD COLUMN IF NOT EXISTS project_id UUID NULL REFERENCES {self._settings.database.postgres_project_table}(id)"
                )
                cur.execute(
                    f"ALTER TABLE {self._settings.database.postgres_session_table} "
                    "ADD COLUMN IF NOT EXISTS state_revision BIGINT NOT NULL DEFAULT 0"
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_message_table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_event_table} (
                        id TEXT PRIMARY KEY,
                        event_sequence BIGSERIAL NOT NULL,
                        session_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_snapshot_table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        stage TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        graph_state JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        UNIQUE(session_id, version)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_approval_table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        tool_key TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        resolved_at TIMESTAMPTZ NULL,
                        decision_note TEXT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_updated "
                    f"ON {self._settings.database.postgres_session_table} (updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_project_updated "
                    f"ON {self._settings.database.postgres_session_table} (project_id, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_message_table}_session_created "
                    f"ON {self._settings.database.postgres_message_table} (session_id, created_at ASC)"
                )
                cur.execute(
                    f"ALTER TABLE {self._settings.database.postgres_event_table} "
                    "ADD COLUMN IF NOT EXISTS event_sequence BIGSERIAL NOT NULL"
                )
                cur.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_event_table}_session_sequence "
                    f"ON {self._settings.database.postgres_event_table} (session_id, event_sequence)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_event_table}_session_timestamp "
                    f"ON {self._settings.database.postgres_event_table} (session_id, timestamp ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_event_table}_session_timestamp_id "
                    f"ON {self._settings.database.postgres_event_table} (session_id, timestamp ASC, id ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_snapshot_table}_session_version "
                    f"ON {self._settings.database.postgres_snapshot_table} (session_id, version DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_approval_table}_session_created "
                    f"ON {self._settings.database.postgres_approval_table} (session_id, created_at ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_mode_updated "
                    f"ON {self._settings.database.postgres_session_table} (mode_key, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_status "
                    f"ON {self._settings.database.postgres_session_table} (status)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_session_table}_created "
                    f"ON {self._settings.database.postgres_session_table} (created_at DESC)"
                )

    def _save_session_sync(
        self,
        session: SessionRecord,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> SessionRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._save_session_in_transaction(
                    cur, session, continuation_approval_id,
                    continuation_lease_token, turn_lease_token,
                )
        return session

    def _import_session_bundles_sync(
        self, bundles: list[SessionImportBundle]
    ) -> tuple[int, int]:
        """Restore session rows and all children in one PostgreSQL transaction."""
        if not bundles:
            return 0, 0

        seen_sessions: set[str] = set()
        child_ids: dict[str, dict[str, str]] = {
            "message": {},
            "event": {},
            "snapshot": {},
            "approval": {},
        }
        for bundle in bundles:
            session_id = bundle.session.id
            if session_id in seen_sessions:
                continue
            validate_session_import_bundle(bundle)
            for label, records in (
                ("message", bundle.session.messages),
                ("event", bundle.events),
                ("snapshot", bundle.snapshots),
                ("approval", bundle.approvals),
            ):
                for record in records:
                    existing_owner = child_ids[label].get(record.id)
                    if existing_owner is not None and existing_owner != session_id:
                        raise ValueError(f"Imported backup contains duplicate {label} IDs.")
                    child_ids[label][record.id] = session_id
            seen_sessions.add(session_id)

        imported = 0
        skipped = 0
        message_table = self._settings.database.postgres_message_table
        event_table = self._settings.database.postgres_event_table
        snapshot_table = self._settings.database.postgres_snapshot_table
        approval_table = self._settings.database.postgres_approval_table

        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            for bundle in bundles:
                session = bundle.session
                session.event_count = max(session.event_count, len(bundle.events))
                session.snapshot_count = max(
                    session.snapshot_count,
                    max((snapshot.version for snapshot in bundle.snapshots), default=0),
                )
                if not self.create_session_in_transaction(cur, session):
                    skipped += 1
                    continue

                for message in session.messages:
                    cur.execute(
                        f"""
                        INSERT INTO {message_table} (
                            id, session_id, role, content, created_at, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            message.id,
                            session.id,
                            message.role.value,
                            message.content,
                            message.created_at,
                            json.dumps(make_json_safe(message.metadata), ensure_ascii=False),
                        ),
                    )

                for event in bundle.events:
                    event_payload = dict(event.payload)
                    context_fields = {
                        name: getattr(event, name)
                        for name in (
                            "request_id",
                            "trace_id",
                            "turn_id",
                            "run_id",
                            "run_item_id",
                            "attempt_id",
                            "worker_id",
                            "resource_id",
                        )
                        if getattr(event, name) is not None
                    }
                    if context_fields:
                        event_payload["_harness_context"] = context_fields
                    cur.execute(
                        f"""
                        INSERT INTO {event_table} (id, session_id, type, timestamp, payload)
                        VALUES (%s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            event.id,
                            session.id,
                            event.type,
                            event.timestamp,
                            json.dumps(make_json_safe(event_payload), ensure_ascii=False),
                        ),
                    )

                for snapshot in bundle.snapshots:
                    cur.execute(
                        f"""
                        INSERT INTO {snapshot_table} (
                            id, session_id, version, stage, created_at, graph_state
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            snapshot.id,
                            session.id,
                            snapshot.version,
                            snapshot.stage,
                            snapshot.created_at,
                            json.dumps(make_json_safe(snapshot.graph_state), ensure_ascii=False),
                        ),
                    )

                for approval in bundle.approvals:
                    cur.execute(
                        f"""
                        INSERT INTO {approval_table} (
                            id, session_id, tool_key, tool_name, reason, status,
                            created_at, resolved_at, decision_note, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            approval.id,
                            session.id,
                            approval.tool_key,
                            approval.tool_name,
                            approval.reason,
                            approval.status.value,
                            approval.created_at,
                            approval.resolved_at,
                            approval.decision_note,
                            json.dumps(make_json_safe(approval.metadata), ensure_ascii=False),
                        ),
                    )
                imported += 1

        return imported, skipped

    def _save_session_with_events_sync(
        self,
        session: SessionRecord,
        events: list[ExecutionEvent],
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
    ) -> list[ExecutionEvent]:
        candidate = deepcopy(session)
        inserted: list[ExecutionEvent] = []
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                immutable_message_ids = frozenset()
                if framework_projection is not None:
                    from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, assert_runtime_owner

                    configure_runtime_owner_transaction(cur, self._settings)
                    assert_runtime_owner(cur, self._settings, framework_projection.owner)
                    if framework_projection.runtime_result_asset_sha256 is not None:
                        from src.runtime.framework_finalization_projection import prepare_finalization_projection

                        immutable_message_ids = prepare_finalization_projection(
                            cur, self._settings, framework_projection, candidate)
                self._save_session_in_transaction(
                    cur, candidate, continuation_approval_id,
                    continuation_lease_token, turn_lease_token,
                    immutable_message_ids=immutable_message_ids,
                )
                for event in events:
                    if event.session_id != session.id:
                        raise ValueError("Final event session_id does not match the session being saved.")
                    if self.append_event_in_transaction(
                        cur, session.id, event, continuation_approval_id,
                        continuation_lease_token, turn_lease_token,
                        framework_projection=(framework_projection if framework_projection is not None
                            and framework_projection.runtime_result_asset_sha256 is not None else None),
                    ):
                        inserted.append(event)
                if framework_projection is not None:
                    from src.runtime.framework_application_projection import record_application_commit

                    record_application_commit(cur, self._settings, candidate, events, framework_projection)
        session.event_count = candidate.event_count + len(inserted)
        session.snapshot_count = candidate.snapshot_count
        session.metadata = candidate.metadata
        session.state_revision = candidate.state_revision
        session.updated_at = candidate.updated_at
        return inserted

    def _save_session_in_transaction(
        self,
        cur,
        session: SessionRecord,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        *,
        immutable_message_ids: frozenset[str] | set[str] = frozenset(),
    ) -> SessionRecord:
        now = datetime.utcnow()
        session.updated_at = now
        self._assert_continuation_lease_sync(
            cur, session.id, continuation_approval_id, continuation_lease_token
        )
        self._assert_turn_lease_sync(cur, session.id, turn_lease_token)
        expected_revision = int(session.state_revision)
        table = self._settings.database.postgres_session_table
        cur.execute(
            f"SELECT state_revision FROM {table} WHERE id = %s FOR UPDATE",
            (session.id,),
        )
        current = cur.fetchone()
        if current is None:
            if expected_revision != 0:
                LOGGER.warning(
                    "session_state_write_conflict",
                    extra={"session_id": session.id, "expected_revision": expected_revision,
                           "actual_revision": None},
                )
                raise SessionWriteConflictError(
                    f"Session {session.id} no longer exists at revision {expected_revision}."
                )
            cur.execute(
                f"""
                INSERT INTO {table} (
                    id, title, status, session_mode, runtime_mode, mode_key, project_id,
                    created_at, updated_at, preferred_model, selected_agent,
                    metadata, event_count, snapshot_count, state_revision
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s::jsonb, %s, %s, 1
                )
                ON CONFLICT (id) DO NOTHING
                RETURNING event_count, snapshot_count, metadata, state_revision
                """,
                (
                    session.id, session.title, session.status.value,
                    session.session_mode.value, session.runtime_mode.value,
                    session.mode_key, session.project_id, session.created_at, now,
                    session.preferred_model, session.selected_agent,
                    json.dumps(make_json_safe(session.metadata), ensure_ascii=False),
                    int(session.event_count), int(session.snapshot_count),
                ),
            )
            counts = cur.fetchone()
            if counts is None:
                cur.execute(f"SELECT state_revision FROM {table} WHERE id = %s", (session.id,))
                raced = cur.fetchone()
                LOGGER.warning(
                    "session_state_write_conflict",
                    extra={"session_id": session.id, "expected_revision": expected_revision,
                           "actual_revision": int(raced["state_revision"]) if raced else None},
                )
                raise SessionWriteConflictError(
                    f"Session {session.id} was created concurrently; reload it before saving."
                )
        else:
            if turn_lease_token:
                # Recheck after acquiring the row lock so an expired or
                # reclaimed owner cannot commit its detached snapshot.
                self._assert_turn_lease_sync(cur, session.id, turn_lease_token)
            actual_revision = int(current["state_revision"])
            write_revision = expected_revision
            if actual_revision != expected_revision:
                cur.execute(f"SELECT * FROM {table} WHERE id = %s", (session.id,))
                current_row = cur.fetchone()
                cur.execute(
                    f"SELECT * FROM {self._settings.database.postgres_message_table} "
                    "WHERE session_id = %s ORDER BY created_at ASC",
                    (session.id,),
                )
                current_messages = [
                    _message_from_row(row) for row in (cur.fetchall() or [])
                ]
                current_snapshot = (
                    _session_from_row(current_row, current_messages)
                    if current_row is not None
                    else None
                )
                if current_snapshot is None or not session_snapshot_can_merge_append(
                    session, current_snapshot
                ):
                    LOGGER.warning(
                        "session_state_write_conflict",
                        extra={"session_id": session.id, "expected_revision": expected_revision,
                               "actual_revision": actual_revision},
                    )
                    raise SessionWriteConflictError(
                        f"Session {session.id} changed from revision {expected_revision} "
                        f"to {actual_revision}; reload it and apply the intended update to current state."
                    )
                write_revision = actual_revision
                LOGGER.info(
                    "session_stale_append_snapshot_merged",
                    extra={"session_id": session.id, "expected_revision": expected_revision,
                           "actual_revision": actual_revision},
                )
            # The row lock above protects SQL-owned lease/dispatch fields while
            # the caller's detached snapshot replaces the rest of the metadata.
            cur.execute(
                f"""
                UPDATE {table}
                SET title = %s,
                    status = %s,
                    session_mode = %s,
                    runtime_mode = %s,
                    mode_key = %s,
                    project_id = %s,
                    updated_at = %s,
                    preferred_model = %s,
                    selected_agent = %s,
                    metadata = (
                        %s::jsonb
                        - 'turn_lease_turn_id'
                        - 'turn_lease_owner'
                        - 'turn_lease_token'
                        - 'turn_lease_expires_at'
                        - 'coordinator_dispatch_claims'
                    ) || jsonb_strip_nulls(jsonb_build_object(
                        'turn_lease_turn_id', metadata->'turn_lease_turn_id',
                        'turn_lease_owner', metadata->'turn_lease_owner',
                        'turn_lease_token', metadata->'turn_lease_token',
                        'turn_lease_expires_at', metadata->'turn_lease_expires_at',
                        'worker_dispatches', metadata->'worker_dispatches',
                        'coordinator_dispatch_claims', metadata->'coordinator_dispatch_claims'
                    )),
                    event_count = GREATEST(event_count, %s),
                    snapshot_count = GREATEST(snapshot_count, %s),
                    state_revision = state_revision + 1
                WHERE id = %s AND state_revision = %s
                RETURNING event_count, snapshot_count, metadata, state_revision
                """,
                (
                    session.title, session.status.value, session.session_mode.value,
                    session.runtime_mode.value, session.mode_key, session.project_id,
                    now, session.preferred_model, session.selected_agent,
                    json.dumps(make_json_safe(session.metadata), ensure_ascii=False),
                    int(session.event_count), int(session.snapshot_count),
                    session.id, write_revision,
                ),
            )
            counts = cur.fetchone()
            if counts is None:
                # The prior SELECT FOR UPDATE makes this a defensive check for
                # schema triggers or an unexpected concurrent writer.
                raise SessionWriteConflictError(
                    f"Session {session.id} changed while revision {expected_revision} was being saved."
                )
        session.event_count = int(counts.get("event_count") or 0)
        session.snapshot_count = int(counts.get("snapshot_count") or 0)
        session.state_revision = int(counts.get("state_revision") or 0)
        if counts.get("metadata") is not None:
            session.metadata = counts["metadata"]
        for item in session.messages:
            if not bool(item.metadata.get("persist_transcript", True)):
                continue
            # Original framework rows are insert-or-reuse, not editable drafts.
            # The caller validates their actual rows again before the A1 proof.
            conflict_clause = "ON CONFLICT (id) DO NOTHING" if item.id in immutable_message_ids else """
                ON CONFLICT (id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    role = EXCLUDED.role,
                    content = EXCLUDED.content,
                    created_at = EXCLUDED.created_at,
                    metadata = EXCLUDED.metadata
            """
            cur.execute(
                f"""
                INSERT INTO {self._settings.database.postgres_message_table} (
                    id, session_id, role, content, created_at, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                {conflict_clause}
                """,
                (
                    item.id,
                    session.id,
                    item.role.value,
                    item.content,
                    item.created_at,
                    json.dumps(make_json_safe(item.metadata), ensure_ascii=False),
                ),
            )
        return session

    def _get_session_sync(self, session_id: str, include_messages: bool) -> SessionRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._settings.database.postgres_session_table} WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                messages: list[ChatMessage] = []
                if include_messages:
                    cur.execute(
                        f"""
                        SELECT * FROM {self._settings.database.postgres_message_table}
                        WHERE session_id = %s
                        ORDER BY created_at ASC
                        """,
                        (session_id,),
                    )
                    messages = [_message_from_row(item) for item in (cur.fetchall() or [])]
        return _session_from_row(row, messages)

    def _list_sessions_sync(
        self,
        limit: int | None = None,
        offset: int = 0,
        mode_key: str | None = None,
        cursor_before: datetime | None = None,
        mode_keys: list[str] | None = None,
    ) -> list[SessionRecord]:
        conditions: list[str] = []
        params: list[object] = []
        if mode_key:
            conditions.append("mode_key = %s")
            params.append(mode_key)
        if mode_keys is not None:
            conditions.append("mode_key = ANY(%s)")
            params.append(mode_keys)
        if cursor_before is not None:
            conditions.append("updated_at < %s")
            params.append(cursor_before)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM {self._settings.database.postgres_session_table} {where} ORDER BY updated_at DESC"
        if limit is not None:
            query += " LIMIT %s"
            params.append(max(int(limit or 0), 0))
            if cursor_before is None and offset:
                query += " OFFSET %s"
                params.append(max(int(offset or 0), 0))
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if params:
                    cur.execute(query, params)
                else:
                    cur.execute(query)
                rows = cur.fetchall() or []
        return [_session_from_row(item, []) for item in rows]

    def _list_sessions_with_pending_input_queue_sync(self) -> list[SessionRecord]:
        table = self._settings.database.postgres_session_table
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT * FROM {table}
                WHERE CASE
                    WHEN jsonb_typeof(metadata->'pending_input_queue') = 'array'
                    THEN jsonb_array_length(metadata->'pending_input_queue') > 0
                    ELSE FALSE
                END
                ORDER BY updated_at DESC
                """
            )
            rows = cur.fetchall() or []
        return [_session_from_row(item, []) for item in rows]

    def _list_task_pool_sessions_sync(
        self,
        limit: int = 24,
        offset: int = 0,
    ) -> list[TaskPoolSessionSummary]:
        normalized_limit = max(int(limit or 0), 0)
        normalized_offset = max(int(offset or 0), 0)
        if normalized_limit <= 0:
            return []
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        id,
                        title,
                        status,
                        session_mode,
                        runtime_mode,
                        mode_key,
                        project_id,
                        created_at,
                        updated_at,
                        selected_agent,
                        COALESCE(metadata->'worker_dispatches', '[]'::jsonb)
                            AS worker_dispatches,
                        COALESCE(metadata->>'parent_session_id', '')
                            AS parent_session_id
                    FROM {self._settings.database.postgres_session_table}
                    WHERE mode_key = 'code_review'
                       OR session_mode = 'background_task'
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (normalized_limit, normalized_offset),
                )
                rows = cur.fetchall() or []
        return [_task_pool_session_from_row(item) for item in rows]

    def _list_history_overviews_sync(self, limit: int = 10) -> list[dict]:
        normalized_limit = max(1, min(int(limit), 50))
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT s.id, s.title, s.status, s.created_at, s.updated_at,
                           s.event_count, s.snapshot_count, s.selected_agent,
                           s.preferred_model,
                           (
                               SELECT COUNT(*)
                               FROM {self._settings.database.postgres_message_table} m
                               WHERE m.session_id = s.id
                           ) AS message_count
                    FROM {self._settings.database.postgres_session_table} s
                    ORDER BY s.updated_at DESC
                    LIMIT %s
                    """,
                    (normalized_limit,),
                )
                rows = list(cur.fetchall() or [])
        return [_history_overview_from_row(row) for row in rows]

    def _list_recent_questions_sync(
        self,
        session_limit: int = 10,
        question_limit: int = 10,
        include_assistant: bool = False,
    ) -> list[dict]:
        roles = ["user", "assistant"] if include_assistant else ["user"]
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH recent_sessions AS (
                        SELECT id
                        FROM {self._settings.database.postgres_session_table}
                        ORDER BY updated_at DESC
                        LIMIT %s
                    )
                    SELECT m.session_id, m.role, m.content, m.created_at
                    FROM recent_sessions rs
                    JOIN {self._settings.database.postgres_message_table} m
                      ON m.session_id = rs.id
                    WHERE m.role = ANY(%s)
                      AND BTRIM(m.content) <> ''
                    ORDER BY m.created_at DESC
                    LIMIT %s
                    """,
                    (
                        max(1, min(int(session_limit), 50)),
                        roles,
                        max(1, min(int(question_limit), 50)),
                    ),
                )
                rows = list(cur.fetchall() or [])
        return [
            {
                "session_id": row["session_id"],
                "role": row["role"],
                "content": str(row.get("content") or "").strip(),
                "created_at": (
                    ensure_utc_datetime(row.get("created_at")) or datetime.utcnow()
                ).isoformat(),
            }
            for row in rows
        ]

    async def queue_idle_compaction_tasks(self, *, idle_seconds: int, limit: int = 50) -> int:
        return await asyncio.to_thread(self._queue_idle_compaction_tasks_sync, idle_seconds, limit)

    def _queue_idle_compaction_tasks_sync(self, idle_seconds: int, limit: int) -> int:
        """Enqueue compaction for Sessions that went quiet while still holding a backlog.

        Switching away from a Session is only a frontend navigation, so idleness plus an
        unfilled watermark is the durable signal that nobody is still writing it. The
        outbox key matches the turn-completion trigger, so the two can never double-queue.
        """
        outbox_table = getattr(self, "_compaction_outbox_table", None)
        stream = getattr(self, "_compaction_task_stream", None)
        if not outbox_table or not stream:
            return 0
        bounded_limit = max(1, min(int(limit), 500))
        queued = 0
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH latest AS (
                        SELECT DISTINCT ON (session_id) session_id, id, event_sequence, timestamp
                        FROM {self._settings.database.postgres_event_table}
                        ORDER BY session_id, event_sequence DESC
                    ), watermark AS (
                        SELECT session_id, MAX(last_compacted_event_sequence) AS watermark
                        FROM {self._settings.database.postgres_compaction_table}
                        WHERE status = 'completed'
                        GROUP BY session_id
                    )
                    SELECT latest.session_id, latest.id, latest.event_sequence
                    FROM latest
                    LEFT JOIN watermark ON watermark.session_id = latest.session_id
                    WHERE latest.timestamp < now() - (%s || ' seconds')::interval
                      AND latest.event_sequence > COALESCE(watermark.watermark, 0)
                    ORDER BY latest.timestamp
                    LIMIT %s
                    """,
                    (max(1, int(idle_seconds)), bounded_limit),
                )
                candidates = list(cur.fetchall() or [])
                for row in candidates:
                    session_id = str(row["session_id"])
                    event_id = str(row["id"])
                    task_payload = {
                        "schema_version": 1,
                        "task_id": f"task_compaction_{session_id}_{event_id}",
                        "task_type": "compaction_task",
                        "request_id": f"req_{event_id}",
                        "trace_id": f"trace_{event_id}",
                        "session_id": session_id,
                        "created_at": datetime.utcnow().isoformat(),
                        "payload": {
                            "source_event_id": event_id,
                            "source_event_type": "idle_sweep",
                        },
                    }
                    cur.execute(
                        f"""
                        INSERT INTO {outbox_table} (event_key, stream, payload)
                        VALUES (%s, %s, %s::jsonb)
                        ON CONFLICT (event_key) DO NOTHING
                        """,
                        (
                            f"compaction:{session_id}:{event_id}",
                            stream,
                            json.dumps(task_payload, ensure_ascii=False, separators=(",", ":")),
                        ),
                    )
                    queued += max(0, int(cur.rowcount or 0))
        return queued

    async def complete_runtime_events(self, session_id, event_log, *, runtime_owner, native_terminal,
                                      returned_event_offset=0, **fence):
        """Declare the acknowledged graph collection in the existing event transaction."""
        from src.runtime.framework_event_completion import completion_event
        from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction

        if runtime_owner.binding.session_id != session_id:
            raise ValueError("Runtime event completion belongs to another Session")
        # Own JSON originals before crossing to_thread, not mutable Runtime state.
        event_log = json.loads(json.dumps(event_log, ensure_ascii=False, allow_nan=False))
        native_terminal = json.loads(json.dumps(native_terminal, ensure_ascii=False, allow_nan=False))

        def write():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                configure_runtime_owner_transaction(cur, self._settings)
                assert_runtime_owner(cur, self._settings, runtime_owner)
                event = completion_event(cur, self._settings, runtime_owner, event_log,
                    native_terminal, returned_event_offset)
                self.append_event_in_transaction(cur, session_id, event, runtime_owner=runtime_owner, **fence)
                return event

        return await asyncio.to_thread(write)

    async def read_runtime_event_completion(self, binding, native_terminal):
        """Verify original material without granting execution or recovery authority."""
        from src.runtime.framework_event_completion import read_completion
        from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction

        native_terminal = json.loads(json.dumps(native_terminal, ensure_ascii=False, allow_nan=False))
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                configure_runtime_owner_transaction(cur, self._settings)
                # Follow Session-first lock order shared by all event writers.
                # A reader never acquires an execution owner or changes a lease.
                cur.execute(f"SELECT id FROM {self._settings.database.postgres_session_table} WHERE id=%s FOR SHARE",
                    (binding.session_id,))
                return read_completion(cur, self._settings, binding, native_terminal)

        return await asyncio.to_thread(read)

    def _append_event_sync(
        self,
        session_id: str,
        event: ExecutionEvent,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
        runtime_owner: RuntimeExecutionOwner | None = None,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                return self.append_event_in_transaction(
                    cur, session_id, event, continuation_approval_id,
                    continuation_lease_token, turn_lease_token,
                    framework_projection,
                    runtime_owner,
                )

    def append_event_in_transaction(
        self, cur, session_id: str, event: ExecutionEvent,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
        runtime_owner: RuntimeExecutionOwner | None = None,
    ) -> bool:
        """Append an event and derived outbox facts in the caller's transaction."""
        if runtime_owner is not None:
            from src.runtime.postgres_runtime_owner import assert_runtime_owner, configure_runtime_owner_transaction

            if framework_projection is not None:
                raise ValueError("Live event authority and result projection must use separate writes")
            if (runtime_owner.binding.session_id != session_id or event.session_id != session_id
                or event.payload.get("turn_id") != runtime_owner.binding.turn_id
                or event.turn_id not in (None, runtime_owner.binding.turn_id)):
                raise ValueError("Live event differs from its runtime owner scope")
            configure_runtime_owner_transaction(cur, self._settings)
            assert_runtime_owner(cur, self._settings, runtime_owner)
        self._assert_continuation_lease_sync(
            cur, session_id, continuation_approval_id, continuation_lease_token
        )
        self._assert_turn_lease_sync(cur, session_id, turn_lease_token)
        projection_stage = None
        if framework_projection is not None:
            from src.runtime.framework_application_projection import prepare_event_projection

            if framework_projection.owner.binding.session_id != session_id:
                raise ValueError("Event projection belongs to another Session")
            projection_stage = prepare_event_projection(cur, self._settings, framework_projection, event)
        # Serialize each session BEFORE allocating its sequence value.
        # A bare sequence does not guarantee transaction commit order.
        cur.execute(
            f"SELECT id FROM {self._settings.database.postgres_session_table} WHERE id = %s FOR UPDATE",
            (session_id,),
        )
        if cur.fetchone() is None:
            raise KeyError(session_id)
        event_payload = dict(event.payload)
        context_fields = {
            name: getattr(event, name)
            for name in (
                "request_id",
                "trace_id",
                "turn_id",
                "run_id",
                "run_item_id",
                "attempt_id",
                "worker_id",
                "resource_id",
            )
            if getattr(event, name) is not None
        }
        request_context = get_request_context()
        if request_context is not None:
            for name in (
                "request_id",
                "trace_id",
                "turn_id",
                "run_id",
                "run_item_id",
                "attempt_id",
                "worker_id",
                "resource_id",
            ):
                value = getattr(request_context, name, None)
                if value is not None:
                    context_fields.setdefault(name, value)
        if context_fields:
            event_payload["_harness_context"] = context_fields
        cur.execute(
            f"""
            INSERT INTO {self._settings.database.postgres_event_table} (
                id, session_id, type, timestamp, payload
            ) VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (id) DO NOTHING RETURNING id
            """,
            (
                event.id,
                session_id,
                event.type,
                event.timestamp,
                json.dumps(make_json_safe(event_payload), ensure_ascii=False),
            ),
        )
        inserted = cur.fetchone() is not None
        if runtime_owner is not None:
            from src.runtime.framework_application_projection import _event_row, _require_event_intent

            _require_event_intent(_event_row(cur, self._settings, event.id), event)
        if framework_projection is not None:
            from src.runtime.framework_application_projection import record_event_projection

            record_event_projection(cur, self._settings, framework_projection, event, projection_stage, inserted=inserted)
        if not inserted:
            return False
        cur.execute(
            f"""
            UPDATE {self._settings.database.postgres_session_table}
            SET event_count = event_count + 1,
                updated_at = %s
            WHERE id = %s
            """,
            (datetime.utcnow(), session_id),
        )
        if (
            getattr(self, "_compaction_outbox_table", None)
            and getattr(self, "_compaction_task_stream", None)
            and event.type in {"turn.completed", "turn.interrupted"}
        ):
            task_id = f"task_compaction_{session_id}_{event.id}"
            task_payload = {
                "schema_version": 1,
                "task_id": task_id,
                "task_type": "compaction_task",
                "request_id": context_fields.get("request_id") or f"req_{event.id}",
                "trace_id": context_fields.get("trace_id") or f"trace_{event.id}",
                "session_id": session_id,
                "turn_id": context_fields.get("turn_id") or event_payload.get("turn_id"),
                "run_id": context_fields.get("run_id"),
                "run_item_id": context_fields.get("run_item_id"),
                "attempt_id": context_fields.get("attempt_id"),
                "worker_id": context_fields.get("worker_id"),
                "resource_id": context_fields.get("resource_id"),
                "created_at": event.timestamp.isoformat(),
                "payload": {
                    "source_event_id": event.id,
                    "source_event_type": event.type,
                },
            }
            cur.execute(
                f"""
                INSERT INTO {self._compaction_outbox_table} (
                    event_key, stream, payload
                ) VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (event_key) DO NOTHING
                """,
                (
                    f"compaction:{session_id}:{event.id}",
                    self._compaction_task_stream,
                    json.dumps(task_payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        return True

    def _list_events_sync(
        self,
        session_id: str,
        limit: int | None = None,
        after_event_id: str | None = None,
        from_start: bool = False,
    ) -> list[ExecutionEvent]:
        limit_value = _normalize_optional_limit(limit)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if after_event_id:
                    cur.execute(
                        f"""
                        SELECT event_sequence FROM {self._settings.database.postgres_event_table}
                        WHERE session_id = %s AND id = %s
                        LIMIT 1
                        """,
                        (session_id, after_event_id),
                    )
                    cursor = cur.fetchone()
                    if not cursor:
                        if not from_start:
                            return []
                        raise EventCursorNotFoundError(session_id)
                    params = [session_id, cursor["event_sequence"] if cursor else 0]
                    query = (f"SELECT * FROM {self._settings.database.postgres_event_table} "
                             "WHERE session_id = %s AND event_sequence > %s ORDER BY event_sequence ASC")
                    if limit_value is not None:
                        query += " LIMIT %s"
                        params.append(limit_value)
                    cur.execute(query, tuple(params))
                elif limit_value is not None and not from_start:
                    cur.execute(
                        f"""
                        SELECT * FROM (
                            SELECT * FROM {self._settings.database.postgres_event_table}
                            WHERE session_id = %s
                            ORDER BY event_sequence DESC
                            LIMIT %s
                        ) recent_events
                        ORDER BY event_sequence ASC
                        """,
                        (session_id, limit_value),
                    )
                else:
                    query = (f"SELECT * FROM {self._settings.database.postgres_event_table} "
                             "WHERE session_id = %s ORDER BY event_sequence ASC")
                    params = [session_id]
                    if limit_value is not None:
                        query += " LIMIT %s"
                        params.append(limit_value)
                    cur.execute(query, tuple(params))
                rows = cur.fetchall() or []
        events: list[ExecutionEvent] = []
        for row in rows:
            payload = dict(row.get("payload") or {})
            context = payload.pop("_harness_context", {})
            if not isinstance(context, dict):
                context = {}
            events.append(
                ExecutionEvent(
                    id=str(row["id"]),
                    type=row["type"],
                    session_id=session_id,
                    timestamp=ensure_utc_datetime(row["timestamp"]) or datetime.utcnow(),
                    payload=payload,
                    **{name: context.get(name) for name in (
                        "request_id", "trace_id", "turn_id", "run_id",
                        "run_item_id", "attempt_id", "worker_id", "resource_id",
                    )},
                )
            )
        return events

    def _save_snapshot_sync(
        self,
        session_id: str,
        snapshot: SessionSnapshot,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        framework_projection: FrameworkProjectionWrite | None = None,
    ) -> SessionSnapshot | None:
        now = datetime.utcnow()
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._assert_continuation_lease_sync(
                    cur, session_id, continuation_approval_id, continuation_lease_token
                )
                self._assert_turn_lease_sync(cur, session_id, turn_lease_token)
                projection_stage = None
                if framework_projection is not None:
                    from src.runtime.framework_application_projection import prepare_snapshot_projection

                    if framework_projection.owner.binding.session_id != session_id:
                        raise ValueError("Snapshot projection belongs to another Session")
                    projection_stage, existing = prepare_snapshot_projection(
                        cur, self._settings, framework_projection, snapshot)
                    if existing is not None:
                        return _snapshot_from_row(existing)
                # Serialize each session BEFORE trusting the caller's version: the
                # caller derived it from an in-memory snapshot_count, so two writers
                # can pick the same value and collide on (session_id, version).
                cur.execute(
                    f"SELECT id FROM {self._settings.database.postgres_session_table} "
                    "WHERE id = %s FOR UPDATE",
                    (session_id,),
                )
                if cur.fetchone() is None:
                    raise KeyError(session_id)
                version = snapshot.version
                cur.execute(
                    f"SELECT count(*) AS taken FROM {self._settings.database.postgres_snapshot_table} "
                    "WHERE session_id = %s AND version = %s AND id <> %s",
                    (session_id, version, snapshot.id),
                )
                if int((cur.fetchone() or {}).get("taken") or 0) > 0:
                    cur.execute(
                        f"SELECT coalesce(max(version), 0) + 1 AS next FROM "
                        f"{self._settings.database.postgres_snapshot_table} WHERE session_id = %s",
                        (session_id,),
                    )
                    version = int((cur.fetchone() or {})["next"])
                    snapshot = snapshot.model_copy(update={"version": version})
                conflict_clause = "ON CONFLICT (id) DO NOTHING" if framework_projection is not None else """
                    ON CONFLICT (id) DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        version = EXCLUDED.version,
                        stage = EXCLUDED.stage,
                        created_at = EXCLUDED.created_at,
                        graph_state = EXCLUDED.graph_state
                    """
                cur.execute(
                    f"""
                    INSERT INTO {self._settings.database.postgres_snapshot_table} (
                        id, session_id, version, stage, created_at, graph_state
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    {conflict_clause}
                    """,
                    (
                        snapshot.id,
                        session_id,
                        snapshot.version,
                        snapshot.stage,
                        snapshot.created_at,
                        json.dumps(make_json_safe(snapshot.graph_state), ensure_ascii=False),
                    ),
                )
                if framework_projection is not None and cur.rowcount != 1:
                    from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

                    raise FrameworkMessageStageRecoveryRequired("Runtime result snapshot appeared after its intent check")
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table}
                    SET snapshot_count = GREATEST(snapshot_count, %s),
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (snapshot.version, now, session_id),
                )
                if framework_projection is not None:
                    from src.runtime.framework_application_projection import record_snapshot_projection

                    actual = record_snapshot_projection(cur, self._settings, framework_projection, projection_stage)
                    return _snapshot_from_row(actual)

    def _assert_continuation_lease_sync(
        self,
        cur,
        session_id: str,
        approval_id: str | None,
        lease_token: str | None,
    ) -> None:
        if not approval_id and not lease_token:
            return
        if not approval_id or not lease_token:
            raise ContinuationLeaseLostError(
                "Continuation fencing requires approval id and lease token."
            )
        cur.execute(
            f"""
            SELECT 1 FROM {self._settings.database.postgres_approval_table}
            WHERE id = %s AND session_id = %s
              AND metadata->>'continuation_lease_token' = %s
              AND NOT (metadata ? 'continuation_completed_at')
              AND (metadata->>'continuation_lease_expires_at')::timestamptz > now()
            FOR UPDATE
            """,
            (approval_id, session_id, lease_token),
        )
        if cur.fetchone() is None:
            raise ContinuationLeaseLostError(
                "Continuation lease is no longer active for fenced write."
            )

    def _assert_turn_lease_sync(
        self,
        cur,
        session_id: str,
        lease_token: str | None,
    ) -> None:
        if not lease_token:
            return
        cur.execute(
            f"""
            SELECT 1 FROM {self._settings.database.postgres_session_table}
            WHERE id = %s
              AND metadata->>'turn_lease_token' = %s
              AND (metadata->>'turn_lease_expires_at')::timestamptz > now()
            FOR UPDATE
            """,
            (session_id, lease_token),
        )
        if cur.fetchone() is None:
            raise ContinuationLeaseLostError(
                "Turn execution lease is no longer active for fenced write."
            )

    def _list_snapshots_sync(
        self,
        session_id: str,
        limit: int | None = None,
        include_graph_state: bool = True,
    ) -> list[SessionSnapshot]:
        limit_value = _normalize_optional_limit(limit)
        columns = "*" if include_graph_state else "id, session_id, version, stage, created_at, '{}'::jsonb AS graph_state"
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if limit_value is not None:
                    cur.execute(
                        f"""
                        SELECT * FROM (
                            SELECT {columns} FROM {self._settings.database.postgres_snapshot_table}
                            WHERE session_id = %s
                            ORDER BY version DESC
                            LIMIT %s
                        ) recent_snapshots
                        ORDER BY version ASC
                        """,
                        (session_id, limit_value),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT {columns} FROM {self._settings.database.postgres_snapshot_table}
                        WHERE session_id = %s
                        ORDER BY version ASC
                        """,
                        (session_id,),
                    )
                rows = cur.fetchall() or []
        return [_snapshot_from_row(item) for item in rows]

    def _get_latest_snapshot_sync(self, session_id: str) -> SessionSnapshot | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {self._settings.database.postgres_snapshot_table}
                    WHERE session_id = %s
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
        return _snapshot_from_row(row) if row else None

    def _save_approval_sync(self, session_id: str, approval: ToolApprovalRequest) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self.save_approval_in_transaction(cur, session_id, approval)

    def save_approval_in_transaction(self, cur, session_id: str, approval: ToolApprovalRequest) -> None:
        """Save approval and its session timestamp in the caller's transaction."""
        now = datetime.utcnow()
        cur.execute(
            f"""
            INSERT INTO {self._settings.database.postgres_approval_table} (
                id, session_id, tool_key, tool_name, reason, status,
                created_at, resolved_at, decision_note, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                session_id = EXCLUDED.session_id,
                tool_key = EXCLUDED.tool_key,
                tool_name = EXCLUDED.tool_name,
                reason = EXCLUDED.reason,
                status = EXCLUDED.status,
                created_at = EXCLUDED.created_at,
                resolved_at = EXCLUDED.resolved_at,
                decision_note = EXCLUDED.decision_note,
                metadata = EXCLUDED.metadata
            """,
            (
                approval.id,
                session_id,
                approval.tool_key,
                approval.tool_name,
                approval.reason,
                approval.status.value,
                approval.created_at,
                approval.resolved_at,
                approval.decision_note,
                json.dumps(make_json_safe(approval.metadata), ensure_ascii=False),
            ),
        )
        cur.execute(
            f"UPDATE {self._settings.database.postgres_session_table} SET updated_at = %s WHERE id = %s",
            (now, session_id),
        )

    def _list_approvals_sync(self, session_id: str) -> list[ToolApprovalRequest]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {self._settings.database.postgres_approval_table}
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall() or []
        return [_approval_from_row(item) for item in rows]

    def _resolve_approval_sync(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest:
        resolved_at = datetime.utcnow()
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_approval_table}
                    SET status = %s,
                        decision_note = %s,
                        resolved_at = %s
                    WHERE id = %s AND session_id = %s AND status = 'pending'
                    RETURNING *
                    """,
                    (status.value, reason, resolved_at, approval_id, session_id),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        f"SELECT * FROM {self._settings.database.postgres_approval_table} "
                        "WHERE id = %s AND session_id = %s",
                        (approval_id, session_id),
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        raise KeyError(approval_id)
                    approval = _approval_from_row(existing)
                    if approval.status == status:
                        return approval
                    raise ValueError(f"Approval already resolved: {approval_id}")
                cur.execute(
                    f"UPDATE {self._settings.database.postgres_session_table} SET updated_at = %s WHERE id = %s",
                    (resolved_at, session_id),
                )
        return _approval_from_row(row)

    def _claim_turn_execution_sync(
        self,
        session_id: str,
        turn_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int,
        approval_id: str | None = None,
    ) -> SessionRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table}
                    SET status = 'running',
                        updated_at = now(),
                        state_revision = state_revision + 1,
                        metadata = metadata || jsonb_build_object(
                            'turn_lease_turn_id', %s::text,
                            'turn_lease_owner', %s::text,
                            'turn_lease_token', %s::text,
                            'turn_lease_expires_at',
                                to_jsonb(now() + (%s * INTERVAL '1 second'))
                        )
                    WHERE id = %s
                      AND (
                          status NOT IN ('running', 'waiting_approval')
                          OR metadata->>'turn_lease_turn_id' = %s
                          OR (status = 'waiting_approval'
                              AND metadata->'pending_turn'->>'turn_id' = %s
                              AND metadata->'pending_turn'->'pending_approval_ids' ? %s)
                      )
                      AND (
                          NOT (metadata ? 'turn_lease_expires_at')
                          OR (metadata->>'turn_lease_expires_at')::timestamptz <= now()
                      )
                    RETURNING *
                    """,
                    (turn_id, owner_id, lease_token, lease_seconds, session_id, turn_id, turn_id, approval_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(
                    f"""
                    SELECT * FROM {self._settings.database.postgres_message_table}
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                messages = [_message_from_row(item) for item in (cur.fetchall() or [])]
                return _session_from_row(row, messages=messages)

    def _renew_turn_execution_sync(
        self,
        session_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table}
                    SET metadata = metadata || jsonb_build_object(
                        'turn_lease_expires_at',
                            to_jsonb(now() + (%s * INTERVAL '1 second'))
                    )
                    WHERE id = %s
                      AND metadata->>'turn_lease_token' = %s
                      AND (metadata->>'turn_lease_expires_at')::timestamptz > now()
                    RETURNING id
                    """,
                    (lease_seconds, session_id, lease_token),
                )
                return cur.fetchone() is not None

    def _complete_turn_execution_sync(
        self,
        session_id: str,
        lease_token: str,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table}
                    SET metadata = metadata
                        - 'turn_lease_turn_id'
                        - 'turn_lease_owner'
                        - 'turn_lease_token'
                        - 'turn_lease_expires_at',
                        updated_at = now(),
                        state_revision = state_revision + 1
                    WHERE id = %s
                      AND metadata->>'turn_lease_token' = %s
                      AND (metadata->>'turn_lease_expires_at')::timestamptz > now()
                    RETURNING id
                    """,
                    (session_id, lease_token),
                )
                return cur.fetchone() is not None

    def _claim_coordinator_dispatch_sync(
        self,
        session_id: str,
        turn_id: str,
        dispatch_key: str,
        owner_id: str,
        request_fingerprint: str | None = None,
        worker_task_ids: list[str] | None = None,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table}
                    SET metadata = jsonb_set(
                        metadata,
                        '{{coordinator_dispatch_claims}}',
                        COALESCE(metadata->'coordinator_dispatch_claims', '[]'::jsonb)
                            || jsonb_build_array(
                                jsonb_build_object(
                                    'turn_id', %s::text,
                                    'dispatch_key', %s::text,
                                    'owner_id', %s::text,
                                    'request_fingerprint', %s::text,
                                    'worker_task_ids', %s::jsonb,
                                    'claimed_at', now()
                                )
                            ),
                        true
                    ),
                    updated_at = now()
                    WHERE id = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              COALESCE(metadata->'coordinator_dispatch_claims', '[]'::jsonb)
                          ) AS claim
                          WHERE claim->>'turn_id' = %s
                            AND claim->>'dispatch_key' = %s
                      )
                    RETURNING id
                    """,
                    (
                        turn_id,
                        dispatch_key,
                        owner_id,
                        request_fingerprint,
                        json.dumps(worker_task_ids or [], ensure_ascii=False),
                        session_id,
                        turn_id,
                        dispatch_key,
                    ),
                )
                return cur.fetchone() is not None

    def _release_coordinator_dispatch_claim_if_no_child_sync(
        self,
        session_id: str,
        turn_id: str,
        dispatch_key: str,
        owner_id: str,
        request_fingerprint: str,
        worker_task_ids: list[str] | None = None,
        require_settled_parent: bool = False,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table} AS parent
                    SET metadata = jsonb_set(
                        COALESCE(parent.metadata, '{{}}'::jsonb),
                        '{{coordinator_dispatch_claims}}',
                        COALESCE(
                            (
                                SELECT jsonb_agg(claim.item ORDER BY claim.ordinality)
                                FROM jsonb_array_elements(
                                    COALESCE(
                                        parent.metadata->'coordinator_dispatch_claims',
                                        '[]'::jsonb
                                    )
                                ) WITH ORDINALITY AS claim(item, ordinality)
                                WHERE NOT (
                                    claim.item->>'turn_id' = %s
                                    AND claim.item->>'dispatch_key' = %s
                                    AND claim.item->>'owner_id' = %s
                                    AND claim.item->>'request_fingerprint' = %s
                                )
                            ),
                            '[]'::jsonb
                        ),
                        true
                    ),
                    updated_at = now()
                    WHERE parent.id = %s
                      AND EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              COALESCE(
                                  parent.metadata->'coordinator_dispatch_claims',
                                  '[]'::jsonb
                              )
                          ) AS claim(item)
                          WHERE claim.item->>'turn_id' = %s
                            AND claim.item->>'dispatch_key' = %s
                            AND claim.item->>'owner_id' = %s
                            AND claim.item->>'request_fingerprint' = %s
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {self._settings.database.postgres_session_table} AS child
                          WHERE child.metadata->>'parent_session_id' = parent.id
                            AND child.metadata->>'parent_turn_id' = %s
                            AND (
                                child.metadata->>'coordinator_dispatch_key' = %s
                                OR child.metadata->>'task_id' = ANY(%s::text[])
                            )
                      )
                      AND (
                          NOT %s::boolean
                          OR (
                              parent.status IN ('interrupted', 'completed', 'failed')
                              AND COALESCE(parent.metadata->>'turn_lease_token', '') = ''
                          )
                      )
                    RETURNING parent.id
                    """,
                    (
                        turn_id,
                        dispatch_key,
                        owner_id,
                        request_fingerprint,
                        session_id,
                        turn_id,
                        dispatch_key,
                        owner_id,
                        request_fingerprint,
                        turn_id,
                        dispatch_key,
                        worker_task_ids or [],
                        require_settled_parent,
                    ),
                )
                return cur.fetchone() is not None

    def _recover_expired_turn_executions_sync(self) -> list[str]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_session_table}
                    SET status = 'interrupted',
                        state_revision = state_revision + 1,
                        metadata = jsonb_set(
                            (
                                metadata
                                - 'turn_lease_turn_id'
                                - 'turn_lease_owner'
                                - 'turn_lease_token'
                                - 'turn_lease_expires_at'
                            ),
                            '{{control}}',
                            COALESCE(
                                (
                                    metadata
                                    - 'turn_lease_turn_id'
                                    - 'turn_lease_owner'
                                    - 'turn_lease_token'
                                    - 'turn_lease_expires_at'
                                )->'control',
                                '{{}}'::jsonb
                            ) || jsonb_build_object(
                                'control_state', 'interrupted',
                                'is_interrupted', true,
                                'is_resumable', COALESCE(metadata->'pending_turn', '{{}}'::jsonb) <> '{{}}'::jsonb,
                                'preserve_resources', COALESCE(metadata->'pending_turn', '{{}}'::jsonb) <> '{{}}'::jsonb,
                                'last_interrupt_reason', 'Turn execution lease expired while the service was unavailable.'
                            ),
                            true
                        ),
                        updated_at = now()
                    WHERE status = 'running'
                      AND (metadata->>'turn_lease_expires_at') IS NOT NULL
                      AND (metadata->>'turn_lease_expires_at')::timestamptz <= now()
                    RETURNING id
                    """
                )
                return [str(row["id"]) for row in (cur.fetchall() or [])]

    def _assert_turn_execution_sync(self, session_id: str, lease_token: str) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._assert_turn_lease_sync(cur, session_id, lease_token)

    def _claim_approval_continuation_sync(
        self,
        session_id: str,
        approval_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_approval_table}
                    SET metadata = metadata || jsonb_build_object(
                        'continuation_lease_owner', %s::text,
                        'continuation_lease_token', %s::text,
                        'continuation_lease_expires_at',
                            to_jsonb(now() + (%s * INTERVAL '1 second'))
                    )
                    WHERE id = %s
                      AND session_id = %s
                      AND status != 'pending'
                      AND NOT (metadata ? 'continuation_completed_at')
                      AND (
                          NOT (metadata ? 'continuation_lease_expires_at')
                          OR (metadata->>'continuation_lease_expires_at')::timestamptz <= now()
                      )
                    RETURNING id
                    """,
                    (owner_id, lease_token, lease_seconds, approval_id, session_id),
                )
                return cur.fetchone() is not None

    def _renew_approval_continuation_sync(
        self,
        session_id: str,
        approval_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_approval_table}
                    SET metadata = metadata || jsonb_build_object(
                        'continuation_lease_expires_at',
                            to_jsonb(now() + (%s * INTERVAL '1 second'))
                    )
                    WHERE id = %s
                      AND session_id = %s
                      AND metadata->>'continuation_lease_token' = %s
                      AND NOT (metadata ? 'continuation_completed_at')
                    RETURNING id
                    """,
                    (lease_seconds, approval_id, session_id, lease_token),
                )
                return cur.fetchone() is not None

    def _complete_approval_continuation_sync(
        self,
        session_id: str,
        approval_id: str,
        lease_token: str,
    ) -> bool:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._settings.database.postgres_approval_table}
                    SET metadata = (
                        metadata
                        - 'continuation_lease_owner'
                        - 'continuation_lease_token'
                        - 'continuation_lease_expires_at'
                    ) || jsonb_build_object('continuation_completed_at', to_jsonb(now()))
                    WHERE id = %s
                      AND session_id = %s
                      AND metadata->>'continuation_lease_token' = %s
                      AND (metadata->>'continuation_lease_expires_at')::timestamptz > now()
                    RETURNING id
                    """,
                    (approval_id, session_id, lease_token),
                )
                return cur.fetchone() is not None

    async def delete_session(self, session_id: str) -> bool:
        def _do() -> bool:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    for tbl in (
                        self._settings.database.postgres_approval_table,
                        self._settings.database.postgres_snapshot_table,
                        self._settings.database.postgres_event_table,
                        self._settings.database.postgres_message_table,
                    ):
                        cur.execute(f"DELETE FROM {tbl} WHERE session_id = %s", (session_id,))
                    cur.execute(
                        f"DELETE FROM {self._settings.database.postgres_session_table} WHERE id = %s RETURNING id",
                        (session_id,),
                    )
                    deleted = cur.fetchone() is not None
                    conn.commit()
                    return deleted

        return await asyncio.to_thread(_do)

    async def count_sessions(self, before: datetime | None = None, after: datetime | None = None) -> int:
        def _do() -> int:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    table = self._settings.database.postgres_session_table
                    if before is None and after is None:
                        cur.execute(f"SELECT COUNT(id) AS cnt FROM {table}")
                    elif before and after:
                        cur.execute(
                            f"SELECT COUNT(id) AS cnt FROM {table} WHERE updated_at >= %s AND updated_at < %s",
                            (after, before),
                        )
                    elif after:
                        cur.execute(
                            f"SELECT COUNT(id) AS cnt FROM {table} WHERE updated_at >= %s",
                            (after,),
                        )
                    else:
                        cur.execute(
                            f"SELECT COUNT(id) AS cnt FROM {table} WHERE updated_at < %s",
                            (before,),
                        )
                    row = cur.fetchone()
                    return int(row["cnt"]) if row else 0
        return await asyncio.to_thread(_do)

    async def count_sessions_by_status(self) -> dict[str, int]:
        def _do() -> dict[str, int]:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT status, COUNT(id) AS cnt FROM {self._settings.database.postgres_session_table} GROUP BY status"
                    )
                    rows = cur.fetchall() or []
                    return {row["status"]: int(row["cnt"]) for row in rows}
        return await asyncio.to_thread(_do)

    async def count_project_sessions(self, project_id: str) -> int:
        def _do() -> int:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT COUNT(id) AS cnt FROM {self._settings.database.postgres_session_table} WHERE project_id = %s",
                        (project_id,),
                    )
                    row = cur.fetchone()
                    return int(row["cnt"]) if row else 0
        return await asyncio.to_thread(_do)

    async def list_project_history_context(
        self,
        project_id: str,
        *,
        limit: int,
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._list_project_history_context_sync,
            project_id,
            max(1, min(limit, 50)),
        )

    def _list_project_history_context_sync(self, project_id: str, limit: int) -> list[dict]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, title, mode_key, status, updated_at, metadata
                    FROM {self._settings.database.postgres_session_table}
                    WHERE project_id = %s
                    ORDER BY updated_at DESC, id ASC
                    LIMIT %s
                    """,
                    (project_id, limit),
                )
                rows = cur.fetchall() or []
        return [_history_context_from_row(row) for row in rows]

    async def delete_sessions_before(self, cutoff: datetime | None = None) -> int:
        def _do() -> int:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    if cutoff is None:
                        where = ""
                        params: tuple = ()
                    else:
                        where = "WHERE updated_at < %s"
                        params = (cutoff,)
                    id_query = f"SELECT id FROM {self._settings.database.postgres_session_table} {where}"
                    for tbl in (
                        self._settings.database.postgres_approval_table,
                        self._settings.database.postgres_snapshot_table,
                        self._settings.database.postgres_event_table,
                        self._settings.database.postgres_message_table,
                    ):
                        cur.execute(f"DELETE FROM {tbl} WHERE session_id IN ({id_query})", params)
                    cur.execute(
                        f"DELETE FROM {self._settings.database.postgres_session_table} {where} RETURNING id",
                        params,
                    )
                    deleted = len(cur.fetchall())
                    conn.commit()
                    return deleted
        return await asyncio.to_thread(_do)

    async def get_session_date_range(self) -> dict[str, datetime | None]:
        def _do() -> dict[str, datetime | None]:
            with postgres_connect(self._settings) as conn:
                with conn.cursor() as cur:
                    tbl = self._settings.database.postgres_session_table
                    cur.execute(f"SELECT MIN(updated_at) AS oldest, MAX(updated_at) AS newest FROM {tbl}")
                    row = cur.fetchone()
                    if not row:
                        return {"oldest": None, "newest": None}
                    return {"oldest": row["oldest"], "newest": row["newest"]}
        return await asyncio.to_thread(_do)

    async def bulk_export(self, progress_fn=None) -> list[dict]:
        return await asyncio.to_thread(self._bulk_export_sync, progress_fn)

    def _bulk_export_sync(self, progress_fn=None) -> list[dict]:
        """Batch-streamed export: iterate sessions in chunks, 5 queries per chunk."""
        s_tbl = self._settings.database.postgres_session_table
        m_tbl = self._settings.database.postgres_message_table
        e_tbl = self._settings.database.postgres_event_table
        snap_tbl = self._settings.database.postgres_snapshot_table
        a_tbl = self._settings.database.postgres_approval_table
        batch_size = 500

        def _iso(v: object) -> str | None:
            return v.isoformat() if hasattr(v, "isoformat") else None

        bundle: list[dict] = []
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT id FROM {s_tbl} ORDER BY updated_at DESC")
                all_ids = [row["id"] for row in (cur.fetchall() or [])]

            for i in range(0, len(all_ids), batch_size):
                chunk_ids = all_ids[i : i + batch_size]
                if progress_fn:
                    progress_fn(min(i + len(chunk_ids), len(all_ids)))
                placeholders = ",".join(["%s"] * len(chunk_ids))

                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT * FROM {s_tbl} WHERE id IN ({placeholders}) ORDER BY updated_at DESC",
                        chunk_ids,
                    )
                    session_rows = cur.fetchall() or []

                    cur.execute(
                        f"SELECT * FROM {m_tbl} WHERE session_id IN ({placeholders}) ORDER BY session_id, created_at",
                        chunk_ids,
                    )
                    msg_rows = cur.fetchall() or []

                    cur.execute(
                        f"SELECT * FROM {e_tbl} WHERE session_id IN ({placeholders}) ORDER BY session_id, timestamp",
                        chunk_ids,
                    )
                    event_rows = cur.fetchall() or []

                    cur.execute(
                        f"SELECT * FROM {snap_tbl} WHERE session_id IN ({placeholders}) ORDER BY session_id, version",
                        chunk_ids,
                    )
                    snap_rows = cur.fetchall() or []

                    cur.execute(
                        f"SELECT * FROM {a_tbl} WHERE session_id IN ({placeholders}) ORDER BY session_id, created_at",
                        chunk_ids,
                    )
                    approval_rows = cur.fetchall() or []

                from collections import defaultdict
                msgs_map: dict[str, list] = defaultdict(list)
                for r in msg_rows:
                    msgs_map[r["session_id"]].append(r)
                events_map: dict[str, list] = defaultdict(list)
                for r in event_rows:
                    events_map[r["session_id"]].append(r)
                snaps_map: dict[str, list] = defaultdict(list)
                for r in snap_rows:
                    snaps_map[r["session_id"]].append(r)
                approvals_map: dict[str, list] = defaultdict(list)
                for r in approval_rows:
                    approvals_map[r["session_id"]].append(r)

                for s in session_rows:
                    sid = s["id"]
                    bundle.append({
                        "id": sid,
                        "title": s["title"],
                        "status": s["status"],
                        "session_mode": s["session_mode"],
                        "runtime_mode": s["runtime_mode"],
                        "mode_key": s["mode_key"],
                        "created_at": _iso(s["created_at"]),
                        "updated_at": _iso(s["updated_at"]),
                        "preferred_model": s.get("preferred_model"),
                        "selected_agent": s.get("selected_agent"),
                        "project_id": str(s.get("project_id")) if s.get("project_id") else None,
                        "metadata": dict(s.get("metadata") or {}),
                        "event_count": s["event_count"],
                        "snapshot_count": s["snapshot_count"],
                        "messages": [
                            {"id": m["id"], "role": m["role"], "content": m["content"],
                             "created_at": _iso(m["created_at"]), "metadata": dict(m.get("metadata") or {})}
                            for m in msgs_map.get(sid, [])
                        ],
                        "events": [
                            {"type": e["type"], "session_id": sid,
                             "timestamp": _iso(e["timestamp"]), "payload": dict(e.get("payload") or {})}
                            for e in events_map.get(sid, [])
                        ],
                        "snapshots": [
                            {"id": sn["id"], "session_id": sid, "version": sn["version"],
                             "stage": sn["stage"], "created_at": _iso(sn["created_at"]),
                             "graph_state": dict(sn.get("graph_state") or {})}
                            for sn in snaps_map.get(sid, [])
                        ],
                        "approvals": [
                            {"id": a["id"], "session_id": sid, "tool_key": a["tool_key"],
                             "tool_name": a["tool_name"], "reason": a["reason"], "status": a["status"],
                             "created_at": _iso(a["created_at"]), "resolved_at": _iso(a.get("resolved_at")),
                             "decision_note": a.get("decision_note"), "metadata": dict(a.get("metadata") or {})}
                            for a in approvals_map.get(sid, [])
                        ],
                    })
        return bundle


def _session_from_row(row: dict, messages: list[ChatMessage]) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        title=row["title"],
        status=SessionStatus(row["status"]),
        session_mode=SessionMode(row["session_mode"]),
        runtime_mode=RuntimeMode(row["runtime_mode"]),
        mode_key=str(row.get("mode_key") or "default"),
        project_id=str(row.get("project_id")) if row.get("project_id") else None,
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
        preferred_model=row.get("preferred_model"),
        selected_agent=row.get("selected_agent"),
        metadata=dict(row.get("metadata") or {}),
        messages=messages,
        event_count=int(row.get("event_count") or 0),
        snapshot_count=int(row.get("snapshot_count") or 0),
        state_revision=int(row.get("state_revision") or 0),
    )


def _history_overview_from_row(row: dict) -> dict:
    return {
        "session_id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "created_at": (
            ensure_utc_datetime(row.get("created_at")) or datetime.utcnow()
        ).isoformat(),
        "updated_at": (
            ensure_utc_datetime(row.get("updated_at")) or datetime.utcnow()
        ).isoformat(),
        "message_count": int(row.get("message_count") or 0),
        "event_count": int(row.get("event_count") or 0),
        "snapshot_count": int(row.get("snapshot_count") or 0),
        "selected_agent": row.get("selected_agent"),
        "preferred_model": row.get("preferred_model"),
    }


def _history_context_from_row(row: dict) -> dict:
    metadata = dict(row.get("metadata") or {})
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
    updated_at = ensure_utc_datetime(row.get("updated_at")) or datetime.utcnow()
    return {
        "session_id": str(row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "mode_key": str(row.get("mode_key") or "default"),
        "status": str(row.get("status") or ""),
        "updated_at": updated_at.isoformat(),
        "failure_summary": failure_summary[:500],
    }


def _task_pool_session_from_row(row: dict) -> TaskPoolSessionSummary:
    raw_workers = row.get("worker_dispatches")
    workers = []
    if isinstance(raw_workers, list):
        workers = [dict(item) for item in raw_workers if isinstance(item, dict)]
    return TaskPoolSessionSummary(
        id=row["id"],
        title=row["title"],
        status=SessionStatus(row["status"]),
        session_mode=SessionMode(row["session_mode"]),
        runtime_mode=RuntimeMode(row["runtime_mode"]),
        mode_key=str(row.get("mode_key") or "default"),
        project_id=str(row.get("project_id")) if row.get("project_id") else None,
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
        selected_agent=row.get("selected_agent"),
        worker_dispatches=workers,
        parent_session_id=str(row.get("parent_session_id") or "").strip(),
    )


def _message_from_row(row: dict) -> ChatMessage:
    return ChatMessage(
        id=row["id"],
        role=MessageRole(row["role"]),
        content=row["content"],
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        metadata=dict(row.get("metadata") or {}),
    )


def _snapshot_from_row(row: dict) -> SessionSnapshot:
    return SessionSnapshot(
        id=row["id"],
        session_id=row["session_id"],
        version=int(row["version"]),
        stage=row["stage"],
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        graph_state=dict(row.get("graph_state") or {}),
    )


def _normalize_optional_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    size = max(int(limit), 0)
    return size if size > 0 else 0


def _approval_from_row(row: dict) -> ToolApprovalRequest:
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
