from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Request

from src.schemas.resource_occupancy import ResourceOccupancyResponse

router = APIRouter(prefix="/resource-occupancy", tags=["resource-occupancy"])


@router.get("", response_model=ResourceOccupancyResponse)
async def get_resource_occupancy(request: Request, project_id: str | None = None):
    leases = await request.app.state.resource_lease_manager.list_active(project_id=project_id)
    return ResourceOccupancyResponse(
        project_id=project_id,
        active_count=len(leases),
        by_type=dict(Counter(str(item.get("resource_type") or "unknown") for item in leases)),
        leases=leases,
        quota_usage=await request.app.state.resource_lease_manager.usage_snapshot(),
    )
