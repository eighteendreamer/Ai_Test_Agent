from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KnowledgeProjectSummary(BaseModel):
    project_id: str | None = None
    project_scope: str
    page_count: int = 0
    element_count: int = 0
    entity_count: int = 0
    edge_count: int = 0
    latest_updated_at: datetime | None = None


class KnowledgeGraphSummary(BaseModel):
    project_id: str | None = None
    project_scope: str
    page_count: int = 0
    element_count: int = 0
    entity_count: int = 0
    edge_count: int = 0
    relation_counts: dict[str, int] = Field(default_factory=dict)
    latest_updated_at: datetime | None = None


class KnowledgeGraphNode(BaseModel):
    id: str
    label: str
    kind: str
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraphResponse(BaseModel):
    summary: KnowledgeGraphSummary
    nodes: list[KnowledgeGraphNode] = Field(default_factory=list)
    edges: list[KnowledgeGraphEdge] = Field(default_factory=list)


class KnowledgeProjectDeleteResponse(BaseModel):
    ok: bool = True
    project_id: str | None = None
    project_scope: str
    deleted_counts: dict[str, int] = Field(default_factory=dict)
    message: str = ""
