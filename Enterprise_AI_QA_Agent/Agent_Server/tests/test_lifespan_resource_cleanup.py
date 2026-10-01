from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from src import main as main_module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_stage", "fail_hot_close"),
    [("quota", False), ("quota", True), ("vector_connect", False)],
)
async def test_startup_failure_closes_all_created_redis_resources(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fail_stage: str,
    fail_hot_close: bool,
) -> None:
    attempts: list[str] = []

    def resource_type(name: str):
        class Resource:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def connect(self) -> None:
                attempts.append(f"connect:{name}")
                if name == "vector" and fail_stage == "vector_connect":
                    raise RuntimeError("vector connect failed")

            async def close(self) -> None:
                attempts.append(f"close:{name}")
                if name == "hot" and fail_hot_close:
                    raise RuntimeError("hot close failed")

            async def configure_quota(self, **kwargs) -> None:
                if fail_stage == "quota":
                    raise RuntimeError("quota failed")

        return Resource

    settings = SimpleNamespace(
        database=SimpleNamespace(redis_url="redis://unused"),
        orchestration=SimpleNamespace(
            redis_task_dispatch_enabled=True,
            redis_task_stream="tasks",
            redis_task_consumer_group="workers",
            redis_task_socket_timeout_seconds=1,
            redis_task_retention_seconds=86400,
            redis_task_retention_interval_seconds=60,
            redis_task_retention_batch_size=1000,
            redis_hot_memory_ttl_seconds=60,
            redis_hot_memory_max_events=10,
            redis_vector_enabled=True,
            redis_vector_socket_timeout_seconds=1,
            cleanup_task_stream="cleanup",
            resource_global_limit=10,
        ),
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "AppContainer", object)
    monkeypatch.setattr(main_module, "RedisTaskQueue", resource_type("queue"))
    monkeypatch.setattr(main_module, "RedisHotMemoryStore", resource_type("hot"))
    monkeypatch.setattr(main_module, "RedisExportTaskStore", resource_type("export"))
    monkeypatch.setattr(main_module, "RedisVectorStore", resource_type("vector"))
    monkeypatch.setattr(main_module, "RedisResourceLeaseManager", resource_type("lease"))

    expected_error = (
        "hot close failed" if fail_hot_close
        else "vector connect failed" if fail_stage == "vector_connect"
        else "quota failed"
    )
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match=expected_error):
        async with main_module.lifespan(FastAPI()):
            pytest.fail("Startup must fail before yield")

    expected_attempts = [
        "connect:queue",
        "connect:hot",
        "connect:export",
        "connect:vector",
    ]
    if fail_stage == "quota":
        expected_attempts.extend(["connect:lease", "close:lease"])
    expected_attempts.extend(["close:vector", "close:export", "close:hot", "close:queue"])
    assert attempts == expected_attempts
    if fail_hot_close:
        assert any(
            record.message == "lifespan_resource_cleanup_failed"
            and record.resource == "hot_memory_store"
            for record in caplog.records
        )


@pytest.mark.asyncio
async def test_shutdown_flushes_after_other_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def close_first() -> None:
        events.append("close:first")

    async def close_second() -> None:
        events.append("close:second")
        raise RuntimeError("second close failed")

    async def flush() -> None:
        events.append("flush")

    async def initialize(app: FastAPI, cleanup: AsyncExitStack, final_cleanup: AsyncExitStack) -> None:
        cleanup.push_async_callback(main_module._close_lifespan_resource, "first", close_first)
        cleanup.push_async_callback(main_module._close_lifespan_resource, "second", close_second)
        final_cleanup.push_async_callback(main_module._close_lifespan_resource, "observability", flush)

    monkeypatch.setattr(main_module, "_initialize_lifespan_resources", initialize)
    with pytest.raises(RuntimeError, match="second close failed"):
        async with main_module.lifespan(FastAPI()):
            events.append("running")

    assert events == ["running", "close:second", "close:first", "flush"]
