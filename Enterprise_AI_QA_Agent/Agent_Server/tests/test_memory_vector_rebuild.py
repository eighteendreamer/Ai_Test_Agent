from unittest.mock import AsyncMock

import pytest

from src.application.context.memory_vector_rebuild_service import MemoryVectorRebuildService
from src.infrastructure.redis_vector_store import RedisVectorStore
from src.schemas.memory import MemoryVectorRecord


class Memory:
    def __init__(self):
        self.records = [MemoryVectorRecord(id=str(i), embedding=[1., 0.], stale=i == 2, metadata={
            "embedding_version": "v1", "embedding_dimension": 2, "project_id": "p1",
        }) for i in range(5)]
        self.cursors = []

    async def vector_inventory(self, version):
        return {2: 5} if version == "v1" else {}

    async def list_vector_records(self, version, *, after_id, limit):
        self.cursors.append(after_id)
        return [record for record in self.records if after_id is None or record.id > after_id][:limit]


class Vector:
    pack_vector = staticmethod(RedisVectorStore.pack_vector)
    def __init__(self):
        self.connect = AsyncMock()
        self.create_index = AsyncMock()
        self.upsert = AsyncMock(side_effect=self.write)
        self.docs = {}

    async def write(self, **kwargs):
        self.docs[kwargs["point_id"]] = kwargs


@pytest.mark.asyncio
async def test_preview_never_touches_redis_or_exports_vectors():
    memory, vector = Memory(), Vector()
    result = await MemoryVectorRebuildService(memory, vector).rebuild("v1")
    assert result.source_count == 5 and result.dimension == 2 and result.status == "preview"
    vector.connect.assert_not_awaited()
    vector.upsert.assert_not_awaited()
    assert not memory.cursors


@pytest.mark.asyncio
async def test_rebuild_keyset_pages_and_retry_is_idempotent():
    memory, vector = Memory(), Vector()
    service = MemoryVectorRebuildService(memory, vector, batch_size=2)
    first = await service.rebuild("v1", execute=True)
    assert first.replicated == 5 and first.status == "completed"
    assert memory.cursors == [None, "1", "3", "4"]
    assert vector.docs["2"]["metadata"]["status"] == "stale"
    second = await service.rebuild("v1", execute=True)
    assert second.replicated == 5 and len(vector.docs) == 5


@pytest.mark.asyncio
async def test_failure_is_not_reported_as_success_and_can_restart(caplog):
    memory, vector = Memory(), Vector()
    service = MemoryVectorRebuildService(memory, vector, batch_size=2)
    vector.upsert.side_effect = [None, ConnectionError("private driver data")]
    with pytest.raises(ConnectionError):
        await service.rebuild("v1", execute=True)
    assert "memory_vector_rebuild_failed" in caplog.text
    assert "private driver data" not in caplog.text
    vector.upsert.side_effect = vector.write
    assert (await service.rebuild("v1", execute=True)).replicated == 5


@pytest.mark.asyncio
async def test_mixed_dimensions_block_before_index_creation():
    memory, vector = Memory(), Vector()
    memory.vector_inventory = AsyncMock(return_value={2: 4, 3: 1})
    with pytest.raises(ValueError, match="inconsistent dimensions"):
        await MemoryVectorRebuildService(memory, vector).rebuild("v1", execute=True)
    vector.create_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_version_is_empty_not_fake_rebuild():
    memory, vector = Memory(), Vector()
    result = await MemoryVectorRebuildService(memory, vector).rebuild("missing", execute=True)
    assert result.status == "empty" and result.dimension is None
    vector.connect.assert_not_awaited()
