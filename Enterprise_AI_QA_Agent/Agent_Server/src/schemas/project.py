from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ProjectStatus = Literal["active", "archived"]


class ProjectRecord(BaseModel):
    id: str
    project_key: str
    name: str
    description: str | None = None
    base_url: str | None = None
    graph_scope_key: str | None = None
    status: ProjectStatus = "active"
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=2048)
    graph_scope_key: str | None = Field(default=None, max_length=160)

    @field_validator("project_key")
    @classmethod
    def normalize_project_key(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("description", "base_url", "graph_scope_key")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = value.strip() if isinstance(value, str) else None
        return normalized or None


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=2048)
    graph_scope_key: str | None = Field(default=None, max_length=160)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", "base_url", "graph_scope_key")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = value.strip() if isinstance(value, str) else None
        return normalized or None


class ProjectPage(BaseModel):
    items: list[ProjectRecord]
    limit: int
    offset: int
    has_more: bool


class ProjectGraphOverview(BaseModel):
    available: bool = True
    project_scope: str | None = None
    page_count: int = 0
    element_count: int = 0
    entity_count: int = 0
    edge_count: int = 0
    error: str | None = None


class ProjectOverview(BaseModel):
    project: ProjectRecord
    api_doc_count: int = 0
    session_count: int = 0
    graph: ProjectGraphOverview = Field(default_factory=ProjectGraphOverview)
