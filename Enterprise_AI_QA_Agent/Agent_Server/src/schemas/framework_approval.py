"""Application identities for the pinned native interrupt delivery protocol."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def approval_value_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class FrameworkInterruptLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(min_length=1)
    binding_id: str = Field(min_length=1)
    checkpoint_namespace: str
    checkpoint_id: str = Field(min_length=1)
    graph_task_id: str = Field(min_length=1)
    task_path: str
    interrupt_id: str = Field(min_length=1)
    resume_index: int = Field(ge=0)
    source_message_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_key: str = Field(min_length=1)
    tool_job_id: str = Field(min_length=1)
    arguments: dict[str, Any]
    scope_hash: str = Field(min_length=1)
    tool_schema_version: str = Field(min_length=1)
    format_version: Literal["langgraph-1.2.12"] = "langgraph-1.2.12"


class FrameworkDecisionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str
    approval_id: str
    decision: dict[str, Any]
    decision_hash: str
    state: Literal["queued", "sending", "delivered"]
    created_at: datetime
    delivered_at: datetime | None = None
    applied_at: datetime | None = None
    proof: dict[str, Any] | None = None
    last_error: str | None = None
