"""Read-only recovery facts. No prompt, tool payload or execution credential."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.framework_model_call import (
    FrameworkModelBudget, FrameworkModelObject, FrameworkModelResponseObservation, FrameworkModelUsage,
)
from src.schemas.runtime_binding import RuntimeBinding


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageTaskFact(_Fact):
    id: str
    status: str
    error_code: str | None
    attempt_id: str | None
    attempt_no: int
    attempt_status: str | None
    interrupt_requested: bool


class MessageInputFact(_Fact):
    payload_present: bool
    payload_hash: str
    payload_hash_matches: bool
    user_message_id: str | None
    user_message_present: bool
    user_message_matches: bool


class MessageOwnerFact(_Fact):
    present: bool
    epoch: int | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    execution_state: str | None = None
    expires_at: datetime | None = None
    matches_origin: bool = False
    lease_active: bool = False


class MessageModelBudgetFact(_Fact):
    policy: FrameworkModelBudget | None = None
    ledger_rows: int = 0
    accounting_complete: bool = False
    reserved_calls: int | None = None
    charged_tokens: int | None = None
    actual_tokens: int | None = None


class MessageModelWireFact(_Fact):
    observation_id: str
    request: FrameworkModelObject | None
    response: FrameworkModelObject | None
    response_status_code: int | None
    request_body_truncated: bool
    response_body_truncated: bool
    response_complete: bool
    attempt_sequence: int
    authoritative: Literal[False] = False
    observed_at: datetime


class MessageModelCallFact(_Fact):
    attempt_id: str
    logical_call_id: str
    branch_id: str
    purpose: str
    owner_task_id: str
    owner_attempt_id: str
    owner_epoch: int
    reservation_transfer_count: int = 0
    reservation_origin_task_id: str | None = None
    reservation_origin_attempt_id: str | None = None
    reservation_origin_epoch: int | None = None
    phase: str
    request: FrameworkModelObject | None
    request_sha256: str
    usage: FrameworkModelUsage | None
    response_observations: list[FrameworkModelResponseObservation]
    wire_observations: list[MessageModelWireFact]
    created_at: datetime
    started_at: datetime | None
    settled_at: datetime | None


class MessagePendingWriteFact(_Fact):
    task_id: str
    index: int
    channel: str


class MessageCheckpointFact(_Fact):
    namespace: str
    checkpoint_id: str
    parent_checkpoint_id: str | None
    step: int | None
    pending_writes: list[MessagePendingWriteFact] = Field(default_factory=list)


class MessageToolJobFact(_Fact):
    id: str
    call_id: str
    tool_key: str
    status: str
    binding_matches: bool
    started_at: datetime | None
    completed_at: datetime | None
    output_present: bool
    execution_task_id: str | None
    execution_attempt_id: str | None
    execution_epoch: int | None


class MessageProjectionFact(_Fact):
    session_status: str
    state_revision: int
    pending_turn_id: str | None
    pending_binding_matches: bool | None
    snapshot_id: str | None
    snapshot_stage: str | None
    snapshot_version: int | None
    framework_message_count: int


class MessageStageFact(_Fact):
    phase: str
    execution_task_id: str | None = None
    recovery_count: int = 0
    owner_epoch: int | None
    owner_attempt_id: str | None
    source_sha256: str | None
    execution_sha256: str | None
    result_reference: str | None


class FrameworkMessageDiagnostic(_Fact):
    schema_version: Literal[1] = 1
    observed_at: datetime
    snapshot_id: str
    recovery_status: Literal["recovery_required"] = "recovery_required"
    automatic_replay_allowed: Literal[False] = False
    evidence_objects_verified: Literal[False] = False
    task: MessageTaskFact
    binding: RuntimeBinding
    input: MessageInputFact
    owner: MessageOwnerFact
    stage: MessageStageFact | None = None
    model_budget: MessageModelBudgetFact
    model_calls: list[MessageModelCallFact]
    checkpoints: list[MessageCheckpointFact]
    tool_jobs: list[MessageToolJobFact]
    projection: MessageProjectionFact
    issues: list[str]
