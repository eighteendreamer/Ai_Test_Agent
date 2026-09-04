from __future__ import annotations

from dependency_injector import containers, providers

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.compatibility import CompatibilityRunnerService
from src.application.context.context_compaction_service import ContextCompactionService
from src.application.context.embedding_runtime_service import EmbeddingRuntimeService
from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.context.observation_runtime_service import ObservationRuntimeService
from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.documents.api_doc_store import PostgresApiDocStore
from src.application.documents.api_docs_service import ApiDocsService
from src.application.docker_management_service import DockerManagementService
from src.application.integrations.integration_catalog_service import IntegrationCatalogService
from src.application.intent.semantic_intent_service import SemanticIntentService
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService
from src.application.mcp.host.connection_manager import McpConnectionManager
from src.application.mcp.host.tool_bridge import McpToolBridge
from src.application.mcp.manager_service import MCPManagerService
from src.application.mcp.runtime_manager import MCPRuntimeManager
from src.application.mcp.server_store import PostgresMCPServerStore
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.models.oauth_token_service import OAuthTokenService
from src.application.orchestration.coordinator_runtime_service import CoordinatorRuntimeService
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.permissions.permission_service import PermissionService
from src.application.projects.legacy_smoke_history_service import LegacySmokeHistoryService
from src.application.projects.project_overview_service import ProjectOverviewService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.prompting.prompt_service import PromptSubmissionService
from src.application.recorder.drivers import EmbeddedBridge, build_default_registry
from src.application.recorder.recording_approval_service import RecordingApprovalService
from src.application.recorder.recording_case_draft_service import RecordingCaseDraftService
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.application.recorder.ui_resource_assessor import UIResourceAssessor
from src.application.report_service import ReportService
from src.application.resources.session_resource_service import SessionResourceService
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.application.security.upload_security_service import UploadSecurityService
from src.modes.security_testing_mode.security_bug_service import SecurityBugService
from src.application.sessions.session_service import SessionService
from src.application.skills.skill_management_service import SkillManagementService
from src.application.skills.skill_marketplace_service import SkillMarketplaceService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.application.task_pool_service import TaskPoolService
from src.application.test_cases.case_service import TestCaseService
from src.application.test_cases.case_store import PostgresTestCaseStore
from src.application.test_cases.generation_pipeline import (
    ModelTestCaseGenerator,
    ProjectTestCaseContextProvider,
)
from src.application.test_runs.case_execution import CaseExecutionAdapter
from src.application.test_runs.execution_service import TestRunExecutionService
from src.application.test_runs.run_service import TestRunService
from src.application.test_runs.run_store import PostgresTestRunStore
from src.application.test_suites.suite_service import TestSuiteService
from src.application.test_suites.suite_store import PostgresTestSuiteStore
from src.core.config import Settings, get_settings
from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.infrastructure.channel_config_store import MySQLChannelConfigStore
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore
from src.infrastructure.recording_store import PostgresRecordingStore
from src.infrastructure.sponsor_config_store import MySQLSponsorConfigStore
from src.modes.security_testing_mode.security_bug_store import PostgresSecurityBugStore
from src.modes.smoke_testing_mode.catalog_store import SmokeCatalogStore
from src.registry.agents import AgentRegistry
from src.registry.mcp import MCPRegistry
from src.registry.modes import ModeRegistry
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.runtime.session_resource_store import PostgresSessionResourceStore


class AppContainer(containers.DeclarativeContainer):
    """Declarative dependency graph for the application.

    Async initialization (await store.initialize()) and setter-injection
    (set_browser_cleanup, set_project_service, etc.) remain in the lifespan
    because dependency-injector providers are synchronous.
    """

    settings: providers.Singleton[Settings] = providers.Singleton(get_settings)

    # ── Config stores (MySQL) ────────────────────────────────────────
    model_config_store = providers.Singleton(MySQLModelConfigStore, settings=settings)
    email_config_store = providers.Singleton(MySQLEmailConfigStore, settings=settings)
    channel_config_store = providers.Singleton(MySQLChannelConfigStore, settings=settings)
    sponsor_config_store = providers.Singleton(MySQLSponsorConfigStore, settings=settings)

    # ── Registries (stateless, no deps) ──────────────────────────────
    agent_registry = providers.Singleton(AgentRegistry)
    tool_registry = providers.Singleton(ToolRegistry)
    model_registry = providers.Singleton(ModelRegistry, store=model_config_store)
    skill_registry = providers.Singleton(SkillRegistry)
    mcp_registry = providers.Singleton(MCPRegistry)
    mode_registry = providers.Singleton(ModeRegistry)
    runtime_control = providers.Singleton(RuntimeControlRegistry)

    # ── Data stores (Postgres) ───────────────────────────────────────
    project_store = providers.Singleton(PostgresProjectStore, settings=settings)
    session_store = providers.Singleton(PostgresSessionStore, settings=settings)
    session_resource_store = providers.Singleton(PostgresSessionResourceStore, settings=settings)
    tool_job_store = providers.Singleton(PostgresToolJobStore, settings=settings)
    security_bug_store = providers.Singleton(PostgresSecurityBugStore, settings=settings)
    memory_store = providers.Singleton(PostgresVectorMemoryStore, settings=settings)
    api_doc_store = providers.Singleton(PostgresApiDocStore, settings=settings)
    test_case_store = providers.Singleton(PostgresTestCaseStore, settings=settings)
    test_suite_store = providers.Singleton(PostgresTestSuiteStore, settings=settings)
    test_run_store = providers.Singleton(PostgresTestRunStore, settings=settings)
    mcp_server_store = providers.Singleton(PostgresMCPServerStore, settings=settings)
    recording_store = providers.Singleton(PostgresRecordingStore, settings=settings)

    # ── Core services ────────────────────────────────────────────────
    oauth_token_service = providers.Singleton(
        OAuthTokenService,
        settings=settings,
        request_timeout=providers.Callable(
            lambda s: s.model.llm_request_timeout_seconds, settings,
        ),
    )
    project_service = providers.Singleton(ProjectService, store=project_store)
    task_pool_service = providers.Singleton(TaskPoolService, store=session_store)
    permission_service = providers.Singleton(PermissionService)
    transcript_hygiene_service = providers.Singleton(TranscriptHygieneService)
    prompt_assembly_service = providers.Singleton(PromptAssemblyService)
    observation_runtime_service = providers.Singleton(ObservationRuntimeService)
    skill_runtime_service = providers.Singleton(SkillRuntimeService, skill_registry=skill_registry)

    # ── Embedding & Memory ───────────────────────────────────────────
    embedding_runtime_service = providers.Singleton(
        EmbeddingRuntimeService,
        model_config_store=model_config_store,
        settings=settings,
        oauth_token_service=oauth_token_service,
    )
    memory_runtime_service = providers.Singleton(
        MemoryRuntimeService,
        memory_store=memory_store,
        top_k=providers.Callable(lambda s: s.orchestration.memory_top_k, settings),
        embedding_runtime_service=embedding_runtime_service,
    )

    # ── Model runtime ────────────────────────────────────────────────
    model_runtime_service = providers.Singleton(
        ModelRuntimeService,
        model_registry=model_registry,
        settings=settings,
        oauth_token_service=oauth_token_service,
    )

    # ── Storage & Security ───────────────────────────────────────────
    artifact_storage_service = providers.Singleton(ArtifactStorageService, settings=settings)
    upload_security_service = providers.Singleton(
        UploadSecurityService,
        settings=settings,
        artifact_storage_service=artifact_storage_service,
    )

    # ── Session resources (needed by MCP) ────────────────────────────
    session_resource_service = providers.Singleton(
        SessionResourceService,
        store=session_resource_store,
    )

    # ── MCP ──────────────────────────────────────────────────────────
    mcp_tool_bridge = providers.Singleton(McpToolBridge, tool_registry=tool_registry)
    mcp_runtime_service = providers.Singleton(
        MCPRuntimeService,
        mcp_registry=mcp_registry,
        settings=settings,
        session_resource_service=session_resource_service,
    )
    mcp_connection_manager = providers.Singleton(
        McpConnectionManager,
        settings=settings,
        mcp_server_store=mcp_server_store,
        tool_bridge=mcp_tool_bridge,
    )
    mcp_runtime_manager = providers.Singleton(
        MCPRuntimeManager,
        builtin_registry=mcp_registry,
        mcp_runtime_service=mcp_runtime_service,
        connection_manager=mcp_connection_manager,
    )
    mcp_manager_service = providers.Singleton(
        MCPManagerService,
        builtin_registry=mcp_registry,
        mcp_server_store=mcp_server_store,
        runtime_manager=mcp_runtime_manager,
        connection_manager=mcp_connection_manager,
    )

    # ── Skills ───────────────────────────────────────────────────────
    skill_management_service = providers.Singleton(
        SkillManagementService,
        skill_registry=skill_registry,
        upload_security_service=upload_security_service,
    )
    skill_marketplace_service = providers.Singleton(
        SkillMarketplaceService,
        skill_management_service=skill_management_service,
    )

    # ── Documents & Knowledge ────────────────────────────────────────
    api_docs_service = providers.Singleton(
        ApiDocsService,
        settings=settings,
        artifact_storage_service=artifact_storage_service,
        upload_security_service=upload_security_service,
        catalog_store=api_doc_store,
        project_service=project_service,
    )
    integration_catalog_service = providers.Singleton(
        IntegrationCatalogService,
        settings=settings,
    )
    knowledge_graph_service = providers.Singleton(KnowledgeGraphService, settings=settings)

    # ── Tool & Job infrastructure ────────────────────────────────────
    tool_job_service = providers.Singleton(
        ToolJobService,
        store=tool_job_store,
        heartbeat_timeout_seconds=providers.Callable(
            lambda s: s.orchestration.tool_job_heartbeat_timeout_seconds, settings,
        ),
    )
    security_bug_service = providers.Singleton(
        SecurityBugService,
        security_bug_store,
        reproduction_required=providers.Callable(
            lambda s: s.security.security_bug_reproduction_required, settings,
        ),
    )
    report_service = providers.Singleton(
        ReportService,
        store=session_store,
        tool_job_service=tool_job_service,
    )

    compatibility_runner_service = providers.Singleton(
        CompatibilityRunnerService,
        settings=settings,
        artifact_storage_service=artifact_storage_service,
    )

    # ── Orchestration ────────────────────────────────────────────────
    input_orchestrator_service = providers.Singleton(
        InputOrchestratorService,
        mode_registry=mode_registry,
    )
    prompt_service = providers.Singleton(
        PromptSubmissionService,
        input_orchestrator=input_orchestrator_service,
    )

    # ── Test engineering ─────────────────────────────────────────────
    test_case_context_provider = providers.Singleton(
        ProjectTestCaseContextProvider,
        api_docs_service=api_docs_service,
        knowledge_graph_service=knowledge_graph_service,
        session_store=session_store,
    )
    test_case_generator = providers.Singleton(
        ModelTestCaseGenerator,
        model_runtime_service=model_runtime_service,
        skill_runtime_service=skill_runtime_service,
    )
    test_case_service = providers.Singleton(
        TestCaseService,
        store=test_case_store,
        project_service=project_service,
        context_provider=test_case_context_provider,
        generator=test_case_generator,
    )
    test_suite_service = providers.Singleton(
        TestSuiteService,
        store=test_suite_store,
        project_service=project_service,
        test_case_service=test_case_service,
    )
    test_run_service = providers.Singleton(
        TestRunService,
        store=test_run_store,
        project_service=project_service,
        suite_service=test_suite_service,
        test_case_service=test_case_service,
        session_store=session_store,
        tool_job_service=tool_job_service,
        lease_reaper_interval_seconds=providers.Callable(
            lambda s: s.orchestration.test_run_lease_reaper_interval_seconds, settings,
        ),
    )

    # ── Legacy smoke ─────────────────────────────────────────────────
    legacy_smoke_catalog = providers.Singleton(SmokeCatalogStore, settings=settings)
    legacy_smoke_history_service = providers.Singleton(
        LegacySmokeHistoryService,
        project_service=project_service,
        catalog=legacy_smoke_catalog,
    )

    # ── Project overview ─────────────────────────────────────────────
    project_overview_service = providers.Singleton(
        ProjectOverviewService,
        project_service=project_service,
        api_doc_store=api_doc_store,
        session_store=session_store,
        knowledge_graph_service=knowledge_graph_service,
        test_case_store=test_case_store,
        test_suite_store=test_suite_store,
        test_run_store=test_run_store,
    )

    # ── Recording ────────────────────────────────────────────────────
    recording_graph_store = providers.Singleton(RecordingGraphStore, settings=settings)
    embedded_bridge = providers.Singleton(EmbeddedBridge)
