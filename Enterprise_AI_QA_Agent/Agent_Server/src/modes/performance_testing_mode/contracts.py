"""Performance Testing Mode contracts.

Defines phase constants, task statuses, workload models, metric keys,
intake slot names, and other shared constants used across the performance testing mode.
"""
from __future__ import annotations

from typing import Literal


# ---------------------------------------------------------------------------
# Phase machine
# ---------------------------------------------------------------------------

PHASE_INTAKE = "intake"
PHASE_DOC_RESOLUTION = "doc_resolution"
PHASE_ENDPOINT_RESOLUTION = "endpoint_resolution"
PHASE_PLAN_RESOLVED = "plan_resolved"
PHASE_SCRIPT_BUILT = "script_built"
PHASE_SMOKE_VALIDATED = "smoke_validated"
PHASE_GUARD_PASSED = "guard_passed"
PHASE_PROVISIONING = "provisioning"
PHASE_LOAD_RUNNING = "load_running"
PHASE_RESULT_COLLECTED = "result_collected"
PHASE_ANALYZED = "analyzed"
PHASE_REPORT_READY = "report_ready"
PHASE_EMAIL_DELIVERED = "email_delivered"
PHASE_FAILED = "failed"
PHASE_INTERRUPTED = "interrupted"

PerformanceTestingPhase = Literal[
    "intake",
    "doc_resolution",
    "endpoint_resolution",
    "plan_resolved",
    "script_built",
    "smoke_validated",
    "guard_passed",
    "provisioning",
    "load_running",
    "result_collected",
    "analyzed",
    "report_ready",
    "email_delivered",
    "failed",
    "interrupted",
]

TERMINAL_PHASES = frozenset({
    PHASE_REPORT_READY,
    PHASE_EMAIL_DELIVERED,
    PHASE_FAILED,
    PHASE_INTERRUPTED,
})

AWAITING_PHASES = frozenset({PHASE_INTAKE})


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

TaskStatus = Literal[
    "pending", "blocked", "ready", "running", "completed", "failed", "skipped"
]


# ---------------------------------------------------------------------------
# Workload model types
# ---------------------------------------------------------------------------

WORKLOAD_MODEL_OPEN = "open"
WORKLOAD_MODEL_CLOSED = "closed"

WorkloadModelType = Literal["open", "closed"]

WORKLOAD_MODE_CONSTANT_ARRIVAL_RATE = "constant_arrival_rate"
WORKLOAD_MODE_RAMPING_ARRIVAL_RATE = "ramping_arrival_rate"
WORKLOAD_MODE_CONSTANT_VUS = "constant_vus"
WORKLOAD_MODE_RAMPING_VUS = "ramping_vus"
WORKLOAD_MODE_SPIKE = "spike"

WorkloadMode = Literal[
    "constant_arrival_rate",
    "ramping_arrival_rate",
    "constant_vus",
    "ramping_vus",
    "spike",
]


# ---------------------------------------------------------------------------
# Run intent
# ---------------------------------------------------------------------------

RUN_INTENT_PROBE = "probe"
RUN_INTENT_REGRESSION = "regression"

RunIntent = Literal["probe", "regression"]


# ---------------------------------------------------------------------------
# Engine keys
# ---------------------------------------------------------------------------

ENGINE_K6 = "k6"
ENGINE_JMETER = "jmeter"

EngineKey = Literal["k6", "jmeter"]


# ---------------------------------------------------------------------------
# Runner backend
# ---------------------------------------------------------------------------

BACKEND_AUTO = "auto"
BACKEND_LOCAL = "local"
BACKEND_DOCKER = "docker"
BACKEND_K8S = "k8s"

RunnerBackend = Literal["auto", "local", "docker", "k8s"]


# ---------------------------------------------------------------------------
# Intake slot names
# ---------------------------------------------------------------------------

SLOT_TARGET = "target"
SLOT_WORKLOAD = "workload"
SLOT_RUN_INTENT = "run_intent"
SLOT_SLA = "sla"
SLOT_AUTH = "auth"
SLOT_DATA = "data"
SLOT_ENGINE = "engine"
SLOT_TARGET_CONFIRMED = "target_confirmed"

REQUIRED_SLOTS = frozenset({SLOT_TARGET, SLOT_WORKLOAD, SLOT_RUN_INTENT, SLOT_TARGET_CONFIRMED})


# ---------------------------------------------------------------------------
# Workload driver source confidence
# ---------------------------------------------------------------------------

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

DriverConfidence = Literal["high", "medium", "low"]

DRIVER_SOURCE_TRAFFIC = "traffic_log"
DRIVER_SOURCE_API_DOC = "api_doc"
DRIVER_SOURCE_SCENARIO = "scenario"

DriverSource = Literal["traffic_log", "api_doc", "scenario"]


# ---------------------------------------------------------------------------
# State metadata keys
# ---------------------------------------------------------------------------

STATE_METADATA_KEY = "performance_testing_state"
REQUEST_CONTEXT_KEY = "performance_testing_request"


# ---------------------------------------------------------------------------
# Agent keys
# ---------------------------------------------------------------------------

AGENT_COORDINATOR = "performance-testing-agent"
AGENT_PLANNER = "perf-planner"
AGENT_SCRIPT_BUILDER = "perf-script-builder"
AGENT_CONTAINER_OPERATOR = "perf-container-operator"
AGENT_RUNNER = "perf-runner"
AGENT_ANALYST = "perf-analyst"
AGENT_FAILURE_ANALYST = "perf-failure-analyst"


# ---------------------------------------------------------------------------
# Limits and defaults
# ---------------------------------------------------------------------------

MAX_CONCURRENT_RUNS = 1
TOOL_EXEC_TIMEOUT_SECONDS = 1800
DEFAULT_SMOKE_ITERATIONS = 3
DEFAULT_MAX_VUS = 2000
DEFAULT_MAX_RATE_RPS = 1000
DEFAULT_MAX_DURATION_SECONDS = 1800


# ---------------------------------------------------------------------------
# Verdict values
# ---------------------------------------------------------------------------

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_BASELINE = "baseline"
VERDICT_INCONCLUSIVE = "inconclusive"

Verdict = Literal["pass", "fail", "baseline", "inconclusive"]


__all__ = [
    "PHASE_INTAKE",
    "PHASE_DOC_RESOLUTION",
    "PHASE_ENDPOINT_RESOLUTION",
    "PHASE_PLAN_RESOLVED",
    "PHASE_SCRIPT_BUILT",
    "PHASE_SMOKE_VALIDATED",
    "PHASE_GUARD_PASSED",
    "PHASE_PROVISIONING",
    "PHASE_LOAD_RUNNING",
    "PHASE_RESULT_COLLECTED",
    "PHASE_ANALYZED",
    "PHASE_REPORT_READY",
    "PHASE_EMAIL_DELIVERED",
    "PHASE_FAILED",
    "PHASE_INTERRUPTED",
    "PerformanceTestingPhase",
    "TERMINAL_PHASES",
    "AWAITING_PHASES",
    "TASK_PENDING",
    "TASK_BLOCKED",
    "TASK_READY",
    "TASK_RUNNING",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_SKIPPED",
    "TaskStatus",
    "WORKLOAD_MODEL_OPEN",
    "WORKLOAD_MODEL_CLOSED",
    "WorkloadModelType",
    "WORKLOAD_MODE_CONSTANT_ARRIVAL_RATE",
    "WORKLOAD_MODE_RAMPING_ARRIVAL_RATE",
    "WORKLOAD_MODE_CONSTANT_VUS",
    "WORKLOAD_MODE_RAMPING_VUS",
    "WORKLOAD_MODE_SPIKE",
    "WorkloadMode",
    "RUN_INTENT_PROBE",
    "RUN_INTENT_REGRESSION",
    "RunIntent",
    "ENGINE_K6",
    "ENGINE_JMETER",
    "EngineKey",
    "BACKEND_AUTO",
    "BACKEND_LOCAL",
    "BACKEND_DOCKER",
    "BACKEND_K8S",
    "RunnerBackend",
    "SLOT_TARGET",
    "SLOT_WORKLOAD",
    "SLOT_RUN_INTENT",
    "SLOT_SLA",
    "SLOT_AUTH",
    "SLOT_DATA",
    "SLOT_ENGINE",
    "SLOT_TARGET_CONFIRMED",
    "REQUIRED_SLOTS",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_LOW",
    "DriverConfidence",
    "DRIVER_SOURCE_TRAFFIC",
    "DRIVER_SOURCE_API_DOC",
    "DRIVER_SOURCE_SCENARIO",
    "DriverSource",
    "STATE_METADATA_KEY",
    "REQUEST_CONTEXT_KEY",
    "AGENT_COORDINATOR",
    "AGENT_PLANNER",
    "AGENT_SCRIPT_BUILDER",
    "AGENT_CONTAINER_OPERATOR",
    "AGENT_RUNNER",
    "AGENT_ANALYST",
    "AGENT_FAILURE_ANALYST",
    "MAX_CONCURRENT_RUNS",
    "TOOL_EXEC_TIMEOUT_SECONDS",
    "DEFAULT_SMOKE_ITERATIONS",
    "DEFAULT_MAX_VUS",
    "DEFAULT_MAX_RATE_RPS",
    "DEFAULT_MAX_DURATION_SECONDS",
    "VERDICT_PASS",
    "VERDICT_FAIL",
    "VERDICT_BASELINE",
    "VERDICT_INCONCLUSIVE",
    "Verdict",
]
