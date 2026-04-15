from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ToolJobStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_approval = "waiting_approval"
    resume_requested = "resume_requested"
    retry_requested = "retry_requested"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ToolArtifactRecord(BaseModel):
    id: str
    tool_job_id: str
    session_id: str
    turn_id: str
    trace_id: str
    tool_key: str
    artifact_type: str
    label: str | None = None
    path: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolJobRecord(BaseModel):
    id: str
    session_id: str
    turn_id: str
    trace_id: str
    call_id: str
    tool_key: str
    tool_name: str
    status: ToolJobStatus
    attempt: int = 1
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error_message: str | None = None
    artifact_count: int = 0
    created_at: datetime
    updated_at: datetime
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolJobActionRequest(BaseModel):
    reason: str | None = None


class ToolJobDetail(ToolJobRecord):
    artifacts: list[ToolArtifactRecord] = Field(default_factory=list)
