from __future__ import annotations

from collections import Counter
import logging

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError

from src.schemas.resource_occupancy import ResourceOccupancyResponse

router = APIRouter(prefix="/resource-occupancy", tags=["resource-occupancy"])
logger = logging.getLogger(__name__)
_PUBLIC_LEASE_FIELDS = {
    "resource_type", "resource_id", "project_id", "session_id", "turn_id", "run_id", "run_item_id",
    "attempt_id", "worker_id", "request_id", "trace_id", "external_resource_id", "allocation_id",
    "created_at", "lease_expire_at_ms", "ttl_ms", "status", "cleanup_reason",
}


@router.get("", response_model=ResourceOccupancyResponse)
async def get_resource_occupancy(request: Request, project_id: str | None = None):
    try:
        manager = request.app.state.resource_lease_manager
        leases = await manager.list_active(project_id=project_id)
        pending = await manager.list_cleanup_pending(project_id=project_id)
        usage = await manager.usage_snapshot()
    except RedisError as exc:
        logger.warning("resource_occupancy_unavailable", extra={"project_id": project_id, "error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="资源占用暂不可用，请稍后刷新") from exc
    # Cleanup task IDs embed the lease token too. Project only the public
    # operational fields, never the internal scheduling/ownership envelope.
    leases = [{key: value for key, value in row.items() if key in _PUBLIC_LEASE_FIELDS} for row in leases]
    pending = [{key: value for key, value in row.items() if key in _PUBLIC_LEASE_FIELDS} for row in pending]
    return ResourceOccupancyResponse(
        project_id=project_id,
        active_count=len(leases),
        cleanup_pending_count=len(pending),
        cleanup_pending=pending,
        by_type=dict(Counter(str(item.get("resource_type") or "unknown") for item in leases)),
        leases=leases,
        quota_usage=usage,
    )
