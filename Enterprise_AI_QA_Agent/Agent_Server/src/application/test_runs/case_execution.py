from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.application.runtime.tool_job_service import ToolJobService
from src.application.testing.verification_service import VerificationService
from src.schemas.agent import ToolDescriptor
from src.schemas.case_management import TestCaseRecord, TestCaseVersionRecord
from src.schemas.run_management import (
    RunEvidenceRef,
    RunItemCompleteRequest,
    TestResultStatus,
    TestRunItemRecord,
    TestRunRecord,
)
from src.schemas.session import VerificationResult, VerificationStatus
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


@dataclass(frozen=True)
class CaseToolInvocation:
    tool: ToolDescriptor
    call: ModelToolCall
    context: ToolExecutionContext


@dataclass(frozen=True)
class CaseExecutionOutcome:
    completion: RunItemCompleteRequest
    tool_record: ToolExecutionRecord
    verification_results: list[VerificationResult]


class CaseExecutionBlockedError(ValueError):
    """用例定义无法无损编译或 Runner 当前能力尚未就绪。"""


class CaseExecutionAdapter:
    """把固定用例版本投影到现有 ToolRuntime，并只依据运行证据生成结果。"""

    def __init__(
        self,
        *,
        tool_resolver: Callable[[str], ToolDescriptor],
        runtime_service: ToolRuntimeService | None = None,
        tool_job_service: ToolJobService | None = None,
        verification_service: VerificationService | None = None,
    ) -> None:
        self._tool_resolver = tool_resolver
        self._runtime = runtime_service
        self._jobs = tool_job_service
        self._verification = verification_service or VerificationService()

    def build_invocation(
        self,
        *,
        case: TestCaseRecord,
        version: TestCaseVersionRecord,
        run: TestRunRecord,
        item: TestRunItemRecord,
    ) -> CaseToolInvocation:
        tool = self._tool_resolver(run.mode_key)
        if tool.owner_mode_key and tool.owner_mode_key != run.mode_key:
            raise ValueError(
                f"Mode entry tool {tool.key} does not belong to test run mode: {run.mode_key}"
            )
        if case.mode_key != run.mode_key:
            raise ValueError(f"Test case mode does not match test run: {case.id}")
        if version.case_id != case.id or item.case_version_id != version.id:
            raise ValueError(f"Test case version does not match run item: {item.id}")
        if not run.session_id:
            raise CaseExecutionBlockedError(
                f"Case-driven execution requires a bound session: {run.id}"
            )

        test_case_envelope = {
            "project_id": run.project_id,
            "run_id": run.id,
            "run_item_id": item.id,
            "case_id": case.id,
            "case_key": case.case_key,
            "version_id": version.id,
            "version": version.version,
            "title": case.title,
            "case_type": case.case_type,
            "priority": case.priority,
            "preconditions": list(version.preconditions),
            "steps": [step.model_dump(mode="json") for step in version.steps],
            "assertions": [item.model_dump(mode="json") for item in version.assertions],
            "test_data": deepcopy(version.test_data),
            "cleanup": list(version.cleanup),
            "content_hash": version.content_hash,
        }
        raw_arguments = version.test_data.get("runner_arguments", {})
        if raw_arguments and not isinstance(raw_arguments, dict):
            raise CaseExecutionBlockedError(
                f"test_data.runner_arguments must be an object for case version: {version.id}"
            )
        arguments = self._build_mode_arguments(
            case=case,
            version=version,
            raw_arguments=raw_arguments if isinstance(raw_arguments, dict) else {},
        )
        arguments["test_case"] = test_case_envelope

        call_id = f"test-run-item:{item.id}:attempt:{item.attempt_no}"
        trace_id = f"test-run:{run.id}:item:{item.id}:attempt:{item.attempt_no}"
        context_bundle = {
            "project_id": run.project_id,
            "test_run_id": run.id,
            "test_run_item_id": item.id,
            "test_case": test_case_envelope,
            f"{run.mode_key}_request": deepcopy(arguments),
        }
        context = ToolExecutionContext(
            session_id=run.session_id,
            turn_id=call_id,
            trace_id=trace_id,
            user_message=str(arguments.get("objective") or case.title),
            normalized_input=str(arguments.get("objective") or case.title),
            context_bundle=context_bundle,
            tool_key=tool.key,
            call_id=call_id,
        )
        return CaseToolInvocation(
            tool=tool,
            call=ModelToolCall(id=call_id, name=tool.key, arguments=arguments),
            context=context,
        )

    def _build_mode_arguments(
        self,
        *,
        case: TestCaseRecord,
        version: TestCaseVersionRecord,
        raw_arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """按模式把通用版本信封转换成现有 Runner 的公开参数。"""
        arguments = deepcopy(raw_arguments)
        arguments.setdefault("objective", case.title)
        if case.mode_key == "api_testing":
            return self._build_api_arguments(case, version, arguments)
        if case.mode_key == "smoke_testing":
            return self._build_smoke_arguments(case, version, arguments)
        if case.mode_key == "compatibility_testing":
            arguments.setdefault("action", "execute_approved_plan")
            return arguments
        if case.mode_key == "ui_automation":
            arguments.setdefault("direction", "browser")
            arguments.setdefault("subdirection", "test_execution")
            data = version.steps[0].data
            arguments.setdefault(
                "target_url",
                str(data.get("endpoint") or data.get("url") or data.get("page_url") or ""),
            )
            return arguments
        return arguments

    @staticmethod
    def _build_api_arguments(
        case: TestCaseRecord,
        version: TestCaseVersionRecord,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """优先复用显式 task；缺失时从步骤与断言确定性构建单接口任务。"""
        task = arguments.get("task")
        if not isinstance(task, dict):
            first_step = version.steps[0]
            data = first_step.data
            endpoint = str(
                data.get("endpoint")
                or data.get("url")
                or data.get("full_url")
                or ""
            ).strip()
            path = str(data.get("path") or endpoint).strip()
            method = str(data.get("method") or "GET").strip().upper()
            if not endpoint and not path:
                raise CaseExecutionBlockedError(
                    f"API test case step requires endpoint/url/path: {version.id}"
                )
            task = {
                "task_id": case.id,
                "name": case.title,
                "method": method,
                "path": path,
                "full_url": endpoint,
                "execution_mode": str(data.get("execution_mode") or "read"),
                "request_headers": deepcopy(data.get("headers") or {}),
                "request_query": deepcopy(data.get("query") or {}),
                "request_body": deepcopy(data.get("body") or {}),
                "assertions": [
                    {
                        "kind": assertion.kind,
                        "expected": deepcopy(assertion.expected),
                        "path": assertion.target,
                        "description": assertion.description,
                    }
                    for assertion in version.assertions
                ],
                "timeout_seconds": float(data.get("timeout_seconds") or 30.0),
            }
            arguments["task"] = task
        arguments["worker_action"] = "execute_task"
        return arguments

    @staticmethod
    def _build_smoke_arguments(
        case: TestCaseRecord,
        version: TestCaseVersionRecord,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """把可无损表达的 HTTP/页面步骤编译为单用例 SmokeExecutionPlan。"""
        arguments.setdefault("action", "execute_approved_plan")
        if isinstance(arguments.get("plan"), dict):
            return arguments
        smoke_steps: list[dict[str, Any]] = []
        expected_status = 200
        expected_fields: list[str] = []
        unsupported_assertions: list[str] = []
        smoke_assertions: list[dict[str, Any]] = []
        for assertion in version.assertions:
            smoke_assertions.append(
                {
                    "kind": assertion.kind,
                    "target": assertion.target or "response.status",
                    "expected": deepcopy(assertion.expected),
                    "operator": assertion.operator,
                    "description": assertion.description,
                }
            )
            if assertion.kind == "status_code":
                expected_status = int(assertion.expected)
            elif assertion.kind == "json_field_present" and assertion.target:
                expected_fields.append(assertion.target)
            else:
                unsupported_assertions.append(assertion.kind)
        if unsupported_assertions:
            raise CaseExecutionBlockedError(
                "Smoke runner cannot losslessly translate assertions: "
                + ", ".join(sorted(set(unsupported_assertions)))
            )
        target_url = ""
        for step in version.steps:
            data = step.data
            url = str(
                data.get("endpoint")
                or data.get("url")
                or data.get("page_url")
                or ""
            ).strip()
            if not url:
                raise CaseExecutionBlockedError(f"Smoke test step requires a URL: {version.id}")
            target_url = target_url or url
            method = str(data.get("method") or "GET").strip().upper()
            smoke_steps.append(
                {
                    "step_id": f"{case.id}-step-{step.order}",
                    "title": step.action,
                    "step_type": "api",
                    "api": {
                        "method": method,
                        "url": url,
                        "headers": deepcopy(data.get("headers") or {}),
                        "query": deepcopy(data.get("query") or {}),
                        "body": deepcopy(data.get("body")),
                        "expected_status": expected_status,
                        "expected_fields": expected_fields,
                    },
                    "assertions": deepcopy(smoke_assertions),
                }
            )
        plan_id = f"test-run-{case.id}-v{version.version}"
        arguments["plan"] = {
            "plan_id": plan_id,
            "version": version.version,
            "title": case.title,
            "objective": str(arguments.get("objective") or case.title),
            "project_scope": "",
            "target_url": target_url,
            "status": "approved_for_execution",
            "cases": [
                {
                    "case_id": case.id,
                    "title": case.title,
                    "case_type": "api",
                    "description": case.title,
                    "steps": smoke_steps,
                    "assertions": smoke_assertions,
                    "selected": True,
                    "execution_eligible": True,
                    "source_refs": [
                        {
                            "source_type": ref.source_type,
                            "source_id": ref.source_id,
                            "title": ref.label,
                            "uri": ref.uri or "",
                            "metadata": deepcopy(ref.metadata),
                        }
                        for ref in version.source_refs
                    ],
                }
            ],
        }
        arguments["selected_case_ids"] = [case.id]
        return arguments

    async def execute(
        self,
        *,
        case: TestCaseRecord,
        version: TestCaseVersionRecord,
        run: TestRunRecord,
        item: TestRunItemRecord,
    ) -> CaseExecutionOutcome:
        if self._runtime is None or self._jobs is None:
            raise RuntimeError("Case execution runtime dependencies are not configured")
        invocation = self.build_invocation(
            case=case,
            version=version,
            run=run,
            item=item,
        )
        tool_record = await self._runtime.execute(
            invocation.tool,
            invocation.call,
            invocation.context,
        )
        job_detail = (
            await self._jobs.get_job_detail(tool_record.job_id)
            if tool_record.job_id
            else None
        )
        full_output = (
            deepcopy(job_detail.output_payload)
            if job_detail is not None and isinstance(job_detail.output_payload, dict)
            else deepcopy(tool_record.output)
        )
        artifacts = list(getattr(job_detail, "artifacts", []) or [])
        tool_result = {
            "call_id": tool_record.call_id,
            "job_id": tool_record.job_id,
            "tool_key": tool_record.tool_key,
            "status": tool_record.status,
            "summary": str(
                getattr(job_detail, "summary", "") or tool_record.summary
            ),
            "output": full_output,
        }
        verification_results = self._verification.build_results(
            session_id=invocation.context.session_id,
            turn_id=invocation.context.turn_id,
            trace_id=invocation.context.trace_id,
            tool_results=[tool_result],
            context_bundle=invocation.context.context_bundle,
        )
        status = self._result_status(tool_record, verification_results)
        artifact_ids = [str(artifact.id) for artifact in artifacts]
        evidence_refs = [
            RunEvidenceRef(
                evidence_type="artifact",
                evidence_id=str(artifact.id),
                label=str(getattr(artifact, "label", "") or getattr(artifact, "path", "")),
                uri=str(getattr(artifact, "path", "") or "") or None,
                metadata={"tool_key": tool_record.tool_key},
            )
            for artifact in artifacts
        ]
        evidence_refs.extend(
            RunEvidenceRef(
                evidence_type="verification",
                evidence_id=result.id,
                label=result.verifier,
                metadata={"status": result.status.value},
            )
            for result in verification_results
        )
        actual = deepcopy(full_output)
        actual["verification_results"] = [
            result.model_dump(mode="json") for result in verification_results
        ]
        metrics = self._extract_metrics(full_output)
        summary = str(
            full_output.get("summary")
            or getattr(job_detail, "summary", "")
            or tool_record.summary
            or "Test case execution finished."
        )
        error_message = None
        if status in {"failed", "error", "blocked"}:
            error_message = str(
                full_output.get("error")
                or getattr(job_detail, "error_message", "")
                or summary
            )
        completion = RunItemCompleteRequest(
            lease_token=str(item.lease_token or ""),
            status=status,
            summary=summary,
            actual=actual,
            evidence_refs=evidence_refs,
            artifact_ids=artifact_ids,
            verification_ids=[result.id for result in verification_results],
            tool_job_id=tool_record.job_id,
            metrics=metrics,
            error_message=error_message,
        )
        return CaseExecutionOutcome(
            completion=completion,
            tool_record=tool_record,
            verification_results=verification_results,
        )

    @staticmethod
    def _result_status(
        tool_record: ToolExecutionRecord,
        verification_results: list[VerificationResult],
    ) -> TestResultStatus:
        # ToolJob 只证明工具流程结束；只有结构化 Verification 能证明用例通过。
        if tool_record.status in {"failed", "denied"}:
            return "error"
        if any(result.status == VerificationStatus.failed for result in verification_results):
            return "failed"
        if verification_results and all(
            result.status == VerificationStatus.passed for result in verification_results
        ):
            return "passed"
        return "blocked"

    @staticmethod
    def _extract_metrics(output: dict[str, Any]) -> dict[str, Any]:
        metrics = output.get("metrics")
        if isinstance(metrics, dict):
            return deepcopy(metrics)
        task_result = output.get("task_result")
        if isinstance(task_result, dict) and task_result.get("duration_ms") is not None:
            return {"duration_ms": task_result.get("duration_ms")}
        return {}
