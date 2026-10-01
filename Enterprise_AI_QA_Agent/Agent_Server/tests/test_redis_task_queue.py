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

    async def eval(self, *args):
        self.calls.append(("eval", args))
        return "2-0"


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
    await queue.reclaim(consumer="worker-2", min_idle_ms=100)
    assert client.calls[-1][2]["start_id"] == "2-0"


@pytest.mark.asyncio
async def test_queue_quarantines_malformed_payload() -> None:
    class BadRedis(FakeRedis):
        async def xreadgroup(self, **kwargs):
            return [("stream:test", [("1-0", {"unexpected": "value"})])]

    client = BadRedis()
    queue = RedisTaskQueue("redis://unused", stream="stream:test", group="workers", client=client)
    await queue.connect()
    assert await queue.consume(consumer="worker-1") == []
    assert client.calls[-1][0] == "eval"
    assert client.calls[-1][1][2:] == (
        "stream:test", "stream:test:dead_letter", "workers", "worker-1", "1-0", "missing_payload",
    )


@pytest.mark.asyncio
async def test_nonblocking_read_and_pending_safe_enqueue():
    client = FakeRedis()
    queue = RedisTaskQueue("unused", stream="test", group="workers", client=client)
    await queue.consume(consumer="worker", block_ms=0)
    assert client.calls[-1][1]["block"] is None
    with pytest.raises(ValueError, match="MAXLEN"):
        await queue.enqueue({"task_id": "t"}, maxlen=1)
    assert not any(call[0] == "xadd" for call in client.calls)


def test_delivery_metadata_is_separate_from_business_payload():
    task = RedisTaskQueue._decode_rows([("test", [("1-0", {
        "payload": '{"task_id":"t"}', "retry_count": "2", "not_before_ms": "123",
    })])])[0]
    assert task.payload == {"task_id": "t"}
    assert task.retry_count == 2 and task.not_before_ms == 123


def test_legacy_retry_decode_does_not_mutate_received_fields():
    fields = {"payload": {"task_id": "t", "retry_count": 2}}
    task = RedisTaskQueue._decode_rows([("test", [("1-0", fields)])])[0]
    assert task.payload == {"task_id": "t"} and task.retry_count == 2
    assert fields["payload"] == {"task_id": "t", "retry_count": 2}
