"""Run the low-priority Redis Session compaction worker."""

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
from src.runtime.compaction_task_handler import CompactionTaskHandler
from src.runtime.redis_task_worker import RedisTaskWorker


def _default_worker_id() -> str:
    host = re.sub(r"[^A-Za-z0-9_.-]", "_", platform.node() or "host")
    return f"compaction_{host}_{os.getpid()}_{new_id('p').split('_', 1)[1][:8]}"


async def _run(*, once: bool, worker_id: str) -> None:
    settings = get_settings()
    async with app.router.lifespan_context(app):
        compaction_worker = getattr(app.state, "compaction_worker", None)
        if compaction_worker is None:
            raise RuntimeError("Compaction worker is not initialized")
        queue = RedisTaskQueue(
            settings.database.redis_url,
            stream=settings.orchestration.compaction_task_stream,
            group=f"{settings.orchestration.redis_task_consumer_group}:compaction",
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
        )
        await queue.connect()
        worker = RedisTaskWorker.from_settings(
            queue,
            settings=settings,
            consumer=worker_id,
            handler=CompactionTaskHandler(compaction_worker),
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
    parser = argparse.ArgumentParser(description="Run Session hot-memory compaction tasks.")
    parser.add_argument("--once", action="store_true", help="Poll one bounded batch and exit")
    parser.add_argument("--worker-id", default=None, help="Stable unique worker/consumer id")
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once, worker_id=args.worker_id or _default_worker_id()))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"compaction_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
