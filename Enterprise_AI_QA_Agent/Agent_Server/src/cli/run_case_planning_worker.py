"""Consume durable project planning tasks outside the API process."""
from __future__ import annotations

import argparse
import asyncio
import os
from uuid import uuid4

from src.core.config import get_settings
from src.core.log_format import configure_logging
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.planning_worker_runtime import planning_worker_runtime
from src.runtime.redis_task_worker import RedisTaskWorker


async def _run(*, once: bool, worker_id: str):
    settings = get_settings()
    async with planning_worker_runtime(settings) as runtime:
        worker = RedisTaskWorker.from_settings(runtime.queue, settings=settings, consumer=worker_id,
            handler=PlanningTaskHandler(runtime.planner, runtime.tasks, worker_id=worker_id,
                revision_finalizer=runtime.revisions))
        try:
            if once:
                print(f"case_planning_processed={await worker.run_once()}")
            else:
                await worker.run()
        finally:
            worker.stop()


def main():
    configure_logging()
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
