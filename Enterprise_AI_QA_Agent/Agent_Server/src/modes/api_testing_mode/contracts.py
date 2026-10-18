"""API Testing Mode contracts.

Defines phase constants, selection kinds, auth types, and task statuses used
across the mode runtime and its supporting services.
"""
from __future__ import annotations

from typing import Literal


# ---------------------------------------------------------------------------
# Phase machine
# ---------------------------------------------------------------------------

PHASE_REQUEST_RESOLVED = "request_resolved"
PHASE_PROJECT_CANDIDATES_FOUND = "project_candidates_found"
PHASE_AWAITING_PROJECT_SELECTION = "awaiting_project_selection"
PHASE_DOCUMENT_SELECTED = "document_selected"
PHASE_ENDPOINT_CANDIDATES_FOUND = "endpoint_candidates_found"
PHASE_AWAITING_ENDPOINT_SCOPE_SELECTION = "awaiting_endpoint_scope_selection"
PHASE_AWAITING_ENDPOINT_SELECTION = "awaiting_endpoint_selection"
PHASE_AWAITING_AUTH_INPUT = "awaiting_auth_input"
PHASE_CAMPAIGN_READY = "campaign_ready"
PHASE_TASK_DISPATCHING = "task_dispatching"
PHASE_TASK_RUNNING = "task_running"
PHASE_REPORT_READY = "report_ready"
PHASE_FAILED = "failed"

ApiTestingPhase = Literal[
    "request_resolved",
    "project_candidates_found",
    "awaiting_project_selection",
    "document_selected",
    "endpoint_candidates_found",
    "awaiting_endpoint_scope_selection",
    "awaiting_endpoint_selection",
    "awaiting_auth_input",
    "campaign_ready",
    "task_dispatching",
    "task_running",
    "report_ready",
    "failed",
]

AWAITING_PHASES = frozenset(
    {
        PHASE_AWAITING_PROJECT_SELECTION,
        PHASE_AWAITING_ENDPOINT_SCOPE_SELECTION,
        PHASE_AWAITING_ENDPOINT_SELECTION,
        PHASE_AWAITING_AUTH_INPUT,
    }
)

TERMINAL_PHASES = frozenset({PHASE_REPORT_READY, PHASE_FAILED})


# ---------------------------------------------------------------------------
# Selection kinds (what the user is being asked to pick)
# ---------------------------------------------------------------------------

SELECTION_KIND_PROJECT = "project"
SELECTION_KIND_ENDPOINT_SCOPE = "endpoint_scope"
SELECTION_KIND_ENDPOINTS = "endpoints"
SELECTION_KIND_CREDENTIAL = "credential"

SelectionKind = Literal["project", "endpoint_scope", "endpoints", "credential"]


# ---------------------------------------------------------------------------
# Endpoint scope preferences
# ---------------------------------------------------------------------------

SCOPE_ALL_RELATED = "all_related"
SCOPE_CORE_ONLY = "core_only"
SCOPE_MANUAL_PICK = "manual_pick"
SCOPE_SINGLE_TARGET = "single_target"

EndpointScope = Literal["all_related", "core_only", "manual_pick", "single_target"]

SCOPE_LABELS: dict[str, str] = {
    SCOPE_ALL_RELATED: "全部相关接口",
    SCOPE_CORE_ONLY: "核心接口（推荐）",
    SCOPE_MANUAL_PICK: "手动挑选接口",
    SCOPE_SINGLE_TARGET: "只测单个接口",
}


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------

TASK_PENDING = "pending"
TASK_BLOCKED = "blocked"
TASK_READY = "ready"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_SKIPPED = "skipped"
TASK_DEFERRED = "deferred"

TaskStatus = Literal[
    "pending", "blocked", "ready", "running", "completed", "failed", "skipped", "deferred"
]


# ---------------------------------------------------------------------------
# Auth types
# ---------------------------------------------------------------------------

AUTH_NONE = "none"
AUTH_BEARER = "bearer"
AUTH_API_KEY = "api_key"
AUTH_BASIC = "basic"
AUTH_COOKIE = "cookie"
AUTH_CUSTOM = "custom"

AuthType = Literal["none", "bearer", "api_key", "basic", "cookie", "custom"]


# ---------------------------------------------------------------------------
# Execution modes for individual tasks
# ---------------------------------------------------------------------------

EXECUTION_MODE_READ = "read"
EXECUTION_MODE_WRITE = "write"
EXECUTION_MODE_AUTH = "auth"

ExecutionMode = Literal["read", "write", "auth"]

# HTTP methods that mutate server state.
WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ---------------------------------------------------------------------------
# Precondition kinds
# ---------------------------------------------------------------------------

PRECOND_AUTH_TOKEN = "auth_token"
PRECOND_SESSION_COOKIE = "session_cookie"
PRECOND_TENANT_CONTEXT = "tenant_context"
PRECOND_RESOURCE_ID_DEPENDENCY = "resource_id_dependency"
PRECOND_TEST_DATA_SEED = "test_data_seed"
PRECOND_ENVIRONMENT_READY = "environment_ready"


# ---------------------------------------------------------------------------
# Campaign state key used inside session.metadata and context_bundle
# ---------------------------------------------------------------------------

STATE_METADATA_KEY = "api_testing_state"
REQUEST_CONTEXT_KEY = "api_testing_request"


__all__ = [
    "PHASE_REQUEST_RESOLVED",
    "PHASE_PROJECT_CANDIDATES_FOUND",
    "PHASE_AWAITING_PROJECT_SELECTION",
    "PHASE_DOCUMENT_SELECTED",
    "PHASE_ENDPOINT_CANDIDATES_FOUND",
    "PHASE_AWAITING_ENDPOINT_SCOPE_SELECTION",
    "PHASE_AWAITING_ENDPOINT_SELECTION",
    "PHASE_AWAITING_AUTH_INPUT",
    "PHASE_CAMPAIGN_READY",
    "PHASE_TASK_DISPATCHING",
    "PHASE_TASK_RUNNING",
    "PHASE_REPORT_READY",
    "PHASE_FAILED",
    "AWAITING_PHASES",
    "TERMINAL_PHASES",
    "ApiTestingPhase",
    "SELECTION_KIND_PROJECT",
    "SELECTION_KIND_ENDPOINT_SCOPE",
    "SELECTION_KIND_ENDPOINTS",
    "SELECTION_KIND_CREDENTIAL",
    "SelectionKind",
    "SCOPE_ALL_RELATED",
    "SCOPE_CORE_ONLY",
    "SCOPE_MANUAL_PICK",
    "SCOPE_SINGLE_TARGET",
    "SCOPE_LABELS",
    "EndpointScope",
    "TASK_PENDING",
    "TASK_BLOCKED",
    "TASK_READY",
    "TASK_RUNNING",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_SKIPPED",
    "TaskStatus",
    "AUTH_NONE",
    "AUTH_BEARER",
    "AUTH_API_KEY",
    "AUTH_BASIC",
    "AUTH_COOKIE",
    "AUTH_CUSTOM",
    "AuthType",
    "EXECUTION_MODE_READ",
    "EXECUTION_MODE_WRITE",
    "EXECUTION_MODE_AUTH",
    "ExecutionMode",
    "WRITE_METHODS",
    "PRECOND_AUTH_TOKEN",
    "PRECOND_SESSION_COOKIE",
    "PRECOND_TENANT_CONTEXT",
    "PRECOND_RESOURCE_ID_DEPENDENCY",
    "PRECOND_TEST_DATA_SEED",
    "PRECOND_ENVIRONMENT_READY",
    "STATE_METADATA_KEY",
    "REQUEST_CONTEXT_KEY",
]
