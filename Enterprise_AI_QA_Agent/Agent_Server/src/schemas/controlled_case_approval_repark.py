"""Successor parking is separate from the immutable initial wait receipt."""
from typing import Literal

from pydantic import Field, model_validator

from src.schemas.controlled_case_approval_wait import ControlledCaseApprovalWaitReceipt


class ControlledCaseApprovalReparkReceipt(ControlledCaseApprovalWaitReceipt):
    scope: Literal["controlled_case_atomic_approval_repark_only"]
    origin_task_id: str = Field(min_length=1)
    parent_wait_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_successor(self):
        if self.task_id == self.origin_task_id:
            raise ValueError("Controlled re-park cannot replace its initial producer")
        return self
