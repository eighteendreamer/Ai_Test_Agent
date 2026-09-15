"""Run the TestRun Redis worker as a separate process.

The API process never starts this worker implicitly. Configure the same Redis
stream/group on each worker host and use a unique worker id per process.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import re

from src.core.config import get_settings
from src.core.request_context import new_id
from src.main import app
from src.runtime.redis_task_worker import RedisTaskWorker


def _default_worker_id() -> str:
    host = re.sub(r"[^A-Za-z0-9_.-]", "_", platform.node() or "host")
    return f"worker_{host}_{os.getpid()}_{new_id('p').split('_', 1)[1][:8]}"


async def _run(*, once: bool, worker_id: str) -> None:
    settings = get_settings()
    async with app.router.lifespan_context(app):
        queue = getattr(app.state, "task_queue", None)
        dispatcher = getattr(app.state, "redis_execution_dispatcher", None)
        if queue is None or dispatcher is None:
            raise RuntimeError(
                "Redis TestRun worker requires ORCHESTRATION__REDIS_TASK_DISPATCH_ENABLED=true"
            )

        async def handle(task):
            await dispatcher.handle(task, worker_id=worker_id)

        worker = RedisTaskWorker.from_settings(
            queue, settings=settings, consumer=worker_id, handler=handle,
        )
        if once:
            await worker.run_once()
            return
        try:
            await worker.run()
        except asyncio.CancelledError:
            worker.stop()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=(
        "Run the TestRun Redis worker outside the API process. "
        "Use --once for a bounded delivery poll."
    ))
    parser.add_argument("--once", action="store_true", help="Poll and process one bounded batch, then exit")
    parser.add_argument("--worker-id", default=None, help="Stable unique worker/consumer id")
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once, worker_id=args.worker_id or _default_worker_id()))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"redis_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
