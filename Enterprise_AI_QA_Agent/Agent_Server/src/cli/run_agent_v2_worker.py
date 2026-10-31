"""Run versioned Agent tasks in the framework-bound v2 runtime."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from src.cli.run_redis_worker import _default_worker_id
from src.core.config import get_settings
from src.core.request_context import bind_request_context
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.main import app
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities


async def _run(*, once: bool, worker_id: str, release_store: Path | None = None,
               release_reference: Path | None = None):
    if (release_store is None) != (release_reference is None):
        raise ValueError("Both release store and reference are required")
    settings = get_settings()
    available = settings.deep_agents.enabled and settings.deep_agents.checkpoint_enabled
    capabilities = (FrameworkWorkerCapabilities.from_artifact(deepagents_available=available)
        if release_reference is None else FrameworkWorkerCapabilities.from_release(release_store,
            json.loads(release_reference.read_text(encoding="utf-8-sig")), deepagents_available=available))
    async with app.router.lifespan_context(app):
        dispatcher = getattr(app.state, "agent_task_dispatcher", None)
        if dispatcher is None:
            raise RuntimeError("Agent v2 Worker requires the Agent task dispatcher")
        dispatcher.set_framework_capabilities(capabilities)
        queue = RedisTaskQueue(
            settings.database.redis_url,
            stream=settings.orchestration.agent_task_v2_stream,
            group=settings.orchestration.agent_task_v2_consumer_group,
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
            retention_seconds=settings.orchestration.redis_task_retention_seconds,
            retention_interval_seconds=settings.orchestration.redis_task_retention_interval_seconds,
            retention_batch_size=settings.orchestration.redis_task_retention_batch_size,
        )
        await queue.connect()

        async def handle(task):
            with bind_request_context(
                worker_id=worker_id,
                queue=queue.stream,
                task_type=str(task.payload.get("task_type") or "agent_turn"),
            ):
                await dispatcher.handle_v2(task, worker_id=worker_id)

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
    parser = argparse.ArgumentParser(description="Run framework-bound Agent v2 tasks.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--release-store", type=Path, default=None)
    parser.add_argument("--release-reference", type=Path, default=None,
        help="Trusted release reference; verify local source before starting services")
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once, worker_id=args.worker_id or _default_worker_id(),
            release_store=args.release_store, release_reference=args.release_reference))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"agent_v2_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
