"""Run low-priority Redis vector-rebuild tasks outside the API process."""

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
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.vector_rebuild_task_handler import VectorRebuildTaskHandler


def _default_worker_id() -> str:
    host = re.sub(r"[^A-Za-z0-9_.-]", "_", platform.node() or "host")
    suffix = new_id("p").split("_", 1)[1][:8]
    return f"vector_rebuild_{host}_{os.getpid()}_{suffix}"


async def _run(*, once: bool, worker_id: str) -> None:
    settings = get_settings()
    async with app.router.lifespan_context(app):
        rebuild_service = getattr(app.state, "memory_vector_rebuild_service", None)
        job_store = getattr(app.state, "vector_rebuild_job_store", None)
        if rebuild_service is None or job_store is None:
            raise RuntimeError(
                "Vector rebuild worker requires ORCHESTRATION__REDIS_VECTOR_ENABLED=true"
            )
        queue = RedisTaskQueue(
            settings.database.redis_url,
            stream=settings.orchestration.vector_rebuild_task_stream,
            group=f"{settings.orchestration.redis_task_consumer_group}:vector_rebuild",
            socket_timeout_seconds=(
                settings.orchestration.redis_task_socket_timeout_seconds
            ),
        )
        await queue.connect()
        worker = RedisTaskWorker.from_settings(
            queue,
            settings=settings,
            consumer=worker_id,
            handler=VectorRebuildTaskHandler(
                rebuild_service,
                job_store,
                worker_id=worker_id,
            ),
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
        description="Run durable memory-vector rebuild tasks."
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
        print(f"vector_rebuild_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
