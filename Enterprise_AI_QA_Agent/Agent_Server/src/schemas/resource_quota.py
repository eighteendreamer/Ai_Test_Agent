from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ResourceQuotaScope = Literal["project", "run"]
ResourceQuotaType = Literal["agent", "browser", "docker", "test_account", "environment"]


class ResourceQuotaRecord(BaseModel):
    scope: ResourceQuotaScope = "project"
    scope_id: str
    resource_type: ResourceQuotaType
    limit: int = Field(ge=0)
    updated_at: datetime | None = None


class ResourceQuotaUpsertRequest(BaseModel):
    resource_type: ResourceQuotaType
    limit: int = Field(ge=0, le=10000)
