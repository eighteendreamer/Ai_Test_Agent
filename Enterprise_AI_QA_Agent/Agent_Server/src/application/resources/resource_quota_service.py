from __future__ import annotations

from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.runtime.resource_quota_store import ResourceQuotaStore
from src.schemas.resource_quota import ResourceQuotaRecord, ResourceQuotaType


class ResourceQuotaService:
    def __init__(self, store: ResourceQuotaStore, lease_manager: RedisResourceLeaseManager) -> None:
        self._store = store
        self._leases = lease_manager

    async def initialize(self) -> None:
        await self._store.initialize()
        for record in await self._store.list_all():
            if record.resource_type == "agent":
                await self._leases.configure_quota(
                    scope="project", identifier=record.scope_id, limit=record.limit,
                )
            else:
                await self._leases.configure_quota(
                    scope="project_resource_type",
                    identifier=f"{record.scope_id}:{record.resource_type}",
                    limit=record.limit,
                )

    async def list_project(self, project_id: str) -> list[ResourceQuotaRecord]:
        return await self._store.list_project(project_id)

    async def upsert(self, project_id: str, resource_type: ResourceQuotaType, limit: int) -> ResourceQuotaRecord:
        record = await self._store.upsert(project_id, resource_type, limit)
        if resource_type == "agent":
            await self._leases.configure_quota(scope="project", identifier=project_id, limit=limit)
        else:
            await self._leases.configure_quota(
                scope="project_resource_type", identifier=f"{project_id}:{resource_type}", limit=limit,
            )
        return record
