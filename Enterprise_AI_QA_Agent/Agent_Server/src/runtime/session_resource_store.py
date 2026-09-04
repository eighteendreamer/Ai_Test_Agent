from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime, make_json_safe
from src.schemas.session_resource import (
    SessionResourceCleanupPolicy,
    SessionResourceKind,
    SessionResourceRecord,
    SessionResourceStatus,
)


class SessionResourceStore(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, resource: SessionResourceRecord) -> SessionResourceRecord: ...
    async def list(self, session_id: str, active_only: bool = False) -> list[SessionResourceRecord]: ...
    async def mark_status(
        self,
        resource_id: str,
        status: SessionResourceStatus,
        metadata: dict | None = None,
    ) -> SessionResourceRecord | None: ...


class InMemorySessionResourceStore:
    def __init__(self) -> None:
        self._resources: dict[str, SessionResourceRecord] = {}
        self._by_session: dict[str, list[str]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def save(self, resource: SessionResourceRecord) -> SessionResourceRecord:
        async with self._lock:
            resource.updated_at = datetime.utcnow()
            if resource.id not in self._resources:
                self._by_session[resource.session_id].append(resource.id)
            self._resources[resource.id] = resource
            return resource

    async def list(self, session_id: str, active_only: bool = False) -> list[SessionResourceRecord]:
        records = [self._resources[item] for item in self._by_session.get(session_id, []) if item in self._resources]
        if active_only:
            records = [item for item in records if item.status == SessionResourceStatus.active]
        return sorted(records, key=lambda item: item.created_at)

    async def mark_status(
        self,
        resource_id: str,
        status: SessionResourceStatus,
        metadata: dict | None = None,
    ) -> SessionResourceRecord | None:
        resource = self._resources.get(resource_id)
        if resource is None:
            return None
        resource.status = status
        resource.updated_at = datetime.utcnow()
        if status in {SessionResourceStatus.released, SessionResourceStatus.missing}:
            resource.released_at = datetime.utcnow()
        if metadata:
            resource.metadata = {**resource.metadata, **metadata}
        return resource


class PostgresSessionResourceStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def save(self, resource: SessionResourceRecord) -> SessionResourceRecord:
        return await asyncio.to_thread(self._save_sync, resource)

    async def list(self, session_id: str, active_only: bool = False) -> list[SessionResourceRecord]:
        return await asyncio.to_thread(self._list_sync, session_id, active_only)

    async def mark_status(
        self,
        resource_id: str,
        status: SessionResourceStatus,
        metadata: dict | None = None,
    ) -> SessionResourceRecord | None:
        return await asyncio.to_thread(self._mark_status_sync, resource_id, status, metadata or {})

    def _initialize_sync(self) -> None:
        table = self._settings.database.postgres_session_resource_table
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        resource_key TEXT NOT NULL,
                        status TEXT NOT NULL,
                        cleanup_policy TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        released_at TIMESTAMPTZ NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        UNIQUE(session_id, kind, resource_key)
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_session_status "
                    f"ON {table} (session_id, status, updated_at DESC)"
                )

    def _save_sync(self, resource: SessionResourceRecord) -> SessionResourceRecord:
        now = datetime.utcnow()
        resource.updated_at = now
        table = self._settings.database.postgres_session_resource_table
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {table} (
                        id, session_id, kind, resource_key, status, cleanup_policy,
                        created_at, updated_at, released_at, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (session_id, kind, resource_key) DO UPDATE SET
                        status = EXCLUDED.status,
                        cleanup_policy = EXCLUDED.cleanup_policy,
                        updated_at = EXCLUDED.updated_at,
                        released_at = EXCLUDED.released_at,
                        metadata = {table}.metadata || EXCLUDED.metadata
                    RETURNING *
                    """,
                    (
                        resource.id,
                        resource.session_id,
                        resource.kind.value,
                        resource.resource_key,
                        resource.status.value,
                        resource.cleanup_policy.value,
                        resource.created_at,
                        resource.updated_at,
                        resource.released_at,
                        json.dumps(make_json_safe(resource.metadata), ensure_ascii=False),
                    ),
                )
                row = cur.fetchone()
        return _resource_from_row(row)

    def _list_sync(self, session_id: str, active_only: bool) -> list[SessionResourceRecord]:
        table = self._settings.database.postgres_session_resource_table
        where = "WHERE session_id = %s"
        params: list[object] = [session_id]
        if active_only:
            where += " AND status = %s"
            params.append(SessionResourceStatus.active.value)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {table} {where} ORDER BY created_at ASC",
                    tuple(params),
                )
                rows = cur.fetchall() or []
        return [_resource_from_row(row) for row in rows]

    def _mark_status_sync(
        self,
        resource_id: str,
        status: SessionResourceStatus,
        metadata: dict,
    ) -> SessionResourceRecord | None:
        table = self._settings.database.postgres_session_resource_table
        now = datetime.utcnow()
        released_at = now if status in {SessionResourceStatus.released, SessionResourceStatus.missing} else None
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {table}
                    SET status = %s,
                        updated_at = %s,
                        released_at = COALESCE(%s, released_at),
                        metadata = metadata || %s::jsonb
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        status.value,
                        now,
                        released_at,
                        json.dumps(make_json_safe(metadata), ensure_ascii=False),
                        resource_id,
                    ),
                )
                row = cur.fetchone()
        return _resource_from_row(row) if row else None


def _resource_from_row(row: dict) -> SessionResourceRecord:
    return SessionResourceRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        kind=SessionResourceKind(row["kind"]),
        resource_key=str(row["resource_key"]),
        status=SessionResourceStatus(row["status"]),
        cleanup_policy=SessionResourceCleanupPolicy(row["cleanup_policy"]),
        created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
        updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
        released_at=ensure_utc_datetime(row.get("released_at")) if row.get("released_at") else None,
        metadata=dict(row.get("metadata") or {}),
    )
