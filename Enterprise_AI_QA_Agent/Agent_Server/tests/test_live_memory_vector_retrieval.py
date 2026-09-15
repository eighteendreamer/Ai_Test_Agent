"""Opt-in real model + PostgreSQL + Redis test using isolated test tables/keys.

Run with RUN_LIVE_POSTGRES_TESTS=1. No user sessions or business indexes are changed.
"""
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.context.memory_vector_rebuild_service import MemoryVectorRebuildService
from src.containers import AppContainer
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore
from src.infrastructure.redis_vector_store import RedisVectorStore
from src.schemas.memory import MemorySearchRequest, MemoryWriteRequest
from tests.live_postgres_config import LivePostgresTestConfig


@pytest.mark.skipif(not LivePostgresTestConfig().run_live_postgres_tests,
                    reason="set RUN_LIVE_POSTGRES_TESTS=1 for real model/PostgreSQL/Redis")
@pytest.mark.asyncio
async def test_live_authoritative_retrieval_and_redis_outage():
    suffix = uuid4().hex[:12]
    table = f"test_mem_{suffix}"
    base = get_settings()
    settings = base.model_copy(update={"database": base.database.model_copy(
        update={"postgres_memory_table": table},
    )})

    class IsolatedRedis(RedisVectorStore):
        def index_name(self, entity, embedding_version):
            return f"qa:test:{suffix}:index:{embedding_version}"

        def prefix(self, entity, embedding_version):
            return f"qa:test:{suffix}:doc:{embedding_version}:"

    store = PostgresVectorMemoryStore(settings)
    redis = IsolatedRedis(base.database.redis_url)
    embedding = AppContainer().embedding_runtime_service()
    index = None
    try:
        await store.initialize()
        await redis.connect()
        result = await embedding.embed_texts(["login verification evidence"], use_cache=False)
        vector, version = result.vectors[0], result.embedding_version
        assert version and len(vector) == result.stored_dimension
        index = await redis.create_index(entity="memory", embedding_version=version, dimension=len(vector))
        service = MemoryRuntimeService(store, top_k=2, embedding_runtime_service=embedding, vector_store=redis)
        metadata = {"embedding": vector, "embedding_version": version,
                    "embedding_dimension": len(vector), "project_id": "project-a", "environment": "qa",
                    "mode_key": "security_testing"}

        async def write(identifier, **overrides):
            request = MemoryWriteRequest(
                content="login verification evidence", session_id="session-a", scope="session",
                tags=["browser"],
                metadata={**metadata, "memory_id": identifier},
            ).model_copy(update=overrides)
            return await service._write_point(request)

        for i in range(12):
            await write(f"valid-{i}")
        await write("other-session", session_id="session-b")
        await write("other-scope", scope="global")
        await write("other-kind", kind="observation")
        await write("other-tag", tags=["docker"])
        await write("stale", stale=True)
        await write("foreign", metadata={**metadata, "memory_id": "foreign", "project_id": "project-b"})
        # Emulate stale/incorrect Redis metadata, not authoritative database state.
        await redis._client.hset(redis.prefix("memory", version) + "stale", mapping={"status": "active"})
        await redis._client.hset(redis.prefix("memory", version) + "foreign", mapping={"project_id": "project-a"})
        # Old embeddings and mixed dimensions exist only in PostgreSQL.
        for identifier, alternate_vector, alternate_version in (
            ("old-model", vector, "old-version"),
            ("wrong-dimension", [1., 0.], version),
        ):
            await store.write(MemoryWriteRequest(
                content="login verification evidence", session_id="session-a",
                metadata={**metadata, "memory_id": identifier, "embedding": alternate_vector,
                          "embedding_dimension": len(alternate_vector), "embedding_version": alternate_version},
            ))

        filters = {"project_id": "project-a", "environment": "qa",
                   "__query_embedding": vector, "__embedding_version": version}
        request = MemorySearchRequest(query="login verification evidence", session_id="session-a",
                                      scopes=["session"], kinds=["episodic"], tags=["browser"],
                                      top_k=2, day_window=0, metadata_filters=filters)
        candidates = await redis.search(entity="memory", embedding_version=version, vector=vector,
                                        top_k=40, filters={"project_id": "project-a", "environment": "qa"})
        assert len(candidates) > 10  # RETURN/LIMIT contract
        store.search = AsyncMock(wraps=store.search)
        hits = await service._search_memory(request)
        assert len(hits) == 2 and all(hit.id.startswith("valid-") for hit in hits)
        store.search.assert_not_awaited()

        # Explicit candidate hydration cannot leak stale/session/project/version rows.
        restricted = request.model_copy(update={"metadata_filters": {
            **filters, "embedding_version": version, "embedding_dimension": len(vector),
        }})
        ids = ["stale", "foreign", "other-session", "other-scope", "other-kind", "other-tag",
               "old-model", "wrong-dimension", "valid-0"]
        assert [p.id for p in await store.search_candidates(restricted, ids)] == ["valid-0"]

        public_healthy = await service.retrieve_for_turn(
            "session-a", "trace-a", "login verification evidence",
            {"project_id": "project-a", "environment": "qa", "mode_key": "security_testing"},
        )
        assert public_healthy.hits
        assert all(p.session_id == "session-a" and p.metadata["project_id"] == "project-a"
                   for p in public_healthy.hits)
        store.search.assert_not_awaited()  # public entry used Redis IDs + DB hydration

        actual_search = redis.search
        redis.search = AsyncMock(side_effect=ConnectionError("simulated Redis outage"))
        fallback = await service._search_memory(request)
        assert len(fallback) == 2 and all(hit.id.startswith("valid-") for hit in fallback)
        store.search.assert_awaited_once()
        # Public Agent-facing retrieval also continues while Redis is unavailable.
        public_result = await service.retrieve_for_turn(
            "session-a", "trace-a", "login verification evidence",
            {"project_id": "project-a", "environment": "qa", "mode_key": "security_testing"},
        )
        assert public_result.hits and all(p.session_id == "session-a" and not p.stale
                                         and p.metadata["project_id"] == "project-a"
                                         for p in public_result.hits)
        wrong_environment = await service.retrieve_for_turn(
            "session-a", "trace-a", "login verification evidence",
            {"project_id": "project-a", "environment": "production", "mode_key": "security_testing"},
        )
        assert not wrong_environment.hits
        redis.search = actual_search
        await redis._client.execute_command("FT.DROPINDEX", index, "DD")
        index = None
        missing_index = await service._search_memory(request)
        assert len(missing_index) == 2 and all(hit.id.startswith("valid-") for hit in missing_index)

        recovery = MemoryVectorRebuildService(store, redis, batch_size=3)
        with pytest.raises(ValueError, match="inconsistent dimensions"):
            await recovery.rebuild(version, execute=True)
        # Remove only this test's deliberately corrupt vector fixture.
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {table} WHERE id = %s", ("wrong-dimension",))
        preview = await recovery.rebuild(version)
        assert preview.source_count == 18 and preview.dimension == len(vector)
        index = redis.index_name("memory", version)
        restored = await recovery.rebuild(version, execute=True)
        assert restored.replicated == 18 and restored.status == "completed"
        assert (await recovery.rebuild(version, execute=True)).replicated == 18
        with pytest.raises(ValueError, match="contract"):
            await redis.create_index(entity="memory", embedding_version=version, dimension=len(vector) + 1)
        with pytest.raises(ValueError, match="contract"):
            await redis.upsert(entity="memory", embedding_version=version, point_id="reject", vector=[1., 0.])
        assert not await redis._client.exists(redis.prefix("memory", version) + "reject")
        store.search.reset_mock()
        rebuilt_hits = await service._search_memory(request)
        assert len(rebuilt_hits) == 2 and all(p.id.startswith("valid-") for p in rebuilt_hits)
        store.search.assert_not_awaited()
        print(f"live_memory_retrieval dimension={len(vector)} candidates={len(candidates)} "
              f"hydration=passed outage=passed missing_index=passed restored={restored.replicated} "
              "retry=passed dimension_mismatch=blocked")
    finally:
        if index is not None and index.encode() in await redis._client.execute_command("FT._LIST"):
            await redis._client.execute_command("FT.DROPINDEX", index, "DD")
        await redis.close()
        # Exact table is generated above solely for this test; no CASCADE or business data.
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {table}")
