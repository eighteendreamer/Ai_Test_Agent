from __future__ import annotations

import asyncio

from src.infrastructure.redis_vector_store import RedisVectorStore


class FakeRedis:
    def __init__(self): self.commands = []
    async def ping(self): return True
    async def execute_command(self, *args): self.commands.append(args); return [0]
    async def hset(self, key, mapping): self.commands.append(("HSET", key, mapping))


def test_vector_index_uses_dynamic_dimension_and_versioned_names():
    async def run():
        client = FakeRedis(); store = RedisVectorStore("redis://unused", client=client)
        await store.create_index(entity="memory", embedding_version="emb_v2", dimension=4096)
        await store.upsert(entity="memory", embedding_version="emb_v2", point_id="p1", vector=[0.1, 0.2], metadata={"project_id": "p"})
        assert "qa:vector:memory:emb_v2" in client.commands[0]
        assert "DIM" in client.commands[0] and "4096" in client.commands[0]
        assert client.commands[1][0] == "HSET"
    asyncio.run(run())
