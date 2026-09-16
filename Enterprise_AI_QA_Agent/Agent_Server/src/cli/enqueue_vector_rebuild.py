"""Create a PostgreSQL-backed vector rebuild task and Outbox intent."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from src.core.config import get_settings
from src.core.request_context import new_id
from src.infrastructure.redis_vector_store import RedisVectorStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.postgres_vector_rebuild_job_store import (
    PostgresVectorRebuildJobStore,
)


async def _run(*, embedding_version: str, activate: bool) -> None:
    settings = get_settings()
    RedisVectorStore.index_name("memory", embedding_version)
    outbox = PostgresTaskOutbox(settings)
    jobs = PostgresVectorRebuildJobStore(
        settings,
        lease_seconds=int(settings.orchestration.redis_task_timeout_seconds),
    )
    await outbox.initialize()
    await jobs.initialize()
    task_id = new_id("task")
    request_id = new_id("req")
    trace_id = new_id("trace")
    stream = settings.orchestration.vector_rebuild_task_stream
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "task_type": "vector_rebuild_task",
        "request_id": request_id,
        "trace_id": trace_id,
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
        "embedding_version": embedding_version,
        "activate": activate,
        "payload": {},
    }
    inserted = await jobs.enqueue(
        task_id=task_id,
        embedding_version=embedding_version,
        activate=activate,
        stream=stream,
        outbox_table=settings.database.postgres_task_outbox_table,
        payload=payload,
    )
    print(
        json.dumps(
            {
                "created": inserted,
                "task_id": task_id,
                "request_id": request_id,
                "trace_id": trace_id,
                "stream": stream,
                "embedding_version": embedding_version,
                "activate": activate,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enqueue a durable low-priority Redis vector rebuild."
    )
    parser.add_argument("--embedding-version", required=True)
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Switch the active index after rebuild validation succeeds.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            _run(
                embedding_version=args.embedding_version,
                activate=args.activate,
            )
        )
    except Exception as exc:
        print(f"vector_rebuild_enqueue_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
