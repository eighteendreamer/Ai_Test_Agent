from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TestSuiteStatus = Literal["active", "archived"]


class TestSuiteRecord(BaseModel):
    id: str
    project_id: str
    name: str
    description: str | None = None
    status: TestSuiteStatus = "active"
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class TestSuiteItemRecord(BaseModel):
    id: str
    suite_id: str
    case_id: str
    case_version_id: str
    position: int = Field(ge=1)


class TestSuiteItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    case_version_id: str = Field(min_length=1)


class TestSuiteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=2000)
    items: list[TestSuiteItemCreateRequest] = Field(min_length=1, max_length=10000)


class TestSuiteBundle(BaseModel):
    suite: TestSuiteRecord
    items: list[TestSuiteItemRecord]


class TestSuitePage(BaseModel):
    items: list[TestSuiteBundle]
    limit: int
    offset: int
    has_more: bool
