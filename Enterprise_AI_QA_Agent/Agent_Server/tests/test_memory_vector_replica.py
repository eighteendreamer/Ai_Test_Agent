from __future__ import annotations

import asyncio
from datetime import datetime

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


def test_memory_write_replicates_embedding_after_postgres():
    async def run():
        vector = Vector()
        service = MemoryRuntimeService(Store(), vector_store=vector)
        request = MemoryWriteRequest(content="x", metadata={"embedding": [1.0, 0.0], "embedding_version": "emb_v1"})
        point = await service._write_point(request)
        assert point.id == "m1"
        assert [item[0] for item in vector.calls] == ["index", "upsert"]
    asyncio.run(run())
