from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import settings as settings_routes
from src.infrastructure.redis_export_task_store import RedisExportTaskStore


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.lists: dict[str, list[bytes]] = {}
        self.expirations: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def hsetnx(self, name: str, key: str, value: str) -> bool:
        row = self.hashes.setdefault(name, {})
        encoded_key = key.encode("utf-8")
        if encoded_key in row:
            return False
        row[encoded_key] = value.encode("utf-8")
        return True

    async def hset(self, name: str, *, mapping: dict[str, Any]) -> int:
        row = self.hashes.setdefault(name, {})
        added = 0
        for key, value in mapping.items():
            encoded_key = key.encode("utf-8")
            added += encoded_key not in row
            row[encoded_key] = str(value).encode("utf-8")
        return added

    async def hgetall(self, name: str) -> dict[bytes, bytes]:
        return dict(self.hashes.get(name, {}))

    async def expire(self, name: str, ttl: int) -> bool:
        if name not in self.hashes and name not in self.lists:
            return False
        self.expirations[name] = int(ttl)
        return True

    async def delete(self, *names: str) -> int:
        deleted = 0
        for name in names:
            deleted += int(name in self.hashes or name in self.lists)
            self.hashes.pop(name, None)
            self.lists.pop(name, None)
            self.expirations.pop(name, None)
        return deleted

    async def rpush(self, name: str, *values: bytes) -> int:
        rows = self.lists.setdefault(name, [])
        rows.extend(value if isinstance(value, bytes) else str(value).encode("utf-8") for value in values)
        return len(rows)

    async def llen(self, name: str) -> int:
        return len(self.lists.get(name, []))

    async def lrange(self, name: str, start: int, stop: int) -> list[bytes]:
        rows = self.lists.get(name, [])
        return list(rows[start : stop + 1])


class ExportStore:
    def __init__(self, count: int = 1) -> None:
        self.count = count

    async def count_sessions(self) -> int:
        return self.count

    async def bulk_export(self, progress_fn=None) -> list[dict]:
        if progress_fn is not None:
            await asyncio.to_thread(progress_fn, self.count)
        return [
            {"id": f"session-{index}", "title": "导出的会话"}
            for index in range(self.count)
        ]


class FailedExportStore:
    async def count_sessions(self) -> int:
        return 1

    async def bulk_export(self, progress_fn=None) -> list[dict]:
        raise RuntimeError("database connection details must not reach the client")


def _client(
    task_store: RedisExportTaskStore,
    *,
    session_count: int = 1,
    store: Any | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(settings_routes.router, prefix="/api/v1")
    app.state.store = store or ExportStore(session_count)
    app.state.export_task_store = task_store
    return TestClient(app)


def _wait_until_done(client: TestClient, task_id: str) -> dict:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/settings/data/export/progress/{task_id}")
        if response.status_code == 200 and response.json()["status"] != "running":
            return response.json()
        time.sleep(0.01)
    raise AssertionError(f"export task {task_id} did not finish")


def test_export_task_ids_do_not_collide_within_one_second(monkeypatch) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 1, 12, 0, 0, tzinfo=tz or UTC)

    monkeypatch.setattr(settings_routes, "datetime", FrozenDateTime)
    redis = FakeRedis()
    task_store = RedisExportTaskStore("redis://unused", client=redis)

    with _client(task_store) as client:
        first = client.post("/api/v1/settings/data/export/start").json()["task_id"]
        second = client.post("/api/v1/settings/data/export/start").json()["task_id"]

    assert first != second


def test_export_is_pollable_and_downloadable_across_workers_and_retries() -> None:
    shared_redis = FakeRedis()
    worker_a_store = RedisExportTaskStore("redis://unused", client=shared_redis)
    worker_b_store = RedisExportTaskStore("redis://unused", client=shared_redis)

    with (
        _client(worker_a_store, session_count=70) as worker_a,
        _client(worker_b_store, session_count=70) as worker_b,
    ):
        started = worker_a.post("/api/v1/settings/data/export/start").json()
        task_id = started["task_id"]
        state = _wait_until_done(worker_b, task_id)

        first = worker_b.get(f"/api/v1/settings/data/export/download/{task_id}")
        second = worker_b.get(f"/api/v1/settings/data/export/download/{task_id}")

    assert state == {"progress": 70, "total": 70, "status": "done", "error": None}
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.content == first.content
    assert first.json()["sessions"] == [
        {"id": f"session-{index}", "title": "导出的会话"}
        for index in range(70)
    ]


def test_empty_export_downloads_as_an_empty_session_array() -> None:
    redis = FakeRedis()
    task_store = RedisExportTaskStore("redis://unused", client=redis)

    with _client(task_store, session_count=0) as client:
        task_id = client.post("/api/v1/settings/data/export/start").json()["task_id"]
        state = _wait_until_done(client, task_id)
        response = client.get(f"/api/v1/settings/data/export/download/{task_id}")

    assert state["status"] == "done"
    assert response.status_code == 200
    assert response.json()["session_count"] == 0
    assert response.json()["sessions"] == []


def test_export_task_state_and_result_share_their_retention_ttl() -> None:
    async def run() -> None:
        redis = FakeRedis()
        store = RedisExportTaskStore("redis://unused", client=redis, ttl_seconds=10)
        task_id = "a" * 32
        await store.create_task(task_id, total=1)
        await store.complete_task(task_id, [{"id": "session-1"}])

        meta_key = f"qa-agent:settings-export:{task_id}:meta"
        result_key = f"qa-agent:settings-export:{task_id}:sessions"
        assert redis.expirations[meta_key] == 10
        assert redis.expirations[result_key] == 10
        assert await store.result_count(task_id) == 1

    asyncio.run(run())


def test_export_failure_is_reported_without_exposing_internal_exception() -> None:
    redis = FakeRedis()
    task_store = RedisExportTaskStore("redis://unused", client=redis)

    with _client(task_store, store=FailedExportStore()) as client:
        task_id = client.post("/api/v1/settings/data/export/start").json()["task_id"]
        state = _wait_until_done(client, task_id)

    assert state["status"] == "error"
    assert state["error"] == "Export failed. Check server logs."
    assert "database connection details" not in state["error"]
