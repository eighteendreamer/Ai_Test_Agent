from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    # Stable identity allocated by the Runner before invoking an external
    # executor. It correlates a late report with the operation that may have
    # reached the target; it is not an execution or retry grant.
    runner_execution_id: str | None = Field(default=None, min_length=1, max_length=128)
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


def runner_report_encodings(payload: CompatibilityRunnerTaskReportRequest) -> dict[str, str]:
    """Known wire encodings, indexed by their exact content digest.

    Missing execution identity keeps the pre149 six-field shape. The initial
    optional-field writer (caad77084) emitted an extra JSON null; retain only
    that exact alternate encoding for reads, never an alternate non-null ID.
    """
    values = {**payload.model_dump(mode="json"), "status": normalize_runner_report_status(payload.status)}
    if payload.runner_execution_id is None:
        values.pop("runner_execution_id")
        variants = [values, {**values, "runner_execution_id": None}]
    else:
        variants = [values]
    contents = [json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) for value in variants]
    return {sha256(content.encode("utf-8")).hexdigest(): content for content in contents}


def runner_report_content(payload: CompatibilityRunnerTaskReportRequest) -> str:
    """Preferred stable wire shape; reads also recognize known old encodings."""
    return next(iter(runner_report_encodings(payload).values()))


def runner_report_receipt(payload: CompatibilityRunnerTaskReportRequest) -> str | None:
    """The existing identified report digest, shared by submit and read paths."""
    if not payload.assignment_id:
        return None
    return next(iter(runner_report_encodings(payload)))


def runner_report_result(
    payload: CompatibilityRunnerTaskReportRequest,
    artifacts: list[CompatibilityArtifactRecord],
) -> dict[str, Any]:
    return {**payload.result, "artifacts": [item.model_dump() for item in artifacts], "error": payload.error}


def runner_report_metadata(value: dict[str, Any]) -> dict[str, Any]:
    server_controlled_keys = {
        "mode_calls_execution_completed_at", "mode_calls_execution_result_count",
        "mode_calls_execution_runner_id", "mode_calls_execution_started_at", "mode_calls_execution_status",
        "mode_calls_execution_id", "mode_calls_execution_reference",
        "mode_calls_execution_result_artifact_id",
        "retry_count", "retry_history", "automatic_retry_count", "automatic_retry_limit", "recovery_reason",
        "recovery_required_at", "recovery_source", "assignment_id", "assignment_fencing_required",
        "report_receipt_sha256", "runner_execution_id",
    }
    return {key: item for key, item in value.items() if key not in server_controlled_keys}


class CompatibilityRunnerReportObservation(BaseModel):
    """Receipt for a rejected report retained only as untrusted evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    scope: Literal["compatibility_runner_report_observation_only"] = "compatibility_runner_report_observation_only"
    artifact_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    dispatch_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    runner_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    runner_execution_id: str | None = Field(default=None, min_length=1, max_length=128)
    report_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: str = Field(min_length=1)
    authoritative: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("schema_version", mode="before")
    @classmethod
    def _json_schema_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("Observation schema version must be a JSON integer.")
        return value

    @field_validator("authoritative", "execution_authority", mode="before")
    @classmethod
    def _no_authority(cls, value: Any) -> bool:
        # Literal validation otherwise treats JSON 0 as False, even in strict mode.
        if value is not False:
            raise ValueError("Observation authority must be JSON false.")
        return value


class CompatibilityModeExecutionReference(BaseModel):
    """Server-owned identity, not an execution grant from tool arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(min_length=1)
    dispatch_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    runner_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompatibilityModeExecutionObservation(CompatibilityModeExecutionReference):
    """Queryable mode-bridge evidence, never authoritative task evidence."""

    schema_version: Literal[1] = 1
    scope: Literal["compatibility_runner_mode_execution_observation_only"] = (
        "compatibility_runner_mode_execution_observation_only"
    )
    artifact_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: str = Field(min_length=1)
    authoritative: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("schema_version", mode="before")
    @classmethod
    def _json_schema_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("Observation schema version must be a JSON integer.")
        return value

    @field_validator("authoritative", "execution_authority", mode="before")
    @classmethod
    def _no_authority(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("Observation authority must be JSON false.")
        return value


class CompatibilityModeExecutionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    reference: CompatibilityModeExecutionReference
    case_ids: list[str]
    runner_selector: dict[str, Any]
    mode_calls: list[dict[str, Any]]
    response: dict[str, Any]
    error_type: str | None = None


def runner_mode_definition(task: CompatibilityQueuedTask) -> dict[str, Any]:
    return {"case_ids": task.case_ids, "runner_selector": task.runner_selector, "mode_calls": task.mode_calls}


def runner_mode_definition_sha256(definition: dict[str, Any]) -> str:
    content = json.dumps(definition, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return sha256(content.encode("utf-8")).hexdigest()


RUNNER_MODE_OBSERVATION_ID_PREFIX = "runner-mode-observation-"


def runner_mode_evidence_content(evidence: CompatibilityModeExecutionEvidence) -> str:
    return json.dumps(evidence.model_dump(mode="json"), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False)


def runner_mode_observation_artifact_id(reference: CompatibilityModeExecutionReference, digest: str) -> str:
    identity = json.dumps([reference.model_dump(mode="json"), digest], sort_keys=True, separators=(",", ":"))
    return RUNNER_MODE_OBSERVATION_ID_PREFIX + sha256(identity.encode("utf-8")).hexdigest()


def validate_runner_mode_evidence(
    observation: CompatibilityModeExecutionObservation, evidence: CompatibilityModeExecutionEvidence,
) -> None:
    """Shared content/receipt checks, not permission to execute or report."""
    reference = evidence.reference
    if (any(getattr(reference, field) != getattr(observation, field)
            for field in CompatibilityModeExecutionReference.model_fields)
        or runner_mode_definition_sha256({"case_ids": evidence.case_ids,
            "runner_selector": evidence.runner_selector, "mode_calls": evidence.mode_calls}) != reference.definition_sha256
        or sha256(runner_mode_evidence_content(evidence).encode("utf-8")).hexdigest() != observation.content_sha256
        or observation.artifact_id != runner_mode_observation_artifact_id(reference, observation.content_sha256)
        or evidence.response.get("task_id") != reference.task_id
        or evidence.response.get("runner_id") != reference.runner_id):
        raise ValueError("Mode observation differs from its original definition or content.")


def runner_mode_response(
    *, task_id: str, runner_id: str, mode_call_count: int,
    results: list[dict[str, Any]], error_type: str | None = None,
) -> dict[str, Any]:
    """The bridge's one aggregation rule; non-completed results never pass."""
    completed = sum(1 for item in results if item.get("status") == "completed")
    failed = sum(1 for item in results if item.get("status") in {"failed", "denied"})
    partial = len(results) - completed - failed
    status = "failed" if error_type or (results and failed == len(results)) else (
        "partial" if failed or partial or len(results) != mode_call_count else "completed"
    )
    return {
        "status": status, "ok": status == "completed", "task_id": task_id, "runner_id": runner_id,
        "mode_call_count": mode_call_count,
        "summary": f"Executed {mode_call_count} compatibility mode call(s): {completed} completed, {partial} partial, {failed} failed.",
        "mode_call_results": results,
    }


def validate_runner_mode_response(
    response: dict[str, Any], *, task_id: str, runner_id: str,
    mode_calls: list[dict[str, Any]], error_type: str | None = None,
) -> None:
    """Validate the live wire response and a saved response by the same rules."""
    if not isinstance(response, dict):
        raise ValueError("Mode response must be an object.")
    results = response.get("mode_call_results")
    if (not isinstance(results, list) or len(results) > len(mode_calls)
        or (not error_type and len(results) != len(mode_calls))):
        raise ValueError("Mode response has an incomplete or invalid result sequence.")
    for index, result in enumerate(results):
        if (not isinstance(result, dict) or type(result.get("call_index")) is not int
            or result["call_index"] != index
            or result.get("tool_key") != str(mode_calls[index].get("tool_key") or "").strip()):
            raise ValueError("Mode result position differs from its frozen call.")
    expected = runner_mode_response(task_id=task_id, runner_id=runner_id,
        mode_call_count=len(mode_calls), results=results, error_type=error_type)
    if runner_mode_definition_sha256(expected) != runner_mode_definition_sha256(response):
        raise ValueError("Mode response aggregation differs from its results.")


class CompatibilityModeExecutionQuery(BaseModel):
    """Only an explicitly bound bridge result can resume original delivery.

    The observation itself remains non-authoritative. The server must verify
    its current assignment and atomic finish binding before returning available;
    upload/report still independently fence delivery. This is not an ACK.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    scope: Literal["compatibility_runner_mode_execution_result_only"] = "compatibility_runner_mode_execution_result_only"
    runner_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    status: Literal["available", "unknown", "conflict"]
    reason_code: Literal[
        "result_available", "execution_not_started", "execution_running", "execution_result_unbound",
        "assignment_superseded", "task_not_assigned", "runner_unavailable", "runner_lease_expired",
    ]
    execution_authority: Literal[False] = False
    observation: CompatibilityModeExecutionObservation | None = None
    evidence: CompatibilityModeExecutionEvidence | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _json_schema_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("Query schema version must be a JSON integer.")
        return value

    @field_validator("execution_authority", mode="before")
    @classmethod
    def _no_authority(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("Query execution authority must be JSON false.")
        return value

    @model_validator(mode="after")
    def _bound_result(self) -> "CompatibilityModeExecutionQuery":
        if self.status != "available":
            reasons = ({"execution_not_started", "execution_running", "execution_result_unbound"}
                if self.status == "unknown" else
                {"assignment_superseded", "task_not_assigned", "runner_unavailable", "runner_lease_expired"})
            if self.reason_code not in reasons or self.observation is not None or self.evidence is not None:
                raise ValueError("Unavailable mode query cannot supply a result.")
            return self
        if self.reason_code != "result_available" or self.observation is None or self.evidence is None:
            raise ValueError("Available mode query requires its original evidence and receipt.")
        evidence = self.evidence
        validate_runner_mode_evidence(self.observation, evidence)
        if any(getattr(self, field) != getattr(evidence.reference, field)
                for field in ("runner_id", "task_id", "assignment_id")):
            raise ValueError("Mode query identity differs from its evidence.")
        validate_runner_mode_response(evidence.response, task_id=self.task_id, runner_id=self.runner_id,
            mode_calls=evidence.mode_calls, error_type=evidence.error_type)
        return self


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
