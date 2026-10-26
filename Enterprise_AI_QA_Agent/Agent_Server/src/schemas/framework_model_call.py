"""Durable model accounting values; credentials and execution tokens stay out."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, computed_field, field_validator, model_validator

from src.schemas.model_config import ModelTransport


class _ModelCallValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class FrameworkModelBudget(_ModelCallValue):
    schema_version: Literal[1] = 1
    max_calls: StrictInt = Field(ge=1, le=100_000)
    max_tokens: StrictInt = Field(ge=1, le=1_000_000_000_000)
    deadline_at: datetime
    model_config_fingerprints: dict[str, str] = Field(min_length=1)

    @field_validator("deadline_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Model budget deadline must include a timezone")
        return value

    @field_validator("model_config_fingerprints")
    @classmethod
    def require_model_identities(cls, value):
        if any(not key.strip() or not fingerprint.strip() for key, fingerprint in value.items()):
            raise ValueError("Model budget requires explicit model identities")
        return value


class FrameworkModelCallIntent(_ModelCallValue):
    position_version: Literal[1] | None = None
    attempt_id: str = Field(min_length=1, max_length=160)
    logical_call_id: str = Field(min_length=1, max_length=160)
    branch_id: str = Field(min_length=1, max_length=160)
    parent_branch_id: str | None = Field(default=None, min_length=1, max_length=160)
    purpose: Literal["agent", "subagent", "summary"]
    model_key: str = Field(min_length=1, max_length=160)
    model_config_fingerprint: str = Field(min_length=1, max_length=128)
    transport: ModelTransport
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reserved_input_tokens: StrictInt = Field(ge=0, le=1_000_000_000_000)
    reserved_output_tokens: StrictInt = Field(ge=1, le=1_000_000_000_000)
    retry_of_attempt_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_lineage(self):
        if self.parent_branch_id == self.branch_id:
            raise ValueError("Model branch cannot be its own parent")
        if self.purpose == "subagent" and self.parent_branch_id is None:
            raise ValueError("Subagent call requires its parent branch")
        if self.retry_of_attempt_id == self.attempt_id:
            raise ValueError("Model retry must have a new attempt identity")
        return self

    @property
    def reserved_tokens(self) -> int:
        return self.reserved_input_tokens + self.reserved_output_tokens


class FrameworkModelUsage(_ModelCallValue):
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self):
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("Model total usage cannot be smaller than input plus output")
        return self


class FrameworkModelObject(_ModelCallValue):
    storage_uri: str = Field(pattern=r"^rustfs://[^/]+/.+")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: StrictInt = Field(ge=1)


class FrameworkModelResponseObservation(_ModelCallValue):
    schema_version: Literal[1] = 1
    observation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    response: FrameworkModelObject
    response_kind: Literal["langchain_messages", "model_invocation_result"]
    authoritative: Literal[False] = False
    observed_at: datetime


class FrameworkModelWireObservation(_ModelCallValue):
    schema_version: Literal[1] = 1
    observation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: FrameworkModelObject | None = None
    response: FrameworkModelObject | None = None
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_status_code: StrictInt | None = Field(default=None, ge=100, le=599)
    request_body_truncated: bool = False
    response_body_truncated: bool = False
    response_complete: bool = False
    attempt_sequence: StrictInt = Field(ge=1)
    authoritative: Literal[False] = False
    observed_at: datetime


class FrameworkModelReservationOwner(_ModelCallValue):
    owner_epoch: StrictInt = Field(ge=1)
    owner_task_id: str = Field(min_length=1)
    owner_attempt_id: str = Field(min_length=1)
    observer_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FrameworkModelReservationTransfer(_ModelCallValue):
    predecessor: FrameworkModelReservationOwner
    successor: FrameworkModelReservationOwner
    predecessor_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transferred_at: datetime

    @model_validator(mode="after")
    def validate_transfer(self):
        if self.successor.owner_epoch <= self.predecessor.owner_epoch:
            raise ValueError("Model reservation transfer requires a newer execution owner")
        if self.transferred_at.tzinfo is None or self.transferred_at.utcoffset() is None:
            raise ValueError("Model reservation transfer time must include a timezone")
        return self


class FrameworkModelCallRecord(_ModelCallValue):
    schema_version: Literal[1, 2] = 1
    binding_id: str
    intent: FrameworkModelCallIntent
    owner_epoch: int
    owner_task_id: str
    owner_attempt_id: str
    observer_token_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request: FrameworkModelObject | None = None
    response_observations: list[FrameworkModelResponseObservation] = Field(default_factory=list)
    wire_observations: list[FrameworkModelWireObservation] = Field(default_factory=list)
    phase: Literal["ready", "invoking", "completed", "unknown"]
    usage: FrameworkModelUsage | None = None
    outcome_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    settled_at: datetime | None = None
    reservation_transfers: list[FrameworkModelReservationTransfer] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_transfers(self):
        if self.schema_version == 1 and self.reservation_transfers:
            raise ValueError("Model call v1 has no reservation transfer contract")
        for previous, current in zip(self.reservation_transfers, self.reservation_transfers[1:]):
            if previous.successor != current.predecessor:
                raise ValueError("Model reservation transfer lineage is not continuous")
        if self.reservation_transfers:
            successor = self.reservation_transfers[-1].successor
            if any(getattr(self, field) != getattr(successor, field) for field in type(successor).model_fields):
                raise ValueError("Model reservation owner differs from its transfer lineage")
        return self


class FrameworkModelBudgetSnapshot(_ModelCallValue):
    policy: FrameworkModelBudget
    calls: list[FrameworkModelCallRecord]

    @computed_field
    @property
    def reserved_calls(self) -> int:
        # A prepared or unknown attempt still occupies its original call slot.
        return len(self.calls)

    @computed_field
    @property
    def charged_tokens(self) -> int:
        return sum(call.usage.total_tokens if call.usage is not None else call.intent.reserved_tokens
            for call in self.calls)

    @computed_field
    @property
    def actual_tokens(self) -> int:
        return sum(call.usage.total_tokens for call in self.calls if call.usage is not None)

    @computed_field
    @property
    def unknown_calls(self) -> int:
        return sum(call.phase == "unknown" for call in self.calls)

    @computed_field
    @property
    def unmeasured_calls(self) -> int:
        return sum(call.phase == "completed" and call.usage is None for call in self.calls)
