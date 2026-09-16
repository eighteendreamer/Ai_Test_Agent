from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from src.core.config import get_settings
from src.infrastructure.redis_hot_memory_store import RedisHotMemoryStore


@pytest_asyncio.fixture
async def hot_store():
    store = RedisHotMemoryStore(get_settings().database.redis_url, ttl_seconds=120)
    await store.connect()
    session_id = f"live-compaction-{uuid4().hex}"
    try:
        yield store, session_id
    finally:
        await store.clear_session(session_id)
        await store.close()


@pytest.mark.asyncio
async def test_live_redis_compaction_cursor_keeps_events_appended_after_cutoff(hot_store):
    store, session_id = hot_store
    await store.append_event(session_id, {"id": "e1", "type": "message"})
    await store.append_event(session_id, {"id": "e2", "type": "message"})
    await store.append_event(session_id, {"id": "e3", "type": "message"})

    assert await store.commit_compaction(session_id, key="e2:v1", last_event_id="e2")
    assert [item["id"] for item in await store.list_events(session_id)] == ["e3"]
    assert await store.commit_compaction(session_id, key="e2:v1", last_event_id="e2")
