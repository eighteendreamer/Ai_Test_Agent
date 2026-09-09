"""API Testing Mode campaign and runtime state models.

All state is Pydantic-serializable so it can be persisted in
``session.metadata`` and restored across turns.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Request interpretation
# ---------------------------------------------------------------------------


class ApiTestingRequestState(BaseModel):
    """Light-weight interpretation of the user intent for this turn."""

    model_config = ConfigDict(extra="allow")

    objective: str = ""
    project_hint: str = ""
    domain_hint: str = ""
    endpoint_hint: str = ""
    method_hint: str = ""
    scope_preference: str = ""  # all_related / core_only / manual_pick / single_target / ""
    verification_focus: str = "general"
    auth_hint: str = ""
    source_preferences: list[str] = Field(default_factory=list)
    raw_message: str = ""


# ---------------------------------------------------------------------------
# Candidate objects returned during clarification
# ---------------------------------------------------------------------------


class ProjectCandidate(BaseModel):
    """A candidate project that matches the user's request."""

    project_name: str
    project_url: str = ""
    doc_ids: list[str] = Field(default_factory=list)
    doc_count: int = 0
    endpoint_count: int = 0
    score: float = 0.0
    rationale: str = ""


class DocumentCandidate(BaseModel):
    """One API document belonging to a resolved project."""

    doc_id: str
    title: str
    filename: str = ""
    project_name: str = ""
    project_url: str = ""
    endpoint_count: int = 0
    updated_at: str = ""


class EndpointCandidate(BaseModel):
    """One endpoint extracted from a document."""

    endpoint_id: str  # deterministic hash of (method, path, doc_id)
    doc_id: str = ""
    method: str
    path: str
    full_url: str = ""
    summary: str = ""
    capability: str = ""  # login / create_order / query_order / pay / ...
    tags: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    section: str = ""  # original markdown section (trimmed)
    score: float = 0.0


# ---------------------------------------------------------------------------
# Pending selection payload (what the runtime is asking the user)
# ---------------------------------------------------------------------------


class PendingSelection(BaseModel):
    """Structured payload describing what selection is currently pending."""

    kind: str  # SelectionKind literal
    prompt: str = ""  # short user-facing prompt
    options: list[dict[str, Any]] = Field(default_factory=list)
    recommended_option_id: str = ""
    allow_free_text: bool = False
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Credential session
# ---------------------------------------------------------------------------


class CredentialSession(BaseModel):
    """Resolved credentials or login state for the current campaign."""

    credential_session_id: str = ""
    auth_type: str = "none"
    token: str = ""  # bearer/API-key value (plain for this first version)
    cookie_jar: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    expires_at: str = ""
    source: str = ""  # user_input / project_test_profile / dynamic_login
    login_endpoint: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Campaign task
# ---------------------------------------------------------------------------


class AssertionSpec(BaseModel):
    """One assertion to evaluate against an HTTP response."""

    kind: Literal[
        "status_code",
        "status_code_range",
        "json_field_present",
        "json_field_equals",
        "json_field_in",
        "header_present",
        "body_contains",
        "response_time_ms",
    ]
    expected: Any = None
    path: str = ""  # json field path for json_field_*
    description: str = ""


class InputBinding(BaseModel):
    """Describes how runtime input values are bound for a task."""

    source_task_id: str = ""
    source_path: str = ""  # e.g. response.body.id
    target_path: str = ""  # e.g. url.path.order_id
    literal_value: Any = None


class ApiTestTask(BaseModel):
    """One executable API test task inside a campaign."""

    task_id: str
    name: str = ""
    method: str
    path: str
    full_url: str = ""
    capability: str = ""
    execution_mode: str = "read"  # read / write / auth
    depends_on: list[str] = Field(default_factory=list)
    auth_ref: str = ""  # credential_session_id
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_query: dict[str, Any] = Field(default_factory=dict)
    request_body: dict[str, Any] = Field(default_factory=dict)
    input_bindings: list[InputBinding] = Field(default_factory=list)
    assertions: list[AssertionSpec] = Field(default_factory=list)
    resource_locks: list[str] = Field(default_factory=list)
    timeout_seconds: float = 30.0

    # Runtime-managed fields
    status: str = "pending"
    attempts: int = 0
    last_error: str = ""
    started_at: str = ""
    completed_at: str = ""
    response_status: int | None = None
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body: Any = None
    check_results: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: float = 0.0
    worker_session_id: str = ""
    worker_status: str = ""
    worker_summary: str = ""
    execution_checkpoint: dict[str, Any] = Field(default_factory=dict)

    @property
    def idempotency_key(self) -> str:
        """Stable logical request identity; attempt count is intentionally excluded."""
        return f"api-task:{self.task_id}:{self.method.upper()}:{self.full_url or self.path}"


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


class ExecutionPolicy(BaseModel):
    """Concurrency and retry rules for a campaign."""

    max_workers: int = 2
    read_parallel: bool = True
    write_serial: bool = True
    serialize_by_auth: bool = True
    serialize_by_resource_lock: bool = True
    max_retries: int = 0
    request_timeout_seconds: float = 30.0


class ApiTestCampaign(BaseModel):
    """Represents one API test campaign."""

    campaign_id: str
    project_name: str = ""
    project_url: str = ""
    objective: str = ""
    verification_focus: str = "general"
    selected_document_ids: list[str] = Field(default_factory=list)
    selected_endpoints: list[EndpointCandidate] = Field(default_factory=list)
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    credential_session_id: str = ""
    tasks: list[ApiTestTask] = Field(default_factory=list)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    created_at: str = ""
    updated_at: str = ""


class CampaignReport(BaseModel):
    """Aggregated campaign outcome."""

    campaign_id: str = ""
    project_name: str = ""
    summary: str = ""
    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    duration_ms: float = 0.0
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    verification_result: dict[str, Any] = Field(default_factory=dict)
    evaluation_result: dict[str, Any] = Field(default_factory=dict)
    generated_at: str = ""


class ApiTaskEventRecord(BaseModel):
    """A checkpoint event emitted while an API campaign is executing."""

    event_id: str = ""
    event_type: str = ""  # task_running / task_completed / task_failed / task_skipped
    task_id: str = ""
    task_name: str = ""
    method: str = ""
    path: str = ""
    status: str = ""
    phase: str = ""
    attempts: int = 0
    response_status: int | None = None
    duration_ms: float = 0.0
    worker_session_id: str = ""
    summary: str = ""
    error: str = ""
    at: str = ""


# ---------------------------------------------------------------------------
# Full state machine
# ---------------------------------------------------------------------------


class ApiTestingState(BaseModel):
    """Top-level state captured per session for the API testing mode."""

    session_id: str = ""
    trace_id: str = ""
    phase: str = "request_resolved"
    previous_phase: str = ""
    selected_agent: str = ""
    selected_tools: list[str] = Field(default_factory=list)
    context_refs: list[dict[str, Any]] = Field(default_factory=list)
    request: ApiTestingRequestState = Field(default_factory=ApiTestingRequestState)
    pending_selection: PendingSelection | None = None
    project_candidates: list[ProjectCandidate] = Field(default_factory=list)
    selected_project: ProjectCandidate | None = None
    document_candidates: list[DocumentCandidate] = Field(default_factory=list)
    selected_documents: list[DocumentCandidate] = Field(default_factory=list)
    endpoint_candidates: list[EndpointCandidate] = Field(default_factory=list)
    selected_scope: str = ""
    selected_endpoints: list[EndpointCandidate] = Field(default_factory=list)
    auth_hint: dict[str, Any] = Field(default_factory=dict)
    credential_session: CredentialSession | None = None
    campaign: ApiTestCampaign | None = None
    report: CampaignReport | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    verification_result: dict[str, Any] = Field(default_factory=dict)
    evaluation_result: dict[str, Any] = Field(default_factory=dict)
    execution_checkpoint: dict[str, Any] = Field(default_factory=dict)
    task_events: list[ApiTaskEventRecord] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    last_updated_at: str = ""
    history: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def record_phase_transition(self, new_phase: str, reason: str = "") -> None:
        """Record a phase change and keep track of the previous one."""
        if self.phase != new_phase:
            self.previous_phase = self.phase
        self.phase = new_phase
        self.last_updated_at = datetime.now(timezone.utc).isoformat()
        if reason:
            self.history.append(
                {
                    "phase": new_phase,
                    "reason": reason,
                    "at": self.last_updated_at,
                }
            )


__all__ = [
    "ApiTestingRequestState",
    "ProjectCandidate",
    "DocumentCandidate",
    "EndpointCandidate",
    "PendingSelection",
    "CredentialSession",
    "AssertionSpec",
    "InputBinding",
    "ApiTestTask",
    "ExecutionPolicy",
    "ApiTestCampaign",
    "CampaignReport",
    "ApiTaskEventRecord",
    "ApiTestingState",
]
