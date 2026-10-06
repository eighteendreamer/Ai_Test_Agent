"""Explicit environment decisions with original-request recovery."""
import logging

from src.runtime.postgres_business_applicability_store import applicability_request_hash

logger = logging.getLogger(__name__)


class BusinessApplicabilityService:
    def __init__(self, *, store, projects, environments):
        self._store, self._projects, self._environments = store, projects, environments

    async def initialize(self):
        await self._store.initialize()

    @staticmethod
    def _owned(value, environment_id, snapshot_id):
        if value is None or value.environment_id != environment_id or value.snapshot.id != snapshot_id:
            raise KeyError("适用确认不属于当前固定环境依据")
        return value

    async def submit(self, project_id, environment_id, snapshot_id, request):
        await self._projects.get(project_id)
        digest = applicability_request_hash(project_id, environment_id, snapshot_id, request)
        prior = await self._store.get_by_request(project_id, request.request_id, digest=digest)
        if prior:
            logger.info("business_applicability_replayed", extra={"project_id": project_id, "review_id": prior.id})
            return self._owned(prior, environment_id, snapshot_id)
        try:
            saved = await self._store.save(project_id, environment_id, snapshot_id, request)
        except Exception as exc:
            logger.warning("business_applicability_write_failed", extra={"project_id": project_id,
                "environment_id": environment_id, "snapshot_id": snapshot_id, "request_id": request.request_id,
                "error_type": type(exc).__name__})
            raise
        logger.info("business_applicability_saved", extra={"project_id": project_id, "environment_id": environment_id,
            "snapshot_id": snapshot_id, "review_id": saved.id, "revision": saved.revision, "action": request.action,
            "point_count": len(request.points), "case_count": len(request.cases)})
        return saved

    async def by_request(self, project_id, environment_id, snapshot_id, request_id):
        await self._projects.get(project_id)
        return self._owned(await self._store.get_by_request(project_id, request_id), environment_id, snapshot_id)

    async def get(self, project_id, environment_id, snapshot_id, review_id):
        await self._projects.get(project_id)
        page = await self._store.read(project_id, environment_id, snapshot_id, review_id=review_id)
        return self._owned(page.items[0] if page.items else None, environment_id, snapshot_id)

    async def list(self, project_id, environment_id, snapshot_id, *, requirement_version_id, limit=20, offset=0):
        await self._environments.get_snapshot(project_id, environment_id, snapshot_id)
        return await self._store.read(project_id, environment_id, snapshot_id,
            requirement_version_id=requirement_version_id, limit=limit, offset=offset)
