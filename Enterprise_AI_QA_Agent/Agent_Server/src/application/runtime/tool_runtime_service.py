from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.documents.api_docs_service import ApiDocsService
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService
from src.application.exploration.ui_graph_store import UIGraphStore
from src.application.mcp.host.connection_manager import McpConnectionManager
from src.application.mcp.host.namespace import decode as decode_mcp_tool_key
from src.application.mcp.host.namespace import is_mcp_tool_key
from src.application.security.execution_environment_service import SecurityExecutionEnvironmentService
from src.application.security.tool_bootstrap_service import ToolBootstrapService
from src.application.security.output_safety_policy import OutputSafetyPolicy
from src.application.security.resource_access_policy import ResourceAccessPolicy
from src.modes.code_review_mode import build_code_review_campaign
from src.modes.code_review_mode.governance import CodeGovernanceRuntime
from src.modes.code_review_mode.project_source import (
    DEFAULT_IGNORED_NAMES,
    ignored_names_from_arguments,
    is_ignored_project_path,
    normalize_project_source,
    project_source_root,
    resolve_local_project_file,
    resolve_local_project_root,
)
from src.application.reporting.report_template_service import ReportTemplateService
from src.application.resources.session_resource_service import SessionResourceService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.testing.ui_exploration_service import UIExplorationService
from src.core.config import Settings
from src.application.mail import MailService
from src.application.mail.contracts import MailSendRequest
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.modes.ui_automation_mode.runtime import UIAutomationModeRuntime
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.modes.compatibility_testing_mode.runtime import CompatibilityTestingModeRuntime
from src.modes.security_testing_mode.runtime import SecurityTestingModeRuntime
from src.runtime.store import SessionStore
from src.schemas.agent import ToolDescriptor
from src.schemas.model_config import ModelConfigRecord
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord
from src.schemas.session_resource import SessionResourceKind


logger = logging.getLogger(__name__)

CODE_REVIEW_RESULT_CATEGORY_LABELS = {
    "serious_issue": "严重问题",
    "critical": "严重问题",
    "严重问题": "严重问题",
    "defect": "缺陷",
    "缺陷": "缺陷",
    "risk": "隐患",
    "隐患": "隐患",
    "feasible": "可行",
    "可行": "可行",
    "excellent": "优秀",
    "优秀": "优秀",
}

@dataclass
class ToolExecutionContext:
    session_id: str
    turn_id: str
    trace_id: str
    user_message: str
    normalized_input: str
    context_bundle: dict[str, Any]
    selected_agent_key: str = ""
    selected_model_key: str = ""
    tool_job_id: str = ""
    tool_key: str = ""
    call_id: str = ""


class ToolRuntimeService:
    def __init__(
        self,
        request_timeout_seconds: int = 20,
        settings: Settings | None = None,
        mcp_runtime_service: MCPRuntimeService | None = None,
        memory_runtime_service: MemoryRuntimeService | None = None,
        tool_job_service: ToolJobService | None = None,
        session_store: SessionStore | None = None,
        transcript_hygiene_service: TranscriptHygieneService | None = None,
        artifact_storage_service: ArtifactStorageService | None = None,
        api_docs_service: ApiDocsService | None = None,
        project_service=None,
        knowledge_graph_service: KnowledgeGraphService | None = None,
        coordinator_runtime_service=None,
        mcp_connection_manager: McpConnectionManager | None = None,
        compatibility_runner_service=None,
        session_resource_service: SessionResourceService | None = None,
        runtime_control=None,
        security_bug_service=None,
        tool_job_heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self._request_timeout_seconds = request_timeout_seconds
        self._settings = settings
        self._docs_dir = Path(__file__).resolve().parents[3] / "docs"
        self._workspace_root = Path.cwd()
        self._mcp_runtime_service = mcp_runtime_service
        self._memory_runtime_service = memory_runtime_service
        self._tool_job_service = tool_job_service
        self._tool_job_heartbeat_interval_seconds = tool_job_heartbeat_interval_seconds or 30.0
        self._artifact_storage_service = artifact_storage_service
        self._api_docs_service = api_docs_service
        self._session_store = session_store
        self._transcript_hygiene_service = transcript_hygiene_service or TranscriptHygieneService()
        self._output_safety_policy = OutputSafetyPolicy()
        self._coordinator_runtime_service = coordinator_runtime_service
        self._mcp_connection_manager = mcp_connection_manager
        self._compatibility_runner_service = compatibility_runner_service
        self._session_resource_service = session_resource_service
        self._model_registry = None
        self._email_config_store = MySQLEmailConfigStore(settings) if settings is not None else None
        self._mail_service = MailService(self._email_config_store)
        self._report_template_service = ReportTemplateService()
        self._security_execution_environment = SecurityExecutionEnvironmentService(
            settings=settings,
            workspace_root=self._workspace_root,
        )
        self._security_tool_bootstrap_service = ToolBootstrapService(
            settings=settings,
            workspace_root=self._workspace_root,
        )
        self._code_governance_runtime = CodeGovernanceRuntime(workspace_root=self._workspace_root)
        self._ui_graph_store = UIGraphStore(settings) if settings is not None else None
        self._ui_exploration_service = (
            UIExplorationService(
                settings=settings,
                mcp_runtime_service=mcp_runtime_service,
                memory_runtime_service=memory_runtime_service,
                ui_graph_store=self._ui_graph_store,
            )
            if settings is not None
            else None
        )
        self._ui_automation_mode_runtime = UIAutomationModeRuntime(
            memory_runtime_service=memory_runtime_service,
            ui_exploration_service=self._ui_exploration_service,
        )
        self._api_testing_mode_runtime = ApiTestingModeRuntime(
            api_docs_service=api_docs_service,
            settings=settings,
            coordinator_runtime_service=coordinator_runtime_service,
            session_store=session_store,
        )
        self._compatibility_testing_mode_runtime = CompatibilityTestingModeRuntime(
            settings=settings,
            coordinator_runtime_service=coordinator_runtime_service,
            session_store=session_store,
            memory_runtime_service=memory_runtime_service,
            runner_service=compatibility_runner_service,
            mode_call_bridge_enabled=True,
        )
        self._smoke_testing_mode_runtime = None
        if settings:
            from src.modes.smoke_testing_mode.runtime import SmokeTestingModeRuntime
            self._smoke_testing_mode_runtime = SmokeTestingModeRuntime(
                settings=settings,
                api_docs_service=api_docs_service,
                memory_runtime_service=memory_runtime_service,
                artifact_storage_service=artifact_storage_service,
                project_service=project_service,
                knowledge_graph_service=knowledge_graph_service,
            )
        self._security_testing_mode_runtime = SecurityTestingModeRuntime(
            settings=settings,
            coordinator_runtime_service=coordinator_runtime_service,
            session_store=session_store,
            memory_runtime_service=memory_runtime_service,
            report_template_service=self._report_template_service,
            runner_executor=self._execute_security_runner,
            report_delivery_executor=self._deliver_security_testing_report,
            runtime_control=runtime_control,
            security_bug_service=security_bug_service,
            execution_environment_service=self._security_execution_environment,
        )
        self._performance_testing_mode_runtime = None
        if settings:
            from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime
            self._performance_testing_mode_runtime = PerformanceTestingModeRuntime(
                settings=settings,
                coordinator_runtime_service=coordinator_runtime_service,
                session_store=session_store,
                memory_runtime_service=memory_runtime_service,
                api_docs_service=api_docs_service,
            )
        self._handlers = {
            "workflow-router": self._run_workflow_router,
            "subagent-dispatch": self._run_subagent_dispatch,
            "knowledge-rag": self._run_knowledge_rag,
            "web_search": self._run_web_search,
            "api-docs-library": self._run_api_docs_library,
            "attachment-reader": self._run_attachment_reader,
            "session-history": self._run_session_history,
            "session-timeline": self._run_session_timeline,
            "observation-search": self._run_observation_search,
            "mcp-bridge": self._run_mcp_bridge,
            "test-case-generator": self._run_test_case_generator,
            "test-case-xlsx-exporter": self._run_test_case_xlsx_exporter,
            "ui-page-explorer": self._run_ui_page_explorer,
            "dom-inspector": self._run_dom_inspector,
            "browser-automation": self._run_browser_automation,
            "browser-control": self._run_browser_control,
            "api-tester": self._run_api_tester,
            "cli-executor": self._run_cli_executor,
            "file-artifact-manager": self._run_file_artifact_manager,
            "report-writer": self._run_report_writer,
            "project-source-loader": self._run_project_source_loader,
            "project-tree-scanner": self._run_project_tree_scanner,
            "project-file-reader": self._run_project_file_reader,
            "project-diff-reader": self._run_project_diff_reader,
            "code-governance-runner": self._run_code_governance_runner,
            "code-review-orchestrator": self._run_code_review_orchestrator,
            "ui-automation-runner": self._run_ui_automation_runner,
            "api-test-runner": self._run_api_test_runner,
            "security-scan-runner": self._run_security_scan_runner,
            "security-tool-bootstrap": self._run_security_tool_bootstrap,
            "network-recon-runner": self._run_network_recon_runner,
            "web-scan-runner": self._run_web_scan_runner,
            "service-audit-runner": self._run_service_audit_runner,
            "credential-attack-runner": self._run_credential_attack_runner,
            "traffic-analysis-runner": self._run_traffic_analysis_runner,
            "exploit-workbench-runner": self._run_exploit_workbench_runner,
            "performance-test-runner": self._run_performance_test_runner,
            "perf-container-manager": self._run_perf_container_manager,
            "api-docs-ingest": self._run_api_docs_ingest,
            "perf-engine-select": self._run_perf_engine_select,
            "http-probe-runner": self._run_http_probe_runner,
            "mock-target-runner": self._run_mock_target_runner,
            "performance-engine-runner": self._run_performance_engine_runner,
            "perf-plan-compiler": self._run_perf_plan_compiler,
            "perf-result-analyzer": self._run_perf_result_analyzer,
            "compatibility-test-runner": self._run_compatibility_test_runner,
            "smoke-suite-runner": self._run_smoke_suite_runner,
            "mail-status": self._run_mail_status,
            "mail-send": self._run_mail_send,
            "mail-confirm": self._run_mail_confirm,
            "mail-list": self._run_mail_list,
            "mail-read": self._run_mail_read,
            "mail-search": self._run_mail_search,
            "mail-reply": self._run_mail_reply,
            "mail-forward": self._run_mail_forward,
            "mail-trash": self._run_mail_trash,
            "mail-download-attachment": self._run_mail_download_attachment,
        }

    def set_coordinator_runtime_service(self, coordinator_runtime_service) -> None:
        self._coordinator_runtime_service = coordinator_runtime_service
        self._api_testing_mode_runtime.set_coordinator_runtime_service(coordinator_runtime_service)
        self._compatibility_testing_mode_runtime.set_coordinator_runtime_service(coordinator_runtime_service)
        self._security_testing_mode_runtime.set_coordinator_runtime_service(coordinator_runtime_service)
        if self._performance_testing_mode_runtime is not None:
            self._performance_testing_mode_runtime.set_coordinator_runtime_service(coordinator_runtime_service)

    def set_model_registry(self, model_registry) -> None:
        self._model_registry = model_registry

    def set_memory_runtime_service(self, memory_runtime_service: MemoryRuntimeService) -> None:
        self._memory_runtime_service = memory_runtime_service
        self._security_testing_mode_runtime.set_memory_runtime_service(memory_runtime_service)
        self._compatibility_testing_mode_runtime.set_memory_runtime_service(memory_runtime_service)
        self._ui_automation_mode_runtime.set_memory_runtime_service(memory_runtime_service)
        if self._smoke_testing_mode_runtime is not None:
            self._smoke_testing_mode_runtime.set_memory_runtime_service(memory_runtime_service)
        if self._performance_testing_mode_runtime is not None:
            self._performance_testing_mode_runtime.set_memory_runtime_service(memory_runtime_service)
        if self._ui_exploration_service is not None:
            self._ui_exploration_service._memory_runtime_service = memory_runtime_service

    def set_tool_job_service(self, tool_job_service: ToolJobService) -> None:
        self._tool_job_service = tool_job_service

    def set_session_store(self, session_store: SessionStore) -> None:
        self._session_store = session_store
        self._api_testing_mode_runtime.set_session_store(session_store)
        self._compatibility_testing_mode_runtime.set_session_store(session_store)
        self._security_testing_mode_runtime.set_session_store(session_store)
        if self._performance_testing_mode_runtime is not None:
            self._performance_testing_mode_runtime.set_session_store(session_store)

    def set_mcp_connection_manager(self, mcp_connection_manager: McpConnectionManager) -> None:
        self._mcp_connection_manager = mcp_connection_manager

    def has_handler(self, tool_key: str) -> bool:
        return tool_key in self._handlers

    def prepare_security_tool_bootstrap_arguments(
        self,
        *,
        campaign_id: str,
        target_allowlist: list[str],
        profile_key: str,
        tool_name: str,
        requested_version: str = "",
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """Build P4's server-owned approval payload without executing it."""
        if not self._security_tool_bootstrap_service.is_enabled():
            return None, "bootstrap_disabled"
        plan = self._security_tool_bootstrap_service.resolve_plan(
            profile_key=profile_key,
            tool_name=tool_name,
        )
        if plan is None:
            return None, "package_not_allowlisted"
        return (
            self._security_tool_bootstrap_service.build_approval_arguments(
                campaign_id=campaign_id,
                target_allowlist=target_allowlist,
                profile_key=profile_key,
                plan=plan,
                requested_version=requested_version,
                timeout_seconds=float(timeout_seconds or 300),
            ),
            "",
        )

    def record_security_tool_bootstrap_campaign(
        self,
        *,
        campaign_id: str,
        target_allowlist: list[str],
        manifest: dict[str, Any],
        tool_summary: str,
        tool_status: str,
        context: ToolExecutionContext,
    ):
        """Project a completed dedicated P4 lifecycle into its security Campaign."""
        return self._security_testing_mode_runtime.record_external_tool_bootstrap(
            campaign_id=campaign_id,
            target_allowlist=target_allowlist,
            manifest=manifest,
            tool_summary=tool_summary,
            tool_status=tool_status,
            context=context,
        )

    async def execute(
        self,
        tool: ToolDescriptor,
        call: ModelToolCall,
        context: ToolExecutionContext,
    ) -> ToolExecutionRecord:
        started_at = datetime.utcnow()
        job = None
        job_heartbeat_stop: asyncio.Event | None = None
        job_heartbeat_task: asyncio.Task | None = None
        handler = self._handlers.get(tool.key)
        if handler is None and is_mcp_tool_key(tool.key):
            handler = self._handlers.get("mcp-bridge")
        if handler is None:
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="failed",
                summary=f"No runtime handler is registered for tool '{tool.key}'.",
                trace_id=context.trace_id,
                input=call.arguments,
                output={},
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        try:
            job_context = context
            if not job_context.tool_key or not job_context.call_id:
                job_context = ToolExecutionContext(
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    trace_id=context.trace_id,
                    user_message=context.user_message,
                    normalized_input=context.normalized_input,
                    context_bundle=context.context_bundle,
                    selected_agent_key=context.selected_agent_key,
                    selected_model_key=context.selected_model_key,
                    tool_job_id=context.tool_job_id,
                    tool_key=tool.key,
                    call_id=call.id,
                )
            if self._tool_job_service is not None:
                if context.tool_job_id:
                    job = await self._tool_job_service.get_job(context.tool_job_id)
                if job is None:
                    job = await self._tool_job_service.create_job(
                        tool=tool,
                        call_id=call.id,
                        session_id=context.session_id,
                        turn_id=context.turn_id,
                        trace_id=context.trace_id,
                        input_payload=call.arguments,
                        metadata={
                            "selected_agent_key": context.selected_agent_key,
                            "selected_model_key": context.selected_model_key,
                        },
                    )
                await self._tool_job_service.mark_running(job)
                job_heartbeat_stop = asyncio.Event()
                job_heartbeat_task = asyncio.create_task(
                    self._keep_tool_job_alive(
                        job_id=job.id,
                        stop_event=job_heartbeat_stop,
                    )
                )
                job_context = ToolExecutionContext(
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    trace_id=context.trace_id,
                    user_message=context.user_message,
                    normalized_input=context.normalized_input,
                    context_bundle=context.context_bundle,
                    selected_agent_key=context.selected_agent_key,
                    selected_model_key=context.selected_model_key,
                    tool_job_id=job.id,
                    tool_key=tool.key,
                    call_id=call.id,
                )

            raw_result = await handler(call.arguments, job_context)
            result = self._normalize_result(tool, raw_result, context=job_context)
            if self._artifact_storage_service is not None:
                result = await self._artifact_storage_service.store_output_artifacts(
                    result,
                    session_id=job_context.session_id,
                    turn_id=job_context.turn_id,
                    tool_key=tool.key,
                )
            result, _, _ = self._output_safety_policy.sanitize_tool_output(result)
            resolved_status = self._resolve_result_status(result)
            summary = str(result.get("summary", f"Tool '{tool.key}' completed."))
            if job is not None and self._tool_job_service is not None:
                if resolved_status == "failed":
                    await self._tool_job_service.mark_failed(
                        job.id,
                        summary=summary,
                        error_message=str(result.get("error") or summary),
                        output_payload=result,
                        artifacts=result.get("artifacts", []) if isinstance(result, dict) else [],
                    )
                elif resolved_status == "partial":
                    await self._tool_job_service.mark_partial(
                        job.id,
                        summary=summary,
                        output_payload=result,
                        artifacts=result.get("artifacts", []) if isinstance(result, dict) else [],
                    )
                elif resolved_status == "waiting_approval":
                    await self._tool_job_service.mark_waiting_approval(
                        job.id,
                        summary=summary,
                        metadata={"output_payload": result},
                    )
                elif resolved_status == "denied":
                    await self._tool_job_service.mark_denied(
                        job.id,
                        summary=summary,
                        output_payload=result,
                    )
                else:
                    await self._tool_job_service.mark_completed(
                        job.id,
                        summary=summary,
                        output_payload=result,
                        artifacts=result.get("artifacts", []) if isinstance(result, dict) else [],
                    )
            record_output = self._compact_tool_output_for_model(tool.key, result)
            if job is not None and self._tool_job_service is not None and resolved_status in {
                "completed",
                "partial",
                "failed",
            }:
                saved_artifacts = await self._tool_job_service.list_artifacts(tool_job_id=job.id)
                downloads = [
                    {
                        "artifact_id": artifact.id,
                        "label": artifact.label or artifact.path,
                        "artifact_type": artifact.artifact_type,
                        "url": f"/api/v1/sessions/{context.session_id}/artifacts/{artifact.id}/content",
                    }
                    for artifact in saved_artifacts
                ]
                if downloads:
                    record_output["download_urls"] = downloads
                    record_output["download_markdown"] = "\n".join(
                        f"[点击下载 {item['label']}]({item['url']})" for item in downloads
                    )
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status=resolved_status,
                summary=summary,
                trace_id=str(result.get("trace_id") or context.trace_id or ""),
                job_id=job.id if job is not None else None,
                input=call.arguments,
                output=record_output,
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        except Exception as exc:
            sanitized_error, _ = self._output_safety_policy.sanitize_text(str(exc))
            if job is not None and self._tool_job_service is not None:
                await self._tool_job_service.mark_failed(
                    job.id,
                    summary=f"Tool '{tool.key}' failed.",
                    error_message=sanitized_error,
                    output_payload={"error": sanitized_error},
                )
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="failed",
                summary=f"Tool '{tool.key}' failed: {sanitized_error}",
                trace_id=context.trace_id,
                job_id=job.id if job is not None else None,
                input=call.arguments,
                output={"error": sanitized_error},
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        finally:
            if job_heartbeat_stop is not None and job_heartbeat_task is not None:
                job_heartbeat_stop.set()
                job_heartbeat_task.cancel()
                try:
                    await job_heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _keep_tool_job_alive(
        self,
        *,
        job_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._tool_job_heartbeat_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                return
            try:
                heartbeat = await self._tool_job_service.heartbeat(job_id)
                if heartbeat is None:
                    raise RuntimeError(f"ToolJob disappeared during execution: {job_id}")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "tool_job_heartbeat_failed",
                    extra={"tool_job_id": job_id},
                )

    def _resolve_result_status(self, result: dict[str, Any]) -> str:
        explicit_status = str(result.get("status") or "").strip().lower()
        if explicit_status == "interrupted":
            return "partial"
        if explicit_status in {"completed", "partial", "failed", "waiting_approval", "denied"}:
            return explicit_status
        if result.get("ok") is False:
            workers = result.get("workers")
            if isinstance(workers, list):
                running_count = sum(1 for item in workers if isinstance(item, dict) and item.get("status") == "running")
                failed_count = sum(1 for item in workers if isinstance(item, dict) and item.get("status") == "failed")
                if running_count > 0 and failed_count > 0:
                    return "partial"
            return "failed"
        return "completed"

    async def _run_mcp_bridge(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._mcp_connection_manager is None:
            return {
                "status": "failed",
                "ok": False,
                "summary": "MCP Host connection manager is not configured.",
                "error": "mcp_connection_manager_not_configured",
            }
        try:
            server_key, tool_name = decode_mcp_tool_key(context.tool_key)
        except ValueError as exc:
            return {
                "status": "failed",
                "ok": False,
                "summary": str(exc),
                "error": "invalid_mcp_tool_key",
            }
        result = await self._mcp_connection_manager.call_tool(server_key, tool_name, arguments)
        return {
            "status": "completed" if result.ok else "failed",
            "ok": result.ok,
            "summary": result.summary,
            "server_key": server_key,
            "tool_name": tool_name,
            "output": result.payload,
            "error": result.error,
        }

    async def _run_knowledge_rag(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or context.normalized_input).strip()
        top_k = int(arguments.get("top_k") or 3)
        if not query:
            return {
                "summary": "No query was provided for knowledge retrieval.",
                "chunks": [],
            }

        memory_matches: list[dict[str, Any]] = []
        if self._memory_runtime_service is not None:
            memory_result = await self._memory_runtime_service.retrieve_for_turn(
                session_id=context.session_id,
                trace_id=context.trace_id,
                query=query,
                context=context.context_bundle,
            )
            memory_matches = [
                {
                    "source": item.source or (self._memory_runtime_service.backend if self._memory_runtime_service is not None else "memory"),
                    "score": item.score or 0.0,
                    "excerpt": item.summary or item.content,
                    "kind": item.kind,
                }
                for item in memory_result.hits
            ]

        tokens = [token.lower() for token in re.split(r"\W+", query) if token.strip()]
        matches: list[dict[str, Any]] = []

        if self._docs_dir.exists():
            for file_path in sorted(self._docs_dir.glob("*.md")):
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                lowered = text.lower()
                filename_score = sum(file_path.name.lower().count(token) for token in tokens)
                score = (sum(lowered.count(token) for token in tokens) + filename_score) or 0
                if score <= 0:
                    continue
                excerpt = _build_excerpt(text, tokens)
                matches.append(
                    {
                        "source": file_path.name,
                        "score": score,
                        "excerpt": excerpt,
                    }
                )

        matches.sort(key=lambda item: item["score"], reverse=True)
        selected = [*memory_matches, *matches][:top_k]
        return {
            "summary": f"Retrieved {len(selected)} knowledge chunks for query '{query}'.",
            "chunks": selected,
            "query": query,
        }

    async def _run_web_search(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or context.normalized_input or context.user_message or "").strip()
        limit = _clamp_int(arguments.get("limit"), default=5, minimum=1, maximum=10)
        time_range = str(arguments.get("time_range") or "").strip()
        if not query:
            return {
                "status": "failed",
                "ok": False,
                "summary": "Web Search requires a non-empty query.",
                "error": "missing_query",
                "provider": "anysearch",
                "results": [],
            }

        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if time_range:
            payload["time_range"] = time_range
        settings = self._settings or Settings()
        api_key = settings.model.anysearch_api_key.strip()
        if not api_key:
            return {
                "status": "failed",
                "ok": False,
                "summary": "AnySearch API key is not configured.",
                "error": "missing_anysearch_api_key",
                "provider": "anysearch",
                "query": query,
                "results": [],
            }

        try:
            async with httpx.AsyncClient(
                base_url=settings.model.anysearch_api_base_url.strip() or "https://api.anysearch.com",
                timeout=min(max(self._request_timeout_seconds, 5), 30),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Enterprise-AI-QA-Agent/1.0",
                },
            ) as client:
                response = await client.post("/v1/search", json=payload)
        except httpx.TimeoutException:
            return {
                "status": "failed",
                "ok": False,
                "summary": "AnySearch request timed out.",
                "error": "anysearch_timeout",
                "provider": "anysearch",
                "query": query,
                "results": [],
            }
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"AnySearch request failed: {exc.__class__.__name__}.",
                "error": "anysearch_request_failed",
                "provider": "anysearch",
                "query": query,
                "results": [],
            }

        request_id = response.headers.get("x-request-id", "")
        try:
            response_payload = response.json()
        except ValueError:
            response_payload = {"message": response.text[:500]}

        if response.status_code >= 400:
            error_message = self._extract_anysearch_error(response_payload) or f"HTTP {response.status_code}"
            if isinstance(response_payload, dict):
                request_id = request_id or str(response_payload.get("request_id") or "")
            return {
                "status": "failed",
                "ok": False,
                "summary": f"AnySearch returned an error: {error_message}.",
                "error": "anysearch_http_error",
                "provider": "anysearch",
                "query": query,
                "status_code": response.status_code,
                "request_id": request_id,
                "results": [],
            }

        results = self._normalize_anysearch_results(response_payload, limit=limit)
        answer = self._extract_anysearch_answer(response_payload)
        return {
            "status": "completed",
            "ok": True,
            "summary": f"AnySearch returned {len(results)} result(s) for query '{query}'.",
            "provider": "anysearch",
            "query": query,
            "answer": answer,
            "results": results,
            "request_id": request_id,
            "metrics": {
                "returned_count": len(results),
            },
        }

    def _normalize_anysearch_results(self, payload: Any, *, limit: int) -> list[dict[str, Any]]:
        candidates = self._extract_anysearch_result_candidates(payload)
        results: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or item.get("headline") or "").strip()
            url = str(
                item.get("url")
                or item.get("link")
                or item.get("href")
                or item.get("source_url")
                or ""
            ).strip()
            snippet = str(
                item.get("snippet")
                or item.get("description")
                or item.get("summary")
                or item.get("content")
                or item.get("text")
                or ""
            ).strip()
            if not title and url:
                title = url
            if not title and not snippet:
                continue
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": str(item.get("source") or item.get("site") or item.get("domain") or "").strip(),
                    "published_at": str(
                        item.get("published_at")
                        or item.get("publishedAt")
                        or item.get("date")
                        or ""
                    ).strip(),
                }
            )
            if len(results) >= limit:
                break
        return results

    def _extract_anysearch_result_candidates(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("results", "items", "organic_results", "web", "documents"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "items", "organic_results", "web", "documents"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _extract_anysearch_answer(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("answer", "summary", "response", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("answer", "summary", "response", "text"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _extract_anysearch_error(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def _run_api_docs_library(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        service = self._require_api_docs_service()
        action = str(arguments.get("action") or "list").strip().lower()
        if action in {"files", "documents", "docs"}:
            action = "list"
        if action in {"get", "show", "inspect"}:
            action = "detail"
        if action in {"find", "lookup"}:
            action = "search"

        limit = _clamp_int(arguments.get("limit"), default=10, minimum=1, maximum=50)
        max_chars = _clamp_int(arguments.get("max_chars"), default=4000, minimum=500, maximum=20000)
        include_preview = bool(arguments.get("include_preview", False))
        project_name, project_url = ResourceAccessPolicy().resolve_api_doc_filters(
            arguments=arguments,
            context=context.context_bundle,
        )

        if action == "list":
            documents = await service.list_documents(project_name=project_name, project_url=project_url)
            selected = documents[:limit]
            return {
                "status": "completed",
                "ok": True,
                "summary": f"Found {len(documents)} imported API document(s).",
                "action": "list",
                "documents": [
                    self._api_doc_summary(document, include_preview=include_preview, max_chars=max_chars)
                    for document in selected
                ],
                "metrics": {
                    "document_count": len(documents),
                    "returned_count": len(selected),
                },
            }

        if action == "detail":
            doc_id = str(arguments.get("doc_id") or "").strip()
            query = str(arguments.get("query") or "").strip()
            if not doc_id and query:
                resolved = await service.search_documents(
                    query=query,
                    project_name=project_name,
                    project_url=project_url,
                    limit=1,
                    max_chars=800,
                )
                matches = resolved.get("matches") if isinstance(resolved.get("matches"), list) else []
                if matches and isinstance(matches[0], dict):
                    doc_id = str(matches[0].get("doc_id") or "")
            if not doc_id:
                return {
                    "status": "failed",
                    "ok": False,
                    "summary": "API Docs Library detail requires doc_id, or a query that can identify one document.",
                    "action": "detail",
                    "error": "missing_doc_id",
                }

            endpoint_result = await service.search_documents(
                doc_id=doc_id,
                project_name=project_name,
                project_url=project_url,
                limit=50,
                max_chars=800,
            )
            if not endpoint_result.get("matches") and (project_name or project_url):
                raise PermissionError("API document is outside the active resource scope.")
            content_result = await service.read_document_content(doc_id, max_chars=max_chars)
            document = dict(content_result.get("document") or {})
            endpoint_index = [
                {
                    "method": item.get("method"),
                    "path": item.get("path"),
                    "full_url": item.get("full_url"),
                    "summary": item.get("summary"),
                }
                for item in endpoint_result.get("matches", [])
                if isinstance(item, dict) and item.get("match_type") == "endpoint"
            ]
            document["endpoint_index"] = endpoint_index
            document["content_preview"] = str(content_result.get("content") or "")
            document["content_preview_truncated"] = bool(content_result.get("truncated"))
            document["content_full_length"] = content_result.get("full_length")
            if content_result.get("read_error"):
                document["read_error"] = content_result.get("read_error")
            return {
                "status": "completed",
                "ok": True,
                "summary": f"Loaded API document detail for '{document.get('title') or doc_id}'.",
                "action": "detail",
                "document": document,
                "metrics": {
                    "endpoint_count": len(endpoint_index),
                    "content_full_length": content_result.get("full_length"),
                },
            }

        if action == "search":
            query = str(arguments.get("query") or context.normalized_input or context.user_message or "").strip()
            result = await service.search_documents(
                query=query,
                doc_id=str(arguments.get("doc_id") or "").strip() or None,
                project_name=project_name,
                project_url=project_url,
                method=str(arguments.get("method") or "").strip() or None,
                path=str(arguments.get("path") or "").strip() or None,
                limit=limit,
                include_preview=include_preview,
                max_chars=max_chars,
            )
            return {
                "status": "completed",
                "ok": True,
                "action": "search",
                **result,
            }

        return {
            "status": "failed",
            "ok": False,
            "summary": "Unknown API Docs Library action. Use one of: list, detail, search.",
            "action": action,
            "error": "unknown_action",
        }

    async def _run_subagent_dispatch(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        coordinator_runtime = self._require_coordinator_runtime()
        return await coordinator_runtime.dispatch(
            payload=arguments,
            context=asdict(context),
        )

    async def _run_test_case_generator(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        feature = str(arguments.get("feature") or arguments.get("goal") or context.user_message).strip()
        requirements = _to_string_list(arguments.get("requirements"))
        acceptance_criteria = _to_string_list(arguments.get("acceptance_criteria"))
        platforms = _to_string_list(arguments.get("platforms")) or ["web"]
        risk_focus = _to_string_list(arguments.get("risk_focus"))

        if not feature:
            return {
                "status": "failed",
                "ok": False,
                "summary": "No feature or QA goal was provided for test case generation.",
                "cases": [],
                "metrics": {"case_count": 0},
                "error": "missing_feature",
            }

        scenario_inputs = acceptance_criteria or requirements or [feature]
        cases: list[dict[str, Any]] = []
        for index, item in enumerate(scenario_inputs, start=1):
            title = item if len(item) <= 72 else f"{item[:69]}..."
            case_id = f"TC-{index:03d}"
            steps = [
                {"step": 1, "action": f"Prepare the environment for {feature}.", "expected": "The target workflow is reachable."},
                {"step": 2, "action": f"Execute scenario: {item}.", "expected": "The workflow processes the action without validation errors."},
                {"step": 3, "action": "Verify outcome and evidence.", "expected": "Observed results match the expected behavior."},
            ]
            cases.append(
                {
                    "id": case_id,
                    "title": title,
                    "type": "functional",
                    "priority": "high" if index == 1 else "medium",
                    "platforms": platforms,
                    "preconditions": [
                        f"The {feature} workflow is available.",
                        "Required test data and environment access are prepared.",
                    ],
                    "steps": steps,
                    "assertions": [
                        item,
                        *[criterion for criterion in acceptance_criteria if criterion != item],
                    ],
                    "risk_focus": risk_focus,
                }
            )

        coverage = {
            "requirements": requirements,
            "acceptance_criteria": acceptance_criteria,
            "risk_focus": risk_focus,
            "platforms": platforms,
        }
        return {
            "summary": f"Generated {len(cases)} structured test cases for '{feature}'.",
            "cases": cases,
            "coverage": coverage,
            "artifacts": [
                {
                    "type": "generated_cases",
                    "label": "test_cases.json",
                    "content": json.dumps({"feature": feature, "cases": cases}, ensure_ascii=False, indent=2),
                }
            ],
            "metrics": {
                "case_count": len(cases),
                "requirement_count": len(requirements),
                "acceptance_criteria_count": len(acceptance_criteria),
            },
        }

    async def _run_test_case_xlsx_exporter(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        cases = arguments.get("cases") or []
        feature = str(arguments.get("feature") or "test_cases").strip()
        if not cases:
            return {
                "status": "failed",
                "ok": False,
                "summary": "No test cases provided for xlsx export.",
                "artifacts": [],
                "metrics": {"case_count": 0},
                "error": "missing_cases",
            }

        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Test Cases"
        headers = [
            "ID", "Title", "Type", "Priority", "Platforms",
            "Preconditions", "Steps", "Expected Results", "Assertions", "Risk Focus",
        ]
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        for column, header in enumerate(headers, start=1):
            cell = worksheet.cell(row=1, column=column, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        for case in cases:
            if not isinstance(case, dict):
                continue
            steps = case.get("steps") if isinstance(case.get("steps"), list) else []
            worksheet.append([
                case.get("id", ""),
                case.get("title", ""),
                case.get("type", ""),
                case.get("priority", ""),
                ", ".join(_to_string_list(case.get("platforms"))),
                "\n".join(_to_string_list(case.get("preconditions"))),
                "\n".join(
                    f"{step.get('step', index)}. {step.get('action', '')}"
                    for index, step in enumerate(steps, start=1)
                    if isinstance(step, dict)
                ),
                "\n".join(
                    str(step.get("expected") or "")
                    for step in steps
                    if isinstance(step, dict) and step.get("expected")
                ),
                "\n".join(_to_string_list(case.get("assertions"))),
                ", ".join(_to_string_list(case.get("risk_focus"))),
            ])

        for column_cells in worksheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max_length + 2, 50)

        artifact_dir = self._prepare_local_artifact_dir(context, "test-case-xlsx-exporter")
        safe_feature = _slug(feature)[:60] or "test_cases"
        xlsx_path = artifact_dir / f"{safe_feature}_test_cases.xlsx"
        workbook.save(xlsx_path)
        return {
            "status": "completed",
            "ok": True,
            "summary": f"Exported {len(cases)} test cases to '{xlsx_path.name}'.",
            "artifact_path": str(xlsx_path),
            "case_count": len(cases),
            "artifacts": [{"type": "test_cases_xlsx", "label": xlsx_path.name, "path": str(xlsx_path)}],
            "metrics": {"case_count": len(cases)},
        }

    async def _run_attachment_reader(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        store = self._require_session_store()
        session = await store.get_session(context.session_id)
        if session is None:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Session '{context.session_id}' was not found.",
                "error": "session_not_found",
            }

        attachments = self._collect_available_attachments(context, session)
        if not attachments:
            return {
                "status": "failed",
                "ok": False,
                "summary": "No uploaded attachments are available in the current turn or recent user message.",
                "error": "attachment_not_found",
            }

        target = self._select_attachment_for_read(arguments, attachments)
        if target is None:
            requested_name = str(arguments.get("name") or arguments.get("attachment_id") or arguments.get("uri") or "").strip()
            return {
                "status": "failed",
                "ok": False,
                "summary": f"No attachment matched '{requested_name or 'current attachment'}'.",
                "available_attachments": [self._attachment_summary(item) for item in attachments],
                "error": "attachment_not_found",
            }

        max_chars = _clamp_int(arguments.get("max_chars"), minimum=200, maximum=50000, default=12000)
        prefer_excerpt = bool(arguments.get("prefer_excerpt", False))
        excerpt = " ".join(str(target.get("text_excerpt") or "").split())
        excerpt_text, excerpt_truncated = _truncate_text(excerpt, max_chars) if excerpt else ("", False)
        attachment_allowed, security_report = self._attachment_read_allowed(target)
        if not attachment_allowed:
            security_decision = str(security_report.get("decision") or "blocked").strip() if security_report else "blocked"
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Attachment '{target.get('name')}' is not readable because it is marked as {security_decision}.",
                "attachment": self._attachment_summary(target),
                "error": "attachment_security_blocked",
            }
        uri = str(target.get("uri") or "").strip()
        content_type = str(target.get("content_type") or target.get("metadata", {}).get("content_type") or "").strip()

        resolved_content = ""
        resolved_from = "excerpt"
        preview_truncated = excerpt_truncated or bool(target.get("metadata", {}).get("preview_truncated"))
        size_bytes = target.get("metadata", {}).get("size_bytes")

        if not prefer_excerpt and uri.startswith("rustfs://") and self._artifact_storage_service is not None:
            try:
                storage_result = await self._artifact_storage_service.read_object_uri(uri)
                decoded_content, decode_error = self._decode_attachment_bytes(
                    content=storage_result["content"],
                    content_type=str(storage_result.get("content_type") or content_type),
                    filename=str(target.get("name") or ""),
                    max_chars=max_chars,
                )
                size_bytes = storage_result.get("size_bytes", size_bytes)
                if decoded_content:
                    resolved_content = decoded_content
                    resolved_from = "rustfs"
                    preview_truncated = len(storage_result["content"]) > len(decoded_content.encode("utf-8", errors="ignore"))
                elif excerpt_text:
                    resolved_content = excerpt_text
                    resolved_from = "excerpt_fallback"
                else:
                    return {
                        "status": "failed",
                        "ok": False,
                        "summary": f"Attachment '{target.get('name')}' is stored but could not be decoded as readable text.",
                        "attachment": self._attachment_summary(target),
                        "error": decode_error or "attachment_not_readable",
                    }
            except Exception as exc:
                if excerpt_text:
                    resolved_content = excerpt_text
                    resolved_from = "excerpt_fallback"
                else:
                    return {
                        "status": "failed",
                        "ok": False,
                        "summary": f"Failed to read attachment '{target.get('name')}' from object storage: {exc}",
                        "attachment": self._attachment_summary(target),
                        "error": "attachment_storage_read_failed",
                    }
        else:
            resolved_content = excerpt_text

        if not resolved_content:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Attachment '{target.get('name')}' does not contain readable text content yet.",
                "attachment": self._attachment_summary(target),
                "error": "attachment_empty",
            }

        attachment_summary = self._attachment_summary(target)
        attachment_summary["size_bytes"] = size_bytes
        return {
            "summary": f"Read attachment '{target.get('name')}' from {resolved_from}.",
            "attachment": attachment_summary,
            "content": resolved_content,
            "content_format": self._attachment_content_format(str(target.get('name') or ""), content_type),
            "resolved_from": resolved_from,
            "preview_truncated": preview_truncated,
            "available_attachments": [self._attachment_summary(item) for item in attachments],
            "metrics": {
                "attachment_count": len(attachments),
                "content_chars": len(resolved_content),
            },
        }

    async def _run_session_history(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        store = self._require_session_store()
        action = str(arguments.get("action") or "history_summary").strip().lower()
        scope = str(arguments.get("scope") or "current_session").strip().lower()
        include_assistant = bool(arguments.get("include_assistant"))
        limit = int(arguments.get("limit") or 10)
        limit = max(1, min(limit, 50))

        if scope == "all_sessions":
            if action == "count_sessions":
                status_counts, recent = await asyncio.gather(
                    store.count_sessions_by_status(),
                    store.list_history_overviews(limit=limit),
                )
                total = sum(status_counts.values())
                return {
                    "summary": f"Counted {total} stored session(s) for scope 'all_sessions'.",
                    "scope": "all_sessions",
                    "session_count": total,
                    "status_counts": status_counts,
                    "sessions": recent,
                    "metrics": {
                        "session_count": total,
                        "status_kind_count": len(status_counts),
                    },
                }
            if action == "list_questions":
                questions = await store.list_recent_questions(
                    session_limit=limit,
                    question_limit=limit,
                    include_assistant=include_assistant,
                )
                return {
                    "summary": (
                        f"Collected {len(questions)} historical question item(s) "
                        "from scope 'all_sessions'."
                    ),
                    "scope": "all_sessions",
                    "questions": questions,
                    "metrics": {
                        "question_count": len(questions),
                        "session_count": len(
                            {item["session_id"] for item in questions}
                        ),
                    },
                }
            if action == "history_summary":
                overviews = await store.list_history_overviews(limit=limit)
                return {
                    "summary": (
                        "Built a history summary for scope 'all_sessions' "
                        f"across {len(overviews)} session(s)."
                    ),
                    "scope": "all_sessions",
                    "sessions": overviews,
                    "questions": [],
                    "metrics": {
                        "session_count": len(overviews),
                        "question_count": 0,
                    },
                }

        scope = "current_session"
        current_session = await store.get_session(context.session_id)
        if current_session is None:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Session '{context.session_id}' was not found.",
                "error": "session_not_found",
            }
        sessions = [current_session]
        full_sessions = [current_session]

        if action == "count_sessions":
            status_counts: dict[str, int] = {}
            for item in sessions:
                status_key = item.status.value
                status_counts[status_key] = status_counts.get(status_key, 0) + 1
            return {
                "summary": (
                    f"Counted {len(sessions)} stored session(s) for scope '{scope}'."
                    if scope == "all_sessions"
                    else "Counted the current session."
                ),
                "scope": scope,
                "session_count": len(sessions),
                "status_counts": status_counts,
                "sessions": [self._session_overview(item) for item in sessions[:limit]],
                "metrics": {
                    "session_count": len(sessions),
                    "status_kind_count": len(status_counts),
                },
            }

        if action == "list_questions":
            questions = self._extract_questions(
                sessions=full_sessions,
                include_assistant=include_assistant,
                limit=limit,
            )
            return {
                "summary": f"Collected {len(questions)} historical question item(s) from scope '{scope}'.",
                "scope": scope,
                "questions": questions,
                "metrics": {
                    "question_count": len(questions),
                    "session_count": len(full_sessions),
                },
            }

        if action == "session_report":
            report = await self._build_session_report(
                session=current_session,
                store=store,
                include_assistant=include_assistant,
                limit=limit,
            )
            markdown = self._format_session_report_markdown(report)
            report_html = self._report_template_service.render_report_html(
                title=f"Session Report: {report['title']}",
                time_label=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                sender=context.selected_agent_key or "Enterprise AI QA Agent",
                markdown_content=markdown,
            )
            return {
                "summary": f"Generated a structured report for session '{current_session.id}'.",
                "scope": "current_session",
                "report": report,
                "report_markdown": markdown,
                "report_html": report_html,
                "artifacts": [
                    {
                        "type": "session_report_markdown",
                        "label": "session_report.md",
                        "content": markdown,
                    },
                    {
                        "type": "session_report_html",
                        "label": "session_report.html",
                        "content": report_html,
                    }
                ],
                "metrics": {
                    "message_count": report["message_count"],
                    "event_count": report["event_count"],
                    "snapshot_count": report["snapshot_count"],
                },
            }

        sessions_overview = [self._session_overview(item) for item in sessions[:limit]]
        recent_questions = (
            self._extract_questions(
                sessions=full_sessions,
                include_assistant=False,
                limit=limit,
            )
            if scope != "all_sessions"
            else []
        )
        return {
            "summary": f"Built a history summary for scope '{scope}' across {len(sessions)} session(s).",
            "scope": scope,
            "sessions": sessions_overview,
            "questions": recent_questions,
            "metrics": {
                "session_count": len(sessions),
                "question_count": len(recent_questions),
            },
        }

    async def _run_session_timeline(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        store = self._require_session_store()
        memory_runtime = self._require_memory_runtime()
        session = await store.get_session(context.session_id)
        if session is None:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Session '{context.session_id}' was not found.",
                "error": "session_not_found",
            }

        limit = int(arguments.get("limit") or 20)
        limit = max(1, min(limit, 100))
        include_messages = bool(arguments.get("include_messages", True))
        include_events = bool(arguments.get("include_events", True))
        include_observations = bool(arguments.get("include_observations", True))

        timeline: list[dict[str, Any]] = []
        if include_messages:
            for item in session.messages[-limit:]:
                message_view = self._transcript_hygiene_service.classify_message(item)
                role = message_view["role"]
                content = message_view["content"]
                if not content:
                    continue
                timeline.append(
                    {
                        "kind": "message",
                        "timestamp": item.created_at.isoformat(),
                        "label": f"{role} message",
                        "role": role,
                        "content": content,
                        "transcript_bucket": message_view["transcript_bucket"],
                        "context_eligible": message_view["context_eligible"],
                        "response_mode": message_view["response_mode"],
                    }
                )
        if include_events:
            events = await store.list_events(session.id, limit=limit)
            for event in events:
                timeline.append(
                    {
                        "kind": "event",
                        "timestamp": event.timestamp.isoformat(),
                        "label": event.type,
                        "payload": dict(event.payload or {}),
                    }
                )
        if include_observations:
            observations = await memory_runtime.list_session_observations(session.id, top_k=limit)
            for observation in observations[-limit:]:
                timeline.append(
                    {
                        "kind": "observation",
                        "timestamp": observation.created_at.isoformat(),
                        "label": observation.title,
                        "category": observation.category,
                        "tool_key": observation.tool_key,
                        "summary": observation.summary,
                        "source": observation.source,
                    }
                )

        timeline.sort(key=lambda item: item.get("timestamp", ""))
        trimmed_timeline = timeline[-limit:]
        report = {
            "session": self._session_overview(session),
            "timeline_count": len(trimmed_timeline),
            "transcript_summary": self._transcript_hygiene_service.summarize_messages(session.messages),
            "includes": {
                "messages": include_messages,
                "events": include_events,
                "observations": include_observations,
            },
        }
        return {
            "summary": f"Built a chronological timeline with {len(trimmed_timeline)} entries for session '{session.id}'.",
            "timeline": trimmed_timeline,
            "report": report,
            "metrics": {
                "timeline_count": len(trimmed_timeline),
                "include_messages": int(include_messages),
                "include_events": int(include_events),
                "include_observations": int(include_observations),
            },
        }

    async def _run_observation_search(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        memory_runtime = self._require_memory_runtime()
        query = str(arguments.get("query") or context.normalized_input or context.user_message).strip()
        if not query:
            return {
                "status": "failed",
                "ok": False,
                "summary": "No observation query was provided.",
                "error": "missing_query",
            }

        scope = str(arguments.get("scope") or "current_session").strip().lower()
        limit = int(arguments.get("limit") or 8)
        limit = max(1, min(limit, 20))
        category_filter = str(arguments.get("category") or "").strip()
        tool_key_filter = str(arguments.get("tool_key") or "").strip()
        if str(context.context_bundle.get("mode_key") or "").strip().lower() == "security_testing":
            scope = "current_session"

        result = await memory_runtime.retrieve_observation_context(
            session_id=context.session_id if scope != "all_sessions" else None,
            trace_id=context.trace_id,
            query=query,
            context=context.context_bundle,
            top_k=limit,
        )
        observations = []
        for item in result.hits:
            metadata = item.metadata or {}
            category = str(metadata.get("observation_category") or "")
            tool_key = str(metadata.get("tool_key") or "")
            if category_filter and category != category_filter:
                continue
            if tool_key_filter and tool_key != tool_key_filter:
                continue
            observations.append(
                {
                    "id": str(metadata.get("observation_id") or item.id),
                    "title": str(metadata.get("observation_title") or item.summary or item.source or item.id),
                    "summary": item.summary,
                    "content": item.content,
                    "scope": item.scope,
                    "source": item.source,
                    "category": category or "tool_execution",
                    "tool_key": tool_key or "tool",
                    "score": item.score or 0.0,
                    "created_at": item.created_at.isoformat(),
                    "tags": list(item.tags or []),
                }
            )

        observations = observations[:limit]
        return {
            "summary": f"Retrieved {len(observations)} observation(s) for query '{query}'.",
            "scope": scope,
            "query": query,
            "observations": observations,
            "metrics": {
                "observation_count": len(observations),
                "category_filter": category_filter,
                "tool_key_filter": tool_key_filter,
            },
        }

    async def _run_workflow_router(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or context.user_message).lower()
        route = "coordinator"
        rationale = "Defaulted to coordinator for orchestration."
        if any(token in query for token in ["browser", "page", "ui", "selenium", "playwright", "页面", "浏览器", "探索", "图谱"]):
            route = "ui-executor"
            rationale = "Detected browser or UI execution intent."
        elif any(
            token in query
            for token in [
                "cli",
                "terminal",
                "shell",
                "powershell",
                "command",
                "cmd",
                "bash",
                "终端",
                "命令行",
                "控制台",
                "shell命令",
            ]
        ):
            route = "ops-executor"
            rationale = "Detected terminal or command execution intent."
        elif any(token in query for token in ["api", "payload", "response", "request"]):
            route = "api-verifier"
            rationale = "Detected API verification intent."
        elif any(token in query for token in ["history", "session report", "conversation report", "previous questions", "历史", "会话报告", "对话报告", "之前问", "以前问"]):
            route = "report-analyst"
            rationale = "Detected session history or conversation reporting intent."
        elif any(token in query for token in ["email", "message", "notify", "notification", "send"]):
            route = "communication-agent"
            rationale = "Detected messaging or notification intent."
        elif any(token in query for token in ["report", "summary", "结论", "报告"]):
            route = "report-analyst"
            rationale = "Detected reporting intent."
        elif any(token in query for token in ["plan", "case", "scenario", "用例", "测试点"]):
            route = "qa-planner"
            rationale = "Detected planning or case design intent."

        return {
            "summary": f"Recommended execution route: {route}.",
            "route": route,
            "rationale": rationale,
        }

    async def _run_dom_inspector(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "browser-mcp",
            "inspect-page",
            arguments,
            asdict(context),
        )

    async def _run_ui_page_explorer(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._ui_exploration_service is None:
            return {"status": "failed", "error": "UI exploration service is not configured."}
        return await self._ui_exploration_service.explore(arguments, context)

    async def _run_browser_automation(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "browser-mcp",
            "browser-automation",
            arguments,
            asdict(context),
        )

    async def _run_browser_control(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "browser-mcp",
            "browser-control",
            arguments,
            asdict(context),
        )

    async def _run_api_tester(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        endpoint = str(arguments.get("endpoint") or arguments.get("url") or "").strip()
        method = str(arguments.get("method") or "GET").upper()
        request_body = arguments.get("request_body") if isinstance(arguments.get("request_body"), dict) else {}
        response_body = arguments.get("response_body") if isinstance(arguments.get("response_body"), dict) else {}
        response_status = arguments.get("response_status")
        expected_status = arguments.get("expected_status")
        expected_fields = _to_string_list(arguments.get("expected_fields"))
        assertions = _to_string_list(arguments.get("assertions"))

        checks: list[dict[str, Any]] = []
        passed = 0
        failed = 0

        if expected_status is not None:
            status_ok = response_status == expected_status
            checks.append(
                {
                    "name": "status_code",
                    "passed": status_ok,
                    "expected": expected_status,
                    "actual": response_status,
                    "reason": "Response status matched expectation." if status_ok else "Response status did not match expectation.",
                }
            )
            passed += 1 if status_ok else 0
            failed += 0 if status_ok else 1

        for field_path in expected_fields:
            value, exists = _lookup_path(response_body, field_path)
            checks.append(
                {
                    "name": f"field:{field_path}",
                    "passed": exists,
                    "expected": "present",
                    "actual": value if exists else None,
                    "reason": "Field is present in the response payload." if exists else "Field is missing from the response payload.",
                }
            )
            passed += 1 if exists else 0
            failed += 0 if exists else 1

        for assertion in assertions:
            checks.append(
                {
                    "name": f"assertion:{len(checks)+1}",
                    "passed": True,
                    "expected": assertion,
                    "actual": "captured",
                    "reason": "Assertion was captured for downstream verification.",
                }
            )
            passed += 1

        if not checks:
            return {
                "status": "partial",
                "ok": True,
                "summary": f"No executable API checks were supplied for {method} {endpoint or 'request'}; captured the request context only.",
                "checks": [],
                "request": {"endpoint": endpoint, "method": method, "body": request_body},
                "response": {"status": response_status, "body": response_body},
                "metrics": {"check_count": 0, "passed": 0, "failed": 0},
                "artifacts": [],
            }

        overall_ok = failed == 0
        status = "completed" if overall_ok else "partial"
        return {
            "status": status,
            "ok": overall_ok,
            "summary": (
                f"Validated {len(checks)} API checks for {method} {endpoint or 'request'}; "
                f"{passed} passed and {failed} failed."
            ),
            "checks": checks,
            "request": {"endpoint": endpoint, "method": method, "body": request_body},
            "response": {"status": response_status, "body": response_body},
            "metrics": {
                "check_count": len(checks),
                "passed": passed,
                "failed": failed,
            },
            "artifacts": [
                {
                    "type": "api-check-summary",
                    "label": "api_checks.json",
                    "content": json.dumps({"checks": checks, "endpoint": endpoint, "method": method}, ensure_ascii=False, indent=2),
                }
            ],
            "error": None if overall_ok else "api_assertions_failed",
        }

    async def _run_cli_executor(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        command = str(arguments.get("command") or "").strip()
        if not command:
            return {
                "status": "failed",
                "ok": False,
                "summary": "CLI Executor requires a non-empty command.",
                "error": "missing_command",
            }

        shell_name = str(arguments.get("shell") or "powershell").strip().lower()
        timeout_seconds = float(arguments.get("timeout_seconds") or 20)
        timeout_seconds = max(1.0, min(timeout_seconds, 300.0))
        cwd = self._resolve_cli_cwd(arguments.get("cwd"))
        artifact_dir = self._prepare_local_artifact_dir(context, "cli-executor")

        executable, shell_args = self._build_cli_invocation(shell_name, command)
        started_at = datetime.utcnow()
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [shutil.which(executable) or executable, *shell_args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            stdout_text = completed.stdout or ""
            stderr_text = completed.stderr or ""
            exit_code = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout_text = exc.stdout or ""
            stderr_text = exc.stderr or ""
            exit_code = -1
            timed_out = True

        status = "completed" if exit_code == 0 and not timed_out else "partial" if timed_out else "failed"
        ok = exit_code == 0 and not timed_out

        transcript = {
            "command": command,
            "shell": shell_name,
            "cwd": str(cwd),
            "started_at": started_at.isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }
        transcript_path = artifact_dir / "cli_transcript.json"
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")

        summary = (
            f"Command finished with exit code {exit_code} in {shell_name}."
            if not timed_out
            else f"Command timed out after {timeout_seconds:.0f}s in {shell_name}."
        )
        return {
            "status": status,
            "ok": ok,
            "summary": summary,
            "command": command,
            "shell": shell_name,
            "cwd": str(cwd),
            "exit_code": exit_code,
            "stdout": stdout_text[-8000:],
            "stderr": stderr_text[-8000:],
            "metrics": {
                "stdout_chars": len(stdout_text),
                "stderr_chars": len(stderr_text),
                "timed_out": timed_out,
            },
            "artifacts": [{"type": "cli_transcript", "path": str(transcript_path)}],
            "error": None if ok else ("timeout" if timed_out else stderr_text[-500:] or f"exit_code={exit_code}"),
        }

    async def _run_file_artifact_manager(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "filesystem-mcp",
            "write-artifact",
            arguments,
            asdict(context),
        )

    async def _deliver_security_testing_report(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._run_mail_send(arguments, context)

    async def _run_report_writer(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        campaign_kind = str(context.context_bundle.get("campaign_kind") or "").strip()
        campaign_project_name = str(context.context_bundle.get("campaign_project_name") or "").strip()
        requested_template_key = str(arguments.get("template_key") or "").strip()
        template_key = requested_template_key or "default"
        if (
            template_key == "default"
            and campaign_kind in {"code_review_debate", "code_review_debate_summary"}
        ):
            template_key = "code_review_debate"

        title = str(arguments.get("title") or "QA Execution Report").strip()
        if (
            template_key == "code_review_debate"
            and (not title or title == "QA Execution Report")
            and campaign_project_name
        ):
            title = f"《{campaign_project_name}》的辩论报告"
        objective = str(arguments.get("objective") or context.user_message).strip()
        summary = str(arguments.get("summary") or "").strip()
        status = str(arguments.get("status") or "completed").strip()
        sender = str(arguments.get("sender") or context.selected_agent_key or "Enterprise AI QA Agent").strip() or "Enterprise AI QA Agent"
        content_markdown = str(arguments.get("content_markdown") or "").strip()
        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        findings = arguments.get("findings") if isinstance(arguments.get("findings"), list) else []
        evidence = arguments.get("evidence") if isinstance(arguments.get("evidence"), list) else []
        recommendations = _to_string_list(arguments.get("recommendations"))
        report_sections: list[dict[str, Any]]
        template_context: dict[str, Any] | None = None
        report_artifact_stem = "qa_report"

        if template_key == "code_review_debate":
            project_name = str(arguments.get("project_name") or campaign_project_name or title).strip() or title
            approval_time = str(arguments.get("approval_time") or generated_at).strip() or generated_at
            approval_result = str(arguments.get("approval_result") or status).strip() or status
            reviewer_dispatches = (
                context.context_bundle.get("reviewer_dispatches")
                if isinstance(context.context_bundle.get("reviewer_dispatches"), list)
                else []
            )
            inferred_agent_count = len(reviewer_dispatches)
            agent_count = _clamp_int(
                arguments.get("agent_count"),
                minimum=0,
                maximum=99,
                default=inferred_agent_count,
            )
            result_rows = arguments.get("result_rows") if isinstance(arguments.get("result_rows"), list) else []
            report_sections = [
                {"title": "审批概览", "content": summary or f"围绕《{project_name}》生成的代码审批辩论报告。"},
                {"title": "最终结论", "content": approval_result},
                {"title": "审批结果", "content": self._build_code_review_category_sections(result_rows)},
                {"title": "证据与发现", "content": _format_object_list(findings)},
                {"title": "辩论证据", "content": _format_object_list(evidence)},
                {"title": "建议措施", "content": "\n".join(f"- {item}" for item in recommendations) or "None provided."},
            ]
            markdown = content_markdown or self._build_code_review_debate_markdown(
                title=title,
                project_name=project_name,
                approval_time=approval_time,
                approval_result=approval_result,
                agent_count=agent_count,
                report_sections=report_sections,
                result_rows=result_rows,
            )
            template_context = {
                "project_name": project_name,
                "approval_result": approval_result,
                "agent_count": str(agent_count),
                "result_summary_markdown": self._build_code_review_result_summary_markdown(result_rows),
            }
            report_artifact_stem = "code_review_debate_report"
            generated_at = approval_time
        else:
            report_sections = [
                {"title": "Overview", "content": summary or f"Report generated for: {objective or title}."},
                {"title": "Status", "content": status},
                {"title": "Findings", "content": _format_object_list(findings)},
                {"title": "Evidence", "content": _format_object_list(evidence)},
                {"title": "Recommendations", "content": "\n".join(f"- {item}" for item in recommendations) or "None provided."},
            ]
            markdown_lines = [f"# {title}", "", f"Status: {status}"]
            if objective:
                markdown_lines.extend(["", f"Objective: {objective}"])
            for section in report_sections:
                markdown_lines.extend(["", f"## {section['title']}", "", str(section["content"])])
            markdown = content_markdown or "\n".join(markdown_lines).strip()

        report_html = self._report_template_service.render_report_html(
            title=title,
            time_label=generated_at,
            sender=sender,
            markdown_content=markdown,
            template_key=template_key,
            template_context=template_context,
        )

        return {
            "summary": f"Generated a structured QA report with {len(report_sections)} sections.",
            "report_title": title,
            "report_sections": report_sections,
            "report_markdown": markdown,
            "report_html": report_html,
            "sender": sender,
            "generated_at": generated_at,
            "template_key": template_key,
            "template_context": template_context,
            "artifacts": [
                {
                    "type": "report_markdown",
                    "label": f"{report_artifact_stem}.md",
                    "content": markdown,
                },
                {
                    "type": "report_html",
                    "label": f"{report_artifact_stem}.html",
                    "content": report_html,
                }
            ],
            "metrics": {
                "section_count": len(report_sections),
                "finding_count": len(findings),
                "evidence_count": len(evidence),
                "recommendation_count": len(recommendations),
            },
        }

    async def _run_project_source_loader(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        project_source = normalize_project_source(arguments)
        timeout_seconds = _clamp_float(arguments.get("timeout_seconds"), minimum=5.0, maximum=60.0, default=20.0)
        try:
            source_info = (
                await self._collect_ssh_source_info(project_source, timeout_seconds)
                if project_source.source_type == "ssh"
                else await self._collect_local_source_info(project_source, timeout_seconds)
            )
        except Exception as exc:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Failed to load project source '{project_source.project_name}': {exc}",
                "project_source": project_source.model_dump(mode="python"),
                "error": str(exc),
            }
        return {
            "summary": (
                f"Loaded project source '{project_source.project_name}' via {project_source.source_type} "
                f"at {source_info['root_path']}."
            ),
            "project_source": project_source.model_dump(mode="python"),
            "source_info": source_info,
            "metrics": {
                "top_entry_count": len(source_info.get("top_entries", [])),
                "git_repo": 1 if source_info.get("is_git_repo") else 0,
            },
        }

    async def _run_project_tree_scanner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        project_source = normalize_project_source(arguments)
        ignored_names = ignored_names_from_arguments(arguments)
        max_depth = _clamp_int(arguments.get("max_depth"), minimum=1, maximum=8, default=3)
        max_files = _clamp_int(arguments.get("max_files"), minimum=20, maximum=2000, default=400)
        timeout_seconds = _clamp_float(arguments.get("timeout_seconds"), minimum=5.0, maximum=90.0, default=30.0)
        try:
            tree_summary = (
                await self._scan_ssh_project_tree(
                    project_source,
                    max_depth=max_depth,
                    max_files=max_files,
                    timeout_seconds=timeout_seconds,
                    ignored_names=ignored_names,
                )
                if project_source.source_type == "ssh"
                else await self._scan_local_project_tree(
                    project_source,
                    max_depth=max_depth,
                    max_files=max_files,
                    ignored_names=ignored_names,
                )
            )
        except Exception as exc:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Failed to scan project tree for '{project_source.project_name}': {exc}",
                "project_source": project_source.model_dump(mode="python"),
                "error": str(exc),
            }
        return {
            "summary": (
                f"Scanned project tree for '{project_source.project_name}' and sampled "
                f"{len(tree_summary.get('sample_files', []))} file(s)."
            ),
            "project_source": project_source.model_dump(mode="python"),
            "tree_summary": tree_summary,
            "sample_files": tree_summary.get("sample_files", []),
            "metrics": {
                "sample_file_count": len(tree_summary.get("sample_files", [])),
                "top_module_count": len(tree_summary.get("top_modules", [])),
                "extension_count": len(tree_summary.get("extensions", {})),
            },
        }

    async def _run_project_file_reader(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        project_source = normalize_project_source(arguments)
        file_path = str(arguments.get("path") or arguments.get("file_path") or "").strip()
        if not file_path:
            return {
                "status": "failed",
                "ok": False,
                "summary": "Project file reader requires a path or file_path.",
                "error": "missing_file_path",
            }
        ignored_names = ignored_names_from_arguments(arguments)
        if is_ignored_project_path(file_path, ignored_names):
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Refused to read ignored code review path '{file_path}'.",
                "project_source": project_source.model_dump(mode="python"),
                "error": "ignored_project_path",
                "ignored_paths": sorted(ignored_names),
            }
        start_line = _clamp_int(arguments.get("start_line"), minimum=1, maximum=500000, default=1)
        end_line = _clamp_int(arguments.get("end_line"), minimum=start_line, maximum=500000, default=start_line + 199)
        max_chars = _clamp_int(arguments.get("max_chars"), minimum=500, maximum=120000, default=16000)
        timeout_seconds = _clamp_float(arguments.get("timeout_seconds"), minimum=5.0, maximum=90.0, default=20.0)
        try:
            file_payload = (
                await self._read_ssh_project_file(
                    project_source,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    max_chars=max_chars,
                    timeout_seconds=timeout_seconds,
                )
                if project_source.source_type == "ssh"
                else self._read_local_project_file(
                    project_source,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    max_chars=max_chars,
                )
            )
        except Exception as exc:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Failed to read file '{file_path}': {exc}",
                "project_source": project_source.model_dump(mode="python"),
                "error": str(exc),
            }
        return {
            "summary": (
                f"Read file '{file_payload['file']['path']}' lines "
                f"{file_payload['file']['start_line']}-{file_payload['file']['end_line']}."
            ),
            "project_source": project_source.model_dump(mode="python"),
            **file_payload,
        }

    async def _run_project_diff_reader(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        project_source = normalize_project_source(arguments)
        timeout_seconds = _clamp_float(arguments.get("timeout_seconds"), minimum=5.0, maximum=90.0, default=20.0)
        max_chars = _clamp_int(arguments.get("max_chars"), minimum=1000, maximum=200000, default=24000)
        try:
            diff_payload = (
                await self._read_ssh_project_diff(project_source, arguments, timeout_seconds=timeout_seconds, max_chars=max_chars)
                if project_source.source_type == "ssh"
                else await self._read_local_project_diff(project_source, arguments, timeout_seconds=timeout_seconds, max_chars=max_chars)
            )
        except Exception as exc:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Failed to read project diff for '{project_source.project_name}': {exc}",
                "project_source": project_source.model_dump(mode="python"),
                "error": str(exc),
            }
        return {
            "summary": (
                f"Collected git diff context for '{project_source.project_name}' with "
                f"{len(diff_payload.get('status_lines', []))} status line(s)."
            ),
            "project_source": project_source.model_dump(mode="python"),
            **diff_payload,
        }

    async def _run_code_governance_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._code_governance_runtime.handle(arguments, context)

    async def _run_code_review_orchestrator(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        bootstrap = await self._build_code_review_bootstrap(arguments, context)
        governance = await self._run_code_governance_runner(
            {
                **arguments,
                "scanner_artifacts": arguments.get("scanner_artifacts")
                or context.context_bundle.get("scanner_artifacts"),
                "policy": arguments.get("policy")
                or context.context_bundle.get("policy")
                or context.context_bundle.get("governance_policy"),
            },
            context,
        )
        debate_plan = self._resolve_code_review_debate_plan(arguments, bootstrap, context)
        effective_arguments = {
            **arguments,
            "cross_review_rounds": debate_plan["cross_review_round_count"],
            "debate_time_budget_minutes": debate_plan["debate_time_budget_minutes"],
            "governance_evidence": governance,
        }
        campaign = build_code_review_campaign(effective_arguments, context)
        campaign["bootstrap"] = bootstrap
        campaign["governance"] = governance
        campaign["debate_plan"] = debate_plan
        scanned_file_count = int(((bootstrap.get("tree_summary") or {}) if isinstance(bootstrap.get("tree_summary"), dict) else {}).get("scanned_file_count") or 0)
        requested_reviewer_limit = _clamp_int(arguments.get("reviewer_count"), minimum=0, maximum=5, default=0)
        reviewer_limit = requested_reviewer_limit
        if reviewer_limit <= 0:
            if scanned_file_count and scanned_file_count <= 80:
                reviewer_limit = 3
            elif scanned_file_count and scanned_file_count <= 200:
                reviewer_limit = 4
        workers_payload = campaign["dispatch_payload"].get("workers", [])
        followup_workers_payload = campaign["dispatch_payload"].get("followup_workers", [])
        if reviewer_limit > 0 and isinstance(workers_payload, list):
            campaign["dispatch_payload"]["workers"] = workers_payload[:reviewer_limit]
            if isinstance(followup_workers_payload, list):
                limited_followup_workers: list[dict[str, Any]] = []
                grouped_followup_workers: dict[int, list[dict[str, Any]]] = {}
                for item in followup_workers_payload:
                    if not isinstance(item, dict):
                        continue
                    context_payload = item.get("context")
                    debate_round_index = 0
                    if isinstance(context_payload, dict):
                        debate_round_index = int(context_payload.get("debate_round_index") or 0)
                    grouped_followup_workers.setdefault(debate_round_index, []).append(item)
                for round_index in sorted(grouped_followup_workers):
                    limited_followup_workers.extend(grouped_followup_workers[round_index][:reviewer_limit])
                campaign["dispatch_payload"]["followup_workers"] = limited_followup_workers
            campaign["review_team"] = campaign["review_team"][:reviewer_limit]
            campaign["metrics"]["reviewer_count"] = len(campaign["dispatch_payload"]["workers"])
        bootstrap_brief = self._build_code_review_bootstrap_brief(bootstrap)
        governance_brief = self._build_code_governance_brief(governance)
        for worker in campaign["dispatch_payload"].get("workers", []):
            if isinstance(worker, dict):
                prompt = str(worker.get("prompt") or "").strip()
                if prompt and (bootstrap_brief or governance_brief):
                    worker["prompt"] = (
                        f"{prompt}\n\n"
                        "Bootstrap digest:\n"
                        f"{bootstrap_brief}\n\n"
                        "Governance evidence:\n"
                        f"{governance_brief}\n\n"
                        "Execution constraints:\n"
                        "- Start with findings from the bootstrap digest before exploring more files.\n"
                        "- Treat deterministic governance findings as primary evidence; challenge them only with concrete counter-evidence.\n"
                        "- If the governance decision is blocked, focus on blocking findings and required remediation first.\n"
                        "- Do not rerun project-source-loader or project-tree-scanner unless the bootstrap digest is missing critical evidence.\n"
                        "- During the first pass, read at most 3 targeted files with project-file-reader.\n"
                        "- Avoid shell-style exploration and focus on evidence-backed findings quickly.\n"
                        "- If you already have enough evidence for a finding, write it immediately instead of continuing broad exploration.\n"
                    )
                worker_context = worker.get("context")
                if isinstance(worker_context, dict):
                    worker_context["project_bootstrap"] = bootstrap
                    worker_context["governance_evidence"] = governance
                    worker_context["debate_plan"] = debate_plan
        for worker in campaign["dispatch_payload"].get("followup_workers", []):
            if isinstance(worker, dict):
                prompt = str(worker.get("prompt") or "").strip()
                if prompt and (bootstrap_brief or governance_brief):
                    worker["prompt"] = (
                        f"{prompt}\n\n"
                        "Bootstrap digest:\n"
                        f"{bootstrap_brief}\n\n"
                        "Governance evidence:\n"
                        f"{governance_brief}\n\n"
                        "Execution constraints:\n"
                        "- This is the rebuttal round, so begin from peer findings instead of rediscovering the repository.\n"
                        "- Use the bootstrap digest only to validate or challenge claims precisely.\n"
                        "- Re-check any peer claim against deterministic governance evidence and the risk score.\n"
                        "- Read at most 2 additional targeted files unless a claim is impossible to evaluate otherwise.\n"
                        f"- Stay within the moderator budget of approximately {debate_plan['debate_time_budget_minutes']} minutes for the whole debate.\n"
                    )
                worker_context = worker.get("context")
                if isinstance(worker_context, dict):
                    worker_context["project_bootstrap"] = bootstrap
                    worker_context["governance_evidence"] = governance
                    worker_context["debate_plan"] = debate_plan
        summary_agent = campaign.get("summary_agent", {})
        if isinstance(summary_agent, dict):
            summary_prompt = str(summary_agent.get("prompt") or "").strip()
            if summary_prompt and (bootstrap_brief or governance_brief):
                summary_agent["prompt"] = (
                    f"{summary_prompt}\n\n"
                    "Bootstrap digest:\n"
                    f"{bootstrap_brief}\n\n"
                    "Governance evidence:\n"
                    f"{governance_brief}\n\n"
                    "Debate time budget:\n"
                    f"- moderator_budget_minutes: {debate_plan['debate_time_budget_minutes']}\n"
                    f"- context_window_tokens: {debate_plan['model_context_window_tokens']}\n"
                    f"- context_pressure: {debate_plan['context_pressure']}\n\n"
                    "Execution constraints:\n"
                    "- Synthesize reviewer outputs first; do not restart broad repository exploration unless reviewer evidence is clearly insufficient.\n"
                    "- The final report must include the governance risk score and approval decision.\n"
                    "- If governance decision is blocked, preserve blocked status unless reviewers provide explicit evidence for a justified waiver.\n"
                    "- Prefer report generation over additional discovery once proposer/support/challenge relationships are established.\n"
                )
        summary_context = summary_agent.get("context")
        if isinstance(summary_context, dict):
            summary_context["project_bootstrap"] = bootstrap
            summary_context["governance_evidence"] = governance
            summary_context["debate_plan"] = debate_plan
        campaign["metrics"]["debate_time_budget_minutes"] = debate_plan["debate_time_budget_minutes"]
        campaign["metrics"]["cross_review_round_count"] = debate_plan["cross_review_round_count"]
        launch_workers = bool(campaign.get("launch_workers", True))
        dispatch_result: dict[str, Any] | None = None
        if launch_workers:
            coordinator_runtime = self._require_coordinator_runtime()
            dispatch_result = await coordinator_runtime.dispatch(
                payload=campaign["dispatch_payload"],
                context=asdict(context),
            )

        workers = dispatch_result.get("workers", []) if isinstance(dispatch_result, dict) else []
        dispatch_status = str(dispatch_result.get("status") or "").strip() if isinstance(dispatch_result, dict) else ""
        return {
            "status": "partial" if launch_workers else "completed",
            "summary": (
                f"Initialized code review debate campaign for '{campaign['project_source']['project_name']}' "
                f"and launched {len(workers)} reviewer worker session(s)."
                if launch_workers
                else f"Prepared code review debate campaign for '{campaign['project_source']['project_name']}'."
            ),
            "approval_decision": "pending_debate",
            "findings": [],
            "campaign": campaign,
            "review_plan": [
                "Normalize project source and scope",
                "Create review points for project or target paths",
                "Launch parallel round-1 reviewer sessions",
                "Launch parallel round-2 cross-review rebuttals",
                "Collect debated findings for summary synthesis",
                "Produce approval-ready structured report",
            ],
            "targets": [point["target"] for point in campaign["review_points"]],
            "change_summary": str(arguments.get("change_summary") or context.user_message).strip(),
            "dispatch": dispatch_result or {
                "status": "not_started",
                "summary": "Reviewer dispatch was skipped for this invocation.",
                "workers": [],
            },
            "bootstrap": bootstrap,
            "governance": governance,
            "next_steps": [
                "Review deterministic governance findings before broad LLM exploration",
                "Feed reviewer outputs into cross-review rounds and summary synthesis",
                "Persist debated findings and governance score into the report center",
            ],
            "metrics": {
                **campaign.get("metrics", {}),
                "bootstrap_ok": 1 if bootstrap.get("ok", False) else 0,
                "governance_score": int((governance.get("risk_score") or {}).get("score") or 0) if isinstance(governance, dict) else 0,
                "governance_finding_count": len(governance.get("findings", [])) if isinstance(governance, dict) else 0,
                "launched_worker_count": len(workers),
                "dispatch_started": launch_workers,
                "dispatch_status": dispatch_status or "not_started",
            },
        }

    async def _run_ui_automation_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._ui_automation_mode_runtime.handle(arguments, context)

    @property
    def ui_automation_mode_runtime(self) -> UIAutomationModeRuntime:
        """UI 自动化模式运行时引用（main.py 注入录制编排依赖用，P0-8）。"""
        return self._ui_automation_mode_runtime

    async def _run_api_test_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._api_testing_mode_runtime.handle(arguments, context)

    async def _run_compatibility_test_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._compatibility_testing_mode_runtime.handle(arguments, context)

    async def _run_security_scan_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._is_security_campaign_request(arguments):
            return await self._security_testing_mode_runtime.handle(arguments, context)
        return await self._execute_security_runner(arguments, context, "security-scan-runner")

    async def _run_security_tool_bootstrap(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        profile_key = str(arguments.get("profile_key") or "").strip()
        tool_name = str(arguments.get("tool_name") or "").strip()
        campaign_id = str(arguments.get("campaign_id") or "").strip()
        target_allowlist = [
            str(item).strip()
            for item in (arguments.get("target_allowlist") or [])
            if str(item).strip()
        ]
        plan = self._security_tool_bootstrap_service.resolve_plan(
            profile_key=profile_key,
            tool_name=tool_name,
        )
        if plan is None:
            return {
                "status": "denied",
                "ok": False,
                "summary": "Requested P4 tool/profile combination is not allowlisted.",
                "error": "package_not_allowlisted",
            }
        artifact_dir = self._prepare_local_artifact_dir(context, "security-tool-bootstrap")
        manifest = await self._security_tool_bootstrap_service.run(
            campaign_id=campaign_id,
            target_allowlist=target_allowlist,
            profile_key=profile_key,
            tool_name=tool_name,
            requested_version=str(arguments.get("requested_version") or "").strip(),
            approval_scope_hash=str(arguments.get("_p4_approval_scope_hash") or "").strip(),
            approval_granted=bool(arguments.get("_server_approval_granted")),
            artifact_dir=artifact_dir,
            timeout_seconds=arguments.get("timeout_seconds"),
        )
        payload = manifest.to_dict()
        success = manifest.status in {"already_available", "completed"}
        result = {
            "status": "completed" if success else "waiting_approval" if manifest.status == "waiting_approval" else "failed",
            "ok": success,
            "summary": (
                f"P4 tool {manifest.tool_name} is {manifest.status}."
                if success
                else manifest.failure_reason or f"P4 tool bootstrap {manifest.status}."
            ),
            "error": None if success else manifest.failure_category,
            "tool_bootstrap": payload,
            "artifacts": [
                {
                    "type": "security_tool_bootstrap_manifest",
                    "path": manifest.manifest_path,
                }
            ],
        }
        await self._promote_security_bootstrap_manifest_uri(
            result=result,
            manifest=manifest,
            context=context,
        )
        campaign_state = self.record_security_tool_bootstrap_campaign(
            campaign_id=campaign_id,
            target_allowlist=target_allowlist,
            manifest=payload,
            tool_summary=str(result.get("summary") or ""),
            tool_status=str(result.get("status") or ""),
            context=context,
        )
        report = campaign_state.report
        settlement = (
            campaign_state.campaign.settlement
            if campaign_state.campaign is not None
            else None
        )
        if report is not None:
            result["campaign_id"] = campaign_state.campaign.campaign_id if campaign_state.campaign else campaign_id
            result["report"] = report.model_dump(mode="json")
            result["report_markdown"] = campaign_state.report_markdown
            result["report_html"] = campaign_state.report_html
            result["verification_result"] = dict(campaign_state.verification_result)
            result["evaluation_result"] = dict(campaign_state.evaluation_result)
            result["settlement"] = settlement.model_dump(mode="json") if settlement is not None else {}
            result["security_campaign_projection"] = {
                "campaign_id": result["campaign_id"],
                "bootstrap_id": str(payload.get("bootstrap_id") or ""),
                "settlement_status": settlement.status if settlement is not None else "failed",
                "cleanup_complete": bool(settlement.cleanup_complete) if settlement is not None else False,
            }
            result["artifacts"] = self._merge_security_bootstrap_artifacts(
                list(result.get("artifacts") or []),
                list(campaign_state.artifacts or []),
            )
        return result

    @staticmethod
    def _merge_security_bootstrap_artifacts(
        bootstrap_artifacts: list[dict[str, Any]],
        report_artifacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Preserve manifest and report artifact payloads for one ToolJob upload."""
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in [*bootstrap_artifacts, *report_artifacts]:
            if not isinstance(item, dict):
                continue
            artifact = dict(item)
            identity = (
                str(artifact.get("type") or ""),
                str(artifact.get("filename") or ""),
                str(artifact.get("path") or ""),
            )
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(artifact)
        return merged

    async def _promote_security_bootstrap_manifest_uri(
        self,
        *,
        result: dict[str, Any],
        manifest: Any,
        context: ToolExecutionContext,
    ) -> None:
        """Persist the P4 manifest first, then keep every audit reference durable.

        Artifact storage may remove local files after upload. P4's manifest
        payload must therefore be updated to the durable URI rather than
        retaining a stale local filesystem path in reports or later campaign
        state.
        """
        if self._artifact_storage_service is None:
            return
        local_path = str(manifest.manifest_path or "").strip()
        if not local_path:
            return
        stored = await self._artifact_storage_service.store_output_artifacts(
            {
                "artifacts": [
                    {
                        "type": "security_tool_bootstrap_manifest",
                        "path": local_path,
                    }
                ]
            },
            session_id=context.session_id,
            turn_id=context.turn_id,
            tool_key="security-tool-bootstrap",
        )
        artifacts = stored.get("artifacts") if isinstance(stored, dict) else []
        durable = artifacts[0] if isinstance(artifacts, list) and artifacts else {}
        durable_path = str(durable.get("path") or "").strip() if isinstance(durable, dict) else ""
        if not durable_path:
            return
        manifest.manifest_path = durable_path
        local_manifest_path = Path(local_path)
        local_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        local_manifest_path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        refreshed = await self._artifact_storage_service.store_output_artifacts(
            {
                "artifacts": [
                    {
                        "type": "security_tool_bootstrap_manifest",
                        "path": local_path,
                    }
                ]
            },
            session_id=context.session_id,
            turn_id=context.turn_id,
            tool_key="security-tool-bootstrap",
        )
        payload = result.get("tool_bootstrap")
        if isinstance(payload, dict):
            payload["manifest_path"] = durable_path
        result["artifacts"] = (
            refreshed.get("artifacts")
            if isinstance(refreshed, dict) and isinstance(refreshed.get("artifacts"), list)
            else artifacts
        )

    async def _run_network_recon_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._execute_security_runner(arguments, context, "network-recon-runner")

    async def _run_web_scan_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._execute_security_runner(arguments, context, "web-scan-runner")

    async def _run_service_audit_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._execute_security_runner(arguments, context, "service-audit-runner")

    async def _run_credential_attack_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._execute_security_runner(arguments, context, "credential-attack-runner")

    async def _run_traffic_analysis_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._execute_security_runner(arguments, context, "traffic-analysis-runner")

    async def _run_exploit_workbench_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        return await self._execute_security_runner(arguments, context, "exploit-workbench-runner")

    def _is_security_campaign_request(self, arguments: dict[str, Any]) -> bool:
        worker_action = str(arguments.get("worker_action") or "").strip().lower()
        if worker_action in {"execute_security_task", "execute_task"}:
            return False
        if isinstance(arguments.get("task"), dict):
            return False
        if str(arguments.get("command_profile") or "").strip():
            return False
        return True

    async def _execute_security_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
        runner_key: str | None = None,
    ) -> dict[str, Any]:
        from src.application.security.finding_normalizer import FindingNormalizer
        from src.application.security.execution_monitor import SecurityExecutionMonitor
        from src.application.security.result_parsers import evaluate_parser_success, get_parser_registry
        from src.application.security.risk_policy import SecurityRiskPolicy
        from src.application.security.tool_catalog import SecurityToolCatalog

        runner_key = runner_key or "security-scan-runner"
        catalog = SecurityToolCatalog()
        risk_policy = SecurityRiskPolicy()
        execution_monitor = SecurityExecutionMonitor()
        normalizer = FindingNormalizer()

        task = self._security_task_from_arguments(arguments)
        profile_key = self._resolve_security_profile_key(arguments, task, runner_key)
        if not profile_key:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": "Security runner requires a command_profile or a serialized task.",
                "runner_key": runner_key,
                "error": "missing_command_profile",
            }

        profile = catalog.get_profile(profile_key)
        if profile is None:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": f"Unknown security command profile: {profile_key}",
                "runner_key": runner_key,
                "command_profile": profile_key,
                "error": "unknown_command_profile",
            }

        risk_tolerance = self._resolve_security_risk_tolerance(arguments, context)
        profile_allowed, monitor_reason = execution_monitor.profile_allowed_for_risk(
            profile.profile_key,
            risk_tolerance,
        )
        if not profile_allowed:
            return {
                "status": "denied",
                "ok": False,
                "success": False,
                "summary": monitor_reason,
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "risk_tolerance": risk_tolerance,
                "error": "profile_blocked_by_execution_monitor",
            }

        if not self._security_profile_matches_runner(profile.tool_family, runner_key):
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": f"Profile {profile.profile_key} belongs to {profile.tool_family}, not {runner_key}.",
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "error": "profile_runner_mismatch",
            }

        trusted_environment = str(context.context_bundle.get("environment") or "").strip()
        environment = str(
            trusted_environment or arguments.get("environment") or "testing"
        ).strip().lower()
        if not risk_policy.is_allowed_in_environment(profile.profile_key, environment):
            return {
                "status": "denied",
                "ok": False,
                "success": False,
                "summary": f"Profile {profile.profile_key} is blocked in the {environment} environment.",
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "environment": environment,
                "error": "profile_blocked_in_environment",
            }

        approval_granted = bool(arguments.get("_server_approval_granted"))
        if risk_policy.requires_approval(profile.profile_key) and not approval_granted:
            return {
                "status": "waiting_approval",
                "ok": False,
                "success": False,
                "summary": f"Profile {profile.profile_key} requires explicit approval before execution.",
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "risk_level": profile.risk_level,
                "approval_required": True,
                "error": "approval_required",
            }

        artifact_dir = self._prepare_local_artifact_dir(context, runner_key)
        try:
            render_args, missing = self._build_security_command_arguments(
                profile,
                arguments,
                task,
                artifact_dir,
            )
        except ValueError as exc:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": str(exc),
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "error": "invalid_profile_argument",
            }
        if missing:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": f"Missing required argument(s) for {profile.profile_key}: {', '.join(missing)}",
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "missing_arguments": missing,
                "error": "missing_profile_arguments",
            }

        if profile.profile_key == "free_command":
            if not self._security_free_command_enabled():
                return {
                    "status": "denied",
                    "ok": False,
                    "success": False,
                    "summary": "Controlled free commands are disabled by server configuration.",
                    "runner_key": runner_key,
                    "command_profile": profile.profile_key,
                    "error": "free_command_disabled",
                }
            free_command_error = self._validate_free_security_command(
                str(render_args.get("command") or ""),
                str(render_args.get("target") or ""),
            )
            if free_command_error:
                return {
                    "status": "denied",
                    "ok": False,
                    "success": False,
                    "summary": free_command_error,
                    "runner_key": runner_key,
                    "command_profile": profile.profile_key,
                    "error": "free_command_rejected",
                }

        preflight_error = self._security_profile_preflight(profile.profile_key, render_args)
        if preflight_error:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": preflight_error,
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "tool_name": profile.tool_name,
                "error": "security_profile_preflight_failed",
            }

        command = profile.command_template.format(**render_args)
        command_args = self._split_security_command(command)
        if not command_args:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": f"Profile {profile.profile_key} rendered an empty command.",
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "error": "empty_command",
            }

        executable = command_args[0]
        bootstrap_applied = False
        if self._security_tool_bootstrap_enabled() and profile.profile_key != "free_command":
            bootstrap = self._security_bootstrap_command(profile.tool_name, command, command_args)
            if bootstrap:
                command = bootstrap
                command_args = ["sh", "-lc", bootstrap]
                bootstrap_applied = True
        timeout_seconds = self._resolve_security_timeout_seconds(arguments, task, profile)
        try:
            execution_result = await self._security_execution_environment.execute(
                command=command,
                command_args=command_args,
                timeout_seconds=timeout_seconds,
                artifact_dir=artifact_dir,
                context=context,
            )
        except FileNotFoundError as exc:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": str(exc),
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "tool_name": profile.tool_name,
                "command": command,
                "error": "execution_environment_not_available",
            }
        except (RuntimeError, ValueError) as exc:
            return {
                "status": "failed",
                "ok": False,
                "success": False,
                "summary": str(exc),
                "runner_key": runner_key,
                "command_profile": profile.profile_key,
                "tool_name": profile.tool_name,
                "command": command,
                "error": "execution_environment_error",
            }

        stdout_text = execution_result.stdout or ""
        stderr_text = execution_result.stderr or ""
        exit_code = execution_result.exit_code
        timed_out = execution_result.timed_out
        raw_output = stdout_text if stdout_text.strip() else stderr_text
        parser_input = self._security_parser_input(profile.parser_key, raw_output, artifact_dir)
        parser_registry = get_parser_registry()
        parsed_result = parser_registry.parse(profile.parser_key, parser_input) if profile.parser_key else {}
        findings = [
            finding.model_dump(mode="json")
            for finding in normalizer.normalize_batch(
                profile.parser_key,
                parsed_result,
                task.task_id if task is not None else "",
            )
        ]
        success, semantic_error = evaluate_parser_success(
            profile.parser_key,
            parsed_result,
            exit_code=exit_code,
            timed_out=timed_out,
        )
        transcript = {
            "runner_key": runner_key,
            "profile_key": profile.profile_key,
            "tool_name": profile.tool_name,
            "command": command,
            "execution_backend": execution_result.backend,
            "container_name": execution_result.container_name,
            "rendered_arguments": render_args,
            "started_at": execution_result.started_at.isoformat(),
            "completed_at": execution_result.completed_at.isoformat(),
            "duration_seconds": max(0.0, (execution_result.completed_at - execution_result.started_at).total_seconds()),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "bootstrap_applied": bootstrap_applied,
            "stdout": stdout_text[-20000:],
            "stderr": stderr_text[-12000:],
        }
        transcript_path = artifact_dir / "security_tool_transcript.json"
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")

        summary = (
            f"Executed {profile.profile_key} with exit code {exit_code}; parsed {len(findings)} finding(s)."
            if not timed_out
            else f"Profile {profile.profile_key} timed out after {timeout_seconds:.0f}s."
        )
        return {
            "status": "completed" if success else "failed",
            "ok": success,
            "success": success,
            "summary": summary,
            "runner_key": runner_key,
            "command_profile": profile.profile_key,
            "tool_name": profile.tool_name,
            "command": command,
            "execution_backend": execution_result.backend,
            "container_name": execution_result.container_name,
            "target": render_args.get("target") or render_args.get("query") or "",
            "exit_code": exit_code,
            "timed_out": timed_out,
            "parsed_result": parsed_result,
            "findings": findings,
            "raw_output": raw_output[-10000:],
            "stderr": stderr_text[-4000:],
            "artifacts": [
                {"type": "security_tool_transcript", "path": str(transcript_path)},
                *execution_result.output_artifacts,
            ],
            "metrics": {
                "finding_count": len(findings),
                "stdout_chars": len(stdout_text),
                "stderr_chars": len(stderr_text),
                "duration_seconds": transcript["duration_seconds"],
                "bootstrap_applied": bootstrap_applied,
            },
            "error": None if success else (
                semantic_error
                or ("timeout" if timed_out else stderr_text[-500:] or f"exit_code={exit_code}")
            ),
            "semantic_success": success,
        }

    def _security_task_from_arguments(self, arguments: dict[str, Any]):
        from src.modes.security_testing_mode.campaign_state import SecurityTask

        raw_task = arguments.get("task")
        if not isinstance(raw_task, dict):
            return None
        try:
            return SecurityTask.model_validate(raw_task)
        except Exception:
            return None

    def _resolve_security_risk_tolerance(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> str:
        explicit = str(arguments.get("risk_tolerance") or "").strip().lower()
        if explicit:
            return explicit
        bundle = context.context_bundle if isinstance(context.context_bundle, dict) else {}
        direct = str(bundle.get("risk_tolerance") or "").strip().lower()
        if direct:
            return direct
        state = bundle.get("security_testing_state")
        if isinstance(state, dict):
            request = state.get("request")
            if isinstance(request, dict):
                risk = str(request.get("risk_tolerance") or "").strip().lower()
                if risk:
                    return risk
        return "medium"

    def _resolve_security_profile_key(
        self,
        arguments: dict[str, Any],
        task: Any,
        runner_key: str,
    ) -> str:
        explicit = str(arguments.get("command_profile") or "").strip()
        if explicit:
            return explicit
        if task is not None and str(task.command_profile or "").strip():
            return str(task.command_profile).strip()
        if runner_key == "network-recon-runner":
            return "nmap_tcp_basic"
        if runner_key == "web-scan-runner":
            return "httpx_probe"
        if runner_key == "service-audit-runner":
            return "sslscan_tls_audit"
        if runner_key == "credential-attack-runner":
            return "hydra_basic_login"
        if runner_key == "traffic-analysis-runner":
            return "tcpdump_timed_capture"
        if runner_key == "exploit-workbench-runner":
            return "searchsploit_exploit_lookup"
        target = str(arguments.get("target") or "").strip()
        return "httpx_probe" if target.startswith(("http://", "https://")) else "nmap_tcp_basic"

    def _resolve_security_timeout_seconds(
        self,
        arguments: dict[str, Any],
        task: Any,
        profile: Any,
    ) -> float:
        assigned = float(
            (task.timeout_seconds if task is not None else 0)
            or profile.timeout_seconds
            or 120
        )
        requested_raw = arguments.get("timeout_seconds")
        try:
            requested = float(requested_raw) if requested_raw not in (None, "") else assigned
        except (TypeError, ValueError):
            requested = assigned
        # A worker may shorten its own command budget, but it cannot expand a
        # server-assigned task/profile timeout by supplying a larger argument.
        return max(1.0, min(requested, assigned, 1800.0))

    def _security_profile_matches_runner(self, tool_family: str, runner_key: str) -> bool:
        if runner_key == "security-scan-runner":
            return True
        expected = {
            "network-recon-runner": {"network_recon"},
            "web-scan-runner": {"web_scan"},
            "service-audit-runner": {"service_audit"},
            "credential-attack-runner": {"credential_attack"},
            "traffic-analysis-runner": {"traffic_analysis"},
            "exploit-workbench-runner": {"exploit"},
        }.get(runner_key, set())
        return tool_family in expected

    def _build_security_command_arguments(
        self,
        profile: Any,
        arguments: dict[str, Any],
        task: Any,
        artifact_dir: Path,
    ) -> tuple[dict[str, Any], list[str]]:
        supplied: dict[str, Any] = {}
        nested = arguments.get("arguments")
        if isinstance(nested, dict):
            supplied.update(nested)
        for key in (
            "target",
            "ports",
            "query",
            "service",
            "userlist",
            "passlist",
            "duration_seconds",
            "packet_count",
            "module_name",
            "command",
        ):
            if arguments.get(key) not in (None, ""):
                supplied[key] = arguments.get(key)
        if task is not None:
            supplied.setdefault("target", task.target)
            if task.target_port:
                supplied.setdefault("ports", str(task.target_port))
        if profile.profile_key in {"searchsploit_lookup", "searchsploit_exploit_lookup"}:
            supplied.setdefault("query", supplied.get("target") or "")
        # Tools like sslscan only accept ``host[:port]`` targets, not full
        # URLs. Normalize before rendering so a target like
        # ``https://example.com/path`` becomes ``example.com:443``.
        if profile.tool_name in {"sslscan", "openssl", "testssl.sh"}:
            target_value = str(supplied.get("target") or "").strip()
            if target_value:
                supplied["target"] = self._normalize_tls_target(target_value)
        # Inject nuclei templates path so the command never falls back to
        # the implicit default that produces "no templates provided for
        # scan" when templates are not installed at the default location.
        if profile.profile_key in {"nuclei_baseline", "nuclei_cve_scan"}:
            supplied.setdefault("templates_dir", self._resolve_nuclei_templates_dir())
        output_root = self._security_output_root(artifact_dir)
        supplied.setdefault("wordlist", self._resolve_security_wordlist(output_root))
        supplied.setdefault("ffuf_output", self._security_output_path(output_root, "ffuf_result.json"))
        supplied.setdefault("hydra_output", self._security_output_path(output_root, "hydra_result.txt"))
        supplied.setdefault("sqlmap_output_dir", self._security_output_path(output_root, "sqlmap_out"))
        supplied.setdefault("ports", "1-1000")
        supplied.setdefault("duration_seconds", "30")
        supplied.setdefault("packet_count", "100")

        render_args: dict[str, Any] = {}
        missing: list[str] = []
        for key in profile.allowed_arguments:
            value = supplied.get(key)
            if value in (None, ""):
                missing.append(key)
                continue
            if profile.profile_key == "free_command" and key == "command":
                render_args[key] = str(value).replace("\r", "").replace("\n", "").strip()
            else:
                render_args[key] = self._sanitize_security_argument(str(value), allow_spaces=key == "query")
        return render_args, missing

    def _security_free_command_enabled(self) -> bool:
        value = getattr(self._settings, "security_runner_allow_free_command", False) if self._settings else False
        return bool(value)

    def _security_tool_bootstrap_enabled(self) -> bool:
        value = getattr(self._settings, "security_runner_tool_bootstrap", False) if self._settings else False
        return bool(value) and self._security_runner_backend() in {"docker", "container"}

    def _security_bootstrap_command(
        self,
        tool_name: str,
        command: str,
        command_args: list[str],
    ) -> str:
        package_by_tool = {
            "tcpdump": "tcpdump",
            "searchsploit": "exploitdb",
            "msfconsole": "metasploit-framework",
        }
        package = package_by_tool.get(str(tool_name or "").strip())
        if not package:
            return ""
        executable = shlex.quote(str(tool_name).strip())
        package_name = shlex.quote(package)
        rendered = command if command.strip() else shlex.join(command_args)
        return (
            f"if ! command -v {executable} >/dev/null 2>&1; then "
            f"apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y {package_name}; "
            f"fi; exec {rendered}"
        )

    def _validate_free_security_command(self, command: str, target: str) -> str:
        normalized = str(command or "").strip()
        explicit_target = str(target or "").strip().strip("'\"")
        if not normalized or len(normalized) > 4096:
            return "Controlled free command must contain between 1 and 4096 characters."
        if not explicit_target:
            return "Controlled free command requires an explicit target for allowlist enforcement."
        try:
            argv = self._split_security_command(normalized)
        except ValueError:
            return "Controlled free command could not be parsed safely."
        allowed_executables = {
            "nmap", "httpx", "whatweb", "nuclei", "sslscan", "searchsploit",
            "tcpdump", "curl", "wget", "python3",
        }
        executable = Path(argv[0]).name.lower() if argv else ""
        if executable not in allowed_executables:
            return f"Executable '{executable or 'unknown'}' is not allowed for controlled free commands."
        target_host = self._normalize_tls_target(explicit_target).split(":", 1)[0]
        if explicit_target not in normalized and target_host not in normalized:
            return "Controlled free command must reference its explicit authorized target."
        denied_fragments = (
            "rm -rf", "mkfs", "shutdown", "reboot", "poweroff", "dd if=",
            ">/dev/", "docker.sock", "--privileged", "curl | sh", "wget | sh",
        )
        lowered = normalized.lower().replace(" ", "")
        for fragment in denied_fragments:
            if fragment.replace(" ", "") in lowered:
                return f"Controlled free command contains denied destructive pattern '{fragment}'."
        return ""

    @staticmethod
    def _normalize_tls_target(target: str) -> str:
        """Reduce a URL-style target to ``host[:port]`` for TLS scanners.

        Examples:
        - ``https://example.com/path?query`` -> ``example.com:443``
        - ``http://example.com:8080/a/b`` -> ``example.com:8080``
        - ``example.com`` -> ``example.com``
        - ``example.com:8443`` -> ``example.com:8443``
        """
        from urllib.parse import urlparse

        candidate = target.strip()
        if "://" in candidate:
            parsed = urlparse(candidate)
            host = parsed.hostname or ""
            port = parsed.port
            if not port:
                scheme = (parsed.scheme or "").lower()
                if scheme == "https":
                    port = 443
                elif scheme == "http":
                    port = 80
            if host:
                return f"{host}:{port}" if port else host
            # Fall back to whatever was after the scheme.
            candidate = parsed.netloc or candidate
        # Strip any trailing path/query if URL parser did not catch it.
        for separator in ("/", "?", "#"):
            if separator in candidate:
                candidate = candidate.split(separator, 1)[0]
        return candidate

    def _resolve_nuclei_templates_dir(self) -> str:
        custom = str(os.getenv("NUCLEI_TEMPLATES_DIR", "") or "").strip()
        if custom:
            return custom
        # Default to a common docker workdir mount point so docker backend
        # users without an env override still get a deterministic path
        # rather than an implicit, image-dependent default.
        backend = self._security_runner_backend()
        if backend in {"docker", "container"}:
            return "/root/nuclei-templates"
        for candidate in (
            "/root/nuclei-templates",
            "/home/kali/nuclei-templates",
            "/usr/share/nuclei-templates",
        ):
            if Path(candidate).exists():
                return candidate
        # Last-resort placeholder; preflight will surface the real error.
        return "/root/nuclei-templates"

    def _security_output_root(self, artifact_dir: Path) -> str:
        backend = self._security_runner_backend()
        if backend in {"docker", "container"}:
            return str(
                getattr(self._settings, "security_runner_docker_workdir", None)
                or os.getenv("SECURITY_RUNNER_DOCKER_WORKDIR")
                or "/work"
            ).strip() or "/work"
        return str(artifact_dir)

    def _security_output_path(self, root: str, name: str) -> str:
        if root.startswith("/"):
            return f"{root.rstrip('/')}/{name}"
        return str(Path(root) / name)

    def _resolve_security_wordlist(self, output_root: str) -> str:
        custom = str(os.getenv("SECURITY_WORDLIST_PATH", "") or "").strip()
        if custom:
            return custom
        if output_root.startswith("/"):
            return "/usr/share/seclists/Discovery/Web-Content/common.txt"
        for candidate in (
            "/usr/share/seclists/Discovery/Web-Content/common.txt",
            "/usr/share/wordlists/dirb/common.txt",
        ):
            if Path(candidate).exists():
                return candidate
        return "/usr/share/seclists/Discovery/Web-Content/common.txt"

    def _security_profile_preflight(self, profile_key: str, render_args: dict[str, Any]) -> str:
        wordlist_profiles = {"ffuf_common_dirs", "gobuster_dirs"}
        if profile_key in wordlist_profiles:
            wordlist = str(render_args.get("wordlist") or "").strip().strip("'\"")
            if wordlist and not wordlist.startswith("/work/") and not Path(wordlist).exists():
                return (
                    f"Required wordlist for {profile_key} was not found: {wordlist}. "
                    "Configure SECURITY_WORDLIST_PATH or install Seclists."
                )
        if profile_key in {"nuclei_baseline", "nuclei_cve_scan"} and self._security_runner_backend() in {"local", "host"}:
            if not self._security_nuclei_templates_available():
                return (
                    "Nuclei templates were not found for local execution. "
                    "Configure NUCLEI_TEMPLATES_DIR or install nuclei-templates before running this profile."
                )
        return ""

    def _security_runner_backend(self) -> str:
        value = getattr(self._settings, "security_runner_backend", None) if self._settings is not None else None
        if value not in (None, ""):
            return str(value).strip().lower()
        return str(os.getenv("SECURITY_RUNNER_BACKEND", "local") or "local").strip().lower()

    def _security_nuclei_templates_available(self) -> bool:
        custom = str(os.getenv("NUCLEI_TEMPLATES_DIR", "") or "").strip()
        candidates = [custom] if custom else []
        candidates.extend(
            [
                "/root/nuclei-templates",
                "/home/kali/nuclei-templates",
                "/usr/share/nuclei-templates",
            ]
        )
        return any(Path(candidate).exists() for candidate in candidates if candidate)

    def _sanitize_security_argument(self, value: str, *, allow_spaces: bool = False) -> str:
        cleaned = value.replace("\r", "").replace("\n", "").strip()
        if not allow_spaces and any(ch.isspace() for ch in cleaned):
            raise ValueError("Security command arguments may not contain whitespace.")
        return shlex.quote(cleaned)

    def _security_parser_input(self, parser_key: str, raw_output: str, artifact_dir: Path) -> str:
        if not parser_key:
            return raw_output

        preferred_outputs = {
            "ffuf": ("ffuf_result.json",),
            "hydra": ("hydra_result.txt",),
        }
        search_roots = [
            artifact_dir,
            artifact_dir.parent / "_security_runner_work",
            artifact_dir.parent,
        ]
        for file_name in preferred_outputs.get(parser_key, ()):
            for root in search_roots:
                candidate = root / file_name
                if not candidate.exists() or not candidate.is_file():
                    continue
                try:
                    content = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if content.strip():
                    return content
        return raw_output

    def _split_security_command(self, command: str) -> list[str]:
        return shlex.split(command, posix=os.name != "nt")

    async def _run_performance_test_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._performance_testing_mode_runtime is None:
            return {
                "status": "error",
                "ok": False,
                "summary": "性能测试运行时尚未初始化，请检查系统配置。",
                "phase": "init_failed",
                "errors": ["PerformanceTestingModeRuntime not initialized"],
            }
        return await self._performance_testing_mode_runtime.handle(arguments, context)

    async def _run_perf_plan_compiler(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
        from src.application.performance.k6_engine_adapter import K6EngineAdapter
        from src.modes.performance_testing_mode.plan_state import PerfPlan

        plan_data = arguments.get("plan", {})
        engine_key = arguments.get("engine", "k6")

        try:
            plan = PerfPlan(**plan_data)
        except Exception as e:
            return {"status": "error", "ok": False, "summary": f"计划解析失败: {e}"}

        rewrite = self._settings.orchestration.performance_rewrite_localhost if self._settings else True
        image = self._resolve_perf_engine_image(engine_key) if self._settings else ""
        if engine_key == "k6":
            adapter = K6EngineAdapter(
                image=image,
                rewrite_localhost=rewrite,
            )
        elif engine_key == "jmeter":
            adapter = JMeterEngineAdapter(
                image=image,
                rewrite_localhost=rewrite,
            )
        else:
            return {"status": "error", "ok": False, "summary": f"不支持的引擎: {engine_key}"}

        script = adapter.build_script(plan)
        return {
            "status": "ok",
            "ok": True,
            "script_content": script.script_content,
            "filename": script.filename,
            "data_files": script.data_files,
            "engine": script.engine,
        }

    def _resolve_perf_engine_image(self, engine: str) -> str:
        from src.application.images.image_resolver import ImageResolver

        normalized = (engine or "k6").lower().strip()
        resolver = ImageResolver()
        image_key = (
            self._settings.orchestration.jmeter_docker_image_key
            if normalized == "jmeter"
            else self._settings.orchestration.k6_docker_image_key
        )
        resolution = resolver.resolve_by_key(image_key) if image_key else resolver.resolve_for_engine(normalized)
        if resolution.ok:
            return resolution.selected_image
        return self._settings.orchestration.jmeter_docker_image if normalized == "jmeter" else self._settings.orchestration.k6_docker_image

    async def _run_perf_container_manager(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._settings is None:
            return {"status": "error", "ok": False, "summary": "系统配置未初始化"}

        from src.application.performance.perf_container_manager import PerfContainerManager

        action = str(arguments.get("action") or "status").lower().strip()
        engine = str(arguments.get("engine") or "k6").lower().strip()
        container_name = str(arguments.get("container_name") or "").strip()

        manager = PerfContainerManager(self._settings)
        try:
            if action == "start":
                result = await manager.start(engine, container_name)
            elif action in {"stop", "destroy"}:
                if not container_name:
                    return {"status": "error", "ok": False, "summary": "stop/destroy 需要 container_name"}
                result = await manager.stop(engine, container_name)
            elif action == "status":
                if not container_name:
                    return {"status": "error", "ok": False, "summary": "status 需要 container_name"}
                result = await manager.status(engine, container_name)
            elif action == "cleanup":
                result = await manager.cleanup(engine if engine in {"k6", "jmeter"} else "")
            else:
                return {"status": "error", "ok": False, "summary": f"不支持的容器动作: {action}"}
        except Exception as e:
            return {"status": "error", "ok": False, "summary": f"容器管理失败: {e}"}

        payload = result.model_dump()
        payload["status"] = "ok" if result.ok else "error"
        if action == "start" and result.ok and self._session_resource_service is not None:
            await self._session_resource_service.register(
                session_id=context.session_id,
                kind=SessionResourceKind.docker_container,
                resource_key=result.container_name,
                metadata={
                    "engine": result.engine,
                    "image": result.image,
                    "tool_key": context.tool_key or "perf-container-manager",
                    "call_id": context.call_id,
                    "tool_job_id": context.tool_job_id,
                    "turn_id": context.turn_id,
                },
            )
        elif action in {"stop", "destroy"} and result.ok and self._session_resource_service is not None:
            await self._session_resource_service.mark_released(
                session_id=context.session_id,
                kind=SessionResourceKind.docker_container,
                resource_key=result.container_name,
                reason=f"perf-container-manager:{action}",
            )
        return payload

    async def _run_api_docs_ingest(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._api_docs_service is None:
            return {"status": "error", "ok": False, "summary": "API 文档服务未初始化"}

        from src.application.api_doc_resolution.service import ApiDocResolutionService

        action = str(arguments.get("action") or "").lower().strip()
        resolution = ApiDocResolutionService(api_docs_service=self._api_docs_service)

        try:
            project_name, project_url = ResourceAccessPolicy().resolve_api_doc_filters(
                arguments=arguments,
                context=context.context_bundle,
            )
            if action == "import_url":
                url = str(arguments.get("url") or "").strip()
                if not url:
                    return {"status": "error", "ok": False, "summary": "import_url 需要 url 参数"}
                result = await resolution.ingest_doc_from_url(
                    url=url,
                    title=arguments.get("title"),
                    project_name=project_name,
                    project_url=project_url,
                )
                return result
            elif action == "import_attachment":
                filename = str(arguments.get("filename") or "api_doc.json").strip()
                content_base64 = str(arguments.get("content_base64") or "").strip()
                if not content_base64:
                    return {"status": "error", "ok": False, "summary": "import_attachment 需要 content_base64 参数"}
                result = await resolution.ingest_doc_from_attachment(
                    filename=filename,
                    content_base64=content_base64,
                    title=arguments.get("title"),
                    project_name=project_name,
                    project_url=project_url,
                    content_type=arguments.get("content_type"),
                )
                return result
            else:
                return {"status": "error", "ok": False, "summary": f"不支持的导入动作: {action}"}
        except Exception as e:
            return {"status": "error", "ok": False, "summary": f"API 文档导入失败: {e}"}

    async def _run_perf_engine_select(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        from src.application.images.image_resolver import ImageResolver

        scenario = str(arguments.get("scenario") or "api_baseline").lower().strip()
        user_engine = str(arguments.get("user_requested_engine") or "").lower().strip() or None
        needs_complex_flow = bool(arguments.get("needs_complex_flow"))
        has_existing_jmx = bool(arguments.get("has_existing_jmx"))

        if user_engine:
            engine = user_engine
            reason = f"用户指定引擎: {engine}"
        elif has_existing_jmx:
            engine = "jmeter"
            reason = "已有 JMX 资产，优先使用 JMeter"
        elif needs_complex_flow or scenario in ("complex_flow",):
            engine = "jmeter"
            reason = f"复杂流程场景 ({scenario}) 优先使用 JMeter"
        else:
            engine = "k6"
            reason = f"API 场景 ({scenario}) 默认使用 k6"

        resolver = ImageResolver()
        resolution = resolver.resolve_for_engine(engine)
        if not resolution.ok:
            return {
                "ok": False,
                "engine": engine,
                "reason": resolution.reason,
                "selected_image_key": "",
                "selected_image": "",
            }

        return {
            "ok": True,
            "engine": engine,
            "reason": reason,
            "selected_image_key": resolution.selected_image_key,
            "selected_image": resolution.selected_image,
        }

    async def _run_http_probe_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        from src.application.performance.http_probe_runner import HttpProbeRunner

        target_url = str(arguments.get("target_url") or "").strip()
        if not target_url:
            return {"status": "error", "ok": False, "summary": "target_url 必填"}

        runner = HttpProbeRunner(self._settings)
        result = await runner.probe(
            target_url=target_url,
            method=str(arguments.get("method") or "GET"),
            headers=arguments.get("headers") or {},
            timeout_seconds=int(arguments.get("timeout_seconds") or 10),
        )
        payload = result.model_dump()
        payload["status"] = "ok" if result.ok else "error"
        return payload

    async def _run_mock_target_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        from src.application.performance.mock_target_runner import MockTargetRunner

        action = str(arguments.get("action") or "").lower().strip()
        runner = MockTargetRunner(self._settings)

        if action == "start":
            result = await runner.start(
                port=int(arguments.get("port") or 18080),
                response_text=str(arguments.get("response_text") or "ok"),
            )
        elif action == "stop":
            container_name = str(arguments.get("container_name") or "").strip()
            if not container_name:
                return {"status": "error", "ok": False, "summary": "stop 需要 container_name"}
            result = await runner.stop(container_name=container_name)
        else:
            return {"status": "error", "ok": False, "summary": f"不支持的动作: {action}"}

        payload = result.model_dump()
        payload["status"] = "ok" if result.ok else "error"
        if action == "start" and result.ok and self._session_resource_service is not None:
            await self._session_resource_service.register(
                session_id=context.session_id,
                kind=SessionResourceKind.docker_container,
                resource_key=result.container_name,
                metadata={
                    "engine": "helper",
                    "image": result.selected_image,
                    "tool_key": context.tool_key or "mock-target-runner",
                    "call_id": context.call_id,
                    "tool_job_id": context.tool_job_id,
                    "turn_id": context.turn_id,
                    "port": result.port,
                },
            )
        elif action == "stop" and result.ok and self._session_resource_service is not None:
            await self._session_resource_service.mark_released(
                session_id=context.session_id,
                kind=SessionResourceKind.docker_container,
                resource_key=result.container_name,
                reason="mock-target-runner:stop",
            )
        return payload

    async def _run_performance_engine_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        from src.application.performance.performance_engine_runner import PerformanceEngineRunner
        from src.modes.performance_testing_mode.plan_state import PerfPlan

        engine = str(arguments.get("engine") or "k6").lower().strip()
        scenario = str(arguments.get("scenario") or "api_baseline").lower().strip()

        runner = PerformanceEngineRunner(self._settings)
        plan_data = arguments.get("plan")
        if plan_data and isinstance(plan_data, dict):
            try:
                plan = PerfPlan(**plan_data)
                result = runner.validate_plan_engine(plan)
            except Exception:
                result = runner.select_engine(engine=engine, scenario=scenario)
        else:
            result = runner.select_engine(engine=engine, scenario=scenario)

        payload = result.model_dump()
        payload["status"] = "ok" if result.ok else "error"
        return payload

    async def _run_perf_result_analyzer(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        from src.application.performance.engine_adapter import RawMetrics
        from src.modes.performance_testing_mode.plan_state import PerfMetrics, PerfPlan, PerfRun
        from src.modes.performance_testing_mode.report_builder import PerfReportBuilder
        from src.modes.performance_testing_mode.result_parser import PerfResultParser

        try:
            raw = RawMetrics(**arguments.get("raw_metrics", {}))
            plan = PerfPlan(**arguments.get("plan", {}))
            run = PerfRun(**arguments.get("run", {}))
        except Exception as e:
            return {"status": "error", "ok": False, "summary": f"参数解析失败: {e}"}

        baseline_data = arguments.get("baseline_metrics")
        baseline = PerfMetrics(**baseline_data) if baseline_data else None

        parser = PerfResultParser()
        parsed = parser.parse(raw)

        builder = PerfReportBuilder()
        report = builder.build(parsed, plan, run, baseline)

        return {
            "status": "ok",
            "ok": True,
            "verdict": report.verdict,
            "metrics": report.metrics.model_dump(),
            "sla_result": report.sla_result.model_dump(),
            "error_breakdown": report.error_breakdown.model_dump(),
            "engine_threshold_crosscheck": report.engine_threshold_crosscheck.model_dump(),
            "baseline_comparison": report.baseline_comparison.model_dump() if report.baseline_comparison else None,
            "load_side_observations": report.load_side_observations,
            "report_markdown": report.report_markdown,
            "report_html": report.report_html,
        }

    async def _run_smoke_suite_runner(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._smoke_testing_mode_runtime is None:
            return {
                "status": "failed",
                "ok": False,
                "summary": "Smoke testing runtime is not configured.",
                "error": "smoke_runtime_not_configured",
            }
        return await self._smoke_testing_mode_runtime.handle(arguments, context)

    def _build_placeholder_mode_result(
        self,
        mode_key: str,
        summary: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        request_context_key = f"{mode_key}_request"
        request_context = (
            context.context_bundle.get(request_context_key)
            if isinstance(context.context_bundle.get(request_context_key), dict)
            else {}
        )
        return {
            "status": "partial",
            "summary": summary,
            "mode_key": mode_key,
            "objective": str(arguments.get("objective") or request_context.get("objective") or context.user_message).strip(),
            "recognized_intent": dict(context.context_bundle.get("mode_intent") or {}),
            "request": request_context,
            "next_steps": [
                "Replace placeholder runtime with dedicated mode executor",
                "Attach verification and evaluation policies",
            ],
        }

    async def _collect_local_source_info(
        self,
        project_source,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        root = resolve_local_project_root(project_source)
        if not root.exists():
            raise FileNotFoundError(f"Project root was not found: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Project root is not a directory: {root}")

        top_entries = [
            {
                "name": item.name,
                "kind": "directory" if item.is_dir() else "file",
            }
            for item in sorted(root.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower()))[:25]
            if not is_ignored_project_path(item.name)
        ]
        git_root = ""
        branch = project_source.branch
        git_check = await self._run_subprocess("git", ["-C", str(root), "rev-parse", "--show-toplevel"], timeout_seconds=timeout_seconds)
        is_git_repo = git_check["ok"]
        if is_git_repo:
            git_root = git_check["stdout"].strip()
            if not branch:
                branch_result = await self._run_subprocess(
                    "git",
                    ["-C", git_root, "branch", "--show-current"],
                    timeout_seconds=timeout_seconds,
                )
                branch = branch_result["stdout"].strip() if branch_result["ok"] else ""

        return {
            "access_mode": "local",
            "root_path": str(root),
            "exists": True,
            "is_directory": True,
            "is_git_repo": is_git_repo,
            "git_root": git_root,
            "branch": branch,
            "top_entries": top_entries,
        }

    async def _build_code_review_bootstrap(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        source_result = await self._run_project_source_loader(arguments, context)
        tree_result = await self._run_project_tree_scanner(arguments, context)
        diff_result = await self._run_project_diff_reader(
            {
                **arguments,
                "max_chars": arguments.get("max_chars") or 12000,
            },
            context,
        )
        source_ok = source_result.get("ok", True)
        tree_ok = tree_result.get("ok", True)
        diff_ok = diff_result.get("ok", True)
        project_source = source_result.get("project_source") or normalize_project_source(arguments).model_dump(mode="python")
        return {
            "ok": bool(source_ok and tree_ok),
            "project_source": project_source,
            "source_info": source_result.get("source_info"),
            "tree_summary": tree_result.get("tree_summary"),
            "sample_files": tree_result.get("sample_files", []),
            "diff_summary": {
                "ok": diff_ok,
                "diff_mode": diff_result.get("diff_mode"),
                "commit_range": diff_result.get("commit_range"),
                "status_lines": diff_result.get("status_lines", []),
                "diff_stat": diff_result.get("diff_stat"),
                "diff_text": diff_result.get("diff_text"),
                "truncated": diff_result.get("truncated", False),
                "error": diff_result.get("error"),
            },
            "errors": {
                "source": source_result.get("error"),
                "tree": tree_result.get("error"),
                "diff": diff_result.get("error"),
            },
        }

    def _build_code_review_bootstrap_brief(self, bootstrap: dict[str, Any]) -> str:
        if not isinstance(bootstrap, dict):
            return ""

        project_source = bootstrap.get("project_source") if isinstance(bootstrap.get("project_source"), dict) else {}
        source_info = bootstrap.get("source_info") if isinstance(bootstrap.get("source_info"), dict) else {}
        tree_summary = bootstrap.get("tree_summary") if isinstance(bootstrap.get("tree_summary"), dict) else {}
        diff_summary = bootstrap.get("diff_summary") if isinstance(bootstrap.get("diff_summary"), dict) else {}
        sample_files = bootstrap.get("sample_files") if isinstance(bootstrap.get("sample_files"), list) else []

        project_name = str(project_source.get("project_name") or source_info.get("root_path") or "Unnamed Project").strip()
        branch = str(source_info.get("branch") or project_source.get("branch") or "unknown").strip()
        scanned_file_count = int(tree_summary.get("scanned_file_count") or 0)
        directory_count = int(tree_summary.get("directory_count") or 0)
        top_modules = [
            str(item).strip()
            for item in (tree_summary.get("top_modules") if isinstance(tree_summary.get("top_modules"), list) else [])
            if str(item).strip()
        ][:6]
        sample_paths: list[str] = []
        for item in sample_files[:6]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("relative_path") or "").strip()
            if path:
                sample_paths.append(path)
        diff_stat = str(diff_summary.get("diff_stat") or "").strip()
        status_lines = [
            str(item).strip()
            for item in (diff_summary.get("status_lines") if isinstance(diff_summary.get("status_lines"), list) else [])
            if str(item).strip()
        ][:5]

        lines = [
            f"- project_name: {project_name}",
            f"- branch: {branch}",
            f"- scanned_files: {scanned_file_count}",
            f"- directories: {directory_count}",
            f"- top_modules: {', '.join(top_modules) if top_modules else 'unknown'}",
            f"- diff_stat: {diff_stat or 'no diff summary'}",
            f"- changed_paths: {', '.join(status_lines) if status_lines else 'none captured'}",
            f"- sampled_files: {', '.join(sample_paths) if sample_paths else 'none captured'}",
        ]
        return "\n".join(lines)

    def _build_code_governance_brief(self, governance: dict[str, Any]) -> str:
        if not isinstance(governance, dict):
            return ""
        decision = governance.get("decision") if isinstance(governance.get("decision"), dict) else {}
        risk_score = governance.get("risk_score") if isinstance(governance.get("risk_score"), dict) else {}
        metrics = governance.get("metrics") if isinstance(governance.get("metrics"), dict) else {}
        findings = governance.get("findings") if isinstance(governance.get("findings"), list) else []
        top_findings: list[str] = []
        for item in findings[:8]:
            if not isinstance(item, dict):
                continue
            finding_id = str(item.get("id") or "").strip()
            severity = str(item.get("severity") or "").strip()
            category = str(item.get("category") or "").strip()
            title = str(item.get("title") or "").strip()
            location = str(item.get("file_path") or "").strip()
            top_findings.append(
                f"{finding_id} [{severity}/{category}] {title}"
                + (f" @ {location}" if location else "")
            )
        lines = [
            f"- decision: {decision.get('status', governance.get('approval_decision', 'unknown'))}",
            f"- decision_reason: {decision.get('reason', governance.get('summary', ''))}",
            f"- risk_score: {risk_score.get('score', 'n/a')}",
            f"- risk_level: {risk_score.get('level', 'n/a')}",
            f"- finding_count: {metrics.get('finding_count', len(findings))}",
            f"- changed_file_count: {metrics.get('changed_file_count', 'n/a')}",
            f"- blocking_findings: {', '.join(decision.get('blocking_findings', []) or []) or 'none'}",
        ]
        if top_findings:
            lines.append("- top_findings:")
            lines.extend(f"  - {item}" for item in top_findings)
        return "\n".join(lines)

    def _resolve_code_review_debate_plan(
        self,
        arguments: dict[str, Any],
        bootstrap: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        requested_budget = _clamp_int(
            arguments.get("debate_time_budget_minutes"),
            minimum=5,
            maximum=60,
            default=0,
        )
        legacy_round_request = _clamp_int(
            arguments.get("cross_review_rounds"),
            minimum=1,
            maximum=4,
            default=0,
        )
        if legacy_round_request <= 0:
            legacy_round_request = _clamp_int(
                arguments.get("debate_round_count"),
                minimum=1,
                maximum=4,
                default=0,
            )
        if requested_budget <= 0 and legacy_round_request > 0:
            # Backward compatibility for older callers that still drive the
            # debate by round count. We translate the legacy round request into
            # a moderator time budget, then continue through the same
            # time-budget path as newer callers.
            requested_budget = {
                1: 15,
                2: 28,
                3: 42,
                4: 55,
            }.get(legacy_round_request, 28)
        tree_summary = bootstrap.get("tree_summary") if isinstance(bootstrap.get("tree_summary"), dict) else {}
        diff_summary = bootstrap.get("diff_summary") if isinstance(bootstrap.get("diff_summary"), dict) else {}

        scanned_file_count = int(tree_summary.get("scanned_file_count") or 0)
        directory_count = int(tree_summary.get("directory_count") or 0)
        changed_path_count = len(diff_summary.get("status_lines", [])) if isinstance(diff_summary.get("status_lines"), list) else 0

        reviewer_count = _clamp_int(arguments.get("reviewer_count"), minimum=2, maximum=5, default=3)
        model_config = self._resolve_code_review_model_config(arguments, context)
        context_window_tokens = int(model_config.context_window) if model_config is not None else 8192
        supports_reasoning = bool(model_config.supports_reasoning) if model_config is not None else False

        if context_window_tokens <= 8192:
            context_pressure = "high"
            context_round_cap = 2
            context_budget_penalty = 8
        elif context_window_tokens <= 32768:
            context_pressure = "medium"
            context_round_cap = 3
            context_budget_penalty = 3
        else:
            context_pressure = "low"
            context_round_cap = 4
            context_budget_penalty = 0

        if scanned_file_count <= 80:
            project_size = "small"
            base_budget = 12
        elif scanned_file_count <= 200:
            project_size = "medium"
            base_budget = 22
        elif scanned_file_count <= 500:
            project_size = "large"
            base_budget = 34
        else:
            project_size = "xlarge"
            base_budget = 46

        complexity_bonus = min(8, directory_count // 12)
        diff_bonus = min(6, max(0, changed_path_count // 8))
        reviewer_bonus = max(0, reviewer_count - 3)
        reasoning_bonus = 4 if supports_reasoning else 0

        auto_budget = base_budget + complexity_bonus + diff_bonus + reviewer_bonus + reasoning_bonus - context_budget_penalty
        effective_budget = requested_budget if requested_budget > 0 else auto_budget
        debate_time_budget_minutes = max(8, min(60, effective_budget))

        if debate_time_budget_minutes <= 15:
            derived_rounds = 1
        elif debate_time_budget_minutes <= 28:
            derived_rounds = 2
        elif debate_time_budget_minutes <= 42:
            derived_rounds = 3
        else:
            derived_rounds = 4
        cross_review_round_count = min(context_round_cap, derived_rounds)

        return {
            "strategy": "time_budget",
            "requested_time_budget_minutes": requested_budget,
            "debate_time_budget_minutes": debate_time_budget_minutes,
            "cross_review_round_count": cross_review_round_count,
            "project_size": project_size,
            "scanned_file_count": scanned_file_count,
            "directory_count": directory_count,
            "changed_path_count": changed_path_count,
            "reviewer_count": reviewer_count,
            "model_context_window_tokens": context_window_tokens,
            "supports_reasoning": supports_reasoning,
            "context_pressure": context_pressure,
        }

    def _resolve_code_review_model_config(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ModelConfigRecord | None:
        if self._model_registry is None:
            return None
        candidate_keys = [
            str(arguments.get("worker_model_key") or "").strip(),
            str(arguments.get("summary_model_key") or "").strip(),
            str(context.selected_model_key or "").strip(),
            str(context.context_bundle.get("preferred_model") or "").strip(),
        ]
        for model_key in candidate_keys:
            if not model_key:
                continue
            try:
                return self._model_registry.get_runtime_config(model_key)
            except Exception:
                continue
        try:
            return self._model_registry.get_default_runtime_config()
        except Exception:
            return None

    async def _collect_ssh_source_info(
        self,
        project_source,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        root_value = project_source_root(project_source).replace("\\", "/")
        script = (
            f"ROOT={shlex.quote(root_value)}; "
            'if [ ! -d "$ROOT" ]; then echo "__ERROR__:missing_root"; exit 4; fi; '
            'echo "__TOP__"; '
            'find "$ROOT" -mindepth 1 -maxdepth 1 | head -n 25; '
            'echo "__TOP_END__"; '
            'if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then '
            'echo "__GIT__"; '
            'git -C "$ROOT" rev-parse --show-toplevel; '
            'git -C "$ROOT" branch --show-current 2>/dev/null; '
            'else echo "__NO_GIT__"; fi'
        )
        result = await self._run_ssh_shell(project_source, script, timeout_seconds=timeout_seconds)
        if not result["ok"]:
            raise RuntimeError(result["stderr"].strip() or result["stdout"].strip() or "SSH source probe failed.")

        top_entries: list[dict[str, Any]] = []
        is_git_repo = False
        git_root = ""
        branch = project_source.branch
        section = ""
        git_lines: list[str] = []
        for raw_line in result["stdout"].splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line == "__TOP__":
                section = "top"
                continue
            if line == "__TOP_END__":
                section = ""
                continue
            if line == "__GIT__":
                section = "git"
                is_git_repo = True
                continue
            if line == "__NO_GIT__":
                section = ""
                continue
            if line.startswith("__ERROR__:"):
                raise RuntimeError(line.removeprefix("__ERROR__:"))
            if section == "top":
                if is_ignored_project_path(PurePosixPath(line).name):
                    continue
                top_entries.append(
                    {
                        "name": PurePosixPath(line).name,
                        "kind": "unknown",
                        "path": line,
                    }
                )
            elif section == "git":
                git_lines.append(line)

        if git_lines:
            git_root = git_lines[0]
        if len(git_lines) > 1 and not branch:
            branch = git_lines[1]

        return {
            "access_mode": "ssh",
            "root_path": root_value,
            "exists": True,
            "is_directory": True,
            "is_git_repo": is_git_repo,
            "git_root": git_root,
            "branch": branch,
            "top_entries": top_entries,
            "ssh_target": self._build_ssh_target(project_source),
        }

    async def _scan_local_project_tree(
        self,
        project_source,
        max_depth: int,
        max_files: int,
        ignored_names: set[str] | None = None,
    ) -> dict[str, Any]:
        root = resolve_local_project_root(project_source)
        if not root.exists():
            raise FileNotFoundError(f"Project root was not found: {root}")
        ignored_names = set(ignored_names or DEFAULT_IGNORED_NAMES)

        sample_files: list[dict[str, Any]] = []
        extension_counter: Counter[str] = Counter()
        language_counter: Counter[str] = Counter()
        top_modules: set[str] = set()
        directories_seen: set[str] = set()
        scanned_files = 0
        scan_truncated = False

        for dirpath, dirnames, filenames in os.walk(root):
            current_path = Path(dirpath)
            relative_dir = current_path.relative_to(root).as_posix() if current_path != root else "."
            depth = 0 if relative_dir == "." else len(PurePosixPath(relative_dir).parts)
            dirnames[:] = [
                item
                for item in dirnames
                if item.lower() not in ignored_names
                and not is_ignored_project_path(current_path.joinpath(item), ignored_names)
                and depth < max_depth
            ]
            directories_seen.add(relative_dir)
            if relative_dir != ".":
                top_modules.add(relative_dir.split("/")[0])
            if depth > max_depth:
                continue
            for filename in sorted(filenames):
                full_path = current_path / filename
                relative_path = full_path.relative_to(root).as_posix()
                if is_ignored_project_path(relative_path, ignored_names):
                    continue
                try:
                    size_bytes = full_path.stat().st_size
                except OSError:
                    continue
                extension = full_path.suffix.lower() or "<none>"
                extension_counter[extension] += 1
                language_counter[_language_from_extension(extension)] += 1
                scanned_files += 1
                if len(sample_files) < 50:
                    sample_files.append(
                        {
                            "path": relative_path,
                            "extension": extension,
                            "size_bytes": size_bytes,
                        }
                    )
                if scanned_files >= max_files:
                    scan_truncated = True
                    break
            if scan_truncated:
                break

        top_entries = [
            {
                "name": item.name,
                "kind": "directory" if item.is_dir() else "file",
            }
            for item in sorted(root.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower()))[:25]
            if not is_ignored_project_path(item.name, ignored_names)
        ]
        return {
            "access_mode": "local",
            "root_path": str(root),
            "max_depth": max_depth,
            "scan_truncated": scan_truncated,
            "scanned_file_count": scanned_files,
            "directory_count": len(directories_seen),
            "top_modules": sorted(top_modules)[:50],
            "top_entries": top_entries,
            "extensions": dict(extension_counter.most_common(20)),
            "languages": dict(language_counter.most_common(15)),
            "sample_files": sample_files,
            "ignored_paths": sorted(ignored_names),
        }

    async def _scan_ssh_project_tree(
        self,
        project_source,
        max_depth: int,
        max_files: int,
        timeout_seconds: float,
        ignored_names: set[str] | None = None,
    ) -> dict[str, Any]:
        root_value = project_source_root(project_source).replace("\\", "/")
        ignored_names = set(ignored_names or DEFAULT_IGNORED_NAMES)
        prune_clause = " -o ".join(f"-name {shlex.quote(item)}" for item in sorted(ignored_names))
        script = (
            f"ROOT={shlex.quote(root_value)}; "
            'if [ ! -d "$ROOT" ]; then echo "__ERROR__:missing_root"; exit 4; fi; '
            'echo "__TOP__"; '
            'find "$ROOT" -mindepth 1 -maxdepth 1 | head -n 25; '
            'echo "__TOP_END__"; '
            'echo "__FILES__"; '
            f'find "$ROOT" -maxdepth {max_depth} \\( {prune_clause} \\) -prune -o -type f -print | head -n {max_files}; '
            'echo "__FILES_END__"'
        )
        result = await self._run_ssh_shell(project_source, script, timeout_seconds=timeout_seconds)
        if not result["ok"]:
            raise RuntimeError(result["stderr"].strip() or result["stdout"].strip() or "SSH tree scan failed.")

        top_entries: list[dict[str, Any]] = []
        sample_files: list[dict[str, Any]] = []
        extension_counter: Counter[str] = Counter()
        language_counter: Counter[str] = Counter()
        top_modules: set[str] = set()
        section = ""
        for raw_line in result["stdout"].splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line == "__TOP__":
                section = "top"
                continue
            if line == "__TOP_END__":
                section = ""
                continue
            if line == "__FILES__":
                section = "files"
                continue
            if line == "__FILES_END__":
                section = ""
                continue
            if line.startswith("__ERROR__:"):
                raise RuntimeError(line.removeprefix("__ERROR__:"))
            if section == "top":
                if PurePosixPath(line).name.lower() in ignored_names:
                    continue
                top_entries.append(
                    {
                        "name": PurePosixPath(line).name,
                        "kind": "unknown",
                        "path": line,
                    }
                )
            elif section == "files":
                relative_path = _relativize_remote_path(root_value, line)
                extension = PurePosixPath(relative_path).suffix.lower() or "<none>"
                extension_counter[extension] += 1
                language_counter[_language_from_extension(extension)] += 1
                sample_files.append({"path": relative_path, "extension": extension})
                if relative_path and "/" in relative_path:
                    top_modules.add(relative_path.split("/")[0])

        return {
            "access_mode": "ssh",
            "root_path": root_value,
            "max_depth": max_depth,
            "scan_truncated": len(sample_files) >= max_files,
            "scanned_file_count": len(sample_files),
            "directory_count": len(top_entries),
            "top_modules": sorted(top_modules)[:50],
            "top_entries": top_entries,
            "extensions": dict(extension_counter.most_common(20)),
            "languages": dict(language_counter.most_common(15)),
            "sample_files": sample_files[:50],
            "ssh_target": self._build_ssh_target(project_source),
            "ignored_paths": sorted(ignored_names),
        }

    def _read_local_project_file(
        self,
        project_source,
        file_path: str,
        start_line: int,
        end_line: int,
        max_chars: int,
    ) -> dict[str, Any]:
        resolved = resolve_local_project_file(project_source, file_path)
        if not resolved.exists():
            raise FileNotFoundError(f"File was not found: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"Target path is not a file: {resolved}")

        text = resolved.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total_lines = len(lines)
        bounded_end = min(end_line, total_lines if total_lines > 0 else end_line)
        selected_lines = lines[start_line - 1:bounded_end]
        content = "\n".join(selected_lines)
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]

        return {
            "content": content,
            "file": {
                "path": resolved.relative_to(resolve_local_project_root(project_source)).as_posix(),
                "absolute_path": str(resolved),
                "start_line": start_line,
                "end_line": bounded_end,
                "total_lines": total_lines,
                "truncated": truncated,
            },
        }

    async def _read_ssh_project_file(
        self,
        project_source,
        file_path: str,
        start_line: int,
        end_line: int,
        max_chars: int,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        remote_path = _join_remote_path(project_source_root(project_source), file_path)
        script = (
            f"FILE={shlex.quote(remote_path)}; "
            'if [ ! -f "$FILE" ]; then echo "__ERROR__:missing_file"; exit 4; fi; '
            'printf "__LINES__=%s\n" "$(wc -l < "$FILE" 2>/dev/null || echo 0)"; '
            'echo "__CONTENT__"; '
            f"sed -n '{start_line},{end_line}p' \"$FILE\""
        )
        result = await self._run_ssh_shell(project_source, script, timeout_seconds=timeout_seconds)
        if not result["ok"]:
            raise RuntimeError(result["stderr"].strip() or result["stdout"].strip() or "SSH file read failed.")

        total_lines = 0
        content_lines: list[str] = []
        capture = False
        for raw_line in result["stdout"].splitlines():
            line = raw_line.rstrip("\n")
            if line.startswith("__ERROR__:"):
                raise RuntimeError(line.removeprefix("__ERROR__:"))
            if line.startswith("__LINES__="):
                total_lines = int(line.removeprefix("__LINES__=") or "0")
                continue
            if line == "__CONTENT__":
                capture = True
                continue
            if capture:
                content_lines.append(raw_line)

        content = "\n".join(content_lines)
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]

        return {
            "content": content,
            "file": {
                "path": _relativize_remote_path(project_source_root(project_source), remote_path),
                "absolute_path": remote_path,
                "start_line": start_line,
                "end_line": min(end_line, total_lines if total_lines > 0 else end_line),
                "total_lines": total_lines,
                "truncated": truncated,
            },
        }

    async def _read_local_project_diff(
        self,
        project_source,
        arguments: dict[str, Any],
        timeout_seconds: float,
        max_chars: int,
    ) -> dict[str, Any]:
        root = resolve_local_project_root(project_source)
        git_root_result = await self._run_subprocess("git", ["-C", str(root), "rev-parse", "--show-toplevel"], timeout_seconds=timeout_seconds)
        if not git_root_result["ok"]:
            raise RuntimeError("Local project source is not a git repository.")
        git_root = git_root_result["stdout"].strip()

        diff_mode = str(arguments.get("diff_mode") or "").strip().lower()
        commit_range = str(arguments.get("commit_range") or project_source.commit_range or "").strip()
        paths = _to_string_list(arguments.get("paths") or arguments.get("targets"))

        status_result = await self._run_subprocess("git", ["-C", git_root, "status", "--short"], timeout_seconds=timeout_seconds)
        diff_stat_args = self._build_git_diff_args(git_root, commit_range, diff_mode, paths, stat_only=True)
        diff_args = self._build_git_diff_args(git_root, commit_range, diff_mode, paths, stat_only=False)
        diff_stat_result = await self._run_subprocess("git", diff_stat_args, timeout_seconds=timeout_seconds)
        diff_result = await self._run_subprocess("git", diff_args, timeout_seconds=timeout_seconds)
        diff_text, truncated = _truncate_text(diff_result["stdout"], max_chars)

        return {
            "git_root": git_root,
            "commit_range": commit_range,
            "diff_mode": diff_mode or ("range" if commit_range else "working_tree"),
            "status_lines": [line for line in status_result["stdout"].splitlines() if line.strip()],
            "diff_stat": diff_stat_result["stdout"].strip(),
            "diff_text": diff_text,
            "truncated": truncated,
            "paths": paths,
        }

    async def _read_ssh_project_diff(
        self,
        project_source,
        arguments: dict[str, Any],
        timeout_seconds: float,
        max_chars: int,
    ) -> dict[str, Any]:
        root_value = project_source_root(project_source).replace("\\", "/")
        diff_mode = str(arguments.get("diff_mode") or "").strip().lower()
        commit_range = str(arguments.get("commit_range") or project_source.commit_range or "").strip()
        paths = _to_string_list(arguments.get("paths") or arguments.get("targets"))
        path_clause = ""
        if paths:
            path_clause = " --" + "".join(f" {shlex.quote(item)}" for item in paths)
        range_clause = f" {shlex.quote(commit_range)}" if commit_range else ""
        cached_clause = " --cached" if diff_mode == "staged" else ""
        script = (
            f"ROOT={shlex.quote(root_value)}; "
            'if ! command -v git >/dev/null 2>&1; then echo "__ERROR__:git_missing"; exit 5; fi; '
            'if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then echo "__ERROR__:not_git_repo"; exit 6; fi; '
            'echo "__STATUS__"; '
            'git -C "$ROOT" status --short; '
            'echo "__STAT__"; '
            f'git -C "$ROOT" diff --stat{cached_clause}{range_clause}{path_clause}; '
            'echo "__DIFF__"; '
            f'git -C "$ROOT" diff --unified=3{cached_clause}{range_clause}{path_clause}'
        )
        result = await self._run_ssh_shell(project_source, script, timeout_seconds=timeout_seconds)
        if not result["ok"]:
            raise RuntimeError(result["stderr"].strip() or result["stdout"].strip() or "SSH diff read failed.")

        status_lines: list[str] = []
        diff_stat_lines: list[str] = []
        diff_lines: list[str] = []
        section = ""
        for raw_line in result["stdout"].splitlines():
            line = raw_line.rstrip("\n")
            if line.startswith("__ERROR__:"):
                raise RuntimeError(line.removeprefix("__ERROR__:"))
            if line == "__STATUS__":
                section = "status"
                continue
            if line == "__STAT__":
                section = "stat"
                continue
            if line == "__DIFF__":
                section = "diff"
                continue
            if section == "status":
                status_lines.append(line)
            elif section == "stat":
                diff_stat_lines.append(line)
            elif section == "diff":
                diff_lines.append(raw_line)

        diff_text, truncated = _truncate_text("\n".join(diff_lines), max_chars)
        return {
            "git_root": root_value,
            "commit_range": commit_range,
            "diff_mode": diff_mode or ("range" if commit_range else "working_tree"),
            "status_lines": [line for line in status_lines if line.strip()],
            "diff_stat": "\n".join(diff_stat_lines).strip(),
            "diff_text": diff_text,
            "truncated": truncated,
            "paths": paths,
            "ssh_target": self._build_ssh_target(project_source),
        }

    def _build_git_diff_args(
        self,
        git_root: str,
        commit_range: str,
        diff_mode: str,
        paths: list[str],
        *,
        stat_only: bool,
    ) -> list[str]:
        args = ["-C", git_root, "diff"]
        if stat_only:
            args.append("--stat")
        else:
            args.append("--unified=3")
        normalized_mode = diff_mode or ("range" if commit_range else "working_tree")
        if normalized_mode == "staged":
            args.append("--cached")
        elif commit_range:
            args.append(commit_range)
        if paths:
            args.append("--")
            args.extend(paths)
        return args

    async def _run_ssh_shell(
        self,
        project_source,
        remote_script: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        target = self._build_ssh_target(project_source)
        if not target:
            raise ValueError("SSH project source requires at least a host.")
        args = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", str(project_source.ssh.port or 22)]
        auth_ref = str(project_source.ssh.auth_ref or "").strip()
        if auth_ref:
            args.extend(["-i", auth_ref])
        args.extend([target, f"sh -lc {shlex.quote(remote_script)}"])
        return await self._run_subprocess("ssh", args, timeout_seconds=timeout_seconds)

    async def _run_subprocess(
        self,
        executable: str,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [shutil.which(executable) or executable, *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            stdout_text = completed.stdout or ""
            stderr_text = completed.stderr or ""
            exit_code = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout_text = exc.stdout or ""
            stderr_text = exc.stderr or ""
            exit_code = -1
            timed_out = True
        return {
            "ok": exit_code == 0 and not timed_out,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }

    def _build_ssh_target(self, project_source) -> str:
        host = str(project_source.ssh.host or "").strip()
        username = str(project_source.ssh.username or "").strip()
        if not host:
            return ""
        return f"{username}@{host}" if username else host

    def _api_doc_summary(
        self,
        document,
        *,
        include_preview: bool,
        max_chars: int,
    ) -> dict[str, Any]:
        item = document.model_dump(mode="json") if hasattr(document, "model_dump") else dict(document)
        preview = str(item.get("preview_text") or "")
        if include_preview and preview:
            item["preview_text"] = preview[:max_chars]
            item["preview_truncated"] = bool(item.get("preview_truncated")) or len(preview) > max_chars
        else:
            item.pop("preview_text", None)
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            item["metadata"] = {
                "original_filename": metadata.get("original_filename"),
                "converted_to_markdown": metadata.get("converted_to_markdown"),
                "project_url": metadata.get("project_url"),
            }
        return item

    def _require_api_docs_service(self) -> ApiDocsService:
        if self._api_docs_service is None:
            raise RuntimeError("API docs service is not configured.")
        return self._api_docs_service

    def _require_mcp_runtime(self) -> MCPRuntimeService:
        if self._mcp_runtime_service is None:
            raise RuntimeError("MCP runtime service is not configured.")
        return self._mcp_runtime_service

    def _require_session_store(self) -> SessionStore:
        if self._session_store is None:
            raise RuntimeError("Session store is not configured.")
        return self._session_store

    def _require_memory_runtime(self) -> MemoryRuntimeService:
        if self._memory_runtime_service is None:
            raise RuntimeError("Memory runtime service is not configured.")
        return self._memory_runtime_service

    def _require_coordinator_runtime(self):
        if self._coordinator_runtime_service is None:
            raise RuntimeError("Coordinator runtime service is not configured.")
        return self._coordinator_runtime_service

    def _resolve_cli_cwd(self, requested_cwd: Any) -> Path:
        if isinstance(requested_cwd, str) and requested_cwd.strip():
            candidate = Path(requested_cwd.strip())
            resolved = candidate.resolve() if candidate.is_absolute() else (self._workspace_root / candidate).resolve()
        else:
            resolved = self._workspace_root.resolve()
        workspace_root = self._workspace_root.resolve()
        if workspace_root != resolved and workspace_root not in resolved.parents:
            raise RuntimeError(f"CLI Executor cwd must stay within the workspace root: {workspace_root}")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _build_cli_invocation(self, shell_name: str, command: str) -> tuple[str, list[str]]:
        if shell_name in {"powershell", "pwsh"}:
            executable = "powershell" if shell_name == "powershell" else "pwsh"
            return executable, ["-Command", command]
        if shell_name in {"cmd", "command"}:
            return "cmd", ["/c", command]
        if shell_name in {"bash", "sh"}:
            executable = "bash" if shell_name == "bash" else "sh"
            return executable, ["-lc", command]
        return "powershell", ["-Command", command]

    def _prepare_local_artifact_dir(self, context: ToolExecutionContext, tool_key: str) -> Path:
        settings = self._settings or Settings()
        artifact_root = Path(__file__).resolve().parents[2] / settings.storage.artifact_root_dir
        artifact_root.mkdir(parents=True, exist_ok=True)
        session_id = _slug(context.session_id or "session")
        turn_id = _slug(context.turn_id or datetime.utcnow().strftime("%Y%m%d%H%M%S"))
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        artifact_dir = artifact_root / session_id / turn_id / f"{tool_key}_{timestamp}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir

    # ─── mail-* tool handlers ───────────────────────────────────────────

    async def _run_mail_status(self, tool_input: dict, context) -> dict:
        return self._mail_service.status()

    async def _run_mail_send(self, tool_input: dict, context) -> dict:
        content = str(tool_input.get("content") or "").strip()
        content_markdown = str(tool_input.get("content_markdown") or "").strip()
        content_html = str(tool_input.get("content_html") or "").strip()
        if content_markdown and not content_html:
            content_html = self._report_template_service.render_report_html(
                title=str(tool_input.get("subject") or "QA Report").strip() or "QA Report",
                time_label=str(
                    tool_input.get("time_label")
                    or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                ),
                sender=str(
                    tool_input.get("sender")
                    or getattr(context, "selected_agent_key", "")
                    or "Enterprise AI QA Agent"
                ).strip(),
                markdown_content=content_markdown,
                template_key=str(tool_input.get("template_key") or "default").strip() or "default",
                template_context=(
                    tool_input.get("template_context")
                    if isinstance(tool_input.get("template_context"), dict)
                    else {}
                ),
            )
        if not content:
            content = content_markdown
        if not content and not content_html:
            raise ValueError("mail-send requires content, content_markdown, or content_html.")
        return self._mail_service.send(
            recipients=tool_input["to"],
            subject=tool_input["subject"],
            content=content,
            content_html=content_html,
        )

    async def _run_mail_confirm(self, tool_input: dict, context) -> dict:
        return self._mail_service.confirm(
            operation=tool_input["operation"],
            confirmation_token=tool_input["confirmation_token"],
        )

    async def _run_mail_list(self, tool_input: dict, context) -> dict:
        opts = {}
        if tool_input.get("folder"):
            opts["dir"] = tool_input["folder"]
        if tool_input.get("limit"):
            opts["limit"] = tool_input["limit"]
        if tool_input.get("before"):
            opts["before"] = tool_input["before"]
        if tool_input.get("after"):
            opts["after"] = tool_input["after"]
        return self._mail_service.list_messages(
            options=opts or None,
        )

    async def _run_mail_read(self, tool_input: dict, context) -> dict:
        return self._mail_service.read_message(
            tool_input["message_id"],
        )

    async def _run_mail_search(self, tool_input: dict, context) -> dict:
        opts = None
        if tool_input.get("limit"):
            opts = {"limit": tool_input["limit"]}
        return self._mail_service.search_messages(
            tool_input["query"],
            options=opts,
        )

    async def _run_mail_reply(self, tool_input: dict, context) -> dict:
        request = MailSendRequest(
            recipients=[],
            subject="",
            content=tool_input["content"],
            content_html=tool_input.get("content_html", ""),
        )
        return self._mail_service.reply(
            tool_input["message_id"],
            request,
        )

    async def _run_mail_forward(self, tool_input: dict, context) -> dict:
        request = MailSendRequest(
            recipients=tool_input["to"],
            subject="",
            content=tool_input.get("comment", ""),
            content_html="",
        )
        return self._mail_service.forward(
            tool_input["message_id"],
            request,
        )

    async def _run_mail_download_attachment(self, tool_input: dict, context) -> dict:
        return self._mail_service.download_attachment(
            message_id=tool_input["message_id"],
            attachment_id=tool_input["attachment_id"],
        )

    async def _run_mail_trash(self, tool_input: dict, context) -> dict:
        return self._mail_service.trash(
            tool_input["message_id"],
        )

    def _normalize_result(
        self,
        tool: ToolDescriptor,
        result: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        normalized = dict(result)
        explicit_status = str(normalized.get("status") or "").strip().lower()
        if "artifacts" not in normalized or not isinstance(normalized.get("artifacts"), list):
            normalized["artifacts"] = []
        if "metrics" not in normalized or not isinstance(normalized.get("metrics"), dict):
            normalized["metrics"] = {}
        if "summary" not in normalized or not str(normalized.get("summary") or "").strip():
            normalized["summary"] = f"Tool '{tool.key}' completed."
        if explicit_status in {"failed", "denied"} and "ok" not in normalized:
            normalized["ok"] = False
        elif "ok" not in normalized:
            normalized["ok"] = True
        if not normalized["ok"] and "error" not in normalized:
            normalized["error"] = normalized.get("summary")
        normalized.setdefault("trace_id", context.trace_id)
        return normalized

    def _compact_tool_output_for_model(self, tool_key: str, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            return result
        if tool_key == "code-governance-runner":
            return self._compact_governance_output(result)
        if tool_key == "code-review-orchestrator":
            compact = {
                key: result.get(key)
                for key in (
                    "status",
                    "ok",
                    "summary",
                    "approval_decision",
                    "review_plan",
                    "targets",
                    "change_summary",
                    "next_steps",
                    "metrics",
                    "trace_id",
                )
                if key in result
            }
            governance = result.get("governance")
            if isinstance(governance, dict):
                compact["governance"] = self._compact_governance_output(governance)
            bootstrap = result.get("bootstrap")
            if isinstance(bootstrap, dict):
                compact["bootstrap"] = self._compact_code_review_bootstrap_output(bootstrap)
            campaign = result.get("campaign")
            if isinstance(campaign, dict):
                compact["campaign"] = self._compact_code_review_campaign_output(campaign)
            dispatch = result.get("dispatch")
            if isinstance(dispatch, dict):
                compact["dispatch"] = {
                    "status": dispatch.get("status"),
                    "summary": dispatch.get("summary"),
                    "worker_count": len(dispatch.get("workers", [])) if isinstance(dispatch.get("workers"), list) else 0,
                }
            return compact
        return result

    def _compact_governance_output(self, result: dict[str, Any]) -> dict[str, Any]:
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        code_graph = result.get("code_graph") if isinstance(result.get("code_graph"), dict) else {}
        graph_metrics = code_graph.get("metrics") if isinstance(code_graph.get("metrics"), dict) else {}
        report_json = result.get("report_json") if isinstance(result.get("report_json"), dict) else {}
        report_diff = report_json.get("diff") if isinstance(report_json.get("diff"), dict) else {}
        report_project = report_json.get("project") if isinstance(report_json.get("project"), dict) else {}
        return {
            "status": result.get("status"),
            "ok": result.get("ok"),
            "summary": result.get("summary"),
            "approval_decision": result.get("approval_decision"),
            "decision": result.get("decision"),
            "risk_score": result.get("risk_score"),
            "metrics": metrics,
            "changed_files": self._limit_dict_list(result.get("changed_files"), limit=80),
            "findings": self._limit_dict_list(result.get("findings"), limit=20),
            "scanner_runs": self._limit_dict_list(result.get("scanner_runs"), limit=20),
            "code_graph": {
                "status": code_graph.get("status"),
                "summary": code_graph.get("summary"),
                "metrics": graph_metrics,
                "impacted_node_ids": self._limit_list(code_graph.get("impacted_node_ids"), limit=40),
            },
            "report_json": {
                "project": report_project,
                "decision": result.get("decision"),
                "risk_score": result.get("risk_score"),
                "metrics": metrics,
                "diff": {
                    "mode": report_diff.get("mode"),
                    "stat": self._truncate_text(report_diff.get("stat"), max_chars=3000),
                    "truncated": report_diff.get("truncated"),
                },
            },
            "report_markdown": self._truncate_text(result.get("report_markdown"), max_chars=5000),
            "artifacts": self._strip_artifact_content(result.get("artifacts")),
            "model_output_compacted": True,
            "full_output_available_in_tool_job": True,
        }

    def _compact_code_review_bootstrap_output(self, bootstrap: dict[str, Any]) -> dict[str, Any]:
        source_info = bootstrap.get("source_info") if isinstance(bootstrap.get("source_info"), dict) else {}
        diff_summary = bootstrap.get("diff_summary") if isinstance(bootstrap.get("diff_summary"), dict) else {}
        tree_summary = bootstrap.get("tree_summary") if isinstance(bootstrap.get("tree_summary"), dict) else {}
        return {
            "ok": bootstrap.get("ok"),
            "errors": bootstrap.get("errors"),
            "source_info": {
                "access_mode": source_info.get("access_mode"),
                "root_path": source_info.get("root_path"),
                "git_root": source_info.get("git_root"),
                "branch": source_info.get("branch"),
                "is_git_repo": source_info.get("is_git_repo"),
                "top_entries": self._limit_dict_list(source_info.get("top_entries"), limit=20),
            },
            "diff_summary": {
                "ok": diff_summary.get("ok"),
                "diff_mode": diff_summary.get("diff_mode"),
                "diff_stat": self._truncate_text(diff_summary.get("diff_stat"), max_chars=3000),
                "changed_file_count": len(diff_summary.get("changed_files", [])) if isinstance(diff_summary.get("changed_files"), list) else 0,
                "changed_files": self._limit_dict_list(diff_summary.get("changed_files"), limit=80),
                "truncated": diff_summary.get("truncated"),
                "error": diff_summary.get("error"),
            },
            "tree_summary": {
                "ok": tree_summary.get("ok"),
                "scanned_file_count": tree_summary.get("scanned_file_count"),
                "directory_count": tree_summary.get("directory_count"),
                "file_count": tree_summary.get("file_count"),
                "language_counts": tree_summary.get("language_counts"),
                "ignored_count": tree_summary.get("ignored_count"),
            },
        }

    def _compact_code_review_campaign_output(self, campaign: dict[str, Any]) -> dict[str, Any]:
        summary_agent = campaign.get("summary_agent") if isinstance(campaign.get("summary_agent"), dict) else {}
        dispatch_payload = campaign.get("dispatch_payload") if isinstance(campaign.get("dispatch_payload"), dict) else {}
        return {
            "summary": campaign.get("summary"),
            "project_source": campaign.get("project_source"),
            "review_points": self._limit_dict_list(campaign.get("review_points"), limit=20),
            "review_team": self._limit_dict_list(campaign.get("review_team"), limit=10),
            "metrics": campaign.get("metrics"),
            "debate_plan": campaign.get("debate_plan"),
            "launch_workers": campaign.get("launch_workers"),
            "dispatch_payload": {
                "worker_count": len(dispatch_payload.get("workers", [])) if isinstance(dispatch_payload.get("workers"), list) else 0,
                "followup_worker_count": len(dispatch_payload.get("followup_workers", [])) if isinstance(dispatch_payload.get("followup_workers"), list) else 0,
            },
            "summary_agent": {
                "agent_key": summary_agent.get("agent_key"),
                "model_key": summary_agent.get("model_key"),
                "description": summary_agent.get("description"),
            },
        }

    def _strip_artifact_content(self, artifacts: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not isinstance(artifacts, list):
            return result
        for item in artifacts[:20]:
            if not isinstance(item, dict):
                continue
            compact = {key: value for key, value in item.items() if key != "content"}
            if "content" in item:
                compact["content_omitted"] = True
                compact["content_chars"] = len(str(item.get("content") or ""))
            result.append(compact)
        return result

    def _limit_dict_list(self, value: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value[:limit] if isinstance(item, dict)]

    def _limit_list(self, value: Any, *, limit: int) -> list[Any]:
        if not isinstance(value, list):
            return []
        return value[:limit]

    def _truncate_text(self, value: Any, *, max_chars: int) -> str:
        text = str(value or "")
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}\n...[truncated {len(text) - max_chars} chars]"

    def _collect_available_attachments(self, context: ToolExecutionContext, session) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        for item in context.context_bundle.get("attachments") or []:
            if isinstance(item, dict):
                attachments.append(dict(item))
        if attachments:
            return attachments

        for message in reversed(getattr(session, "messages", []) or []):
            role = getattr(message.role, "value", str(message.role))
            if role != "user":
                continue
            metadata = dict(getattr(message, "metadata", {}) or {})
            for item in metadata.get("attachments") or []:
                if isinstance(item, dict):
                    attachments.append(dict(item))
            if attachments:
                return attachments
        return attachments

    def _select_attachment_for_read(
        self,
        arguments: dict[str, Any],
        attachments: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        requested_id = str(arguments.get("attachment_id") or "").strip()
        requested_uri = str(arguments.get("uri") or "").strip()
        requested_name = str(arguments.get("name") or "").strip().lower()

        def matches(item: dict[str, Any]) -> bool:
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
            attachment_id = str(metadata.get("attachment_id") or item.get("id") or "").strip()
            item_uri = str(item.get("uri") or "").strip()
            item_name = str(item.get("name") or "").strip().lower()
            if requested_id and attachment_id == requested_id:
                return True
            if requested_uri and item_uri == requested_uri:
                return True
            if requested_name and item_name == requested_name:
                return True
            return False

        for item in attachments:
            if matches(item):
                return item
        return attachments[0] if attachments else None

    def _attachment_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
        security = self._attachment_security_report(item)
        return {
            "attachment_id": str(metadata.get("attachment_id") or item.get("id") or "").strip(),
            "name": str(item.get("name") or "").strip(),
            "uri": str(item.get("uri") or "").strip(),
            "content_type": str(item.get("content_type") or "").strip(),
            "source": str(metadata.get("source") or "").strip(),
            "uploaded_at": str(metadata.get("uploaded_at") or "").strip(),
            "size_bytes": metadata.get("size_bytes"),
            "security_decision": str(security.get("decision") or "").strip(),
            "security_risk_score": security.get("risk_score"),
        }

    def _attachment_security_report(self, item: dict[str, Any]) -> dict[str, Any]:
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
        security = metadata.get("security")
        return security if isinstance(security, dict) else {}

    def _attachment_read_allowed(self, item: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        security = self._attachment_security_report(item)
        decision = str(security.get("decision") or "").strip().lower()
        if decision and decision != "allow":
            return False, security

        uri = str(item.get("uri") or "").strip()
        if uri.startswith("rustfs://"):
            bucket = self._attachment_bucket_from_uri(uri)
            if self._settings is not None and bucket in {
                self._settings.storage.rustfs_upload_temp_bucket,
                self._settings.storage.rustfs_upload_quarantine_bucket,
            }:
                return False, security
        return True, security

    def _attachment_bucket_from_uri(self, uri: str) -> str:
        raw = uri.removeprefix("rustfs://")
        bucket, _, _ = raw.partition("/")
        return bucket

    def _decode_attachment_bytes(
        self,
        *,
        content: bytes,
        content_type: str,
        filename: str,
        max_chars: int,
    ) -> tuple[str, str]:
        if not content:
            return "", "empty_attachment"

        text_like = (
            content_type.startswith("text/")
            or content_type in {
                "application/json",
                "application/xml",
                "application/javascript",
                "application/x-yaml",
                "application/yaml",
            }
            or Path(filename).suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml", ".xml", ".csv", ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".vue", ".html", ".css"}
        )
        if not text_like:
            return "", "binary_attachment"

        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1"):
            try:
                decoded = content.decode(encoding)
                break
            except UnicodeDecodeError:
                decoded = ""
        if not decoded:
            return "", "attachment_decode_failed"
        normalized = decoded.replace("\r\n", "\n").strip()
        truncated, _ = _truncate_text(normalized, max_chars)
        return truncated, ""

    def _attachment_content_format(self, filename: str, content_type: str) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix == ".md":
            return "markdown"
        if suffix in {".json"} or content_type == "application/json":
            return "json"
        if suffix in {".yaml", ".yml"}:
            return "yaml"
        if suffix in {".html", ".htm"}:
            return "html"
        if suffix in {".csv"}:
            return "csv"
        if suffix in {".xml"}:
            return "xml"
        return "text"

    def _session_overview(self, session) -> dict[str, Any]:
        return {
            "session_id": session.id,
            "title": session.title,
            "status": session.status.value,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "message_count": len(session.messages),
            "event_count": session.event_count,
            "snapshot_count": session.snapshot_count,
            "selected_agent": session.selected_agent,
            "preferred_model": session.preferred_model,
        }

    def _extract_questions(
        self,
        sessions: list[Any],
        include_assistant: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for session in sorted(sessions, key=lambda item: item.updated_at, reverse=True):
            for message in session.messages:
                role = getattr(message.role, "value", str(message.role))
                if role != "user" and not include_assistant:
                    continue
                if not str(message.content or "").strip():
                    continue
                if role == "assistant" and not include_assistant:
                    continue
                items.append(
                    {
                        "session_id": session.id,
                        "role": role,
                        "content": str(message.content).strip(),
                        "created_at": message.created_at.isoformat(),
                    }
                )
        items.sort(key=lambda item: item["created_at"])
        return items[:limit]

    async def _build_session_report(
        self,
        session,
        store: SessionStore,
        include_assistant: bool,
        limit: int,
    ) -> dict[str, Any]:
        events = await store.list_events(session.id, limit=limit)
        snapshots = await store.list_snapshots(
            session.id,
            limit=limit,
            include_graph_state=False,
        )
        approvals = await store.list_approvals(session.id)
        transcript_summary = self._transcript_hygiene_service.summarize_messages(session.messages)
        user_messages = [item for item in session.messages if getattr(item.role, "value", str(item.role)) == "user"]
        assistant_messages = [item for item in session.messages if getattr(item.role, "value", str(item.role)) == "assistant"]
        tool_messages = [item for item in session.messages if getattr(item.role, "value", str(item.role)) == "tool"]
        transcript_excerpt = self._transcript_hygiene_service.build_display_transcript(
            session.messages,
            limit=limit,
            include_assistant=include_assistant,
            include_tools=False,
            include_errors=False,
        )

        recent_event_types = [event.type for event in events]
        snapshot_stages = [snapshot.stage for snapshot in snapshots]
        return {
            "session_id": session.id,
            "title": session.title,
            "status": session.status.value,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "message_count": len(session.messages),
            "event_count": session.event_count,
            "snapshot_count": session.snapshot_count,
            "approval_count": len(approvals),
            "user_message_count": len(user_messages),
            "assistant_message_count": len(assistant_messages),
            "tool_message_count": len(tool_messages),
            "transcript_summary": transcript_summary,
            "recent_user_questions": [
                {
                    "content": str(item.content or "").strip(),
                    "created_at": item.created_at.isoformat(),
                }
                for item in user_messages[-limit:]
            ],
            "recent_event_types": recent_event_types,
            "snapshot_stages": snapshot_stages,
            "transcript_excerpt": transcript_excerpt,
        }

    def _format_session_report_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            f"# Session Report: {report['title']}",
            "",
            f"- Session ID: {report['session_id']}",
            f"- Status: {report['status']}",
            f"- Created At: {report['created_at']}",
            f"- Updated At: {report['updated_at']}",
            f"- Messages: {report['message_count']}",
            f"- Events: {report['event_count']}",
            f"- Snapshots: {report['snapshot_count']}",
            f"- Approvals: {report['approval_count']}",
            f"- Context Eligible Messages: {report.get('transcript_summary', {}).get('context_eligible_count', 0)}",
            f"- Tool Messages: {report.get('transcript_summary', {}).get('tool_count', 0)}",
            f"- Error Messages: {report.get('transcript_summary', {}).get('error_count', 0)}",
            "",
            "## Recent User Questions",
        ]
        questions = report.get("recent_user_questions") or []
        if questions:
            for item in questions:
                lines.append(f"- {item['created_at']}: {item['content']}")
        else:
            lines.append("- None")
        lines.extend(["", "## Recent Event Types"])
        event_types = report.get("recent_event_types") or []
        if event_types:
            for item in event_types:
                lines.append(f"- {item}")
        else:
            lines.append("- None")
        lines.extend(["", "## Snapshot Stages"])
        snapshot_stages = report.get("snapshot_stages") or []
        if snapshot_stages:
            for item in snapshot_stages:
                lines.append(f"- {item}")
        else:
            lines.append("- None")
        return "\n".join(lines).strip()

    def _build_code_review_debate_markdown(
        self,
        *,
        title: str,
        project_name: str,
        approval_time: str,
        approval_result: str,
        agent_count: int,
        report_sections: list[dict[str, Any]],
        result_rows: list[Any],
    ) -> str:
        markdown_lines = [
            f"# {title}",
            "",
            f"## 就《{project_name}》的辩论报告",
            "",
            f"- 审批时间: {approval_time}",
            f"- 审批结果: {approval_result}",
            f"- 参与agent数量: {agent_count}",
            "",
            "## 审批结果总览",
            "",
            self._build_code_review_result_summary_markdown(result_rows),
        ]
        for section in report_sections:
            markdown_lines.extend(["", f"## {section['title']}", "", str(section["content"])])
        return "\n".join(markdown_lines).strip()

    def _build_code_review_result_summary_markdown(self, result_rows: list[Any]) -> str:
        if not result_rows:
            return "暂无审批结果摘要。"
        lines = [
            "| 类别 | 结果 | 提出Agent | 摘要 | 建议措施 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in result_rows:
            if not isinstance(item, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        self._escape_markdown_table_cell(self._code_review_category_label(item.get("category"))),
                        self._escape_markdown_table_cell(str(item.get("title") or item.get("result") or "未命名结果")),
                        self._escape_markdown_table_cell(str(item.get("proposer_agent") or item.get("agent") or "未标注")),
                        self._escape_markdown_table_cell(str(item.get("summary") or item.get("detail") or "暂无摘要")),
                        self._escape_markdown_table_cell(str(item.get("recommended_action") or item.get("action") or "待补充")),
                    ]
                )
                + " |"
            )
        return "\n".join(lines)

    def _build_code_review_category_sections(self, result_rows: list[Any]) -> str:
        grouped: dict[str, list[dict[str, Any]]] = {
            "严重问题": [],
            "缺陷": [],
            "隐患": [],
            "可行": [],
            "优秀": [],
        }
        for item in result_rows:
            if not isinstance(item, dict):
                continue
            grouped[self._code_review_category_label(item.get("category"))].append(item)

        section_lines: list[str] = []
        for label, items in grouped.items():
            section_lines.append(f"### {label}")
            if not items:
                section_lines.extend(["", "- None provided.", ""])
                continue
            section_lines.append("")
            for item in items:
                title = str(item.get("title") or item.get("result") or "未命名结果")
                proposer = str(item.get("proposer_agent") or item.get("agent") or "未标注")
                summary = str(item.get("summary") or item.get("detail") or "暂无摘要")
                section_lines.append(f"- {title} | 提出Agent: {proposer} | {summary}")
            section_lines.append("")
        return "\n".join(section_lines).strip()

    def _code_review_category_label(self, value: Any) -> str:
        raw_value = str(value or "").strip()
        if raw_value in CODE_REVIEW_RESULT_CATEGORY_LABELS:
            return CODE_REVIEW_RESULT_CATEGORY_LABELS[raw_value]
        return CODE_REVIEW_RESULT_CATEGORY_LABELS.get(raw_value.lower(), "隐患")

    def _escape_markdown_table_cell(self, value: str) -> str:
        return value.replace("|", "\\|").replace("\n", "<br>")


def _build_excerpt(text: str, tokens: list[str], radius: int = 140) -> str:
    lowered = text.lower()
    pivot = min(
        (lowered.find(token) for token in tokens if lowered.find(token) >= 0),
        default=0,
    )
    start = max(0, pivot - radius)
    end = min(len(text), pivot + radius)
    return " ".join(text[start:end].split())


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower()


def _to_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _clamp_int(value: Any, *, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _clamp_float(value: Any, *, minimum: float, maximum: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _truncate_text(value: str, max_chars: int) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True


def _language_from_extension(extension: str) -> str:
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".vue": "vue",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".sql": "sql",
        ".sh": "shell",
        ".ps1": "powershell",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
    }
    return mapping.get(extension.lower(), "other")


def _join_remote_path(root_path: str, child_path: str) -> str:
    normalized_root = root_path.replace("\\", "/").strip()
    normalized_child = child_path.replace("\\", "/").strip()
    if not normalized_child:
        return normalized_root
    if normalized_child.startswith("/"):
        return normalized_child
    return str(PurePosixPath(normalized_root) / normalized_child)


def _relativize_remote_path(root_path: str, target_path: str) -> str:
    normalized_root = root_path.replace("\\", "/").rstrip("/")
    normalized_target = target_path.replace("\\", "/")
    if normalized_target.startswith(f"{normalized_root}/"):
        return normalized_target[len(normalized_root) + 1:]
    if normalized_target == normalized_root:
        return "."
    return normalized_target


def _lookup_path(payload: dict[str, Any], dotted_path: str) -> tuple[Any, bool]:
    current: Any = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None, False
        current = current[segment]
    return current, True


def _format_object_list(items: list[Any]) -> str:
    if not items:
        return "None provided."
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or item.get("id") or "Item")
            body = str(item.get("summary") or item.get("detail") or item.get("content") or item)
            lines.append(f"- {title}: {body}")
            continue
        lines.append(f"- {item}")
    return "\n".join(lines)
