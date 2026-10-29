from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompatibilityPlanActionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: str | None = None
    context_bundle: dict[str, Any] = Field(default_factory=dict)


class CompatibilityArtifactRecord(BaseModel):
    artifact_id: str
    task_id: str
    dispatch_id: str
    plan_id: str
    runner_id: str | None = None
    environment_id: str
    type: str
    uri: str
    mime_type: str | None = None
    label: str = ""
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompatibilityRunnerRegistrationRequest(BaseModel):
    runner_id: str
    name: str = ""
    os: str = ""
    capabilities: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    max_parallel: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompatibilityRunnerHeartbeatRequest(BaseModel):
    status: str = "online"
    active_task_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompatibilityRunnerCleanupRequest(BaseModel):
    older_than_seconds: int = 3600
    runner_ids: list[str] = Field(default_factory=list)


class CompatibilityRunnerCleanupResponse(BaseModel):
    deleted_count: int = 0
    runner_ids: list[str] = Field(default_factory=list)
    skipped_runner_ids: list[str] = Field(default_factory=list)
    skipped_reasons: dict[str, str] = Field(default_factory=dict)


class CompatibilityRunnerRecord(BaseModel):
    runner_id: str
    name: str = ""
    os: str = ""
    capabilities: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    max_parallel: int = 1
    status: str = "online"
    active_task_ids: list[str] = Field(default_factory=list)
    registered_at: str
    heartbeat_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompatibilityQueuedTask(BaseModel):
    task_id: str
    dispatch_id: str
    plan_id: str
    environment_id: str
    case_ids: list[str] = Field(default_factory=list)
    runner_selector: dict[str, Any] = Field(default_factory=dict)
    mode_calls: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "queued"
    assigned_runner_id: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[CompatibilityArtifactRecord] = Field(default_factory=list)
    error: str | None = None
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompatibilityRunnerPollResponse(BaseModel):
    runner_id: str
    tasks: list[CompatibilityQueuedTask] = Field(default_factory=list)


class CompatibilityRunnerTaskReportRequest(BaseModel):
    status: str
    assignment_id: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def normalize_runner_report_status(value: str) -> str:
    status = str(value or "").strip().lower()
    aliases = {
        "passed": "completed", "pass": "completed", "success": "completed",
        "error": "failed", "failure": "failed", "canceled": "cancelled",
    }
    normalized = aliases.get(status, status)
    if normalized not in {"completed", "failed", "cancelled"}:
        raise ValueError("Runner task report status must be completed, failed, or cancelled.")
    return normalized


def runner_report_receipt(payload: CompatibilityRunnerTaskReportRequest) -> str | None:
    """The existing identified report digest, shared by submit and read paths."""
    if not payload.assignment_id:
        return None
    return sha256(json.dumps(
        {**payload.model_dump(mode="json"), "status": normalize_runner_report_status(payload.status)},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def runner_report_result(
    payload: CompatibilityRunnerTaskReportRequest,
    artifacts: list[CompatibilityArtifactRecord],
) -> dict[str, Any]:
    return {**payload.result, "artifacts": [item.model_dump() for item in artifacts], "error": payload.error}


def runner_report_metadata(value: dict[str, Any]) -> dict[str, Any]:
    server_controlled_keys = {
        "mode_calls_execution_completed_at", "mode_calls_execution_result_count",
        "mode_calls_execution_runner_id", "mode_calls_execution_started_at", "mode_calls_execution_status",
        "retry_count", "retry_history", "automatic_retry_count", "automatic_retry_limit", "recovery_reason",
        "recovery_required_at", "recovery_source", "assignment_id", "assignment_fencing_required",
        "report_receipt_sha256",
    }
    return {key: item for key, item in value.items() if key not in server_controlled_keys}


class CompatibilityRunnerJournalReconciliation(BaseModel):
    """Read-only evidence joining a local Runner journal to server state.

    A reconciliation result never grants permission to execute, retry, upload,
    or report a task.  The local journal may have reached an external executor
    before the process stopped, so non-terminal server state remains unknown.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    scope: Literal["compatibility_runner_journal_reconciliation_only"] = (
        "compatibility_runner_journal_reconciliation_only"
    )
    runner_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    assignment_id: str | None = None
    journal_phase: str = Field(min_length=1)
    server_status: str | None = None
    server_runner_id: str | None = None
    server_assignment_id: str | None = None
    report_receipt_sha256: str | None = None
    server_receipt_sha256: str | None = None
    status: Literal["completed", "unknown", "drifted"]
    execution_authority: Literal[False] = False
    reason_code: Literal[
        "report_verified",
        "server_non_terminal",
        "server_recovery_required",
        "server_task_missing",
        "server_task_identity_drift",
        "server_assignment_drift",
        "server_runner_drift",
        "journal_identity_drift",
        "journal_assignment_missing",
        "journal_report_missing",
        "journal_report_identity_drift",
        "server_definition_drift",
        "server_receipt_missing",
        "server_report_drift",
        "server_status_invalid",
    ]


class CompatibilityTaskRequeueRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)
    dispatch_id: str | None = None
    runner_id: str | None = None
    statuses: list[str] = Field(default_factory=lambda: ["failed"])
    reason: str = "manual_retry"


class CompatibilityTaskRequeueResponse(BaseModel):
    requeued_count: int = 0
    task_ids: list[str] = Field(default_factory=list)
    skipped_task_ids: list[str] = Field(default_factory=list)
    skipped_reasons: dict[str, str] = Field(default_factory=dict)


class CompatibilityArtifactUploadRequest(BaseModel):
    filename: str
    content_base64: str
    assignment_id: str | None = None
    upload_id: str | None = Field(default=None, min_length=1, max_length=128)
    type: str = "evidence"
    label: str = ""
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompatibilityRunnerTaskSummary(BaseModel):
    total: int = 0
    queued: int = 0
    assigned: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    other: int = 0
    artifact_count: int = 0
    by_environment: dict[str, dict[str, int]] = Field(default_factory=dict)
    failure_summaries: list[dict[str, Any]] = Field(default_factory=list)


class CompatibilityEnvironmentReport(BaseModel):
    environment_id: str
    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    pending: int = 0
    artifact_count: int = 0
    status: str = "pending"


class CompatibilityFailureReport(BaseModel):
    task_id: str
    environment_id: str
    runner_id: str | None = None
    summary: str = ""
    error: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class CompatibilityRecoverableTaskReport(BaseModel):
    task_id: str
    environment_id: str
    runner_id: str | None = None
    status: str
    summary: str = ""
    error: str | None = None


class CompatibilityExecutionReport(BaseModel):
    report_id: str
    dispatch_id: str | None = None
    runner_id: str | None = None
    generated_at: str
    status: str
    summary: str
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    cancelled_tasks: int = 0
    pending_tasks: int = 0
    pass_rate: float = 0
    artifact_count: int = 0
    environments: list[CompatibilityEnvironmentReport] = Field(default_factory=list)
    failures: list[CompatibilityFailureReport] = Field(default_factory=list)
    recoverable_tasks: list[CompatibilityRecoverableTaskReport] = Field(default_factory=list)
    artifacts: list[CompatibilityArtifactRecord] = Field(default_factory=list)
    markdown: str = ""
