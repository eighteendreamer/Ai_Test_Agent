from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI

from src import main as main_module
from src.infrastructure import sqlalchemy_runtime
from src.infrastructure.sqlalchemy_runtime import (
    dispose_sqlalchemy_engines,
    iter_engines,
    mysql_engine,
    mysql_sessionmaker,
    postgres_engine,
    postgres_sessionmaker,
)


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
        database=main_module.get_settings().database.model_copy(update={"redis_url": "redis://unused"}),
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


@pytest.mark.parametrize("database", [
    pytest.param("postgres", marks=pytest.mark.skipif(
        os.getenv("RUN_LIVE_POSTGRES_TESTS") != "1", reason="set RUN_LIVE_POSTGRES_TESTS=1",
    )),
    pytest.param("mysql", marks=pytest.mark.skipif(
        os.getenv("RUN_LIVE_MYSQL_TESTS") != "1", reason="set RUN_LIVE_MYSQL_TESTS=1",
    )),
])
@pytest.mark.asyncio
async def test_lifespan_disposes_only_last_owners_sql_pool(monkeypatch, database):
    """Formal lifespan and real SQL pool; unrelated business initialization is omitted."""
    settings = main_module.get_settings().model_copy(deep=True)
    settings.database.postgres_pool_size = 1
    other_settings = settings.model_copy(deep=True)
    other_settings.database.postgres_pool_size = 2
    other_settings.database.mysql_charset = "utf8" if settings.database.mysql_charset != "utf8" else "utf8mb4"
    engine_for = postgres_engine if database == "postgres" else mysql_engine
    factory_for = postgres_sessionmaker if database == "postgres" else mysql_sessionmaker
    engine = engine_for(settings)
    other_engine = engine_for(other_settings)
    factory = factory_for(settings)
    old_pool = engine.pool
    other_pool = other_engine.pool

    def read_only_query(selected_engine):
        with selected_engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1

    async def initialize(_app, _cleanup, _final_cleanup):
        pass

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "_initialize_lifespan_resources", initialize)
    try:
        await asyncio.to_thread(read_only_query, other_engine)
        async with main_module.lifespan(FastAPI()):
            await asyncio.to_thread(read_only_query, engine)
            assert old_pool.checkedin() == 1
            async with main_module.lifespan(FastAPI()):
                assert engine_for(settings) is engine
                assert factory_for(settings) is factory
            assert old_pool.checkedin() == 1
            assert engine_for(settings) is engine
        assert old_pool.checkedin() == 0, "Application shutdown retained its checked-in SQL connection"
        assert engine not in iter_engines(database)
        assert factory_for(settings) is not factory
        assert engine_for(other_settings) is other_engine
        assert other_pool.checkedin() == 1
    finally:
        # This isolated pytest process owns these read-only pools.
        dispose_sqlalchemy_engines()


@pytest.mark.asyncio
async def test_sql_pool_cleanup_settles_before_shutdown_cancellation(monkeypatch):
    """Controlled cancellation of the waiter, not a production database failure."""
    started, release, finished = Event(), Event(), Event()
    original_release = sqlalchemy_runtime._release_engine_owners

    def delayed_release(keys):
        started.set()
        assert release.wait(timeout=5)
        original_release(keys)
        finished.set()

    async def initialize(_app, _cleanup, _final_cleanup):
        pass

    monkeypatch.setattr(sqlalchemy_runtime, "_release_engine_owners", delayed_release)
    monkeypatch.setattr(main_module, "_initialize_lifespan_resources", initialize)
    lifecycle = main_module.lifespan(FastAPI())
    await lifecycle.__aenter__()
    shutdown = asyncio.create_task(lifecycle.__aexit__(None, None, None))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(shutdown, return_exceptions=True)


def test_sql_disposal_attempts_other_pool_after_one_failure(monkeypatch, caplog):
    failed = SimpleNamespace(dispose=Mock(side_effect=RuntimeError("controlled close failure")))
    healthy = SimpleNamespace(dispose=Mock())
    monkeypatch.setattr(sqlalchemy_runtime, "_ENGINES", {("mysql",): failed, ("postgres",): healthy})
    monkeypatch.setattr(sqlalchemy_runtime, "_SESSION_FACTORIES", {})
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="controlled close failure"):
        dispose_sqlalchemy_engines()
    failed.dispose.assert_called_once()
    healthy.dispose.assert_called_once()
    assert any(record.message == "sqlalchemy_engine_disposal_failed" and record.database == "mysql"
               for record in caplog.records)
