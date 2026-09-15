from __future__ import annotations

from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.runtime.resource_quota_store import ResourceQuotaStore
from src.schemas.resource_quota import ResourceQuotaRecord, ResourceQuotaScope, ResourceQuotaType


class ResourceQuotaService:
    def __init__(self, store: ResourceQuotaStore, lease_manager: RedisResourceLeaseManager) -> None:
        self._store = store
        self._leases = lease_manager

    async def initialize(self) -> None:
        await self._store.initialize()
        for record in await self._store.list_all():
            if record.resource_type == "agent":
                await self._leases.configure_quota(
                    scope=record.scope, identifier=record.scope_id, limit=record.limit,
                )
            else:
                quota_scope = f"{record.scope}_resource_type"
                await self._leases.configure_quota(
                    scope=quota_scope,
                    identifier=f"{record.scope_id}:{record.resource_type}",
                    limit=record.limit,
                )

    async def list_project(self, project_id: str) -> list[ResourceQuotaRecord]:
        return await self._store.list_project(project_id)

    async def upsert(self, project_id: str, resource_type: ResourceQuotaType, limit: int) -> ResourceQuotaRecord:
        return await self.upsert_scope("project", project_id, resource_type, limit)

    async def upsert_scope(self, scope: ResourceQuotaScope, scope_id: str, resource_type: ResourceQuotaType, limit: int) -> ResourceQuotaRecord:
        record = await self._store.upsert_scope(scope, scope_id, resource_type, limit)
        if resource_type == "agent":
            await self._leases.configure_quota(scope=scope, identifier=scope_id, limit=limit)
        else:
            await self._leases.configure_quota(
                scope=f"{scope}_resource_type", identifier=f"{scope_id}:{resource_type}", limit=limit,
            )
        return record

    async def list_run(self, run_id: str) -> list[ResourceQuotaRecord]:
        return await self._store.list_run(run_id)
