from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from src.application.security.command_profiles import get_profile_registry
from src.schemas.session import VerificationEvidence, VerificationResult, VerificationStatus


class VerificationService:
    def build_results(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_results: list[dict[str, Any]],
        context_bundle: dict[str, Any] | None = None,
    ) -> list[VerificationResult]:
        results: list[VerificationResult] = []
        context_bundle = context_bundle or {}
        for item in tool_results:
            tool_key = str(item.get("tool_key") or "")
            output = item.get("output") if isinstance(item.get("output"), dict) else {}
            if tool_key == "api-tester":
                results.append(self._from_api_tester(session_id, turn_id, trace_id, item, output, context_bundle))
                continue
            if tool_key == "test-case-generator":
                results.append(self._from_test_case_generator(session_id, turn_id, trace_id, item, output))
                continue
            if tool_key == "browser-automation":
                results.append(self._from_browser_automation(session_id, turn_id, trace_id, item, output))
                continue
            if tool_key == "smoke-suite-runner":
                results.append(self._from_smoke_suite_runner(session_id, turn_id, trace_id, item, output))
                continue
            if tool_key == "api-test-runner":
                results.append(self._from_api_test_runner(session_id, turn_id, trace_id, item, output))
                continue
            if tool_key == "ui-automation-runner":
                results.append(self._from_ui_automation_runner(session_id, turn_id, trace_id, item, output))
                continue
            if tool_key == "compatibility-test-runner":
                results.append(self._from_compatibility_test_runner(session_id, turn_id, trace_id, item, output))
                continue
            if tool_key == "performance-test-runner":
                results.append(
                    self._from_performance_test_runner(
                        session_id,
                        turn_id,
                        trace_id,
                        item,
                        output,
                        context_bundle,
                    )
                )
                continue
            if tool_key == "security-scan-runner":
                results.append(
                    self._from_security_scan_runner(
                        session_id,
                        turn_id,
                        trace_id,
                        item,
                        output,
                        context_bundle,
                    )
                )
                continue
        return results

    def _from_security_scan_runner(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
        context_bundle: dict[str, Any],
    ) -> VerificationResult:
        """Evaluate only explicit reviewed assertions against Runner evidence."""
        test_case = (
            context_bundle.get("test_case")
            if isinstance(context_bundle.get("test_case"), dict)
            else {}
        )
        assertions = (
            test_case.get("assertions")
            if isinstance(test_case.get("assertions"), list)
            else []
        )
        parsed_result = (
            output.get("parsed_result")
            if isinstance(output.get("parsed_result"), dict)
            else {}
        )
        findings = output.get("findings") if isinstance(output.get("findings"), list) else []
        profile_key = str(output.get("command_profile") or "").strip()
        profile_registry = get_profile_registry()
        try:
            profile_registry.verification_capabilities(profile_key)
        except KeyError:
            profile_key = ""
        runner_ok = (
            str(output.get("status") or "").lower() == "completed"
            and output.get("ok") is True
            and output.get("semantic_success") is True
        )
        assertion_results: list[dict[str, Any]] = []
        unsupported_kinds: list[str] = []
        unsupported_operators: list[str] = []
        unsupported_targets: list[str] = []
        for assertion in assertions:
            if not isinstance(assertion, dict):
                unsupported_kinds.append("invalid")
                continue
            kind = str(assertion.get("kind") or "").strip().lower()
            operator = str(assertion.get("operator") or "equals").strip().lower()
            expected = assertion.get("expected")
            if profile_key:
                validation_error = profile_registry.validate_assertion(
                    profile_key,
                    kind=kind,
                    target=str(assertion.get("target") or ""),
                    operator=operator,
                )
            else:
                validation_error = (
                    None
                    if kind == "runner_success" and operator in {"equals", "not_equals"}
                    else "unsupported_assertion_kind"
                )
            if validation_error == "unsupported_assertion_kind":
                unsupported_kinds.append(kind or "missing")
                continue
            if validation_error == "unsupported_assertion_operator":
                unsupported_operators.append(f"{kind}:{operator}")
                continue
            if kind == "runner_success":
                actual = runner_ok
                expected = True if expected is None else expected
            elif kind == "finding_count":
                actual = len(findings)
            elif kind == "parsed_field":
                target = str(assertion.get("target") or "")
                if validation_error == "unsupported_assertion_target":
                    unsupported_targets.append(target or "<empty>")
                    continue
                actual = self._read_dotted_path(
                    parsed_result,
                    target,
                )
            assertion_results.append(
                {
                    "kind": kind,
                    "target": str(assertion.get("target") or ""),
                    "operator": operator,
                    "expected": expected,
                    "actual": actual,
                    "passed": self._matches_assertion(actual, expected, operator),
                }
            )
        passed_count = sum(1 for item in assertion_results if item["passed"])
        failed_count = sum(1 for item in assertion_results if not item["passed"])
        if not runner_ok:
            status = VerificationStatus.failed
            passed_count = 0
            failed_count = max(1, len(assertions))
        elif failed_count:
            status = VerificationStatus.failed
        elif assertions and len(assertion_results) == len(assertions):
            status = VerificationStatus.passed
        elif assertion_results:
            status = VerificationStatus.partial
        else:
            status = VerificationStatus.not_run
        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="security-scan-runner",
            status=status,
            summary=str(
                output.get("summary")
                or tool_result.get("summary")
                or "Security Runner verification captured."
            ),
            assertion_count=len(assertions),
            passed_count=passed_count,
            failed_count=failed_count,
            evidence=[
                VerificationEvidence(
                    source_type="tool_job",
                    source_id=str(
                        tool_result.get("job_id")
                        or tool_result.get("call_id")
                        or uuid4()
                    ),
                    label="security_runner_result",
                    detail=str(output.get("command_profile") or ""),
                    metadata={
                        "target": output.get("target"),
                        "finding_count": len(findings),
                    },
                )
            ],
            metadata={
                "tool_key": "security-scan-runner",
                "command_profile": output.get("command_profile"),
                "target": output.get("target"),
                "runner_ok": runner_ok,
                "finding_count": len(findings),
                "assertion_results": assertion_results,
                "unsupported_assertion_kinds": sorted(set(unsupported_kinds)),
                "unsupported_assertion_operators": sorted(set(unsupported_operators)),
                "unsupported_assertion_targets": sorted(set(unsupported_targets)),
            },
            created_at=datetime.utcnow(),
        )


    @staticmethod
    def _read_dotted_path(value: dict[str, Any], path: str) -> Any:
        current: Any = value
        for part in (item for item in str(path or "").split(".") if item):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _matches_assertion(actual: Any, expected: Any, operator: str) -> bool:
        if operator == "equals":
            return actual == expected
        if operator == "not_equals":
            return actual != expected
        if operator == "contains":
            try:
                return expected in actual
            except TypeError:
                return False
        if operator == "not_contains":
            try:
                return expected not in actual
            except TypeError:
                return False
        if operator in {"lt", "lte", "gt", "gte"}:
            try:
                left = float(actual)
                right = float(expected)
            except (TypeError, ValueError):
                return False
            return {
                "lt": left < right,
                "lte": left <= right,
                "gt": left > right,
                "gte": left >= right,
            }[operator]
        return False
    def _from_performance_test_runner(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
        context_bundle: dict[str, Any],
    ) -> VerificationResult:
        """性能回归必须同时具备 Runner verdict、SLA 和引擎交叉验证证据。"""
        test_case = (
            context_bundle.get("test_case")
            if isinstance(context_bundle.get("test_case"), dict)
            else {}
        )
        assertions = (
            test_case.get("assertions")
            if isinstance(test_case.get("assertions"), list)
            else []
        )
        sla_result = (
            output.get("sla_result")
            if isinstance(output.get("sla_result"), dict)
            else {}
        )
        crosscheck = (
            output.get("engine_threshold_crosscheck")
            if isinstance(output.get("engine_threshold_crosscheck"), dict)
            else {}
        )
        violations = (
            sla_result.get("violations")
            if isinstance(sla_result.get("violations"), list)
            else []
        )
        verdict = str(output.get("verdict") or "").strip().lower()
        run_intent = str(output.get("run_intent") or "").strip().lower()
        output_status = str(output.get("status") or "").strip().lower()
        sla_passed = sla_result.get("passed") is True
        crosscheck_agrees = crosscheck.get("agree") is True
        assertion_count = len(assertions)
        metrics = output.get("metrics") if isinstance(output.get("metrics"), dict) else {}
        try:
            sample_count = int(metrics.get("samples") or 0)
        except (TypeError, ValueError):
            sample_count = 0
        performance_assertion_kinds = {
            "p95_ms",
            "p99_ms",
            "error_rate",
            "throughput_tps",
            "min_tps",
        }
        performance_assertion_count = sum(
            1
            for assertion in assertions
            if isinstance(assertion, dict)
            and str(assertion.get("kind") or "").strip().lower()
            in performance_assertion_kinds
        )
        has_performance_assertion = performance_assertion_count > 0
        performance_request = (
            context_bundle.get("performance_testing_request")
            if isinstance(context_bundle.get("performance_testing_request"), dict)
            else {}
        )
        has_explicit_sla = has_performance_assertion or any(
            performance_request.get(key) is not None
            for key in (
                "sla_p95_ms",
                "sla_p99_ms",
                "sla_error_rate",
                "sla_min_tps",
            )
        )
        baseline_comparison = (
            output.get("baseline_comparison")
            if isinstance(output.get("baseline_comparison"), dict)
            else None
        )
        baseline_regressed = (
            baseline_comparison is not None
            and baseline_comparison.get("regressed") is True
        )
        has_pass_criterion = has_explicit_sla or baseline_comparison is not None

        if (
            output_status == "completed"
            and output.get("ok") is True
            and run_intent == "regression"
            and verdict == "pass"
            and sample_count > 0
            and has_pass_criterion
            and sla_passed
            and crosscheck_agrees
            and not baseline_regressed
        ):
            status = VerificationStatus.passed
            passed_count = performance_assertion_count
            failed_count = 0
        elif verdict == "fail" or sla_result.get("passed") is False or baseline_regressed:
            status = VerificationStatus.failed
            failed_count = min(
                assertion_count,
                max(1, len(violations)),
            ) if assertion_count else max(1, len(violations))
            passed_count = max(0, assertion_count - failed_count)
        elif run_intent == "probe" and verdict == "baseline":
            status = VerificationStatus.not_run
            passed_count = 0
            failed_count = 0
        elif output_status in {"error", "failed", "blocked"} or output.get("ok") is False:
            status = VerificationStatus.failed
            passed_count = 0
            failed_count = max(1, assertion_count)
        elif run_intent == "regression" and verdict == "pass" and not has_pass_criterion:
            status = VerificationStatus.not_run
            passed_count = 0
            failed_count = 0
        else:
            status = VerificationStatus.partial if verdict else VerificationStatus.not_run
            passed_count = 0
            failed_count = 0

        core_metrics = {
            key: metrics[key]
            for key in ("samples", "throughput_tps", "p95_ms", "p99_ms", "error_rate")
            if key in metrics
        }
        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="performance-test-runner",
            status=status,
            summary=str(
                output.get("summary")
                or tool_result.get("summary")
                or "Performance Runner verification captured."
            ),
            assertion_count=assertion_count,
            passed_count=passed_count,
            failed_count=failed_count,
            evidence=[
                VerificationEvidence(
                    source_type="tool_job",
                    source_id=str(
                        tool_result.get("job_id")
                        or tool_result.get("call_id")
                        or uuid4()
                    ),
                    label="performance_runner_verdict",
                    detail=str(output.get("run_id") or ""),
                    metadata={"metrics": core_metrics},
                )
            ],
            metadata={
                "tool_key": "performance-test-runner",
                "run_id": output.get("run_id"),
                "run_intent": run_intent,
                "verdict": verdict,
                "sla_passed": sla_result.get("passed"),
                "engine_crosscheck_agree": crosscheck.get("agree"),
                "sample_count": sample_count,
                "has_pass_criterion": has_pass_criterion,
            },
            created_at=datetime.utcnow(),
        )

    def _from_api_test_runner(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
    ) -> VerificationResult:
        """从 API Runner 的真实断言或聚合验证结果投影统一 Verification。"""
        task_result = output.get("task_result") if isinstance(output.get("task_result"), dict) else {}
        checks = task_result.get("check_results") if isinstance(task_result.get("check_results"), list) else []
        if not checks and isinstance(output.get("checks"), list):
            checks = output["checks"]
        explicit = output.get("verification_result") if isinstance(output.get("verification_result"), dict) else {}
        passed_count = sum(
            1 for check in checks if isinstance(check, dict) and check.get("passed") is True
        )
        failed_count = sum(
            1 for check in checks if isinstance(check, dict) and check.get("passed") is False
        )
        if explicit:
            raw_status = str(explicit.get("status") or explicit.get("verdict") or "").lower()
            passed_count = int(explicit.get("passed_count") or explicit.get("passed_rules") or passed_count)
            failed_count = int(explicit.get("failed_count") or explicit.get("failed_rules") or failed_count)
            if raw_status in {"passed", "pass", "ready", "success"} and failed_count == 0:
                status = VerificationStatus.passed
            elif raw_status in {"failed", "fail", "blocked"} or failed_count > 0:
                status = VerificationStatus.failed
            elif raw_status in {"partial", "warning"}:
                status = VerificationStatus.partial
            else:
                status = VerificationStatus.not_run
        elif checks and failed_count == 0 and passed_count == len(checks):
            status = VerificationStatus.passed
        elif failed_count > 0 or str(task_result.get("status") or "") == "failed":
            status = VerificationStatus.failed
        elif checks:
            status = VerificationStatus.partial
        else:
            status = VerificationStatus.not_run
        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="api-test-runner",
            status=status,
            summary=str(
                explicit.get("summary")
                or output.get("summary")
                or tool_result.get("summary")
                or "API Runner verification captured."
            ),
            assertion_count=len(checks) or int(explicit.get("assertion_count") or 0),
            passed_count=passed_count,
            failed_count=failed_count,
            evidence=[
                VerificationEvidence(
                    source_type="tool_job",
                    source_id=str(tool_result.get("job_id") or tool_result.get("call_id") or uuid4()),
                    label="api_runner_assertions",
                    detail=str(output.get("summary") or ""),
                    metadata={"task_id": task_result.get("task_id")},
                )
            ],
            metadata={"tool_key": "api-test-runner", "task_status": task_result.get("status")},
            created_at=datetime.utcnow(),
        )

    def _from_ui_automation_runner(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
    ) -> VerificationResult:
        """UI 当前结构探索不等于用例断言通过；缺少执行证据时明确标记 not_run。"""
        raw = output.get("verification_result") if isinstance(output.get("verification_result"), dict) else {}
        checks = raw.get("checks") if isinstance(raw.get("checks"), list) else []
        passed_count = sum(1 for item in checks if isinstance(item, dict) and item.get("passed") is True)
        failed_count = sum(1 for item in checks if isinstance(item, dict) and item.get("passed") is False)
        if checks and failed_count == 0 and passed_count == len(checks):
            status = VerificationStatus.passed
        elif failed_count > 0:
            status = VerificationStatus.failed
        elif checks:
            status = VerificationStatus.partial
        else:
            status = VerificationStatus.not_run
        artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), list) else []
        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="ui-automation-runner",
            status=status,
            summary=str(
                raw.get("summary")
                or output.get("summary")
                or "UI 结构探索已记录，但没有可判定用例通过的断言证据。"
            ),
            assertion_count=len(checks),
            passed_count=passed_count,
            failed_count=failed_count,
            evidence=[
                VerificationEvidence(
                    source_type="artifact",
                    source_id=str(item.get("path") or uuid4()),
                    label=str(item.get("label") or item.get("type") or "ui_artifact"),
                    detail=str(item.get("path") or ""),
                    metadata={"tool_key": "ui-automation-runner"},
                )
                for item in artifacts
                if isinstance(item, dict)
            ],
            metadata={"tool_key": "ui-automation-runner", "phase": output.get("phase")},
            created_at=datetime.utcnow(),
        )

    def _from_compatibility_test_runner(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
    ) -> VerificationResult:
        """兼容性入口只派发任务；仅在 Runner 汇总含终态计数时形成通过/失败结论。"""
        summary = output.get("runner_summary") if isinstance(output.get("runner_summary"), dict) else {}
        completed_count = int(summary.get("completed") or summary.get("completed_count") or 0)
        failed_count = int(summary.get("failed") or summary.get("failed_count") or 0)
        pending_count = int(
            summary.get("pending")
            or summary.get("queued_count")
            or summary.get("running_count")
            or 0
        )
        total = int(summary.get("total") or summary.get("total_count") or 0)
        if total > 0 and failed_count == 0 and pending_count == 0 and completed_count == total:
            status = VerificationStatus.passed
        elif failed_count > 0:
            status = VerificationStatus.failed
        elif completed_count > 0:
            status = VerificationStatus.partial
        else:
            status = VerificationStatus.not_run
        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="compatibility-test-runner",
            status=status,
            summary=str(output.get("summary") or tool_result.get("summary") or "兼容性任务已派发。"),
            assertion_count=total,
            passed_count=completed_count,
            failed_count=failed_count,
            evidence=[
                VerificationEvidence(
                    source_type="tool_job",
                    source_id=str(tool_result.get("job_id") or tool_result.get("call_id") or uuid4()),
                    label="compatibility_runner_summary",
                    detail=str(output.get("phase") or ""),
                    metadata={"runner_summary": summary},
                )
            ],
            metadata={"tool_key": "compatibility-test-runner", "phase": output.get("phase")},
            created_at=datetime.utcnow(),
        )

    def _from_api_tester(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
        context_bundle: dict[str, Any],
    ) -> VerificationResult:
        checks = output.get("checks") if isinstance(output.get("checks"), list) else []
        passed_count = sum(1 for item in checks if isinstance(item, dict) and item.get("passed") is True)
        failed_count = sum(1 for item in checks if isinstance(item, dict) and item.get("passed") is False)
        status = VerificationStatus.passed if failed_count == 0 and checks else VerificationStatus.failed if failed_count > 0 else VerificationStatus.not_run
        if checks and failed_count == 0 and passed_count < len(checks):
            status = VerificationStatus.partial
        evidence = [
            VerificationEvidence(
                source_type="tool_result",
                source_id=str(tool_result.get("call_id") or tool_result.get("job_id") or uuid4()),
                label="api_checks",
                detail=str(output.get("summary") or tool_result.get("summary") or ""),
                metadata={
                    "endpoint": output.get("request", {}).get("endpoint") if isinstance(output.get("request"), dict) else "",
                    "method": output.get("request", {}).get("method") if isinstance(output.get("request"), dict) else "",
                    "verification_mode": bool(context_bundle.get("verification_mode")),
                },
            )
        ]
        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="api-tester",
            status=status,
            summary=str(output.get("summary") or tool_result.get("summary") or "API verification result captured."),
            assertion_count=len(checks),
            passed_count=passed_count,
            failed_count=failed_count,
            evidence=evidence,
            metadata={"tool_key": "api-tester", "tool_status": tool_result.get("status")},
            created_at=datetime.utcnow(),
        )

    def _from_test_case_generator(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
    ) -> VerificationResult:
        cases = output.get("cases") if isinstance(output.get("cases"), list) else []
        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="test-case-generator",
            status=VerificationStatus.not_run,
            summary=f"Generated {len(cases)} planned verification cases for downstream execution.",
            assertion_count=len(cases),
            passed_count=0,
            failed_count=0,
            evidence=[
                VerificationEvidence(
                    source_type="tool_result",
                    source_id=str(tool_result.get("call_id") or uuid4()),
                    label="planned_cases",
                    detail=str(tool_result.get("summary") or ""),
                    metadata={"case_count": len(cases)},
                )
            ],
            metadata={"tool_key": "test-case-generator", "coverage": output.get("coverage", {})},
            created_at=datetime.utcnow(),
        )

    def _from_browser_automation(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
    ) -> VerificationResult:
        steps = output.get("steps") if isinstance(output.get("steps"), list) else []
        artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), list) else []
        status = VerificationStatus.passed if str(tool_result.get("status")) == "completed" else VerificationStatus.partial
        evidence = [
            VerificationEvidence(
                source_type="artifact",
                source_id=str(item.get("path") or item.get("label") or uuid4()),
                label=str(item.get("label") or item.get("type") or "artifact"),
                detail=str(item.get("path") or ""),
                metadata={k: v for k, v in item.items() if k not in {"label", "path"}},
            )
            for item in artifacts
            if isinstance(item, dict)
        ]
        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="browser-automation",
            status=status,
            summary=str(output.get("summary") or tool_result.get("summary") or "Browser verification evidence captured."),
            assertion_count=len(steps),
            passed_count=len(steps) if status == VerificationStatus.passed else 0,
            failed_count=0 if status == VerificationStatus.passed else max(0, len(steps) - 1),
            evidence=evidence,
            metadata={"tool_key": "browser-automation", "artifact_count": len(artifacts)},
            created_at=datetime.utcnow(),
        )

    def _from_smoke_suite_runner(
        self,
        session_id: str,
        turn_id: str,
        trace_id: str,
        tool_result: dict[str, Any],
        output: dict[str, Any],
    ) -> VerificationResult:
        run_result = output.get("run_result") if isinstance(output.get("run_result"), dict) else {}
        plan = output.get("plan") if isinstance(output.get("plan"), dict) else {}
        case_results = run_result.get("case_results") if isinstance(run_result.get("case_results"), list) else []
        assertion_count = sum(int(item.get("assertion_count") or 0) for item in case_results if isinstance(item, dict))
        passed_count = int(run_result.get("passed_cases") or 0)
        failed_count = int(run_result.get("failed_cases") or 0)
        blocked_count = int(run_result.get("blocked_cases") or 0)
        verdict = str(run_result.get("verdict") or output.get("phase") or "").strip()
        if verdict == "ready":
            status = VerificationStatus.passed
        elif verdict == "blocked" or failed_count > 0:
            status = VerificationStatus.failed
        elif verdict == "partial":
            status = VerificationStatus.partial
        else:
            status = VerificationStatus.not_run

        evidence: list[VerificationEvidence] = []
        for label, uri in [
            ("smoke_plan", output.get("plan_uri")),
            ("approved_plan", output.get("approved_plan_uri")),
            ("run_result", output.get("run_result_uri")),
            ("run_report", output.get("report_uri")),
        ]:
            if uri:
                evidence.append(
                    VerificationEvidence(
                        source_type="artifact",
                        source_id=str(uri),
                        label=label,
                        detail=str(uri),
                        metadata={"tool_key": "smoke-suite-runner"},
                    )
                )
        for case in case_results[:8]:
            if not isinstance(case, dict):
                continue
            evidence.append(
                VerificationEvidence(
                    source_type="tool_result",
                    source_id=str(case.get("case_id") or uuid4()),
                    label=str(case.get("status") or "smoke_case"),
                    detail=str(case.get("summary") or case.get("title") or ""),
                    metadata={
                        "case_type": case.get("case_type"),
                        "failure_category": case.get("failure_category"),
                    },
                )
            )

        return VerificationResult(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            verifier="冒烟测试结果",
            status=status,
            summary=str(run_result.get("summary") or output.get("summary") or tool_result.get("summary") or "冒烟测试方案已生成，等待确认。"),
            assertion_count=assertion_count,
            passed_count=passed_count,
            failed_count=failed_count,
            evidence=evidence,
            metadata={
                "tool_key": "smoke-suite-runner",
                "plan_id": output.get("plan_id") or plan.get("plan_id"),
                "plan_version": output.get("plan_version") or plan.get("version"),
                "verdict": verdict,
                "blocked_count": blocked_count,
                "selected_case_count": run_result.get("selected_case_count") or len(output.get("selected_case_ids") or []),
                "total_cases": run_result.get("total_cases") or len(plan.get("cases") or []),
                "report_uri": output.get("report_uri"),
                "approved_plan_uri": output.get("approved_plan_uri"),
            },
            created_at=datetime.utcnow(),
        )
