from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.infrastructure.redis_vector_store import RedisVectorStore
from src.schemas.memory import MemoryPoint, MemorySearchRequest


def point(identifier="stored"):
    now = datetime.now(timezone.utc)
    return MemoryPoint(id=identifier, content="PostgreSQL authoritative text", created_at=now, updated_at=now)


def harness(candidates=None, hits=None):
    store = SimpleNamespace(
        search=AsyncMock(return_value=[point("fallback")]),
        search_candidates=AsyncMock(return_value=[point()] if hits is None else hits),
    )
    vector = SimpleNamespace(
        search=AsyncMock(return_value=candidates if candidates is not None else [{
            "id": RedisVectorStore.prefix("memory", "v1") + "stored",
            "content": "Untrusted Redis text",
        }]),
        prefix=RedisVectorStore.prefix,
    )
    service = MemoryRuntimeService(store, vector_store=vector)
    request = MemorySearchRequest(
        query="login", session_id="session-a", scopes=["session"], kinds=["observation"],
        top_k=1, tags=["browser"], day_window=1,
        metadata_filters={"project_id": "p-a", "environment": "qa", "mode_key": "ui_testing",
                          "__query_embedding": [1.0, 0.0], "__embedding_version": "v1"},
    )
    return service, store, vector, request


@pytest.mark.asyncio
async def test_redis_only_provides_ids_and_all_filters_survive_hydration():
    service, store, vector, request = harness()
    result = await service._search_memory(request)
    assert result[0].content == "PostgreSQL authoritative text"
    hydrated, ids = store.search_candidates.call_args.args
    assert ids == ["stored"]
    assert hydrated.session_id == "session-a"
    assert hydrated.scopes == ["session"] and hydrated.kinds == ["observation"]
    assert hydrated.tags == ["browser"] and not hydrated.include_stale
    assert hydrated.metadata_filters["project_id"] == "p-a"
    assert hydrated.metadata_filters["embedding_version"] == "v1"
    assert hydrated.metadata_filters["embedding_dimension"] == 2
    assert "embedding_version" not in request.metadata_filters  # no mutation
    assert vector.search.call_args.kwargs["filters"] == {"project_id": "p-a", "environment": "qa", "status": "active"}
    store.search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("candidates", [[], [{"id": "another-index:stored"}], [{"id": 123}]])
async def test_empty_or_foreign_candidates_fall_back(candidates):
    service, store, _, request = harness(candidates=candidates, hits=[])
    assert (await service._search_memory(request))[0].id == "fallback"
    assert store.search_candidates.call_args.args[1] == []
    store.search.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_redis_outage_falls_back_and_logs_without_query(caplog):
    service, store, vector, request = harness()
    vector.search.side_effect = ConnectionError("private query value")
    assert (await service._search_memory(request))[0].id == "fallback"
    assert "memory_vector_retrieval_fallback" in caplog.text
    assert "private query value" not in caplog.text
    store.search_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_stale_candidates_fall_back():
    service, store, _, request = harness(hits=[])
    assert (await service._search_memory(request))[0].id == "fallback"
    store.search.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_database_failure_is_not_misreported_as_redis_failure():
    service, store, _, request = harness()
    store.search_candidates.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service._search_memory(request)
    store.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_embedding_version_skips_redis():
    service, store, vector, request = harness()
    request.metadata_filters.pop("__embedding_version")
    await service._search_memory(request)
    vector.search.assert_not_awaited()
    store.search.assert_awaited_once_with(request)
