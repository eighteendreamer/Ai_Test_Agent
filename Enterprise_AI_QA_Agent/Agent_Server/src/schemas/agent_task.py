"""Durable command and public receipt for one Agent execution phase."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.controlled_case_task_input import (
    ControlledCaseTaskInputAsset, ControlledCaseTaskInputReference, parse_controlled_case_task_input,
)
from src.schemas.session import ConversationResponse
from src.schemas.runtime_binding import RuntimeBinding, RuntimeExecutionOwner


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
LegacyAgentTaskOperation = Literal[
    "message",
    "manual_resume",
    "approval_resume",
    "coordinator_child",
    "coordinator_resume",
]
AgentTaskOperation = Literal[LegacyAgentTaskOperation, "framework_reconcile", "approval_recover", "message_recover", "case_execute"]
TERMINAL_AGENT_TASK_STATUSES = frozenset(
    {"completed", "interrupted", "failed", "cancelled", "recovery_required"}
)


class AgentTaskCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    task_type: Literal["agent_turn", "coordinator_child", "coordinator_resume"] = "agent_turn"
    operation: LegacyAgentTaskOperation = "message"
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


class AgentTaskCommandV2(BaseModel):
    """Versioned command consumed only by the v2 worker stream."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    task_type: Literal["agent_turn", "coordinator_child", "coordinator_resume"] = "agent_turn"
    operation: AgentTaskOperation = "message"
    task_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=256)
    trace_id: str = Field(min_length=1, max_length=256)
    runtime_binding: RuntimeBinding
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
    payload: dict[str, Any] = Field(default_factory=dict)
    case_execution_input: ControlledCaseTaskInputAsset | None = Field(default=None, exclude_if=lambda value: value is None)
    case_execution_reference: ControlledCaseTaskInputReference | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("case_execution_input", mode="before")
    @classmethod
    def read_case_execution_input(cls, value):
        return parse_controlled_case_task_input(value) if value is not None else None

    @model_validator(mode="after")
    def validate_binding_identity(self):
        if self.runtime_binding.session_id != self.session_id:
            raise ValueError("runtime binding session_id does not match command")
        if self.runtime_binding.turn_id != self.turn_id:
            raise ValueError("runtime binding turn_id does not match command")
        if self.runtime_binding.runtime_kind == "legacy":
            raise ValueError("v2 commands cannot use a legacy runtime binding")
        asset, reference = self.case_execution_input, self.case_execution_reference
        if self.operation == "case_execute":
            if (asset is None) == (reference is None):
                raise ValueError("case_execute requires exactly one original frozen case input or delivery reference")
            if (self.task_type != "agent_turn" or self.runtime_binding.runtime_kind != "langgraph"
                or any(value is not None for value in (self.attempt_id, self.worker_id, self.resource_id)) or self.payload):
                raise ValueError("case_execute command differs from its frozen scope or carries a second execution identity/input")
            if asset is not None and (self.runtime_binding != asset.graph_input.binding
                or self.trace_id != asset.graph_input.trace_id
                or (self.project_id, self.run_id, self.run_item_id, self.reference_id)
                != (asset.context.project_id, asset.context.run_id, asset.context.run_item_id, asset.case_version_id)):
                raise ValueError("case_execute command differs from its frozen scope")
            if reference is not None and (not all((self.project_id, self.run_id, self.run_item_id, self.reference_id))
                or self.turn_id != str(uuid5(NAMESPACE_URL,
                    f"case-run:{self.run_id}:{self.run_item_id}:{reference.run_item_execution_attempt_id}"))):
                raise ValueError("case_execute delivery differs from its original RunItem turn or fixed scope")
        elif asset is not None or reference is not None:
            raise ValueError("Only case_execute may carry frozen case input")
        return self


AgentTaskCommandEnvelope = AgentTaskCommand | AgentTaskCommandV2


def parse_agent_task_command(value: Any) -> AgentTaskCommandEnvelope:
    """Parse by explicit wire version; never downgrade v2 to the legacy schema."""
    if not isinstance(value, dict):
        raise ValueError("Agent task command must be an object")
    version = value.get("schema_version", 1)
    if version == 1:
        return AgentTaskCommand.model_validate(value)
    if version == 2:
        return AgentTaskCommandV2.model_validate(value)
    raise ValueError(f"Unsupported Agent task schema_version={version!r}")


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
    runtime_binding: RuntimeBinding | None = None
    created_at: datetime
    updated_at: datetime


class AgentTaskExecution(AgentTaskRecord):
    """Worker-only fields; never serialize this type in a public task response."""
    lease_token: str | None = None
    runtime_owner: RuntimeExecutionOwner | None = None
    fencing_token: int | None = None
    input: dict[str, Any]
    user_message_id: str | None = None


class AgentTaskAccepted(ConversationResponse):
    task: AgentTaskRecord
