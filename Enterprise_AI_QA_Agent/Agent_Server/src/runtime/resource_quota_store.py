from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.resource_quota import ResourceQuotaRecord, ResourceQuotaScope, ResourceQuotaType


class ResourceQuotaStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._table = settings.database.postgres_resource_quota_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def list_project(self, project_id: str) -> list[ResourceQuotaRecord]:
        return await self.list_scope("project", project_id)

    async def list_run(self, run_id: str) -> list[ResourceQuotaRecord]:
        return await self.list_scope("run", run_id)

    async def list_scope(self, scope: ResourceQuotaScope, scope_id: str) -> list[ResourceQuotaRecord]:
        return await asyncio.to_thread(self._list_sync, scope, scope_id)

    async def list_all(self) -> list[ResourceQuotaRecord]:
        return await asyncio.to_thread(self._list_all_sync)

    async def upsert(self, project_id: str, resource_type: ResourceQuotaType, limit: int) -> ResourceQuotaRecord:
        return await self.upsert_scope("project", project_id, resource_type, limit)

    async def upsert_scope(self, scope: ResourceQuotaScope, scope_id: str, resource_type: ResourceQuotaType, limit: int) -> ResourceQuotaRecord:
        return await asyncio.to_thread(self._upsert_sync, scope, scope_id, resource_type, limit)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        scope TEXT NOT NULL DEFAULT 'project',
                        scope_id TEXT NOT NULL,
                        resource_type TEXT NOT NULL,
                        quota_limit INTEGER NOT NULL CHECK (quota_limit >= 0),
                        updated_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (scope, scope_id, resource_type)
                    )
                """)
            conn.commit()

    def _list_sync(self, scope: ResourceQuotaScope, scope_id: str) -> list[ResourceQuotaRecord]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._table} WHERE scope=%s AND scope_id=%s ORDER BY resource_type", (scope, scope_id))
                rows = cur.fetchall() or []
        return [self._from_row(row) for row in rows]

    def _list_all_sync(self) -> list[ResourceQuotaRecord]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._table} ORDER BY scope_id, resource_type")
                rows = cur.fetchall() or []
        return [self._from_row(row) for row in rows]

    def _upsert_sync(self, scope: ResourceQuotaScope, scope_id: str, resource_type: ResourceQuotaType, limit: int) -> ResourceQuotaRecord:
        now = datetime.now(timezone.utc)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {self._table} (scope, scope_id, resource_type, quota_limit, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (scope, scope_id, resource_type) DO UPDATE SET
                        quota_limit=EXCLUDED.quota_limit, updated_at=EXCLUDED.updated_at
                    RETURNING *
                """, (scope, scope_id, resource_type, limit, now))
                row = cur.fetchone()
            conn.commit()
        return self._from_row(row)

    @staticmethod
    def _from_row(row: dict) -> ResourceQuotaRecord:
        return ResourceQuotaRecord(
            scope=row["scope"], scope_id=str(row["scope_id"]),
            resource_type=row["resource_type"], limit=int(row["quota_limit"]),
            updated_at=row.get("updated_at"),
        )
