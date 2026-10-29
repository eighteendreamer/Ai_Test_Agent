"""Versioned proof of the application database projection, not external effects."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from src.schemas.framework_approval import approval_value_hash
from src.schemas.memory import MemoryWriteRequest


TURN_MEMORY_INTENT_KEY = "post_commit_memory_intent"
TURN_MEMORY_PLAN_KEY = "post_commit_memory_plan"
TURN_MEMORY_EXECUTION_KEY = "post_commit_memory_execution"


def post_commit_effect_id(session_id: str, turn_id: str, snapshot_id: str, effect_kind: str) -> str:
    return str(uuid5(NAMESPACE_URL,
        f"enterprise-ai-qa-agent:post-commit-effect:{session_id}:{turn_id}:{snapshot_id}:{effect_kind}"))


def turn_memory_request_id(effect_id: str, ordinal: int) -> str:
    return str(uuid5(NAMESPACE_URL,
        f"enterprise-ai-qa-agent:turn-memory-request:{effect_id}:{ordinal}"))


def turn_memory_requested(result) -> bool:
    return bool(result.output_text.strip()
        and str((result.state.get("model_response_summary") or {}).get("mode") or "ok") == "ok")


class FrameworkTurnMemoryIntent(BaseModel):
    """An atomic request bound to original input; never an execution receipt or retry permission."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    scope: Literal["derived_turn_memory_intent_only"] = "derived_turn_memory_intent_only"
    derivation_contract_version: Literal[1] = 1
    effect_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1)
    requested: StrictBool

    @field_validator("schema_version", "derivation_contract_version", mode="before")
    @classmethod
    def require_integer_versions(cls, value):
        if type(value) is not int:
            raise ValueError("Memory intent requires explicit integer versions")
        return value

    @classmethod
    def from_result_asset(cls, asset):
        binding, result = asset.binding, asset.runtime_result
        return cls(effect_id=post_commit_effect_id(binding.session_id, binding.turn_id, result.snapshot.id, "turn_memory"),
            session_id=binding.session_id, turn_id=binding.turn_id, binding_fingerprint=binding.fingerprint,
            runtime_result_asset_sha256=asset.asset_sha256, source_snapshot_id=result.snapshot.id,
            requested=turn_memory_requested(result))


class FrameworkTurnMemoryRequest(BaseModel):
    """One frozen Memory request; it contains intent only, never execution state."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    request_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    request: "MemoryWriteRequest"

    @model_validator(mode="before")
    @classmethod
    def reject_nested_request_extras(cls, value):
        if isinstance(value, dict) and isinstance(value.get("request"), dict):
            extras = set(value["request"]) - set(MemoryWriteRequest.model_fields)
            if extras:
                raise ValueError("Frozen Memory request contains unknown execution fields")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Memory request requires an explicit integer version")
        return value

    @field_validator("ordinal", mode="before")
    @classmethod
    def require_integer_ordinal(cls, value):
        if type(value) is not int:
            raise ValueError("Memory request requires an explicit integer ordinal")
        return value

    @model_validator(mode="after")
    def verify(self):
        if self.request.turn_lease_token or self.request.compaction_key or self.request.compaction_owner_id:
            raise ValueError("Frozen Memory request cannot carry execution fencing or compaction ownership")
        forbidden = {
            "memory_ids", "completed_at", "execution_authority", "retry_authority", "status",
            "embedding", "embedding_model", "embedding_provider", "embedding_adapter",
            "embedding_source_dimension", "embedding_dimension", "embedding_version", "normalized",
        }
        if forbidden.intersection(self.request.metadata):
            raise ValueError("Frozen Memory request contains execution-only metadata")
        return self


class FrameworkTurnMemoryPlan(BaseModel):
    """Atomic per-request Memory intent bound to the original runtime result."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    scope: Literal["derived_turn_memory_plan_only"] = "derived_turn_memory_plan_only"
    derivation_contract_version: Literal[1] = 1
    effect_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1)
    requested: StrictBool
    requests: list[FrameworkTurnMemoryRequest]
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("schema_version", "derivation_contract_version", mode="before")
    @classmethod
    def require_integer_versions(cls, value):
        if type(value) is not int:
            raise ValueError("Memory plan requires explicit integer versions")
        return value

    @model_validator(mode="after")
    def verify(self):
        expected_effect = post_commit_effect_id(self.session_id, self.turn_id, self.source_snapshot_id, "turn_memory")
        if self.effect_id != expected_effect:
            raise ValueError("Memory plan effect identity differs from its source result")
        if [item.ordinal for item in self.requests] != list(range(len(self.requests))):
            raise ValueError("Memory plan request ordinals are not contiguous")
        request_ids = [item.request_id for item in self.requests]
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("Memory plan contains duplicate request identities")
        for item in self.requests:
            if item.request_id != turn_memory_request_id(self.effect_id, item.ordinal):
                raise ValueError("Memory plan request identity differs from its ordinal")
            if item.request.session_id != self.session_id or item.request.turn_id != self.turn_id:
                raise ValueError("Memory plan request is bound to another Turn")
        if self.requested != bool(self.requests):
            raise ValueError("Memory plan requested flag differs from its frozen requests")
        if self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"})):
            raise ValueError("Memory plan digest differs from its contents")
        return self


class FrameworkTurnMemoryRowReceipt(BaseModel):
    """Receipt for one durable Memory row, separate from the frozen plan."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    request_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    status: Literal["committed"] = "committed"
    memory_id: str = Field(min_length=1)
    row_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime

    @field_validator("schema_version", "ordinal", mode="before")
    @classmethod
    def require_integer_fields(cls, value):
        if type(value) is not int:
            raise ValueError("Memory row receipt requires explicit integer fields")
        return value


class FrameworkTurnMemoryExecution(BaseModel):
    """Mutable derived execution state; it never changes the immutable plan."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["derived_turn_memory_execution_receipt_only"] = "derived_turn_memory_execution_receipt_only"
    effect_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1)
    plan_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["running", "completed"]
    row_receipts: list[FrameworkTurnMemoryRowReceipt] = Field(default_factory=list)
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Memory execution requires an explicit integer version")
        return value

    @model_validator(mode="after")
    def verify(self):
        request_ids = [item.request_id for item in self.row_receipts]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("Memory execution contains duplicate row receipts")
        if self.state == "completed" and not self.row_receipts:
            # An empty requested plan is a valid completed derived effect.
            pass
        if self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"})):
            raise ValueError("Memory execution digest differs from its contents")
        return self


class FrameworkSnapshotProjectionCommit(BaseModel):
    """The actual saved row, not the caller's requested version/header."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["runtime_result_snapshot"] = "runtime_result_snapshot"
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FrameworkEventProjectionCommits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["runtime_result_events"] = "runtime_result_events"
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_hashes: dict[str, str] = Field(min_length=1)


class FrameworkProjectionCommit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["session_messages_completion_events"] = "session_messages_completion_events"
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_message_id: str | None = None
    message_hashes: dict[str, str] = Field(min_length=1)
    event_hashes: dict[str, str] = Field(min_length=1)
    model_calls_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_jobs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    control: dict[str, str | bool]
    committed_at: datetime
