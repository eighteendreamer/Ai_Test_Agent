from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Support both:
# 1. `python Agent_Server/src/main.py`
# 2. `uvicorn src.main:app --reload`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.api.routes.attachments import router as attachments_router
from src.api.routes.api_docs import router as api_docs_router
from src.api.routes.compatibility import router as compatibility_router
from src.api.routes.docker import router as docker_router
from src.api.routes.health import router as health_router
from src.api.routes.integrations import router as integrations_router
from src.api.routes.knowledge import router as knowledge_router
from src.api.routes.oauth import router as oauth_router
from src.api.routes.projects import router as projects_router
from src.api.routes.recordings import router as recordings_router
from src.api.routes.reports import router as reports_router
from src.api.routes.registry import router as registry_router
from src.api.routes.sessions import router as sessions_router
from src.api.routes.case_management import router as test_cases_router
from src.api.routes.suite_management import router as test_suites_router
from src.api.routes.run_management import router as test_runs_router
from src.api.routes.security_bugs import router as security_bugs_router
from src.api.routes.settings import router as settings_router
from src.api.routes.sponsors import router as sponsors_router
from src.api.routes.task_pool import router as task_pool_router
from src.api.routes.mail import router as mail_router
from src.application.mail.auth_monitor import TencentAuthMonitor
from src.application.models.oauth_token_service import OAuthTokenService
from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.compatibility import CompatibilityRunnerService
from src.application.documents.api_docs_service import ApiDocsService
from src.application.documents.api_doc_store import PostgresApiDocStore
from src.application.docker_management_service import DockerManagementService
from src.application.integrations.integration_catalog_service import IntegrationCatalogService
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService
from src.application.mcp.host.connection_manager import McpConnectionManager
from src.application.mcp.host.tool_bridge import McpToolBridge
from src.application.mcp.manager_service import MCPManagerService
from src.application.mcp.runtime_manager import MCPRuntimeManager
from src.application.mcp.server_store import PostgresMCPServerStore
from src.application.orchestration.coordinator_runtime_service import CoordinatorRuntimeService
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.context.embedding_runtime_service import EmbeddingRuntimeService
from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.intent.semantic_intent_service import SemanticIntentService
from src.application.context.observation_runtime_service import ObservationRuntimeService
from src.application.permissions.permission_service import PermissionService
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.prompting.prompt_service import PromptSubmissionService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_overview_service import ProjectOverviewService
from src.application.projects.legacy_smoke_history_service import LegacySmokeHistoryService
from src.application.projects.project_store import PostgresProjectStore
from src.application.registries.registry_service import RegistryService
from src.application.report_service import ReportService
from src.application.resources.session_resource_service import SessionResourceService
from src.application.runtime.runtime_service import RuntimeService
from src.application.sessions.session_service import SessionService
from src.application.security.upload_security_service import UploadSecurityService
from src.application.skills.skill_management_service import SkillManagementService
from src.application.skills.skill_marketplace_service import SkillMarketplaceService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.application.settings.settings_service import SettingsService
from src.application.task_pool_service import TaskPoolService
from src.application.test_cases.generation_pipeline import (
    ModelTestCaseGenerator,
    ProjectTestCaseContextProvider,
)
from src.application.test_cases.case_service import TestCaseService
from src.application.test_cases.case_store import PostgresTestCaseStore
from src.application.test_suites.suite_service import TestSuiteService
from src.application.test_suites.suite_store import PostgresTestSuiteStore
from src.application.test_runs.run_service import TestRunService
from src.application.test_runs.run_store import PostgresTestRunStore
from src.application.test_runs.case_execution import (
    CaseExecutionAdapter,
    resolve_case_execution_tool as resolve_case_execution_entry,
)
from src.application.test_runs.execution_service import TestRunExecutionService
from src.modes.smoke_testing_mode.catalog_store import SmokeCatalogStore
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.context.context_compaction_service import ContextCompactionService
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.application.recorder.drivers import EmbeddedBridge, build_default_registry
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.application.recorder.recording_case_draft_service import RecordingCaseDraftService
from src.application.recorder.recording_approval_service import RecordingApprovalService
from src.application.recorder.ui_resource_assessor import UIResourceAssessor
from src.core.config import get_settings
from src.containers import AppContainer
from src.graph.builder import build_agent_graph
from src.infrastructure.channel_config_store import MySQLChannelConfigStore
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore
from src.infrastructure.recording_store import PostgresRecordingStore
from src.infrastructure.sponsor_config_store import MySQLSponsorConfigStore
from src.modes.security_testing_mode.security_bug_service import SecurityBugService
from src.modes.security_testing_mode.security_bug_store import PostgresSecurityBugStore
from src.registry.agents import AgentRegistry
from src.registry.mcp import MCPRegistry
from src.registry.modes import ModeRegistry
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.session_resource_store import PostgresSessionResourceStore
from src.runtime.control import RuntimeControlRegistry
from src.runtime.postgres_tool_job_store import PostgresToolJobStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    container = AppContainer()

    # ── Async initialization of stores ───────────────────────────────
    project_store = container.project_store()
    project_service = container.project_service()
    await project_service.initialize()

    store = container.session_store()
    await store.initialize()

    task_pool_service = container.task_pool_service()

    agent_registry = container.agent_registry()
    tool_registry = container.tool_registry()

    model_config_store = container.model_config_store()
    model_config_store.initialize()

    oauth_token_service = container.oauth_token_service()
    embedding_runtime_service = container.embedding_runtime_service()

    email_config_store = container.email_config_store()
    email_config_store.initialize()
    channel_config_store = container.channel_config_store()
    channel_config_store.initialize()
    sponsor_config_store = container.sponsor_config_store()
    sponsor_config_store.initialize()

    model_registry = container.model_registry()

    skill_registry = container.skill_registry()
    mcp_registry = container.mcp_registry()
    mode_registry = container.mode_registry()
    skill_runtime_service = container.skill_runtime_service()

    session_resource_store = container.session_resource_store()
    session_resource_service = container.session_resource_service()
    await session_resource_service.initialize()

    mcp_runtime_service = container.mcp_runtime_service()
    session_resource_service.set_browser_cleanup(mcp_runtime_service.close_browser_session)

    artifact_storage_service = container.artifact_storage_service()
    upload_security_service = container.upload_security_service()

    api_doc_store = container.api_doc_store()
    api_docs_service = container.api_docs_service()
    await api_docs_service.initialize()

    integration_catalog_service = container.integration_catalog_service()
    mcp_server_store = container.mcp_server_store()
    await mcp_server_store.initialize()
    await mcp_server_store.migrate_legacy_integrations(
        await integration_catalog_service.list_legacy_mcp_integrations()
    )

    mcp_tool_bridge = container.mcp_tool_bridge()
    mcp_connection_manager = container.mcp_connection_manager()
    mcp_runtime_manager = container.mcp_runtime_manager()
    mcp_manager_service = container.mcp_manager_service()

    skill_management_service = container.skill_management_service()
    skill_marketplace_service = container.skill_marketplace_service()

    memory_store = container.memory_store()
    memory_runtime_service = container.memory_runtime_service()
    await memory_runtime_service.initialize()

    tool_job_store = container.tool_job_store()
    security_bug_store = container.security_bug_store()
    security_bug_service = container.security_bug_service()
    await security_bug_service.initialize()

    knowledge_graph_service = container.knowledge_graph_service()
    knowledge_graph_service.set_project_service(project_service)

    tool_job_service = container.tool_job_service()
    await tool_job_service.initialize()

    report_service = container.report_service()
    permission_service = container.permission_service()
    compatibility_runner_service = container.compatibility_runner_service()

    input_orchestrator_service = container.input_orchestrator_service()
    prompt_service = container.prompt_service()
    prompt_assembly_service = container.prompt_assembly_service()
    observation_runtime_service = container.observation_runtime_service()
    transcript_hygiene_service = container.transcript_hygiene_service()
    runtime_control = container.runtime_control()

    model_runtime_service = container.model_runtime_service()

    test_case_store = container.test_case_store()
    test_case_context_provider = container.test_case_context_provider()
    test_case_generator = container.test_case_generator()
    test_case_service = container.test_case_service()
    await test_case_service.initialize()

    test_suite_store = container.test_suite_store()
    test_suite_service = container.test_suite_service()
    await test_suite_service.initialize()

    test_run_store = container.test_run_store()
    test_run_service = container.test_run_service()
    await test_run_service.initialize()

    legacy_smoke_catalog = container.legacy_smoke_catalog()
    legacy_smoke_history_service = container.legacy_smoke_history_service()
    await legacy_smoke_history_service.initialize()

    project_overview_service = container.project_overview_service()

    input_orchestrator_service.set_semantic_intent_service(
        SemanticIntentService(
            model_runtime_service=model_runtime_service,
            enabled=settings.orchestration.intent_semantic_classifier_enabled,
            deterministic_confidence_threshold=settings.orchestration.intent_deterministic_confidence_threshold,
        )
    )
    tool_runtime_service = ToolRuntimeService(
        request_timeout_seconds=settings.model.llm_request_timeout_seconds,
        settings=settings,
        mcp_runtime_service=mcp_runtime_service,
        memory_runtime_service=memory_runtime_service,
        tool_job_service=tool_job_service,
        session_store=store,
        transcript_hygiene_service=transcript_hygiene_service,
        artifact_storage_service=artifact_storage_service,
        api_docs_service=api_docs_service,
        project_service=project_service,
        knowledge_graph_service=knowledge_graph_service,
        mcp_connection_manager=mcp_connection_manager,
        compatibility_runner_service=compatibility_runner_service,
        session_resource_service=session_resource_service,
        runtime_control=runtime_control,
        security_bug_service=security_bug_service,
    )

    def resolve_case_execution_tool(mode_key: str):
        """从模式清单解析专业执行入口，避免在运行服务中复制模式映射。"""
        return resolve_case_execution_entry(mode_registry, tool_registry, mode_key)

    case_execution_adapter = CaseExecutionAdapter(
        tool_resolver=resolve_case_execution_tool,
        runtime_service=tool_runtime_service,
        tool_job_service=tool_job_service,
    )
    test_run_execution_service = TestRunExecutionService(
        run_service=test_run_service,
        test_case_service=test_case_service,
        adapter=case_execution_adapter,
        session_store=store,
        permission_service=permission_service,
        tool_job_service=tool_job_service,
        security_settings=settings,
    )
    graph = build_agent_graph(
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        model_registry=model_registry,
        skill_registry=skill_registry,
        skill_runtime_service=skill_runtime_service,
        mcp_runtime_service=mcp_runtime_service,
        memory_runtime_service=memory_runtime_service,
        permission_service=permission_service,
        prompt_assembly_service=prompt_assembly_service,
        model_runtime_service=model_runtime_service,
        tool_runtime_service=tool_runtime_service,
        tool_job_service=tool_job_service,
        tool_message_max_chars=settings.orchestration.tool_message_max_chars,
    )
    context_compaction_service = ContextCompactionService(
        model_runtime_service=model_runtime_service,
        transcript_hygiene_service=transcript_hygiene_service,
        watermark=settings.orchestration.context_compaction_watermark,
        max_tail_messages=settings.orchestration.context_max_tail_messages,
    )
    runtime_service = RuntimeService(
        graph=graph,
        model_runtime_service=model_runtime_service,
        tool_runtime_service=tool_runtime_service,
        tool_registry=tool_registry,
        runtime_control=runtime_control,
        transcript_hygiene_service=transcript_hygiene_service,
        max_iterations=settings.orchestration.runtime_max_iterations,
        session_resource_service=session_resource_service,
        context_compaction_service=context_compaction_service,
        context_max_tail_messages=settings.orchestration.context_max_tail_messages,
    )

    app.state.settings = settings
    app.state.store = store
    app.state.agent_registry = agent_registry
    app.state.tool_registry = tool_registry
    app.state.model_config_store = model_config_store
    app.state.email_config_store = email_config_store
    app.state.channel_config_store = channel_config_store
    app.state.sponsor_config_store = sponsor_config_store
    app.state.model_registry = model_registry
    app.state.skill_registry = skill_registry
    app.state.skill_management_service = skill_management_service
    app.state.skill_marketplace_service = skill_marketplace_service
    app.state.mcp_registry = mcp_registry
    app.state.mode_registry = mode_registry
    app.state.skill_runtime_service = skill_runtime_service
    app.state.mcp_runtime_service = mcp_runtime_service
    app.state.artifact_storage_service = artifact_storage_service
    app.state.upload_security_service = upload_security_service
    app.state.api_docs_service = api_docs_service
    app.state.api_doc_store = api_doc_store
    app.state.project_store = project_store
    app.state.project_service = project_service
    app.state.integration_catalog_service = integration_catalog_service
    app.state.mcp_server_store = mcp_server_store
    app.state.mcp_tool_bridge = mcp_tool_bridge
    app.state.mcp_connection_manager = mcp_connection_manager
    app.state.mcp_runtime_manager = mcp_runtime_manager
    app.state.mcp_manager_service = mcp_manager_service
    app.state.memory_store = memory_store
    app.state.session_resource_store = session_resource_store
    app.state.session_resource_service = session_resource_service
    app.state.memory_runtime_service = memory_runtime_service
    app.state.session_backend = settings.orchestration.session_backend
    app.state.task_pool_service = task_pool_service
    app.state.tool_job_store = tool_job_store
    app.state.tool_job_service = tool_job_service
    app.state.security_bug_store = security_bug_store
    app.state.security_bug_service = security_bug_service
    app.state.report_service = report_service
    app.state.tool_job_backend = settings.orchestration.tool_job_backend
    app.state.knowledge_graph_service = knowledge_graph_service
    app.state.project_overview_service = project_overview_service
    app.state.test_case_store = test_case_store
    app.state.test_case_context_provider = test_case_context_provider
    app.state.test_case_generator = test_case_generator
    app.state.test_case_service = test_case_service
    app.state.test_suite_store = test_suite_store
    app.state.test_suite_service = test_suite_service
    app.state.test_run_store = test_run_store
    app.state.test_run_service = test_run_service
    app.state.case_execution_adapter = case_execution_adapter
    app.state.test_run_execution_service = test_run_execution_service
    app.state.legacy_smoke_history_service = legacy_smoke_history_service
    app.state.memory_backend = memory_runtime_service.backend
    app.state.ui_graph_backend = settings.orchestration.ui_graph_backend
    app.state.permission_service = permission_service
    app.state.input_orchestrator_service = input_orchestrator_service
    app.state.prompt_service = prompt_service
    app.state.prompt_assembly_service = prompt_assembly_service
    app.state.observation_runtime_service = observation_runtime_service
    app.state.transcript_hygiene_service = transcript_hygiene_service
    app.state.runtime_control = runtime_control
    app.state.graph = graph
    app.state.model_runtime_service = model_runtime_service
    app.state.tool_runtime_service = tool_runtime_service
    app.state.mail_service = tool_runtime_service._mail_service
    app.state.docker_management_service = DockerManagementService(settings)
    tencent_auth_monitor = TencentAuthMonitor(
        settings=settings,
        email_config_store=email_config_store,
        registry=app.state.mail_service._registry,
    )
    app.state.tencent_auth_monitor = tencent_auth_monitor
    app.state.compatibility_runner_service = compatibility_runner_service
    app.state.runtime_service = runtime_service
    session_service = SessionService(
        store=store,
        input_orchestrator_service=input_orchestrator_service,
        runtime_service=runtime_service,
        mode_registry=mode_registry,
        memory_runtime_service=memory_runtime_service,
        observation_runtime_service=observation_runtime_service,
        transcript_hygiene_service=transcript_hygiene_service,
        session_resource_service=session_resource_service,
        project_service=project_service,
    )
    coordinator_runtime_service = CoordinatorRuntimeService(
        settings=settings,
        store=store,
        session_service=session_service,
        agent_registry=agent_registry,
    )
    tool_runtime_service.set_coordinator_runtime_service(coordinator_runtime_service)
    tool_runtime_service.set_model_registry(model_registry)
    tool_runtime_service.set_session_store(store)
    app.state.coordinator_runtime_service = coordinator_runtime_service
    app.state.session_service = session_service
    app.state.registry_service = RegistryService(
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        model_registry=model_registry,
        skill_registry=skill_registry,
        mcp_registry=mcp_registry,
        mode_registry=mode_registry,
        mcp_manager_service=mcp_manager_service,
    )
    app.state.oauth_token_service = oauth_token_service
    app.state.embedding_runtime_service = embedding_runtime_service
    app.state.settings_service = SettingsService(
        settings=settings,
        model_config_store=model_config_store,
        email_config_store=email_config_store,
        channel_config_store=channel_config_store,
        oauth_token_service=oauth_token_service,
        embedding_runtime_service=embedding_runtime_service,
    )
    await tencent_auth_monitor.startup()
    await test_run_service.start_lease_reaper()

    # UI 录制域（方案第 8 章）：PG 事件流 + Memgraph 固化 + embedded 桥 + 会话编排
    recording_store = PostgresRecordingStore(settings)
    await recording_store.initialize()
    recording_graph_store = RecordingGraphStore(settings)
    embedded_bridge = EmbeddedBridge()
    recorder_service = RecorderSessionService(
        settings=settings,
        store=recording_store,
        graph_store=recording_graph_store,
        bridge=embedded_bridge,
        registry=build_default_registry(embedded_bridge, settings=settings),
    )
    app.state.recording_store = recording_store
    app.state.recording_graph_store = recording_graph_store
    app.state.embedded_bridge = embedded_bridge
    app.state.recorder_service = recorder_service
    # P2-2：录制 → 用例草稿（进既有评审 → 固定版本 → 套件冻结链路）
    recording_case_draft_service = RecordingCaseDraftService(
        recorder_service=recorder_service,
        test_case_service=test_case_service,
    )
    app.state.recording_case_draft_service = recording_case_draft_service

    # UI 录制编排接线（方案第 4 章 / P0-8）：
    # 三源资源检索（图谱/用例/记忆）→ 录制审批 → 审批通过自动 launch
    recording_approval_service = RecordingApprovalService(
        recorder_service=recorder_service,
        session_store=store,
    )
    session_service.set_recording_approval_service(recording_approval_service)
    ui_resource_assessor = UIResourceAssessor(
        settings=settings,
        test_case_service=test_case_service,
        memory_runtime_service=memory_runtime_service,
        memgraph_provider=MemgraphRuntimeProvider(settings),
    )

    async def _project_catalog() -> list[dict[str, Any]]:
        page = await project_service.list(status=None, query=None, limit=50, offset=0)
        return [
            {"project_id": item.id, "name": getattr(item, "name", "")}
            for item in page.items
        ]

    tool_runtime_service.ui_automation_mode_runtime.set_recording_orchestration(
        resource_assessor=ui_resource_assessor,
        recording_approval_service=recording_approval_service,
        project_catalog_provider=_project_catalog,
    )

    try:
        yield
    finally:
        await test_run_service.stop_lease_reaper()
        await tencent_auth_monitor.shutdown()
        await mcp_connection_manager.shutdown()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix=settings.api_v1_prefix)
app.include_router(knowledge_router, prefix=settings.api_v1_prefix)
app.include_router(registry_router, prefix=settings.api_v1_prefix)
app.include_router(attachments_router, prefix=settings.api_v1_prefix)
app.include_router(api_docs_router, prefix=settings.api_v1_prefix)
app.include_router(projects_router, prefix=settings.api_v1_prefix)
app.include_router(compatibility_router, prefix=settings.api_v1_prefix)
app.include_router(integrations_router, prefix=settings.api_v1_prefix)
app.include_router(reports_router, prefix=settings.api_v1_prefix)
app.include_router(task_pool_router, prefix=settings.api_v1_prefix)
app.include_router(sessions_router, prefix=settings.api_v1_prefix)
app.include_router(test_cases_router, prefix=settings.api_v1_prefix)
app.include_router(test_suites_router, prefix=settings.api_v1_prefix)
app.include_router(test_runs_router, prefix=settings.api_v1_prefix)
app.include_router(security_bugs_router, prefix=settings.api_v1_prefix)
app.include_router(settings_router, prefix=settings.api_v1_prefix)
app.include_router(sponsors_router, prefix=settings.api_v1_prefix)
app.include_router(oauth_router, prefix=settings.api_v1_prefix)
app.include_router(mail_router, prefix=settings.api_v1_prefix)
app.include_router(docker_router, prefix=settings.api_v1_prefix)
app.include_router(recordings_router, prefix=settings.api_v1_prefix)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="127.0.0.1", port=1032, reload=False)
