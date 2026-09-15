from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResourceOccupancyResponse(BaseModel):
    project_id: str | None = None
    active_count: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    leases: list[dict[str, Any]] = Field(default_factory=list)
    quota_usage: dict[str, int] = Field(default_factory=dict)
