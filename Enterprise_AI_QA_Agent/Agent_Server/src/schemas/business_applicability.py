"""Explicit, scoped decisions, separate from immutable execution outcomes."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.business_environment import BusinessEnvironmentSnapshot
from src.schemas.business_tool_observation import BusinessToolObservation, BusinessToolObservationRequest
from src.schemas.case_management import TestCaseVersionRecord
from src.schemas.document_differences import UUID_PATTERN
from src.schemas.requirement_execution import COVERAGE_PATTERN
from src.schemas.requirements import RequirementCoverageReview, RequirementVersion


class DecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApplicabilityObservationInput(BusinessToolObservationRequest):
    expected_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_value_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("tool_job_id")
    @classmethod
    def canonical_identity(cls, value):
        return str(UUID(value))


class EnvironmentPointDecision(DecisionModel):
    point_key: str = Field(min_length=1, max_length=160)
    status: Literal["effective", "not_effective", "unknown"]
    note: str = Field(min_length=1, max_length=8000)
    observation_indexes: list[int] = Field(default_factory=list, max_length=100)


class EnvironmentCaseDecision(DecisionModel):
    case_version_id: str = Field(pattern=UUID_PATTERN)
    point_keys: list[str] = Field(min_length=1, max_length=100)
    status: Literal["valid", "invalid", "unknown"]
    note: str = Field(min_length=1, max_length=8000)

    @field_validator("case_version_id")
    @classmethod
    def canonical_identity(cls, value):
        return str(UUID(value))


class BusinessApplicabilityRequest(DecisionModel):
    request_id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(pattern=UUID_PATTERN)
    requirement_id: str = Field(pattern=UUID_PATTERN)
    requirement_version_id: str = Field(pattern=UUID_PATTERN)
    expected_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_requirement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_id: str = Field(pattern=COVERAGE_PATTERN)
    expected_coverage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_review_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    action: Literal["review", "revoke"] = "review"
    note: str = Field(min_length=1, max_length=8000)
    observations: list[ApplicabilityObservationInput] = Field(default_factory=list, max_length=100)
    points: list[EnvironmentPointDecision] = Field(default_factory=list, max_length=100)
    cases: list[EnvironmentCaseDecision] = Field(default_factory=list, max_length=100)

    @field_validator("document_id", "requirement_id", "requirement_version_id", "coverage_id", "expected_review_id")
    @classmethod
    def canonical_identity(cls, value):
        return value if value in {None, "initial"} else str(UUID(value))

    @model_validator(mode="after")
    def scope(self):
        if self.action == "revoke":
            if self.expected_review_id is None or self.observations or self.points or self.cases:
                raise ValueError("撤回须指定上一决定，且不附加新点、用例或观察")
        elif not self.points:
            raise ValueError("评审须明确选择验证点，不能默认确认整个需求")
        keys = [p.point_key for p in self.points]
        versions = [c.case_version_id for c in self.cases]
        refs = [(o.tool_job_id, tuple(o.output_path_tokens)) for o in self.observations]
        if len(keys) != len(set(keys)) or len(versions) != len(set(versions)) or len(refs) != len(set(refs)):
            raise ValueError("点、用例版本或观察引用不能重复")
        for point in self.points:
            if (len(point.observation_indexes) != len(set(point.observation_indexes)) or
                any(i < 0 or i >= len(self.observations) for i in point.observation_indexes) or
                (point.status != "unknown" and not point.observation_indexes)):
                raise ValueError("确定的生效结论须引用实际观察，索引不能重复或越界")
        for case in self.cases:
            if len(case.point_keys) != len(set(case.point_keys)) or not set(case.point_keys).issubset(keys):
                raise ValueError("用例适用确认须指向本次选中的点")
            statuses = [p.status for p in self.points if p.point_key in case.point_keys]
            if ((case.status == "invalid" and any(s != "effective" for s in statuses)) or
                (case.status == "valid" and "unknown" in statuses)):
                raise ValueError("未知生效不能确认用例适用；失效须有对应已生效点")
        return self


class BusinessApplicabilityCase(EnvironmentCaseDecision):
    version: TestCaseVersionRecord
    version_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BusinessApplicabilityReview(DecisionModel):
    implementation_version: Literal["business-applicability-v1"] = "business-applicability-v1"
    id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    environment_id: str = Field(pattern=UUID_PATTERN)
    snapshot: BusinessEnvironmentSnapshot
    requirement_version: RequirementVersion
    coverage: RequirementCoverageReview | None
    request: BusinessApplicabilityRequest
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_review_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    revision: int = Field(ge=1)
    observations: list[BusinessToolObservation]
    cases: list[BusinessApplicabilityCase]
    scope: Literal["selected_points_and_case_versions"] = "selected_points_and_case_versions"
    decision_source: Literal["explicit_review"] = "explicit_review"
    created_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BusinessApplicabilityPage(DecisionModel):
    items: list[BusinessApplicabilityReview]
    limit: int
    offset: int
    has_more: bool
