from __future__ import annotations

import asyncio

from src.cli.run_redis_worker import _default_worker_id
from src.runtime.redis_task_worker import RedisTaskWorker


def test_worker_id_is_unique_process_safe():
    worker_id = _default_worker_id()
    assert worker_id.startswith("worker_")
    assert all(character.isalnum() or character in "_.-" for character in worker_id)


def test_worker_from_settings_uses_delivery_configuration():
    config = type("Config", (), {
        "redis_task_block_ms": 0,
        "redis_task_max_retries": 4,
        "redis_task_reclaim_idle_ms": 30,
        "redis_task_retry_base_ms": 10,
        "redis_task_retry_max_ms": 100,
        "redis_task_timeout_seconds": 2,
    })()
    settings = type("Settings", (), {"orchestration": config})()
    worker = RedisTaskWorker.from_settings(type("Queue", (), {})(), settings=settings,
                                            consumer="worker-1", handler=lambda task: asyncio.sleep(0))
    assert worker._max_retries == 4
    assert worker._reclaim_idle_ms == 30
