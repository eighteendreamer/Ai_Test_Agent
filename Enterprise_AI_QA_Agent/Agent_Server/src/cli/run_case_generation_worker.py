from __future__ import annotations

import argparse
import asyncio
import os
from uuid import uuid4

from src.core.config import get_settings
from src.core.log_format import configure_logging
from src.runtime.generation_runtime import GENERATION_STREAM
from src.runtime.generation_task_handler import GenerationTaskHandler
from src.runtime.generation_worker_runtime import generation_worker_runtime
from src.runtime.redis_task_worker import RedisTaskWorker


async def _run(*, once, worker_id, stream=GENERATION_STREAM):
    settings = get_settings()
    async with generation_worker_runtime(settings, stream=stream) as runtime:
        worker = RedisTaskWorker.from_settings(runtime.queue, settings=settings, consumer=worker_id,
            handler=GenerationTaskHandler(runtime.service, runtime.store, worker_id=worker_id))
        try:
            if once:
                print(f"case_generation_processed={await worker.run_once()}")
            else:
                await worker.run()
        finally:
            worker.stop()


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description="Run durable incremental case generation modules.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id")
    parser.add_argument("--stream", default=GENERATION_STREAM,
        help="Registered transport stream; defaults to qa:tasks:case_generation")
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once,
            worker_id=args.worker_id or f"case_generation_{os.getpid()}_{uuid4().hex[:8]}", stream=args.stream))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"case_generation_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
