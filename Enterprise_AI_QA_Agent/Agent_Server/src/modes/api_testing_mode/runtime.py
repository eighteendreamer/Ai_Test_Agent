"""API Testing Mode Runtime: the main state machine orchestrator.

Handles the full lifecycle from request interpretation through project/endpoint
clarification, campaign building, parallel execution, and report generation.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.application.documents.api_docs_service import ApiDocsService
from src.modes.api_testing_mode.campaign_state import (
    ApiTestCampaign,
    ApiTaskEventRecord,
    ApiTestTask,
    ApiTestingRequestState,
    ApiTestingState,
    CredentialSession,
    EndpointCandidate,
    ExecutionPolicy,
    PendingSelection,
    ProjectCandidate,
)
from src.modes.api_testing_mode.capability_mapper import CapabilityMapper, MappedEndpoint
from src.modes.api_testing_mode.contracts import (
    AWAITING_PHASES,
    PHASE_AWAITING_AUTH_INPUT,
    PHASE_AWAITING_ENDPOINT_SCOPE_SELECTION,
    PHASE_AWAITING_ENDPOINT_SELECTION,
    PHASE_AWAITING_PROJECT_SELECTION,
    PHASE_CAMPAIGN_READY,
    PHASE_DOCUMENT_SELECTED,
    PHASE_ENDPOINT_CANDIDATES_FOUND,
    PHASE_FAILED,
    PHASE_PROJECT_CANDIDATES_FOUND,
    PHASE_REPORT_READY,
    PHASE_REQUEST_RESOLVED,
    PHASE_TASK_DISPATCHING,
    PHASE_TASK_RUNNING,
    SCOPE_ALL_RELATED,
    SCOPE_CORE_ONLY,
    SCOPE_MANUAL_PICK,
    SCOPE_SINGLE_TARGET,
    SELECTION_KIND_CREDENTIAL,
    SELECTION_KIND_ENDPOINT_SCOPE,
    SELECTION_KIND_ENDPOINTS,
    SELECTION_KIND_PROJECT,
    STATE_METADATA_KEY,
    TERMINAL_PHASES,
)
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.dependency_planner import DependencyPlanner
from src.modes.api_testing_mode.doc_parser import ApiDocParser
from src.modes.api_testing_mode.endpoint_scope_service import EndpointScopeService
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.precondition_resolver import PreconditionResolver
from src.modes.api_testing_mode.project_locator import ApiProjectLocator
from src.modes.api_testing_mode.report_builder import ReportBuilder
from src.modes.api_testing_mode.selection_resolver import SelectionResolver
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.modes.api_testing_mode.tools import API_TESTING_TOOL_KEYS
from src.modes.api_testing_mode.verification import ApiTestingVerificationPolicy
from src.modes.api_testing_mode.evaluation import ApiTestingEvaluationPolicy


class ApiTestingModeRuntime:
    """Drives the API testing mode state machine."""

    def __init__(
        self,
        *,
        api_docs_service: ApiDocsService,
        settings: Any = None,
        coordinator_runtime_service: Any = None,
        session_store: Any = None,
    ) -> None:
        self._api_docs_service = api_docs_service
        self._settings = settings
        self._coordinator_runtime_service = coordinator_runtime_service
        self._session_store = session_store
        self._project_locator = ApiProjectLocator(api_docs_service=api_docs_service)
        self._doc_parser = ApiDocParser(api_docs_service=api_docs_service)
        self._capability_mapper = CapabilityMapper()
        self._scope_service = EndpointScopeService()
        self._precondition_resolver = PreconditionResolver()
        self._dependency_planner = DependencyPlanner()
        self._selection_resolver = SelectionResolver()
        self._report_builder = ReportBuilder()
        self._credential_manager = CredentialManager()
        self._verification_policy = ApiTestingVerificationPolicy()
        self._evaluation_policy = ApiTestingEvaluationPolicy()
        self._last_auth_hint: dict = {}
        self._last_markdown_report: str = ""
        self._last_artifacts: list[dict] = []

    # ==================================================================
    # Public entry point
    # ==================================================================

    async def handle(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        """Main entry: restore state, drive phase machine, return result."""
        worker_action = str(arguments.get("worker_action") or "").strip().lower()
        if worker_action == "execute_task":
            return await self._execute_dispatched_task(arguments)

        state = self._restore_state(context)
        request = self._build_request(arguments, context)

        # If state is in a terminal phase and user sends a new request, reset for a new campaign.
        if state.phase in TERMINAL_PHASES and request.raw_message.strip():
            state = ApiTestingState()

        state.request = request
        self._attach_runtime_context(state, context)

        # If we are in an awaiting phase, try to resolve the user's reply.
        if state.phase in AWAITING_PHASES and state.pending_selection:
            selection = self._selection_resolver.resolve(
                pending=state.pending_selection,
                message=request.raw_message,
            )
            if selection.resolved:
                state = await self._apply_selection(state, selection)
            else:
                # User reply did not resolve the pending selection.
                self._persist_state(state, context)
                return self._build_output(state, note="请从候选列表中选择，或输入序号/关键词。")

        # Drive the phase machine forward.
        state = await self._advance(state, context)

        # Persist state for next turn.
        self._persist_state(state, context)
        return self._build_output(state)

    # ==================================================================
    # Phase machine
    # ==================================================================

    async def _advance(self, state: ApiTestingState, context: Any) -> ApiTestingState:
        """Advance the state machine as far as possible without user input."""
        # Guard: if already terminal, return.
        if state.phase in TERMINAL_PHASES:
            return state

        # Phase: request_resolved -> discover projects.
        if state.phase == PHASE_REQUEST_RESOLVED:
            state = await self._discover_projects(state)

        # Phase: project_candidates_found -> auto-select or ask.
        if state.phase == PHASE_PROJECT_CANDIDATES_FOUND:
            state = self._resolve_project(state)

        # Phase: awaiting_project_selection -> stop (need user input).
        if state.phase == PHASE_AWAITING_PROJECT_SELECTION:
            return state

        # Phase: document_selected -> parse endpoints.
        if state.phase == PHASE_DOCUMENT_SELECTED:
            state = await self._parse_endpoints(state)

        # Phase: endpoint_candidates_found -> scope resolution.
        if state.phase == PHASE_ENDPOINT_CANDIDATES_FOUND:
            state = self._resolve_scope(state)

        # Phase: awaiting_endpoint_scope_selection -> stop.
        if state.phase == PHASE_AWAITING_ENDPOINT_SCOPE_SELECTION:
            return state

        # Phase: awaiting_endpoint_selection -> stop.
        if state.phase == PHASE_AWAITING_ENDPOINT_SELECTION:
            return state

        # Phase: awaiting_auth_input -> stop.
        if state.phase == PHASE_AWAITING_AUTH_INPUT:
            return state

        # Phase: campaign_ready -> execute.
        if state.phase == PHASE_CAMPAIGN_READY:
            state = await self._execute_campaign(state, context)

        return state

    # ------------------------------------------------------------------
    # Phase handlers
    # ------------------------------------------------------------------

    async def _discover_projects(self, state: ApiTestingState) -> ApiTestingState:
        result = await self._project_locator.locate(request=state.request)
        if not result.has_candidates:
            state.record_phase_transition(PHASE_FAILED, "No API documents found.")
            state.notes.append("系统中没有找到任何 API 文档，请先上传接口文档。")
            return state

        state.project_candidates = result.candidates
        state.record_phase_transition(PHASE_PROJECT_CANDIDATES_FOUND, "Projects discovered.")
        return state

    def _resolve_project(self, state: ApiTestingState) -> ApiTestingState:
        candidates = state.project_candidates
        if not candidates:
            state.record_phase_transition(PHASE_FAILED, "No project candidates.")
            return state

        # Check if clarification is needed.
        result_sync = None
        # Re-run the locator's clarification logic inline.
        needs_clarification = len(candidates) > 1
        if len(candidates) == 1:
            needs_clarification = False
        elif state.request.project_hint:
            top = candidates[0]
            second = candidates[1] if len(candidates) > 1 else None
            if second and (top.score - second.score) >= 20.0:
                needs_clarification = False

        if needs_clarification:
            options = [
                {
                    "id": f"project_{i}",
                    "label": c.project_name,
                    "value": c.project_name,
                    "project_name": c.project_name,
                    "project_url": c.project_url,
                    "doc_count": c.doc_count,
                    "endpoint_count": c.endpoint_count,
                    "score": c.score,
                }
                for i, c in enumerate(candidates)
            ]
            state.pending_selection = PendingSelection(
                kind=SELECTION_KIND_PROJECT,
                prompt=f"发现 {len(candidates)} 个项目，请选择要测试的项目：",
                options=options,
                recommended_option_id=options[0]["id"] if options else "",
            )
            state.record_phase_transition(PHASE_AWAITING_PROJECT_SELECTION, "Multiple projects need clarification.")
            return state

        # Auto-select the top candidate.
        state.selected_project = candidates[0]
        state = self._select_project_documents(state, candidates[0])
        return state

    def _select_project_documents(self, state: ApiTestingState, project: ProjectCandidate) -> ApiTestingState:
        state.selected_project = project
        state.selected_documents = [
            doc
            for doc in (state.document_candidates or [])
            if doc.project_name == project.project_name
        ]
        # If we don't have document_candidates yet, build them from project.
        if not state.selected_documents:
            from src.modes.api_testing_mode.campaign_state import DocumentCandidate
            state.selected_documents = [
                DocumentCandidate(
                    doc_id=doc_id,
                    title=project.project_name,
                    project_name=project.project_name,
                    project_url=project.project_url,
                )
                for doc_id in project.doc_ids
            ]
        state.context_refs = self._build_document_context_refs(state.selected_documents)
        state.record_phase_transition(PHASE_DOCUMENT_SELECTED, f"Project '{project.project_name}' selected.")
        return state

    async def _parse_endpoints(self, state: ApiTestingState) -> ApiTestingState:
        parsed_indexes = await self._doc_parser.parse_documents_deduplicated(documents=state.selected_documents)
        all_endpoints: list[EndpointCandidate] = []
        auth_hint: dict = {}
        for index in parsed_indexes:
            all_endpoints.extend(index.endpoints)
            if index.auth_hint and index.auth_hint.get("type", "none") != "none":
                auth_hint = index.auth_hint

        # Store auth_hint for later use by executor.
        self._last_auth_hint = auth_hint
        state.auth_hint = dict(auth_hint)

        if not all_endpoints:
            state.record_phase_transition(PHASE_FAILED, "No endpoints found in selected documents.")
            state.notes.append("所选文档中未解析到任何接口端点。")
            return state

        state.endpoint_candidates = all_endpoints
        state.record_phase_transition(PHASE_ENDPOINT_CANDIDATES_FOUND, f"Found {len(all_endpoints)} endpoints.")
        return state

    def _resolve_scope(self, state: ApiTestingState) -> ApiTestingState:
        mapped = self._capability_mapper.map_many(state.endpoint_candidates)

        # Check if scope clarification is needed.
        should_clarify, reason = self._scope_service.should_clarify_scope(
            mapped_endpoints=mapped,
            request=state.request,
        )

        if should_clarify:
            project_name = state.selected_project.project_name if state.selected_project else ""
            state.pending_selection = self._scope_service.build_pending_selection(
                project_name=project_name,
                mapped_endpoints=mapped,
            )
            state.record_phase_transition(PHASE_AWAITING_ENDPOINT_SCOPE_SELECTION, reason)
            return state

        # Auto-resolve scope.
        scope_pref = state.request.scope_preference or SCOPE_CORE_ONLY
        resolution = self._scope_service.resolve_scope(
            scope=scope_pref,
            mapped_endpoints=mapped,
            request=state.request,
        )

        if resolution.requires_manual_pick:
            project_name = state.selected_project.project_name if state.selected_project else ""
            state.pending_selection = self._scope_service.build_endpoint_selection(
                project_name=project_name,
                mapped_endpoints=mapped,
            )
            state.record_phase_transition(PHASE_AWAITING_ENDPOINT_SELECTION, "Manual endpoint selection required.")
            return state

        state.selected_scope = resolution.resolved_scope
        state.selected_endpoints = resolution.selected_endpoints
        return self._check_preconditions(state, mapped)

    def _check_preconditions(
        self,
        state: ApiTestingState,
        mapped: list[MappedEndpoint] | None = None,
    ) -> ApiTestingState:
        # Get auth hint from parsed docs.
        auth_hint = state.auth_hint if isinstance(state.auth_hint, dict) else {}
        # Analyze preconditions.
        analysis = self._precondition_resolver.analyze(
            endpoints=state.selected_endpoints,
            auth_hint=auth_hint,
        )

        if analysis.requires_auth and not self._credential_manager.has_valid_session():
            state.pending_selection = PendingSelection(
                kind=SELECTION_KIND_CREDENTIAL,
                prompt=(
                    "所选接口需要认证，请提供凭证信息。"
                    " 支持格式：Bearer token / API Key / 用户名+密码。"
                ),
                options=[
                    {"id": "bearer", "label": "Bearer Token", "value": "bearer"},
                    {"id": "api_key", "label": "API Key", "value": "api_key"},
                    {"id": "basic", "label": "用户名 + 密码", "value": "basic"},
                ],
                allow_free_text=True,
            )
            state.record_phase_transition(PHASE_AWAITING_AUTH_INPUT, "Auth required but no credentials available.")
            return state

        # Build campaign.
        return self._build_campaign(state, mapped)

    def _build_campaign(
        self,
        state: ApiTestingState,
        mapped: list[MappedEndpoint] | None = None,
    ) -> ApiTestingState:
        if mapped is None:
            mapped = self._capability_mapper.map_many(state.selected_endpoints)

        # Filter mapped to only selected endpoints.
        selected_ids = {ep.endpoint_id for ep in state.selected_endpoints}
        filtered_mapped = [m for m in mapped if m.endpoint.endpoint_id in selected_ids]
        if not filtered_mapped:
            filtered_mapped = mapped

        preconditions = self._precondition_resolver.analyze(
            endpoints=state.selected_endpoints,
        )
        credential_session = self._credential_manager.get_latest()
        credential_session_id = credential_session.credential_session_id if credential_session else ""

        base_url = ""
        if state.selected_project:
            base_url = state.selected_project.project_url or ""

        graph = self._dependency_planner.plan(
            mapped_endpoints=filtered_mapped,
            preconditions=preconditions,
            credential_session_id=credential_session_id,
            base_url=base_url,
        )

        campaign = ApiTestCampaign(
            campaign_id=str(uuid4()),
            project_name=state.selected_project.project_name if state.selected_project else "",
            project_url=base_url,
            objective=state.request.objective,
            verification_focus=state.request.verification_focus,
            selected_document_ids=[doc.doc_id for doc in state.selected_documents],
            selected_endpoints=state.selected_endpoints,
            credential_session_id=credential_session_id,
            tasks=graph.tasks,
            execution_policy=ExecutionPolicy(max_workers=2),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        state.campaign = campaign
        state.record_phase_transition(PHASE_CAMPAIGN_READY, f"Campaign built with {len(graph.tasks)} tasks.")
        return state

    async def _execute_campaign(self, state: ApiTestingState, context: Any) -> ApiTestingState:
        if state.campaign is None:
            state.record_phase_transition(PHASE_FAILED, "No campaign to execute.")
            return state

        state.record_phase_transition(PHASE_TASK_DISPATCHING, "Dispatching campaign tasks to worker agents.")
        self._checkpoint_execution_state(
            state=state,
            context=context,
            event_type="campaign_dispatching",
            tasks=state.campaign.tasks,
            summary="Dispatching campaign tasks to worker agents.",
        )

        pool = ApiTaskPool(tasks=state.campaign.tasks)
        # Resolve auth token field from parsed doc hints.
        auth_token_field = "access_token"
        if hasattr(self, "_last_auth_hint") and isinstance(self._last_auth_hint, dict):
            auth_token_field = str(self._last_auth_hint.get("token_field") or "access_token")
        if state.auth_hint:
            auth_token_field = str(state.auth_hint.get("token_field") or auth_token_field)
        state.record_phase_transition(PHASE_TASK_RUNNING, "Executing campaign tasks.")
        self._checkpoint_execution_state(
            state=state,
            context=context,
            event_type="campaign_running",
            tasks=state.campaign.tasks,
            summary="Executing campaign tasks.",
        )

        if self._can_use_subagent_execution(context):
            coordinator = ApiSubagentCoordinator(
                pool=pool,
                policy=state.campaign.execution_policy,
                coordinator_runtime_service=self._coordinator_runtime_service,
                session_store=self._session_store,
                parent_context=self._build_dispatch_context(context),
                credential_manager=self._credential_manager,
                auth_token_field=auth_token_field,
                worker_model_key=getattr(context, "selected_model_key", "") or None,
                checkpoint_callback=self._build_checkpoint_callback(state, context),
            )
            completed_tasks = await coordinator.run_all()
        else:
            executor = ApiTaskExecutor(
                credential_manager=self._credential_manager,
                timeout_seconds=state.campaign.execution_policy.request_timeout_seconds,
                auth_token_field=auth_token_field,
            )
            coordinator = ApiTestCoordinator(
                pool=pool,
                policy=state.campaign.execution_policy,
                executor_fn=executor.execute,
                checkpoint_callback=self._build_checkpoint_callback(state, context),
            )
            completed_tasks = await coordinator.run_all()
        state.campaign.tasks = completed_tasks
        state.campaign.updated_at = datetime.now(timezone.utc).isoformat()
        self._checkpoint_execution_state(
            state=state,
            context=context,
            event_type="campaign_tasks_settled",
            tasks=completed_tasks,
            summary="All campaign tasks have settled.",
        )

        # Build report.
        report = self._report_builder.build(campaign=state.campaign)
        state.report = report

        # Generate Markdown report.
        markdown_report = self._report_builder.build_markdown(campaign=state.campaign, report=report)
        self._last_markdown_report = markdown_report

        # Build artifact payloads (ready for persistence).
        artifacts = self._report_builder.build_artifacts(
            campaign=state.campaign,
            report=report,
            markdown_report=markdown_report,
        )
        self._last_artifacts = artifacts
        state.artifacts = self._artifact_metadata(artifacts)

        # Run verification policy.
        verification_verdict = self._verification_policy.verify(campaign=state.campaign, report=report)

        # Run evaluation policy.
        total_available = len(state.endpoint_candidates) if state.endpoint_candidates else len(completed_tasks)
        evaluation_result = self._evaluation_policy.evaluate(
            campaign=state.campaign,
            total_available_endpoints=total_available,
            verification_verdict=verification_verdict,
        )
        state.verification_result = verification_verdict.to_dict()
        state.evaluation_result = evaluation_result.to_dict()
        state.errors = self._build_error_records(evaluation_result.to_dict(), completed_tasks)
        report.artifacts = list(state.artifacts)
        report.verification_result = dict(state.verification_result)
        report.evaluation_result = dict(state.evaluation_result)
        state.report = report

        # Attach to report notes.
        state.notes.append(f"验证结论: {verification_verdict.summary}")
        state.notes.append(f"质量评估: {evaluation_result.summary}")
        if evaluation_result.recommendations:
            for rec in evaluation_result.recommendations[:5]:
                state.notes.append(rec)

        state.record_phase_transition(PHASE_REPORT_READY, "Campaign execution complete.")
        return state

    # ==================================================================
    # Selection application
    # ==================================================================

    async def _apply_selection(self, state: ApiTestingState, selection) -> ApiTestingState:
        kind = selection.kind

        if kind == SELECTION_KIND_PROJECT:
            # Find the selected project.
            option_id = selection.option_ids[0] if selection.option_ids else ""
            index = self._extract_index(option_id, prefix="project_")
            if index is not None and index < len(state.project_candidates):
                project = state.project_candidates[index]
                state.pending_selection = None
                state = self._select_project_documents(state, project)
            else:
                state.notes.append("无法识别所选项目，请重新选择。")
            return state

        if kind == SELECTION_KIND_ENDPOINT_SCOPE:
            scope = selection.scope or selection.option_ids[0] if selection.option_ids else SCOPE_CORE_ONLY
            state.selected_scope = scope
            state.pending_selection = None
            mapped = self._capability_mapper.map_many(state.endpoint_candidates)
            resolution = self._scope_service.resolve_scope(
                scope=scope,
                mapped_endpoints=mapped,
                request=state.request,
            )
            if resolution.requires_manual_pick:
                project_name = state.selected_project.project_name if state.selected_project else ""
                state.pending_selection = self._scope_service.build_endpoint_selection(
                    project_name=project_name,
                    mapped_endpoints=mapped,
                )
                state.record_phase_transition(PHASE_AWAITING_ENDPOINT_SELECTION, "Manual pick required.")
            else:
                state.selected_endpoints = resolution.selected_endpoints
                state = self._check_preconditions(state, mapped)
            return state

        if kind == SELECTION_KIND_ENDPOINTS:
            selected_ids = set(selection.option_ids)
            state.selected_endpoints = [
                ep for ep in state.endpoint_candidates
                if ep.endpoint_id in selected_ids
            ]
            state.pending_selection = None
            if not state.selected_endpoints:
                state.notes.append("未匹配到任何接口，请重新选择。")
                return state
            mapped = self._capability_mapper.map_many(state.endpoint_candidates)
            state = self._check_preconditions(state, mapped)
            return state

        if kind == SELECTION_KIND_CREDENTIAL:
            if selection.extracted_credentials:
                session = self._credential_manager.create_from_user_input(
                    extracted=selection.extracted_credentials,
                )
                state.credential_session = session
            state.pending_selection = None
            mapped = self._capability_mapper.map_many(state.selected_endpoints)
            state = self._build_campaign(state, mapped)
            return state

        return state

    # ==================================================================
    # State persistence
    # ==================================================================

    def _restore_state(self, context: Any) -> ApiTestingState:
        bundle = getattr(context, "context_bundle", None) or {}
        raw = bundle.get(STATE_METADATA_KEY)
        if isinstance(raw, dict):
            try:
                state = ApiTestingState.model_validate(raw)
                # Restore credential session into credential_manager if present.
                if state.credential_session and state.credential_session.token:
                    self._credential_manager.create_from_user_input(
                        extracted={
                            "auth_type": state.credential_session.auth_type,
                            "token": state.credential_session.token,
                        },
                        source="state_restore",
                    )
                if state.auth_hint:
                    self._last_auth_hint = dict(state.auth_hint)
                return state
            except Exception:
                pass
        return ApiTestingState()

    def _persist_state(self, state: ApiTestingState, context: Any) -> None:
        state.last_updated_at = datetime.now(timezone.utc).isoformat()
        bundle = getattr(context, "context_bundle", None)
        if isinstance(bundle, dict):
            bundle[STATE_METADATA_KEY] = state.model_dump(mode="json")

    # ==================================================================
    # Output builder
    # ==================================================================

    def _build_output(self, state: ApiTestingState, note: str = "") -> dict[str, Any]:
        output: dict[str, Any] = {
            "status": "completed" if state.phase == PHASE_REPORT_READY else (
                "failed" if state.phase == PHASE_FAILED else "partial"
            ),
            "phase": state.phase,
            "summary": self._build_summary(state, note),
        }
        if state.trace_id:
            output["trace_id"] = state.trace_id
        if state.selected_agent:
            output["selected_agent"] = state.selected_agent
        if state.selected_tools:
            output["selected_tools"] = list(state.selected_tools)
        if state.context_refs:
            output["context_refs"] = list(state.context_refs)

        if state.pending_selection:
            output["pending_selection"] = state.pending_selection.model_dump(mode="json")

        if state.selected_project:
            output["selected_project"] = state.selected_project.model_dump(mode="json")

        if state.project_candidates and state.phase == PHASE_AWAITING_PROJECT_SELECTION:
            output["project_candidates"] = [c.model_dump(mode="json") for c in state.project_candidates]

        if state.endpoint_candidates and state.phase in {
            PHASE_AWAITING_ENDPOINT_SCOPE_SELECTION,
            PHASE_AWAITING_ENDPOINT_SELECTION,
            PHASE_ENDPOINT_CANDIDATES_FOUND,
        }:
            output["endpoint_count"] = len(state.endpoint_candidates)

        if state.selected_endpoints:
            output["selected_endpoint_count"] = len(state.selected_endpoints)

        if state.campaign:
            output["campaign_id"] = state.campaign.campaign_id
            output["task_count"] = len(state.campaign.tasks)

        if state.report:
            output["report"] = state.report.model_dump(mode="json")
            # Include markdown report if available.
            if hasattr(self, "_last_markdown_report") and self._last_markdown_report:
                output["report_markdown"] = self._last_markdown_report
            # Include artifact metadata if available.
            if hasattr(self, "_last_artifacts") and self._last_artifacts:
                output["artifacts"] = self._artifact_metadata(self._last_artifacts)
            elif state.artifacts:
                output["artifacts"] = list(state.artifacts)

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

        output[STATE_METADATA_KEY] = state.model_dump(mode="json")
        return output

    def _build_summary(self, state: ApiTestingState, note: str = "") -> str:
        if note:
            return note
        if state.phase == PHASE_FAILED:
            return state.notes[-1] if state.notes else "API testing mode encountered an error."
        if state.phase == PHASE_REPORT_READY and state.report:
            return state.report.summary
        if state.phase == PHASE_AWAITING_PROJECT_SELECTION:
            return f"发现 {len(state.project_candidates)} 个项目，请选择要测试的项目。"
        if state.phase == PHASE_AWAITING_ENDPOINT_SCOPE_SELECTION:
            return f"发现 {len(state.endpoint_candidates)} 个接口，请选择测试范围。"
        if state.phase == PHASE_AWAITING_ENDPOINT_SELECTION:
            return "请从接口列表中选择要测试的端点。"
        if state.phase == PHASE_AWAITING_AUTH_INPUT:
            return "所选接口需要认证，请提供凭证信息。"
        if state.phase == PHASE_CAMPAIGN_READY:
            task_count = len(state.campaign.tasks) if state.campaign else 0
            return f"Campaign 已就绪，共 {task_count} 个任务待执行。"
        if state.phase == PHASE_TASK_RUNNING:
            return "正在执行 API 测试任务..."
        return f"API testing mode is in phase: {state.phase}"

    # ==================================================================
    # Request building
    # ==================================================================

    def _build_request(self, arguments: dict[str, Any], context: Any) -> ApiTestingRequestState:
        bundle = getattr(context, "context_bundle", None) or {}
        mode_request = bundle.get("api_testing_request") or {}
        if not isinstance(mode_request, dict):
            mode_request = {}

        user_message = str(getattr(context, "user_message", "") or "")
        objective = str(
            arguments.get("objective")
            or mode_request.get("objective")
            or user_message
        ).strip()

        return ApiTestingRequestState(
            objective=objective,
            project_hint=str(arguments.get("project_hint") or mode_request.get("project_hint") or "").strip(),
            domain_hint=str(arguments.get("domain_hint") or mode_request.get("domain_hint") or "").strip(),
            endpoint_hint=str(arguments.get("endpoint") or mode_request.get("endpoint") or "").strip(),
            method_hint=str(arguments.get("method") or mode_request.get("method") or "").strip().upper(),
            scope_preference=str(arguments.get("scope_preference") or mode_request.get("scope_preference") or "").strip(),
            verification_focus=str(
                arguments.get("verification_focus")
                or mode_request.get("verification_focus")
                or "general"
            ).strip(),
            auth_hint=str(arguments.get("auth_hint") or mode_request.get("auth_hint") or "").strip(),
            raw_message=user_message,
        )

    def _attach_runtime_context(self, state: ApiTestingState, context: Any) -> None:
        state.session_id = str(getattr(context, "session_id", "") or state.session_id)
        state.trace_id = str(getattr(context, "trace_id", "") or state.trace_id)
        state.selected_agent = str(
            getattr(context, "selected_agent_key", "") or state.selected_agent or "api-testing-agent"
        )
        state.selected_tools = list(API_TESTING_TOOL_KEYS)

    def _build_checkpoint_callback(self, state: ApiTestingState, context: Any):
        def checkpoint(event_type: str, task: ApiTestTask, tasks: list[ApiTestTask]) -> None:
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
        state: ApiTestingState,
        context: Any,
        event_type: str,
        task: ApiTestTask | None = None,
        tasks: list[ApiTestTask] | None = None,
        summary: str = "",
    ) -> None:
        if state.campaign and tasks is not None:
            state.campaign.tasks = list(tasks)
        task_list = list(tasks or (state.campaign.tasks if state.campaign else []))
        now = datetime.now(timezone.utc).isoformat()
        if task is not None:
            event = ApiTaskEventRecord(
                event_id=f"{event_type}_{task.task_id}_{task.attempts}_{len(state.task_events) + 1}",
                event_type=event_type,
                task_id=task.task_id,
                task_name=task.name,
                method=task.method,
                path=task.path or task.full_url,
                status=task.status,
                phase=state.phase,
                attempts=task.attempts,
                response_status=task.response_status,
                duration_ms=task.duration_ms,
                worker_session_id=task.worker_session_id,
                summary=summary or task.worker_summary,
                error=task.last_error,
                at=now,
            )
            state.task_events.append(event)
            if len(state.task_events) > 200:
                state.task_events = state.task_events[-200:]

        state.execution_checkpoint = {
            "phase": state.phase,
            "campaign_id": state.campaign.campaign_id if state.campaign else "",
            "last_event_type": event_type,
            "active_task_id": task.task_id if task is not None else "",
            "active_task_status": task.status if task is not None else "",
            "task_summary": self._task_status_summary(task_list),
            "event_count": len(state.task_events),
            "updated_at": now,
            "trace_id": state.trace_id,
        }
        self._persist_state(state, context)

    def _task_status_summary(self, tasks: list[ApiTestTask]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        counts["total"] = len(tasks)
        return counts

    def _build_document_context_refs(self, documents: list[Any]) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for document in documents:
            doc_id = str(getattr(document, "doc_id", "") or "")
            if not doc_id:
                continue
            refs.append(
                {
                    "type": "api_document",
                    "source": "api_docs_service",
                    "doc_id": doc_id,
                    "title": str(getattr(document, "title", "") or ""),
                    "project_name": str(getattr(document, "project_name", "") or ""),
                    "project_url": str(getattr(document, "project_url", "") or ""),
                    "updated_at": str(getattr(document, "updated_at", "") or ""),
                }
            )
        return refs

    def _artifact_metadata(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        metadata: list[dict[str, Any]] = []
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            artifact = {
                "type": item.get("type"),
                "filename": item.get("filename"),
                "content_type": item.get("content_type"),
                "label": item.get("label"),
            }
            content = item.get("content")
            if isinstance(content, bytes):
                artifact["content"] = content.decode("utf-8", errors="replace")
            elif content is not None:
                artifact["content"] = str(content)
            if item.get("task_id"):
                artifact["task_id"] = item.get("task_id")
            metadata.append(artifact)
        return metadata

    def _build_error_records(
        self,
        evaluation_result: dict[str, Any],
        tasks: list[ApiTestTask],
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
                        "response_status": item.get("response_status"),
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
                    "response_status": task.response_status,
                    "is_transient": False,
                }
            )
        return errors

    # ==================================================================
    # Utilities
    # ==================================================================

    def _extract_index(self, option_id: str, prefix: str = "") -> int | None:
        if prefix and option_id.startswith(prefix):
            try:
                return int(option_id[len(prefix):])
            except ValueError:
                return None
        try:
            return int(option_id)
        except ValueError:
            return None

    async def _execute_dispatched_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_task = arguments.get("task")
        if not isinstance(raw_task, dict):
            return {
                "status": "failed",
                "ok": False,
                "summary": "Worker task execution requires a serialized `task` payload.",
                "error": "missing_task_payload",
            }

        task = ApiTestTask.model_validate(raw_task)
        credential_manager = CredentialManager()

        raw_credential = arguments.get("credential_session")
        if isinstance(raw_credential, dict):
            try:
                credential_manager.restore_session(CredentialSession.model_validate(raw_credential))
            except Exception:
                pass

        auth_token_field = str(arguments.get("auth_token_field") or "access_token")
        executor = ApiTaskExecutor(
            credential_manager=credential_manager,
            timeout_seconds=task.timeout_seconds or 30.0,
            auth_token_field=auth_token_field,
        )
        result = await executor.execute(task)
        latest_credential = credential_manager.get_latest()
        summary = (
            f"Executed API task {result.task_id} with final status {result.status}."
            if result.response_status is None
            else f"Executed API task {result.task_id} with final status {result.status} and HTTP {result.response_status}."
        )
        return {
            "status": "completed",
            "ok": result.status == "completed",
            "summary": summary,
            "worker_kind": "api_test_task_execution",
            "task_result": result.model_dump(mode="json"),
            "credential_session": latest_credential.model_dump(mode="json") if latest_credential else None,
        }

    def _can_use_subagent_execution(self, context: Any) -> bool:
        if self._coordinator_runtime_service is None or self._session_store is None:
            return False
        return bool(getattr(context, "session_id", "") and getattr(context, "turn_id", ""))

    def _build_dispatch_context(self, context: Any) -> dict[str, Any]:
        return {
            "session_id": str(getattr(context, "session_id", "") or ""),
            "turn_id": str(getattr(context, "turn_id", "") or ""),
            "trace_id": str(getattr(context, "trace_id", "") or ""),
            "selected_agent_key": str(getattr(context, "selected_agent_key", "") or ""),
            "selected_model_key": str(getattr(context, "selected_model_key", "") or ""),
        }

    def set_coordinator_runtime_service(self, coordinator_runtime_service: Any) -> None:
        self._coordinator_runtime_service = coordinator_runtime_service

    def set_session_store(self, session_store: Any) -> None:
        self._session_store = session_store


__all__ = ["ApiTestingModeRuntime"]
