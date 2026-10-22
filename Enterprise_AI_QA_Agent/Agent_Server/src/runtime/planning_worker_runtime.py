"""Own planning and append-only Run revisions without API execution services.

Uses the same AppContainer providers and source wiring as main.py. Planning
retains vector recall and finalized recording reads, but starts no Session,
browser, tool execution, recorder orchestration or background recovery loops.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from src.containers import AppContainer
from src.application.test_cases.planning_service import CasePlanningService
from src.application.test_runs.plan_run_service import CasePlanRunService
from src.application.test_runs.revision_planning_service import RunRevisionPlanningService
from src.application.documents.parsing_service import DocumentParsingService
from src.core.config import Settings
from src.core.request_context import bind_request_context
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.redis_vector_store import RedisVectorStore
from src.infrastructure.recording_scene_store import PostgresRecordingSceneStore
from src.infrastructure.sqlalchemy_runtime import sqlalchemy_engine_lifespan
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.postgres_document_parse_store import PostgresDocumentParseStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanningWorkerRuntime:
    planner: CasePlanningService
    tasks: PostgresPlanningTaskStore
    queue: RedisTaskQueue
    revisions: RunRevisionPlanningService


@asynccontextmanager
async def planning_worker_runtime(settings: Settings):
    stream = settings.orchestration.case_planning_task_stream
    if stream not in settings.orchestration.task_stream_names:
        logger.error("case_planning_worker_stream_unregistered", extra={"queue": stream})
        raise ValueError("Register the case_planning stream in REDIS_TASK_OUTBOX_STREAMS")
    container = AppContainer()
    container.settings.override(settings)

    async def initialize(resource, operation):
        with bind_request_context(resource_id=resource, task_type="case_planning", queue=stream):
            logger.info("case_planning_worker_resource_initializing", extra={"status": "initializing"})
            try:
                await operation
            except asyncio.CancelledError:
                logger.warning("case_planning_worker_startup_interrupted", extra={"status": "interrupted"})
                raise
            except Exception as exc:
                logger.error("case_planning_worker_resource_initialization_failed",
                    extra={"status": "startup_failed", "error_type": type(exc).__name__})
                raise
            logger.info("case_planning_worker_resource_initialized", extra={"status": "ready"})

    async with AsyncExitStack() as cleanup:
        await cleanup.enter_async_context(sqlalchemy_engine_lifespan(settings))
        # Register each close before initialization, including partial failure.
        objects = container.artifact_storage_service()
        cleanup.push_async_callback(objects.close)
        embedding_runtime = container.embedding_runtime_service()
        cleanup.push_async_callback(embedding_runtime.close)
        graphs = container.recording_graph_store()
        cleanup.push_async_callback(graphs.close)
        vector = None
        if settings.orchestration.redis_vector_enabled:
            vector = RedisVectorStore(settings.database.redis_url,
                socket_timeout_seconds=settings.orchestration.redis_vector_socket_timeout_seconds)
            cleanup.push_async_callback(vector.close)
            await initialize("vector", vector.connect())
        await initialize("model_config", asyncio.to_thread(container.model_config_store().initialize))
        await initialize("application_config", asyncio.to_thread(container.application_config_store().initialize))
        await initialize("projects", container.project_service().initialize())
        await initialize("cases", container.test_case_service().initialize())
        await initialize("documents", container.api_docs_service().initialize())
        document_parsing = DocumentParsingService(store=PostgresDocumentParseStore(settings),
            documents=container.api_docs_service(), projects=container.project_service(),
            configs=container.document_parser_config_service(), objects=objects, stream=None)
        await initialize("document_parsing", document_parsing.initialize())
        container.api_docs_service().set_parsing_service(document_parsing)
        await initialize("recordings", container.recording_store().initialize())
        container.project_planning_sources().set_recording_sources(
            recording_store=container.recording_store(), recording_graph_store=graphs)
        scenes = PostgresRecordingSceneStore(settings)
        await initialize("recording_scenes", scenes.initialize())
        container.project_planning_sources().set_scene_sources(scenes)
        embeddings = container.test_case_embedding_service()
        embeddings.set_vector_store(vector)
        await initialize("case_embeddings", embeddings.initialize())
        planner = container.case_planning_service()
        await initialize("planner", planner.initialize())
        await initialize("outbox", PostgresTaskOutbox(settings).initialize())
        tasks = PostgresPlanningTaskStore(settings,
            lease_seconds=settings.orchestration.task_outbox_lease_seconds)
        await initialize("planning_tasks", tasks.initialize())
        # Replanning can publish an append-only revision, but starts no execution
        # services or lease reaper. Existing Runs retain their claimed snapshots.
        suites, runs = container.test_suite_store(), container.test_run_store()
        # The API has already initialized these tables before creating the Run.
        # Ordinary planning startup must not migrate execution tables.
        revisions = RunRevisionPlanningService(tasks=tasks, planner=planner, runs=runs,
            projects=container.project_service(), dispatch_stream=stream,
            revision_service=CasePlanRunService(run_store=runs, suite_store=suites,
                plan_store=container.case_plan_store(), case_service=container.test_case_service(),
                project_service=container.project_service()))
        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
            group=f"{settings.orchestration.redis_task_consumer_group}:case_planning",
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
            retention_seconds=settings.orchestration.redis_task_retention_seconds,
            retention_interval_seconds=settings.orchestration.redis_task_retention_interval_seconds,
            retention_batch_size=settings.orchestration.redis_task_retention_batch_size)
        cleanup.push_async_callback(queue.close)
        await initialize("task_queue", queue.connect())
        logger.info("case_planning_worker_runtime_started", extra={"queue": stream})
        try:
            yield PlanningWorkerRuntime(planner=planner, tasks=tasks, queue=queue, revisions=revisions)
        finally:
            logger.info("case_planning_worker_runtime_stopping", extra={"queue": stream})
