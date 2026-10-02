from __future__ import annotations

from typing import Any

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
