"""Consume durable project planning tasks outside the API process."""
from __future__ import annotations

import argparse
import asyncio
import os
from uuid import uuid4

from src.core.config import get_settings
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.main import app
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.redis_task_worker import RedisTaskWorker


async def _run(*, once: bool, worker_id: str):
    settings = get_settings()
    stream = settings.orchestration.case_planning_task_stream
    if stream not in settings.orchestration.task_stream_names:
        raise ValueError("Register the case_planning stream in REDIS_TASK_OUTBOX_STREAMS")
    async with app.router.lifespan_context(app):
        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
            group=f"{settings.orchestration.redis_task_consumer_group}:case_planning",
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
            retention_seconds=settings.orchestration.redis_task_retention_seconds,
            retention_interval_seconds=settings.orchestration.redis_task_retention_interval_seconds,
            retention_batch_size=settings.orchestration.redis_task_retention_batch_size)
        try:
            await queue.connect()
            worker = RedisTaskWorker.from_settings(queue, settings=settings, consumer=worker_id,
                handler=PlanningTaskHandler(app.state.case_planning_service, app.state.planning_task_store, worker_id=worker_id))
            if once:
                await worker.run_once()
            else:
                await worker.run()
        finally:
            await queue.close()


def main():
    parser = argparse.ArgumentParser(description="Run durable read-only project planning tasks.")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--worker-id", default=None, help="Unique Worker identity")
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once, worker_id=args.worker_id or f"planning_{os.getpid()}_{uuid4().hex[:8]}"))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"case_planning_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
