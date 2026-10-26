"""Initial-message admission and immutable pre-graph execution facts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrameworkMessageRecoveryLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    predecessor_task_id: str = Field(min_length=1)
    predecessor_attempt_id: str = Field(min_length=1)
    predecessor_owner_epoch: int = Field(ge=1)
    budget_sha256: str = Field(min_length=1)
    admitted_at: datetime
    resume_phase: Literal["submitted", "orchestrating", "orchestrated", "runtime_submitted"] = "submitted"
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_calls_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_reference: str | None = Field(default=None, min_length=1)
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FrameworkMessageStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9] = 3
    task_id: str
    binding_id: str
    input_sha256: str
    user_message_id: str
    phase: Literal["submitted", "orchestrating", "orchestrated", "runtime_submitted", "projected"]
    owner_epoch: int | None = None
    owner_attempt_id: str | None = None
    source_manifest: dict[str, Any] | None = None
    source_sha256: str | None = None
    execution_request: dict[str, Any] | None = None
    execution_sha256: str | None = None
    result_reference: str | None = None
    recoveries: list[FrameworkMessageRecoveryLink] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @property
    def current_task_id(self):
        return self.recoveries[-1].task_id if self.recoveries else self.task_id
