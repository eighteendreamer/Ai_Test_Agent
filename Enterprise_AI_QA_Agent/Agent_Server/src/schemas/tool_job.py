from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


class ToolJobStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_approval = "waiting_approval"
    waiting_resource = "waiting_resource"
    waiting_children = "waiting_children"
    resume_requested = "resume_requested"
    retry_requested = "retry_requested"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    denied = "denied"
    cancelled = "cancelled"


class ToolNonExecutionEvidence(BaseModel):
    """A native handler's pre-dispatch fact, never inferred from model arguments."""
    model_config = ConfigDict(extra="forbid")

    stage: Literal["input_validation", "private_input_resolution"]
    reason: str = Field(min_length=1, max_length=120)


class ToolOutputObservation(BaseModel):
    """A returned, sanitized tool output; business assertions are still separate."""
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["completed", "partial", "failed"]
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    non_execution: ToolNonExecutionEvidence | None = None

    @model_validator(mode="after")
    def non_execution_requires_failed_return(self):
        if self.non_execution is not None and self.status != "failed":
            raise ValueError("Non-execution evidence requires a failed native return")
        return self

    @model_serializer(mode="wrap")
    def preserve_original_observation(self, handler):
        data = handler(self)
        if self.non_execution is None:
            data.pop("non_execution", None)
        return data


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
