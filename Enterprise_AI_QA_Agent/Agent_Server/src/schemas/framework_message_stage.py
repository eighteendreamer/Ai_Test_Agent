"""Initial-message admission and immutable pre-graph execution facts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer


class FrameworkMessageRecoveryLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    predecessor_task_id: str = Field(min_length=1)
    predecessor_attempt_id: str = Field(min_length=1)
    predecessor_owner_epoch: int = Field(ge=1)
    budget_sha256: str = Field(min_length=1)
    admitted_at: datetime
    resume_phase: Literal["submitted", "orchestrating", "orchestrated", "runtime_submitted", "finalizing", "settling"] = "submitted"
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_calls_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_reference: str | None = Field(default=None, min_length=1)
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FrameworkApprovalFinalizationLink(FrameworkMessageRecoveryLink):
    resume_phase: Literal["finalizing"] = "finalizing"
    owner_attempt_id: str | None = Field(default=None, min_length=1)
    owner_epoch: int | None = Field(default=None, ge=1)


class FrameworkApprovalFinalizationProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    scope: Literal["application_finalization"] = "application_finalization"
    origin_task_id: str = Field(min_length=1)
    origin_attempt_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    finalization: FrameworkMessageRecoveryLink


class FrameworkApprovalSettlementLink(FrameworkApprovalFinalizationLink):
    resume_phase: Literal["settling"] = "settling"


class FrameworkApprovalSettlementProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[3] = 3
    scope: Literal["application_settlement"] = "application_settlement"
    origin_task_id: str = Field(min_length=1)
    origin_attempt_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    settlement: FrameworkMessageRecoveryLink


class FrameworkMessageStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23] = 3
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
    approval_finalizations: list[FrameworkApprovalFinalizationLink] = Field(default_factory=list)
    approval_settlements: list[FrameworkApprovalSettlementLink] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_serializer(mode="wrap")
    def preserve_legacy_facts(self, handler):
        value = handler(self)
        if self.schema_version < 18 and not self.approval_finalizations:
            value.pop("approval_finalizations", None)
        if self.schema_version < 23 and not self.approval_settlements:
            value.pop("approval_settlements", None)
        return value

    @property
    def current_task_id(self):
        return self.recoveries[-1].task_id if self.recoveries else self.task_id
