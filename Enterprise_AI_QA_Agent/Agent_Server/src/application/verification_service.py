from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

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
        return results

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
