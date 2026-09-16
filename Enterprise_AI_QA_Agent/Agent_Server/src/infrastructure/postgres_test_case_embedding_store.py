"""PostgreSQL source-of-truth projection for active test-case embeddings."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_management import TestCaseRecord, TestCaseVersionRecord


_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


@dataclass(frozen=True)
class TestCaseEmbeddingWrite:
    case_id: str
    case_version_id: str
    project_id: str
    content: str
    content_hash: str
    embedding: list[float]
    embedding_model_key: str
    embedding_provider: str
    embedding_model_id: str
    embedding_version: str
    embedding_dimension: int
    distance_metric: str = "cosine"
    normalized: bool = True
    environment: str = "any"
    mode_key: str = "default"


@dataclass(frozen=True)
class TestCaseVectorHit:
    case: TestCaseRecord
    version: TestCaseVersionRecord
    score: float
    embedding_version: str
    embedding_dimension: int
    environment: str


@dataclass(frozen=True)
class TestCaseVectorRecord:
    id: str
    embedding: list[float]
    metadata: dict[str, Any]


class PostgresTestCaseEmbeddingStore:
    """Stores complete vectors and uses dimension-safe ANN expressions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._table = settings.database.postgres_test_case_embedding_table
        self._case_table = settings.database.postgres_test_case_table
        self._version_table = settings.database.postgres_test_case_version_table

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def upsert(self, record: TestCaseEmbeddingWrite) -> None:
        await asyncio.to_thread(self._upsert_sync, record)

    async def search(
        self,
        *,
        project_id: str,
        query_vector: list[float],
        embedding_version: str,
        top_k: int,
        candidate_multiplier: int,
        environment: str | None = None,
        mode_key: str | None = None,
    ) -> list[TestCaseVectorHit]:
        return await asyncio.to_thread(
            self._search_sync,
            project_id,
            query_vector,
            embedding_version,
            top_k,
            candidate_multiplier,
            environment,
            mode_key,
            None,
        )

    async def search_candidates(
        self,
        *,
        project_id: str,
        query_vector: list[float],
        embedding_version: str,
        case_version_ids: list[str],
        top_k: int,
        environment: str | None = None,
        mode_key: str | None = None,
    ) -> list[TestCaseVectorHit]:
        if not case_version_ids:
            return []
        return await asyncio.to_thread(
            self._search_sync,
            project_id,
            query_vector,
            embedding_version,
            top_k,
            1,
            environment,
            mode_key,
            list(dict.fromkeys(case_version_ids)),
        )

    async def vector_inventory(self, embedding_version: str) -> dict[int, int]:
        return await asyncio.to_thread(self._vector_inventory_sync, embedding_version)

    async def list_vector_records(
        self,
        embedding_version: str,
        *,
        after_id: str | None = None,
        limit: int = 64,
    ) -> list[TestCaseVectorRecord]:
        return await asyncio.to_thread(
            self._list_vector_records_sync,
            embedding_version,
            after_id,
            limit,
        )

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        case_version_id UUID NOT NULL REFERENCES {self._version_table}(id) ON DELETE CASCADE,
                        embedding_version TEXT NOT NULL,
                        case_id UUID NOT NULL REFERENCES {self._case_table}(id) ON DELETE CASCADE,
                        project_id UUID NOT NULL,
                        content TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        embedding_model_key TEXT NOT NULL,
                        embedding_provider TEXT NOT NULL,
                        embedding_model_id TEXT NOT NULL,
                        embedding_dimension INTEGER NOT NULL CHECK (embedding_dimension > 0),
                        distance_metric TEXT NOT NULL CHECK (distance_metric = 'cosine'),
                        normalized BOOLEAN NOT NULL,
                        environment TEXT NOT NULL DEFAULT 'any',
                        mode_key TEXT NOT NULL DEFAULT 'default',
                        embedding VECTOR NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (case_version_id, embedding_version)
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._table}_project_version "
                    f"ON {self._table} (project_id, embedding_version, environment, mode_key)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._table}_case "
                    f"ON {self._table} (case_id, case_version_id)"
                )

    def _upsert_sync(self, record: TestCaseEmbeddingWrite) -> None:
        dimension = self._validate_vector(
            record.embedding,
            expected_dimension=record.embedding_dimension,
        )
        version = self._validate_version(record.embedding_version)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table} (
                        case_version_id, embedding_version, case_id, project_id,
                        content, content_hash, embedding_model_key,
                        embedding_provider, embedding_model_id,
                        embedding_dimension, distance_metric, normalized,
                        environment, mode_key, embedding
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s::vector
                    )
                    ON CONFLICT (case_version_id, embedding_version) DO UPDATE SET
                        case_id = EXCLUDED.case_id,
                        project_id = EXCLUDED.project_id,
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        embedding_model_key = EXCLUDED.embedding_model_key,
                        embedding_provider = EXCLUDED.embedding_provider,
                        embedding_model_id = EXCLUDED.embedding_model_id,
                        embedding_dimension = EXCLUDED.embedding_dimension,
                        distance_metric = EXCLUDED.distance_metric,
                        normalized = EXCLUDED.normalized,
                        environment = EXCLUDED.environment,
                        mode_key = EXCLUDED.mode_key,
                        embedding = EXCLUDED.embedding,
                        updated_at = NOW()
                    """,
                    (
                        record.case_version_id,
                        version,
                        record.case_id,
                        record.project_id,
                        record.content,
                        record.content_hash,
                        record.embedding_model_key,
                        record.embedding_provider,
                        record.embedding_model_id,
                        dimension,
                        record.distance_metric,
                        bool(record.normalized),
                        record.environment or "any",
                        record.mode_key or "default",
                        self._serialize_vector(record.embedding),
                    ),
                )
                self._ensure_ann_index(cur, version, dimension)

    def _search_sync(
        self,
        project_id: str,
        query_vector: list[float],
        embedding_version: str,
        top_k: int,
        candidate_multiplier: int,
        environment: str | None,
        mode_key: str | None,
        case_version_ids: list[str] | None,
    ) -> list[TestCaseVectorHit]:
        dimension = self._validate_vector(query_vector)
        version = self._validate_version(embedding_version)
        limit = max(1, min(int(top_k), 50))
        candidates = max(limit, min(limit * max(1, int(candidate_multiplier)), 1000))
        where = [
            "e.project_id = %s",
            "e.embedding_version = %s",
            "e.embedding_dimension = %s",
            "e.content_hash = v.content_hash",
            "c.lifecycle_status = 'active'",
            "c.active_version_id = e.case_version_id",
            "v.id = e.case_version_id",
        ]
        params: list[Any] = [project_id, version, dimension]
        if environment:
            where.append("e.environment IN (%s, 'any')")
            params.append(environment)
        if mode_key:
            where.append("e.mode_key = %s")
            params.append(mode_key)
        if case_version_ids is not None:
            where.append("e.case_version_id = ANY(%s::uuid[])")
            params.append(case_version_ids)
        predicate = " AND ".join(where)
        vector_text = self._serialize_vector(query_vector)
        candidate_order = self._candidate_order_expression(dimension)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                if case_version_ids is None:
                    cur.execute("SET LOCAL hnsw.iterative_scan = strict_order")
                cur.execute(
                    f"""
                    WITH candidates AS MATERIALIZED (
                        SELECT e.*, c.record AS case_record, v.record AS version_record
                        FROM {self._table} e
                        JOIN {self._case_table} c ON c.id = e.case_id
                        JOIN {self._version_table} v ON v.id = e.case_version_id
                        WHERE {predicate}
                        ORDER BY {candidate_order}
                        LIMIT %s
                    )
                    SELECT case_record, version_record, embedding_version,
                           embedding_dimension, environment,
                           1 - (embedding <=> %s::vector) AS score
                    FROM candidates
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (
                        *params,
                        vector_text,
                        candidates,
                        vector_text,
                        vector_text,
                        limit,
                    ),
                )
                rows = list(cur.fetchall() or [])
        return [self._row_to_hit(row) for row in rows]

    def _vector_inventory_sync(self, embedding_version: str) -> dict[int, int]:
        version = self._validate_version(embedding_version)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT e.embedding_dimension AS dimension, COUNT(*) AS total
                    FROM {self._table} e
                    JOIN {self._case_table} c ON c.id = e.case_id
                    JOIN {self._version_table} v ON v.id = e.case_version_id
                    WHERE e.embedding_version = %s
                      AND e.content_hash = v.content_hash
                      AND c.lifecycle_status = 'active'
                      AND c.active_version_id = e.case_version_id
                    GROUP BY e.embedding_dimension
                    """,
                    (version,),
                )
                return {
                    int(row["dimension"]): int(row["total"])
                    for row in cur.fetchall()
                }

    def _list_vector_records_sync(
        self,
        embedding_version: str,
        after_id: str | None,
        limit: int,
    ) -> list[TestCaseVectorRecord]:
        version = self._validate_version(embedding_version)
        if limit <= 0:
            raise ValueError("Vector export page size must be positive")
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT e.case_version_id::text AS id,
                           e.embedding::text AS embedding,
                           e.project_id::text AS project_id,
                           e.environment, e.embedding_dimension
                    FROM {self._table} e
                    JOIN {self._case_table} c ON c.id = e.case_id
                    JOIN {self._version_table} v ON v.id = e.case_version_id
                    WHERE e.embedding_version = %s
                      AND e.content_hash = v.content_hash
                      AND c.lifecycle_status = 'active'
                      AND c.active_version_id = e.case_version_id
                      AND (%s::text IS NULL OR e.case_version_id::text > %s)
                    ORDER BY e.case_version_id::text
                    LIMIT %s
                    """,
                    (version, after_id, after_id, limit),
                )
                rows = list(cur.fetchall() or [])
        return [
            TestCaseVectorRecord(
                id=str(row["id"]),
                embedding=json.loads(row["embedding"]),
                metadata={
                    "project_id": str(row["project_id"]),
                    "case_version_id": str(row["id"]),
                    "status": "active",
                    "environment": str(row.get("environment") or "any"),
                    "embedding_version": version,
                    "embedding_dimension": int(row["embedding_dimension"]),
                },
            )
            for row in rows
        ]

    def _ensure_ann_index(self, cur, version: str, dimension: int) -> None:
        strategy = self._index_strategy(dimension)
        digest = hashlib.sha256(
            f"{self._table}:{version}:{dimension}:{strategy}".encode("utf-8")
        ).hexdigest()[:20]
        expression, operator = {
            "vector": (
                f"(embedding::vector({dimension}))",
                "vector_cosine_ops",
            ),
            "halfvec": (
                f"(embedding::halfvec({dimension}))",
                "halfvec_cosine_ops",
            ),
            "binary": (
                f"(binary_quantize(embedding)::bit({dimension}))",
                "bit_hamming_ops",
            ),
        }[strategy]
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_tc_embedding_{digest} "
            f"ON {self._table} USING hnsw ({expression} {operator}) "
            f"WHERE embedding_version = '{version}' "
            f"AND embedding_dimension = {dimension}"
        )

    @classmethod
    def _candidate_order_expression(cls, dimension: int) -> str:
        strategy = cls._index_strategy(dimension)
        if strategy == "vector":
            return f"e.embedding::vector({dimension}) <=> %s::vector({dimension})"
        if strategy == "halfvec":
            return f"e.embedding::halfvec({dimension}) <=> %s::halfvec({dimension})"
        return (
            f"binary_quantize(e.embedding)::bit({dimension}) <~> "
            f"binary_quantize(%s::vector)::bit({dimension})"
        )

    @staticmethod
    def _index_strategy(dimension: int) -> str:
        if dimension <= 2000:
            return "vector"
        if dimension <= 4000:
            return "halfvec"
        if dimension <= 16000:
            return "binary"
        raise ValueError("pgvector supports at most 16000 dimensions")

    @staticmethod
    def _validate_version(value: str) -> str:
        version = str(value or "").strip()
        if not _VERSION_PATTERN.fullmatch(version):
            raise ValueError("Embedding version is not a safe index identifier")
        return version

    @classmethod
    def _validate_vector(
        cls,
        vector: list[float],
        *,
        expected_dimension: int | None = None,
    ) -> int:
        if not isinstance(vector, list) or not vector:
            raise ValueError("Embedding vector must be a nonempty list")
        values = [float(value) for value in vector]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Embedding vector contains non-finite values")
        if math.sqrt(sum(value * value for value in values)) <= 0:
            raise ValueError("Embedding vector must not be zero")
        dimension = len(values)
        if expected_dimension is not None and dimension != int(expected_dimension):
            raise ValueError("Embedding dimension does not match vector length")
        cls._index_strategy(dimension)
        return dimension

    @staticmethod
    def _serialize_vector(vector: list[float]) -> str:
        return json.dumps([float(value) for value in vector], separators=(",", ":"))

    @staticmethod
    def _row_to_hit(row: dict[str, Any]) -> TestCaseVectorHit:
        case_value = row["case_record"]
        version_value = row["version_record"]
        return TestCaseVectorHit(
            case=TestCaseRecord.model_validate(
                json.loads(case_value) if isinstance(case_value, str) else case_value
            ),
            version=TestCaseVersionRecord.model_validate(
                json.loads(version_value)
                if isinstance(version_value, str)
                else version_value
            ),
            score=float(row.get("score") or 0.0),
            embedding_version=str(row["embedding_version"]),
            embedding_dimension=int(row["embedding_dimension"]),
            environment=str(row.get("environment") or "any"),
        )
