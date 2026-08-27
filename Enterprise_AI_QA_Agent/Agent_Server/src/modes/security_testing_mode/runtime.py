"""Security Testing Mode runtime.

This module drives the Phase 1 security testing state machine. The runtime is
intentionally conservative: it builds a small, auditable campaign from the
target supplied by the user, executes it through registered runner tools, and
packages the resulting findings into Markdown/JSON/HTML report artifacts.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from src.application.reporting.report_template_service import ReportTemplateService
from src.application.security.execution_monitor import SecurityExecutionMonitor
from src.application.security.finding_normalizer import FindingNormalizer
from src.application.security.risk_policy import SecurityRiskPolicy
from src.application.security.target_guard import SecurityTargetGuard
from src.application.security.tool_catalog import SecurityToolCatalog
from src.modes.security_testing_mode.agent import (
    ATTACK_SURFACE_PLANNER_KEY,
    SECURITY_DOC_ANALYST_KEY,
    SECURITY_EXPLOIT_CODER_KEY,
    SECURITY_FAILURE_ANALYST_KEY,
    resolve_security_worker_agent,
)
from src.modes.security_testing_mode.asset_discovery_service import SecurityAssetDiscoveryService
from src.modes.security_testing_mode.attack_chain import SecurityAttackChainService
from src.modes.security_testing_mode.attack_session import SecurityShellSession
from src.modes.security_testing_mode.callback_broker import SecurityCallbackBroker
from src.modes.security_testing_mode.exploit_workspace import (
    ExploitWorkspace,
    ExploitWorkspaceResult,
)
from src.modes.security_testing_mode.auth_strategy_planner import SecurityAuthStrategyPlanner
from src.modes.security_testing_mode.campaign_state import (
    AgentActivityRecord,
    CampaignSettlement,
    CredentialReference,
    EvidenceArtifact,
    ReportDeliveryRecord,
    SecurityCampaign,
    SecurityReport,
    SecurityShellCommandState,
    SecurityShellSessionState,
    ExploitWorkspaceState,
    SecuritySubtask,
    SecurityTask,
    SecurityTaskEventRecord,
    SecurityTestingRequestState,
    SecurityTestingState,
    TargetCandidate,
    ThreatHypothesis,
    ToolBootstrapState,
    ToolExecutionRecord,
)
from src.modes.security_testing_mode.contracts import (
    PHASE_ASSET_DISCOVERED,
    PHASE_SCENARIO_ANALYZED,
    PHASE_EMAIL_DELIVERED,
    PHASE_ATTACK_PLAN_READY,
    PHASE_FAILED,
    PHASE_INTERRUPTED,
    PHASE_RECON_COMPLETE,
    PHASE_RECON_RUNNING,
    PHASE_HYPOTHESIS_PLANNING,
    PHASE_ATTACK_LOOP,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_BUG_TRACKING,
    PHASE_REPORT_READY,
    PHASE_REQUEST_RESOLVED,
    PHASE_SCOPE_CONFIRMED,
    PHASE_TARGET_DISCOVERED,
    PHASE_TASK_DISPATCHING,
    PHASE_TASK_RUNNING,
    STATE_METADATA_KEY,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_SKIPPED,
    TERMINAL_PHASES,
)
from src.modes.security_testing_mode.evidence_service import SecurityEvidenceService
from src.modes.security_testing_mode.evaluation import SecurityTestingEvaluationPolicy
from src.modes.security_testing_mode.memory_service import SecurityMemoryService
from src.modes.security_testing_mode.output_compaction import (
    DEFAULT_THRESHOLD_BYTES,
    compact_security_output,
)
from src.modes.security_testing_mode.recon_planner import SecurityReconPlanner
from src.modes.security_testing_mode.reflection_service import SecurityReflectionService
from src.modes.security_testing_mode.report_builder import SecurityReportBuilder
from src.modes.security_testing_mode.report_template import SecurityReportTemplate
from src.modes.security_testing_mode.prompt_contract import (
    build_security_exploit_coder_prompt,
    build_security_failure_analysis_prompt,
    build_security_scenario_analysis_prompt,
    build_security_threat_planning_prompt,
)
from src.modes.security_testing_mode.request_interpreter import SecurityRequestInterpreter
from src.modes.security_testing_mode.scenario_analysis_service import (
    SecurityScenarioAnalysisService,
    extract_json_object,
)
from src.modes.security_testing_mode.scenario_incremental_planner import (
    SecurityScenarioIncrementalPlanner,
)
from src.modes.security_testing_mode.security_graph_store import SecurityGraphStore
from src.modes.security_testing_mode.security_bug_service import SecurityBugService
from src.modes.security_testing_mode.security_bug_store import InMemorySecurityBugStore
from src.modes.security_testing_mode.severity_evaluator import SeverityEvaluator
from src.modes.security_testing_mode.subagent_coordinator import SecuritySubagentCoordinator
from src.modes.security_testing_mode.subtask_generator import SecuritySubtaskGenerator
from src.modes.security_testing_mode.subtask_refiner import SecuritySubtaskRefiner
from src.modes.security_testing_mode.task_pool import SecurityTaskPool
from src.modes.security_testing_mode.threat_intelligence_service import SecurityThreatIntelligenceService
from src.modes.security_testing_mode.tools import SECURITY_TESTING_TOOL_KEYS
from src.modes.security_testing_mode.verification import SecurityTestingVerificationPolicy
from src.modes.security_testing_mode.vulnerability_planner import SecurityVulnerabilityPlanner

RunnerExecutor = Callable[[dict[str, Any], Any, str | None], Awaitable[dict[str, Any]]]
ReportDeliveryExecutor = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]

logger = logging.getLogger("uvicorn.error.security_testing_mode.runtime")


class SecurityTestingModeRuntime:
    """Drives the Security Testing Mode phase machine."""

    def __init__(
        self,
        *,
        settings: Any = None,
        coordinator_runtime_service: Any = None,
        session_store: Any = None,
        memory_runtime_service: Any = None,
        report_template_service: ReportTemplateService | None = None,
        runner_executor: RunnerExecutor | None = None,
        report_delivery_executor: ReportDeliveryExecutor | None = None,
        runtime_control: Any = None,
        security_bug_service: SecurityBugService | None = None,
        execution_environment_service: Any = None,
        callback_broker: SecurityCallbackBroker | None = None,
        security_graph_store: Any = None,
    ) -> None:
        self._settings = settings
        self._coordinator_runtime_service = coordinator_runtime_service
        self._session_store = session_store
        self._memory_runtime_service = memory_runtime_service
        self._runner_executor = runner_executor
        self._report_delivery_executor = report_delivery_executor
        self._runtime_control = runtime_control
        self._execution_environment_service = execution_environment_service
        self._tool_catalog = SecurityToolCatalog()
        self._risk_policy = SecurityRiskPolicy()
        self._execution_monitor = SecurityExecutionMonitor()
        self._target_guard = SecurityTargetGuard(settings)
        self._output_summary_threshold_bytes = int(
            getattr(settings, "security_runner_output_summary_threshold_bytes", DEFAULT_THRESHOLD_BYTES)
            or DEFAULT_THRESHOLD_BYTES
        )
        self._finding_normalizer = FindingNormalizer()
        self._severity_evaluator = SeverityEvaluator()
        self._verification_policy = SecurityTestingVerificationPolicy()
        self._evaluation_policy = SecurityTestingEvaluationPolicy()
        self._request_interpreter = SecurityRequestInterpreter()
        self._asset_discovery = SecurityAssetDiscoveryService()
        self._auth_strategy_planner = SecurityAuthStrategyPlanner()
        self._evidence_service = SecurityEvidenceService()
        self._memory_service = SecurityMemoryService()
        self._recon_planner = SecurityReconPlanner(
            tool_catalog=self._tool_catalog,
            risk_policy=self._risk_policy,
        )
        self._vulnerability_planner = SecurityVulnerabilityPlanner(
            risk_policy=self._risk_policy,
        )
        self._reflection_service = SecurityReflectionService()
        self._attack_chain = SecurityAttackChainService(risk_policy=self._risk_policy)
        self._attack_chain_enabled = bool(
            getattr(settings, "security_attack_chain_enabled", True)
        )
        self._campaign_max_loops = max(
            1,
            int(getattr(settings, "security_campaign_max_loops", 5) or 5),
        )
        self._campaign_max_attempts = max(
            1,
            int(getattr(settings, "security_campaign_max_attempts", 30) or 30),
        )
        self._security_bug_registry_enabled = bool(
            getattr(settings, "security_bug_registry_enabled", True)
        )
        self._attack_session_enabled = bool(
            getattr(settings, "security_attack_session_enabled", False)
            if settings is not None
            else False
        )
        self._attack_session_timeout_seconds = float(
            getattr(settings, "security_attack_session_timeout_seconds", 900) or 900
        )
        self._attack_session_command_timeout_seconds = float(
            getattr(settings, "security_attack_session_command_timeout_seconds", 120) or 120
        )
        self._callback_broker_enabled = bool(
            getattr(settings, "security_callback_broker_enabled", False)
            if settings is not None
            else False
        )
        self._callback_broker = callback_broker or SecurityCallbackBroker(
            port_range=str(
                getattr(settings, "security_callback_port_range", "28000-28100")
                if settings is not None
                else "28000-28100"
            ),
            lease_timeout_seconds=float(
                getattr(settings, "security_callback_lease_timeout_seconds", 300)
                if settings is not None
                else 300
            ),
        )
        self._security_graph_memory_enabled = bool(
            getattr(settings, "security_graph_memory_enabled", False)
            if settings is not None
            else False
        )
        self._security_graph_store = security_graph_store or (
            SecurityGraphStore(settings) if self._security_graph_memory_enabled and settings is not None else None
        )
        self._security_bug_service = security_bug_service or SecurityBugService(
            InMemorySecurityBugStore(),
            reproduction_required=bool(
                getattr(settings, "security_bug_reproduction_required", True)
            ),
        )
        self._subtask_generator = SecuritySubtaskGenerator()
        self._subtask_refiner = SecuritySubtaskRefiner()
        self._scenario_analysis = SecurityScenarioAnalysisService()
        self._threat_intelligence = SecurityThreatIntelligenceService()
        self._scenario_incremental_planner = SecurityScenarioIncrementalPlanner(
            scenario_analysis=self._scenario_analysis,
            recon_planner=self._recon_planner,
            tool_catalog=self._tool_catalog,
            risk_policy=self._risk_policy,
            execution_monitor=self._execution_monitor,
        )
        self._report_builder = SecurityReportBuilder()
        self._report_template = SecurityReportTemplate(report_template_service)
        # A client timeout must not cause a retry to start a second campaign
        # for the same session. The lock is process-local, while the cached
        # state makes the completed result idempotent within the runtime.
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._state_cache: dict[str, SecurityTestingState] = {}

    def set_coordinator_runtime_service(self, coordinator_runtime_service: Any) -> None:
        self._coordinator_runtime_service = coordinator_runtime_service

    def set_session_store(self, session_store: Any) -> None:
        self._session_store = session_store

    def set_memory_runtime_service(self, memory_runtime_service: Any) -> None:
        self._memory_runtime_service = memory_runtime_service

    def set_runner_executor(self, runner_executor: RunnerExecutor | None) -> None:
        self._runner_executor = runner_executor

    def set_report_delivery_executor(
        self,
        report_delivery_executor: ReportDeliveryExecutor | None,
    ) -> None:
        self._report_delivery_executor = report_delivery_executor

    async def handle(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        worker_action = str(arguments.get("worker_action") or "").strip().lower()
        if worker_action == "execute_security_task" or isinstance(arguments.get("task"), dict):
            return await self._handle_impl(arguments, context)
        session_id = str(getattr(context, "session_id", "") or "")
        if not session_id:
            return await self._handle_impl(arguments, context)
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            return await self._handle_impl(arguments, context)

    def record_external_tool_bootstrap(
        self,
        *,
        campaign_id: str,
        target_allowlist: list[str],
        manifest: dict[str, Any],
        tool_summary: str,
        tool_status: str,
        context: Any,
    ) -> SecurityTestingState:
        """Project one completed P4 lifecycle into the session's Campaign.

        P4 deliberately bypasses the model graph after approval.  It must not,
        however, bypass the Campaign's audit, report, verification, and
        cleanup settlement contracts.  This method is the only bridge for the
        dedicated P4 path: it records readiness/installation as infrastructure
        evidence, never as a scan Finding or vulnerability verification.
        """
        normalized_campaign_id = str(campaign_id or manifest.get("campaign_id") or "").strip()
        if not normalized_campaign_id:
            raise ValueError("P4 campaign projection requires a campaign_id.")

        bootstrap = ToolBootstrapState.model_validate(dict(manifest or {}))
        bootstrap.campaign_id = normalized_campaign_id
        now = datetime.now(timezone.utc).isoformat()
        if not bootstrap.created_at:
            bootstrap.created_at = now
        if not bootstrap.completed_at and bootstrap.status in {"already_available", "completed", "failed"}:
            bootstrap.completed_at = now

        state = self._restore_state(context)
        existing_campaign = state.campaign
        if existing_campaign is not None and existing_campaign.campaign_id not in {"", normalized_campaign_id}:
            raise ValueError(
                "P4 campaign projection refused because the supplied campaign_id "
                "does not match the Campaign already bound to this session."
            )

        target_values = self._unique_text_values(target_allowlist)
        targets = [
            self._request_interpreter.build_target(value)
            for value in target_values
        ]
        if existing_campaign is None:
            target_fingerprint = targets[0].fingerprint if targets else ""
            state.request = SecurityTestingRequestState(
                objective=str(getattr(context, "user_message", "") or "P4 temporary tool bootstrap"),
                target_url=targets[0].value if targets and targets[0].target_type == "url" else "",
                target_host=targets[0].value if targets and targets[0].target_type != "url" else "",
                target_fingerprint=target_fingerprint,
                risk_tolerance="low",
                raw_message=str(getattr(context, "user_message", "") or ""),
            )
            state.request_fingerprint = self._request_fingerprint(state.request)
            state.targets = targets
            state.campaign = SecurityCampaign(
                campaign_id=normalized_campaign_id,
                objective=state.request.objective,
                target_fingerprint=target_fingerprint,
                targets=targets,
                assets=self._asset_discovery.seed_assets(targets, state.request),
                scope_notes=", ".join(target_values),
                operational_constraints=[
                    "P4 record represents temporary tool readiness/installation only; it does not represent a security scan or a vulnerability finding."
                ],
                risk_tolerance="low",
                created_at=now,
            )
        else:
            state.campaign.campaign_id = normalized_campaign_id
            if not state.campaign.targets and targets:
                state.campaign.targets = targets
            if not state.targets and targets:
                state.targets = targets

        campaign = state.campaign
        assert campaign is not None
        state.session_id = str(getattr(context, "session_id", "") or state.session_id)
        state.trace_id = str(getattr(context, "trace_id", "") or state.trace_id)
        self._attach_runtime_context(state, context)

        bootstrap_id = str(bootstrap.bootstrap_id or f"bootstrap_{hashlib.sha256(normalized_campaign_id.encode('utf-8')).hexdigest()[:16]}")
        bootstrap.bootstrap_id = bootstrap_id
        campaign.tool_bootstraps = [
            item for item in campaign.tool_bootstraps
            if item.bootstrap_id != bootstrap_id
        ]
        campaign.tool_bootstraps.append(bootstrap)

        task_id = f"p4_bootstrap_{hashlib.sha256(bootstrap_id.encode('utf-8')).hexdigest()[:16]}"
        completed = (
            str(tool_status or "").strip().lower() == "completed"
            and bootstrap.status in {"already_available", "completed"}
            and bootstrap.cleanup_complete
        )
        task = SecurityTask(
            task_id=task_id,
            name="P4 temporary security tool readiness",
            description=(
                "Dedicated approval-gated temporary tool readiness/installation lifecycle. "
                "This task is not a scan and produces no vulnerability finding."
            ),
            surface_type="infrastructure",
            tool_family="general_scan",
            command_profile=bootstrap.profile_key or "security_tool_bootstrap",
            target=target_values[0] if target_values else "",
            risk_level="low",
            requires_approval=True,
            timeout_seconds=300,
            max_retries=0,
            status=TASK_COMPLETED if completed else TASK_FAILED,
            attempts=1,
            refine_origin="p4_tool_bootstrap",
            started_at=bootstrap.created_at,
            completed_at=bootstrap.completed_at or now,
            worker_agent_key="security-tool-bootstrap",
            worker_execution_mode="dedicated_p4_runtime",
            result_summary=str(tool_summary or "").strip(),
            raw_output=self._p4_output_summary(bootstrap),
            last_error="" if completed else str(bootstrap.failure_reason or tool_summary or "P4 tool bootstrap did not complete."),
            failure_analysis=(
                {}
                if completed
                else {
                    "failure_category": bootstrap.failure_category or "tool_bootstrap_failed",
                    "root_cause": bootstrap.failure_reason or str(tool_summary or ""),
                }
            ),
            artifacts=[bootstrap.manifest_path] if bootstrap.manifest_path else [],
            planning_rationale=(
                "Dedicated P4 approval scope supplied temporary tool readiness evidence; "
                "the operation is reported separately from security discovery."
            ),
        )
        campaign.tasks = [item for item in campaign.tasks if item.task_id != task_id]
        campaign.tasks.append(task)
        campaign.execution_records = [
            item for item in campaign.execution_records if item.task_id != task_id
        ]
        campaign.execution_records.append(
            ToolExecutionRecord(
                record_id=f"exec_{task_id}_1",
                task_id=task_id,
                tool_name="security-tool-bootstrap",
                command=bootstrap.command_template_id or bootstrap.profile_key or "security_tool_bootstrap",
                started_at=task.started_at,
                completed_at=task.completed_at,
                exit_code=(
                    bootstrap.profile_exit_code
                    if bootstrap.profile_exit_code is not None
                    else bootstrap.install_exit_code
                    if bootstrap.install_exit_code is not None
                    else bootstrap.readiness_exit_code
                ),
                stdout_summary=self._truncate_p4_text(bootstrap.stdout or tool_summary, limit=2000),
                stderr_summary=self._truncate_p4_text(bootstrap.stderr or bootstrap.failure_reason, limit=1200),
                success=completed,
                error=task.last_error,
                artifacts=list(task.artifacts),
            )
        )
        if bootstrap.manifest_path:
            evidence_id = f"ev_{task_id}_manifest"
            campaign.evidence = [
                item for item in campaign.evidence if item.artifact_id != evidence_id
            ]
            campaign.evidence.append(
                EvidenceArtifact(
                    artifact_id=evidence_id,
                    artifact_type="security_tool_bootstrap_manifest",
                    filename=f"{bootstrap_id}.json",
                    content_type="application/json",
                    content=bootstrap.manifest_path,
                    source_task_id=task_id,
                    created_at=task.completed_at,
                )
            )

        campaign.updated_at = now
        report = self._report_builder.build_report(campaign)
        verification_verdict = self._verification_policy.verify(campaign=campaign, report=report)
        evaluation_result = self._evaluation_policy.evaluate(
            campaign=campaign,
            report=report,
            verification_verdict=verification_verdict,
        )
        settlement_status = (
            "failed"
            if not verification_verdict.passed
            else "partial"
            if task.status == TASK_FAILED
            else "success"
        )
        campaign.settlement = CampaignSettlement(
            status=settlement_status,
            reason=verification_verdict.summary,
            all_tasks_settled=True,
            all_chains_settled=True,
            report_ready=True,
            cleanup_complete=all(item.cleanup_complete for item in campaign.tool_bootstraps),
            finalized_at=now,
        )
        state.verification_result = verification_verdict.to_dict()
        state.evaluation_result = evaluation_result.to_dict()
        report.settlement = campaign.settlement.model_dump(mode="json")
        report.verification_result = dict(state.verification_result)
        report.evaluation_result = dict(state.evaluation_result)
        markdown_report = self._report_builder.build_markdown(report)
        html_report = self._report_template.render(
            report=report,
            markdown_content=markdown_report,
            sender=str(getattr(context, "selected_agent_key", "") or "security-testing-agent"),
        )
        artifacts = self._prepare_report_artifacts(
            self._report_builder.build_artifacts(
                report=report,
                markdown_report=markdown_report,
                html_report=html_report,
            ),
            state=state,
            context=context,
        )
        state.report_markdown = markdown_report
        state.report_html = html_report
        state.artifacts = self._artifact_metadata(artifacts)
        state.errors = self._build_error_records(evaluation_result.to_dict(), campaign.tasks)
        report.artifacts = list(state.artifacts)
        state.report = report
        state.execution_strategy = "dedicated_p4_runtime"
        state.notes.append(
            f"P4 bootstrap {bootstrap.bootstrap_id} projected into Campaign {campaign.campaign_id}; "
            f"status={bootstrap.status}, cleanup_complete={bootstrap.cleanup_complete}."
        )
        state.record_phase_transition(
            PHASE_REPORT_READY,
            "P4 temporary-tool lifecycle was projected into Campaign settlement and report.",
        )
        self._persist_state(state, context)
        logger.info(
            "security.tool_bootstrap.campaign_projected %s",
            self._log_payload(
                context=context,
                campaign_id=campaign.campaign_id,
                bootstrap_id=bootstrap.bootstrap_id,
                bootstrap_status=bootstrap.status,
                cleanup_complete=bootstrap.cleanup_complete,
                settlement_status=campaign.settlement.status,
                verification_passed=verification_verdict.passed,
            ),
        )
        return state

    async def _handle_impl(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        """Restore state, advance the phase machine, persist, and return output."""
        worker_action = str(arguments.get("worker_action") or "").strip().lower()
        if worker_action == "execute_security_task" or isinstance(arguments.get("task"), dict):
            logger.info(
                "security_worker_entry %s",
                self._log_payload(
                    context=context,
                    worker_action=worker_action or "execute_security_task",
                    task_id=str((arguments.get("task") or {}).get("task_id") or ""),
                ),
            )
            return await self._execute_dispatched_task(arguments, context)

        state = self._restore_state(context)
        request = self._build_request(arguments, context)
        logger.info(
            "security_campaign_entry %s",
            self._log_payload(
                context=context,
                phase=state.phase,
                objective=request.objective[:200],
                target=request.target_url or request.target_host,
                risk_tolerance=request.risk_tolerance,
            ),
        )

        request_fingerprint = self._request_fingerprint(request)
        if state.phase in TERMINAL_PHASES and request.raw_message.strip():
            force_retest = bool(arguments.get("force_retest"))
            if (
                not force_retest
                and state.request_fingerprint
                and state.request_fingerprint == request_fingerprint
            ):
                logger.info(
                    "security_campaign_idempotent_replay %s",
                    self._log_payload(
                        context=context,
                        request_fingerprint=request_fingerprint,
                        campaign_id=state.campaign.campaign_id if state.campaign else "",
                        phase=state.phase,
                    ),
                )
                return self._build_output(state)
            state = SecurityTestingState()

        state.request = request
        state.request_fingerprint = request_fingerprint
        self._attach_runtime_context(state, context)
        try:
            state = await self._advance(state, context)
        except Exception as exc:
            # Any unexpected exception during phase advancement must still
            # leave the campaign in a reportable terminal state so the
            # workbench/email pipeline does not get stuck mid-execution.
            error_note = f"Security campaign execution raised an unhandled error: {exc}"
            if error_note not in state.notes:
                state.notes.append(error_note)
            state.error = str(exc)
            logger.exception(
                "security_campaign_unhandled_error %s",
                self._log_payload(context=context, phase=state.phase, error=str(exc)),
            )
            state.record_phase_transition(PHASE_FAILED, "Unhandled execution error.")
        if state.phase == PHASE_FAILED:
            state = await self._finalize_failed_state(state, context)
        # Settlement-driven safety net: if execution ended without a report or
        # delivery attempt (e.g. all tasks failed but phase machine never
        # reached PHASE_FAILED, or report was generated but delivery was
        # skipped), reconcile delivery state once here.
        state = await self._ensure_terminal_delivery(state, context)
        self._persist_state(state, context)
        logger.info(
            "security_campaign_exit %s",
            self._log_payload(
                context=context,
                phase=state.phase,
                campaign_id=state.campaign.campaign_id if state.campaign else "",
                execution_strategy=state.execution_strategy,
                task_summary=self._task_status_summary(state.campaign.tasks if state.campaign else []),
                finding_count=len(state.campaign.findings) if state.campaign else 0,
                error=state.error,
            ),
        )
        return self._build_output(state)

    async def _advance(self, state: SecurityTestingState, context: Any) -> SecurityTestingState:
        """Advance until a terminal phase or an external wait point."""
        if state.phase in TERMINAL_PHASES:
            return state

        if state.phase == PHASE_REQUEST_RESOLVED:
            state = self._resolve_targets(state)

        if state.phase == PHASE_TARGET_DISCOVERED:
            state = self._confirm_scope(state)

        if state.phase == PHASE_SCOPE_CONFIRMED:
            state = self._discover_seed_assets(state)

        if state.phase == PHASE_ASSET_DISCOVERED:
            state = await self._analyze_scenario(state, context)

        if state.phase == PHASE_SCENARIO_ANALYZED:
            state = await self._build_campaign(state, context)

        if state.phase == PHASE_ATTACK_PLAN_READY:
            state = await self._execute_campaign(state, context)

        return state

    async def _analyze_scenario(self, state: SecurityTestingState, context: Any) -> SecurityTestingState:
        """Run the mandatory evidence-first scenario stage before task planning."""
        if state.campaign is None:
            state.record_phase_transition(PHASE_FAILED, "No campaign state available for scenario analysis.")
            return state

        await self._run_preplanning_discovery(state, context)
        analyst_payload: dict[str, Any] = {}
        planner_payload: dict[str, Any] = {}
        profile, threats = self._scenario_analysis.analyze(
            request=state.request,
            targets=state.targets,
            assets=state.campaign.assets,
        )
        if self._can_use_subagent_execution(context):
            analyst_prompt = build_security_scenario_analysis_prompt(
                target=profile.target,
                request=state.request.model_dump(mode="json", exclude={"credentials"}),
                assets=[asset.model_dump(mode="json") for asset in state.campaign.assets],
            )
            analyst_text = await self._dispatch_analysis_worker(
                agent_key=SECURITY_DOC_ANALYST_KEY,
                task_id=f"scenario_analysis_{state.campaign.campaign_id}",
                prompt=analyst_prompt,
                context=context,
            )
            analyst_payload = extract_json_object(analyst_text)
            profile, threats = self._scenario_analysis.analyze(
                request=state.request,
                targets=state.targets,
                assets=state.campaign.assets,
                analyst_payload=analyst_payload,
            )
            planner_text = await self._dispatch_analysis_worker(
                agent_key=ATTACK_SURFACE_PLANNER_KEY,
                task_id=f"threat_planning_{state.campaign.campaign_id}",
                prompt=build_security_threat_planning_prompt(
                    scenario=profile.model_dump(mode="json")
                ),
                context=context,
            )
            planner_payload = extract_json_object(planner_text)
            profile, threats = self._scenario_analysis.analyze(
                request=state.request,
                targets=state.targets,
                assets=state.campaign.assets,
                analyst_payload=analyst_payload,
                planner_payload=planner_payload,
            )
        else:
            note = "Scenario specialist sessions unavailable; used deterministic evidence-backed analysis."
            if note not in state.notes:
                state.notes.append(note)

        state.campaign.scenario_profile = profile
        state.campaign.threat_hypotheses = threats
        self._ingest_threat_intelligence(state, context)
        state.campaign.updated_at = datetime.now(timezone.utc).isoformat()
        state.notes.append(
            f"Scenario analyzed as {profile.product_type} with {len(threats)} threat hypothesis(es); "
            f"{len(profile.unknowns)} unknown(s) remain explicit."
        )
        state.record_phase_transition(
            PHASE_SCENARIO_ANALYZED,
            f"Scenario {profile.scenario_id} analyzed before executable planning.",
        )
        return state

    def _ingest_threat_intelligence(self, state: SecurityTestingState, context: Any) -> None:
        if state.campaign is None:
            return
        raw_items = self._threat_intelligence_items(state, context)
        if not raw_items:
            return
        product = (
            state.campaign.scenario_profile.product_type
            if state.campaign.scenario_profile is not None
            else "unknown"
        )
        observed_version = self._observed_version(state)
        existing_by_key = {
            self._threat_intelligence_key(item): item
            for item in state.campaign.threat_intelligence
            if self._threat_intelligence_key(item)
        }
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            record = self._threat_intelligence.normalize(item)
            record_key = self._threat_intelligence_key(record)
            if record_key in existing_by_key:
                continue
            self._threat_intelligence.match(record, product=product, version=observed_version)
            state.campaign.threat_intelligence.append(record)
            existing_by_key[record_key] = record
            logger.info(
                "security.threat_intelligence.ingested %s",
                self._log_payload(
                    context=context,
                    campaign_id=state.campaign.campaign_id,
                    intelligence_id=record.intelligence_id,
                    validation_status=record.validation_status,
                    confidence=record.confidence,
                    executable=self._threat_intelligence.may_generate_executable_attempt(record),
                ),
            )

        self._append_intelligence_hypotheses(state)
        if state.campaign.threat_intelligence:
            note = (
                f"Ingested {len(state.campaign.threat_intelligence)} structured threat intelligence record(s); "
                "records remain non-executable until lab_verified evidence is present."
            )
            if note not in state.notes:
                state.notes.append(note)

    def _threat_intelligence_key(self, record: Any) -> str:
        source_url = str(getattr(record, "source_url", "") or "").strip().lower().rstrip("/")
        if source_url:
            return f"url:{source_url}"
        content_hash = str(getattr(record, "content_hash", "") or "").strip().lower()
        if content_hash:
            return f"hash:{content_hash}"
        title = str(getattr(record, "title", "") or "").strip().lower()
        source_type = str(getattr(record, "source_type", "") or "").strip().lower()
        return f"title:{source_type}:{title}" if title else ""

    def _threat_intelligence_items(self, state: SecurityTestingState, context: Any) -> list[dict[str, Any]]:
        candidates: list[Any] = []
        request_extra = getattr(state.request, "threat_intelligence", None)
        if isinstance(request_extra, list):
            candidates.extend(request_extra)
        bundle = getattr(context, "context_bundle", None) or {}
        if isinstance(bundle, dict):
            direct = bundle.get("threat_intelligence")
            if isinstance(direct, list):
                candidates.extend(direct)
            mode_request = bundle.get("security_testing_request")
            if isinstance(mode_request, dict) and isinstance(mode_request.get("threat_intelligence"), list):
                candidates.extend(mode_request.get("threat_intelligence") or [])
        return [dict(item) for item in candidates if isinstance(item, dict)]

    def _observed_version(self, state: SecurityTestingState) -> str:
        if state.campaign is None:
            return ""
        for asset in state.campaign.assets:
            if asset.service_version:
                return str(asset.service_version).strip()
            for technology in asset.technologies:
                text = str(technology or "").strip()
                match = re.search(r"\b\d+(?:\.\d+){1,3}(?:[-+~][A-Za-z0-9._-]+)?\b", text)
                if match:
                    return match.group(0)
        return ""

    def _append_intelligence_hypotheses(self, state: SecurityTestingState) -> None:
        if state.campaign is None or state.campaign.scenario_profile is None:
            return
        scenario = state.campaign.scenario_profile
        known = {threat.threat_id for threat in state.campaign.threat_hypotheses}
        fact_ids = [fact.fact_id for fact in scenario.facts]
        for record in state.campaign.threat_intelligence:
            if record.validation_status != "matched":
                continue
            threat_id = f"threat_intel_{record.content_hash[:12]}"
            if threat_id in known:
                continue
            state.campaign.threat_hypotheses.append(
                ThreatHypothesis(
                    threat_id=threat_id,
                    scenario_id=scenario.scenario_id,
                    actor="unauthenticated",
                    entry_point=scenario.entry_points[0] if scenario.entry_points else scenario.target,
                    trust_boundary=scenario.trust_boundaries[0] if scenario.trust_boundaries else "client-to-target",
                    technique=f"Intelligence-informed review: {record.title}",
                    cwe_ids=list(record.cwe_ids),
                    owasp_categories=[],
                    attack_references=[record.source_url] if record.source_url else [],
                    expected_impact=scenario.sensitive_data_types,
                    supporting_fact_ids=fact_ids,
                    assumptions=[
                        "Threat-intelligence record is a planning reference only; it cannot create an executable attempt until lab_verified evidence exists."
                    ],
                    priority=65 if record.confidence in {"high", "medium"} else 35,
                    confidence=0.55 if record.confidence in {"high", "medium"} else 0.3,
                )
            )
            known.add(threat_id)

    async def _run_preplanning_discovery(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> None:
        """Collect one low-risk response fingerprint before specialist planning."""
        if state.campaign is None or not state.targets or self._runner_executor is None:
            return
        target = state.targets[0]
        if target.target_type != "url":
            return
        existing = [
            task
            for task in state.campaign.tasks
            if task.refine_origin == "scenario_discovery"
        ]
        if existing:
            return

        profile = self._tool_catalog.get_profile("whatweb_fingerprint")
        if profile is None:
            state.notes.append("Pre-planning discovery skipped because whatweb_fingerprint is unavailable.")
            return
        task = SecurityTask(
            task_id="scenario_discovery_whatweb",
            name="Pre-planning web fingerprint discovery",
            description="Collect a low-risk HTTP response and technology fingerprint before scenario planning.",
            surface_type="web",
            tool_family=profile.tool_family,
            command_profile=profile.profile_key,
            target=target.value,
            target_port=target.port,
            risk_level=profile.risk_level,
            timeout_seconds=profile.timeout_seconds,
            max_retries=0,
            refine_origin="scenario_discovery",
            worker_agent_key=resolve_security_worker_agent(
                surface_type="web",
                tool_family=profile.tool_family,
                command_profile=profile.profile_key,
            ),
            planning_rationale=(
                "Mandatory low-risk discovery supplies real response evidence before the scenario "
                "and threat plan are frozen."
            ),
        )
        logger.info(
            "security.scenario.discovery.started %s",
            self._log_payload(
                context=context,
                campaign_id=state.campaign.campaign_id,
                task_id=task.task_id,
                command_profile=task.command_profile,
                target=task.target,
            ),
        )
        state.campaign.tasks = [task]
        completed = await self._run_tasks_locally(
            SecurityTaskPool(tasks=[task]),
            context,
            state.campaign,
            checkpoint_callback=self._build_checkpoint_callback(state, context),
            execution_mode="preplanning_discovery",
        )
        state.campaign.tasks = completed
        self._hydrate_campaign_from_task_results(state.campaign)
        result = completed[0]
        if result.status == TASK_FAILED:
            result.failure_analysis = self._local_failure_analysis(result)
            category = str(result.failure_analysis.get("failure_category") or "execution")
            state.notes.append(
                f"Pre-planning discovery failed as {category}; dependent planning must preserve this limitation."
            )
        else:
            state.notes.append(
                "Pre-planning discovery completed and its observed response evidence was added to the scenario."
            )
        logger.info(
            "security.scenario.discovery.completed %s",
            self._log_payload(
                context=context,
                campaign_id=state.campaign.campaign_id,
                task_id=result.task_id,
                command_profile=result.command_profile,
                status=result.status,
                failure_category=str(result.failure_analysis.get("failure_category") or ""),
                asset_count=len(state.campaign.assets),
                evidence_count=len(state.campaign.evidence),
            ),
        )

    async def _dispatch_analysis_worker(
        self,
        *,
        agent_key: str,
        task_id: str,
        prompt: str,
        context: Any,
    ) -> str:
        """Dispatch one read-only specialist and return its final assistant JSON."""
        if self._coordinator_runtime_service is None or self._session_store is None:
            return ""
        logger.info(
            "security_scenario_worker_requested %s",
            self._log_payload(context=context, task_id=task_id, agent_key=agent_key),
        )
        result = await self._coordinator_runtime_service.dispatch(
            payload={
                "workers": [
                    {
                        "task_id": task_id,
                        "description": f"Pre-planning security analysis by {agent_key}",
                        "prompt": prompt,
                        "agent_key": agent_key,
                        "model_key": str(getattr(context, "selected_model_key", "") or "") or None,
                        "context": {
                            "dispatch_role": "security_scenario_analysis",
                            "mode_key": "security_testing",
                            "security_memory_scope": "session_only",
                        },
                    }
                ]
            },
            context=self._build_analysis_dispatch_context(context),
        )
        workers = result.get("workers") if isinstance(result.get("workers"), list) else []
        record = next((item for item in workers if isinstance(item, dict)), None)
        child_session_id = str((record or {}).get("child_session_id") or "")
        if not child_session_id:
            logger.warning(
                "security_scenario_worker_unavailable %s",
                self._log_payload(context=context, task_id=task_id, agent_key=agent_key),
            )
            return ""
        sessions = await self._wait_for_worker_sessions([child_session_id], overall_timeout_seconds=180.0)
        session = sessions[0] if sessions else None
        session_status = self._session_status_value(session)
        timed_out = bool(session is not None and session_status not in {"completed", "failed", "interrupted"})
        if timed_out:
            await self._coordinator_runtime_service.cancel_workers(
                task_ids=[task_id],
                child_session_ids=[child_session_id],
                reason="Pre-planning security specialist exceeded the 180s deadline.",
            )
            session = await self._session_store.get_session(child_session_id)
            session_status = self._session_status_value(session)
            logger.warning(
                "security_scenario_worker_timed_out %s",
                self._log_payload(
                    context=context,
                    task_id=task_id,
                    agent_key=agent_key,
                    child_session_id=child_session_id,
                    status=session_status,
                ),
            )
        summary = self._extract_assistant_summary_from_messages(getattr(session, "messages", [])) if session else ""
        logger.info(
            "security_scenario_worker_completed %s",
            self._log_payload(
                context=context,
                task_id=task_id,
                agent_key=agent_key,
                child_session_id=child_session_id,
                status=session_status,
                timed_out=timed_out,
                structured=bool(extract_json_object(summary)),
            ),
        )
        return summary

    def _resolve_targets(self, state: SecurityTestingState) -> SecurityTestingState:
        target = self._request_interpreter.resolve_primary_target(state.request)
        if target is None:
            state.notes.append("Security testing requires a target URL, host, IP, domain, or CIDR range.")
            state.record_phase_transition(PHASE_FAILED, "No target was provided.")
            return state

        state.targets = [target]
        state.context_refs = self._build_context_refs(state)
        state.record_phase_transition(PHASE_TARGET_DISCOVERED, f"Resolved target: {target.value}")
        return state

    def _confirm_scope(self, state: SecurityTestingState) -> SecurityTestingState:
        if not state.targets:
            state.record_phase_transition(PHASE_FAILED, "No target to scope.")
            return state

        target_values = ", ".join(target.value for target in state.targets if target.value)
        state.notes.append(
            f"Scope auto-confirmed for Phase 1 safe baseline testing: {target_values}."
        )
        if state.request.platform_label:
            state.notes.append(f"Platform context detected: {state.request.platform_label}.")
        for constraint in state.request.access_constraints:
            if constraint not in state.notes:
                state.notes.append(constraint)
        state.record_phase_transition(PHASE_SCOPE_CONFIRMED, "Scope auto-confirmed.")
        return state

    def _discover_seed_assets(self, state: SecurityTestingState) -> SecurityTestingState:
        assets = self._asset_discovery.seed_assets(state.targets, state.request)
        credential_session = self._auth_strategy_planner.prepare_credential_session(state.request)
        state.record_phase_transition(PHASE_ASSET_DISCOVERED, f"Seeded {len(assets)} asset(s).")
        if state.campaign is None:
            scope_notes = ", ".join(target.value for target in state.targets)
            if state.request.platform_label:
                scope_notes = f"{scope_notes} | Platform: {state.request.platform_label}"
            state.campaign = SecurityCampaign(
                campaign_id=str(uuid4()),
                objective=state.request.objective,
                target_fingerprint=state.request.target_fingerprint,
                targets=list(state.targets),
                assets=assets,
                scope_notes=scope_notes,
                operational_constraints=list(state.request.access_constraints),
                risk_tolerance=state.request.risk_tolerance or "medium",
                credential_session=credential_session,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            state.campaign.assets = assets
            state.campaign.credential_session = credential_session
            if state.request.target_fingerprint:
                state.campaign.target_fingerprint = state.request.target_fingerprint
            if state.request.access_constraints:
                state.campaign.operational_constraints = list(state.request.access_constraints)
        state.context_refs = self._build_context_refs(state)
        return state

    async def _build_campaign(self, state: SecurityTestingState, context: Any) -> SecurityTestingState:
        if state.campaign is None:
            state.record_phase_transition(PHASE_FAILED, "No campaign state available.")
            return state

        state.recalled_patterns = await self._memory_service.recall_successful_patterns(
            target_fingerprint=state.campaign.target_fingerprint or state.request.target_fingerprint,
            surface_types=[self._recon_planner.surface_for_target(target) for target in state.targets],
            context=context,
            memory_runtime_service=self._memory_runtime_service,
        )
        recalled_profile_keys = [
            str(item.get("profile_key") or "")
            for item in state.recalled_patterns
            if str(item.get("profile_key") or "")
        ]
        if recalled_profile_keys:
            state.notes.append(
                "Memory-first planning recalled successful profile(s): "
                + ", ".join(recalled_profile_keys)
                + ". Historical success is advisory; runtime failures still trigger route changes."
            )
            bundle = getattr(context, "context_bundle", None)
            if isinstance(bundle, dict):
                bundle["security_recalled_patterns"] = list(state.recalled_patterns)
        discovery_tasks = [
            task
            for task in state.campaign.tasks
            if task.refine_origin == "scenario_discovery"
        ]
        planned_tasks = self._recon_planner.build_campaign_tasks(
            state.targets,
            state.request,
            preferred_profile_keys=recalled_profile_keys,
            scenario_profile=state.campaign.scenario_profile,
            threat_hypotheses=state.campaign.threat_hypotheses,
        )
        planned_tasks = self._vulnerability_planner.refine_tasks(planned_tasks, state.request)
        planned_tasks, monitor_notes = self._execution_monitor.filter_planned_tasks(
            planned_tasks,
            state.request.risk_tolerance,
        )
        for note in monitor_notes:
            if note not in state.notes:
                state.notes.append(note)

        discovery_keys = {(task.target, task.command_profile) for task in discovery_tasks}
        planned_tasks = [
            task
            for task in planned_tasks
            if (task.target, task.command_profile) not in discovery_keys
        ]
        environment_limited = any(
            task.status == TASK_FAILED
            and str(task.failure_analysis.get("failure_category") or "") == "environment_limited"
            for task in discovery_tasks
        )
        if environment_limited:
            tasks = discovery_tasks
            state.notes.append(
                "Pre-planning discovery proved the target unreachable from the isolated runner; "
                "dependent HTTP profiles were deferred instead of blindly retried."
            )
        else:
            tasks = [*discovery_tasks, *planned_tasks]

        scenario = state.campaign.scenario_profile
        threats = state.campaign.threat_hypotheses
        for task in discovery_tasks:
            task.scenario_fact_refs = [fact.fact_id for fact in (scenario.facts if scenario else [])]
            task.threat_hypothesis_ids = [threat.threat_id for threat in threats]
            if scenario is not None:
                task.planning_rationale = (
                    f"Mandatory low-risk discovery for the {scenario.product_type} scenario supplied "
                    "the response evidence used by specialist planning."
                )

        if not tasks:
            state.notes.append("No executable security tasks could be planned for the supplied target.")
            state.record_phase_transition(PHASE_FAILED, "No planned tasks.")
            return state

        state.campaign.tasks = tasks
        state.campaign.subtasks = self._subtask_generator.generate(state.campaign, state.request)
        state.campaign.updated_at = datetime.now(timezone.utc).isoformat()
        state.notes.append(
            f"Generated {len(state.campaign.subtasks)} PentAGI-style security subtask(s)."
        )
        state.record_phase_transition(PHASE_ATTACK_PLAN_READY, f"Built campaign with {len(tasks)} task(s).")
        return state

    async def _execute_campaign(self, state: SecurityTestingState, context: Any) -> SecurityTestingState:
        if state.campaign is None:
            state.record_phase_transition(PHASE_FAILED, "No campaign to execute.")
            return state

        # Re-attach runtime context now that the campaign has been created so
        # subagent dispatches see campaign_id in their context bundle.
        self._attach_runtime_context(state, context)

        state.record_phase_transition(PHASE_TASK_DISPATCHING, "Dispatching security tasks.")
        self._checkpoint_execution_state(
            state=state,
            context=context,
            event_type="campaign_dispatching",
            tasks=state.campaign.tasks,
            summary="Dispatching security tasks.",
        )
        pool = SecurityTaskPool(tasks=state.campaign.tasks)
        state.record_phase_transition(PHASE_TASK_RUNNING, "Executing security tasks.")
        state.record_phase_transition(PHASE_RECON_RUNNING, "Reconnaissance tasks are running.")
        self._checkpoint_execution_state(
            state=state,
            context=context,
            event_type="campaign_running",
            tasks=state.campaign.tasks,
            summary="Reconnaissance tasks are running.",
        )

        if self._can_use_subagent_execution(context):
            state.execution_strategy = "subagent_session"
            logger.info(
                "security_execution_strategy %s",
                self._log_payload(
                    context=context,
                    campaign_id=state.campaign.campaign_id,
                    strategy=state.execution_strategy,
                    task_count=len(state.campaign.tasks),
                    max_workers=state.campaign.max_workers,
                ),
            )
            coordinator = SecuritySubagentCoordinator(
                pool=pool,
                coordinator_runtime_service=self._coordinator_runtime_service,
                session_store=self._session_store,
                parent_context=self._build_dispatch_context(context),
                max_workers=state.campaign.max_workers,
                worker_model_key=str(getattr(context, "selected_model_key", "") or "") or None,
                checkpoint_callback=self._build_checkpoint_callback(state, context),
                target_guard=self._target_guard,
                execution_monitor=self._execution_monitor,
                runner_lookup=self._tool_catalog.resolve_runner_for_family,
                output_summary_threshold_bytes=self._output_summary_threshold_bytes,
                task_refiner=self._subtask_refiner,
                scenario_incremental_planner=self._scenario_incremental_planner,
                campaign=state.campaign,
                request=state.request,
                batch_hydrator=lambda _settled: self._hydrate_campaign_from_task_results(
                    state.campaign
                ),
                interrupt_check=lambda: self._is_interrupt_requested(context),
            )
            completed_tasks = await coordinator.run_all()
            state.campaign.activities.extend(coordinator.activities)
        else:
            state.execution_strategy = "local_worker_fallback"
            logger.info(
                "security_execution_strategy %s",
                self._log_payload(
                    context=context,
                    campaign_id=state.campaign.campaign_id,
                    strategy=state.execution_strategy,
                    task_count=len(state.campaign.tasks),
                ),
            )
            note = (
                "Subagent execution unavailable; using local worker fallback while preserving "
                "specialist worker routing."
            )
            if note not in state.notes:
                state.notes.append(note)
            completed_tasks = await self._run_tasks_locally(
                pool,
                context,
                state.campaign,
                checkpoint_callback=self._build_checkpoint_callback(state, context),
                request=state.request,
                enable_scenario_replan=True,
            )

        state.campaign.tasks = completed_tasks
        if self._is_interrupt_requested(context):
            state.error = "Security campaign interrupted by user request."
            if state.error not in state.notes:
                state.notes.append(state.error)
            state.record_phase_transition(
                PHASE_INTERRUPTED,
                "Parent session interrupt propagated to security workers.",
            )
            return state
        state.campaign.subtasks, refinement_notes = self._subtask_refiner.refine_after_execution(state.campaign)
        monitor_notes = self._execution_monitor.analyze_settled_tasks(
            completed_tasks,
            self._tool_catalog.resolve_runner_for_family,
        )
        for note in [*refinement_notes, *monitor_notes]:
            if note not in state.notes:
                state.notes.append(note)
        self._checkpoint_execution_state(
            state=state,
            context=context,
            event_type="campaign_tasks_settled",
            tasks=completed_tasks,
            summary="All security tasks have settled.",
        )
        self._evidence_service.hydrate_missing_records(state.campaign)
        self._hydrate_campaign_from_task_results(state.campaign)
        failure_analysis_notes = await self._analyze_failed_tasks(state, context)
        for note in failure_analysis_notes:
            if note not in state.notes:
                state.notes.append(note)
        reflection = self._reflection_service.analyze_campaign(state.campaign)
        for note in reflection.get("notes", []):
            if isinstance(note, str) and note not in state.notes:
                state.notes.append(note)
        state.campaign.updated_at = datetime.now(timezone.utc).isoformat()
        state.record_phase_transition(PHASE_RECON_COMPLETE, "Task execution complete.")

        state = await self._run_persistent_attack_session(state, context)
        if state.phase == PHASE_INTERRUPTED:
            return state

        state = await self._run_attack_chain(state, context)
        if state.phase == PHASE_INTERRUPTED:
            return state

        state = await self._run_exploit_coder(state, context)
        if state.phase == PHASE_INTERRUPTED:
            return state

        state = await self._run_callback_broker(state, context)
        await self._cleanup_callback_leases(state)

        if self._security_bug_registry_enabled:
            state.record_phase_transition(
                PHASE_BUG_TRACKING,
                "Generating and deduplicating evidence-backed Security Bugs.",
            )
            await self._security_bug_service.sync_campaign(
                state.campaign,
                session_id=state.session_id,
            )
        else:
            limitation = (
                "Security Bug registry is disabled; verified Findings were not promoted "
                "to persistent reproducible Bug records."
            )
            if limitation not in state.campaign.operational_constraints:
                state.campaign.operational_constraints.append(limitation)

        await self._persist_security_graph(state)

        report = self._report_builder.build_report(state.campaign)
        markdown_report = self._report_builder.build_markdown(report)
        html_report = self._report_template.render(
            report=report,
            markdown_content=markdown_report,
            sender=str(getattr(context, "selected_agent_key", "") or "security-testing-agent"),
        )
        artifacts = self._prepare_report_artifacts(
            self._report_builder.build_artifacts(
                report=report,
                markdown_report=markdown_report,
                html_report=html_report,
            ),
            state=state,
            context=context,
        )
        state.report_markdown = markdown_report
        state.report_html = html_report
        state.artifacts = self._artifact_metadata(artifacts)
        verification_verdict = self._verification_policy.verify(campaign=state.campaign, report=report)
        evaluation_result = self._evaluation_policy.evaluate(
            campaign=state.campaign,
            report=report,
            verification_verdict=verification_verdict,
        )
        state.verification_result = verification_verdict.to_dict()
        state.evaluation_result = evaluation_result.to_dict()
        all_campaign_tasks = list(state.campaign.tasks)
        has_failed_task = any(task.status == TASK_FAILED for task in all_campaign_tasks)
        has_blocked_attempt = any(
            attempt.status in {"blocked", "failed"}
            for attempt in state.campaign.verification_attempts
        )
        has_shell_failure = bool(
            state.campaign.shell_session
            and state.campaign.shell_session.status not in {"completed", "closed"}
        )
        has_exploit_workspace_failure = any(
            workspace.status != "completed"
            for workspace in state.campaign.exploit_workspaces
        )
        settlement_status = (
            "failed"
            if not verification_verdict.passed
            else "partial"
            if has_failed_task or has_blocked_attempt or has_shell_failure or has_exploit_workspace_failure
            else "success"
        )
        state.campaign.settlement = CampaignSettlement(
            status=settlement_status,
            reason=verification_verdict.summary,
            all_tasks_settled=all(
                task.status in {TASK_COMPLETED, TASK_FAILED, TASK_SKIPPED}
                for task in all_campaign_tasks
            ),
            all_chains_settled=self._attack_chain.all_chains_settled(state.campaign),
            report_ready=True,
            cleanup_complete=(
                (
                    state.campaign.shell_session.cleanup_complete
                    if state.campaign.shell_session is not None
                    else True
                )
                and all(
                    workspace.cleanup_complete
                    for workspace in state.campaign.exploit_workspaces
                )
                and all(
                    lease.cleanup_complete
                    for lease in state.campaign.callback_leases
                )
            ),
            finalized_at=datetime.now(timezone.utc).isoformat(),
        )
        state.errors = self._build_error_records(evaluation_result.to_dict(), completed_tasks)
        report.artifacts = list(state.artifacts)
        report.verification_result = dict(state.verification_result)
        report.evaluation_result = dict(state.evaluation_result)
        report.settlement = state.campaign.settlement.model_dump(mode="json")
        state.report = report
        state.notes.append(f"Verification verdict: {verification_verdict.summary}")
        state.notes.append(f"Security evaluation: {evaluation_result.summary}")
        for recommendation in evaluation_result.recommendations[:5]:
            if recommendation not in state.notes:
                state.notes.append(recommendation)
        memory_ids = await self._memory_service.persist_campaign_observations(
            campaign=state.campaign,
            context=context,
            memory_runtime_service=self._memory_runtime_service,
        )
        if memory_ids:
            state.notes.append(f"Persisted {len(memory_ids)} security observation(s) to memory.")
        state.record_phase_transition(PHASE_REPORT_READY, "Security report generated.")
        state = await self._deliver_report_if_requested(
            state=state,
            context=context,
            markdown_report=markdown_report,
            html_report=html_report,
        )
        return state

    async def _run_exploit_coder(self, state: SecurityTestingState, context: Any) -> SecurityTestingState:
        """Run one isolated workspace for every eligible verified hypothesis."""
        if state.campaign is None or not self._exploit_coder_requested(state.request):
            return state
        eligible = {
            hypothesis.hypothesis_id
            for hypothesis in state.campaign.vulnerability_hypotheses
            if hypothesis.status == "verified"
            and hypothesis.result_class in {"confirmed", "verified_exploitable"}
        }
        while True:
            before = len(state.campaign.exploit_workspaces)
            state = await self._run_exploit_coder_once(state, context)
            after = len(state.campaign.exploit_workspaces)
            if after == before or after >= len(eligible):
                break
        return state

    async def _run_callback_broker(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> SecurityTestingState:
        """Lease an opt-in loopback callback endpoint without dispatching a payload."""
        campaign = state.campaign
        if campaign is None or not self._callback_broker_requested(state.request):
            return state
        if not self._callback_broker_enabled:
            note = "P3 callback broker was requested but is disabled by configuration."
            if note not in campaign.operational_constraints:
                campaign.operational_constraints.append(note)
            return state
        target = str((campaign.targets[0].value if campaign.targets else "") or "").strip()
        approval_scope_hash = self._approval_scope_hash(context)
        try:
            lease = await self._callback_broker.lease(
                campaign_id=campaign.campaign_id,
                target=target,
                approval_scope_hash=approval_scope_hash,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            note = f"P3 callback broker lease failed: {exc}"
            campaign.operational_constraints.append(note)
            logger.warning(
                "security.callback.lease_failed %s",
                self._log_payload(
                    context=context,
                    campaign_id=campaign.campaign_id,
                    target=target,
                    error=str(exc),
                ),
            )
            return state
        campaign.callback_leases.append(lease)
        campaign.activities.append(
            AgentActivityRecord(
                activity_id=f"act_{lease.lease_id}",
                agent_key="security-callback-broker",
                agent_name="security-callback-broker",
                action="leased",
                summary=(
                    "P3 loopback callback lease created for an authorized campaign; "
                    "no payload was dispatched by the broker."
                ),
                started_at=lease.created_at,
                execution_mode="callback_broker",
            )
        )
        logger.info(
            "security.callback.leased %s",
            self._log_payload(
                context=context,
                campaign_id=campaign.campaign_id,
                lease_id=lease.lease_id,
                protocol=lease.protocol,
                port=lease.port,
            ),
        )
        return state

    async def _cleanup_callback_leases(self, state: SecurityTestingState) -> None:
        campaign = state.campaign
        if campaign is None:
            return
        for index, lease in enumerate(list(campaign.callback_leases)):
            if lease.status != "active":
                continue
            try:
                released = await self._callback_broker.release(
                    lease.lease_id,
                    reason="campaign_settlement",
                )
                campaign.callback_leases[index] = released
                logger.info(
                    "security.callback.released campaign_id=%s lease_id=%s callback_count=%s",
                    campaign.campaign_id,
                    released.lease_id,
                    released.callback_count,
                )
            except (KeyError, OSError, RuntimeError) as exc:
                campaign.callback_leases[index].cleanup_complete = False
                campaign.callback_leases[index].release_reason = str(exc)[:160]
                campaign.operational_constraints.append(
                    f"P3 callback cleanup failed for {lease.lease_id}: {exc}"
                )

    def _callback_broker_requested(self, request: SecurityTestingRequestState) -> bool:
        normalized = " ".join(
            f"{request.objective} {request.raw_message}".lower()
            .replace("-", " ")
            .replace("_", " ")
            .split()
        )
        if any(
            phrase in normalized
            for phrase in (
                "no callback",
                "without callback",
                "不要回连",
                "不创建回连",
                "零回连",
            )
        ):
            return False
        return "p3" in normalized and any(
            token in normalized
            for token in ("callback", "broker", "回连", "端口租约")
        )

    async def _persist_security_graph(self, state: SecurityTestingState) -> None:
        campaign = state.campaign
        if campaign is None:
            return
        if not self._security_graph_memory_enabled or self._security_graph_store is None:
            return
        credential = campaign.credential_session
        if credential and not campaign.credential_references:
            has_secret = bool(credential.token or credential.cookie_jar or credential.headers)
            campaign.credential_references.append(
                CredentialReference(
                    credential_ref_id=(
                        f"credref_{hashlib.sha256(campaign.campaign_id.encode('utf-8')).hexdigest()[:20]}"
                    ),
                    auth_type=credential.auth_type,
                    principal_hint=credential.username[:160],
                    source=credential.source,
                    expires_at=credential.expires_at,
                    secret_present=has_secret,
                )
            )
        outcome = await self._security_graph_store.persist_campaign(campaign)
        campaign.graph_persistence = outcome
        if outcome.status != "completed":
            note = f"P3 security graph unavailable: {outcome.detail or outcome.status}"
            if note not in campaign.operational_constraints:
                campaign.operational_constraints.append(note)
        logger.info(
            "security.graph.persistence campaign_id=%s status=%s nodes=%s relations=%s",
            campaign.campaign_id,
            outcome.status,
            outcome.node_count,
            outcome.relation_count,
        )

    async def _run_exploit_coder_once(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> SecurityTestingState:
        """Run one bounded P2 verifier only when explicitly requested."""
        campaign = state.campaign
        if campaign is None or not self._exploit_coder_requested(state.request):
            return state
        candidates = [
            hypothesis
            for hypothesis in campaign.vulnerability_hypotheses
            if hypothesis.status == "verified"
            and hypothesis.result_class in {"confirmed", "verified_exploitable"}
            and hypothesis.hypothesis_id not in {
                workspace.hypothesis_id for workspace in campaign.exploit_workspaces
            }
        ]
        if not candidates:
            note = "P2 Exploit Coder was requested, but no evidence-backed verified hypothesis was available."
            if note not in campaign.operational_constraints:
                campaign.operational_constraints.append(note)
            return state
        if self._execution_environment_service is None:
            campaign.operational_constraints.append("P2 Exploit Coder execution environment is unavailable.")
            return state

        hypothesis = candidates[0]
        target = str(hypothesis.target or "").strip()
        task_id = f"exploit_coder_{hypothesis.hypothesis_id}"
        requires_approval = str(hypothesis.risk_level or "").lower() in {"high", "critical"}
        approved = str(hypothesis.approval_status or "").lower() == "approved"
        if requires_approval and not approved:
            now = datetime.now(timezone.utc).isoformat()
            result = ExploitWorkspaceResult(
                workspace_id=f"exploit_workspace_{uuid4().hex[:20]}",
                hypothesis_id=hypothesis.hypothesis_id,
                target=target,
                status="failed",
                failure_category="approval_required",
                failure_reason="High-risk P2 verifier execution requires explicit hypothesis approval.",
                next_route="await_approval",
                created_at=now,
                completed_at=now,
                cleanup_complete=True,
            )
            source = ""
        else:
            logger.info(
            "security.exploit_coder.requested %s",
            self._log_payload(
                context=context,
                campaign_id=campaign.campaign_id,
                hypothesis_id=hypothesis.hypothesis_id,
                target=target,
            ),
            )
            summary = await self._dispatch_analysis_worker(
                agent_key=SECURITY_EXPLOIT_CODER_KEY,
                task_id=task_id,
                prompt=build_security_exploit_coder_prompt(
                    hypothesis=hypothesis.model_dump(mode="json"),
                    target=target,
                ),
                context=context,
            )
            payload = extract_json_object(summary) or {}
            source = str(payload.get("source") or "")
            filename = str(payload.get("filename") or "verifier.py")
            if source:
                workspace = ExploitWorkspace(
                    environment=self._execution_environment_service,
                    campaign_id=campaign.campaign_id,
                    hypothesis_id=hypothesis.hypothesis_id,
                    target=target,
                    artifact_dir=self._exploit_workspace_artifact_dir(state, context),
                    context=context,
                )
                result = await workspace.run(source=source, filename=filename)
            else:
                now = datetime.now(timezone.utc).isoformat()
                result = ExploitWorkspaceResult(
                    workspace_id=f"exploit_workspace_{uuid4().hex[:20]}",
                    hypothesis_id=hypothesis.hypothesis_id,
                    target=target,
                    status="failed",
                    failure_category="coder_output_invalid",
                    failure_reason="security-exploit-coder returned no verifier source.",
                    next_route="return_to_exploit_coder",
                    created_at=now,
                    completed_at=now,
                    cleanup_complete=True,
                )
        workspace_state = self._exploit_workspace_state(result, source)
        campaign.exploit_workspaces.append(workspace_state)
        evidence_payload = workspace_state.model_dump(mode="json")
        evidence_text = json.dumps(evidence_payload, ensure_ascii=False)
        campaign.evidence.append(
            EvidenceArtifact(
                artifact_id=f"ev_{result.workspace_id}_summary",
                artifact_type="exploit_workspace_result",
                filename=f"{result.workspace_id}.json",
                content_type="application/json",
                content=evidence_text,
                size_bytes=len(evidence_text.encode("utf-8")),
                source_task_id=task_id,
                finding_id=hypothesis.finding_id,
                created_at=result.completed_at or datetime.now(timezone.utc).isoformat(),
            )
        )
        campaign.activities.append(
            AgentActivityRecord(
                activity_id=f"act_{result.workspace_id}",
                agent_key=SECURITY_EXPLOIT_CODER_KEY,
                agent_name=SECURITY_EXPLOIT_CODER_KEY,
                task_id=task_id,
                action="completed" if result.status == "completed" else "failed",
                summary=(
                    f"P2 verifier workspace {result.status}; "
                    f"failure_category={result.failure_category or 'none'}; "
                    f"cleanup_complete={result.cleanup_complete}."
                ),
                started_at=result.created_at,
                completed_at=result.completed_at,
                execution_mode="exploit_workspace",
                tool_calls=["coder_dispatch", "static_check", "compile", "execute", "cleanup"],
                notes=result.failure_reason,
            )
        )
        if result.status != "completed":
            refiner_note = (
                f"P2 Refiner classified {result.workspace_id} as {result.failure_category}; "
                f"next_route={result.next_route or 'manual_review'}."
            )
            if refiner_note not in campaign.operational_constraints:
                campaign.operational_constraints.append(refiner_note)
            logger.info(
                "security.exploit_refiner.classified %s",
                self._log_payload(
                    context=context,
                    campaign_id=campaign.campaign_id,
                    hypothesis_id=hypothesis.hypothesis_id,
                    workspace_id=result.workspace_id,
                    failure_category=result.failure_category,
                    next_route=result.next_route,
                ),
            )
        logger.info(
            "security.exploit_coder.completed %s",
            self._log_payload(
                context=context,
                campaign_id=campaign.campaign_id,
                hypothesis_id=hypothesis.hypothesis_id,
                workspace_id=result.workspace_id,
                status=result.status,
                failure_category=result.failure_category,
                source_hash=result.source_hash,
                artifact_hash=result.artifact_hash,
                cleanup_complete=result.cleanup_complete,
            ),
        )
        return state

    def _exploit_coder_requested(self, request: SecurityTestingRequestState) -> bool:
        text = " ".join(
            f"{request.objective} {request.raw_message}".lower()
            .replace("-", " ")
            .replace("_", " ")
            .split()
        )
        denied_phrases = (
            "no exploit coder",
            "without exploit coder",
            "do not use exploit coder",
            "do not run exploit coder",
            "不要使用 exploit coder",
            "不要运行 exploit coder",
            "不使用 exploit coder",
            "不运行 exploit coder",
            "不要使用利用代码",
            "不执行利用代码",
            "不生成验证器",
            "不使用验证器",
        )
        if any(phrase in text for phrase in denied_phrases):
            return False
        return any(token in text for token in ("p2", "exploit coder", "exploit workbench", "影响验证", "验证器"))

    def _exploit_workspace_artifact_dir(self, state: SecurityTestingState, context: Any) -> Path:
        """Return the short, campaign-scoped host root mounted by the P2 runner.

        P2 workspaces are transient and their auditable result is persisted in
        campaign evidence.  They must therefore not inherit the ordinary
        session/turn artifact hierarchy: nested UUIDs combined with Docker's
        ``_security_attack_session_work`` directory exceeded Windows' normal
        path limit during the first real acceptance run.
        """
        root = Path(__file__).resolve().parents[2] / "data" / "artifacts"
        campaign_id = state.campaign.campaign_id if state.campaign else "campaign"
        campaign_key = "".join(
            character
            for character in str(campaign_id or "campaign").lower()
            if character.isalnum()
        )[:12] or "campaign"
        return root / "p2" / campaign_key

    def _exploit_workspace_state(self, result: ExploitWorkspaceResult, source: str) -> ExploitWorkspaceState:
        return ExploitWorkspaceState(
            workspace_id=result.workspace_id,
            hypothesis_id=result.hypothesis_id,
            target=result.target,
            language=result.language,
            filename=result.filename,
            status=result.status,
            failure_category=result.failure_category,
            failure_reason=result.failure_reason,
            next_route=result.next_route,
            source_summary=(source[:240] + "..." if len(source) > 240 else source),
            source_hash=result.source_hash,
            artifact_hash=result.artifact_hash,
            static_check_status=result.static_check_status,
            static_check_findings=list(result.static_check_findings),
            compile_command=result.compile_command,
            compile_exit_code=result.compile_exit_code,
            compile_stdout=result.compile_stdout,
            compile_stderr=result.compile_stderr,
            execute_command=result.execute_command,
            execute_exit_code=result.execute_exit_code,
            execute_stdout=result.execute_stdout,
            execute_stderr=result.execute_stderr,
            impact_verdict=result.impact_verdict,
            result_class=result.result_class,
            container_name=result.container_name,
            workspace_path=result.workspace_path,
            created_at=result.created_at,
            completed_at=result.completed_at,
            cleanup_complete=result.cleanup_complete,
        )

    async def _run_persistent_attack_session(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> SecurityTestingState:
        """Execute the P1 three-step stateful verification when requested."""
        campaign = state.campaign
        if campaign is None or not self._persistent_session_requested(state.request):
            return state
        environment_limited = any(
            task.status == TASK_FAILED
            and str(task.failure_analysis.get("failure_category") or "") == "environment_limited"
            for task in campaign.tasks
        )
        if environment_limited:
            limitation = (
                "P1 persistent attack session was not created because pre-planning discovery "
                "proved the target unreachable from the isolated runner."
            )
            if limitation not in campaign.operational_constraints:
                campaign.operational_constraints.append(limitation)
            logger.info(
                "security.attack_session.environment_limited %s",
                self._log_payload(context=context, campaign_id=campaign.campaign_id),
            )
            return state
        if not self._attack_session_enabled:
            campaign.shell_session = SecurityShellSessionState(
                campaign_id=campaign.campaign_id,
                target_allowlist=[target.value for target in campaign.targets],
                approval_scope_hash=self._approval_scope_hash(context),
                status="disabled",
                close_reason="SECURITY_ATTACK_SESSION_ENABLED is false.",
            )
            campaign.operational_constraints.append(
                "P1 persistent attack session was requested but disabled by server configuration."
            )
            logger.info(
                "security.attack_session.disabled %s",
                self._log_payload(context=context, campaign_id=campaign.campaign_id),
            )
            return state
        if self._execution_environment_service is None:
            campaign.shell_session = SecurityShellSessionState(
                campaign_id=campaign.campaign_id,
                target_allowlist=[target.value for target in campaign.targets],
                approval_scope_hash=self._approval_scope_hash(context),
                status="failed",
                close_reason="Security execution environment service is unavailable.",
            )
            return state

        target = campaign.targets[0].value if campaign.targets else ""
        artifact_dir = self._attack_session_artifact_dir(state, context)
        shell = SecurityShellSession(
            environment=self._execution_environment_service,
            campaign_id=campaign.campaign_id,
            target_allowlist=[item.value for item in campaign.targets if item.value],
            approval_scope_hash=self._approval_scope_hash(context),
            artifact_dir=artifact_dir,
            context=context,
            timeout_seconds=self._attack_session_timeout_seconds,
            command_timeout_seconds=self._attack_session_command_timeout_seconds,
        )
        session_state = SecurityShellSessionState(
            session_id=shell.session_id,
            campaign_id=campaign.campaign_id,
            target_allowlist=list(shell.target_allowlist),
            approval_scope_hash=shell.approval_scope_hash,
            status="creating",
        )
        campaign.shell_session = session_state
        logger.info(
            "security.attack_session.created %s",
            self._log_payload(
                context=context,
                campaign_id=campaign.campaign_id,
                attack_session_id=shell.session_id,
                target_allowlist=shell.target_allowlist,
            ),
        )
        try:
            await shell.create()
            session_state.container_name = shell.container_name
            session_state.created_at = shell.created_at
            session_state.status = "active"
            state_value = self._attack_session_state_value(campaign.campaign_id)
            commands = [
                (
                    "establish_state",
                    f"printf '%s\\n' {state_value!r} > /work/p1_session_state.txt && cat /work/p1_session_state.txt",
                ),
                (
                    "use_state_and_probe",
                    "test -s /work/p1_session_state.txt && "
                    f"curl -sS -o /work/p1_http_body.txt -D /work/p1_http_headers.txt --max-time 20 {target!r} && "
                    "printf 'state=' && cat /work/p1_session_state.txt && "
                    "printf 'status=' && awk 'NR==1 {print $2}' /work/p1_http_headers.txt",
                ),
                (
                    "read_result",
                    "printf 'state=' && cat /work/p1_session_state.txt && "
                    "printf 'http=' && head -n 1 /work/p1_http_headers.txt && "
                    "printf 'body_bytes=' && wc -c < /work/p1_http_body.txt",
                ),
            ]
            for index, (step, command) in enumerate(commands, start=1):
                logger.info(
                    "security.attack_session.command_started %s",
                    self._log_payload(
                        context=context,
                        campaign_id=campaign.campaign_id,
                        attack_session_id=shell.session_id,
                        container_name=shell.container_name,
                        step=step,
                        command_index=index,
                    ),
                )
                result = await shell.exec(command=command, target=target)
                evidence_id = f"ev_attack_session_{index}_{shell.session_id[-8:]}"
                command_state = SecurityShellCommandState(
                    command_id=shell.commands[-1].command_id,
                    step=step,
                    command=command,
                    target=target,
                    container_name=shell.container_name,
                    started_at=result.started_at.isoformat(),
                    completed_at=result.completed_at.isoformat(),
                    exit_code=result.exit_code,
                    timed_out=result.timed_out,
                    stdout_summary=(result.stdout or "")[-2000:],
                    stderr_summary=(result.stderr or "")[-1200:],
                    evidence_ids=[evidence_id],
                )
                session_state.commands.append(command_state)
                campaign.execution_records.append(
                    ToolExecutionRecord(
                        record_id=f"exec_attack_session_{index}_{shell.session_id[-8:]}",
                        task_id=f"attack_session_step_{index}",
                        tool_name="security-shell-session",
                        command=command,
                        started_at=command_state.started_at,
                        completed_at=command_state.completed_at,
                        duration_seconds=max(
                            0.0,
                            (result.completed_at - result.started_at).total_seconds(),
                        ),
                        exit_code=result.exit_code,
                        stdout_summary=command_state.stdout_summary,
                        stderr_summary=command_state.stderr_summary,
                        success=result.exit_code == 0 and not result.timed_out,
                        error=(result.stderr or "")[-1200:]
                        if result.exit_code != 0 or result.timed_out
                        else "",
                    )
                )
                campaign.evidence.append(
                    EvidenceArtifact(
                        artifact_id=evidence_id,
                        artifact_type="security_shell_output",
                        filename=f"attack_session_step_{index}.txt",
                        content_type="text/plain",
                        content=(result.stdout or result.stderr or "")[-6000:],
                        size_bytes=len((result.stdout or result.stderr or "").encode("utf-8", errors="ignore")),
                        source_task_id=f"attack_session_step_{index}",
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                logger.info(
                    "security.attack_session.command_completed %s",
                    self._log_payload(
                        context=context,
                        campaign_id=campaign.campaign_id,
                        attack_session_id=shell.session_id,
                        container_name=shell.container_name,
                        step=step,
                        command_index=index,
                        exit_code=result.exit_code,
                        timed_out=result.timed_out,
                        evidence_id=evidence_id,
                    ),
                )
                if result.exit_code != 0 or result.timed_out:
                    raise RuntimeError(
                        result.stderr.strip()
                        or f"Persistent attack session step {index} failed with exit code {result.exit_code}."
                    )
            session_state.heartbeat_ok = await shell.heartbeat()
            if not session_state.heartbeat_ok:
                raise RuntimeError("Persistent attack session heartbeat failed before close.")
            session_state.status = "completed"
            campaign.activities.append(
                AgentActivityRecord(
                    activity_id=f"act_{shell.session_id}",
                    agent_key="security-testing-agent",
                    agent_name="security-testing-agent",
                    task_id="persistent_attack_session",
                    action="completed",
                    summary=(
                        "Executed a three-step stateful verification in one campaign-scoped "
                        f"container ({shell.container_name})."
                    ),
                    started_at=shell.created_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    execution_mode="persistent_attack_session",
                    tool_calls=["create", "exec", "heartbeat", "close"],
                )
            )
        except Exception as exc:
            session_state.status = "failed"
            session_state.close_reason = str(exc)
            campaign.operational_constraints.append(
                f"P1 persistent attack session failed: {exc}"
            )
            logger.exception(
                "security.attack_session.failed %s",
                self._log_payload(
                    context=context,
                    campaign_id=campaign.campaign_id,
                    attack_session_id=shell.session_id,
                    container_name=shell.container_name,
                    error=str(exc),
                ),
            )
        finally:
            try:
                await shell.close(session_state.status)
                session_state.cleanup_complete = True
            except Exception as cleanup_exc:
                session_state.cleanup_complete = False
                session_state.close_reason = (
                    f"{session_state.close_reason}; cleanup failed: {cleanup_exc}"
                    if session_state.close_reason
                    else f"cleanup failed: {cleanup_exc}"
                )
                logger.exception(
                    "security.attack_session.cleanup_failed %s",
                    self._log_payload(
                        context=context,
                        campaign_id=campaign.campaign_id,
                        attack_session_id=shell.session_id,
                        container_name=shell.container_name,
                        error=str(cleanup_exc),
                    ),
                )
            session_state.closed_at = shell.closed_at
            if not session_state.close_reason:
                session_state.close_reason = shell.close_reason
            logger.info(
                "security.attack_session.closed %s",
                self._log_payload(
                    context=context,
                    campaign_id=campaign.campaign_id,
                    attack_session_id=shell.session_id,
                    container_name=shell.container_name,
                    status=session_state.status,
                    command_count=len(session_state.commands),
                    heartbeat_ok=session_state.heartbeat_ok,
                    cleanup_complete=session_state.cleanup_complete,
                    close_reason=session_state.close_reason,
                ),
            )
        return state

    def _persistent_session_requested(self, request: SecurityTestingRequestState) -> bool:
        text = f"{request.objective} {request.raw_message}".lower()
        normalized = " ".join(
            text.replace("-", " ").replace("_", " ").split()
        )
        denied_phrases = (
            "zero persistent attack session",
            "zero persistent session",
            "no persistent attack session",
            "no persistent session",
            "do not create persistent attack session",
            "不得创建持久",
            "不创建持久",
            "零持久会话",
        )
        if any(phrase in normalized for phrase in denied_phrases):
            return False
        explicit_phrase = any(
            token in normalized
            for token in (
                "persistent session",
                "persistent attack session",
                "stateful session",
                "持久会话",
                "持久隔离攻击会话",
                "三步持久",
            )
        )
        p1_session_request = (
            "p1" in normalized
            and any(token in normalized for token in ("session", "three step", "3 step"))
        )
        return explicit_phrase or p1_session_request

    def _approval_scope_hash(self, context: Any) -> str:
        bundle = getattr(context, "context_bundle", None)
        if isinstance(bundle, dict):
            grant = bundle.get("trusted_security_authorization")
            if isinstance(grant, dict):
                value = str(grant.get("approval_scope_hash") or "").strip()
                if value:
                    return value
        return f"campaign-{uuid4().hex[:16]}"

    def _attack_session_artifact_dir(self, state: SecurityTestingState, context: Any) -> Path:
        root = Path(__file__).resolve().parents[2] / "data" / "artifacts"
        return (
            root
            / (str(getattr(context, "session_id", "") or "session"))
            / (str(getattr(context, "turn_id", "") or "turn"))
            / f"persistent_attack_session_{state.campaign.campaign_id[:8]}"
        )

    def _attack_session_state_value(self, campaign_id: str) -> str:
        return f"campaign={campaign_id};nonce={uuid4().hex[:12]}"

    async def _run_attack_chain(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> SecurityTestingState:
        """Run bounded, evidence-backed verification after reconnaissance."""
        campaign = state.campaign
        if campaign is None:
            return state
        state.record_phase_transition(
            PHASE_HYPOTHESIS_PLANNING,
            "Planning evidence-backed vulnerability hypotheses.",
        )
        if not self._attack_chain_enabled:
            limitation = (
                "Attack-chain verification is disabled; findings were not promoted to "
                "hypotheses or verification attempts."
            )
            if limitation not in campaign.operational_constraints:
                campaign.operational_constraints.append(limitation)
            state.record_phase_transition(
                PHASE_VERIFICATION_COMPLETE,
                "Attack-chain verification disabled by configuration.",
            )
            return state

        authorization_scope_hash = self._security_authorization_scope_hash(context)
        for loop_index in range(1, self._campaign_max_loops + 1):
            tasks_before_loop = list(campaign.tasks)
            verification_tasks = self._attack_chain.plan_next_attempts(
                campaign,
                authorization_scope_hash=authorization_scope_hash,
                max_attempts=self._campaign_max_attempts,
            )
            if not verification_tasks:
                break
            campaign.attack_loop_count = loop_index
            campaign.tasks.extend(verification_tasks)
            state.record_phase_transition(
                PHASE_ATTACK_LOOP,
                f"Executing attack-chain verification loop {loop_index} with "
                f"{len(verification_tasks)} task(s).",
            )
            self._checkpoint_execution_state(
                state=state,
                context=context,
                event_type="attack_loop_started",
                tasks=campaign.tasks,
                summary=f"Attack-chain verification loop {loop_index} started.",
            )
            self._attack_chain.mark_attempts_running(campaign, verification_tasks)
            pool = SecurityTaskPool(tasks=verification_tasks)
            if self._can_use_subagent_execution(context):
                coordinator = SecuritySubagentCoordinator(
                    pool=pool,
                    coordinator_runtime_service=self._coordinator_runtime_service,
                    session_store=self._session_store,
                    parent_context=self._build_dispatch_context(context),
                    max_workers=min(campaign.max_workers, len(verification_tasks)),
                    worker_model_key=str(getattr(context, "selected_model_key", "") or "") or None,
                    checkpoint_callback=self._build_checkpoint_callback(state, context),
                    target_guard=self._target_guard,
                    execution_monitor=self._execution_monitor,
                    runner_lookup=self._tool_catalog.resolve_runner_for_family,
                    output_summary_threshold_bytes=self._output_summary_threshold_bytes,
                    task_refiner=None,
                    max_reflect_attempts=1,
                    interrupt_check=lambda: self._is_interrupt_requested(context),
                )
                completed_verification_tasks = await coordinator.run_all()
                campaign.activities.extend(coordinator.activities)
            else:
                completed_verification_tasks = await self._run_tasks_locally(
                    pool,
                    context,
                    campaign,
                    checkpoint_callback=self._build_checkpoint_callback(state, context),
                    execution_mode="attack_chain_local_fallback",
                )
            # Coordinator checkpoints operate on the verification pool and therefore
            # temporarily replace campaign.tasks. Restore the complete campaign ledger
            # before handling interrupts, settlement, reporting, or replay snapshots.
            campaign.tasks = [*tasks_before_loop, *completed_verification_tasks]
            if self._is_interrupt_requested(context):
                state.error = "Security campaign interrupted during attack-chain verification."
                if state.error not in state.notes:
                    state.notes.append(state.error)
                state.record_phase_transition(
                    PHASE_INTERRUPTED,
                    "Parent session interrupt propagated during attack-chain verification.",
                )
                return state
            self._evidence_service.hydrate_missing_records(campaign)
            self._attack_chain.settle_attempts(campaign, completed_verification_tasks)
            self._checkpoint_execution_state(
                state=state,
                context=context,
                event_type="attack_loop_settled",
                tasks=campaign.tasks,
                summary=f"Attack-chain verification loop {loop_index} settled.",
            )
            campaign.updated_at = datetime.now(timezone.utc).isoformat()

        unsettled = not self._attack_chain.all_chains_settled(campaign)
        if unsettled:
            limitation = (
                "Attack-chain verification reached its configured loop or attempt budget; "
                "unsettled hypotheses require manual review."
            )
            if limitation not in campaign.operational_constraints:
                campaign.operational_constraints.append(limitation)
        state.record_phase_transition(
            PHASE_VERIFICATION_COMPLETE,
            (
                f"Attack-chain verification settled {len(campaign.verification_attempts)} "
                f"attempt(s) across {len(campaign.vulnerability_hypotheses)} hypothesis(es)."
            ),
        )
        return state

    def _security_authorization_scope_hash(self, context: Any) -> str:
        bundle = getattr(context, "context_bundle", None)
        if not isinstance(bundle, dict):
            return ""
        authorization = bundle.get("trusted_security_authorization")
        if not isinstance(authorization, dict):
            authorization = bundle.get("security_authorization")
        if not isinstance(authorization, dict):
            return ""
        return str(authorization.get("scope_hash") or "")

    async def _deliver_report_if_requested(
        self,
        *,
        state: SecurityTestingState,
        context: Any,
        markdown_report: str,
        html_report: str,
    ) -> SecurityTestingState:
        recipients = self._to_string_list(state.request.report_recipients)
        if not recipients or state.report is None:
            return state

        payload = self._build_report_delivery_payload(
            report=state.report,
            recipients=recipients,
            markdown_report=markdown_report,
            html_report=html_report,
            context=context,
        )
        if self._report_delivery_executor is None:
            state.delivery = ReportDeliveryRecord(
                status="skipped",
                recipients=recipients,
                subject=str(payload.get("subject") or ""),
                summary="Report recipients were provided, but no report delivery executor is configured.",
                error="report_delivery_executor_not_configured",
            )
            state.notes.append(state.delivery.summary)
            return state

        try:
            result = await self._report_delivery_executor(payload, context)
        except Exception as exc:
            state.delivery = ReportDeliveryRecord(
                status="failed",
                recipients=recipients,
                subject=str(payload.get("subject") or ""),
                summary=f"Security report email delivery failed: {exc}",
                error=str(exc),
            )
            state.notes.append(state.delivery.summary)
            return state

        delivery = result.get("delivery") if isinstance(result.get("delivery"), dict) else {}
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
        artifact_paths = [
            str(item.get("path") or item.get("filename") or "")
            for item in artifacts
            if isinstance(item, dict) and str(item.get("path") or item.get("filename") or "").strip()
        ]
        explicit_status = str(result.get("status") or "").strip().lower()
        ok = result.get("ok")
        failed = explicit_status in {"failed", "denied"} or ok is False or bool(result.get("error"))
        confirmation_required = bool(result.get("confirmation_required")) and not failed
        if confirmation_required:
            confirmation_summary = str(
                result.get("confirmation_summary") or result.get("summary") or ""
            ).strip()
            state.delivery = ReportDeliveryRecord(
                status="awaiting_confirmation",
                recipients=recipients,
                subject=str(payload.get("subject") or ""),
                summary=confirmation_summary or "Security report email is ready for confirmation.",
                sent=False,
                provider=str(result.get("provider") or "tencent_agently"),
                from_email=str(result.get("from_email") or ""),
                recipient_count=len(recipients),
                confirmation_required=True,
                confirmation_token=str(result.get("confirmation_token") or ""),
                confirmation_summary=confirmation_summary,
                artifact_paths=artifact_paths,
            )
            state.notes.append("Security report email is prepared and waiting for user confirmation.")
            return state
        sent = bool(delivery.get("sent")) and not failed
        recipient_count = int(delivery.get("recipient_count") or len(recipients)) if sent else 0
        delivery_error = "" if sent else str(result.get("error") or result.get("summary") or "email_delivery_failed")

        state.delivery = ReportDeliveryRecord(
            status="sent" if sent else "failed",
            recipients=recipients,
            subject=str(payload.get("subject") or ""),
            summary=str(result.get("summary") or ""),
            sent=sent,
            provider=str(delivery.get("provider") or ""),
            from_email=str(delivery.get("from_email") or ""),
            recipient_count=recipient_count,
            artifact_paths=artifact_paths,
            error=delivery_error,
            delivered_at=datetime.now(timezone.utc).isoformat() if sent else "",
        )
        if sent:
            state.notes.append(f"Delivered security report email to {len(recipients)} recipient(s).")
            state.record_phase_transition(PHASE_EMAIL_DELIVERED, "Security report delivered by email.")
        else:
            state.notes.append(
                state.delivery.summary
                or f"Security report email delivery failed for {len(recipients)} recipient(s)."
            )
        return state

    async def _ensure_terminal_delivery(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> SecurityTestingState:
        """Last-line guarantee that a terminal campaign produced delivery state.

        This handles the seam between ``_execute_campaign`` (success path) and
        ``_finalize_failed_state`` (failure path) so that:

        - if a report exists and recipients exist but no delivery was ever
          attempted, delivery is attempted once;
        - if the phase did not reach ``PHASE_REPORT_READY`` /
          ``PHASE_EMAIL_DELIVERED`` even though a report was generated, the
          phase is reconciled to ``PHASE_REPORT_READY`` so consumers don't
          observe a half-finished state.
        """
        if state.report is None:
            return state
        recipients = self._to_string_list(state.request.report_recipients)
        if recipients and state.delivery is None:
            state = await self._deliver_report_if_requested(
                state=state,
                context=context,
                markdown_report=state.report_markdown,
                html_report=state.report_html,
            )
        if state.phase not in {PHASE_REPORT_READY, PHASE_EMAIL_DELIVERED, PHASE_FAILED}:
            state.record_phase_transition(PHASE_REPORT_READY, "Report reconciled to terminal state.")
        return state

    async def _finalize_failed_state(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> SecurityTestingState:
        """Build and optionally deliver a report even when the campaign failed early."""
        if state.report is not None:
            return state

        if not state.execution_strategy:
            state.execution_strategy = "synthetic_failure_summary"
        campaign = self._ensure_failure_campaign(state)
        if not campaign.tasks:
            campaign.tasks = [self._build_failure_placeholder_task(state)]
        campaign.updated_at = datetime.now(timezone.utc).isoformat()

        report = self._report_builder.build_report(campaign)
        markdown_report = self._report_builder.build_markdown(report)
        html_report = self._report_template.render(
            report=report,
            markdown_content=markdown_report,
            sender=str(getattr(context, "selected_agent_key", "") or "security-testing-agent"),
        )
        artifacts = self._prepare_report_artifacts(
            self._report_builder.build_artifacts(
                report=report,
                markdown_report=markdown_report,
                html_report=html_report,
            ),
            state=state,
            context=context,
        )
        state.report_markdown = markdown_report
        state.report_html = html_report
        state.artifacts = self._artifact_metadata(artifacts)

        verification_verdict = self._verification_policy.verify(campaign=campaign, report=report)
        evaluation_result = self._evaluation_policy.evaluate(
            campaign=campaign,
            report=report,
            verification_verdict=verification_verdict,
        )
        state.verification_result = verification_verdict.to_dict()
        state.evaluation_result = evaluation_result.to_dict()
        state.errors = self._build_error_records(evaluation_result.to_dict(), campaign.tasks)

        report.artifacts = list(state.artifacts)
        report.verification_result = dict(state.verification_result)
        report.evaluation_result = dict(state.evaluation_result)
        state.report = report
        state.campaign = campaign
        state.notes.append("Security failure report generated for an incomplete campaign.")
        state.notes.append(f"Verification verdict: {verification_verdict.summary}")
        state.notes.append(f"Security evaluation: {evaluation_result.summary}")

        state.record_phase_transition(PHASE_REPORT_READY, "Failure report generated.")
        state = await self._deliver_report_if_requested(
            state=state,
            context=context,
            markdown_report=markdown_report,
            html_report=html_report,
        )
        return state

    def _build_report_delivery_payload(
        self,
        *,
        report: SecurityReport,
        recipients: list[str],
        markdown_report: str,
        html_report: str,
        context: Any,
    ) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc)
        target_label = " ".join((report.target_summary or report.campaign_id[:8]).split())
        if len(target_label) > 80:
            target_label = f"{target_label[:77]}..."
        date_label = generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        subject = f"安全测试报告 - {target_label} - {date_label}"
        return {
            "to": recipients,
            "subject": subject,
            "content": markdown_report,
            "content_markdown": markdown_report,
            "content_html": html_report,
            "sender": str(getattr(context, "selected_agent_key", "") or "security-testing-agent"),
            "time_label": date_label,
            "template_key": "security_testing_full",
            "template_context": {
                "campaign_id": report.campaign_id,
                "target_summary": report.target_summary,
                "total_findings": str(report.total_findings),
                "completed_tasks": str(report.completed_tasks),
                "total_tasks": str(report.total_tasks),
            },
            "file_name": f"security_report_email_{report.campaign_id[:8]}",
        }

    def _ensure_failure_campaign(self, state: SecurityTestingState) -> SecurityCampaign:
        if state.campaign is not None:
            if not state.campaign.targets:
                state.campaign.targets = list(state.targets)
            if not state.campaign.objective:
                state.campaign.objective = state.request.objective or state.request.raw_message
            if not state.campaign.scope_notes:
                state.campaign.scope_notes = ", ".join(target.value for target in state.targets if target.value)
            if not state.campaign.operational_constraints and state.request.access_constraints:
                state.campaign.operational_constraints = list(state.request.access_constraints)
            if not state.campaign.created_at:
                state.campaign.created_at = datetime.now(timezone.utc).isoformat()
            return state.campaign

        campaign = SecurityCampaign(
            campaign_id=str(uuid4()),
            objective=state.request.objective or state.request.raw_message or "Security testing campaign",
            targets=list(state.targets),
            scope_notes=", ".join(target.value for target in state.targets if target.value) or "No resolved target.",
            operational_constraints=list(state.request.access_constraints),
            risk_tolerance=state.request.risk_tolerance or "medium",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        state.campaign = campaign
        return campaign

    def _build_failure_placeholder_task(self, state: SecurityTestingState) -> SecurityTask:
        now = datetime.now(timezone.utc).isoformat()
        target_value = ""
        surface_type = ""
        if state.targets:
            target_value = str(state.targets[0].value or "")
            surface_type = str(state.targets[0].target_type or "")
        elif state.request.target_url:
            target_value = state.request.target_url
            surface_type = "url"
        elif state.request.target_host:
            target_value = state.request.target_host
            surface_type = "host"
        elif state.request.target_network:
            target_value = state.request.target_network
            surface_type = "network"

        summary = state.error or (state.notes[-1] if state.notes else "Security campaign failed before task execution.")
        return SecurityTask(
            task_id="failure_summary",
            name="Campaign Failure Summary",
            description="Synthetic failed task recorded so the failure path remains auditable.",
            surface_type=surface_type,
            tool_family="general_scan",
            command_profile="campaign_failure",
            target=target_value,
            risk_level="info",
            status=TASK_FAILED,
            attempts=1,
            started_at=now,
            completed_at=now,
            worker_execution_mode="synthetic_failure_summary",
            result_summary=summary,
            last_error=summary,
            worker_agent_key=SECURITY_FAILURE_ANALYST_KEY,
        )

    async def _run_tasks_locally(
        self,
        pool: SecurityTaskPool,
        context: Any,
        campaign: SecurityCampaign,
        checkpoint_callback: Callable[[str, SecurityTask, list[SecurityTask]], None] | None = None,
        execution_mode: str = "local_worker_fallback",
        request: SecurityTestingRequestState | None = None,
        enable_scenario_replan: bool = False,
    ) -> list[SecurityTask]:
        while not pool.is_complete:
            if self._is_interrupt_requested(context):
                pool.interrupt_unsettled("Parent security campaign interrupt requested.")
                break
            pool.resolve_blocked()
            ready = pool.ready_tasks()
            if not ready:
                break
            for task in ready:
                if self._is_interrupt_requested(context):
                    pool.interrupt_unsettled("Parent security campaign interrupt requested.")
                    break
                if self._reject_local_task_out_of_scope(task, pool, campaign, checkpoint_callback):
                    continue
                task.worker_agent_key = task.worker_agent_key or resolve_security_worker_agent(
                    surface_type=task.surface_type,
                    tool_family=task.tool_family,
                    command_profile=task.command_profile,
                )
                task.worker_execution_mode = execution_mode
                pool.mark_running(task.task_id)
                if checkpoint_callback is not None:
                    checkpoint_callback("task_running", task, pool.all_tasks)
                started_at = task.started_at
                runner_key = self._tool_catalog.resolve_runner_for_family(task.tool_family)
                result = await self._execute_task_with_runner(task, context, runner_key)
                reported_profile = str(result.get("command_profile") or "").strip()
                if reported_profile and reported_profile != task.command_profile:
                    result = {
                        **result,
                        "status": "failed",
                        "ok": False,
                        "success": False,
                        "error": (
                            f"Worker executed profile {reported_profile}, but task was assigned "
                            f"{task.command_profile}. Alternative profile was not accepted."
                        ),
                    }
                    task.failure_analysis = {
                        "failure_category": "profile_identity_mismatch",
                        "root_cause": result["error"],
                        "retryable": False,
                        "alternative_profile": reported_profile,
                    }
                task.raw_output = compact_security_output(
                    str(result.get("raw_output") or ""),
                    max_bytes=self._output_summary_threshold_bytes,
                )
                task.parsed_result = result.get("parsed_result") if isinstance(result.get("parsed_result"), dict) else {}
                task.result_summary = str(result.get("summary") or "")
                task.artifacts = [
                    str(item.get("path") or item.get("label") or item.get("filename") or "")
                    for item in result.get("artifacts", [])
                    if isinstance(item, dict)
                ]
                if result.get("success") or result.get("ok"):
                    pool.mark_completed(task.task_id, task.result_summary)
                    if checkpoint_callback is not None:
                        checkpoint_callback("task_completed", task, pool.all_tasks)
                else:
                    error_text = str(result.get("error") or task.result_summary or "execution_failed")
                    signals = " ".join(
                        value
                        for value in (task.result_summary, task.raw_output, error_text)
                        if value
                    )
                    from src.modes.security_testing_mode.subagent_coordinator import _detect_restricted_access

                    if _detect_restricted_access(signals):
                        task.failure_analysis = {
                            "failure_category": "restricted_access",
                            "root_cause": (
                                "Target platform requires additional access that the runner could not satisfy."
                            ),
                            "retryable": False,
                            "suggested_fix": (
                                "Provide credentials, deploy the lab, or run from an authorized network."
                            ),
                            "alternative_profile": "",
                            "notes": error_text[:500],
                        }
                        # Disable retries for restricted-access targets.
                        task.max_retries = 0
                    pool.mark_failed(task.task_id, error_text)
                    if checkpoint_callback is not None:
                        checkpoint_callback("task_failed", task, pool.all_tasks)
                self._evidence_service.record_runner_result(campaign, task, result, started_at=started_at)
                self._record_local_activity(task, started_at, campaign=campaign)
                replan_result: dict[str, Any] = {}
                if enable_scenario_replan and request is not None:
                    self._hydrate_campaign_from_task_results(campaign)
                    replan_result = self._scenario_incremental_planner.replan(
                        campaign=campaign,
                        request=request,
                        pool=pool,
                        refinement_id=f"local_{task.task_id}_{task.attempts}",
                    )
                if replan_result.get("replanned"):
                    logger.info(
                        "security.scenario.replanned %s",
                        self._log_payload(
                            context=context,
                            campaign_id=campaign.campaign_id,
                            refinement_id=replan_result.get("refinement_id"),
                            previous_scenario_id=replan_result.get("previous_scenario_id"),
                            scenario_id=replan_result.get("scenario_id"),
                            previous_product_type=replan_result.get("previous_product_type"),
                            product_type=replan_result.get("product_type"),
                            new_observed_facts=replan_result.get("new_observed_facts"),
                            changed_dimensions=replan_result.get("changed_dimensions"),
                            added_task_ids=replan_result.get("added_task_ids"),
                            removed_task_ids=replan_result.get("removed_task_ids"),
                            updated_task_ids=replan_result.get("updated_task_ids"),
                        ),
                    )
                    campaign.activities.append(
                        AgentActivityRecord(
                            activity_id=f"scenario_replan_{replan_result['refinement_id']}",
                            agent_key="security-scenario-incremental-planner",
                            agent_name="security-scenario-incremental-planner",
                            action="scenario_replanned",
                            summary=(
                                f"Scenario changed from {replan_result.get('previous_product_type')} "
                                f"to {replan_result.get('product_type')}."
                            ),
                            completed_at=datetime.now(timezone.utc).isoformat(),
                            execution_mode="scheduler",
                        )
                    )
                    # A replan can remove a task that was already in the
                    # current ready snapshot. Re-select from the pool before
                    # dispatching another task so stale work is never executed.
                    break
        return pool.all_tasks

    def _reject_local_task_out_of_scope(
        self,
        task: SecurityTask,
        pool: SecurityTaskPool,
        campaign: SecurityCampaign,
        checkpoint_callback: Callable[[str, SecurityTask, list[SecurityTask]], None] | None,
    ) -> bool:
        """S6 pre-execution allowlist gate for the local fallback path.

        Mirrors the coordinator's pre-dispatch gate so both execution
        strategies refuse out-of-scope targets identically. The execute()-level
        gate is still the last line of defense for worker-crafted commands.
        """
        result = self._target_guard.evaluate_target(task.target)
        if result.ok:
            if getattr(result, "warn_public", False) and result.reason:
                logger.warning(
                    "security target allowlist warning (local): task=%s %s",
                    task.task_id,
                    result.reason,
                )
            return False
        logger.warning(
            "security target rejected before local execution: task=%s target=%s reason=%s",
            task.task_id,
            task.target,
            result.reason,
        )
        task.failure_analysis = {
            "failure_category": "target_not_allowed",
            "root_cause": result.reason,
            "retryable": False,
            "suggested_fix": (
                "Add the target to security_target_allowlist or retest an in-scope target."
            ),
            "alternative_profile": "",
            "notes": f"target={task.target}",
        }
        task.max_retries = 0
        pool.mark_failed(task.task_id, f"target_not_allowed: {result.reason}")
        if checkpoint_callback is not None:
            checkpoint_callback("task_failed", task, pool.all_tasks)
        return True

    async def _execute_task_with_runner(
        self,
        task: SecurityTask,
        context: Any,
        runner_key: str,
    ) -> dict[str, Any]:
        if self._runner_executor is None:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": "Security runner executor is not configured.",
                "error": "runner_executor_not_configured",
            }
        return await self._runner_executor(
            {
                "worker_action": "execute_security_task",
                "task": task.model_dump(mode="json"),
            },
            context,
            runner_key,
        )

    async def _execute_dispatched_task(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        raw_task = arguments.get("task")
        if not isinstance(raw_task, dict):
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": "Security task execution requires a serialized `task` payload.",
                "error": "missing_task_payload",
            }
        task = SecurityTask.model_validate(raw_task)
        runner_key = self._tool_catalog.resolve_runner_for_family(task.tool_family)
        return await self._execute_task_with_runner(task, context, runner_key)

    def _build_tasks_for_target(
        self,
        target: TargetCandidate,
        request: SecurityTestingRequestState,
    ) -> list[SecurityTask]:
        return self._recon_planner.build_tasks_for_target(target, request)

    def _suggest_profile_keys(
        self,
        surface_type: str,
        target: TargetCandidate,
        request: SecurityTestingRequestState,
    ) -> list[str]:
        return self._recon_planner.suggest_profile_keys(surface_type, target, request)

    def _hydrate_campaign_from_task_results(self, campaign: SecurityCampaign) -> None:
        self._asset_discovery.hydrate_campaign_from_task_results(
            campaign,
            profile_lookup=self._tool_catalog.get_profile,
            finding_normalizer=self._finding_normalizer,
            severity_evaluator=self._severity_evaluator,
        )

    def _record_local_activity(
        self,
        task: SecurityTask,
        started_at: str = "",
        campaign: SecurityCampaign | None = None,
    ) -> None:
        if task.completed_at and started_at:
            try:
                start = datetime.fromisoformat(started_at)
                end = datetime.fromisoformat(task.completed_at)
                duration = max(0.0, (end - start).total_seconds())
            except (TypeError, ValueError):
                duration = 0.0
        else:
            duration = 0.0
        if not task.worker_agent_key:
            task.worker_agent_key = resolve_security_worker_agent(
                surface_type=task.surface_type,
                tool_family=task.tool_family,
                command_profile=task.command_profile,
            )
        activity = AgentActivityRecord(
            activity_id=f"act_{task.task_id}",
            agent_key=task.worker_agent_key,
            agent_name=task.worker_agent_key,
            task_id=task.task_id,
            action="completed" if task.status == TASK_COMPLETED else "failed",
            summary=task.result_summary or task.last_error,
            started_at=started_at or task.started_at,
            completed_at=task.completed_at,
            duration_seconds=duration,
            execution_mode=task.worker_execution_mode or "local_worker_fallback",
            tool_calls=[task.command_profile],
        )
        if activity.summary:
            self._append_unique_observation(task, activity.summary)
        # Persist the activity onto the campaign so the report renderer can
        # show the same execution trail as the subagent path. Without this
        # the local fallback path produced thinner reports than the subagent
        # path.
        if campaign is not None and not any(
            existing.activity_id == activity.activity_id for existing in campaign.activities
        ):
            campaign.activities.append(activity)

    async def _analyze_failed_tasks(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> list[str]:
        if state.campaign is None:
            return []
        failed_tasks = [task for task in state.campaign.tasks if task.status == TASK_FAILED]
        if not failed_tasks:
            return []
        if self._can_use_subagent_execution(context):
            try:
                # Hard cap on the entire subagent-driven failure analysis so
                # one stuck failure-analyst can never block report generation.
                # The inner wait gives normal model calls enough time and
                # explicitly cancels any child that still exceeds its budget.
                return await asyncio.wait_for(
                    self._dispatch_failure_analysis_subagents(state, context, failed_tasks),
                    timeout=180.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Security failure-analysis subagent dispatch exceeded the 180s deadline; "
                    "falling back to local heuristic analysis for %d failed task(s).",
                    len(failed_tasks),
                )
            except Exception as exc:
                logger.warning(
                    "Security failure-analysis subagent dispatch raised %s: %s; "
                    "falling back to local heuristic analysis.",
                    type(exc).__name__,
                    exc,
                )
            # Subagent path failed or timed out — fall through to local
            # analysis so the campaign can still settle into a report.
            notes = [
                "Failure analyst subagent timed out or failed; using local heuristic analysis for failed tasks.",
            ]
        else:
            notes = [
                "Failure analyst subagent unavailable; using local failure-analysis fallback for failed tasks.",
            ]
        for task in failed_tasks:
            analysis = self._local_failure_analysis(task)
            task.failure_analysis = analysis
            self._append_unique_observation(
                task,
                f"Failure analysis: {analysis.get('root_cause') or analysis.get('failure_category') or 'unknown'}",
            )
            self._append_failure_analysis_activity(
                state.campaign,
                task,
                summary=analysis.get("root_cause") or analysis.get("notes") or task.last_error,
                execution_mode="local_failure_analysis",
            )
            notes.append(
                f"Failure analyst locally classified {task.task_id} as {analysis.get('failure_category') or 'execution'}."
            )
        return notes

    async def _dispatch_failure_analysis_subagents(
        self,
        state: SecurityTestingState,
        context: Any,
        failed_tasks: list[SecurityTask],
    ) -> list[str]:
        if state.campaign is None or self._coordinator_runtime_service is None or self._session_store is None:
            return []

        workers: list[dict[str, Any]] = []
        task_map: dict[str, SecurityTask] = {}
        for task in failed_tasks:
            analysis_task_id = f"failure_analysis_{task.task_id}"
            task_map[analysis_task_id] = task
            self._checkpoint_execution_state(
                state=state,
                context=context,
                event_type="failure_analysis_requested",
                task=task,
                summary="Dispatching failure analysis for failed task.",
                worker_agent_key_override=SECURITY_FAILURE_ANALYST_KEY,
                execution_mode_override="subagent_failure_analysis",
            )
            workers.append(
                {
                    "task_id": analysis_task_id,
                    "description": f"Failure analysis for {task.command_profile} -> {task.target}",
                    "prompt": build_security_failure_analysis_prompt(task),
                    "agent_key": SECURITY_FAILURE_ANALYST_KEY,
                    "model_key": str(getattr(context, "selected_model_key", "") or "") or None,
                    "context": {
                        "dispatch_role": "security_failure_analysis",
                        "mode_key": "security_testing",
                        "security_task_id": task.task_id,
                        "surface_type": task.surface_type,
                        "tool_family": task.tool_family,
                        "command_profile": task.command_profile,
                        "target_fingerprint": state.request.target_fingerprint,
                        "campaign_id": state.campaign.campaign_id,
                        "security_memory_scope": "session_only",
                    },
                }
            )

        dispatch_result = await self._coordinator_runtime_service.dispatch(
            payload={"workers": workers},
            context=self._build_failure_analysis_dispatch_context(context),
        )
        records = {
            str(item.get("task_id") or ""): item
            for item in dispatch_result.get("workers", [])
            if isinstance(item, dict)
        }
        child_session_ids = [
            str(record.get("child_session_id") or "")
            for record in records.values()
            if str(record.get("status") or "") == "running" and str(record.get("child_session_id") or "")
        ]
        settled_sessions = await self._wait_for_worker_sessions(
            child_session_ids,
            overall_timeout_seconds=120.0,
        )
        settled_map = {session.id: session for session in settled_sessions}
        unfinished_task_ids: list[str] = []
        unfinished_session_ids: list[str] = []
        for analysis_task_id, record in records.items():
            child_session_id = str(record.get("child_session_id") or "")
            session = settled_map.get(child_session_id)
            if child_session_id and self._session_status_value(session) not in {
                "completed",
                "failed",
                "interrupted",
            }:
                unfinished_task_ids.append(analysis_task_id)
                unfinished_session_ids.append(child_session_id)
        if unfinished_session_ids:
            await self._coordinator_runtime_service.cancel_workers(
                task_ids=unfinished_task_ids,
                child_session_ids=unfinished_session_ids,
                reason="Security failure analysis exceeded the 120s child-session deadline.",
            )
            for child_session_id in unfinished_session_ids:
                refreshed = await self._session_store.get_session(child_session_id)
                if refreshed is not None:
                    settled_map[child_session_id] = refreshed
            logger.warning(
                "security_failure_analysis_workers_cancelled %s",
                json.dumps(
                    {
                        "campaign_id": state.campaign.campaign_id,
                        "task_ids": unfinished_task_ids,
                        "child_session_ids": unfinished_session_ids,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )

        notes: list[str] = []
        model_unavailable_detected = self._is_model_unavailable_failure(str(dispatch_result.get("error") or ""))
        for analysis_task_id, task in task_map.items():
            if model_unavailable_detected:
                self._apply_local_failure_analysis(
                    campaign=state.campaign,
                    task=task,
                    notes=notes,
                    note=f"Failure analyst skipped for {task.task_id}; active model unavailable, used local fallback.",
                )
                continue

            record = records.get(analysis_task_id)
            if not record:
                self._apply_local_failure_analysis(
                    campaign=state.campaign,
                    task=task,
                    notes=notes,
                    note=f"Failure analyst dispatch missing for {task.task_id}; used local fallback.",
                )
                continue

            child_session_id = str(record.get("child_session_id") or "")
            session = settled_map.get(child_session_id)
            if session is None:
                self._apply_local_failure_analysis(
                    campaign=state.campaign,
                    task=task,
                    notes=notes,
                    note=f"Failure analyst session not found for {task.task_id}; used local fallback.",
                )
                continue

            summary = self._extract_assistant_summary_from_messages(getattr(session, "messages", []))
            session_status = self._session_status_value(session)
            failure_excerpt = summary or self._extract_message_excerpt(getattr(session, "messages", []))
            if session_status != "completed" or self._is_model_unavailable_failure(failure_excerpt):
                note = (
                    f"Failure analyst could not review {task.task_id} because the active model was unavailable; "
                    "used local fallback."
                    if self._is_model_unavailable_failure(failure_excerpt)
                    else f"Failure analyst session ended with status {session_status} for {task.task_id}; used local fallback."
                )
                if self._is_model_unavailable_failure(failure_excerpt):
                    model_unavailable_detected = True
                self._apply_local_failure_analysis(
                    campaign=state.campaign,
                    task=task,
                    notes=notes,
                    note=note,
                )
                continue

            parsed = self._parse_failure_analysis_response(summary)
            task.failure_analysis = parsed
            self._append_unique_observation(
                task,
                f"Failure analysis: {parsed.get('root_cause') or parsed.get('failure_category') or 'unknown'}",
            )
            self._append_failure_analysis_activity(
                state.campaign,
                task,
                summary=parsed.get("root_cause") or summary or task.last_error,
                execution_mode="subagent_failure_analysis",
                session_id=child_session_id,
                started_at=self._session_iso(getattr(session, "created_at", None)),
                completed_at=self._session_iso(getattr(session, "updated_at", None)),
            )
            self._checkpoint_execution_state(
                state=state,
                context=context,
                event_type="failure_analysis_completed",
                task=task,
                summary=parsed.get("root_cause") or summary or "Failure analysis completed.",
                worker_agent_key_override=SECURITY_FAILURE_ANALYST_KEY,
                execution_mode_override="subagent_failure_analysis",
            )
            notes.append(
                f"Failure analyst reviewed {task.task_id}: {parsed.get('failure_category') or 'execution'}."
            )

        return notes

    def _apply_local_failure_analysis(
        self,
        *,
        campaign: SecurityCampaign,
        task: SecurityTask,
        notes: list[str],
        note: str,
    ) -> None:
        analysis = self._local_failure_analysis(task)
        task.failure_analysis = analysis
        self._append_unique_observation(
            task,
            f"Failure analysis: {analysis.get('root_cause') or analysis.get('failure_category') or 'unknown'}",
        )
        self._append_failure_analysis_activity(
            campaign,
            task,
            summary=analysis.get("root_cause") or analysis.get("notes") or task.last_error,
            execution_mode="local_failure_analysis",
        )
        notes.append(note)

    def _local_failure_analysis(self, task: SecurityTask) -> dict[str, Any]:
        from src.modes.security_testing_mode.subagent_coordinator import _detect_restricted_access

        signals = " ".join(
            value
            for value in (task.last_error, task.result_summary, task.raw_output)
            if value
        )
        message = signals.lower()
        network_limit_tokens = (
            "network is unreachable",
            "connection refused",
            "could not connect",
            "failed to connect",
            "no route to host",
            "name or service not known",
            "temporary failure in name resolution",
            "target_response_not_observed",
        )
        if any(token in message for token in network_limit_tokens):
            return {
                "failure_category": "environment_limited",
                "root_cause": (
                    "The isolated security runner did not observe a target response because the target "
                    "was unreachable or refused the connection from that execution environment."
                ),
                "retryable": False,
                "suggested_fix": (
                    "Expose the authorized target to the Kali Docker network or bind the service to a "
                    "reachable interface, then repeat the same controlled profile."
                ),
                "alternative_profile": "",
                "notes": "Do not generate vulnerability findings from an empty or unreachable response.",
            }
        if _detect_restricted_access(signals):
            return {
                "failure_category": "restricted_access",
                "root_cause": (
                    "Target platform requires additional access (login, subscription, VPN, "
                    "or lab activation) that the runner could not satisfy."
                ),
                "retryable": False,
                "suggested_fix": (
                    "Provide credentials, deploy the target lab, or run from an authorized "
                    "network before retrying."
                ),
                "alternative_profile": "",
                "notes": "Surface the access gap in the report instead of broadening scope.",
            }
        if "timeout" in message or "timed out" in message:
            return {
                "failure_category": "timeout",
                "root_cause": "The task timed out before the assigned profile could finish.",
                "retryable": True,
                "suggested_fix": "Increase timeout or narrow the target scope before retrying.",
                "alternative_profile": "",
                "notes": "Preserve current evidence and avoid escalating to a broader scanner automatically.",
            }
        if "exit_code=2" in message or "exit code 2" in message:
            return {
                "failure_category": "profile_compatibility",
                "root_cause": "The assigned profile appears incompatible with the current environment or target.",
                "retryable": False,
                "suggested_fix": "Check tool dependencies and profile prerequisites before retrying.",
                "alternative_profile": "",
                "notes": "Prefer reporting the compatibility gap over switching to ad-hoc shell commands.",
            }
        if "not configured" in message or "not installed" in message or "not found" in message:
            return {
                "failure_category": "environment",
                "root_cause": "A required tool or environment dependency is missing.",
                "retryable": False,
                "suggested_fix": "Install or configure the missing dependency and rerun the same controlled profile.",
                "alternative_profile": "",
                "notes": "Report this as an environment gap instead of broadening the workflow.",
            }
        if "approval" in message or "denied" in message or "policy" in message:
            return {
                "failure_category": "approval_or_policy",
                "root_cause": "Execution was blocked by an approval or policy gate.",
                "retryable": False,
                "suggested_fix": "Obtain the required approval or lower the requested risk level.",
                "alternative_profile": "",
                "notes": "Do not bypass the block with alternate tools.",
            }
        return {
            "failure_category": "execution",
            "root_cause": str(task.last_error or task.result_summary or "Execution failed without a structured root cause."),
            "retryable": bool(task.attempts <= task.max_retries and not task.requires_approval),
            "suggested_fix": "Review the runner output and task evidence before retrying the same profile.",
            "alternative_profile": "",
            "notes": "Preserve the evidence and let the reporter describe the coverage impact.",
        }

    def _append_failure_analysis_activity(
        self,
        campaign: SecurityCampaign,
        task: SecurityTask,
        *,
        summary: str,
        execution_mode: str,
        session_id: str = "",
        started_at: str = "",
        completed_at: str = "",
    ) -> None:
        activity_id = f"failure_act_{task.task_id}_{execution_mode}"
        if any(activity.activity_id == activity_id for activity in campaign.activities):
            return
        campaign.activities.append(
            AgentActivityRecord(
                activity_id=activity_id,
                agent_key=SECURITY_FAILURE_ANALYST_KEY,
                agent_name=SECURITY_FAILURE_ANALYST_KEY,
                task_id=task.task_id,
                action="reflected",
                summary=summary,
                started_at=started_at,
                completed_at=completed_at,
                execution_mode=execution_mode,
                tool_calls=["failure_analysis"],
                notes=session_id,
            )
        )

    def _append_unique_observation(self, task: SecurityTask, value: str) -> None:
        if value and value not in task.observations:
            task.observations.append(value)

    async def _wait_for_worker_sessions(
        self,
        child_session_ids: list[str],
        *,
        overall_timeout_seconds: float = 60.0,
    ) -> list[Any]:
        """Wait for worker child sessions to settle with a hard deadline.

        Used by the failure-analysis dispatch path. Failure analysts are
        expected to be short-lived (read evidence, return JSON), so this
        defaults to a 60s overall timeout. If a failure-analyst child
        session hangs (which has been observed in production), it is
        surfaced as ``timed_out`` so the campaign can still settle and
        produce the report — failure analysis is best-effort, NOT a gate
        on report delivery.
        """
        from src.schemas.session import SessionStatus

        pending = {session_id for session_id in child_session_ids if session_id}
        settled: dict[str, Any] = {}
        approval_wait_counts: dict[str, int] = {}
        max_approval_polls = 60  # ~12s at the 0.2s poll interval

        deadline: float | None = None
        if overall_timeout_seconds > 0:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + overall_timeout_seconds

        while pending:
            completed_ids: list[str] = []
            for session_id in list(pending):
                session = await self._session_store.get_session(session_id)
                if session is None:
                    completed_ids.append(session_id)
                    continue
                if session.status in {
                    SessionStatus.completed,
                    SessionStatus.failed,
                    SessionStatus.interrupted,
                }:
                    settled[session_id] = session
                    completed_ids.append(session_id)
                    continue
                if session.status == SessionStatus.waiting_approval:
                    approval_wait_counts[session_id] = approval_wait_counts.get(session_id, 0) + 1
                    if approval_wait_counts[session_id] >= max_approval_polls:
                        settled[session_id] = session
                        completed_ids.append(session_id)
            for session_id in completed_ids:
                pending.discard(session_id)
            if not pending:
                break
            if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                # Hard timeout. Surface whatever state the remaining
                # sessions are in so failure analysis falls back to the
                # local heuristic instead of blocking the entire campaign.
                for stuck_id in list(pending):
                    stuck_session = await self._session_store.get_session(stuck_id)
                    if stuck_session is not None:
                        settled[stuck_id] = stuck_session
                pending.clear()
                break
            await asyncio.sleep(0.2)
        return list(settled.values())

    def _extract_assistant_summary_from_messages(self, messages: list[Any]) -> str:
        for message in reversed(messages):
            if str(getattr(message, "role", "")) == "MessageRole.assistant":
                return str(getattr(message, "content", "") or "").strip()
            role_value = getattr(message, "role", "")
            if getattr(role_value, "value", "") == "assistant":
                return str(getattr(message, "content", "") or "").strip()
        return ""

    def _extract_message_excerpt(self, messages: list[Any]) -> str:
        for message in reversed(messages):
            content = str(getattr(message, "content", "") or "").strip()
            if content:
                return content
        return ""

    def _is_model_unavailable_failure(self, content: str) -> bool:
        """Detect signals that the LLM provider itself is unreachable.

        This previously matched bare words like ``authentication`` or
        ``unauthorized`` which are extremely common in security testing
        output (the *target* often has auth/authz issues). We now require a
        provider-side keyword to appear together with the failure phrase, or
        a clearly provider-scoped signal like ``invalid api key`` /
        ``model invocation failed`` to fire.
        """
        text = str(content or "").lower()
        if not text:
            return False
        provider_unambiguous = (
            "model invocation failed",
            "invalid api key",
            "insufficient balance",
            "payment required",
            "quota exceeded",
        )
        if any(token in text for token in provider_unambiguous):
            return True
        provider_context_tokens = (
            "model",
            "openai",
            "anthropic",
            "claude",
            "gpt-",
            "llm",
            "api key",
            "provider",
            "billing",
        )
        provider_failure_phrases = (
            "401",
            "402",
            "403 forbidden",
            "rate limit",
            "rate-limit",
            "unauthorized",
            "authentication failed",
        )
        if not any(token in text for token in provider_context_tokens):
            return False
        return any(phrase in text for phrase in provider_failure_phrases)

    def _parse_failure_analysis_response(self, content: str) -> dict[str, Any]:
        parsed = self._try_parse_json_object(content)
        if isinstance(parsed, dict):
            return {
                "failure_category": str(parsed.get("failure_category") or "execution"),
                "root_cause": str(parsed.get("root_cause") or parsed.get("notes") or "").strip(),
                "retryable": bool(parsed.get("retryable")),
                "suggested_fix": str(parsed.get("suggested_fix") or "").strip(),
                "alternative_profile": str(parsed.get("alternative_profile") or "").strip(),
                "notes": str(parsed.get("notes") or "").strip(),
            }
        return {
            "failure_category": "execution",
            "root_cause": str(content or "Failure analysis worker returned no structured content.").strip(),
            "retryable": False,
            "suggested_fix": "",
            "alternative_profile": "",
            "notes": "",
        }

    def _try_parse_json_object(self, content: str) -> dict[str, Any] | None:
        if not content:
            return None
        text = str(content).strip()
        candidates = [text]
        if "```json" in text:
            candidates.append(text.split("```json", 1)[1].split("```", 1)[0].strip())
        if "```" in text:
            candidates.append(text.split("```", 1)[1].rsplit("```", 1)[0].strip())
        for candidate in candidates:
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _session_iso(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _session_status_value(self, session: Any) -> str:
        if session is None:
            return "missing"
        status = getattr(session, "status", None)
        return str(getattr(status, "value", status) or "").strip().lower()

    def _build_request(self, arguments: dict[str, Any], context: Any) -> SecurityTestingRequestState:
        return self._request_interpreter.interpret(arguments, context)

    def _attach_runtime_context(self, state: SecurityTestingState, context: Any) -> None:
        state.session_id = str(getattr(context, "session_id", "") or state.session_id)
        state.trace_id = str(getattr(context, "trace_id", "") or state.trace_id)
        state.selected_agent = str(
            getattr(context, "selected_agent_key", "") or state.selected_agent or "security-testing-agent"
        )
        state.selected_tools = list(SECURITY_TESTING_TOOL_KEYS)
        bundle = getattr(context, "context_bundle", None)
        if isinstance(bundle, dict):
            bundle["mode_key"] = "security_testing"
            bundle["security_memory_scope"] = "session_only"
            if state.request.target_fingerprint:
                bundle["target_fingerprint"] = state.request.target_fingerprint
            if state.request.platform_label:
                bundle["platform_label"] = state.request.platform_label
            if state.campaign is not None:
                bundle["campaign_id"] = state.campaign.campaign_id
        if not state.context_refs:
            state.context_refs = self._build_context_refs(state)

    def _build_checkpoint_callback(
        self,
        state: SecurityTestingState,
        context: Any,
    ) -> Callable[[str, SecurityTask, list[SecurityTask]], None]:
        def checkpoint(event_type: str, task: SecurityTask, tasks: list[SecurityTask]) -> None:
            self._checkpoint_execution_state(
                state=state,
                context=context,
                event_type=event_type,
                task=task,
                tasks=tasks,
            )

        return checkpoint

    def _checkpoint_execution_state(
        self,
        *,
        state: SecurityTestingState,
        context: Any,
        event_type: str,
        task: SecurityTask | None = None,
        tasks: list[SecurityTask] | None = None,
        summary: str = "",
        worker_agent_key_override: str = "",
        execution_mode_override: str = "",
    ) -> None:
        if state.campaign and tasks is not None:
            state.campaign.tasks = list(tasks)
        task_list = list(tasks or (state.campaign.tasks if state.campaign else []))
        now = datetime.now(timezone.utc).isoformat()
        if task is not None:
            runner_key = self._tool_catalog.resolve_runner_for_family(task.tool_family)
            event = SecurityTaskEventRecord(
                event_id=f"{event_type}_{task.task_id}_{task.attempts}_{len(state.task_events) + 1}",
                event_type=event_type,
                task_id=task.task_id,
                task_name=task.name,
                command_profile=task.command_profile,
                tool_family=task.tool_family,
                target=task.target,
                status=task.status,
                phase=state.phase,
                attempts=task.attempts,
                worker_agent_key=worker_agent_key_override or task.worker_agent_key,
                worker_session_id=task.worker_session_id,
                execution_mode=execution_mode_override or task.worker_execution_mode,
                runner_key=runner_key,
                summary=summary or task.result_summary,
                error=task.last_error,
                at=now,
            )
            state.task_events.append(event)
            if len(state.task_events) > 200:
                state.task_events = state.task_events[-200:]

        state.execution_checkpoint = {
            "phase": state.phase,
            "campaign_id": state.campaign.campaign_id if state.campaign else "",
            "execution_strategy": state.execution_strategy,
            "last_event_type": event_type,
            "active_task_id": task.task_id if task is not None else "",
            "active_task_status": task.status if task is not None else "",
            "task_summary": self._task_status_summary(task_list),
            "event_count": len(state.task_events),
            "updated_at": now,
            "trace_id": state.trace_id,
        }
        logger.info(
            "security_execution_checkpoint %s",
            self._log_payload(
                context=context,
                campaign_id=state.execution_checkpoint["campaign_id"],
                phase=state.phase,
                event_type=event_type,
                task_id=task.task_id if task else "",
                task_status=task.status if task else "",
                command_profile=task.command_profile if task else "",
                worker_session_id=task.worker_session_id if task else "",
                task_summary=state.execution_checkpoint["task_summary"],
                error=task.last_error if task else "",
            ),
        )
        self._persist_state(state, context)

    def _log_payload(self, *, context: Any, **values: Any) -> str:
        """Render stable JSON log context without credentials or raw payloads."""
        payload = {
            "session_id": str(getattr(context, "session_id", "") or ""),
            "turn_id": str(getattr(context, "turn_id", "") or ""),
            "trace_id": str(getattr(context, "trace_id", "") or ""),
            **values,
        }
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    def _restore_state(self, context: Any) -> SecurityTestingState:
        session_id = str(getattr(context, "session_id", "") or "")
        cached = self._state_cache.get(session_id)
        if cached is not None:
            return cached.model_copy(deep=True)
        bundle = getattr(context, "context_bundle", None) or {}
        raw = bundle.get(STATE_METADATA_KEY)
        if isinstance(raw, dict):
            try:
                return SecurityTestingState.model_validate(raw)
            except Exception:
                pass
        return SecurityTestingState()

    def _persist_state(self, state: SecurityTestingState, context: Any) -> None:
        state.last_updated_at = datetime.now(timezone.utc).isoformat()
        bundle = getattr(context, "context_bundle", None)
        if isinstance(bundle, dict):
            bundle[STATE_METADATA_KEY] = state.model_dump(mode="json")
        session_id = str(getattr(context, "session_id", "") or "")
        if session_id:
            self._state_cache[session_id] = state.model_copy(deep=True)

    def _request_fingerprint(self, request: SecurityTestingRequestState) -> str:
        payload = {
            "objective": request.objective.strip(),
            "target_url": request.target_url.strip(),
            "target_host": request.target_host.strip(),
            "target_network": request.target_network.strip(),
            "scope_preference": request.scope_preference.strip(),
            "risk_tolerance": request.risk_tolerance.strip(),
            "focus_areas": sorted(request.focus_areas),
            "excluded_areas": sorted(request.excluded_areas),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]

    def _build_output(self, state: SecurityTestingState) -> dict[str, Any]:
        delivery_failed = state.delivery is not None and state.delivery.status == "failed"
        completed = state.phase in {PHASE_REPORT_READY, PHASE_EMAIL_DELIVERED}
        report_has_failures = bool(state.report and state.report.failed_tasks)
        output: dict[str, Any] = {
            "status": (
                "interrupted"
                if state.phase == PHASE_INTERRUPTED
                else "failed"
                if completed and state.campaign and state.campaign.settlement
                and state.campaign.settlement.status == "failed"
                else "partial"
                if delivery_failed or (completed and report_has_failures)
                else "completed"
                if completed
                else "failed"
                if state.phase == PHASE_FAILED
                else "partial"
            ),
            "phase": state.phase,
            "summary": self._build_summary(state),
        }
        if state.trace_id:
            output["trace_id"] = state.trace_id
        if state.selected_agent:
            output["selected_agent"] = state.selected_agent
        if state.selected_tools:
            output["selected_tools"] = list(state.selected_tools)
        if state.execution_strategy:
            output["execution_strategy"] = state.execution_strategy
        if state.context_refs:
            output["context_refs"] = list(state.context_refs)
        if state.targets:
            output["targets"] = [target.model_dump(mode="json") for target in state.targets]
        if state.campaign:
            output["campaign_id"] = state.campaign.campaign_id
            output["task_count"] = len(state.campaign.tasks)
            output["task_summary"] = self._task_summary(state.campaign.tasks)
            output["subtask_count"] = len(state.campaign.subtasks)
            output["subtask_summary"] = self._subtask_summary(state.campaign.subtasks)
        if state.report:
            output["report"] = state.report.model_dump(mode="json")
            output["report_markdown"] = state.report_markdown
            output["report_html"] = state.report_html
            if state.artifacts:
                output["artifacts"] = list(state.artifacts)
        if state.delivery:
            output["delivery"] = state.delivery.model_dump(mode="json")
        if state.verification_result:
            output["verification_result"] = dict(state.verification_result)
        if state.evaluation_result:
            output["evaluation_result"] = dict(state.evaluation_result)
        if state.errors:
            output["errors"] = list(state.errors)
        if state.execution_checkpoint:
            output["execution_checkpoint"] = dict(state.execution_checkpoint)
        if state.task_events:
            output["task_events"] = [event.model_dump(mode="json") for event in state.task_events]
        if state.notes:
            output["notes"] = list(state.notes)
        if state.error:
            output["error"] = state.error
        output[STATE_METADATA_KEY] = state.model_dump(mode="json")
        return output

    def _build_summary(self, state: SecurityTestingState) -> str:
        if state.phase == PHASE_INTERRUPTED:
            return (
                "Security testing was interrupted; completed evidence was preserved "
                "and no new workers will be dispatched."
            )
        if state.phase == PHASE_FAILED:
            return state.notes[-1] if state.notes else "Security testing mode encountered an error."
        if state.phase == PHASE_EMAIL_DELIVERED and state.report:
            recipient_count = state.delivery.recipient_count if state.delivery else 0
            if state.report.failed_tasks or state.previous_phase == PHASE_FAILED:
                return (
                    f"Security testing ended with failures for {state.report.target_summary or state.report.campaign_id[:8]}; "
                    f"report generated with {state.report.failed_tasks} failed task(s) and emailed to {recipient_count} recipient(s)."
                )
            return (
                f"Security testing completed for {state.report.target_summary}; "
                f"{state.report.total_findings} finding(s), "
                f"{state.report.completed_tasks}/{state.report.total_tasks} task(s) completed; "
                f"report emailed to {recipient_count} recipient(s)."
            )
        if state.phase == PHASE_REPORT_READY and state.report:
            if state.report.failed_tasks or state.previous_phase == PHASE_FAILED:
                return (
                    f"Security testing ended with failures for {state.report.target_summary or state.report.campaign_id[:8]}; "
                    f"report generated with {state.report.failed_tasks} failed task(s)."
                )
            return (
                f"Security testing completed for {state.report.target_summary}; "
                f"{state.report.total_findings} finding(s), "
                f"{state.report.completed_tasks}/{state.report.total_tasks} task(s) completed."
            )
        if state.phase == PHASE_ATTACK_PLAN_READY and state.campaign:
            return f"Security campaign is ready with {len(state.campaign.tasks)} task(s)."
        return f"Security testing mode is in phase: {state.phase}."

    def _is_interrupt_requested(self, context: Any) -> bool:
        if self._runtime_control is None:
            return False
        session_id = str(getattr(context, "session_id", "") or "")
        return bool(session_id and self._runtime_control.is_interrupt_requested(session_id))

    def _can_use_subagent_execution(self, context: Any) -> bool:
        if self._coordinator_runtime_service is None or self._session_store is None:
            return False
        return bool(getattr(context, "session_id", "") and getattr(context, "turn_id", ""))

    def _build_dispatch_context(self, context: Any) -> dict[str, Any]:
        bundle = dict(getattr(context, "context_bundle", {}) or {})
        bundle["mode_key"] = "security_testing"
        bundle["security_memory_scope"] = "session_only"
        return {
            "session_id": str(getattr(context, "session_id", "") or ""),
            "turn_id": str(getattr(context, "turn_id", "") or ""),
            "trace_id": str(getattr(context, "trace_id", "") or ""),
            "selected_agent_key": str(getattr(context, "selected_agent_key", "") or ""),
            "selected_model_key": str(getattr(context, "selected_model_key", "") or ""),
            "context_bundle": bundle,
        }

    def _build_failure_analysis_dispatch_context(self, context: Any) -> dict[str, Any]:
        """Build a minimal dispatch context for failure-analyst subagents.

        Failure-analyst sessions are read-only, short-lived, and should
        NOT inherit the parent campaign's full context_bundle (which
        contains the original scan tasks' worker_dispatches and other
        state). Inheriting it has caused failure-analyst sessions to
        receive backfill notifications for the original scan tasks and to
        be considered part of the same campaign dispatch loop.

        We keep only the fields the failure-analyst genuinely needs and
        nothing else.
        """
        parent_bundle = getattr(context, "context_bundle", None) or {}
        if not isinstance(parent_bundle, dict):
            parent_bundle = {}
        scoped_bundle: dict[str, Any] = {
            "mode_key": "security_testing",
            "security_memory_scope": "session_only",
            "dispatch_role": "security_failure_analysis",
        }
        # Pass through identification fields only — never worker_dispatches,
        # never pending_followup_workers, never completion_worker metadata.
        for key in ("target_fingerprint", "platform_label", "campaign_id"):
            value = parent_bundle.get(key)
            if value:
                scoped_bundle[key] = value
        return {
            "session_id": str(getattr(context, "session_id", "") or ""),
            "turn_id": str(getattr(context, "turn_id", "") or ""),
            "trace_id": str(getattr(context, "trace_id", "") or ""),
            "selected_agent_key": str(getattr(context, "selected_agent_key", "") or ""),
            "selected_model_key": str(getattr(context, "selected_model_key", "") or ""),
            "context_bundle": scoped_bundle,
        }

    def _build_analysis_dispatch_context(self, context: Any) -> dict[str, Any]:
        """Keep pre-planning specialists isolated from executable worker state."""
        parent_bundle = getattr(context, "context_bundle", None) or {}
        if not isinstance(parent_bundle, dict):
            parent_bundle = {}
        scoped_bundle: dict[str, Any] = {
            "mode_key": "security_testing",
            "security_memory_scope": "session_only",
            "dispatch_role": "security_scenario_analysis",
        }
        for key in ("target_fingerprint", "platform_label", "campaign_id"):
            value = parent_bundle.get(key)
            if value:
                scoped_bundle[key] = value
        return {
            "session_id": str(getattr(context, "session_id", "") or ""),
            "turn_id": str(getattr(context, "turn_id", "") or ""),
            "trace_id": str(getattr(context, "trace_id", "") or ""),
            "selected_agent_key": str(getattr(context, "selected_agent_key", "") or ""),
            "selected_model_key": str(getattr(context, "selected_model_key", "") or ""),
            "context_bundle": scoped_bundle,
        }

    def _task_summary(self, tasks: list[SecurityTask]) -> dict[str, int]:
        return {
            "total": len(tasks),
            "completed": sum(1 for task in tasks if task.status == TASK_COMPLETED),
            "failed": sum(1 for task in tasks if task.status == TASK_FAILED),
            "skipped": sum(1 for task in tasks if task.status == TASK_SKIPPED),
        }

    def _task_status_summary(self, tasks: list[SecurityTask]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        counts["total"] = len(tasks)
        return counts

    def _subtask_summary(self, subtasks: list[SecuritySubtask]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for subtask in subtasks:
            status = subtask.status or "unknown"
            counts[status] = counts.get(status, 0) + 1
        counts["total"] = len(subtasks)
        return counts

    def _build_context_refs(self, state: SecurityTestingState) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for target in state.targets:
            refs.append(
                {
                    "type": "security_target",
                    "source": "user_request",
                    "target_id": target.target_id,
                        "target_type": target.target_type,
                        "value": target.value,
                        "label": target.label,
                        "fingerprint": target.fingerprint,
                        "protocol": target.protocol,
                        "port": target.port,
                    }
            )
        if state.campaign:
            for asset in state.campaign.assets:
                refs.append(
                    {
                        "type": "security_asset",
                        "source": "asset_discovery",
                        "asset_id": asset.asset_id,
                        "asset_type": asset.asset_type,
                        "address": asset.address,
                        "hostname": asset.hostname,
                        "port": asset.port,
                        "protocol": asset.protocol,
                    }
                )
        return refs

    def _artifact_metadata(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project artifact dicts into the shape expected by the tool layer.

        The tool job service persists artifacts via
        :func:`ToolJobService._save_artifacts`, which reads either ``content``
        (inline text) or ``path`` (file on disk). Earlier this helper stripped
        both fields, which silently caused every security report artifact to
        be dropped on the floor — and made
        ``GET /api/v1/sessions/{sid}/artifacts`` always return ``[]``.

        We now pass ``content`` (and ``path`` when present) through. ``label``
        defaults to ``filename`` if missing so list views always have a
        meaningful name.
        """
        metadata: list[dict[str, Any]] = []
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename")
            artifact: dict[str, Any] = {
                "type": item.get("type"),
                "filename": filename,
                "content_type": item.get("content_type"),
                "label": item.get("label") or filename or item.get("type"),
            }
            content = item.get("content")
            if content not in (None, ""):
                artifact["content"] = content
            path = item.get("path")
            if path:
                artifact["path"] = path
            if item.get("task_id"):
                artifact["task_id"] = item.get("task_id")
            metadata.append(artifact)
        return metadata

    def _prepare_report_artifacts(
        self,
        artifacts: list[dict[str, Any]],
        *,
        state: SecurityTestingState,
        context: Any,
    ) -> list[dict[str, Any]]:
        """Persist generated reports as normal file artifacts.

        The shared ToolRuntimeService already sends file-backed artifacts
        through ArtifactStorageService, which uploads them to the configured
        backend such as RustFS.  Security mode therefore only materializes its
        report content to the ordinary artifact directory and keeps storage
        concerns in the shared artifact layer.
        """
        prepared: list[dict[str, Any]] = []
        report_dir = self._report_artifact_dir(state, context)
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            artifact = dict(item)
            content = artifact.get("content")
            if content not in (None, "") and not artifact.get("path"):
                filename = str(artifact.get("filename") or artifact.get("label") or artifact.get("type") or "report")
                safe_name = self._safe_artifact_filename(filename)
                report_dir.mkdir(parents=True, exist_ok=True)
                path = report_dir / safe_name
                path.write_text(str(content), encoding="utf-8")
                artifact["path"] = str(path)
            prepared.append(artifact)
        return prepared

    def _report_artifact_dir(self, state: SecurityTestingState, context: Any) -> Path:
        root = Path(__file__).resolve().parents[2] / "data" / "artifacts"
        session_id = str(getattr(context, "session_id", "") or state.session_id or "session")
        turn_id = str(getattr(context, "turn_id", "") or "turn")
        campaign_id = state.campaign.campaign_id if state.campaign is not None else "campaign"
        return (
            root
            / self._safe_artifact_segment(session_id)
            / self._safe_artifact_segment(turn_id)
            / "security_reports"
            / self._safe_artifact_segment(campaign_id)[:32]
        )

    def _safe_artifact_filename(self, value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "report")).strip("._")
        return normalized or "report"

    def _safe_artifact_segment(self, value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "item")).strip("._")
        return normalized or "item"

    def _build_error_records(
        self,
        evaluation_result: dict[str, Any],
        tasks: list[SecurityTask],
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        classifications = evaluation_result.get("failure_classifications")
        if isinstance(classifications, list):
            for item in classifications:
                if not isinstance(item, dict):
                    continue
                errors.append(
                    {
                        "task_id": item.get("task_id"),
                        "category": item.get("category"),
                        "severity": item.get("severity"),
                        "message": item.get("description"),
                        "command_profile": item.get("command_profile"),
                        "target": item.get("target"),
                        "is_transient": item.get("is_transient"),
                    }
                )
        if errors:
            return errors
        for task in tasks:
            if not task.last_error:
                continue
            errors.append(
                {
                    "task_id": task.task_id,
                    "category": "execution",
                    "severity": "medium",
                    "message": task.last_error,
                    "command_profile": task.command_profile,
                    "target": task.target,
                    "is_transient": False,
                }
            )
        return errors

    def _to_string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [item.strip() for item in value.split(",") if item.strip()]
        return []

    def _unique_text_values(self, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    def _p4_output_summary(self, bootstrap: ToolBootstrapState) -> str:
        return self._truncate_p4_text(
            "\n".join(
                value
                for value in (
                    bootstrap.stdout,
                    bootstrap.stderr,
                    bootstrap.failure_reason,
                )
                if str(value or "").strip()
            ),
            limit=3000,
        )

    @staticmethod
    def _truncate_p4_text(value: str, *, limit: int) -> str:
        text = str(value or "").strip()
        return text if len(text) <= limit else f"{text[:limit - 3]}..."


__all__ = ["SecurityTestingModeRuntime"]
