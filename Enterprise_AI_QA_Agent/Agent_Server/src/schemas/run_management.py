from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.case_management import TestCaseRecord, TestCaseVersionRecord
from src.schemas.session import ApprovalDecisionRequest


TestRunKind = Literal["normal", "regression"]
TestRunOrigin = Literal["native", "legacy_smoke_import"]
TestRunStatus = Literal["queued", "running", "completed", "cancelled"]
RunItemStatus = Literal[
    "queued",
    "claimed",
    "running",
    "waiting_approval",
    "passed",
    "failed",
    "error",
    "blocked",
    "skipped",
    "cancelled",
]
RunAttemptStatus = Literal[
    "claimed",
    "running",
    "waiting_approval",
    "passed",
    "failed",
    "error",
    "blocked",
    "skipped",
    "cancelled",
    "expired",
]
TestResultStatus = Literal["passed", "failed", "error", "blocked", "skipped"]
RegressionFailureStatus = Literal["failed", "error", "blocked"]


class TestRunStats(BaseModel):
    total: int = 0
    queued: int = 0
    claimed: int = 0
    running: int = 0
    waiting_approval: int = 0
    passed: int = 0
    failed: int = 0
    error: int = 0
    blocked: int = 0
    skipped: int = 0
    cancelled: int = 0


class TestRunRecord(BaseModel):
    id: str
    project_id: str
    suite_id: str
    run_kind: TestRunKind = "normal"
    origin: TestRunOrigin = "native"
    mode_key: str
    session_id: str | None = None
    parent_run_id: str | None = None
    status: TestRunStatus = "queued"
    stats: TestRunStats = Field(default_factory=TestRunStats)
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_reason: str | None = None


class TestRunItemRecord(BaseModel):
    id: str
    run_id: str
    case_id: str
    case_version_id: str
    position: int = Field(ge=1)
    status: RunItemStatus = "queued"
    attempt_no: int = Field(default=0, ge=0)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    approval_id: str | None = None
    tool_job_id: str | None = None
    result_id: str | None = None
    regression_source_result_id: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TestRunAttemptRecord(BaseModel):
    id: str
    run_id: str
    run_item_id: str
    attempt_no: int = Field(ge=1)
    worker_id: str
    lease_token: str
    status: RunAttemptStatus = "claimed"
    claimed_at: datetime
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    approval_id: str | None = None
    tool_job_id: str | None = None
    completed_at: datetime | None = None


class RunEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_type: str = Field(min_length=1, max_length=80)
    evidence_id: str = Field(min_length=1, max_length=256)
    label: str = Field(default="", max_length=240)
    uri: str | None = Field(default=None, max_length=2048)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TestCaseResultRecord(BaseModel):
    id: str
    run_id: str
    run_item_id: str
    case_id: str
    case_version_id: str
    regression_source_result_id: str | None = None
    attempt_id: str
    attempt_no: int = Field(ge=1)
    status: TestResultStatus
    summary: str
    actual: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[RunEvidenceRef] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    verification_ids: list[str] = Field(default_factory=list)
    tool_job_id: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    payload_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime


class RegressionSourceRecord(BaseModel):
    result_id: str
    run_item_id: str
    case_id: str
    case_version_id: str
    status: TestResultStatus
    run_item_position: int | None = Field(default=None, ge=1)


class LatestRegressionRecord(BaseModel):
    run_id: str
    run_status: TestRunStatus
    run_item_id: str
    item_status: RunItemStatus
    result_id: str | None = None
    result_status: TestResultStatus | None = None
    case_version_id: str
    created_at: datetime
    updated_at: datetime


class RegressionFailureRecord(BaseModel):
    source_result_id: str
    source_run_id: str
    source_run_status: TestRunStatus
    source_run_created_at: datetime
    case_id: str
    case_version_id: str
    mode_key: str
    failure_status: RegressionFailureStatus
    summary: str
    error_message: str | None = None
    failed_at: datetime
    evidence_count: int = Field(default=0, ge=0)
    artifact_count: int = Field(default=0, ge=0)
    verification_count: int = Field(default=0, ge=0)
    has_actual: bool = False
    regression_batch_count: int = Field(default=0, ge=0)
    latest_regression: LatestRegressionRecord | None = None


class RegressionFailureSummary(RegressionFailureRecord):
    case_key: str
    case_title: str


class RegressionFailurePage(BaseModel):
    items: list[RegressionFailureSummary] = Field(default_factory=list)
    limit: int
    next_cursor: str | None = None
    has_more: bool = False


class RegressionEvidenceSummary(BaseModel):
    evidence_type: str
    evidence_id: str
    label: str = ""


class RegressionArtifactLink(BaseModel):
    artifact_id: str
    content_url: str | None = None


class RegressionVerificationSummary(BaseModel):
    id: str
    verifier: str = ""
    status: str
    summary: str = ""
    assertion_count: int = Field(default=0, ge=0)
    passed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    created_at: datetime | None = None


class RegressionContext(BaseModel):
    source_result_id: str
    source_run_id: str
    case_id: str
    case_version_id: str
    mode_key: str
    failure_status: RegressionFailureStatus
    summary: str
    error_message: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence: list[RegressionEvidenceSummary] = Field(default_factory=list)
    artifacts: list[RegressionArtifactLink] = Field(default_factory=list)
    verifications: list[RegressionVerificationSummary] = Field(default_factory=list)
    failed_at: datetime


class RegressionBatchRecord(BaseModel):
    run_id: str
    run_kind: TestRunKind
    run_status: TestRunStatus
    parent_run_id: str | None = None
    run_item_id: str
    item_status: RunItemStatus
    result_id: str | None = None
    result_status: TestResultStatus | None = None
    case_version_id: str
    created_at: datetime
    updated_at: datetime


class RegressionBatchPage(BaseModel):
    source_result_id: str
    items: list[RegressionBatchRecord] = Field(default_factory=list)
    limit: int
    next_cursor: str | None = None
    has_more: bool = False


class TestRunDetail(BaseModel):
    run: TestRunRecord
    items: list[TestRunItemRecord] = Field(default_factory=list)
    attempts: list[TestRunAttemptRecord] = Field(default_factory=list)
    results: list[TestCaseResultRecord] = Field(default_factory=list)


class TestRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode_key: str = Field(min_length=1, max_length=80)
    session_id: str | None = Field(default=None, min_length=1, max_length=160)


class RegressionRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_ids: list[str] = Field(default_factory=list, max_length=1000)
    version_overrides: dict[str, str] = Field(default_factory=dict, max_length=1000)
    session_id: str | None = Field(default=None, min_length=1, max_length=160)


class TestRunPage(BaseModel):
    items: list[TestRunRecord] = Field(default_factory=list)
    limit: int
    offset: int
    has_more: bool = False


class RunClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=160)
    limit: int = Field(default=1, ge=1, le=100)
    lease_seconds: int = Field(default=90, ge=15, le=3600)


class RunItemClaim(BaseModel):
    item: TestRunItemRecord
    attempt: TestRunAttemptRecord
    lease_token: str
    case: TestCaseRecord
    version: TestCaseVersionRecord


class RunClaimResponse(BaseModel):
    claims: list[RunItemClaim] = Field(default_factory=list)


class RunItemLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_token: str = Field(min_length=1, max_length=256)


class RunItemHeartbeatRequest(RunItemLeaseRequest):
    lease_seconds: int = Field(default=90, ge=15, le=3600)


class RunItemExecuteRequest(RunItemLeaseRequest):
    approval_id: str | None = Field(default=None, min_length=1, max_length=160)


class RunItemApprovalDecisionRequest(ApprovalDecisionRequest):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1, max_length=160)


class RunItemApprovalWaitRequest(RunItemLeaseRequest):
    approval_id: str = Field(min_length=1, max_length=160)
    tool_job_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=4000)
    approval_scope_hash: str = Field(min_length=64, max_length=64)


class RunItemApprovalPending(BaseModel):
    status: Literal["waiting_approval"] = "waiting_approval"
    run_id: str
    run_item_id: str
    attempt_no: int = Field(ge=1)
    approval_id: str
    tool_job_id: str
    summary: str


class RunItemCompleteRequest(RunItemLeaseRequest):
    status: TestResultStatus
    summary: str = Field(min_length=1, max_length=4000)
    actual: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[RunEvidenceRef] = Field(default_factory=list, max_length=200)
    artifact_ids: list[str] = Field(default_factory=list, max_length=200)
    verification_ids: list[str] = Field(default_factory=list, max_length=200)
    tool_job_id: str | None = Field(default=None, max_length=160)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = Field(default=None, max_length=8000)


class RunItemCompletion(BaseModel):
    status: TestResultStatus
    summary: str
    actual: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[RunEvidenceRef] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    verification_ids: list[str] = Field(default_factory=list)
    tool_job_id: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    payload_hash: str = Field(min_length=64, max_length=64)


class RunCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="Cancelled by operator", min_length=1, max_length=2000)


class LeaseRecoveryResponse(BaseModel):
    recovered_count: int = Field(ge=0)
