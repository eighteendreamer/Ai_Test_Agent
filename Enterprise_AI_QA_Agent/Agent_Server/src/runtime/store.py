from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Protocol

import pymysql

from src.core.config import Settings
from src.domain.models import SessionRecord
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
            session.snapshot_count += 1
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
        return sorted(
            self._approvals.get(session_id, {}).values(),
            key=lambda item: item.created_at,
        )

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


class MySQLSessionStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queues: dict[str, asyncio.Queue[ExecutionEvent]] = defaultdict(asyncio.Queue)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

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

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.session_table}` (
                        `id` VARCHAR(64) NOT NULL PRIMARY KEY,
                        `title` VARCHAR(255) NOT NULL,
                        `status` VARCHAR(64) NOT NULL,
                        `session_mode` VARCHAR(64) NOT NULL,
                        `runtime_mode` VARCHAR(64) NOT NULL,
                        `created_at` DATETIME NOT NULL,
                        `updated_at` DATETIME NOT NULL,
                        `preferred_model` VARCHAR(255) NULL,
                        `selected_agent` VARCHAR(255) NULL,
                        `metadata_json` LONGTEXT NULL,
                        `event_count` INT NOT NULL DEFAULT 0,
                        `snapshot_count` INT NOT NULL DEFAULT 0
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.session_message_table}` (
                        `record_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        `message_id` VARCHAR(64) NOT NULL,
                        `session_id` VARCHAR(64) NOT NULL,
                        `role` VARCHAR(32) NOT NULL,
                        `content` LONGTEXT NOT NULL,
                        `created_at` DATETIME NOT NULL,
                        `metadata_json` LONGTEXT NULL,
                        UNIQUE KEY `uniq_session_message` (`session_id`, `message_id`),
                        KEY `idx_message_session` (`session_id`, `record_id`)
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.session_event_table}` (
                        `record_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        `session_id` VARCHAR(64) NOT NULL,
                        `event_type` VARCHAR(128) NOT NULL,
                        `timestamp` DATETIME NOT NULL,
                        `payload_json` LONGTEXT NULL,
                        KEY `idx_event_session` (`session_id`, `record_id`)
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.session_snapshot_table}` (
                        `record_id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        `snapshot_id` VARCHAR(64) NOT NULL,
                        `session_id` VARCHAR(64) NOT NULL,
                        `version` INT NOT NULL,
                        `stage` VARCHAR(64) NOT NULL,
                        `created_at` DATETIME NOT NULL,
                        `graph_state_json` LONGTEXT NULL,
                        UNIQUE KEY `uniq_snapshot_session_version` (`session_id`, `version`),
                        UNIQUE KEY `uniq_snapshot_id` (`snapshot_id`)
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.session_approval_table}` (
                        `id` VARCHAR(64) NOT NULL PRIMARY KEY,
                        `session_id` VARCHAR(64) NOT NULL,
                        `tool_key` VARCHAR(128) NOT NULL,
                        `tool_name` VARCHAR(255) NOT NULL,
                        `reason` LONGTEXT NOT NULL,
                        `status` VARCHAR(32) NOT NULL,
                        `created_at` DATETIME NOT NULL,
                        `resolved_at` DATETIME NULL,
                        `decision_note` LONGTEXT NULL,
                        `metadata_json` LONGTEXT NULL,
                        KEY `idx_approval_session_created` (`session_id`, `created_at`)
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
            conn.commit()

    def _save_session_sync(self, session: SessionRecord) -> SessionRecord:
        now = datetime.utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT event_count, snapshot_count FROM `{self._settings.session_table}` WHERE id=%s",
                    (session.id,),
                )
                existing = cur.fetchone()
                event_count = max(int(session.event_count), int(existing["event_count"])) if existing else int(session.event_count)
                snapshot_count = max(int(session.snapshot_count), int(existing["snapshot_count"])) if existing else int(session.snapshot_count)
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.session_table}`
                    (`id`, `title`, `status`, `session_mode`, `runtime_mode`, `created_at`, `updated_at`,
                     `preferred_model`, `selected_agent`, `metadata_json`, `event_count`, `snapshot_count`)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `title`=VALUES(`title`),
                        `status`=VALUES(`status`),
                        `session_mode`=VALUES(`session_mode`),
                        `runtime_mode`=VALUES(`runtime_mode`),
                        `updated_at`=VALUES(`updated_at`),
                        `preferred_model`=VALUES(`preferred_model`),
                        `selected_agent`=VALUES(`selected_agent`),
                        `metadata_json`=VALUES(`metadata_json`),
                        `event_count`=GREATEST(`event_count`, VALUES(`event_count`)),
                        `snapshot_count`=GREATEST(`snapshot_count`, VALUES(`snapshot_count`))
                    """,
                    (
                        session.id,
                        session.title,
                        session.status.value,
                        session.session_mode.value,
                        session.runtime_mode.value,
                        session.created_at,
                        now,
                        session.preferred_model,
                        session.selected_agent,
                        _json_dumps(session.metadata),
                        event_count,
                        snapshot_count,
                    ),
                )
                if session.messages:
                    payloads = [
                        (
                            item.id,
                            session.id,
                            item.role.value,
                            item.content,
                            item.created_at,
                            _json_dumps(item.metadata),
                        )
                        for item in session.messages
                        if bool(item.metadata.get("persist_transcript", True))
                    ]
                    if payloads:
                        cur.executemany(
                            f"""
                            INSERT IGNORE INTO `{self._settings.session_message_table}`
                            (`message_id`, `session_id`, `role`, `content`, `created_at`, `metadata_json`)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            payloads,
                        )
            conn.commit()

        session.updated_at = now
        session.event_count = event_count
        session.snapshot_count = snapshot_count
        return session

    def _get_session_sync(self, session_id: str, include_messages: bool) -> SessionRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM `{self._settings.session_table}` WHERE id=%s",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None

                messages: list[ChatMessage] = []
                if include_messages:
                    cur.execute(
                        f"""
                        SELECT message_id, role, content, created_at, metadata_json
                        FROM `{self._settings.session_message_table}`
                        WHERE session_id=%s
                        ORDER BY record_id ASC
                        """,
                        (session_id,),
                    )
                    messages = [self._row_to_message(item) for item in cur.fetchall()]

        return self._row_to_session(row, messages)

    def _list_sessions_sync(self) -> list[SessionRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM `{self._settings.session_table}` ORDER BY updated_at DESC"
                )
                rows = cur.fetchall()
        return [self._row_to_session(item, []) for item in rows]

    def _append_event_sync(self, session_id: str, event: ExecutionEvent) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.session_event_table}`
                    (`session_id`, `event_type`, `timestamp`, `payload_json`)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        session_id,
                        event.type,
                        event.timestamp,
                        _json_dumps(event.payload),
                    ),
                )
                cur.execute(
                    f"""
                    UPDATE `{self._settings.session_table}`
                    SET event_count = event_count + 1, updated_at=%s
                    WHERE id=%s
                    """,
                    (datetime.utcnow(), session_id),
                )
            conn.commit()

    def _list_events_sync(self, session_id: str) -> list[ExecutionEvent]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT event_type, timestamp, payload_json
                    FROM `{self._settings.session_event_table}`
                    WHERE session_id=%s
                    ORDER BY record_id ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
        return [
            ExecutionEvent(
                type=row["event_type"],
                session_id=session_id,
                timestamp=_to_datetime(row["timestamp"]),
                payload=_json_loads(row.get("payload_json")),
            )
            for row in rows
        ]

    def _save_snapshot_sync(self, session_id: str, snapshot: SessionSnapshot) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.session_snapshot_table}`
                    (`snapshot_id`, `session_id`, `version`, `stage`, `created_at`, `graph_state_json`)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `stage`=VALUES(`stage`),
                        `created_at`=VALUES(`created_at`),
                        `graph_state_json`=VALUES(`graph_state_json`)
                    """,
                    (
                        snapshot.id,
                        session_id,
                        snapshot.version,
                        snapshot.stage,
                        snapshot.created_at,
                        _json_dumps(snapshot.graph_state),
                    ),
                )
                cur.execute(
                    f"""
                    UPDATE `{self._settings.session_table}`
                    SET snapshot_count = GREATEST(snapshot_count, %s), updated_at=%s
                    WHERE id=%s
                    """,
                    (snapshot.version, datetime.utcnow(), session_id),
                )
            conn.commit()

    def _list_snapshots_sync(self, session_id: str) -> list[SessionSnapshot]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT snapshot_id, version, stage, created_at, graph_state_json
                    FROM `{self._settings.session_snapshot_table}`
                    WHERE session_id=%s
                    ORDER BY version ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
        return [
            SessionSnapshot(
                id=row["snapshot_id"],
                session_id=session_id,
                version=int(row["version"]),
                stage=row["stage"],
                created_at=_to_datetime(row["created_at"]),
                graph_state=_json_loads(row.get("graph_state_json")),
            )
            for row in rows
        ]

    def _get_latest_snapshot_sync(self, session_id: str) -> SessionSnapshot | None:
        snapshots = self._list_snapshots_sync(session_id)
        return snapshots[-1] if snapshots else None

    def _save_approval_sync(self, session_id: str, approval: ToolApprovalRequest) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.session_approval_table}`
                    (`id`, `session_id`, `tool_key`, `tool_name`, `reason`, `status`,
                     `created_at`, `resolved_at`, `decision_note`, `metadata_json`)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `tool_key`=VALUES(`tool_key`),
                        `tool_name`=VALUES(`tool_name`),
                        `reason`=VALUES(`reason`),
                        `status`=VALUES(`status`),
                        `resolved_at`=VALUES(`resolved_at`),
                        `decision_note`=VALUES(`decision_note`),
                        `metadata_json`=VALUES(`metadata_json`)
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
                        _json_dumps(approval.metadata),
                    ),
                )
                cur.execute(
                    f"UPDATE `{self._settings.session_table}` SET updated_at=%s WHERE id=%s",
                    (datetime.utcnow(), session_id),
                )
            conn.commit()

    def _list_approvals_sync(self, session_id: str) -> list[ToolApprovalRequest]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, tool_key, tool_name, reason, status, created_at, resolved_at,
                           decision_note, metadata_json
                    FROM `{self._settings.session_approval_table}`
                    WHERE session_id=%s
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
        return [
            ToolApprovalRequest(
                id=row["id"],
                session_id=session_id,
                tool_key=row["tool_key"],
                tool_name=row["tool_name"],
                reason=row["reason"],
                status=ToolApprovalStatus(row["status"]),
                created_at=_to_datetime(row["created_at"]),
                resolved_at=_to_datetime(row["resolved_at"]),
                decision_note=row.get("decision_note"),
                metadata=_json_loads(row.get("metadata_json")),
            )
            for row in rows
        ]

    def _resolve_approval_sync(
        self,
        session_id: str,
        approval_id: str,
        status: ToolApprovalStatus,
        reason: str | None = None,
    ) -> ToolApprovalRequest:
        resolved_at = datetime.utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE `{self._settings.session_approval_table}`
                    SET `status`=%s, `decision_note`=%s, `resolved_at`=%s
                    WHERE `id`=%s AND `session_id`=%s
                    """,
                    (status.value, reason, resolved_at, approval_id, session_id),
                )
                if cur.rowcount == 0:
                    raise KeyError(approval_id)
                cur.execute(
                    f"""
                    SELECT id, tool_key, tool_name, reason, status, created_at, resolved_at,
                           decision_note, metadata_json
                    FROM `{self._settings.session_approval_table}`
                    WHERE id=%s AND session_id=%s
                    """,
                    (approval_id, session_id),
                )
                row = cur.fetchone()
            conn.commit()

        if row is None:
            raise KeyError(approval_id)
        return ToolApprovalRequest(
            id=row["id"],
            session_id=session_id,
            tool_key=row["tool_key"],
            tool_name=row["tool_name"],
            reason=row["reason"],
            status=ToolApprovalStatus(row["status"]),
            created_at=_to_datetime(row["created_at"]),
            resolved_at=_to_datetime(row["resolved_at"]),
            decision_note=row.get("decision_note"),
            metadata=_json_loads(row.get("metadata_json")),
        )

    def _connect(self):
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password,
            database=self._settings.mysql_database,
            charset=self._settings.mysql_charset,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def _row_to_session(self, row: dict, messages: list[ChatMessage]) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            title=row["title"],
            status=SessionStatus(row["status"]),
            session_mode=SessionMode(row["session_mode"]),
            runtime_mode=RuntimeMode(row["runtime_mode"]),
            created_at=_to_datetime(row["created_at"]) or datetime.utcnow(),
            updated_at=_to_datetime(row["updated_at"]) or datetime.utcnow(),
            preferred_model=row.get("preferred_model"),
            selected_agent=row.get("selected_agent"),
            metadata=_json_loads(row.get("metadata_json")),
            messages=messages,
            event_count=int(row.get("event_count") or 0),
            snapshot_count=int(row.get("snapshot_count") or 0),
        )

    def _row_to_message(self, row: dict) -> ChatMessage:
        return ChatMessage(
            id=row["message_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            created_at=_to_datetime(row["created_at"]) or datetime.utcnow(),
            metadata=_json_loads(row.get("metadata_json")),
        )


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None) -> dict:
    if not value:
        return {}
    loaded = json.loads(value)
    if isinstance(loaded, dict):
        return loaded
    return {"value": loaded}


def _to_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
