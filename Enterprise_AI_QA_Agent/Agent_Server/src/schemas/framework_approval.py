"""Application identities for the pinned native interrupt delivery protocol."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field
from src.schemas.session import ToolApprovalRequest
from src.schemas.tool_job import ToolJobRecord
from src.schemas.framework_harness_settings import FrameworkHarnessSettings


def approval_value_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class FrameworkToolCallIdentity(BaseModel):
    """Stable source identity shared by interrupt projection and tool execution."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_namespace: str
    source_message_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any]


class NativeToolInterrupt(FrameworkToolCallIdentity):
    """Execution identity read from a pending native tool task, before projection."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_id: str = Field(min_length=1)
    graph_task_id: str = Field(min_length=1)
    task_path: str
    interrupt_id: str = Field(min_length=1)
    resume_index: int = Field(ge=0)
    format_version: Literal["langgraph-1.2.12"] = "langgraph-1.2.12"


class FrameworkInterruptLink(NativeToolInterrupt):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(min_length=1)
    binding_id: str = Field(min_length=1)
    tool_key: str = Field(min_length=1)
    tool_job_id: str = Field(min_length=1)
    scope_hash: str = Field(min_length=1)
    tool_schema_version: str = Field(min_length=1)
    business_call_id: str | None = Field(default=None, min_length=1)


def framework_tool_call_id(binding, native: FrameworkToolCallIdentity) -> str:
    return "framework:" + approval_value_hash([binding.binding_id, binding.turn_id,
        native.checkpoint_namespace, native.source_message_id, native.tool_call_id])


def framework_approval_id(binding, native: FrameworkToolCallIdentity) -> str:
    return str(uuid5(NAMESPACE_URL, "framework-approval:" + framework_tool_call_id(binding, native)))


class PreparedFrameworkApproval(BaseModel):
    """Unpublished business projection, committed only under a runtime owner."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval: ToolApprovalRequest
    job: ToolJobRecord
    link: FrameworkInterruptLink


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


class FrameworkContinuationPlan(PreparedFrameworkApproval):
    """One validated decision; invocation mode derives from durable delivery."""

    receipt: FrameworkDecisionReceipt
    harness_settings: FrameworkHarnessSettings | None = Field(default=None, exclude_if=lambda value: value is None)

    @property
    def continue_from_checkpoint(self) -> bool:
        return self.receipt.state == "delivered"

    @property
    def resume(self) -> dict[str, Any] | None:
        if self.continue_from_checkpoint:
            return None
        return {self.link.interrupt_id: dict(self.receipt.decision)}
