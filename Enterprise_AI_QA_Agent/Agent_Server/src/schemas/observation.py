from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ObservationScope = Literal["session", "page", "artifact"]
ObservationCategory = Literal[
    "tool_execution",
    "page_state",
    "api_assertion",
    "cli_execution",
    "report_artifact",
    "knowledge_hit",
    "history_fact",
]


class ObservationRecord(BaseModel):
    id: str
    session_id: str
    turn_id: str
    trace_id: str
    tool_key: str
    status: str = "completed"
    scope: ObservationScope = "session"
    category: ObservationCategory = "tool_execution"
    title: str
    summary: str
    content: str
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SessionObservationResponse(BaseModel):
    session_id: str
    observations: list[ObservationRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
