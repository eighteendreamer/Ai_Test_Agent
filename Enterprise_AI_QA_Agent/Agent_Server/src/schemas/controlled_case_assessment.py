"""Immutable evidence-backed assessment, not a completed RunItem or execution permit."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash
from src.schemas.run_management import RunItemCompletion
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import VerificationResult, VerificationEvidence
from src.schemas.tool_runtime import ToolExecutionRecord


class ControlledCaseAssessmentCompletion(RunItemCompletion):
    """Strict retained wire without changing the legacy completion DTO."""
    model_config = ConfigDict(extra="forbid", frozen=True)


class ControlledCaseAssessmentEvidence(VerificationEvidence):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ControlledCaseAssessmentVerification(VerificationResult):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence: list[ControlledCaseAssessmentEvidence] = Field(default_factory=list)


class ControlledCaseAssessmentToolRecord(ToolExecutionRecord):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ControlledCaseAssessmentAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    scope: Literal["controlled_case_assessment_preparation_only"]
    binding: RuntimeBinding
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    run_item_id: str = Field(min_length=1)
    run_item_execution_attempt_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    application_commit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completion: ControlledCaseAssessmentCompletion
    verification_results: list[ControlledCaseAssessmentVerification]
    tool_records: list[ControlledCaseAssessmentToolRecord]
    prepared_at: datetime
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled assessment requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def require_originals(self):
        ids = [v.id for v in self.verification_results]
        if (self.prepared_at.utcoffset() is None or len(ids) != len(set(ids))
            or self.completion.verification_ids != ids
            or self.completion.actual.get("verification_results") != [v.model_dump(mode="json") for v in self.verification_results]
            or any((v.session_id, v.turn_id) != (self.binding.session_id, self.binding.turn_id)
                for v in self.verification_results)
            or self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"}))):
            raise ValueError("Controlled assessment differs from its original evidence/results")
        return self
