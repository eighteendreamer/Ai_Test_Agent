"""Immutable post-application material, not a parking or execution receipt.

No upstream counterpart (checked: original waiting asset, approval application
material, v4 §7.3/§9.2): a formal successor cannot overwrite its root wait.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.controlled_case_approval_submission import ControlledCaseApprovalAdmission
from src.schemas.framework_approval import FrameworkDecisionReceipt, NativeToolInterrupt, approval_value_hash
from src.schemas.framework_tool_projection import FrameworkRuntimeToolProjection
from src.schemas.runtime_binding import RuntimeBinding


class ControlledCaseApprovalProgressAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    scope: Literal["controlled_case_approval_progress_material_only"]
    binding: RuntimeBinding
    origin_task_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1, strict=True)
    parent_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission: ControlledCaseApprovalAdmission
    decision_receipt: FrameworkDecisionReceipt
    natives: tuple[NativeToolInterrupt, ...] = Field(min_length=1)
    native_messages: list[dict[str, Any]]
    tool_materials: tuple[FrameworkRuntimeToolProjection, ...] = Field(min_length=1)
    root_model_budget: dict[str, Any]
    hygiene_metadata: dict[str, Any]
    event_references: list[dict[str, Any]] = Field(min_length=1)
    state: dict[str, Any]
    retained_at: datetime
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled approval progress requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def verify(self):
        proof, receipt, state = self.admission, self.decision_receipt, self.state
        if (self.retained_at.utcoffset() is None or receipt.applied_at is None
            or receipt.delivered_at is None or receipt.state != "delivered" or receipt.applied_at > self.retained_at
            or (proof.task_id, proof.origin_task_id, proof.binding_fingerprint, proof.approval_id, proof.decision_id)
                != (self.task_id, self.origin_task_id, self.binding.fingerprint, receipt.approval_id, receipt.decision_id)
            or proof.decision_sha256 != receipt.decision_hash
            or state.get("session_id") != self.binding.session_id or state.get("turn_id") != self.binding.turn_id
            or state.get("control_state") != "resuming" or state.get("pending_turn") != {}
            or state.get("termination_reason") != "" or state.get("continue_loop") is not True
            or (state.get("context_bundle") or {}).get("runtime_binding") != self.binding.model_dump(mode="json")
            or len({n.interrupt_id for n in self.natives}) != len(self.natives)
            or self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"}))):
            raise ValueError("Controlled approval progress differs from its original application material")
        return self
