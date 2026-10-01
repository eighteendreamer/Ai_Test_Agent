"""P1-74: pause the formal worker, expire its claim, and take it over."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.context.memory_vector_rebuild_service import (
    MemoryVectorRebuildService,
)
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore
from src.infrastructure.redis_hot_memory_store import RedisHotMemoryStore
from src.infrastructure.redis_vector_store import RedisVectorStore
from src.infrastructure.storage_utils import ensure_utc_datetime
from src.runtime.compaction_worker import CompactionWorker
from src.runtime.postgres_compaction_record_store import PostgresCompactionRecordStore
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.memory import MemoryWriteRequest
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _drop_tables


@pytest_asyncio.fixture
async def compaction_harness():
    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires real PostgreSQL and Redis")
    settings = get_settings().model_copy(deep=True)
    suffix = uuid4().hex[:12]
    settings.database.postgres_compaction_table = f"ownership_compaction_{suffix}"
    settings.database.postgres_event_table = f"ownership_event_{suffix}"
    settings.database.postgres_memory_table = f"ownership_memory_{suffix}"
    tables = [
        settings.database.postgres_compaction_table,
        settings.database.postgres_event_table,
        settings.database.postgres_memory_table,
    ]
    records = PostgresCompactionRecordStore(settings, lease_seconds=300)
    hot = RedisHotMemoryStore(settings.database.redis_url, ttl_seconds=86400)
    session_id = f"ownership-{suffix}"
    try:
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_event_table} "
                "(id TEXT,session_id TEXT,event_sequence BIGINT)"
            )
        await records.initialize()
        await hot.connect()
        for event_id, role in [("e1", "user"), ("e2", "assistant")]:
            await hot.append_event(
                session_id,
                {
                    "id": event_id,
                    "type": "message",
                    "payload": {"role": role, "content": role},
                },
            )
        yield SimpleNamespace(
            settings=settings, records=records, hot=hot, session_id=session_id
        )
    finally:
        if hot._client is not None:
            keys = [
                key
                async for key in hot._client.scan_iter(
                    match=f"qa:session:{session_id}:*"
                )
            ]
            if keys:
                await hot._client.delete(*keys)
            await hot.close()
        _drop_tables(settings, tables)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "fail"])
async def test_old_worker_cannot_settle_a_new_owners_claim_or_trim_hot_memory(
    compaction_harness, outcome
):
    h = compaction_harness
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedMemory:
        async def write_turn_memory(self, **kwargs):
            entered.set()
            await release.wait()
            if outcome == "fail":
                raise RuntimeError("injected old compression failure")
            return ["old-memory"]

    worker = CompactionWorker(
        hot_memory_store=h.hot,
        memory_runtime_service=PausedMemory(),
        record_store=h.records,
        owner_id="old-worker",
    )
    execution = asyncio.create_task(
        worker.compact_session(session_id=h.session_id, turn_id="t1", trace_id="trace")
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        key = f"{h.session_id}:e2:v1"
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {h.settings.database.postgres_compaction_table} "
                "SET claimed_until=NOW()-INTERVAL '1 second' WHERE compaction_key=%s",
                (key,),
            )
        assert await h.records.begin(
            compaction_key=key,
            session_id=h.session_id,
            last_event_id="e2",
            compression_version="v1",
            owner_id="new-attempt",
        )
        release.set()
        assert await asyncio.wait_for(execution, timeout=3) is False
        record = await h.records.get(key)
        assert record.status == "running" and record.claimed_by == "new-attempt"
        assert record.memory_ids == []
        assert [item["id"] for item in await h.hot.list_events(h.session_id)] == [
            "e1",
            "e2",
        ]
    finally:
        release.set()
        await asyncio.gather(execution, return_exceptions=True)


@pytest.mark.asyncio
async def test_running_marker_uses_execution_lease_instead_of_hot_data_retention(
    compaction_harness,
):
    h = compaction_harness
    assert await h.hot.acquire_compaction(h.session_id, "e2:v1")
    marker = h.hot._key(h.session_id, "compaction:e2:v1")
    ttl = await h.hot._client.ttl(marker)
    assert 0 < ttl <= 300


@pytest.mark.asyncio
async def test_new_cycle_does_not_reuse_a_completed_compaction_key(compaction_harness):
    h = compaction_harness

    class Memory:
        def __init__(self):
            self.keys = []

        async def write_turn_memory(self, **kwargs):
            self.keys.append(kwargs["compaction_key"])
            return [f"memory-{len(self.keys)}"]

    memory = Memory()
    worker = CompactionWorker(
        hot_memory_store=h.hot, memory_runtime_service=memory, record_store=h.records
    )
    assert await worker.compact_session(
        session_id=h.session_id, turn_id="t1", trace_id="trace"
    )
    await h.hot.append_event(
        h.session_id,
        {"id": "e3", "type": "message", "payload": {"role": "user", "content": "next"}},
    )
    await h.hot.append_event(
        h.session_id,
        {
            "id": "e4",
            "type": "message",
            "payload": {"role": "assistant", "content": "next answer"},
        },
    )

    assert await worker.compact_session(
        session_id=h.session_id, turn_id="t2", trace_id="trace"
    )
    assert memory.keys == [f"{h.session_id}:e2:v1", f"{h.session_id}:e4:v1"]
    assert (await h.records.get(memory.keys[-1])).status == "completed"


@pytest.mark.asyncio
async def test_stale_redis_owner_cannot_commit_mark_pending_or_fail(compaction_harness):
    h = compaction_harness
    key = "e2:v1"
    assert await h.hot.acquire_compaction(h.session_id, key, owner_id="old-attempt")
    assert await h.hot.mark_compaction_pending(
        h.session_id, key=key, last_event_id="e2", owner_id="old-attempt"
    )
    await h.hot._client.delete(h.hot._key(h.session_id, f"compaction:{key}"))
    assert await h.hot.acquire_compaction(h.session_id, key, owner_id="new-attempt")
    assert await h.hot.mark_compaction_pending(
        h.session_id, key=key, last_event_id="e2", owner_id="new-attempt"
    )
    assert not await h.hot.commit_compaction(
        h.session_id, key=key, last_event_id="e2", owner_id="old-attempt"
    )
    assert not await h.hot.mark_compaction_pending(
        h.session_id, key=key, last_event_id="e1", owner_id="old-attempt"
    )
    assert not await h.hot.mark_compaction_failed(
        h.session_id, key=key, error="late failure", owner_id="old-attempt"
    )
    assert not await h.hot.complete_compaction(
        h.session_id, key, owner_id="old-attempt"
    )
    assert not await h.hot.commit_compaction(h.session_id, key=key, last_event_id="e2")
    assert (await h.hot.get_compaction_state(h.session_id))["owner_id"] == "new-attempt"
    assert [row["id"] for row in await h.hot.list_events(h.session_id)] == ["e1", "e2"]
    assert await h.hot.commit_compaction(
        h.session_id, key=key, last_event_id="e2", owner_id="new-attempt"
    )
    assert await h.hot.commit_compaction(
        h.session_id, key=key, last_event_id="e2", owner_id="new-attempt"
    )


@pytest.mark.asyncio
async def test_completed_pg_record_repairs_failed_cache_commit_without_recompressing(
    compaction_harness, monkeypatch
):
    h = compaction_harness
    calls = []

    class Memory:
        async def write_turn_memory(self, **kwargs):
            calls.append(kwargs["compaction_key"])
            return ["durable-memory"]

    commit = h.hot.commit_compaction

    async def fail_commit_once(*args, **kwargs):
        monkeypatch.setattr(h.hot, "commit_compaction", commit)
        raise ConnectionError("injected Redis commit failure")

    monkeypatch.setattr(h.hot, "commit_compaction", fail_commit_once)
    worker = CompactionWorker(
        hot_memory_store=h.hot, memory_runtime_service=Memory(), record_store=h.records
    )
    assert not await worker.compact_session(
        session_id=h.session_id, turn_id="t1", trace_id="trace"
    )
    key = f"{h.session_id}:e2:v1"
    assert (await h.records.get(key)).status == "completed"
    assert [row["id"] for row in await h.hot.list_events(h.session_id)] == ["e1", "e2"]
    # New events arriving while projection is retried must not be cleared.
    await h.hot.append_event(h.session_id, {"id": "e3", "type": "message"})
    assert await worker.compact_session(
        session_id=h.session_id, turn_id="t1", trace_id="trace"
    )
    assert calls == [key]
    assert [row["id"] for row in await h.hot.list_events(h.session_id)] == ["e3"]


@pytest.mark.asyncio
async def test_legacy_running_marker_is_bounded_on_first_retry(compaction_harness):
    h = compaction_harness
    marker = h.hot._key(h.session_id, "compaction:e2:v1")
    await h.hot._client.set(marker, "running", ex=86400)
    assert not await h.hot.acquire_compaction(
        h.session_id, "e2:v1", owner_id="new-attempt", lease_seconds=300
    )
    assert 0 < await h.hot._client.ttl(marker) <= 300


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_settled_marker_keeps_owner_and_rejects_unowned_mutation(
    compaction_harness, status
):
    h = compaction_harness
    assert await h.hot.acquire_compaction(h.session_id, "e2:v1", owner_id="owner")
    if status == "completed":
        assert await h.hot.complete_compaction(h.session_id, "e2:v1", owner_id="owner")
    else:
        assert await h.hot.mark_compaction_failed(
            h.session_id, key="e2:v1", error="failure", owner_id="owner"
        )
        assert not await h.hot.commit_compaction(
            h.session_id, key="e2:v1", last_event_id="e2", owner_id="owner"
        )
    assert not await h.hot.mark_compaction_failed(
        h.session_id, key="e2:v1", error="unowned failure"
    )


@pytest.mark.asyncio
async def test_durable_completion_without_hot_cursor_preserves_new_events(
    compaction_harness,
):
    h = compaction_harness

    class Memory:
        async def write_turn_memory(self, **kwargs):
            # Expiration/eviction during the durable write removes the cursor.
            await h.hot._client.delete(h.hot._key(h.session_id, "events"))
            await h.hot.append_event(h.session_id, {"id": "e3", "type": "message"})
            return ["durable-memory"]

    worker = CompactionWorker(
        hot_memory_store=h.hot, memory_runtime_service=Memory(), record_store=h.records
    )
    assert await worker.compact_session(
        session_id=h.session_id, turn_id="t1", trace_id="trace"
    )
    assert (await h.records.get(f"{h.session_id}:e2:v1")).status == "completed"
    assert [row["id"] for row in await h.hot.list_events(h.session_id)] == ["e3"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "fail"])
async def test_pg_rejects_expired_owner_even_before_takeover(
    compaction_harness, outcome
):
    h = compaction_harness
    key = f"{h.session_id}:e2:v1"
    assert await h.records.begin(
        compaction_key=key,
        session_id=h.session_id,
        last_event_id="e2",
        compression_version="v1",
        owner_id="expired-attempt",
    )
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_compaction_table} "
            "SET claimed_until=NOW()-INTERVAL '1 second' WHERE compaction_key=%s",
            (key,),
        )
    if outcome == "complete":
        assert not await h.records.complete(
            key, ["old-memory"], owner_id="expired-attempt"
        )
    else:
        assert not await h.records.fail(key, "old failure", owner_id="expired-attempt")
    assert (await h.records.get(key)).status == "running"


@pytest.mark.asyncio
async def test_expired_worker_can_be_replaced_and_late_success_is_rejected(
    compaction_harness,
):
    h = compaction_harness
    h.records = PostgresCompactionRecordStore(h.settings, lease_seconds=12)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Memory:
        async def write_turn_memory(self, **kwargs):
            calls.append(kwargs["compaction_key"])
            if len(calls) == 1:
                entered.set()
                await release.wait()
                return ["old-memory"]
            return ["new-memory"]

    # Reuse the same worker: each invocation still needs a different claim ID.
    worker = CompactionWorker(
        hot_memory_store=h.hot,
        memory_runtime_service=Memory(),
        record_store=h.records,
        owner_id="same-process",
    )
    first = asyncio.create_task(
        worker.compact_session(session_id=h.session_id, turn_id="t1", trace_id="trace")
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        key = f"{h.session_id}:e2:v1"
        original = await h.records.get(key)
        marker = h.hot._key(h.session_id, "compaction:e2:v1")
        assert 0 < await h.hot._client.ttl(marker) <= h.records.lease_seconds
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {h.settings.database.postgres_compaction_table} "
                "SET claimed_until=NOW()-INTERVAL '1 second' WHERE compaction_key=%s",
                (key,),
            )
        await h.hot._client.delete(marker)
        assert await worker.compact_session(
            session_id=h.session_id, turn_id="t1", trace_id="trace"
        )
        release.set()
        assert not await asyncio.wait_for(first, timeout=3)
        completed = await h.records.get(key)
        assert completed.status == "completed" and completed.memory_ids == [
            "new-memory"
        ]
        assert completed.claimed_by is None
        assert (await h.hot.get_compaction_state(h.session_id))["status"] == "completed"
        assert await h.hot.list_events(h.session_id) == []
        assert original.claimed_by.startswith("same-process:")
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_slow_compression_keeps_both_claims_alive(compaction_harness):
    h = compaction_harness
    h.records = PostgresCompactionRecordStore(h.settings, lease_seconds=1)

    class SlowMemory:
        async def write_turn_memory(self, **kwargs):
            await asyncio.sleep(1.2)
            record = await h.records.get(f"{h.session_id}:e2:v1")
            assert record.status == "running"
            assert not await h.records.begin(
                compaction_key=record.compaction_key,
                session_id=h.session_id,
                last_event_id="e2",
                compression_version="v1",
                owner_id="competing-worker",
            )
            assert not await h.hot.acquire_compaction(
                h.session_id, "e2:v1", owner_id="competing-worker", lease_seconds=1
            )
            await asyncio.sleep(1)
            return ["slow-memory"]

    worker = CompactionWorker(
        hot_memory_store=h.hot,
        memory_runtime_service=SlowMemory(),
        record_store=h.records,
    )
    assert await worker.compact_session(
        session_id=h.session_id, turn_id="t1", trace_id="trace"
    )
    record = await h.records.get(f"{h.session_id}:e2:v1")
    assert record.status == "completed" and record.memory_ids == ["slow-memory"]
    assert await h.hot.list_events(h.session_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["pg_owner", "redis_owner", "transport", "timeout"])
async def test_lost_heartbeat_cancels_memory_and_retains_events(
    compaction_harness, monkeypatch, fault, caplog
):
    h = compaction_harness
    h.records = PostgresCompactionRecordStore(h.settings, lease_seconds=1)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    key = f"{h.session_id}:e2:v1"

    class Memory:
        async def write_turn_memory(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    worker = CompactionWorker(
        hot_memory_store=h.hot, memory_runtime_service=Memory(), record_store=h.records
    )
    execution = asyncio.create_task(
        worker.compact_session(session_id=h.session_id, turn_id="t1", trace_id="trace")
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        if fault == "pg_owner":
            with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {h.settings.database.postgres_compaction_table} "
                    "SET claimed_by='new-owner' WHERE compaction_key=%s",
                    (key,),
                )
        elif fault == "redis_owner":
            await h.hot._client.delete(h.hot._key(h.session_id, "compaction:e2:v1"))
            assert await h.hot.acquire_compaction(
                h.session_id, "e2:v1", owner_id="new-owner", lease_seconds=10
            )
        else:

            async def broken_renew(*args, **kwargs):
                if fault == "timeout":
                    await asyncio.Event().wait()
                raise ConnectionError("injected renewal transport failure")

            monkeypatch.setattr(h.hot, "renew_compaction", broken_renew)
        assert await asyncio.wait_for(execution, timeout=3) is False
        assert cancelled.is_set()
        assert [row["id"] for row in await h.hot.list_events(h.session_id)] == [
            "e1",
            "e2",
        ]
        record = await h.records.get(key)
        assert record.status != "completed" and record.memory_ids == []
        if fault == "pg_owner":
            assert record.status == "running" and record.claimed_by == "new-owner"
        assert any(
            r.getMessage() == "compaction_lease_watch_stopped" for r in caplog.records
        )
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_memory_cleanup_and_stops_heartbeat(
    compaction_harness,
):
    h = compaction_harness
    h.records = PostgresCompactionRecordStore(h.settings, lease_seconds=1)
    entered, cleanup_started, release, cleaned = (asyncio.Event() for _ in range(4))

    class Memory:
        async def write_turn_memory(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release.wait()
                cleaned.set()

    worker = CompactionWorker(
        hot_memory_store=h.hot, memory_runtime_service=Memory(), record_store=h.records
    )
    execution = asyncio.create_task(
        worker.compact_session(session_id=h.session_id, turn_id="t1", trace_id="trace")
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        execution.cancel()
        await asyncio.wait_for(cleanup_started.wait(), timeout=3)
        execution.cancel()
        await asyncio.sleep(0)
        assert not execution.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=3)
        assert cleaned.is_set()
        before = await h.records.get(f"{h.session_id}:e2:v1")
        await asyncio.sleep(0.5)
        after = await h.records.get(before.compaction_key)
        assert after.claimed_until == before.claimed_until
    finally:
        release.set()
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


@pytest.mark.asyncio
async def test_renewals_do_not_revive_expired_or_settled_owners(compaction_harness):
    h = compaction_harness
    key = f"{h.session_id}:e2:v1"
    assert await h.records.begin(
        compaction_key=key,
        session_id=h.session_id,
        last_event_id="e2",
        compression_version="v1",
        owner_id="owner",
    )
    assert not await h.records.renew(key, owner_id="different-owner")
    assert await h.records.renew(key, owner_id="owner")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_compaction_table} "
            "SET claimed_until=NOW()-INTERVAL '1 second' WHERE compaction_key=%s",
            (key,),
        )
    assert not await h.records.renew(key, owner_id="owner")
    assert await h.records.begin(
        compaction_key=key,
        session_id=h.session_id,
        last_event_id="e2",
        compression_version="v1",
        owner_id="new-owner",
    )
    assert await h.records.complete(key, [], owner_id="new-owner")
    assert not await h.records.renew(key, owner_id="new-owner")

    assert await h.hot.acquire_compaction(h.session_id, "e2:v1", owner_id="owner")
    assert not await h.hot.renew_compaction(
        h.session_id, "e2:v1", owner_id="different", lease_seconds=300
    )
    assert not await h.hot.renew_compaction(
        h.session_id, "e2:v1", owner_id="", lease_seconds=300
    )
    assert await h.hot.renew_compaction(
        h.session_id, "e2:v1", owner_id="owner", lease_seconds=300
    )
    assert await h.hot.complete_compaction(h.session_id, "e2:v1", owner_id="owner")
    assert not await h.hot.renew_compaction(
        h.session_id, "e2:v1", owner_id="owner", lease_seconds=300
    )
    await h.hot._client.delete(h.hot._key(h.session_id, "compaction:e2:v1"))
    assert not await h.hot.renew_compaction(
        h.session_id, "e2:v1", owner_id="owner", lease_seconds=300
    )


@pytest.mark.asyncio
async def test_cancelled_memory_thread_cannot_overwrite_completed_takeover(
    compaction_harness, monkeypatch
):
    h = compaction_harness
    memory_store = PostgresVectorMemoryStore(h.settings)
    await memory_store.initialize()
    memory = MemoryRuntimeService(memory_store)
    entered, release, finished = (threading.Event() for _ in range(3))
    errors = []
    write = memory_store._write_sync

    def paused_write(request):
        if request.content != "User goal: old-user":
            return write(request)
        entered.set()
        try:
            if not release.wait(timeout=10):
                raise TimeoutError("test thread release was not delivered")
            return write(request)
        except Exception as exc:
            errors.append(exc)
            raise
        finally:
            finished.set()

    monkeypatch.setattr(memory_store, "_write_sync", paused_write)
    worker = CompactionWorker(
        hot_memory_store=h.hot, memory_runtime_service=memory, record_store=h.records
    )
    old = asyncio.create_task(
        worker.compact_session(
            session_id=h.session_id,
            turn_id="old-turn",
            trace_id="trace-old",
            user_message="old-user",
            assistant_message="old-answer",
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        # asyncio cancellation does not stop the already running memory thread.
        assert not finished.is_set()
        key = f"{h.session_id}:e2:v1"
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {h.settings.database.postgres_compaction_table} "
                "SET claimed_until=NOW()-INTERVAL '1 second' WHERE compaction_key=%s",
                (key,),
            )
        await h.hot._client.delete(h.hot._key(h.session_id, "compaction:e2:v1"))
        assert await worker.compact_session(
            session_id=h.session_id,
            turn_id="new-turn",
            trace_id="trace-new",
            user_message="new-user",
            assistant_message="new-answer",
        )
        assert (await h.records.get(key)).status == "completed"
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT content FROM {h.settings.database.postgres_memory_table} "
                "WHERE source='session.user' AND session_id=%s",
                (h.session_id,),
            )
            assert cur.fetchone()["content"] == "User goal: new-user"
        assert len(errors) == 1 and isinstance(errors[0], ContinuationLeaseLostError)
    finally:
        release.set()
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["expired", "completed", "failed", "wrong_owner", "wrong_session", "missing_claim"],
)
async def test_stale_memory_write_is_rejected_before_vector_replication(
    compaction_harness, fault
):
    h = compaction_harness
    store = PostgresVectorMemoryStore(h.settings)
    await store.initialize()
    key = f"{h.session_id}:e2:v1"
    assert await h.records.begin(
        compaction_key=key,
        session_id=h.session_id,
        last_event_id="e2",
        compression_version="v1",
        owner_id="owner",
    )
    if fault == "expired":
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {h.settings.database.postgres_compaction_table} "
                "SET claimed_until=NOW()-INTERVAL '1 second' WHERE compaction_key=%s",
                (key,),
            )
    elif fault == "completed":
        assert await h.records.complete(key, [], owner_id="owner")
    elif fault == "failed":
        assert await h.records.fail(key, "injected failure", owner_id="owner")

    class Replica:
        def __init__(self):
            self.calls = []

        async def create_index(self, **kwargs):
            self.calls.append("index")

        async def upsert(self, **kwargs):
            self.calls.append("upsert")

    replica = Replica()
    service = MemoryRuntimeService(store, vector_store=replica)
    request = MemoryWriteRequest(
        content="stale-memory",
        session_id="other-session" if fault == "wrong_session" else h.session_id,
        compaction_key=key + ":missing" if fault == "missing_claim" else key,
        compaction_owner_id="other-owner" if fault == "wrong_owner" else "owner",
        metadata={
            "memory_id": "stale-memory",
            "embedding": [1.0, 0.0],
            "embedding_version": "test-v1",
        },
    )
    with pytest.raises(ContinuationLeaseLostError):
        await service._write_point(request)
    assert replica.calls == []
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) AS total FROM {h.settings.database.postgres_memory_table}"
        )
        assert cur.fetchone()["total"] == 0


@pytest.mark.asyncio
async def test_memory_lease_expiry_is_checked_after_waiting_for_claim_lock(
    compaction_harness,
):
    h = compaction_harness
    h.records = PostgresCompactionRecordStore(h.settings, lease_seconds=1)
    store = PostgresVectorMemoryStore(h.settings)
    await store.initialize()
    key = f"{h.session_id}:e2:v1"
    assert await h.records.begin(
        compaction_key=key,
        session_id=h.session_id,
        last_event_id="e2",
        compression_version="v1",
        owner_id="owner",
    )
    request = MemoryWriteRequest(
        content="late",
        session_id=h.session_id,
        compaction_key=key,
        compaction_owner_id="owner",
    )
    execution = None
    try:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {h.settings.database.postgres_compaction_table} "
                "WHERE compaction_key=%s FOR UPDATE",
                (key,),
            )
            execution = asyncio.create_task(store.write(request))
            await asyncio.sleep(1.2)
            assert not execution.done()
        with pytest.raises(ContinuationLeaseLostError):
            await asyncio.wait_for(execution, timeout=3)
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS total FROM {h.settings.database.postgres_memory_table}"
            )
            assert cur.fetchone()["total"] == 0
    finally:
        if execution is not None:
            await asyncio.gather(execution, return_exceptions=True)


def test_runtime_carries_compaction_proof_without_serializing_owner():
    class Store:
        def __init__(self):
            self.requests = []

        async def write(self, request):
            self.requests.append(request)

    store = Store()
    service = MemoryRuntimeService(store)
    asyncio.run(
        service.write_turn_memory(
            "s1",
            "t1",
            "trace",
            "goal",
            "answer",
            [],
            {},
            compaction_key="s1:e2:v1",
            compaction_owner_id="owner",
        )
    )
    assert len(store.requests) == 3
    for request in store.requests:
        assert (
            request.compaction_key == "s1:e2:v1"
            and request.compaction_owner_id == "owner"
        )
        assert "compaction_key" not in request.model_dump()
        assert "compaction_owner_id" not in request.model_dump()
        assert "compaction_owner_id" not in request.metadata


@pytest_asyncio.fixture
async def vector_harness(compaction_harness):
    h = compaction_harness
    suffix = h.session_id.removeprefix("ownership-")

    class IsolatedVector(RedisVectorStore):
        def index_name(self, entity, embedding_version):
            return f"qa:ownership:{suffix}:index:{embedding_version}"

        def prefix(self, entity, embedding_version):
            return f"qa:ownership:{suffix}:doc:{embedding_version}:"

        def active_key(self, entity):
            return f"qa:ownership:{suffix}:active:{entity}"

    vector = IsolatedVector(h.settings.database.redis_url)
    store = PostgresVectorMemoryStore(h.settings)
    index = None
    try:
        await store.initialize()
        await vector.connect()
        index = await vector.create_index(
            entity="memory", embedding_version="test-v1", dimension=2
        )
        yield SimpleNamespace(h=h, store=store, vector=vector)
    finally:
        if index is not None:
            await vector._client.execute_command("FT.DROPINDEX", index, "DD")
            await vector._client.delete(vector.active_key("memory"))
        await vector.close()


@pytest.mark.asyncio
async def test_late_old_replica_cannot_replace_new_compaction_vector(
    vector_harness, monkeypatch
):
    h, store, vector = vector_harness.h, vector_harness.store, vector_harness.vector
    key = f"{h.session_id}:e2:v1"
    assert await h.records.begin(
        compaction_key=key,
        session_id=h.session_id,
        last_event_id="e2",
        compression_version="v1",
        owner_id="old-owner",
    )
    entered, release = asyncio.Event(), asyncio.Event()
    upsert = vector.upsert

    async def paused_upsert(**kwargs):
        if kwargs["vector"] == [1.0, 0.0]:
            entered.set()
            await release.wait()
        return await upsert(**kwargs)

    monkeypatch.setattr(vector, "upsert", paused_upsert)
    service = MemoryRuntimeService(store, vector_store=vector)

    def request(owner, content, embedding):
        return MemoryWriteRequest(
            session_id=h.session_id,
            compaction_key=key,
            compaction_owner_id=owner,
            content=content,
            metadata={
                "memory_id": "same-compaction-point",
                "embedding": embedding,
                "embedding_version": "test-v1",
                "embedding_dimension": 2,
            },
        )

    old = asyncio.create_task(
        service._write_point(request("old-owner", "old", [1.0, 0.0]))
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {h.settings.database.postgres_compaction_table} "
                "SET claimed_until=NOW()-INTERVAL '1 second' WHERE compaction_key=%s",
                (key,),
            )
        assert await h.records.begin(
            compaction_key=key,
            session_id=h.session_id,
            last_event_id="e2",
            compression_version="v1",
            owner_id="new-owner",
        )
        await service._write_point(request("new-owner", "new", [0.0, 1.0]))
        assert await h.records.complete(
            key, ["same-compaction-point"], owner_id="new-owner"
        )
        release.set()
        await asyncio.wait_for(old, timeout=3)
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT content FROM {h.settings.database.postgres_memory_table} WHERE id=%s",
                ("same-compaction-point",),
            )
            assert cur.fetchone()["content"] == "new"
        raw = await vector._client.hget(
            vector.prefix("memory", "test-v1") + "same-compaction-point", "embedding"
        )
        assert raw == vector.pack_vector([0.0, 1.0])
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_rebuild_snapshot_does_not_replace_new_replica_or_activate(
    vector_harness, monkeypatch
):
    store, vector = vector_harness.store, vector_harness.vector
    service = MemoryRuntimeService(store, vector_store=vector)

    def request(embedding):
        return MemoryWriteRequest(
            content="point",
            metadata={
                "memory_id": "p1",
                "embedding": embedding,
                "embedding_version": "test-v1",
                "embedding_dimension": 2,
            },
        )

    await service._write_point(request([1.0, 0.0]))
    snapshot = await store.list_vector_records("test-v1")
    newer = await service._write_point(request([0.0, 1.0]))
    assert newer.updated_at > snapshot[0].updated_at
    export = store.list_vector_records

    async def old_export(version, *, after_id=None, limit=64):
        if after_id is None:
            return snapshot
        return await export(version, after_id=after_id, limit=limit)

    monkeypatch.setattr(store, "list_vector_records", old_export)
    with pytest.raises(ValueError, match="probe vector"):
        await MemoryVectorRebuildService(store, vector).rebuild(
            "test-v1", execute=True, activate=True
        )
    raw = await vector._client.hget(
        vector.prefix("memory", "test-v1") + "p1", "embedding"
    )
    assert raw == vector.pack_vector([0.0, 1.0])
    assert await vector.get_active_version(entity="memory") is None


@pytest.mark.asyncio
async def test_current_rebuild_restores_versioned_replica_and_rejects_unversioned_overwrite(
    vector_harness,
):
    store, vector = vector_harness.store, vector_harness.vector
    service = MemoryRuntimeService(store, vector_store=vector)
    point = await service._write_point(
        MemoryWriteRequest(
            content="point",
            metadata={
                "memory_id": "p1",
                "embedding": [0.0, 1.0],
                "embedding_version": "test-v1",
                "embedding_dimension": 2,
            },
        )
    )
    key = vector.prefix("memory", "test-v1") + "p1"
    await vector._client.delete(key)
    result = await MemoryVectorRebuildService(store, vector).rebuild(
        "test-v1", execute=True
    )
    assert result.status == "validated"
    assert (
        await vector._client.hget(key, "_source_updated_at")
        == point.updated_at.isoformat(timespec="microseconds").encode()
    )
    await vector.upsert(
        entity="memory", embedding_version="test-v1", point_id="p1", vector=[1.0, 0.0]
    )
    assert await vector._client.hget(key, "embedding") == vector.pack_vector([0.0, 1.0])
    # Equal authoritative revisions remain repairable and idempotent.
    await vector.upsert(
        entity="memory",
        embedding_version="test-v1",
        point_id="p1",
        vector=[0.0, 1.0],
        source_updated_at=point.updated_at,
    )
    assert await vector._client.hget(key, "embedding") == vector.pack_vector([0.0, 1.0])


@pytest.mark.asyncio
async def test_memory_row_revision_is_monotonic_and_embedding_backfill_advances_it(
    vector_harness,
):
    h, store = vector_harness.h, vector_harness.store
    request = MemoryWriteRequest(content="point", metadata={"memory_id": "p1"})
    first = await store.write(request)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {h.settings.database.postgres_memory_table} SET updated_at=clock_timestamp()+INTERVAL '1 day' WHERE id='p1' RETURNING updated_at"
        )
        future = cur.fetchone()["updated_at"]
    second = await store.write(request)
    assert second.updated_at > first.updated_at
    assert second.updated_at > ensure_utc_datetime(future)
    metadata = {"embedding_version": "test-v1", "embedding_dimension": 2}
    assert await store.update_embeddings([("p1", [1.0, 0.0], metadata)]) == 1
    exported = await store.list_vector_records("test-v1")
    assert exported[0].updated_at > second.updated_at
    assert await store.update_embeddings([("p1", [0.0, 1.0], metadata)]) == 0
    assert (await store.list_vector_records("test-v1"))[0].updated_at == exported[
        0
    ].updated_at
