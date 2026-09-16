"""Isolated Redis 8.8 acceptance for vector validation and active-index switching."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from src.core.config import get_settings
from src.infrastructure.redis_vector_store import RedisVectorStore


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_REDIS_VECTOR_TESTS") != "1",
    reason="set RUN_LIVE_REDIS_VECTOR_TESTS=1 for real Redis vector acceptance",
)
@pytest.mark.asyncio
async def test_live_vector_validation_switch_and_rollback() -> None:
    suffix = uuid4().hex[:12]
    settings = get_settings()

    class IsolatedRedisVectorStore(RedisVectorStore):
        def index_name(self, entity: str, embedding_version: str) -> str:
            return f"qa:test:{suffix}:index:{entity}:{embedding_version}"

        def prefix(self, entity: str, embedding_version: str) -> str:
            return f"qa:test:{suffix}:doc:{entity}:{embedding_version}:"

        def active_key(self, entity: str) -> str:
            return f"qa:test:{suffix}:active:{entity}"

    store = IsolatedRedisVectorStore(
        settings.database.redis_url,
        socket_timeout_seconds=(
            settings.orchestration.redis_vector_socket_timeout_seconds
        ),
    )
    indexes: list[str] = []
    try:
        await store.connect()
        for version, point_id, vector in (
            ("v1", "point-v1", [1.0, 0.0]),
            ("v2", "point-v2", [0.0, 1.0]),
        ):
            indexes.append(
                await store.create_index(
                    entity="memory",
                    embedding_version=version,
                    dimension=2,
                )
            )
            await store.upsert(
                entity="memory",
                embedding_version=version,
                point_id=point_id,
                vector=vector,
                metadata={"project_id": "acceptance"},
            )
            await store.validate_replica(
                entity="memory",
                embedding_version=version,
                dimension=2,
                expected_count=1,
                probe_id=point_id,
                probe_vector=vector,
                timeout_seconds=5,
                poll_interval_seconds=0.05,
            )

        await store.activate_index(
            entity="memory", embedding_version="v1", dimension=2,
        )
        assert await store.get_active_version(entity="memory") == "v1"
        await store.activate_index(
            entity="memory", embedding_version="v2", dimension=2,
        )
        assert await store.get_active_version(entity="memory") == "v2"
        await store.activate_index(
            entity="memory", embedding_version="v1", dimension=2,
        )
        assert await store.get_active_version(entity="memory") == "v1"
    finally:
        if store._client is not None:
            await store.deactivate_index(entity="memory")
            existing = set(await store._client.execute_command("FT._LIST"))
            for index in indexes:
                if index.encode() in existing or index in existing:
                    await store._client.execute_command("FT.DROPINDEX", index, "DD")
        await store.close()
