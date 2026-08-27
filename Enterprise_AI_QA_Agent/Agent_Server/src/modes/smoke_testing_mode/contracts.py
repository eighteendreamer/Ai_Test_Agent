from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


SmokePlanStatus = Literal[
    "draft",
    "awaiting_user_confirmation",
    "revision_requested",
    "approved_for_execution",
    "executing",
    "completed",
    "blocked",
    "partial",
    "needs_review",
]
SmokeCaseType = Literal["health", "api", "ui"]
SmokeRiskLevel = Literal["low", "medium", "high"]
SmokeVerdict = Literal["ready", "blocked", "partial", "needs_review"]
SmokeCaseStatus = Literal["passed", "failed", "blocked", "partial", "not_run"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class SmokeSource(BaseModel):
    source_type: str
    source_id: str = ""
    title: str = ""
    uri: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SmokeAssertion(BaseModel):
    assertion_id: str = Field(default_factory=lambda: new_id("assert"))
    kind: str
    target: str
    expected: Any = None
    operator: str = "equals"
    description: str = ""


class SmokeApiStep(BaseModel):
    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    expected_status: int = 200
    expected_fields: list[str] = Field(default_factory=list)
    expected_schema: dict[str, Any] = Field(default_factory=dict)


class SmokeUiStep(BaseModel):
    page_url: str
    action: str = "open"
    locator: str = ""
    input: str = ""
    expected_visible_text: str = ""


class SmokeStep(BaseModel):
    step_id: str = Field(default_factory=lambda: new_id("step"))
    title: str
    step_type: SmokeCaseType
    api: SmokeApiStep | None = None
    ui: SmokeUiStep | None = None
    assertions: list[SmokeAssertion] = Field(default_factory=list)


class SmokeCase(BaseModel):
    case_id: str = Field(default_factory=lambda: new_id("case"))
    title: str
    case_type: SmokeCaseType
    description: str = ""
    steps: list[SmokeStep] = Field(default_factory=list)
    assertions: list[SmokeAssertion] = Field(default_factory=list)
    selected: bool = True
    execution_eligible: bool = True
    requires_approval: bool = False
    risk_level: SmokeRiskLevel = "low"
    source_refs: list[SmokeSource] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SmokePlanRevision(BaseModel):
    revision_id: str = Field(default_factory=lambda: new_id("rev"))
    user_revision: str
    summary: str = ""
    created_at: str = Field(default_factory=utc_now)


class SmokeExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: new_id("smoke"))
    version: int = 1
    title: str = "冒烟测试方案"
    objective: str = ""
    project_scope: str = ""
    target_url: str = ""
    status: SmokePlanStatus = "awaiting_user_confirmation"
    cases: list[SmokeCase] = Field(default_factory=list)
    source_refs: list[SmokeSource] = Field(default_factory=list)
    credential_summary: str = ""
    risk_summary: dict[str, int] = Field(default_factory=dict)
    review_notes: list[str] = Field(default_factory=list)
    revisions: list[SmokePlanRevision] = Field(default_factory=list)
    rustfs_uris: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class SmokeCaseResult(BaseModel):
    case_id: str
    title: str
    case_type: SmokeCaseType
    status: SmokeCaseStatus
    summary: str
    assertion_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    duration_ms: int = 0
    failure_category: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SmokeRunResult(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    plan_id: str
    plan_version: int
    project_scope: str = ""
    target_url: str = ""
    status: SmokeCaseStatus = "not_run"
    verdict: SmokeVerdict = "needs_review"
    total_cases: int = 0
    selected_case_count: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    blocked_cases: int = 0
    case_results: list[SmokeCaseResult] = Field(default_factory=list)
    started_at: str = Field(default_factory=utc_now)
    completed_at: str = ""
    rustfs_uris: dict[str, str] = Field(default_factory=dict)
    summary: str = ""


class SmokePlanArtifact(BaseModel):
    label: str
    uri: str
    content_type: str = "application/json"
    size_bytes: int = 0


class RegressionCandidateCase(BaseModel):
    case_id: str
    source_plan_id: str
    source_run_id: str = ""
    project_scope: str = ""
    case_type: SmokeCaseType
    title: str
    case_uri: str = ""
    stability_score: float = 100.0
    status: str = "stable"
    run_count: int = 1
    pass_count: int = 0
    fail_count: int = 0
    flaky_count: int = 0
    blocked_count: int = 0
    last_status: SmokeCaseStatus = "not_run"
    last_passed_at: str = ""
    updated_at: str = Field(default_factory=utc_now)

