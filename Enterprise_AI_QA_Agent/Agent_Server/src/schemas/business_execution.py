"""Confirmation inspected at first start; this does not assert deployment or validity."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field
from src.schemas.business_applicability import BusinessApplicabilityReview


class BusinessExecutionAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["business-execution-admission-v1"] = "business-execution-admission-v1"
    decision: Literal["no_explicit_invalid"] = "no_explicit_invalid"
    project_id: str
    run_id: str
    run_item_id: str
    plan_id: str
    case_id: str
    case_version_id: str
    planning_basis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_id: str
    snapshot_id: str
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_attempt_id: str
    checked_at: datetime
    reviews: list[BusinessApplicabilityReview] = Field(default_factory=list)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
