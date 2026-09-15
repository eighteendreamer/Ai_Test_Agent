from __future__ import annotations

import asyncio

from src.infrastructure.redis_hot_memory_store import RedisHotMemoryStore


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.seq = 0

    async def ping(self): return True
    async def incr(self, key): self.seq += 1; return self.seq
    async def rpush(self, key, value): self.data.setdefault(key, []).append(value)
    async def ltrim(self, key, start, end): self.data[key] = self.data.get(key, [])[start:]
    async def expire(self, key, ttl): return 1
    async def lrange(self, key, start, end): return self.data.get(key, [])
    async def set(self, key, value, ex=None): self.data[key] = value
    async def get(self, key): return self.data.get(key)
    async def delete(self, *keys):
        for key in keys: self.data.pop(key, None)


def test_hot_memory_is_session_isolated_and_cursorable():
    async def run():
        store = RedisHotMemoryStore("redis://unused", client=FakeRedis(), max_events=2)
        await store.connect()
        await store.append_event("s1", {"type": "one"})
        event_id = await store.append_event("s1", {"type": "two"})
        await store.append_event("s1", {"type": "three"})
        assert [item["type"] for item in await store.list_events("s1")] == ["two", "three"]
        assert await store.list_events("s1", after_id=event_id) == [{"type": "three", "id": "3", "hot_stored_at": (await store.list_events("s1"))[1]["hot_stored_at"]}]

    asyncio.run(run())
