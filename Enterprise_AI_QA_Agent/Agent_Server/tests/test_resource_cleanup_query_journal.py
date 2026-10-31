from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from psycopg import sql

from src.application.docker_management_service import DockerCommandError
from src.core.config import get_settings
from src.core.request_context import bind_request_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask
from src.runtime.docker_resource_cleanup import DockerResourceCleanup
from src.runtime.postgres_resource_cleanup_store import PostgresResourceCleanupStore
from src.runtime.resource_cleanup_worker import ResourceCleanupTaskHandler
from src.runtime.task_deferred import TaskDeferred
from src.schemas.resource_cleanup import DockerResourceQueryObservation
from tests.test_resource_cleanup_worker import Manager, expired_lease, query_observation


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.environ.get("RUN_LIVE_POSTGRES_TESTS") != "1", reason="requires live PostgreSQL")]


@pytest.mark.parametrize("outcome", ["present", "failed", "unconfirmed"])
async def test_unconfirmed_target_query_preserves_durable_intent_and_result(outcome, caplog):
    settings = get_settings()
    store = PostgresResourceCleanupStore(settings)
    await store.initialize()
    task_id = f"query-history-{uuid4().hex}"
    lease = replace(expired_lease("docker", "slot-query-history", "a" * 64), allocation_id="b" * 32)
    manager = Manager()
    manager.namespace = "qa:test"
    queried = []

    class Allocations:
        async def get_many(self, ids, *, namespace):
            return {lease.allocation_id: {"allocation_id": lease.allocation_id, "lease_namespace": namespace,
                "lease_record": lease.__dict__, "container_id": lease.external_resource_id}}

    class Docker:
        async def container_exists(self, target):
            # The query request must survive a separate store/connection before
            # any external observation, not be reconstructed after the response.
            rows = await PostgresResourceCleanupStore(settings).list_target_queries(task_id, lease, namespace=manager.namespace)
            assert len(rows) == 1 and rows[0]["result"] is None
            assert rows[0]["request"]["observer_worker_id"] == "cleanup"
            assert rows[0]["request"]["lease"]["external_resource_id"] == target
            queried.append(target)
            if outcome == "failed":
                raise DockerCommandError("private-query-diagnostic")
            return True if outcome == "present" else None

        async def list_containers(self):
            raise AssertionError("A query must not dispatch cleanup")

    try:
        await store.begin(task_id, lease, worker_id="original-worker")
        before = await asyncio.to_thread(store._read_sync, task_id)
        callback = DockerResourceCleanup(settings, allocations=Allocations(), namespace=manager.namespace, docker=Docker())
        with pytest.raises(TaskDeferred):
            await ResourceCleanupTaskHandler(store, {"docker": callback}, manager=manager)(QueuedTask(
                "query-0", {"task_type": "cleanup_task", "task_id": task_id, **lease.__dict__}))
        rows = await PostgresResourceCleanupStore(settings).list_target_queries(task_id, lease, namespace=manager.namespace)
        assert len(rows) == 1 and rows[0]["sequence"] > 0
        assert rows[0]["result"]["status"] == ("present" if outcome == "present" else "unknown")
        assert rows[0]["result"]["reason_code"] == {
            "present": "container_present", "failed": "query_failed", "unconfirmed": "query_unconfirmed"}[outcome]
        assert rows[0]["result"]["error_type"] == ("DockerCommandError" if outcome == "failed" else None)
        assert rows[0]["recorded_at"] is not None and queried == [lease.external_resource_id]
        assert "lease_token" not in json.dumps(rows[0]["request"])
        assert "private-query-diagnostic" not in json.dumps(rows[0]["result"]) + caplog.text
        assert await asyncio.to_thread(store._read_sync, task_id) == before
        assert manager.finalized == [] and (await store.reconcile(task_id, lease)).status == "unknown"
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE task_id=%s").format(store._table), (task_id,))


async def test_target_query_history_is_immutable_ordered_and_paginated():
    settings = get_settings()
    store = PostgresResourceCleanupStore(settings)
    await store.initialize()
    task_id = f"query-history-pages-{uuid4().hex}"
    lease = replace(expired_lease("docker", "slot-history", "a" * 64), allocation_id="b" * 32)
    kwargs = {"namespace": "qa:test", "observer_worker_id": "cleanup"}
    observation = DockerResourceQueryObservation(status="present", reason_code="container_present",
        observed_at=datetime.now(timezone.utc))
    try:
        await store.begin(task_id, lease, worker_id="original-worker")
        before = await asyncio.to_thread(store._read_sync, task_id)
        ids = [await store.begin_target_query(task_id, lease, **kwargs) for _ in range(3)]
        await store.finish_target_query(task_id, lease, ids[0], observation, **kwargs)
        rows = await store.list_target_queries(task_id, lease, namespace="qa:test")
        assert [row["query_id"] for row in rows] == ids
        assert [row["sequence"] for row in rows] == sorted({row["sequence"] for row in rows})
        assert [row["result"] is None for row in rows] == [False, True, True]
        await store.finish_target_query(task_id, lease, ids[0], observation, **kwargs)
        assert await store.list_target_queries(task_id, lease, namespace="qa:test") == rows
        page = await store.list_target_queries(task_id, lease, namespace="qa:test", limit=1)
        assert page == rows[:1]
        assert await store.list_target_queries(task_id, lease, namespace="qa:test",
            after_sequence=page[0]["sequence"], limit=1) == rows[1:2]
        assert not await store.list_target_queries(task_id, lease, namespace="qa:test", after_sequence=rows[-1]["sequence"])
        changed = observation.model_copy(update={"observed_at": datetime.now(timezone.utc)})
        with pytest.raises(ValueError, match="immutable"):
            await store.finish_target_query(task_id, lease, ids[0], changed, **kwargs)
        assert await asyncio.to_thread(store._read_sync, task_id) == before
        with pytest.raises(ValueError, match="identity"):
            await store.list_target_queries(task_id, replace(lease, external_resource_id="c" * 64), namespace="qa:test")
        with pytest.raises(ValueError, match="identity"):
            await store.list_target_queries(task_id, lease, namespace="foreign")
        with pytest.raises(ValueError, match="identity"):
            await store.finish_target_query(task_id, lease, ids[1], observation,
                namespace="qa:test", observer_worker_id="foreign")
        with pytest.raises(ValueError, match="identity"):
            await store.finish_target_query("foreign-task", lease, ids[1], observation, **kwargs)
        for invalid in ({"limit": 0}, {"limit": True}, {"after_sequence": -1}, {"after_sequence": True}):
            with pytest.raises(ValueError, match="page"):
                await store.list_target_queries(task_id, lease, namespace="qa:test", **invalid)
        await store.finish(task_id, worker_id="original-worker", status="completed")
        terminal = await asyncio.to_thread(store._read_sync, task_id)
        assert await store.begin_target_query(task_id, lease, **kwargs) is None
        # Late evidence may be recorded but never downgrade/reclaim the receipt.
        await store.finish_target_query(task_id, lease, ids[1], observation, **kwargs)
        assert await asyncio.to_thread(store._read_sync, task_id) == terminal
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE task_id=%s").format(store._table), (task_id,))


@pytest.mark.parametrize("boundary", ["request_before_commit", "request_after_commit", "result_before_commit", "result_after_commit", "cancel_query"])
async def test_query_journal_failure_never_releases_or_dispatches_cleanup(boundary):
    settings = get_settings()
    store = PostgresResourceCleanupStore(settings)
    await store.initialize()
    task_id = f"query-window-{uuid4().hex}"
    lease = replace(expired_lease("docker", "slot-window", "a" * 64), allocation_id="b" * 32)
    manager = Manager()
    manager.namespace = "qa:test"
    queried = []

    class Observer:
        async def __call__(self, _lease):
            raise AssertionError("A journal failure must not authorize cleanup")

        async def query_target(self, value):
            queried.append(value.external_resource_id)
            if boundary == "cancel_query" and len(queried) == 1:
                raise asyncio.CancelledError()
            return query_observation(value)

    begin, finish = store.begin_target_query, store.finish_target_query
    async def fail_request(*args, **kwargs):
        if boundary == "request_after_commit":
            await begin(*args, **kwargs)
        raise ConnectionError("private-request-commit-error")
    async def fail_result(*args, **kwargs):
        if boundary == "result_after_commit":
            await finish(*args, **kwargs)
        raise ConnectionError("private-result-commit-error")
    try:
        await store.begin(task_id, lease, worker_id="original-worker")
        before = await asyncio.to_thread(store._read_sync, task_id)
        if boundary.startswith("request_"):
            store.begin_target_query = fail_request
        elif boundary.startswith("result_"):
            store.finish_target_query = fail_result
        handler = ResourceCleanupTaskHandler(store, {"docker": Observer()}, manager=manager)
        task = QueuedTask("window-0", {"task_type": "cleanup_task", "task_id": task_id, **lease.__dict__})
        with pytest.raises(asyncio.CancelledError if boundary == "cancel_query" else ConnectionError):
            await handler(task)
        assert queried == ([] if boundary.startswith("request_") else [lease.external_resource_id])
        first = await store.list_target_queries(task_id, lease, namespace="qa:test")
        assert len(first) == (0 if boundary == "request_before_commit" else 1)
        if first:
            assert (first[0]["result"] is not None) == (boundary == "result_after_commit")
        assert await asyncio.to_thread(store._read_sync, task_id) == before and manager.finalized == []
        store.begin_target_query, store.finish_target_query = begin, finish
        await handler(task)
        rows = await store.list_target_queries(task_id, lease, namespace="qa:test")
        assert rows[:len(first)] == first and len(rows) == len(first) + 1
        assert rows[-1]["result"]["status"] == "absent"
        receipt = await asyncio.to_thread(store._read_sync, task_id)
        assert receipt["status"] == "completed" and receipt["attempts"] == 1
        assert receipt["worker_id"] == "original-worker" and manager.finalized == [lease.resource_id]
        await handler(task)
        assert await store.list_target_queries(task_id, lease, namespace="qa:test") == rows
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE task_id=%s").format(store._table), (task_id,))


@pytest.mark.parametrize("drift", ["scope", "namespace", "observer", "target", "result", "absence"])
async def test_query_history_reader_rejects_persisted_identity_or_result_drift(drift):
    settings = get_settings()
    store = PostgresResourceCleanupStore(settings)
    await store.initialize()
    task_id = f"query-drift-{uuid4().hex}"
    lease = replace(expired_lease("docker", "slot-drift", "a" * 64), allocation_id="b" * 32)
    kwargs = {"namespace": "qa:test", "observer_worker_id": "cleanup"}
    try:
        await store.begin(task_id, lease, worker_id="original-worker")
        query_id = await store.begin_target_query(task_id, lease, **kwargs)
        await store.finish_target_query(task_id, lease, query_id, query_observation(lease), **kwargs)
        row = (await store.list_target_queries(task_id, lease, namespace="qa:test"))[0]
        column = "result" if drift in {"result", "absence"} else "request"
        data = row[column]
        if drift == "scope": data["scope"] = "retry_authorized"
        if drift == "namespace": data["lease_namespace"] = "foreign"
        if drift == "observer": data["observer_worker_id"] = ""
        if drift == "target": data["lease"]["external_resource_id"] = "c" * 64
        if drift == "result": data["status"] = "present"
        if drift == "absence": data["absence_observation"]["container_id"] = "c" * 64
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("UPDATE {} SET {}=%s::jsonb WHERE query_id=%s").format(
                store._queries, sql.Identifier(column)), (json.dumps(data), query_id))
        before = await asyncio.to_thread(store._read_sync, task_id)
        with pytest.raises(ValueError):
            await store.list_target_queries(task_id, lease, namespace="qa:test")
        assert await asyncio.to_thread(store._read_sync, task_id) == before
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE task_id=%s").format(store._table), (task_id,))


async def test_concurrent_query_intents_and_result_writes_do_not_overwrite_history():
    settings = get_settings()
    store = PostgresResourceCleanupStore(settings)
    await store.initialize()
    task_id = f"query-concurrent-{uuid4().hex}"
    lease = replace(expired_lease("docker", "slot-concurrent", "a" * 64), allocation_id="b" * 32)
    kwargs = {"namespace": "qa:test", "observer_worker_id": "cleanup"}
    observation = DockerResourceQueryObservation(status="present", reason_code="container_present",
        observed_at=datetime.now(timezone.utc))
    try:
        await store.begin(task_id, lease, worker_id="original-worker")
        query_ids = await asyncio.gather(*(store.begin_target_query(task_id, lease, **kwargs) for _ in range(4)))
        await asyncio.gather(*(store.finish_target_query(task_id, lease, query_id, observation, **kwargs)
            for query_id in query_ids for _ in range(2)))
        rows = await store.list_target_queries(task_id, lease, namespace="qa:test")
        assert len(rows) == len({row["sequence"] for row in rows}) == 4
        assert {row["query_id"] for row in rows} == set(query_ids)
        assert all(row["result"] == observation.model_dump(mode="json") for row in rows)
        receipt = await asyncio.to_thread(store._read_sync, task_id)
        assert receipt["status"] == "running" and receipt["attempts"] == 1
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE task_id=%s").format(store._table), (task_id,))


async def test_request_context_without_worker_keeps_adapter_and_query_observer_consistent():
    settings = get_settings()
    store = PostgresResourceCleanupStore(settings)
    await store.initialize()
    task_id = f"query-context-{uuid4().hex}"
    lease = replace(expired_lease("docker", "slot-context", "a" * 64), allocation_id="b" * 32)
    manager = Manager()
    manager.namespace = "qa:test"
    class Allocations:
        async def get_many(self, ids, *, namespace):
            return {lease.allocation_id: {"allocation_id": lease.allocation_id, "lease_namespace": namespace,
                "lease_record": lease.__dict__, "container_id": lease.external_resource_id}}
    class Docker:
        async def container_exists(self, _target):
            return False
    callback = DockerResourceCleanup(settings, allocations=Allocations(), namespace=manager.namespace, docker=Docker())
    try:
        await store.begin(task_id, lease, worker_id="original-worker")
        with bind_request_context(session_id="query-session-without-worker"):
            await ResourceCleanupTaskHandler(store, {"docker": callback}, manager=manager)(QueuedTask(
                "context-0", {"task_type": "cleanup_task", "task_id": task_id, **lease.__dict__}))
        row = (await store.list_target_queries(task_id, lease, namespace=manager.namespace))[0]
        assert row["request"]["observer_worker_id"] == "cleanup"
        assert row["result"]["absence_observation"]["observer_worker_id"] == "cleanup"
        assert (await store.reconcile(task_id, lease)).status == "completed"
    finally:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DELETE FROM {} WHERE task_id=%s").format(store._table), (task_id,))
