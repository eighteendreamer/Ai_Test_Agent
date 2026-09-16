"""Publish PostgreSQL task intents to Redis as an independent relay process."""

from __future__ import annotations

import argparse
import asyncio

from src.core.config import get_settings
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.postgres_task_outbox import PostgresTaskOutbox


async def _run(*, once: bool, batch_size: int) -> None:
    settings = get_settings()
    outbox = PostgresTaskOutbox(settings)
    await outbox.initialize()
    queues: dict[str, RedisTaskQueue] = {}
    try:
        for stream in settings.orchestration.task_stream_names:
            queue = RedisTaskQueue(
                settings.database.redis_url,
                stream=stream,
                group=settings.orchestration.redis_task_consumer_group,
                socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
            )
            await queue.connect()
            queues[stream] = queue

        if not queues:
            raise RuntimeError("No Redis task streams are configured for the outbox relay")

        primary_queue = queues[settings.orchestration.redis_task_stream]

        def resolve_queue(stream: str) -> RedisTaskQueue | None:
            return queues.get(stream)

        if once:
            print(
                "task_outbox_published="
                f"{await outbox.relay_once(primary_queue, limit=batch_size, queue_for_stream=resolve_queue)}"
            )
            return
        while True:
            await outbox.relay_once(primary_queue, limit=batch_size, queue_for_stream=resolve_queue)
            await asyncio.sleep(max(0.1, settings.orchestration.redis_task_retry_base_ms / 1000))
    finally:
        for queue in queues.values():
            await queue.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay PostgreSQL task outbox rows to Redis Streams.")
    parser.add_argument("--once", action="store_true", help="Publish one bounded batch and exit")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 1000:
        raise SystemExit("--batch-size must be between 1 and 1000")
    try:
        asyncio.run(_run(once=args.once, batch_size=args.batch_size))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"task_outbox_relay_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
