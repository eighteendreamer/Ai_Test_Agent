"""API Testing Mode evaluation policy.

Implements the Evaluation Harness for API testing campaigns:
- Coverage assessment (what percentage of available endpoints were tested)
- Severity grading (auth failure > logic error > timeout > assertion)
- Quality score computation
- Structured evaluation output for reporting
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestTask, CampaignReport
from src.modes.api_testing_mode.contracts import TASK_COMPLETED, TASK_FAILED, TASK_SKIPPED
from src.modes.api_testing_mode.verification import VerificationVerdict


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

SEVERITY_CRITICAL = "critical"  # auth failures, complete service down
SEVERITY_HIGH = "high"  # business logic errors (wrong status, missing fields)
SEVERITY_MEDIUM = "medium"  # timeout, slow response
SEVERITY_LOW = "low"  # assertion captured but non-blocking
SEVERITY_INFO = "info"  # informational only


@dataclass
class FailureClassification:
    """Classification of a single task failure."""

    task_id: str
    task_name: str
    severity: str
    category: str  # auth / server_error / timeout / assertion / connection / unknown
    description: str
    response_status: int | None = None
    is_transient: bool = False  # likely to pass on retry


@dataclass
class CoverageMetrics:
    """How much of the available API surface was tested."""

    total_available_endpoints: int = 0
    tested_endpoints: int = 0
    coverage_rate: float = 0.0
    untested_capabilities: list[str] = field(default_factory=list)
    tested_capabilities: list[str] = field(default_factory=list)


@dataclass
class QualityScore:
    """Composite quality score for the campaign."""

    overall: float = 0.0  # 0.0 - 100.0
    reliability: float = 0.0  # based on pass rate
    performance: float = 0.0  # based on response times
    coverage: float = 0.0  # based on endpoint coverage
    severity_penalty: float = 0.0  # deducted for critical/high failures
    grade: str = "F"  # A / B / C / D / F


@dataclass
class EvaluationResult:
    """Complete evaluation output for a campaign."""

    campaign_id: str
    quality_score: QualityScore
    coverage: CoverageMetrics
    failure_classifications: list[FailureClassification] = field(default_factory=list)
    verification_verdict: VerificationVerdict | None = None
    recommendations: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "quality_score": {
                "overall": round(self.quality_score.overall, 1),
                "reliability": round(self.quality_score.reliability, 1),
                "performance": round(self.quality_score.performance, 1),
                "coverage": round(self.quality_score.coverage, 1),
                "severity_penalty": round(self.quality_score.severity_penalty, 1),
                "grade": self.quality_score.grade,
            },
            "coverage": {
                "total_available_endpoints": self.coverage.total_available_endpoints,
                "tested_endpoints": self.coverage.tested_endpoints,
                "coverage_rate": round(self.coverage.coverage_rate, 4),
                "untested_capabilities": self.coverage.untested_capabilities,
                "tested_capabilities": self.coverage.tested_capabilities,
            },
            "failure_classifications": [
                {
                    "task_id": f.task_id,
                    "task_name": f.task_name,
                    "severity": f.severity,
                    "category": f.category,
                    "description": f.description,
                    "response_status": f.response_status,
                    "is_transient": f.is_transient,
                }
                for f in self.failure_classifications
            ],
            "verification_verdict": self.verification_verdict.to_dict() if self.verification_verdict else None,
            "recommendations": self.recommendations,
            "summary": self.summary,
        }


class ApiTestingEvaluationPolicy:
    """Evaluate campaign quality with coverage, severity, and scoring."""

    def evaluate(
        self,
        *,
        campaign: ApiTestCampaign,
        total_available_endpoints: int = 0,
        verification_verdict: VerificationVerdict | None = None,
    ) -> EvaluationResult:
        tasks = campaign.tasks

        # 1. Classify failures.
        classifications = self._classify_failures(tasks)

        # 2. Compute coverage.
        coverage = self._compute_coverage(
            tasks=tasks,
            total_available=total_available_endpoints or len(tasks),
        )

        # 3. Compute quality score.
        quality_score = self._compute_quality_score(
            tasks=tasks,
            classifications=classifications,
            coverage=coverage,
        )

        # 4. Generate recommendations.
        recommendations = self._generate_recommendations(
            tasks=tasks,
            classifications=classifications,
            coverage=coverage,
            quality_score=quality_score,
        )

        # 5. Build summary.
        summary = self._build_summary(quality_score, coverage, classifications)
        if any(c.category == "worker_unsettled" for c in classifications):
            summary = f"执行未完成，质量评分仅供参考；需先核对子会话结果。{summary}"

        return EvaluationResult(
            campaign_id=campaign.campaign_id,
            quality_score=quality_score,
            coverage=coverage,
            failure_classifications=classifications,
            verification_verdict=verification_verdict,
            recommendations=recommendations,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Failure classification
    # ------------------------------------------------------------------

    def _classify_failures(self, tasks: list[ApiTestTask]) -> list[FailureClassification]:
        classifications: list[FailureClassification] = []
        for task in tasks:
            if task.status != TASK_FAILED:
                continue
            category, severity, is_transient = self._categorize_failure(task)
            classifications.append(
                FailureClassification(
                    task_id=task.task_id,
                    task_name=task.name,
                    severity=severity,
                    category=category,
                    description=task.last_error or f"{task.method} {task.path} failed",
                    response_status=task.response_status,
                    is_transient=is_transient,
                )
            )
        # Sort by severity.
        severity_order = {SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 1, SEVERITY_MEDIUM: 2, SEVERITY_LOW: 3, SEVERITY_INFO: 4}
        classifications.sort(key=lambda c: severity_order.get(c.severity, 5))
        return classifications

    def _categorize_failure(self, task: ApiTestTask) -> tuple[str, str, bool]:
        """Return (category, severity, is_transient)."""
        if task.request_outcome == "unknown":
            return "unknown_side_effect", SEVERITY_CRITICAL, False
        status = task.response_status or 0
        error = (task.last_error or "").lower()

        if error.startswith(("worker_timeout:", "worker_unsettled:")):
            return "worker_unsettled", SEVERITY_MEDIUM, False

        # Auth failures.
        if status in {401, 403}:
            return "auth", SEVERITY_CRITICAL, False

        # Server errors.
        if status >= 500:
            return "server_error", SEVERITY_HIGH, True

        # Timeout / connection.
        if "timeout" in error or "timed out" in error:
            return "timeout", SEVERITY_MEDIUM, True
        if "connection" in error or "refused" in error or "dns" in error:
            return "connection", SEVERITY_MEDIUM, True

        # Assertion failures (got a response but checks failed).
        if status and 200 <= status < 500:
            return "assertion", SEVERITY_HIGH, False

        # Unknown.
        return "unknown", SEVERITY_MEDIUM, False

    # ------------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------------

    def _compute_coverage(
        self,
        *,
        tasks: list[ApiTestTask],
        total_available: int,
    ) -> CoverageMetrics:
        tested = [
            t for t in tasks
            if t.status in {TASK_COMPLETED, TASK_FAILED}
            and not (t.last_error or "").lower().startswith(("worker_timeout:", "worker_unsettled:"))
        ]
        tested_capabilities = list({t.capability for t in tested if t.capability})
        all_capabilities = list({t.capability for t in tasks if t.capability})
        untested = [cap for cap in all_capabilities if cap not in tested_capabilities]

        tested_count = len(tested)
        total = max(total_available, len(tasks))
        rate = tested_count / total if total > 0 else 0.0

        return CoverageMetrics(
            total_available_endpoints=total,
            tested_endpoints=tested_count,
            coverage_rate=rate,
            untested_capabilities=untested,
            tested_capabilities=tested_capabilities,
        )

    # ------------------------------------------------------------------
    # Quality score
    # ------------------------------------------------------------------

    def _compute_quality_score(
        self,
        *,
        tasks: list[ApiTestTask],
        classifications: list[FailureClassification],
        coverage: CoverageMetrics,
    ) -> QualityScore:
        # Reliability: based on pass rate (0-40 points).
        evaluated = [t for t in tasks if t.status != TASK_SKIPPED]
        if evaluated:
            passed = sum(1 for t in evaluated if t.status == TASK_COMPLETED)
            reliability = (passed / len(evaluated)) * 40.0
        else:
            reliability = 0.0

        # Performance: based on response times (0-20 points).
        completed = [t for t in tasks if t.status == TASK_COMPLETED and t.duration_ms > 0]
        if completed:
            avg_ms = sum(t.duration_ms for t in completed) / len(completed)
            if avg_ms <= 500:
                performance = 20.0
            elif avg_ms <= 1000:
                performance = 15.0
            elif avg_ms <= 3000:
                performance = 10.0
            elif avg_ms <= 5000:
                performance = 5.0
            else:
                performance = 2.0
        else:
            performance = 0.0

        # Coverage: (0-20 points).
        coverage_score = coverage.coverage_rate * 20.0

        # Severity penalty: deduct for critical/high failures (0-20 points deducted).
        penalty = 0.0
        for classification in classifications:
            if classification.severity == SEVERITY_CRITICAL:
                penalty += 10.0
            elif classification.severity == SEVERITY_HIGH:
                penalty += 5.0
            elif classification.severity == SEVERITY_MEDIUM:
                penalty += 2.0
        penalty = min(penalty, 20.0)

        # Base score = 20 (for attempting the campaign).
        overall = 20.0 + reliability + performance + coverage_score - penalty
        overall = max(0.0, min(100.0, overall))

        grade = self._score_to_grade(overall)

        return QualityScore(
            overall=overall,
            reliability=reliability,
            performance=performance,
            coverage=coverage_score,
            severity_penalty=penalty,
            grade=grade,
        )

    def _score_to_grade(self, score: float) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    def _generate_recommendations(
        self,
        *,
        tasks: list[ApiTestTask],
        classifications: list[FailureClassification],
        coverage: CoverageMetrics,
        quality_score: QualityScore,
    ) -> list[str]:
        recommendations: list[str] = []

        # Auth issues.
        auth_failures = [c for c in classifications if c.category == "auth"]
        if auth_failures:
            recommendations.append(
                f"🔑 {len(auth_failures)} 个接口返回 401/403，请检查凭证是否正确或是否有权限访问。"
            )

        # Server errors.
        server_errors = [c for c in classifications if c.category == "server_error"]
        if server_errors:
            recommendations.append(
                f"🔥 {len(server_errors)} 个接口返回 5xx 服务端错误，建议检查目标服务是否正常运行。"
            )

        # Timeout.
        timeouts = [c for c in classifications if c.category == "timeout"]
        if timeouts:
            recommendations.append(
                f"⏱️ {len(timeouts)} 个接口超时，建议检查网络连通性或增加超时时间。"
            )

        unsettled = [c for c in classifications if c.category == "worker_unsettled"]
        if unsettled:
            recommendations.append(
                f"⏱️ {len(unsettled)} 个子会话结果未结算，请核对子会话后再决定是否重试。"
            )

        unknown_effects = [c for c in classifications if c.category == "unknown_side_effect"]
        if unknown_effects:
            recommendations.append(
                f"🛑 {len(unknown_effects)} 个写操作结果未知，必须先执行只读查证；禁止重试或继续依赖步骤。"
            )

        # Low coverage.
        if coverage.coverage_rate < 0.5:
            recommendations.append(
                f"📊 测试覆盖率仅 {coverage.coverage_rate:.0%}，建议扩大测试范围。"
            )

        # Untested capabilities.
        if coverage.untested_capabilities:
            recommendations.append(
                f"📋 以下能力未被测试：{', '.join(coverage.untested_capabilities[:5])}。"
            )

        # Performance.
        if any(t.status == TASK_COMPLETED for t in tasks) and quality_score.performance < 10.0:
            recommendations.append(
                "🐢 平均响应时间较长，建议关注接口性能。"
            )

        if not recommendations:
            recommendations.append("✅ 所有检查通过，无需额外操作。")

        return recommendations

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        quality_score: QualityScore,
        coverage: CoverageMetrics,
        classifications: list[FailureClassification],
    ) -> str:
        critical_count = sum(1 for c in classifications if c.severity == SEVERITY_CRITICAL)
        high_count = sum(1 for c in classifications if c.severity == SEVERITY_HIGH)

        return (
            f"质量评分: {quality_score.overall:.0f}/100 (等级 {quality_score.grade}) | "
            f"覆盖率: {coverage.coverage_rate:.0%} | "
            f"严重问题: {critical_count} 个 | 高优问题: {high_count} 个"
        )


# Legacy constant for backward compatibility.
API_TESTING_EVALUATION = {"policy": "api_assertion_review"}


__all__ = [
    "ApiTestingEvaluationPolicy",
    "EvaluationResult",
    "QualityScore",
    "CoverageMetrics",
    "FailureClassification",
    "API_TESTING_EVALUATION",
]
