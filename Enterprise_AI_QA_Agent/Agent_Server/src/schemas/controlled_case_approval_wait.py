"""Retained atomic parking fact. This is not a delivery ACK or resume grant."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash


class ControlledCaseWaitEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(min_length=1)
    tool_job_id: str = Field(min_length=1)
    interrupt_id: str = Field(min_length=1)
    native_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    link_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ControlledCaseApprovalWaitReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    scope: Literal["controlled_case_atomic_approval_wait_only"]
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    run_item_id: str = Field(min_length=1)
    run_item_execution_attempt_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_claim_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_return_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch: tuple[ControlledCaseWaitEntry, ...] = Field(min_length=1)
    parked_at: datetime
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled wait requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def require_original(self):
        if (self.parked_at.utcoffset() is None or any(
                len({getattr(entry, key) for entry in self.batch}) != len(self.batch)
                for key in ("approval_id", "tool_job_id", "interrupt_id"))
            or self.receipt_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"receipt_sha256"}))):
            raise ValueError("Controlled wait differs from its complete original receipt")
        return self


class ControlledCaseApprovalStateWaitReceipt(ControlledCaseApprovalWaitReceipt):
    """Public summary only; full state remains on the private formal Attempt."""
    schema_version: Literal[2]
    state_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def parse_controlled_case_approval_wait(value):
    if isinstance(value, ControlledCaseApprovalWaitReceipt):
        value = value.model_dump(mode="json")
    reader = ControlledCaseApprovalStateWaitReceipt if isinstance(value, dict) and value.get("schema_version") == 2 else ControlledCaseApprovalWaitReceipt
    return reader.model_validate(value)
