"""Private original waiting material, not a completed result or resume grant.

No upstream counterpart (checked: RunItem admission/result checkpoints,
FrameworkRuntimePreparation, v4 §7.3/§9.2): native waiting checkpoints do not
retain application slots. Keep the material on the original formal Attempt.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import NativeToolInterrupt, approval_value_hash
from src.schemas.framework_tool_projection import FrameworkRuntimeToolProjection
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ToolApprovalRequest


class ControlledCaseApprovalStateAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    scope: Literal["controlled_case_wait_state_material_only"]
    binding: RuntimeBinding
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1, strict=True)
    run_item_execution_attempt_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    root_model_budget: dict[str, Any]
    native_messages: list[dict[str, Any]]
    natives: tuple[NativeToolInterrupt, ...] = Field(min_length=1)
    approvals: tuple[ToolApprovalRequest, ...] = Field(min_length=1)
    tool_materials: tuple[FrameworkRuntimeToolProjection, ...]
    event_references: list[dict[str, Any]] = Field(min_length=1)
    hygiene_metadata: dict[str, Any]
    state: dict[str, Any]
    retained_at: datetime
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled waiting state requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def verify(self):
        state = self.state
        if (self.retained_at.utcoffset() is None
            or self.binding.runtime_kind != "langgraph"
            or state.get("session_id") != self.binding.session_id or state.get("turn_id") != self.binding.turn_id
            or (state.get("context_bundle") or {}).get("runtime_binding") != self.binding.model_dump(mode="json")
            or state.get("control_state") != "waiting_approval" or state.get("termination_reason") != "waiting_approval"
            or state.get("continue_loop") is not False
            or any(a.status.value != "pending" or a.resolved_at is not None for a in self.approvals)
            or any(n.checkpoint_namespace != "" for n in self.natives)
            or len({n.interrupt_id for n in self.natives}) != len(self.natives)
            or len({a.id for a in self.approvals}) != len(self.natives)
            or self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"}))):
            raise ValueError("Controlled waiting state differs from its complete original material")
        return self
