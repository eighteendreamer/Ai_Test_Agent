"""Settlement admission is immutable lineage, never runtime execution authority."""
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash


def settlement_recovery_task_id(origin_task_id: str, origin_attempt_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"controlled-case:settlement:{origin_task_id}:{origin_attempt_id}"))


class ControlledCaseSettlementReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    origin_task_id: str = Field(min_length=1)
    origin_attempt_id: str = Field(min_length=1)
    run_item_execution_attempt_id: str = Field(min_length=1)
    admission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version", 1)) is not int:
            raise ValueError("Controlled settlement reference requires an integer version")
        return value


class ControlledCaseSettlementAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    scope: Literal["controlled_case_settlement_admission_only"]
    task_id: str = Field(min_length=1)
    origin_task_id: str = Field(min_length=1)
    origin_attempt_id: str = Field(min_length=1)
    origin_owner_epoch: int = Field(ge=1)
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_item_execution_attempt_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    application_commit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin_task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin_attempt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin_owner_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_item_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_item_attempt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admitted_at: datetime
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled settlement admission requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def require_original(self):
        if (self.task_id != settlement_recovery_task_id(self.origin_task_id, self.origin_attempt_id)
            or self.admitted_at.utcoffset() is None
            or self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"}))):
            raise ValueError("Controlled settlement admission differs from its immutable lineage")
        return self
