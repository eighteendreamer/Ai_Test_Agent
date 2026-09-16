"""Run low-priority Embedding tasks outside the API process."""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import re

from src.core.config import get_settings
from src.core.request_context import new_id
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.main import app
from src.runtime.embedding_task_handler import EmbeddingTaskHandler
from src.runtime.redis_task_worker import RedisTaskWorker


def _default_worker_id() -> str:
    host = re.sub(r"[^A-Za-z0-9_.-]", "_", platform.node() or "host")
    suffix = new_id("p").split("_", 1)[1][:8]
    return f"embedding_{host}_{os.getpid()}_{suffix}"


async def _run(*, once: bool, worker_id: str) -> None:
    settings = get_settings()
    async with app.router.lifespan_context(app):
        service = app.state.test_case_embedding_service
        jobs = app.state.embedding_job_store
        queue = RedisTaskQueue(
            settings.database.redis_url,
            stream=settings.orchestration.embedding_task_stream,
            group=f"{settings.orchestration.redis_task_consumer_group}:embedding",
            socket_timeout_seconds=(
                settings.orchestration.redis_task_socket_timeout_seconds
            ),
        )
        await queue.connect()
        worker = RedisTaskWorker.from_settings(
            queue,
            settings=settings,
            consumer=worker_id,
            handler=EmbeddingTaskHandler(service, jobs, worker_id=worker_id),
        )
        try:
            if once:
                await worker.run_once()
                return
            await worker.run()
        finally:
            worker.stop()
            await queue.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run durable low-priority Embedding tasks."
    )
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--worker-id", default=None, help="Unique worker/consumer id")
    args = parser.parse_args()
    try:
        asyncio.run(
            _run(
                once=args.once,
                worker_id=args.worker_id or _default_worker_id(),
            )
        )
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"embedding_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
