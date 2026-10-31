from __future__ import annotations

import asyncio
from datetime import datetime
import logging

import pytest

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.schemas.memory import MemoryPoint, MemoryWriteRequest


class Store:
    backend = "postgres_pgvector"
    async def write(self, request):
        return MemoryPoint(id="m1", content=request.content, created_at=datetime.utcnow(), updated_at=datetime.utcnow())


class Vector:
    def __init__(self): self.calls = []
    async def create_index(self, **kwargs): self.calls.append(("index", kwargs))
    async def upsert(self, **kwargs): self.calls.append(("upsert", kwargs))


class FailingVector(Vector):
    async def upsert(self, **kwargs):
        self.calls.append(("upsert", kwargs))
        raise ConnectionError("redis-vector-private-failure")


def test_memory_write_replicates_embedding_after_postgres():
    async def run():
        vector = Vector()
        service = MemoryRuntimeService(Store(), vector_store=vector)
        request = MemoryWriteRequest(content="x", metadata={"embedding": [1.0, 0.0], "embedding_version": "emb_v1"})
        point = await service._write_point(request)
        assert point.id == "m1"
        assert [item[0] for item in vector.calls] == ["index", "upsert"]
    asyncio.run(run())


@pytest.mark.asyncio
async def test_memory_vector_replica_failure_is_visible_to_post_commit_effect(caplog):
    vector = FailingVector()
    service = MemoryRuntimeService(Store(), vector_store=vector)
    request = MemoryWriteRequest(
        content="private memory content",
        session_id="session-vector-failure",
        turn_id="turn-vector-failure",
        trace_id="trace-vector-failure",
        metadata={"embedding": [1.0, 0.0], "embedding_version": "emb_v1"},
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ConnectionError):
            await service._write_point(request)

    assert [item[0] for item in vector.calls] == ["index", "upsert"]
    record = next(
        record for record in caplog.records
        if record.getMessage() == "memory_vector_replica_write_failed"
    )
    assert record.memory_id == "m1"
    assert record.embedding_version == "emb_v1"
    assert record.status == "unknown"
    assert record.error_type == "ConnectionError"
    assert "redis-vector-private-failure" not in caplog.text
    assert "private memory content" not in caplog.text
