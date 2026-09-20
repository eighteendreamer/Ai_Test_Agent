"""Run ordinary conversation tasks outside the API process."""
from __future__ import annotations

import argparse
import asyncio

from src.cli.run_redis_worker import _default_worker_id
from src.core.config import get_settings
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.main import app
from src.runtime.redis_task_worker import RedisTaskWorker


async def _run(*, once: bool, worker_id: str):
    settings = get_settings()
    async with app.router.lifespan_context(app):
        queue = RedisTaskQueue(
            settings.database.redis_url,
            stream=settings.orchestration.agent_task_stream,
            group=settings.orchestration.redis_task_consumer_group,
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
        )
        await queue.connect()

        async def handle(task):
            await app.state.agent_task_dispatcher.handle(task, worker_id=worker_id)

        worker = RedisTaskWorker.from_settings(
            queue,
            settings=settings,
            consumer=worker_id,
            handler=handle,
        )
        try:
            if once:
                await worker.run_once()
            else:
                await worker.run()
        finally:
            worker.stop()
            await queue.close()


def main():
    parser = argparse.ArgumentParser(description="Run durable Agent conversation tasks.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once, worker_id=args.worker_id or _default_worker_id()))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"agent_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
