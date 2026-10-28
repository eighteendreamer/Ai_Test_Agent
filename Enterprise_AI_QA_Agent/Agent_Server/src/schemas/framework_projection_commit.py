"""Versioned proof of the application database projection, not external effects."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


TURN_MEMORY_INTENT_KEY = "post_commit_memory_intent"


def post_commit_effect_id(session_id: str, turn_id: str, snapshot_id: str, effect_kind: str) -> str:
    return str(uuid5(NAMESPACE_URL,
        f"enterprise-ai-qa-agent:post-commit-effect:{session_id}:{turn_id}:{snapshot_id}:{effect_kind}"))


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
            requested=bool(result.output_text.strip()
                and str((result.state.get("model_response_summary") or {}).get("mode") or "ok") == "ok"))


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
