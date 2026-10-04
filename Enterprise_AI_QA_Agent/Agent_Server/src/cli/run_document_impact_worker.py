from __future__ import annotations

import argparse
import asyncio
import os
from uuid import uuid4

from src.core.config import get_settings
from src.core.log_format import configure_logging
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM
from src.runtime.document_impact_task_handler import DocumentImpactTaskHandler
from src.runtime.document_impact_worker_runtime import document_impact_worker_runtime
from src.runtime.redis_task_worker import RedisTaskWorker


async def _run(*, once, worker_id, stream=DOCUMENT_IMPACT_STREAM):
    settings = get_settings()
    async with document_impact_worker_runtime(settings, stream=stream) as runtime:
        worker = RedisTaskWorker.from_settings(runtime.queue, settings=settings, consumer=worker_id,
            handler=DocumentImpactTaskHandler(runtime.service, runtime.store, worker_id=worker_id))
        try:
            if once:
                print(f"document_impact_processed={await worker.run_once()}")
            else:
                await worker.run()
        finally:
            worker.stop()


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description="Run durable document impact candidate analysis.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id")
    parser.add_argument("--stream", default=DOCUMENT_IMPACT_STREAM,
        help="Registered transport stream; defaults to qa:tasks:document_impact")
    args = parser.parse_args()
    try:
        asyncio.run(_run(once=args.once,
            worker_id=args.worker_id or f"document_impact_{os.getpid()}_{uuid4().hex[:8]}", stream=args.stream))
    except KeyboardInterrupt:
        return
    except Exception as exc:
        print(f"document_impact_worker_failed error_type={type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
