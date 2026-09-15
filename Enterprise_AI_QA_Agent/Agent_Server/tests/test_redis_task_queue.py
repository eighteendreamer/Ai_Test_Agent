from __future__ import annotations

import pytest

from src.infrastructure.redis_task_queue import RedisTaskQueue


class FakeRedis:
    def __init__(self) -> None:
        self.calls = []

    async def xgroup_create(self, **kwargs):
        self.calls.append(("xgroup_create", kwargs))

    async def xadd(self, stream, fields, **kwargs):
        self.calls.append(("xadd", stream, fields, kwargs))
        return "1-0"

    async def xreadgroup(self, **kwargs):
        self.calls.append(("xreadgroup", kwargs))
        return [("stream:test", [("1-0", {"payload": '{"task_id":"t1"}'})])]

    async def xack(self, *args):
        self.calls.append(("xack", args))
        return 1

    async def xpending_range(self, *args, **kwargs):
        self.calls.append(("xpending_range", args, kwargs))
        return [{"message_id": "1-0", "times_delivered": 1}]

    async def xautoclaim(self, *args, **kwargs):
        self.calls.append(("xautoclaim", args, kwargs))
        return ["2-0", [("1-0", {"payload": '{"task_id":"t1"}'})], []]


@pytest.mark.asyncio
async def test_queue_enqueues_consumes_ack_and_reclaims() -> None:
    client = FakeRedis()
    queue = RedisTaskQueue("redis://unused", stream="stream:test", group="workers", client=client)
    await queue.connect()
    assert await queue.enqueue({"task_id": "t1", "session_id": "s1"}) == "1-0"
    tasks = await queue.consume(consumer="worker-1")
    assert tasks[0].payload == {"task_id": "t1"}
    assert await queue.ack(tasks[0].message_id) == 1
    assert (await queue.pending())[0]["message_id"] == "1-0"
    assert (await queue.reclaim(consumer="worker-2", min_idle_ms=100))[0].message_id == "1-0"


@pytest.mark.asyncio
async def test_queue_rejects_malformed_payload() -> None:
    class BadRedis(FakeRedis):
        async def xreadgroup(self, **kwargs):
            return [("stream:test", [("1-0", {"unexpected": "value"})])]

    queue = RedisTaskQueue("redis://unused", stream="stream:test", group="workers", client=BadRedis())
    await queue.connect()
    with pytest.raises(ValueError, match="has no payload"):
        await queue.consume(consumer="worker-1")
