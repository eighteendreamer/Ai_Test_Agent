from __future__ import annotations

import logging

from src.runtime.resource_lease_manager import RedisResourceLeaseManager
from src.runtime.resource_quota_store import ResourceQuotaStore
from src.schemas.resource_quota import ResourceQuotaRecord, ResourceQuotaScope, ResourceQuotaType

logger = logging.getLogger(__name__)


class ResourceQuotaService:
    def __init__(self, store: ResourceQuotaStore, lease_manager: RedisResourceLeaseManager) -> None:
        self._store = store
        self._leases = lease_manager

    async def initialize(self) -> None:
        await self._store.initialize()
        for record in await self._store.list_all():
            await self._apply(record)

    async def _apply(self, record: ResourceQuotaRecord) -> None:
        await self._leases.configure_quota(
            scope=f"{record.scope}_resource_type",
            identifier=f"{record.scope_id}:{record.resource_type}", limit=record.limit,
        )
        if record.resource_type == "agent":
            # Remove the former aggregate-resource mapping when bootstrapping
            # persisted task quotas; it must not block a task's child resources.
            await self._leases.configure_quota(scope=record.scope, identifier=record.scope_id, limit=0)
        logger.info("resource_quota_applied", extra={
            "quota_scope": record.scope, "scope_id": record.scope_id,
            "resource_type": record.resource_type, "quota_limit": record.limit,
        })

    async def list_project(self, project_id: str) -> list[ResourceQuotaRecord]:
        return await self._store.list_project(project_id)

    async def upsert(self, project_id: str, resource_type: ResourceQuotaType, limit: int) -> ResourceQuotaRecord:
        return await self.upsert_scope("project", project_id, resource_type, limit)

    async def upsert_scope(self, scope: ResourceQuotaScope, scope_id: str, resource_type: ResourceQuotaType, limit: int) -> ResourceQuotaRecord:
        record = await self._store.upsert_scope(scope, scope_id, resource_type, limit)
        await self._apply(record)
        return record

    async def list_run(self, run_id: str) -> list[ResourceQuotaRecord]:
        return await self._store.list_run(run_id)
