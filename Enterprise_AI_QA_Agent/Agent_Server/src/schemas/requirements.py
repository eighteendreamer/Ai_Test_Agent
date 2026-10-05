"""Explicit requirement decisions, immutable versions and design coverage."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.document_differences import UUID_PATTERN
from src.schemas.document_impact_responses import DocumentImpactRuleCandidate, DocumentImpactResponseCheckpoint
from src.schemas.document_impact_workflow import DocumentImpactDiscoveredRule


class RequirementModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequirementPointInput(RequirementModel):
    key: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)


class RequirementCaseLinkInput(RequirementModel):
    case_version_id: str = Field(pattern=UUID_PATTERN)
    point_keys: list[str] = Field(min_length=1, max_length=100)


class RequirementReviewRequest(RequirementModel):
    request_id: str = Field(min_length=1, max_length=160)
    rule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["accept", "reject", "defer"]
    note: str = Field(min_length=1, max_length=8000)
    expected_review_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    requirement_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    expected_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    change: Literal["added", "modified", "removed"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    statement: str | None = Field(default=None, min_length=1, max_length=20000)
    points: list[RequirementPointInput] = Field(default_factory=list, max_length=100)
    case_links: list[RequirementCaseLinkInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision == "accept":
            if self.change is None or self.title is None or self.statement is None:
                raise ValueError("接受需求需要明确分类、标题和正文")
            if bool(self.requirement_id) != bool(self.expected_version_id):
                raise ValueError("关联已有需求必须指定预期父版本；新建不指定父版本")
            if self.change != "removed" and not self.points:
                raise ValueError("有效需求必须有明确验证点")
            if self.change == "removed" and (self.points or self.case_links):
                raise ValueError("删除需求只追加移除版本，不添加新覆盖")
        elif (self.requirement_id or self.expected_version_id or self.change or self.title or
              self.statement or self.points or self.case_links):
            raise ValueError("拒绝或暂缓不修改正式需求及覆盖")
        keys = [point.key for point in self.points]
        versions = [link.case_version_id for link in self.case_links]
        if len(keys) != len(set(keys)) or len(versions) != len(set(versions)):
            raise ValueError("验证点key或关联用例版本不能重复")
        for link in self.case_links:
            if len(set(link.point_keys)) != len(link.point_keys) or not set(link.point_keys).issubset(keys):
                raise ValueError("覆盖必须指向本次明确验证点，且不能重复")
        return self


class RequirementPoint(RequirementPointInput):
    id: str = Field(pattern=UUID_PATTERN)


class RequirementCaseLink(RequirementCaseLinkInput):
    case_id: str = Field(pattern=UUID_PATTERN)
    version_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RequirementVersion(RequirementModel):
    id: str = Field(pattern=UUID_PATTERN)
    requirement_id: str = Field(pattern=UUID_PATTERN)
    version: int = Field(ge=1)
    parent_version_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    review_id: str = Field(pattern=UUID_PATTERN)
    change: Literal["added", "modified", "removed"]
    title: str
    statement: str
    points: list[RequirementPoint]
    case_links: list[RequirementCaseLink]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class RequirementRecord(RequirementModel):
    id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    document_id: str = Field(pattern=UUID_PATTERN)
    current_version_id: str = Field(pattern=UUID_PATTERN)
    latest_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class RequirementReviewSource(RequirementModel):
    snapshot_id: str = Field(pattern=UUID_PATTERN)
    task_id: str = Field(pattern=UUID_PATTERN)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule: DocumentImpactDiscoveredRule
    case_assessments: list[DocumentImpactRuleCandidate]
    case_checkpoints: list[DocumentImpactResponseCheckpoint]
    basis_limitations: list[str]
    analysis_status: Literal["complete", "partial"]
    deployment_status: Literal["unknown"] = "unknown"


class RequirementReview(RequirementModel):
    id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    document_id: str = Field(pattern=UUID_PATTERN)
    request: RequirementReviewRequest
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: RequirementReviewSource
    previous_review_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    review_number: int = Field(ge=1)
    requirement_version: RequirementVersion | None = None
    created_at: datetime


class RequirementReviewPage(RequirementModel):
    items: list[RequirementReview]
    has_more: bool
    limit: int
    offset: int


class RequirementBundle(RequirementModel):
    requirement: RequirementRecord
    version: RequirementVersion


class RequirementPage(RequirementModel):
    items: list[RequirementBundle]
    has_more: bool
    limit: int
    offset: int


class RequirementMatrixPoint(RequirementModel):
    point: RequirementPoint
    case_links: list[RequirementCaseLink]
    previous_case_links: list[RequirementCaseLink]
    design_status: Literal["uncovered", "design_covered", "needs_review"]
    execution_status: Literal["not_assessed"] = "not_assessed"


class RequirementMatrix(RequirementBundle):
    points: list[RequirementMatrixPoint]
    removed: bool
    deployment_status: Literal["unknown"] = "unknown"
