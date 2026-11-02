"""Atomic formal settlement proof; never permission to execute or repair."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash


class ControlledCaseSettlementReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    scope: Literal["controlled_case_atomic_settlement_only"]
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1)
    assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_id: str = Field(min_length=1)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_attempt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settled_at: datetime
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled settlement requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def require_original(self):
        if (self.settled_at.utcoffset() is None or self.receipt_sha256 != approval_value_hash(
                self.model_dump(mode="json", exclude={"receipt_sha256"}))):
            raise ValueError("Controlled settlement differs from its original receipt")
        return self
