"""Performance report builder.

Constructs PerfReport from parsed metrics: SLA judgment, error classification,
engine threshold crosscheck, load-side observations, and markdown/HTML output.
"""
from __future__ import annotations

import uuid
import logging
from html import escape
from typing import Any
from src.application.performance.request_validation import request_validation

from src.modes.performance_testing_mode.contracts import (
    RUN_INTENT_PROBE,
    RUN_INTENT_REGRESSION,
    VERDICT_BASELINE,
    VERDICT_FAIL,
    VERDICT_INCONCLUSIVE,
    VERDICT_PASS,
    Verdict,
)
from src.modes.performance_testing_mode.plan_state import (
    BaselineComparison,
    EngineThresholdCrosscheck,
    ErrorBreakdown,
    PerfMetrics,
    PerfPlan,
    PerfReport,
    PerfRun,
    SLAConfig,
    SLAResult,
    SLAViolation,
)
from src.modes.performance_testing_mode.result_parser import ParsedMetrics

logger = logging.getLogger(__name__)

class PerfReportBuilder:
    """Builds a complete PerfReport from parsed metrics and plan context."""

    def build(
        self,
        parsed: ParsedMetrics,
        plan: PerfPlan,
        run: PerfRun,
        baseline_metrics: PerfMetrics | None = None,
    ) -> PerfReport:
        report = PerfReport(
            report_id=f"rpt-{uuid.uuid4().hex[:8]}",
            run_id=run.run_id,
            run_intent=plan.run_intent,
            metrics=parsed.metrics,
            execution_status=run.status,
            engine_exit_code=run.exit_code,
            evidence_status=run.evidence.get("status"),
            evidence_warning=str(run.evidence.get("error") or ", ".join(run.evidence.get("missing_artifacts", []))),
            request_validation=request_validation(parsed.raw, plan),
        )

        report.error_breakdown = self._classify_errors(parsed, run)
        report.sla_result = self._judge_sla(parsed.metrics, plan.sla, plan.run_intent)
        report.engine_threshold_crosscheck = self._crosscheck_thresholds(
            run.engine_thresholds, report.sla_result
        )

        if baseline_metrics and plan.run_intent == RUN_INTENT_REGRESSION:
            report.baseline_comparison = self._compare_baseline(
                parsed.metrics, baseline_metrics
            )

        report.load_side_observations = self._observe(parsed, plan)
        report.verdict = self._decide_verdict(report)
        logger.info("performance_report_judged", extra={"run_id": run.run_id,
            "verdict": report.verdict, "request_validation_status": report.request_validation.get("status"),
            "failed_checks": report.request_validation.get("failed_checks", []),
            "missing_checks": report.request_validation.get("missing_checks", []),
            "engine_exit_code": run.exit_code, "evidence_status": report.evidence_status})
        report.report_markdown = self._render_markdown(report)
        report.report_html = self._render_html(report)

        return report

    def _classify_errors(self, parsed: ParsedMetrics, run: PerfRun) -> ErrorBreakdown:
        raw_data = parsed.raw
        if not raw_data:
            return ErrorBreakdown()

        metrics = raw_data.get("metrics", {})
        status_codes = self._extract_status_codes(raw_data)

        protocol_errors = sum(
            count for code, count in status_codes.items()
            if code.startswith("5") or code in ("0", "timeout")
        )
        throttle_errors = sum(
            count for code, count in status_codes.items()
            if code in ("429", "503")
        )
        application_errors = sum(
            count for code, count in status_codes.items()
            if code.startswith("4") and code not in ("429",)
        )

        return ErrorBreakdown(
            protocol_errors=protocol_errors,
            application_errors=application_errors,
            expected_throttle=throttle_errors,
        )

    def _judge_sla(
        self, metrics: PerfMetrics, sla: SLAConfig, intent: str
    ) -> SLAResult:
        violations: list[SLAViolation] = []
        missing = ["samples"] if metrics.samples is None or metrics.samples <= 0 else []
        if intent != RUN_INTENT_PROBE:
            for name, threshold in (
                ("p95_ms", sla.p95_ms), ("p99_ms", sla.p99_ms),
                ("error_rate", sla.error_rate), ("throughput_tps", sla.min_tps),
            ):
                if threshold is None:
                    continue
                actual = getattr(metrics, name)
                if actual is None:
                    missing.append(name)
                elif (actual < threshold if name == "throughput_tps" else actual > threshold):
                    violations.append(SLAViolation(metric=name, actual=actual, threshold=threshold))
        return SLAResult(
            passed=False if violations else (None if missing else True),
            violations=violations, missing_metrics=missing,
        )

    def _crosscheck_thresholds(
        self, engine_thresholds: dict[str, Any], sla_result: SLAResult
    ) -> EngineThresholdCrosscheck:
        if not engine_thresholds:
            return EngineThresholdCrosscheck(agree=True, detail="引擎未输出 thresholds")

        if sla_result.passed is None:
            return EngineThresholdCrosscheck(agree=False, detail="缺少测量，无法与引擎判定交叉验证")

        engine_passed = all(
            v.get("ok", True) if isinstance(v, dict) else True
            for v in engine_thresholds.values()
        )
        sla_passed = sla_result.passed

        if engine_passed == sla_passed:
            return EngineThresholdCrosscheck(agree=True, detail="引擎判定与 SLA 判定一致")

        return EngineThresholdCrosscheck(
            agree=False,
            detail=(
                f"引擎判定={'通过' if engine_passed else '未通过'}，"
                f"SLA 判定={'通过' if sla_passed else '未通过'}，存在分歧需人工确认"
            ),
        )

    def _compare_baseline(
        self, current: PerfMetrics, baseline: PerfMetrics
    ) -> BaselineComparison:
        if baseline.p95_ms is None or baseline.p95_ms <= 0 or current.p95_ms is None:
            return BaselineComparison()

        delta_pct = ((current.p95_ms - baseline.p95_ms) / baseline.p95_ms) * 100
        regressed = delta_pct > 10.0

        return BaselineComparison(p95_delta_pct=round(delta_pct, 2), regressed=regressed)

    def _observe(self, parsed: ParsedMetrics, plan: PerfPlan) -> list[str]:
        observations: list[str] = []
        m = parsed.metrics

        if m.error_rate is not None and m.error_rate > 0.1:
            observations.append(f"错误率较高 ({m.error_rate*100:.1f}%)，建议检查服务端日志")

        if m.p99_ms is not None and m.p95_ms is not None and m.p99_ms > 0 and m.p95_ms > 0:
            tail_ratio = m.p99_ms / m.p95_ms
            if tail_ratio > 3.0:
                observations.append(
                    f"长尾效应明显 (P99/P95={tail_ratio:.1f}x)，可能存在 GC 或锁竞争"
                )

        if m.max_ms is not None and m.p99_ms is not None and m.max_ms > m.p99_ms * 5 and m.max_ms > 5000:
            observations.append(
                f"极端异常值 (max={m.max_ms:.0f}ms >> P99={m.p99_ms:.0f}ms)，"
                "建议排查超时/重试"
            )

        if parsed.inflection_point:
            observations.append(
                f"检测到拐点约在 {parsed.inflection_point:.0f} rps/VU 处，"
                "超过此负载后延迟急剧上升"
            )

        if m.throughput_tps is not None and m.throughput_tps > 0 and plan.workload.target_rate_rps:
            achieved_ratio = m.throughput_tps / plan.workload.target_rate_rps
            if achieved_ratio < 0.8:
                observations.append(
                    f"实际吞吐 ({m.throughput_tps:.0f} tps) 未达目标 "
                    f"({plan.workload.target_rate_rps} rps) 的 80%，系统可能已饱和"
                )

        return observations

    def _decide_verdict(self, report: PerfReport) -> Verdict:
        if report.request_validation.get("status") == "failed":
            return VERDICT_FAIL
        if report.sla_result.passed is False:
            return VERDICT_FAIL
        if report.baseline_comparison and report.baseline_comparison.regressed:
            return VERDICT_FAIL
        if report.sla_result.passed is None:
            return VERDICT_INCONCLUSIVE
        if report.request_validation.get("status") == "missing":
            return VERDICT_INCONCLUSIVE
        if (report.execution_status != "completed" or report.engine_exit_code not in {0, None}
                or report.evidence_status not in {None, "saved"}):
            return VERDICT_INCONCLUSIVE
        if report.run_intent == RUN_INTENT_PROBE:
            return VERDICT_BASELINE

        if report.baseline_comparison and report.baseline_comparison.p95_delta_pct is None:
            return VERDICT_INCONCLUSIVE

        if not report.engine_threshold_crosscheck.agree:
            return VERDICT_INCONCLUSIVE
        return VERDICT_PASS

    @staticmethod
    def _measurement(value: float | int | None, suffix: str = "", precision: int = 1, scale: float = 1) -> str:
        return "未采集" if value is None else f"{value * scale:.{precision}f}{suffix}"

    def _render_markdown(self, report: PerfReport) -> str:
        lines: list[str] = []
        m = report.metrics

        lines.append(f"# 性能测试报告 ({report.report_id})")
        lines.append("")
        lines.append(f"**运行 ID**: {report.run_id}")
        lines.append(f"**意图**: {report.run_intent}")
        lines.append(f"**结论**: {report.verdict}")
        lines.append(f"**引擎状态**: {report.execution_status}；退出码: {report.engine_exit_code}")
        if report.evidence_status:
            lines.append(f"**原件状态**: {report.evidence_status}")
        if report.evidence_warning:
            lines.append(f"**证据缺口**: {report.evidence_warning}")
        if report.request_validation.get("status") not in {None, "not_requested"}:
            lines.append(f"**响应核验**: {report.request_validation['status']}")
            for key in ("failed_checks", "missing_checks"):
                if report.request_validation.get(key):
                    lines.append(f"- {key}: " + ", ".join(report.request_validation[key]))
        lines.append("")

        lines.append("## 核心指标")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 样本数 | {self._measurement(m.samples, precision=0)} |")
        lines.append(f"| 吞吐量 | {self._measurement(m.throughput_tps, ' tps')} |")
        lines.append(f"| 平均延迟 | {self._measurement(m.avg_ms, ' ms')} |")
        lines.append(f"| P50 | {self._measurement(m.p50_ms, ' ms')} |")
        lines.append(f"| P90 | {self._measurement(m.p90_ms, ' ms')} |")
        lines.append(f"| P95 | {self._measurement(m.p95_ms, ' ms')} |")
        lines.append(f"| P99 | {self._measurement(m.p99_ms, ' ms')} |")
        lines.append(f"| 错误率 | {self._measurement(m.error_rate, '%', 2, 100)} |")
        lines.append("")

        if report.error_breakdown.protocol_errors or report.error_breakdown.application_errors:
            lines.append("## 错误分类")
            lines.append("")
            eb = report.error_breakdown
            lines.append(f"- 协议错误 (5xx/timeout): {eb.protocol_errors}")
            lines.append(f"- 应用错误 (4xx): {eb.application_errors}")
            lines.append(f"- 预期限流 (429/503): {eb.expected_throttle}")
            lines.append("")

        if report.sla_result.missing_metrics:
            lines.append("## 测量缺口")
            lines.append("")
            lines.append("- 无有效样本或未采集必要指标：" + ", ".join(report.sla_result.missing_metrics))
            lines.append("")
        if report.sla_result.violations:
            lines.append("## SLA 违规")
            lines.append("")
            for v in report.sla_result.violations:
                comparator = "<" if v.metric == "throughput_tps" else ">"
                lines.append(f"- **{v.metric}**: 实际 {v.actual:.2f} {comparator} 阈值 {v.threshold:.2f}")
            lines.append("")

        if not report.engine_threshold_crosscheck.agree:
            lines.append("## 引擎交叉验证")
            lines.append("")
            lines.append(f"> {report.engine_threshold_crosscheck.detail}")
            lines.append("")

        if report.baseline_comparison:
            lines.append("## 基线对比")
            lines.append("")
            bc = report.baseline_comparison
            if bc.p95_delta_pct is None:
                lines.append("- P95 变化: 无法计算（缺少测量或基线为零）")
            else:
                direction = "↑" if bc.p95_delta_pct > 0 else "↓"
                lines.append(f"- P95 变化: {direction} {abs(bc.p95_delta_pct):.1f}%")
                lines.append(f"- 回归判定: {'是' if bc.regressed else '否'}")
            lines.append("")

        if report.load_side_observations:
            lines.append("## 负载侧观测")
            lines.append("")
            for obs in report.load_side_observations:
                lines.append(f"- {obs}")
            lines.append("")
            lines.append(f"> {report.bottleneck_note}")
            lines.append("")

        return "\n".join(lines)

    def _render_html(self, report: PerfReport) -> str:
        md = report.report_markdown or self._render_markdown(report)
        lines: list[str] = [
            "<!DOCTYPE html>",
            '<html lang="zh-CN"><head><meta charset="utf-8">',
            f"<title>性能报告 {report.report_id}</title>",
            "<style>",
            "body{font-family:system-ui;max-width:800px;margin:2rem auto;padding:0 1rem}",
            "table{border-collapse:collapse;width:100%}",
            "th,td{border:1px solid #ddd;padding:8px;text-align:left}",
            "th{background:#f5f5f5}",
            ".pass{color:#16a34a}.fail{color:#dc2626}.baseline{color:#2563eb}.inconclusive{color:#b45309}",
            "</style></head><body>",
        ]

        verdict_class = report.verdict
        lines.append(
            f'<h1>性能测试报告 <span class="{verdict_class}">[{report.verdict.upper()}]</span></h1>'
        )
        lines.append(f"<p>运行 ID: {report.run_id} | 意图: {report.run_intent}</p>")
        lines.append(f"<p>引擎状态: {escape(report.execution_status)} | 退出码: {report.engine_exit_code}</p>")
        if report.evidence_status:
            lines.append(f"<p>原件状态: {escape(report.evidence_status)}</p>")
        if report.evidence_warning:
            lines.append(f"<p>证据缺口: {escape(report.evidence_warning)}</p>")
        if report.request_validation.get("status") not in {None, "not_requested"}:
            lines.append(f"<p>响应核验: {escape(report.request_validation['status'])}</p>")
            for key in ("failed_checks", "missing_checks"):
                if report.request_validation.get(key):
                    lines.append(f"<p>{key}: {escape(', '.join(report.request_validation[key]))}</p>")

        m = report.metrics
        lines.append("<h2>核心指标</h2><table><tr><th>指标</th><th>值</th></tr>")
        rows = [
            ("样本数", self._measurement(m.samples, precision=0)),
            ("吞吐量", self._measurement(m.throughput_tps, " tps")),
            ("P95", self._measurement(m.p95_ms, " ms")),
            ("P99", self._measurement(m.p99_ms, " ms")),
            ("错误率", self._measurement(m.error_rate, "%", 2, 100)),
        ]
        for label, val in rows:
            lines.append(f"<tr><td>{label}</td><td>{val}</td></tr>")
        lines.append("</table>")

        if report.sla_result.missing_metrics:
            lines.append("<h2>测量缺口</h2><p>无有效样本或未采集必要指标：" + ", ".join(report.sla_result.missing_metrics) + "</p>")

        if report.load_side_observations:
            lines.append("<h2>负载侧观测</h2><ul>")
            for obs in report.load_side_observations:
                lines.append(f"<li>{obs}</li>")
            lines.append("</ul>")
            lines.append(f"<blockquote>{report.bottleneck_note}</blockquote>")

        lines.append("</body></html>")
        return "\n".join(lines)

    @staticmethod
    def _extract_status_codes(raw_data: dict[str, Any]) -> dict[str, int]:
        metrics = raw_data.get("metrics", {})
        codes: dict[str, int] = {}

        for key, val in metrics.items():
            if key.startswith("http_req_status{status:"):
                code = key.split("status:")[1].rstrip("}")
                count = 0
                if isinstance(val, dict):
                    count = int(val.get("values", {}).get("count", 0))
                codes[code] = count

        return codes
