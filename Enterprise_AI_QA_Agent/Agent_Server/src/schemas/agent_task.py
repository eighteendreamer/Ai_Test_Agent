"""Durable command and public receipt for one Agent execution phase."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.session import ConversationResponse


AgentTaskStatus = Literal[
    "queued",
    "waiting_resource",
    "claimed",
    "running",
    "completed",
    "interrupted",
    "failed",
    "cancelled",
    "recovery_required",
    "waiting_children",
    "coordinator_resuming",
]
AgentTaskOperation = Literal[
    "message",
    "manual_resume",
    "approval_resume",
    "coordinator_child",
    "coordinator_resume",
]
TERMINAL_AGENT_TASK_STATUSES = frozenset(
    {"completed", "interrupted", "failed", "cancelled", "recovery_required"}
)


class AgentTaskCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    task_type: Literal["agent_turn", "coordinator_child", "coordinator_resume"] = "agent_turn"
    operation: AgentTaskOperation = "message"
    task_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=256)
    trace_id: str = Field(min_length=1, max_length=256)
    parent_task_id: str | None = None
    root_trace_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    run_item_id: str | None = None
    attempt_id: str | None = None
    worker_id: str | None = None
    resource_id: str | None = None
    reference_id: str | None = None
    priority: int = Field(default=50, ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, str] = Field(
        default_factory=lambda: {"input_source": "postgres"}
    )


class AgentTaskRecord(BaseModel):
    id: str
    operation: AgentTaskOperation = "message"
    session_id: str
    turn_id: str
    project_id: str | None = None
    request_id: str
    trace_id: str
    run_id: str | None = None
    run_item_id: str | None = None
    status: AgentTaskStatus
    priority: int = 50
    attempt_id: str | None = None
    attempt_no: int = 0
    worker_id: str | None = None
    waiting_reason: str | None = None
    result_status: str | None = None
    error_code: str | None = None
    resource_id: str | None = None
    reference_id: str | None = None
    parent_task_id: str | None = None
    root_trace_id: str | None = None
    result_payload: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class AgentTaskExecution(AgentTaskRecord):
    """Worker-only fields; never serialize this type in a public task response."""
    lease_token: str | None = None
    fencing_token: int | None = None
    input: dict[str, Any]
    user_message_id: str | None = None


class AgentTaskAccepted(ConversationResponse):
    task: AgentTaskRecord
