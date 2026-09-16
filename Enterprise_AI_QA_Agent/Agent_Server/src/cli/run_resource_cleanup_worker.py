"""Run the lease registry cleanup process independently from the API."""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import re

from src.core.config import get_settings
from src.core.request_context import new_id
from src.application.docker_management_service import DockerManagementService
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.postgres_resource_cleanup_store import PostgresResourceCleanupStore
from src.runtime.resource_cleanup_worker import (
    ResourceCleanupTaskHandler,
    ResourceCleanupWorker,
)
from src.runtime.resource_lease_manager import RedisResourceLeaseManager


def _default_worker_id() -> str:
    host = re.sub(r"[^A-Za-z0-9_.-]", "_", platform.node() or "host")
    suffix = new_id("p").split("_", 1)[1][:8]
    return f"cleanup_{host}_{os.getpid()}_{suffix}"


def build_cleanup_callbacks(settings):
    docker = DockerManagementService(settings)

    async def cleanup_docker(lease) -> None:
        if lease.resource_type != "docker":
            raise ValueError(
                f"Unexpected Docker cleanup resource type: {lease.resource_type}"
            )
        if not lease.external_resource_id:
            return
        if not re.fullmatch(r"[a-f0-9]{64}", lease.external_resource_id):
            raise ValueError("Automatic Docker cleanup requires an immutable full container ID")
        containers = await docker.list_containers()
        target = next(
            (
                item
                for item in containers
                if lease.external_resource_id == item.get("id")
            ),
            None,
        )
        if target is None:
            return
        await docker.remove_container(lease.external_resource_id, force=True)

    return {"docker": cleanup_docker}


async def _run(*, once: bool, worker_id: str) -> None:
    settings = get_settings()
    manager = RedisResourceLeaseManager(
        settings.database.redis_url,
        socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
        cleanup_stream=settings.orchestration.cleanup_task_stream,
    )
    queue = RedisTaskQueue(
        settings.database.redis_url,
        stream=settings.orchestration.cleanup_task_stream,
        group=f"{settings.orchestration.redis_task_consumer_group}:cleanup",
        socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
    )
    store = PostgresResourceCleanupStore(settings)
    reaper = ResourceCleanupWorker(
        manager,
        interval_seconds=settings.orchestration.resource_cleanup_interval_seconds,
    )
    cleanup_worker = RedisTaskWorker.from_settings(
        queue,
        settings=settings,
        consumer=worker_id,
        handler=ResourceCleanupTaskHandler(store, build_cleanup_callbacks(settings)),
    )
    try:
        await store.initialize()
        await manager.connect()
        await queue.connect()
        if once:
            count = await reaper.run_once()
            cleaned = await cleanup_worker.run_once()
            print(f"resource_cleanup_reaped={count}")
            print(f"resource_cleanup_completed={cleaned}")
        else:
            async with asyncio.TaskGroup() as group:
                group.create_task(reaper.run())
                group.create_task(cleanup_worker.run())
    finally:
        reaper.stop()
        cleanup_worker.stop()
        await queue.close()
        await manager.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reap expired Redis resource leases independently from API workers.")
    parser.add_argument("--once", action="store_true", help="Run one bounded sweep and exit")
    parser.add_argument("--worker-id", default=None, help="Stable unique cleanup consumer id")
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once, worker_id=args.worker_id or _default_worker_id()))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"resource_cleanup_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
