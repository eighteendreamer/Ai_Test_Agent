"""Dedicated decision lineage, not a lease or native delivery proof."""
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash


def case_approval_task_id(origin_task_id, approval_id):
    return str(uuid5(NAMESPACE_URL, f"controlled-case:approval:{origin_task_id}:{approval_id}"))


class ControlledCaseApprovalReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    origin_task_id: str = Field(min_length=1)
    origin_attempt_id: str = Field(min_length=1)
    run_item_execution_attempt_id: str = Field(min_length=1)
    admission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled approval reference requires an explicit integer version")
        return value


class ControlledCaseApprovalAdmission(ControlledCaseApprovalReference):
    scope: Literal["controlled_case_approval_admission_only"]
    task_id: str = Field(min_length=1)
    origin_owner_epoch: int = Field(ge=1, strict=True)
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    wait_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    decision: Literal["approved", "denied"]
    decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admitted_at: datetime

    @model_validator(mode="after")
    def require_original(self):
        if (self.task_id != case_approval_task_id(self.origin_task_id, self.approval_id)
            or self.admitted_at.utcoffset() is None or self.admission_sha256 != approval_value_hash(
                self.model_dump(mode="json", exclude={"admission_sha256"}))):
            raise ValueError("Controlled approval differs from its original decision admission")
        return self
