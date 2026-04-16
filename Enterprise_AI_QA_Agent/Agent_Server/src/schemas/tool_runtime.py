from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ToolExecutionStatus = Literal["completed", "partial", "failed", "waiting_approval", "denied"]


class ModelToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionRecord(BaseModel):
    call_id: str
    job_id: str | None = None
    tool_key: str
    tool_name: str
    status: ToolExecutionStatus
    summary: str
    trace_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
