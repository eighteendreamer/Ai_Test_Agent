"""Opt-in PostgreSQL + Redis acceptance for 4096-dimensional test-case vectors."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.context.embedding_runtime_service import EmbeddingBatchResult
from src.application.context.memory_vector_rebuild_service import (
    MemoryVectorRebuildService,
)
from src.application.test_cases.embedding_service import (
    TestCaseEmbeddingService as EmbeddingService,
)
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_test_case_embedding_store import (
    PostgresTestCaseEmbeddingStore as EmbeddingStore,
    TestCaseEmbeddingWrite as EmbeddingWrite,
)
from src.infrastructure.redis_vector_store import RedisVectorStore
from src.schemas.case_management import (
    TestCaseRecord as CaseRecord,
    TestCaseVersionRecord as CaseVersionRecord,
)


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TEST_CASE_EMBEDDING_TESTS") != "1",
    reason="set RUN_LIVE_TEST_CASE_EMBEDDING_TESTS=1 for PostgreSQL/Redis acceptance",
)
@pytest.mark.asyncio
async def test_live_4096_dimension_test_case_vector_pipeline() -> None:
    suffix = uuid4().hex[:12]
    case_table = f"test_cases_{suffix}"
    version_table = f"test_case_versions_{suffix}"
    embedding_table = f"test_case_embeddings_{suffix}"
    embedding_version = f"emb_{suffix}"
    case_id = str(uuid4())
    version_id = str(uuid4())
    project_id = str(uuid4())
    now = datetime.now(timezone.utc)
    case = CaseRecord(
        id=case_id,
        project_id=project_id,
        case_key="login-valid",
        title="Valid login",
        mode_key="api_testing",
        case_type="api",
        lifecycle_status="active",
        active_version_id=version_id,
        created_at=now,
        updated_at=now,
    )
    version = CaseVersionRecord(
        id=version_id,
        case_id=case_id,
        version=1,
        steps=[{"order": 1, "action": "Submit credentials", "expected": "200"}],
        assertions=[{"kind": "status_code", "expected": 200}],
        test_data={"environment": "qa"},
        content_hash="a" * 64,
        source_refs=[{"source_type": "api_doc", "source_id": "doc-1"}],
        model_key="generator-1",
        prompt_version="v1",
        skill_versions={"generate": "v1"},
        created_at=now,
    )
    base = get_settings()
    settings = base.model_copy(
        update={
            "database": base.database.model_copy(
                update={
                    "postgres_test_case_table": case_table,
                    "postgres_test_case_version_table": version_table,
                    "postgres_test_case_embedding_table": embedding_table,
                }
            )
        }
    )

    class IsolatedRedis(RedisVectorStore):
        def index_name(self, entity, version):
            return f"qa:test:{suffix}:index:{entity}:{version}"

        def prefix(self, entity, version):
            return f"qa:test:{suffix}:doc:{entity}:{version}:"

        def active_key(self, entity):
            return f"qa:test:{suffix}:active:{entity}"

    vector = [1.0, *([0.0] * 4095)]
    store = EmbeddingStore(settings)
    redis = IsolatedRedis(settings.database.redis_url)
    index_name = redis.index_name("test_case", embedding_version)
    try:
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE {case_table} (
                        id UUID PRIMARY KEY, project_id UUID NOT NULL,
                        lifecycle_status TEXT NOT NULL, active_version_id UUID,
                        record JSONB NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE {version_table} (
                        id UUID PRIMARY KEY, case_id UUID NOT NULL,
                        content_hash TEXT NOT NULL, record JSONB NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"INSERT INTO {case_table} VALUES (%s, %s, 'active', %s, %s::jsonb)",
                    (
                        case_id,
                        project_id,
                        version_id,
                        json.dumps(case.model_dump(mode="json")),
                    ),
                )
                cur.execute(
                    f"INSERT INTO {version_table} VALUES (%s, %s, %s, %s::jsonb)",
                    (
                        version_id,
                        case_id,
                        version.content_hash,
                        json.dumps(version.model_dump(mode="json")),
                    ),
                )
        await store.initialize()
        await store.upsert(
            EmbeddingWrite(
                case_id=case_id,
                case_version_id=version_id,
                project_id=project_id,
                content="valid login",
                content_hash=version.content_hash,
                embedding=vector,
                embedding_model_key="embedding-key",
                embedding_provider="openai_compatible",
                embedding_model_id="Qwen3-VL-Embedding-8B",
                embedding_version=embedding_version,
                embedding_dimension=4096,
                environment="qa",
                mode_key="api_testing",
            )
        )

        hits = await store.search(
            project_id=project_id,
            query_vector=vector,
            embedding_version=embedding_version,
            top_k=1,
            candidate_multiplier=2,
            environment="qa",
            mode_key="api_testing",
        )
        assert len(hits) == 1 and hits[0].version.id == version_id
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT indexdef FROM pg_indexes WHERE tablename = %s "
                    "AND position('binary_quantize' in indexdef) > 0",
                    (embedding_table,),
                )
                index_row = cur.fetchone()
        assert index_row is not None and "bit(4096)" in index_row["indexdef"]

        await redis.connect()
        rebuild = MemoryVectorRebuildService(
            store,
            redis,
            entity="test_case",
            batch_size=1,
            validation_timeout_seconds=5,
            validation_poll_interval_seconds=0.05,
        )
        rebuilt = await rebuild.rebuild(
            embedding_version,
            execute=True,
            activate=True,
        )
        assert rebuilt.dimension == 4096
        assert rebuilt.replicated == 1 and rebuilt.status == "activated"

        runtime = SimpleNamespace(
            embed_texts=AsyncMock(
                return_value=EmbeddingBatchResult(
                    vectors=[vector],
                    model_name="Embedding",
                    provider="openai_compatible",
                    adapter="openai",
                    original_dimension=4096,
                    stored_dimension=4096,
                    latency_ms=1,
                    embedding_version=embedding_version,
                )
            )
        )
        service = EmbeddingService(
            case_store=SimpleNamespace(),
            embedding_store=store,
            embedding_runtime=runtime,
            redis_vector_store=redis,
            top_k=1,
        )
        result = await service.retrieve(
            project_id=project_id,
            query="valid login",
            environment="qa",
            mode_key="api_testing",
            top_k=1,
        )
        assert len(result.hits) == 1
        assert result.hits[0].case.project_id == project_id
    finally:
        if redis._client is not None:
            await redis.deactivate_index(entity="test_case")
            indexes = set(await redis._client.execute_command("FT._LIST"))
            if index_name in indexes or index_name.encode() in indexes:
                await redis._client.execute_command("FT.DROPINDEX", index_name, "DD")
            await redis.close()
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {embedding_table}")
                cur.execute(f"DROP TABLE IF EXISTS {version_table}")
                cur.execute(f"DROP TABLE IF EXISTS {case_table}")
