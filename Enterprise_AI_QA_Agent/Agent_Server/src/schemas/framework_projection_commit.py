"""Versioned proof of the application database projection, not external effects."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FrameworkSnapshotProjectionCommit(BaseModel):
    """The actual saved row, not the caller's requested version/header."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["runtime_result_snapshot"] = "runtime_result_snapshot"
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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
