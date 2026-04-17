from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


MemoryScope = Literal["session", "global", "page", "artifact"]
MemoryKind = Literal["episodic", "semantic", "page_knowledge", "verification", "artifact"]


class MemoryPoint(BaseModel):
    id: str
    scope: MemoryScope = "session"
    kind: MemoryKind = "episodic"
    content: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    score: float | None = None
    session_id: str | None = None
    turn_id: str | None = None
    trace_id: str | None = None
    source: str | None = None
    stale: bool = False
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchRequest(BaseModel):
    query: str
    session_id: str | None = None
    trace_id: str | None = None
    scope: MemoryScope | None = None
    scopes: list[MemoryScope] = Field(default_factory=list)
    kinds: list[MemoryKind] = Field(default_factory=list)
    top_k: int = 6
    include_stale: bool = False
    tags: list[str] = Field(default_factory=list)
    day_window: int = 7
    metadata_filters: dict[str, Any] = Field(default_factory=dict)


class MemorySearchResult(BaseModel):
    query: str
    hits: list[MemoryPoint] = Field(default_factory=list)
    prompt_blocks: list[str] = Field(default_factory=list)
    source_count: int = 0
    total_session_docs: int = 0
    total_global_docs: int = 0
    total_docs: int = 0
    backend: str = ""


class MemoryWriteRequest(BaseModel):
    scope: MemoryScope = "session"
    kind: MemoryKind = "episodic"
    content: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    session_id: str | None = None
    turn_id: str | None = None
    trace_id: str | None = None
    source: str | None = None
    stale: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
