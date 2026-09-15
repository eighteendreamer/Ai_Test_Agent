"""Reconcile a version's Redis replica from authoritative PostgreSQL vectors.

This is same-version disaster recovery, not model switching or re-embedding.
Retries restart the keyset scan and overwrite the same document IDs. Concurrent
business changes are still rechecked in PostgreSQL by the retrieval path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.contracts.memory_store import MemoryStoreProtocol
from src.infrastructure.redis_vector_store import RedisVectorStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorRebuildResult:
    embedding_version: str
    dimension: int | None
    source_count: int
    replicated: int
    status: str


class MemoryVectorRebuildService:
    def __init__(
        self, memory_store: MemoryStoreProtocol, vector_store: RedisVectorStore,
        *, batch_size: int = 64,
    ) -> None:
        if not 1 <= batch_size <= 1000:
            raise ValueError("Vector rebuild batch size must be between 1 and 1000")
        self._memory = memory_store
        self._vector = vector_store
        self._batch_size = batch_size

    async def rebuild(self, embedding_version: str, *, execute: bool = False) -> VectorRebuildResult:
        RedisVectorStore.index_name("memory", embedding_version)  # validate without connecting
        inventory = await self._memory.vector_inventory(embedding_version)
        if len(inventory) > 1:
            raise ValueError("Stored embedding version has inconsistent dimensions; refusing replica rebuild")
        dimension = next(iter(inventory), None)
        total = sum(inventory.values())
        if not execute or dimension is None:
            return VectorRebuildResult(embedding_version, dimension, total, 0, "preview" if not execute else "empty")

        replicated = 0
        cursor = None
        try:
            await self._vector.connect()
            await self._vector.create_index(entity="memory", embedding_version=embedding_version, dimension=dimension)
            while True:
                records = await self._memory.list_vector_records(
                    embedding_version, after_id=cursor, limit=self._batch_size,
                )
                if not records:
                    break
                for record in records:
                    if (record.metadata.get("embedding_version") != embedding_version
                            or record.metadata.get("embedding_dimension") != dimension
                            or len(record.embedding) != dimension):
                        raise ValueError("Stored vector metadata does not match rebuild contract")
                    self._vector.pack_vector(record.embedding)
                for record in records:
                    await self._vector.upsert(
                        entity="memory", embedding_version=embedding_version, point_id=record.id,
                        vector=record.embedding,
                        metadata={
                            "project_id": str(record.metadata.get("project_id") or ""),
                            "case_version_id": str(record.metadata.get("case_version_id") or ""),
                            "environment": str(record.metadata.get("environment") or ""),
                            "status": "stale" if record.stale else "active",
                        },
                    )
                    replicated += 1
                cursor = records[-1].id
                LOGGER.info("memory_vector_rebuild_batch", extra={
                    "embedding_version": embedding_version, "replicated": replicated,
                    "last_memory_id": cursor,
                })
            LOGGER.info("memory_vector_rebuild_completed", extra={
                "embedding_version": embedding_version, "replicated": replicated,
                "dimension": dimension,
            })
            return VectorRebuildResult(embedding_version, dimension, total, replicated, "completed")
        except Exception as exc:
            LOGGER.error("memory_vector_rebuild_failed", extra={
                "embedding_version": embedding_version, "replicated": replicated,
                "last_memory_id": cursor, "error_type": type(exc).__name__,
            })
            raise
