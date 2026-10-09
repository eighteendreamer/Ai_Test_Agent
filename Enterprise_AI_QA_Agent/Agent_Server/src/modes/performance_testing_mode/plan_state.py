"""Performance Testing Mode state models.

Pydantic models for the structured state machine: request, workload model,
plan, run, report, and top-level orchestration state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from .contracts import (
    DriverConfidence,
    DriverSource,
    EngineKey,
    PerformanceTestingPhase,
    RunIntent,
    RunnerBackend,
    Verdict,
    WorkloadMode,
    WorkloadModelType,
    PHASE_INTAKE,
)


# ---------------------------------------------------------------------------
# Request state (from intake slot-filling)
# ---------------------------------------------------------------------------


class PerfTestingRequestState(BaseModel):
    model_config = ConfigDict(extra="allow")

    raw_message: str = ""
    target: str | None = None
    workload_qps: int | None = None
    workload_vus: int | None = None
    duration_seconds: int | None = None
    run_intent: RunIntent | None = None
    sla_p95_ms: float | None = None
    sla_p99_ms: float | None = None
    sla_error_rate: float | None = None
    sla_min_tps: float | None = None
    auth_type: str | None = None
    auth_credential_ref: str | None = None
    data_csv_ref: str | None = None
    engine: EngineKey | None = None
    target_confirmed: bool = False
    filled_slots: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Workload model (unified output from L1/L2/L3 drivers)
# ---------------------------------------------------------------------------


class CriticalPath(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    weight: float = 1.0
    steps: list[dict[str, Any]] = Field(default_factory=list)
    endpoints: list[dict[str, Any]] = Field(default_factory=list)


class TrafficMixEntry(BaseModel):
    path: str
    method: str = "GET"
    weight: float = 1.0


class WorkloadModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: DriverSource = "scenario"
    confidence: DriverConfidence = "low"
    description: str = ""
    workload_config: "PerfWorkloadConfig | None" = None
    critical_paths: list[CriticalPath] = Field(default_factory=list)
    traffic_mix: list[TrafficMixEntry] = Field(default_factory=list)
    peak_qps: int | None = None
    data_profile: dict[str, Any] = Field(default_factory=dict)
    sla: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# PerfPlan (planner output, consumed by script-builder)
# ---------------------------------------------------------------------------


class PerfTarget(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    method: str = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: dict[str, Any] | str | None = None


class RampStage(BaseModel):
    target: int
    duration: str


class PerfWorkloadConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: WorkloadModelType = "open"
    mode: WorkloadMode = "ramping_arrival_rate"
    target_rate_rps: int | None = None
    ramp_stages: list[RampStage] = Field(default_factory=list)
    virtual_users: int | None = None
    hold_seconds: int = 300
    think_time_ms: int = 0


class SmokeConfig(BaseModel):
    vus: int = 1
    iterations: int = 3
    expect_status: list[int] = Field(default_factory=lambda: [200, 201])
    expect_extract: list[str] = Field(default_factory=list)


class DataParam(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    source: str = "csv"
    ref: str = ""
    uniqueness: str = "per_iteration"
    exhaustion: str = "recycle"


class Correlation(BaseModel):
    extract: str
    from_path: str = ""
    into_header: str = ""
    from_target: str | None = None
    into_target: str | None = None
    header_prefix: str | None = None

    @model_serializer(mode="wrap")
    def preserve_original_fields(self, handler):
        data = handler(self)
        for name in ("from_target", "into_target", "header_prefix"):
            if getattr(self, name) is None:
                data.pop(name, None)
        return data


class Assertion(BaseModel):
    type: str
    op: str = "<"
    value: float = 0


class SLAConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    p95_ms: float | None = None
    p99_ms: float | None = None
    error_rate: float | None = None
    min_tps: float | None = None


class PerfLimits(BaseModel):
    max_vus: int = 2000
    max_rate_rps: int = 1000
    max_duration_seconds: int = 1800


class TopologyConfig(BaseModel):
    backend: RunnerBackend = "auto"
    injector_count: int = 0


class PerfPlan(BaseModel):
    model_config = ConfigDict(extra="allow")

    plan_id: str = ""
    title: str = ""
    engine: EngineKey = "k6"
    run_intent: RunIntent = "probe"
    targets: list[PerfTarget] = Field(default_factory=list)
    workload: PerfWorkloadConfig = Field(default_factory=PerfWorkloadConfig)
    smoke: SmokeConfig = Field(default_factory=SmokeConfig)
    data_params: list[DataParam] = Field(default_factory=list)
    correlations: list[Correlation] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)
    sla: SLAConfig = Field(default_factory=SLAConfig)
    baseline_ref: str | None = None
    topology: TopologyConfig = Field(default_factory=TopologyConfig)
    limits: PerfLimits = Field(default_factory=PerfLimits)


# ---------------------------------------------------------------------------
# PerfRun (runner output — raw data only, no verdict)
# ---------------------------------------------------------------------------


class SmokeResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    passed: bool = False
    checked_status: list[int] = Field(default_factory=list)
    extracted: dict[str, bool] = Field(default_factory=dict)
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    request_validation: dict[str, Any] = Field(default_factory=dict)


class PerfRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str = ""
    plan_id: str = ""
    engine: EngineKey = "k6"
    backend: str = "docker"
    container_or_cluster: str = ""
    status: str = "pending"
    smoke_result: SmokeResult | None = None
    started_at: str | None = None
    completed_at: str | None = None
    result_artifact: str = ""
    raw_metrics: dict[str, Any] = Field(default_factory=dict)
    engine_thresholds: dict[str, Any] = Field(default_factory=dict)
    html_report_artifact: str = ""
    stdout_tail: str = ""
    exit_code: int | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# PerfReport (analyst output — independent verdict)
# ---------------------------------------------------------------------------


class ErrorBreakdown(BaseModel):
    protocol_errors: int = 0
    application_errors: int = 0
    expected_throttle: int = 0


class SLAViolation(BaseModel):
    metric: str
    actual: float
    threshold: float


class SLAResult(BaseModel):
    passed: bool | None = True
    violations: list[SLAViolation] = Field(default_factory=list)
    missing_metrics: list[str] = Field(default_factory=list)


class BaselineComparison(BaseModel):
    model_config = ConfigDict(extra="allow")

    p95_delta_pct: float | None = None
    regressed: bool = False


class EngineThresholdCrosscheck(BaseModel):
    agree: bool = True
    detail: str = ""


class PerfMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    samples: int | None = None
    throughput_tps: float | None = None
    avg_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    p50_ms: float | None = None
    p90_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    error_rate: float | None = None


class PerfReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_id: str = ""
    run_id: str = ""
    run_intent: RunIntent = "probe"
    metrics: PerfMetrics = Field(default_factory=PerfMetrics)
    error_breakdown: ErrorBreakdown = Field(default_factory=ErrorBreakdown)
    sla_result: SLAResult = Field(default_factory=SLAResult)
    engine_threshold_crosscheck: EngineThresholdCrosscheck = Field(
        default_factory=EngineThresholdCrosscheck
    )
    baseline_comparison: BaselineComparison | None = None
    load_side_observations: list[str] = Field(default_factory=list)
    bottleneck_note: str = (
        "以上为负载侧观测，根因需结合服务端监控（CPU/GC/连接池/DB）确认"
    )
    verdict: Verdict = "baseline"
    report_markdown: str = ""
    report_html: str = ""
    execution_status: str = ""
    engine_exit_code: int | None = None
    evidence_status: str | None = None
    evidence_warning: str = ""
    request_validation: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level orchestration state
# ---------------------------------------------------------------------------


class PerformanceTestingState(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str = ""
    phase: PerformanceTestingPhase = PHASE_INTAKE
    request: PerfTestingRequestState = Field(default_factory=PerfTestingRequestState)
    workload_model: WorkloadModel | None = None
    plan: PerfPlan | None = None
    run: PerfRun | None = None
    smoke_result: SmokeResult | None = None
    report: PerfReport | None = None
    failure_analysis: dict[str, Any] = Field(default_factory=dict)
    intake_complete: bool = False
    doc_resolution_status: dict[str, Any] = Field(default_factory=dict)
    resolved_endpoint: dict[str, Any] = Field(default_factory=dict)
    missing_doc_reason: str = ""
    errors: list[str] = Field(default_factory=list)
    phase_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    def record_phase_transition(self, new_phase: PerformanceTestingPhase) -> None:
        self.phase_history.append({
            "from": self.phase,
            "to": new_phase,
            "at": datetime.utcnow().isoformat(),
        })
        self.phase = new_phase
        self.updated_at = datetime.utcnow().isoformat()
