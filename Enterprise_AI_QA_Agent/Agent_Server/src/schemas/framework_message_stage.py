"""Initial-message admission and immutable pre-graph execution facts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class FrameworkMessageStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
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
    created_at: datetime
    updated_at: datetime
