from __future__ import annotations

import asyncio
import pytest
from redis.exceptions import ResponseError

from src.infrastructure.redis_vector_store import RedisVectorStore


class FakeRedis:
    def __init__(self):
        self.commands = []
        self.indexes = {}
        self.values = {}
        self.hashes = {}
    async def ping(self): return True
    async def get(self, key): return self.values.get(key)
    async def set(self, key, value): self.values[key] = value; return True
    async def delete(self, key): return int(self.values.pop(key, None) is not None)
    async def execute_command(self, *args):
        self.commands.append(args)
        if args[0] == "FT.CREATE":
            if args[1] in self.indexes:
                raise ResponseError("Index already exists")
            self.indexes[args[1]] = {
                b"index_definition": {b"key_type": b"HASH", b"prefixes": [args[6]]},
                b"attributes": [{b"attribute": b"embedding", b"type": b"VECTOR",
                                 b"algorithm": b"HNSW", b"data_type": b"FLOAT32",
                                 b"dim": int(args[args.index("DIM") + 1]), b"distance_metric": b"COSINE"}],
            }
        if args[0] == "FT.INFO":
            stored = self.indexes[args[1]]
            if isinstance(stored, list):
                return [*stored, b"num_docs", len(self.hashes)]
            info = dict(stored)
            prefix = info[b"index_definition"][b"prefixes"][0]
            info[b"num_docs"] = sum(key.startswith(prefix) for key in self.hashes)
            return info
        if args[0] == "FT.SEARCH":
            info = self.indexes[args[1]]
            prefix = info[b"index_definition"][b"prefixes"][0]
            keys = [key for key in self.hashes if key.startswith(prefix)]
            if not keys:
                return [0]
            key = keys[0]
            return [1, key, [b"vector_distance", b"0"]]
        return [0]
    async def hset(self, key, mapping):
        self.commands.append(("HSET", key, mapping))
        self.hashes[key] = mapping


def test_vector_index_uses_dynamic_dimension_and_versioned_names():
    async def run():
        client = FakeRedis(); store = RedisVectorStore("redis://unused", client=client)
        await store.create_index(entity="memory", embedding_version="emb_v2", dimension=4096)
        await store.upsert(entity="memory", embedding_version="emb_v2", point_id="p1", vector=[1.] + [0.] * 4095, metadata={"project_id": "p"})
        assert "qa:vector:memory:emb_v2" in client.commands[0]
        assert "DIM" in client.commands[0] and "4096" in client.commands[0]
        assert client.commands[-1][0] == "HSET"
    asyncio.run(run())


@pytest.mark.asyncio
async def test_active_index_pointer_switches_only_after_index_contract_validation():
    client = FakeRedis()
    store = RedisVectorStore("redis://unused", client=client)
    await store.create_index(entity="memory", embedding_version="v1", dimension=2)
    await store.create_index(entity="memory", embedding_version="v2", dimension=2)

    assert await store.get_active_version(entity="memory") is None
    assert await store.activate_index(
        entity="memory", embedding_version="v1", dimension=2,
    ) == "qa:vector:memory:v1"
    assert await store.get_active_version(entity="memory") == "v1"
    assert await store.get_active_index(entity="memory") == "qa:vector:memory:v1"

    with pytest.raises(ValueError, match="contract"):
        await store.activate_index(
            entity="memory", embedding_version="v1", dimension=3,
        )
    assert await store.get_active_version(entity="memory") == "v1"

    await store.activate_index(entity="memory", embedding_version="v2", dimension=2)
    assert await store.get_active_version(entity="memory") == "v2"
    assert await store.deactivate_index(entity="memory") is True
    assert await store.get_active_index(entity="memory") is None


@pytest.mark.asyncio
async def test_replica_validation_checks_document_count_and_recall_probe():
    client = FakeRedis()
    store = RedisVectorStore("redis://unused", client=client)
    await store.create_index(entity="memory", embedding_version="v1", dimension=2)
    await store.upsert(
        entity="memory",
        embedding_version="v1",
        point_id="p1",
        vector=[1.0, 0.0],
    )

    await store.validate_replica(
        entity="memory",
        embedding_version="v1",
        dimension=2,
        expected_count=1,
        probe_id="p1",
        probe_vector=[1.0, 0.0],
        timeout_seconds=0.1,
        poll_interval_seconds=0.01,
    )

    with pytest.raises(TimeoutError, match="document count"):
        await store.validate_replica(
            entity="memory",
            embedding_version="v1",
            dimension=2,
            expected_count=2,
            timeout_seconds=0.01,
            poll_interval_seconds=0.005,
        )


def test_knn_limit_does_not_silently_use_redis_default_ten():
    async def run():
        client = FakeRedis()
        store = RedisVectorStore("redis://unused", client=client)
        await store.create_index(entity="memory", embedding_version="v1", dimension=2)
        await store.search(entity="memory", embedding_version="v1", vector=[1., 0.],
                           top_k=40, filters={"project_id": "project-a", "environment": "qa"})
        command = client.commands[-1]
        assert command[2].startswith("(@project_id:")
        assert command[command.index("LIMIT") + 1:command.index("LIMIT") + 3] == ("0", "40")
    asyncio.run(run())


@pytest.mark.asyncio
async def test_same_version_rejects_dimension_change_without_hash_write():
    client = FakeRedis()
    store = RedisVectorStore("unused", client=client)
    await store.create_index(entity="memory", embedding_version="v1", dimension=2)
    await store.create_index(entity="memory", embedding_version="v1", dimension=2)
    with pytest.raises(ValueError, match="contract"):
        await store.create_index(entity="memory", embedding_version="v1", dimension=3)
    with pytest.raises(ValueError, match="contract"):
        await store.upsert(entity="memory", embedding_version="v1", point_id="bad", vector=[1., 0., 0.])
    assert not any(command[0] == "HSET" for command in client.commands)


@pytest.mark.asyncio
async def test_resp2_metadata_is_validated():
    client = FakeRedis()
    store = RedisVectorStore("unused", client=client)
    name = await store.create_index(entity="memory", embedding_version="v1", dimension=2)
    info = client.indexes[name]
    def flat(value):
        return [part for pair in value.items() for part in pair]
    info[b"index_definition"] = flat(info[b"index_definition"])
    info[b"attributes"] = [flat(info[b"attributes"][0])]
    client.indexes[name] = flat(info)
    await store.validate_index(entity="memory", embedding_version="v1", dimension=2)


@pytest.mark.parametrize("vector", [[], [0., 0.], [float("nan"), 1.], [float("inf")], [1e100], [1e-100], [True]])
def test_invalid_vectors_are_rejected(vector):
    with pytest.raises(ValueError):
        RedisVectorStore.pack_vector(vector)


def test_version_names_do_not_collide_by_lossy_sanitization():
    with pytest.raises(ValueError):
        RedisVectorStore.index_name("memory", "v/1")
    with pytest.raises(ValueError):
        RedisVectorStore.index_name("memory", "")
