"""Explicit point-to-check associations; execution outcomes come from stored checks."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from src.schemas.document_differences import UUID_PATTERN

COVERAGE_PATTERN = r"^(initial|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"


class ExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequirementExecutionCheckInput(ExecutionModel):
    result_id: str = Field(pattern=UUID_PATTERN)
    verification_id: str = Field(pattern=UUID_PATTERN)
    check_index: int = Field(ge=0, le=199)
    point_keys: list[str] = Field(min_length=1, max_length=100)

    @field_validator("result_id", "verification_id")
    @classmethod
    def canonical_identity(cls, value):
        return str(UUID(value))


class RequirementExecutionRequest(ExecutionModel):
    request_id: str = Field(min_length=1, max_length=160)
    coverage_id: str = Field(pattern=COVERAGE_PATTERN)
    expected_execution_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    note: str = Field(min_length=1, max_length=8000)
    checks: list[RequirementExecutionCheckInput] = Field(default_factory=list, max_length=200)

    @field_validator("coverage_id", "expected_execution_id")
    @classmethod
    def canonical_identity(cls, value):
        return value if value in {None, "initial"} else str(UUID(value))

    @model_validator(mode="after")
    def unique_checks(self):
        refs = [(item.result_id, item.verification_id, item.check_index) for item in self.checks]
        if len(refs) != len(set(refs)) or any(len(set(item.point_keys)) != len(item.point_keys) for item in self.checks):
            raise ValueError("执行确认的核验引用或验证点不能重复")
        return self


class RequirementExecutionCheck(RequirementExecutionCheckInput):
    run_id: str = Field(pattern=UUID_PATTERN)
    run_item_id: str = Field(pattern=UUID_PATTERN)
    attempt_id: str = Field(pattern=UUID_PATTERN)
    attempt_no: int = Field(ge=1)
    case_id: str = Field(pattern=UUID_PATTERN)
    case_version_id: str = Field(pattern=UUID_PATTERN)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_status: Literal["passed", "failed", "error", "blocked", "skipped"]
    run_kind: Literal["normal", "regression"]
    run_origin: Literal["native", "legacy_smoke_import"]
    execution_strategy: Literal["legacy", "agent"]
    mode_key: str
    regression_source_result_id: str | None = None
    plan_sha256: str | None = None
    check: dict[str, Any]
    check_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_sources: list[dict[str, Any]]


class RequirementExecutionReview(ExecutionModel):
    implementation_version: Literal["requirement-execution-v1"] = "requirement-execution-v1"
    id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    document_id: str = Field(pattern=UUID_PATTERN)
    requirement_id: str = Field(pattern=UUID_PATTERN)
    requirement_version_id: str = Field(pattern=UUID_PATTERN)
    requirement_version_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: RequirementExecutionRequest
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_execution_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    revision: int = Field(ge=1)
    checks: list[RequirementExecutionCheck]
    created_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RequirementExecutionPage(ExecutionModel):
    items: list[RequirementExecutionReview]
    has_more: bool
    limit: int
    offset: int


class RequirementExecutionCandidate(ExecutionModel):
    result_id: str
    run_id: str
    case_id: str
    case_version_id: str
    status: str
    summary: str
    created_at: datetime
    checks: list[dict[str, Any]]
    warnings: list[str]


class RequirementExecutionCandidates(ExecutionModel):
    items: list[RequirementExecutionCandidate]
    has_more: bool
    limit: int
    offset: int
