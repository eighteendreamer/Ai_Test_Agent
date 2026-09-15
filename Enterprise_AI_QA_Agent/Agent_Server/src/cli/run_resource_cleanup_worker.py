"""Run the lease registry cleanup process independently from the API."""

from __future__ import annotations

import argparse
import asyncio

from src.core.config import get_settings
from src.runtime.resource_cleanup_worker import ResourceCleanupWorker
from src.runtime.resource_lease_manager import RedisResourceLeaseManager


async def _run(*, once: bool) -> None:
    settings = get_settings()
    manager = RedisResourceLeaseManager(
        settings.database.redis_url,
        socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
    )
    await manager.connect()
    worker = ResourceCleanupWorker(
        manager,
        interval_seconds=settings.orchestration.resource_cleanup_interval_seconds,
    )
    try:
        if once:
            count = await worker.run_once()
            print(f"resource_cleanup_reaped={count}")
        else:
            await worker.run()
    finally:
        await manager.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reap expired Redis resource leases independently from API workers.")
    parser.add_argument("--once", action="store_true", help="Run one bounded sweep and exit")
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"resource_cleanup_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
