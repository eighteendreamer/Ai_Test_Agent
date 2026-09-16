"""Real PostgreSQL Outbox + Redis Worker + RediSearch rebuild acceptance."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.application.context.memory_vector_rebuild_service import (
    MemoryVectorRebuildService,
)
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.infrastructure.redis_vector_store import RedisVectorStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.postgres_vector_rebuild_job_store import (
    PostgresVectorRebuildJobStore,
)
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.vector_rebuild_task_handler import VectorRebuildTaskHandler
from src.schemas.memory import MemoryVectorRecord


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_VECTOR_REBUILD_PIPELINE_TESTS") != "1",
    reason=(
        "set RUN_LIVE_VECTOR_REBUILD_PIPELINE_TESTS=1 for PostgreSQL/Redis "
        "vector task acceptance"
    ),
)
@pytest.mark.asyncio
async def test_live_vector_rebuild_task_pipeline() -> None:
    suffix = uuid4().hex[:12]
    task_id = f"task_{suffix}"
    version = f"emb_{suffix}"
    stream = f"qa:test:{suffix}:tasks:vector_rebuild"
    base = get_settings()
    settings = base.model_copy(
        update={
            "database": base.database.model_copy(
                update={
                    "postgres_task_outbox_table": f"test_outbox_{suffix}",
                    "postgres_vector_rebuild_job_table": f"test_vector_job_{suffix}",
                }
            )
        }
    )

    class Memory:
        records = [
            MemoryVectorRecord(
                id="memory-1",
                embedding=[1.0, 0.0],
                stale=False,
                metadata={
                    "embedding_version": version,
                    "embedding_dimension": 2,
                    "project_id": "acceptance",
                },
            ),
            MemoryVectorRecord(
                id="memory-2",
                embedding=[0.0, 1.0],
                stale=False,
                metadata={
                    "embedding_version": version,
                    "embedding_dimension": 2,
                    "project_id": "acceptance",
                },
            ),
        ]

        async def vector_inventory(self, embedding_version):
            return {2: 2} if embedding_version == version else {}

        async def list_vector_records(
            self, embedding_version, *, after_id=None, limit=64,
        ):
            assert embedding_version == version
            return [
                item
                for item in self.records
                if after_id is None or item.id > after_id
            ][:limit]

    class IsolatedVectorStore(RedisVectorStore):
        def index_name(self, entity, embedding_version):
            return f"qa:test:{suffix}:index:{entity}:{embedding_version}"

        def prefix(self, entity, embedding_version):
            return f"qa:test:{suffix}:doc:{entity}:{embedding_version}:"

        def active_key(self, entity):
            return f"qa:test:{suffix}:active:{entity}"

    outbox = PostgresTaskOutbox(settings)
    jobs = PostgresVectorRebuildJobStore(settings, lease_seconds=30)
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=stream,
        group=f"qa:test:{suffix}:workers",
    )
    vector = IsolatedVectorStore(settings.database.redis_url)
    index_name = vector.index_name("memory", version)
    try:
        await outbox.initialize()
        await jobs.initialize()
        await queue.connect()
        payload = {
            "schema_version": 1,
            "task_id": task_id,
            "task_type": "vector_rebuild_task",
            "request_id": f"req_{suffix}",
            "trace_id": f"trace_{suffix}",
            "session_id": None,
            "turn_id": None,
            "project_id": None,
            "run_id": None,
            "run_item_id": None,
            "attempt_id": None,
            "worker_id": None,
            "resource_id": None,
            "priority": 10,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entity": "memory",
            "embedding_version": version,
            "activate": True,
            "payload": {},
        }
        assert await jobs.enqueue(
            task_id=task_id,
            embedding_version=version,
            activate=True,
            stream=stream,
            outbox_table=settings.database.postgres_task_outbox_table,
            payload=payload,
        )
        assert await outbox.relay_once(queue, limit=1) == 1

        service = MemoryVectorRebuildService(
            Memory(),
            vector,
            batch_size=1,
            validation_timeout_seconds=5,
            validation_poll_interval_seconds=0.05,
        )
        worker = RedisTaskWorker(
            queue,
            consumer=f"worker-{suffix}",
            handler=VectorRebuildTaskHandler(
                service, jobs, worker_id=f"worker-{suffix}",
            ),
            block_ms=0,
            max_retries=1,
            reclaim_idle_ms=1000,
            retry_base_ms=10,
            retry_max_ms=10,
            task_timeout_seconds=30,
        )
        assert await worker.run_once() == 1

        job = await jobs.get(task_id)
        assert job is not None
        assert job.status == "index_activated"
        assert job.dimension == 2
        assert job.source_count == 2 and job.replicated == 2
        assert job.active_index == index_name
        assert await vector.get_active_version(entity="memory") == version
    finally:
        if vector._client is not None:
            await vector.deactivate_index(entity="memory")
            indexes = set(await vector._client.execute_command("FT._LIST"))
            if index_name.encode() in indexes or index_name in indexes:
                await vector._client.execute_command("FT.DROPINDEX", index_name, "DD")
            await vector.close()
        if queue._client is not None:
            await queue._client.delete(stream, f"{stream}:dead_letter")
        await queue.close()
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DROP TABLE IF EXISTS {settings.database.postgres_task_outbox_table}"
                )
                cur.execute(
                    f"DROP TABLE IF EXISTS {settings.database.postgres_vector_rebuild_job_table}"
                )
            conn.commit()
