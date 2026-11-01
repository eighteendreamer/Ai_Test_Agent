"""Original RunItem-to-task reservation, not an AgentTask execution lease."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.run_management import RunPlanSnapshotIdentity
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.framework_runtime_result import ControlledCaseRuntimeResultAsset
from src.schemas.framework_approval import approval_value_hash


CONTROLLED_CASE_ADMISSION_KEY = "controlled_case_admission"


class ControlledCaseAdmissionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    run_item_id: str = Field(min_length=1)
    run_item_execution_attempt_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    binding: RuntimeBinding
    plan_snapshot_identity: RunPlanSnapshotIdentity
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admitted_at: datetime

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled case admission requires its explicit integer version")
        return value

    @field_validator("admitted_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Controlled case admission requires an aware database time")
        return value

    @model_validator(mode="after")
    def verify_original_turn(self):
        expected = str(uuid5(NAMESPACE_URL,
            f"case-run:{self.run_id}:{self.run_item_id}:{self.run_item_execution_attempt_id}"))
        if self.binding.runtime_kind != "langgraph" or self.binding.turn_id != expected:
            raise ValueError("Controlled case admission differs from its original RunItem turn")
        return self


class ControlledCaseResultCheckpoint(BaseModel):
    """Retain the original reservation and result in the same attempt record."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2]
    admission: ControlledCaseAdmissionCheckpoint
    runtime_result_asset: ControlledCaseRuntimeResultAsset
    retained_at: datetime

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled result checkpoint requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def require_original(self):
        source, asset = self.admission, self.runtime_result_asset
        if (self.retained_at.utcoffset() is None or asset.binding != source.binding or asset.task_id != source.task_id
            or (asset.run_id, asset.run_item_id, asset.run_item_execution_attempt_id)
                != (source.run_id, source.run_item_id, source.run_item_execution_attempt_id)
            or asset.input_sha256 != source.input_sha256 or asset.execution_sha256 != source.submission_sha256
            or asset.source_sha256 != approval_value_hash(source.model_dump(mode="json"))):
            raise ValueError("Controlled result checkpoint differs from its original admission")
        return self


def read_admission_checkpoint(attempt) -> ControlledCaseAdmissionCheckpoint:
    """Read the original reservation only; inherited attempts need reconciliation."""
    if attempt.checkpoint_key != CONTROLLED_CASE_ADMISSION_KEY or attempt.checkpoint_version not in {1, 2}:
        raise ValueError("RunItem has no supported original controlled case admission checkpoint")
    checkpoint = (ControlledCaseResultCheckpoint.model_validate(attempt.checkpoint_payload).admission
        if attempt.checkpoint_version == 2 else ControlledCaseAdmissionCheckpoint.model_validate(attempt.checkpoint_payload))
    if ((checkpoint.run_id, checkpoint.run_item_id, checkpoint.run_item_execution_attempt_id)
        != (attempt.run_id, attempt.run_item_id, attempt.id)
        or checkpoint.plan_snapshot_identity != attempt.plan_snapshot_identity
        or checkpoint.admitted_at != attempt.checkpoint_at):
        raise ValueError("Controlled case admission checkpoint differs from its original RunItem attempt")
    return checkpoint
