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
from src.runtime.compaction_backlog_sweeper import CompactionBacklogSweeper
from src.runtime.compaction_task_handler import CompactionTaskHandler
from src.runtime.postgres_session_store import PostgresSessionStore
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
        session_store = PostgresSessionStore(settings)
        compaction_stream = settings.orchestration.compaction_task_stream
        configure_outbox = getattr(session_store, "set_compaction_task_outbox", None)
        if callable(configure_outbox) and compaction_stream in settings.orchestration.task_stream_names:
            # Without this the idle sweep has nowhere to write and would report 0.
            configure_outbox(
                table_name=settings.database.postgres_task_outbox_table,
                stream=compaction_stream,
            )
        worker = RedisTaskWorker.from_settings(
            queue,
            settings=settings,
            consumer=worker_id,
            handler=CompactionTaskHandler(compaction_worker),
        )
        sweeper = CompactionBacklogSweeper(
            session_store,
            idle_seconds=settings.orchestration.compaction_idle_seconds,
            interval_seconds=settings.orchestration.compaction_sweep_interval_seconds,
        )
        try:
            if once:
                await sweeper.run_once()
                await worker.run_once()
                return
            async with asyncio.TaskGroup() as group:
                group.create_task(worker.run())
                group.create_task(sweeper.run())
        finally:
            worker.stop()
            sweeper.stop()
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
