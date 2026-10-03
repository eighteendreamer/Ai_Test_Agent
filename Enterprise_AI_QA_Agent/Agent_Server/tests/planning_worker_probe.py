"""Isolated process acceptance host for the production planning Handler/Worker.

Reads exact fixture settings from stdin; never prints their contents. This host
does not replace, or claim acceptance of, the full production app lifespan.
"""
from __future__ import annotations

import asyncio
import json
import sys

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.documents.api_doc_store import PostgresApiDocStore
from src.application.documents.api_docs_service import ApiDocsService
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.application.test_cases.case_service import TestCaseService
from src.application.test_cases.case_store import PostgresTestCaseStore
from src.application.test_cases.planning_service import CasePlanningService
from src.application.test_cases.planning_sources import ProjectPlanningSources
from src.application.test_cases.planning_store import PostgresCasePlanStore
from src.core.config import Settings
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.registry.models import ModelRegistry
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.redis_task_worker import RedisTaskWorker


async def run(input_record):
    settings = Settings.model_validate(input_record["settings"])
    projects = ProjectService(store=PostgresProjectStore(settings))
    cases = TestCaseService(store=PostgresTestCaseStore(settings), project_service=projects)
    objects = ArtifactStorageService(settings)
    docs = ApiDocsService(settings=settings, artifact_storage_service=objects,
        catalog_store=PostgresApiDocStore(settings), project_service=projects)
    runtime = ModelRuntimeService(ModelRegistry(MySQLModelConfigStore(settings)), settings)
    planner = CasePlanningService(store=PostgresCasePlanStore(settings), config_store=MySQLApplicationConfigStore(settings),
        project_service=projects, sources=ProjectPlanningSources(case_service=cases, api_docs_service=docs), model_runtime_service=runtime)
    jobs = PostgresPlanningTaskStore(settings, lease_seconds=3)
    queue = RedisTaskQueue(settings.database.redis_url, stream=input_record["stream"], group=input_record["group"])
    try:
        await queue.connect()
        worker = RedisTaskWorker(queue, consumer=input_record["worker_id"], block_ms=10, reclaim_idle_ms=100,
            handler=PlanningTaskHandler(planner, jobs, worker_id=input_record["worker_id"]))
        # One poll may legitimately return zero while a delivery is not ready.
        # Exercise the production polling loop, bounded by the acceptance host.
        async with asyncio.timeout(20):
            processed = 0
            while not processed:
                processed = await worker.run_once()
                if not processed:
                    await asyncio.sleep(.05)
        print(f"processed={processed}", flush=True)
    finally:
        await queue.close(); await objects.close()


if __name__ == "__main__":
    asyncio.run(run(json.loads(sys.stdin.readline())))
