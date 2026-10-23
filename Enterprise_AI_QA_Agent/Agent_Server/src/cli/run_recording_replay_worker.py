"""Run the durable recording replay Worker."""
from __future__ import annotations

import argparse
import asyncio
import os
from uuid import uuid4

from src.application.recorder.replay_service import RECORDING_REPLAY_STREAM
from src.core.config import get_settings
from src.core.log_format import configure_logging
from src.main import app
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.recording_replay_task_handler import RecordingReplayTaskHandler
from src.runtime.redis_task_worker import RedisTaskWorker


async def _run(*, once: bool, worker_id: str) -> None:
    settings = get_settings()
    if RECORDING_REPLAY_STREAM not in settings.orchestration.task_stream_names:
        raise RuntimeError("Register qa:tasks:recording_replay in REDIS_TASK_OUTBOX_STREAMS")
    async with app.router.lifespan_context(app):
        service = getattr(app.state, "recording_replay_service", None)
        store = getattr(app.state, "recording_replay_store", None)
        if service is None or store is None:
            raise RuntimeError("recording replay service is not initialized")
        queue = RedisTaskQueue(settings.database.redis_url, stream=RECORDING_REPLAY_STREAM,
            group=f"{settings.orchestration.redis_task_consumer_group}:recording_replay",
            socket_timeout_seconds=settings.orchestration.redis_task_socket_timeout_seconds,
            retention_seconds=settings.orchestration.redis_task_retention_seconds,
            retention_interval_seconds=settings.orchestration.redis_task_retention_interval_seconds,
            retention_batch_size=settings.orchestration.redis_task_retention_batch_size)
        await queue.connect()
        worker = RedisTaskWorker.from_settings(queue, settings=settings, consumer=worker_id,
            handler=RecordingReplayTaskHandler(service, store, worker_id=worker_id))
        try:
            if once:
                print(f"recording_replay_processed={await worker.run_once()}")
            else:
                await worker.run()
        finally:
            worker.stop()
            await queue.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run durable recording replay tasks.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once, worker_id=args.worker_id or f"recording_replay_{os.getpid()}_{uuid4().hex[:8]}"))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"recording_replay_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
