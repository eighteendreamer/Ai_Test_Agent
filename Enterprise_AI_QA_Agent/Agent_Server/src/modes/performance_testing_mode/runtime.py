"""Performance Testing Mode Runtime.

Core state machine that drives the full lifecycle:
intake → plan → script → guard → smoke → execute → analyze → report.
"""
from __future__ import annotations

import logging
import json
import uuid
from typing import Any

from src.application.performance.engine_adapter import RawMetrics, RunOptions
from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.application.performance.perf_metrics_store import PerfMetricsStore
from src.application.performance.perf_runner_service import PerfRunnerService
from src.application.performance.perf_target_guard import PerfTargetGuard
from src.core.config import Settings
from src.modes.performance_testing_mode.contracts import (
    PHASE_ANALYZED,
    PHASE_DOC_RESOLUTION,
    PHASE_ENDPOINT_RESOLUTION,
    PHASE_FAILED,
    PHASE_GUARD_PASSED,
    PHASE_INTAKE,
    PHASE_INTERRUPTED,
    PHASE_LOAD_RUNNING,
    PHASE_PLAN_RESOLVED,
    PHASE_REPORT_READY,
    PHASE_RESULT_COLLECTED,
    PHASE_SCRIPT_BUILT,
    PHASE_SMOKE_VALIDATED,
    REQUIRED_SLOTS,
    STATE_METADATA_KEY,
    TERMINAL_PHASES,
)
from src.modes.performance_testing_mode.coordinator import PerfCoordinator
from src.modes.performance_testing_mode.failure_analyzer import PerfFailureAnalyzer
from src.modes.performance_testing_mode.intake import (
    check_slots_ready,
    generate_next_questions,
)
from src.modes.performance_testing_mode.plan_state import (
    PerfMetrics,
    PerfPlan,
    PerfReport,
    PerfRun,
    PerfTarget,
    PerfWorkloadConfig,
    PerformanceTestingState,
    SLAConfig,
    SmokeConfig,
)
from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
from src.modes.performance_testing_mode.request_interpreter import PerfRequestInterpreter
from src.modes.performance_testing_mode.result_parser import PerfResultParser
from src.modes.performance_testing_mode.workload_modeler import WorkloadModeler

logger = logging.getLogger(__name__)


class PerformanceTestingModeRuntime:
    """State machine runtime for performance testing mode."""

    def __init__(
        self,
        settings: Settings | None = None,
        coordinator_runtime_service=None,
        session_store=None,
        memory_runtime_service=None,
        api_docs_service=None,
    ):
        self._settings = settings
        self._coordinator_runtime_service = coordinator_runtime_service
        self._session_store = session_store
        self._memory_runtime_service = memory_runtime_service
        self._api_docs_service = api_docs_service
        self._interpreter = PerfRequestInterpreter()
        self._modeler = WorkloadModeler()
        self._parser = PerfResultParser()
        self._report_builder = PerfReportBuilder()
        self._coordinator = PerfCoordinator(coordinator_runtime_service)
        self._failure_analyzer = PerfFailureAnalyzer()

        if settings:
            self._guard = PerfTargetGuard(settings)
            self._runner = PerfRunnerService(settings)
            self._engine_adapter = self._create_engine_adapter(settings.performance_default_engine)
            self._metrics_store = PerfMetricsStore(settings)
        else:
            self._guard = None
            self._runner = None
            self._engine_adapter = None
            self._metrics_store = None

    def set_coordinator_runtime_service(self, svc) -> None:
        self._coordinator_runtime_service = svc
        self._coordinator.set_coordinator_runtime_service(svc)

    def set_session_store(self, store) -> None:
        self._session_store = store

    def set_memory_runtime_service(self, svc) -> None:
        self._memory_runtime_service = svc

    def _create_engine_adapter(self, engine_key: str | None = None):
        engine = (engine_key or "k6").lower()
        rewrite = self._settings.performance_rewrite_localhost if self._settings else True
        image = self._resolve_engine_image(engine) if self._settings else ""
        if engine == "k6":
            return K6EngineAdapter(
                image=image,
                rewrite_localhost=rewrite,
            )
        if engine == "jmeter":
            return JMeterEngineAdapter(
                image=image,
                rewrite_localhost=rewrite,
            )
        return None

    def _resolve_engine_image(self, engine: str) -> str:
        from src.application.images.image_resolver import ImageResolver
        resolver = ImageResolver()
        image_key = (
            self._settings.jmeter_docker_image_key
            if engine == "jmeter"
            else self._settings.k6_docker_image_key
        )
        resolution = resolver.resolve_by_key(image_key) if image_key else resolver.resolve_for_engine(engine)
        if resolution.ok:
            return resolution.selected_image
        return self._settings.jmeter_docker_image if engine == "jmeter" else self._settings.k6_docker_image

    async def handle(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        """Main entry point — restore state, advance phases, persist, return."""
        state = self._restore_state(context)

        try:
            result = await self._advance(state, arguments, context)
        except Exception as e:
            logger.exception("Performance testing runtime error")
            state.record_phase_transition(PHASE_FAILED)
            state.errors.append(str(e))
            result = {
                "status": "error",
                "ok": False,
                "phase": PHASE_FAILED,
                "summary": f"运行时异常: {e}",
                "errors": state.errors,
            }
        finally:
            self._persist_state(state, context)

        result["performance_testing_state"] = state.model_dump()
        return result

    async def _advance(
        self, state: PerformanceTestingState, arguments: dict[str, Any], context: Any
    ) -> dict[str, Any]:
        phase = state.phase

        if phase in TERMINAL_PHASES:
            return {
                "status": "completed",
                "ok": True,
                "phase": phase,
                "summary": f"性能测试已完成 (phase={phase})",
            }

        if phase == PHASE_INTAKE:
            return await self._handle_intake(state, arguments)

        if phase == PHASE_DOC_RESOLUTION:
            return await self._handle_doc_resolution(state, arguments)

        if phase == PHASE_ENDPOINT_RESOLUTION:
            return await self._handle_endpoint_resolution(state, arguments)

        if phase == PHASE_PLAN_RESOLVED:
            return await self._handle_script_build(state)

        if phase == PHASE_SCRIPT_BUILT:
            return await self._handle_guard_check(state)

        if phase == PHASE_GUARD_PASSED:
            return await self._handle_smoke(state)

        if phase == PHASE_SMOKE_VALIDATED:
            return await self._handle_execution(state)

        if phase == PHASE_LOAD_RUNNING:
            return await self._handle_result_collection(state)

        if phase == PHASE_RESULT_COLLECTED:
            return await self._handle_analysis(state)

        if phase == PHASE_ANALYZED:
            return await self._handle_report(state)

        return {"status": "error", "ok": False, "phase": phase, "summary": f"未知阶段: {phase}"}

    async def _handle_intake(
        self, state: PerformanceTestingState, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        interpreted = self._interpreter.interpret(arguments.get("objective", ""))

        for slot, value in interpreted.slots.items():
            state.request.filled_slots[slot] = value
            if slot == "engine":
                state.request.engine = value

        for key, value in arguments.items():
            if key == "target_url" and value:
                state.request.filled_slots["target"] = value
                state.request.target = value
            elif key == "target_rate_rps" and value:
                state.request.filled_slots["workload"] = f"rate={value}rps"
                state.request.workload_qps = value
            elif key == "virtual_users" and value:
                state.request.filled_slots["workload"] = f"vus={value}"
                state.request.workload_vus = value
            elif key == "run_intent" and value:
                state.request.filled_slots["run_intent"] = value
                state.request.run_intent = value
            elif key == "confirm_target" and value:
                state.request.filled_slots["target_confirmed"] = True
                state.request.target_confirmed = True
            elif key == "duration_seconds" and value:
                state.request.duration_seconds = value
            elif key == "sla_p95_ms" and value:
                state.request.sla_p95_ms = value
            elif key == "sla_p99_ms" and value:
                state.request.sla_p99_ms = value
            elif key == "sla_error_rate" and value is not None:
                state.request.sla_error_rate = value
            elif key == "engine" and value:
                state.request.filled_slots["engine"] = value
                state.request.engine = value

        ready, missing = check_slots_ready(state.request)

        if not ready:
            questions = generate_next_questions(state.request)
            return {
                "status": "awaiting_input",
                "ok": True,
                "phase": PHASE_INTAKE,
                "summary": "需要更多信息以继续",
                "questions": questions,
                "filled_slots": state.request.filled_slots,
                "missing_slots": list(missing),
            }

        if self._needs_doc_resolution(state):
            state.record_phase_transition(PHASE_DOC_RESOLUTION)
            return await self._handle_doc_resolution(state, arguments)

        plan = self._build_plan_from_slots(state)
        state.plan = plan
        state.record_phase_transition(PHASE_PLAN_RESOLVED)

        return await self._handle_script_build(state)

    def _build_plan_from_slots(self, state: PerformanceTestingState) -> PerfPlan:
        req = state.request
        slots = req.filled_slots

        target_url = req.target or slots.get("target", "")
        method = "GET"

        workload_model = "open"
        target_rate = req.workload_qps
        vus = req.workload_vus
        duration = req.duration_seconds or 60

        if vus and not target_rate:
            workload_model = "closed"

        wl_config = PerfWorkloadConfig(
            model=workload_model,
            mode="constant_arrival_rate" if workload_model == "open" else "constant_vus",
            target_rate_rps=target_rate,
            virtual_users=vus or (max((target_rate or 50) * 2, 100) if workload_model == "open" else 50),
            hold_seconds=duration,
        )

        sla = SLAConfig(
            p95_ms=req.sla_p95_ms,
            p99_ms=req.sla_p99_ms,
            error_rate=req.sla_error_rate,
            min_tps=req.sla_min_tps,
        )

        return PerfPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            title=f"压测 {target_url}",
            engine=req.engine or slots.get("engine") or (self._settings.performance_default_engine if self._settings else "k6"),
            run_intent=req.run_intent or "probe",
            targets=[PerfTarget(name="primary", url=target_url, method=method)],
            workload=wl_config,
            smoke=SmokeConfig(),
            sla=sla,
        )

    def _needs_doc_resolution(self, state: PerformanceTestingState) -> bool:
        """Check whether the target is a full URL or needs API doc resolution."""
        target = state.request.target or state.request.filled_slots.get("target", "")
        if not target:
            return False
        return not (target.startswith("http://") or target.startswith("https://"))

    async def _handle_doc_resolution(
        self, state: PerformanceTestingState, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve API docs when target is not a direct URL."""
        if not self._api_docs_service:
            return self._error("API 文档服务未初始化，无法解析接口文档", state)

        from src.application.api_doc_resolution.service import ApiDocResolutionService

        service = ApiDocResolutionService(api_docs_service=self._api_docs_service)
        project_name = arguments.get("project_name") or state.request.filled_slots.get("project_name")
        project_url = arguments.get("project_url") or state.request.filled_slots.get("project_url")

        result = await service.discover_project_docs(
            project_name=project_name,
            project_url=project_url,
        )

        state.doc_resolution_status = result.model_dump()
        state.missing_doc_reason = result.missing_doc_reason

        if result.status == "awaiting_input":
            return {
                "status": "awaiting_input",
                "ok": True,
                "phase": PHASE_DOC_RESOLUTION,
                "summary": result.summary,
                "missing_doc_reason": result.missing_doc_reason,
                "accepted_inputs": result.accepted_inputs,
                "questions": ["请上传接口文档，或提供 OpenAPI/Swagger 文档 URL。"],
            }

        state.record_phase_transition(PHASE_ENDPOINT_RESOLUTION)
        return await self._handle_endpoint_resolution(state, arguments)

    async def _handle_endpoint_resolution(
        self, state: PerformanceTestingState, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve a concrete endpoint from API docs."""
        if not self._api_docs_service:
            return self._error("API 文档服务未初始化", state)

        from src.application.api_doc_resolution.service import ApiDocResolutionService

        service = ApiDocResolutionService(api_docs_service=self._api_docs_service)
        project_name = arguments.get("project_name") or state.request.filled_slots.get("project_name")
        project_url = arguments.get("project_url") or state.request.filled_slots.get("project_url")
        endpoint_hint = arguments.get("endpoint_hint") or state.request.target or ""

        method, path = self._parse_endpoint_hint(endpoint_hint)

        result = await service.resolve_endpoint_from_docs(
            project_name=project_name,
            project_url=project_url,
            method=method,
            path=path,
            query=endpoint_hint,
        )

        state.doc_resolution_status = result.model_dump()
        state.missing_doc_reason = result.missing_doc_reason

        if result.status == "awaiting_input":
            return {
                "status": "awaiting_input",
                "ok": True,
                "phase": PHASE_ENDPOINT_RESOLUTION,
                "summary": result.summary,
                "missing_doc_reason": result.missing_doc_reason,
                "accepted_inputs": result.accepted_inputs,
                "questions": ["请上传接口文档，或提供 OpenAPI/Swagger 文档 URL。"],
            }

        if result.status == "ambiguous":
            return {
                "status": "awaiting_input",
                "ok": True,
                "phase": PHASE_ENDPOINT_RESOLUTION,
                "summary": result.summary,
                "candidates": result.candidates,
                "questions": ["多个接口候选，请确认目标接口。"],
            }

        if result.resolved_endpoint:
            ep = result.resolved_endpoint
            state.resolved_endpoint = ep.model_dump()
            full_url = ep.base_url.rstrip("/") + ep.path if ep.base_url else ep.path
            state.request.target = full_url
            state.request.filled_slots["target"] = full_url

        state.record_phase_transition(PHASE_PLAN_RESOLVED)
        plan = self._build_plan_from_slots(state)
        state.plan = plan
        return await self._handle_script_build(state)

    @staticmethod
    def _parse_endpoint_hint(hint: str) -> tuple[str | None, str | None]:
        """Parse 'POST /api/orders' or '/api/orders' into (method, path)."""
        if not hint:
            return None, None
        hint = hint.strip()
        parts = hint.split(None, 1)
        if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
            return parts[0].upper(), parts[1]
        if hint.startswith("/"):
            return None, hint
        return None, hint

    async def _handle_script_build(self, state: PerformanceTestingState) -> dict[str, Any]:
        if not state.plan:
            return self._error("缺少压测计划", state)

        await self._emit_progress(state, PHASE_SCRIPT_BUILT, "正在生成性能测试脚本")

        engine_adapter = self._create_engine_adapter(state.plan.engine)
        if not engine_adapter:
            return self._error("引擎适配器未初始化", state)

        script = engine_adapter.build_script(state.plan)
        state.record_phase_transition(PHASE_SCRIPT_BUILT)

        return await self._handle_guard_check(state)

    async def _handle_guard_check(self, state: PerformanceTestingState) -> dict[str, Any]:
        if not self._guard or not state.plan:
            return self._error("安全护栏未初始化", state)

        await self._emit_progress(state, PHASE_GUARD_PASSED, "正在执行压测安全护栏检查")

        result = self._guard.validate(state.plan)
        if not result.ok:
            state.failure_analysis = self._failure_analyzer.analyze({
                "phase": PHASE_GUARD_PASSED,
                "reason": result.reason,
            }).model_dump()
            state.record_phase_transition(PHASE_FAILED)
            state.errors.append(result.reason)
            return {
                "status": "blocked",
                "ok": False,
                "phase": PHASE_FAILED,
                "summary": f"安全护栏拒绝: {result.reason}",
                "guard_result": {"ok": False, "reason": result.reason},
            }

        state.record_phase_transition(PHASE_GUARD_PASSED)

        if self._settings and self._settings.performance_smoke_required:
            return await self._handle_smoke(state)

        state.record_phase_transition(PHASE_SMOKE_VALIDATED)
        return await self._handle_execution(state)

    async def _handle_smoke(self, state: PerformanceTestingState) -> dict[str, Any]:
        if not self._runner or not state.plan:
            return self._error("执行器未初始化", state)

        await self._emit_progress(state, PHASE_SMOKE_VALIDATED, "正在执行冒烟验证")

        engine_adapter = self._create_engine_adapter(state.plan.engine)
        if not engine_adapter:
            return self._error("引擎适配器未初始化", state)

        script = engine_adapter.build_script(state.plan)
        smoke_result = await self._runner.run_smoke(state.plan, script, engine_adapter)

        if not smoke_result.passed:
            state.failure_analysis = self._failure_analyzer.analyze({
                "phase": PHASE_SMOKE_VALIDATED,
                "detail": smoke_result.detail,
                "smoke_result": smoke_result.model_dump(),
            }).model_dump()
            state.record_phase_transition(PHASE_FAILED)
            state.errors.append(f"冒烟验证失败: {smoke_result.detail}")
            return {
                "status": "blocked",
                "ok": False,
                "phase": PHASE_FAILED,
                "summary": f"冒烟验证未通过: {smoke_result.detail}",
                "smoke_result": smoke_result.model_dump(),
            }

        state.record_phase_transition(PHASE_SMOKE_VALIDATED)
        return await self._handle_execution(state)

    async def _handle_execution(self, state: PerformanceTestingState) -> dict[str, Any]:
        if not self._runner or not state.plan:
            return self._error("执行器未初始化", state)

        await self._emit_progress(state, PHASE_LOAD_RUNNING, "正在执行正式压测")

        engine_adapter = self._create_engine_adapter(state.plan.engine)
        if not engine_adapter:
            return self._error("引擎适配器未初始化", state)

        state.record_phase_transition(PHASE_LOAD_RUNNING)

        script = engine_adapter.build_script(state.plan)
        run_opts = RunOptions(
            timeout_seconds=state.plan.workload.hold_seconds + 120,
        )

        run = await self._runner.execute(state.plan, script, run_opts, engine_adapter)
        state.run = run

        if run.status != "completed" and not run.raw_metrics:
            state.failure_analysis = self._failure_analyzer.analyze({
                "phase": PHASE_LOAD_RUNNING,
                "status": run.status,
                "exit_code": run.exit_code,
                "stdout_tail": run.stdout_tail,
            }).model_dump()
            state.record_phase_transition(PHASE_FAILED)
            state.errors.append(f"压测执行失败: {run.status}")
            return {
                "status": "error",
                "ok": False,
                "phase": PHASE_FAILED,
                "summary": f"压测执行失败: {run.status}",
                "run_id": run.run_id,
                "failure_analysis": state.failure_analysis,
                "errors": state.errors,
            }

        state.record_phase_transition(PHASE_RESULT_COLLECTED)
        return await self._handle_analysis(state)

    async def _handle_result_collection(self, state: PerformanceTestingState) -> dict[str, Any]:
        state.record_phase_transition(PHASE_RESULT_COLLECTED)
        return await self._handle_analysis(state)

    async def _handle_analysis(self, state: PerformanceTestingState) -> dict[str, Any]:
        if not state.run or not state.plan:
            return self._error("缺少运行结果", state)

        await self._emit_progress(state, PHASE_ANALYZED, "正在分析压测结果")

        raw_metrics = RawMetrics(**state.run.raw_metrics) if state.run.raw_metrics else RawMetrics()

        if state.run.engine_thresholds:
            raw_metrics.thresholds = state.run.engine_thresholds

        parsed = self._parser.parse(raw_metrics)

        baseline = None
        target_url = state.plan.targets[0].url if state.plan.targets else ""

        if self._metrics_store and target_url:
            try:
                baseline = await self._metrics_store.get_baseline(target_url)
            except Exception:
                logger.debug("Baseline lookup failed, proceeding without baseline")

        report = self._report_builder.build(parsed, state.plan, state.run, baseline)
        state.report = report

        state.record_phase_transition(PHASE_ANALYZED)
        return await self._handle_report(state)

    async def _handle_report(self, state: PerformanceTestingState) -> dict[str, Any]:
        if not state.report:
            return self._error("缺少分析报告", state)

        await self._emit_progress(state, PHASE_REPORT_READY, "正在生成性能测试报告")

        # Persist run metrics for future baseline lookups
        if self._metrics_store and state.run and state.plan:
            target_url = state.plan.targets[0].url if state.plan.targets else ""
            try:
                await self._metrics_store.save_run(
                    run=state.run,
                    metrics=state.report.metrics,
                    verdict=state.report.verdict,
                    target_url=target_url,
                    run_intent=state.report.run_intent,
                )
            except Exception:
                logger.debug("Failed to persist perf run metrics, non-blocking")

        state.record_phase_transition(PHASE_REPORT_READY)

        report = state.report
        metrics_payload = {
            "run_id": report.run_id,
            "run_intent": report.run_intent,
            "verdict": report.verdict,
            "metrics": report.metrics.model_dump(),
            "sla_result": report.sla_result.model_dump(),
            "engine_threshold_crosscheck": report.engine_threshold_crosscheck.model_dump(),
            "baseline_comparison": (
                report.baseline_comparison.model_dump()
                if report.baseline_comparison
                else None
            ),
        }

        return {
            "status": "completed",
            "ok": True,
            "phase": PHASE_REPORT_READY,
            "summary": f"性能测试完成，verdict={state.report.verdict}",
            "verdict": state.report.verdict,
            "run_id": state.run.run_id if state.run else "",
            "run_intent": state.report.run_intent,
            "metrics": state.report.metrics.model_dump(),
            "sla_result": state.report.sla_result.model_dump(),
            "error_breakdown": state.report.error_breakdown.model_dump(),
            "engine_threshold_crosscheck": state.report.engine_threshold_crosscheck.model_dump(),
            "baseline_comparison": (
                state.report.baseline_comparison.model_dump()
                if state.report.baseline_comparison
                else None
            ),
            "load_side_observations": state.report.load_side_observations,
            "report_markdown": state.report.report_markdown,
            "report_html": state.report.report_html,
            "artifacts": [
                {
                    "type": "performance_report_markdown",
                    "label": "Performance report (Markdown)",
                    "content": state.report.report_markdown,
                },
                {
                    "type": "performance_report_html",
                    "label": "Performance report (HTML)",
                    "content": state.report.report_html,
                },
                {
                    "type": "performance_metrics_json",
                    "label": "Performance metrics and SLA (JSON)",
                    "content": json.dumps(
                        metrics_payload,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                },
            ],
        }

    async def _emit_progress(
        self,
        state: PerformanceTestingState,
        phase: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._coordinator.emit_progress(
            phase=phase,
            message=message,
            session_id=state.session_id,
            data=data,
        )

    def _error(self, message: str, state: PerformanceTestingState) -> dict[str, Any]:
        state.record_phase_transition(PHASE_FAILED)
        state.errors.append(message)
        return {
            "status": "error",
            "ok": False,
            "phase": PHASE_FAILED,
            "summary": message,
            "errors": state.errors,
        }

    def _restore_state(self, context: Any) -> PerformanceTestingState:
        if hasattr(context, "context_bundle") and isinstance(context.context_bundle, dict):
            raw = context.context_bundle.get(STATE_METADATA_KEY)
            if raw and isinstance(raw, dict):
                try:
                    return PerformanceTestingState(**raw)
                except Exception:
                    pass

        state = PerformanceTestingState(session_id=str(uuid.uuid4())[:8])
        return state

    def _persist_state(self, state: PerformanceTestingState, context: Any) -> None:
        if hasattr(context, "context_bundle") and isinstance(context.context_bundle, dict):
            context.context_bundle[STATE_METADATA_KEY] = state.model_dump()
