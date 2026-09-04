from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from uuid import uuid4

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import ensure_utc_datetime, recent_day_buckets
from src.schemas.memory import MemoryPoint, MemorySearchRequest, MemoryWriteRequest


class PostgresVectorMemoryStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._backend = "postgres_pgvector"

    @property
    def backend(self) -> str:
        return self._backend

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def refresh_connection_status(self) -> str:
        try:
            await asyncio.to_thread(self._healthcheck_sync)
            return self._backend
        except Exception:
            return f"{self._backend}_unavailable"

    async def write(self, request: MemoryWriteRequest) -> MemoryPoint | None:
        return await asyncio.to_thread(self._write_sync, request)

    async def search(self, request: MemorySearchRequest) -> list[MemoryPoint]:
        return await asyncio.to_thread(self._search_sync, request)

    async def list_points(self, request: MemorySearchRequest) -> list[MemoryPoint]:
        return await asyncio.to_thread(self._list_points_sync, request)

    async def count_documents(self, request: MemorySearchRequest) -> int:
        return await asyncio.to_thread(self._count_documents_sync, request)

    async def count_missing_embeddings(self) -> int:
        return await asyncio.to_thread(self._count_missing_embeddings_sync)

    async def list_missing_embeddings(self, limit: int) -> list[MemoryPoint]:
        return await asyncio.to_thread(self._list_missing_embeddings_sync, limit)

    async def update_embeddings(
        self,
        items: list[tuple[str, list[float], dict]],
    ) -> int:
        return await asyncio.to_thread(self._update_embeddings_sync, items)

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._settings.database.postgres_memory_table} (
                        id TEXT PRIMARY KEY,
                        scope TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        content TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                        session_id TEXT NULL,
                        turn_id TEXT NULL,
                        trace_id TEXT NULL,
                        source TEXT NULL,
                        stale BOOLEAN NOT NULL DEFAULT FALSE,
                        mode_key TEXT NOT NULL DEFAULT 'default',
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding VECTOR({self._settings.database.postgres_vector_dimension}) NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_memory_table}_session_updated "
                    f"ON {self._settings.database.postgres_memory_table} (session_id, updated_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_memory_table}_scope "
                    f"ON {self._settings.database.postgres_memory_table} (scope)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_memory_table}_kind "
                    f"ON {self._settings.database.postgres_memory_table} (kind)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_memory_table}_stale "
                    f"ON {self._settings.database.postgres_memory_table} (stale)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_memory_table}_created "
                    f"ON {self._settings.database.postgres_memory_table} (created_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_memory_table}_tags "
                    f"ON {self._settings.database.postgres_memory_table} USING GIN (tags)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_memory_table}_metadata "
                    f"ON {self._settings.database.postgres_memory_table} USING GIN (metadata)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._settings.database.postgres_memory_table}_embedding "
                    f"ON {self._settings.database.postgres_memory_table} USING hnsw (embedding vector_cosine_ops)"
                )

    def _healthcheck_sync(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

    def _write_sync(self, request: MemoryWriteRequest) -> MemoryPoint | None:
        now = datetime.utcnow()
        point_id = str(
            request.metadata.get("memory_id") or request.metadata.get("id") or uuid4()
        )
        stored_metadata = dict(request.metadata or {})
        embedding = self._extract_embedding(stored_metadata)
        stored_metadata.pop("embedding", None)
        point = MemoryPoint(
            id=point_id,
            scope=request.scope,
            kind=request.kind,
            content=request.content,
            summary=request.summary,
            tags=request.tags,
            session_id=request.session_id,
            turn_id=request.turn_id,
            trace_id=request.trace_id,
            source=request.source,
            stale=request.stale,
            created_at=now,
            updated_at=now,
            metadata=stored_metadata,
        )
        mode_key = str(stored_metadata.get("mode_key") or "default")
        metadata_json = json.dumps(stored_metadata, ensure_ascii=False)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._settings.database.postgres_memory_table} (
                        id, scope, kind, content, summary, tags, session_id, turn_id, trace_id,
                        source, stale, mode_key, metadata, embedding, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s::jsonb, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        scope = EXCLUDED.scope,
                        kind = EXCLUDED.kind,
                        content = EXCLUDED.content,
                        summary = EXCLUDED.summary,
                        tags = EXCLUDED.tags,
                        session_id = EXCLUDED.session_id,
                        turn_id = EXCLUDED.turn_id,
                        trace_id = EXCLUDED.trace_id,
                        source = EXCLUDED.source,
                        stale = EXCLUDED.stale,
                        mode_key = EXCLUDED.mode_key,
                        metadata = EXCLUDED.metadata,
                        embedding = COALESCE(EXCLUDED.embedding, {self._settings.database.postgres_memory_table}.embedding),
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        point.id,
                        point.scope,
                        point.kind,
                        point.content,
                        point.summary,
                        point.tags,
                        point.session_id,
                        point.turn_id,
                        point.trace_id,
                        point.source,
                        point.stale,
                        mode_key,
                        metadata_json,
                        embedding,
                        point.created_at,
                        point.updated_at,
                    ),
                )
        return point

    def _search_sync(self, request: MemorySearchRequest) -> list[MemoryPoint]:
        rows = self._select_rows(request, limit=max(request.top_k * 8, 40))
        tokens = [token for token in re.split(r"\W+", request.query.lower()) if token]
        query_embedding = self._extract_query_embedding(request)
        hits: list[MemoryPoint] = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if not _match_metadata_filters(metadata, request.metadata_filters):
                continue
            score = _score_document(row, request.query, tokens)
            if query_embedding and row.get("vector_similarity") is not None:
                score += max(float(row["vector_similarity"]), 0.0) * 8.0
            if score <= 0:
                continue
            hits.append(
                MemoryPoint(
                    id=row["id"],
                    scope=row.get("scope", "session"),
                    kind=row.get("kind", "episodic"),
                    content=row.get("content") or "",
                    summary=row.get("summary") or "",
                    tags=list(row.get("tags") or []),
                    score=score,
                    session_id=row.get("session_id"),
                    turn_id=row.get("turn_id"),
                    trace_id=row.get("trace_id"),
                    source=row.get("source"),
                    stale=bool(row.get("stale")),
                    created_at=ensure_utc_datetime(row.get("created_at"))
                    or datetime.utcnow(),
                    updated_at=ensure_utc_datetime(row.get("updated_at"))
                    or datetime.utcnow(),
                    metadata=metadata,
                )
            )
        hits.sort(key=lambda item: (item.score or 0.0, item.updated_at), reverse=True)
        return hits[: request.top_k]

    def _list_points_sync(self, request: MemorySearchRequest) -> list[MemoryPoint]:
        rows = self._select_rows(request, limit=max(request.top_k, 1))
        points: list[MemoryPoint] = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if not _match_metadata_filters(metadata, request.metadata_filters):
                continue
            points.append(
                MemoryPoint(
                    id=row["id"],
                    scope=row.get("scope", "session"),
                    kind=row.get("kind", "episodic"),
                    content=row.get("content") or "",
                    summary=row.get("summary") or "",
                    tags=list(row.get("tags") or []),
                    score=None,
                    session_id=row.get("session_id"),
                    turn_id=row.get("turn_id"),
                    trace_id=row.get("trace_id"),
                    source=row.get("source"),
                    stale=bool(row.get("stale")),
                    created_at=ensure_utc_datetime(row.get("created_at"))
                    or datetime.utcnow(),
                    updated_at=ensure_utc_datetime(row.get("updated_at"))
                    or datetime.utcnow(),
                    metadata=metadata,
                )
            )
        return points

    def _count_documents_sync(self, request: MemorySearchRequest) -> int:
        where_clause, params = self._build_where_clause(request)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS total FROM {self._settings.database.postgres_memory_table} {where_clause}",
                    params,
                )
                row = cur.fetchone()
        return int((row or {}).get("total") or 0)

    def _count_missing_embeddings_sync(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS total FROM {self._settings.database.postgres_memory_table} "
                    "WHERE embedding IS NULL"
                )
                row = cur.fetchone()
        return int((row or {}).get("total") or 0)

    def _list_missing_embeddings_sync(self, limit: int) -> list[MemoryPoint]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, scope, kind, content, summary, tags, session_id, turn_id,
                           trace_id, source, stale, metadata, created_at, updated_at
                    FROM {self._settings.database.postgres_memory_table}
                    WHERE embedding IS NULL
                    ORDER BY created_at ASC, id ASC
                    LIMIT %s
                    """,
                    (max(int(limit), 1),),
                )
                rows = list(cur.fetchall() or [])
        return [
            MemoryPoint(
                id=row["id"],
                scope=row.get("scope", "session"),
                kind=row.get("kind", "episodic"),
                content=row.get("content") or "",
                summary=row.get("summary") or "",
                tags=list(row.get("tags") or []),
                session_id=row.get("session_id"),
                turn_id=row.get("turn_id"),
                trace_id=row.get("trace_id"),
                source=row.get("source"),
                stale=bool(row.get("stale")),
                created_at=ensure_utc_datetime(row.get("created_at"))
                or datetime.utcnow(),
                updated_at=ensure_utc_datetime(row.get("updated_at"))
                or datetime.utcnow(),
                metadata=row.get("metadata") or {},
            )
            for row in rows
        ]

    def _update_embeddings_sync(
        self,
        items: list[tuple[str, list[float], dict]],
    ) -> int:
        if not items:
            return 0
        values = [
            (
                self._serialize_embedding(embedding),
                json.dumps(metadata, ensure_ascii=False),
                point_id,
            )
            for point_id, embedding, metadata in items
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f"""
                    UPDATE {self._settings.database.postgres_memory_table}
                    SET embedding=%s::vector,
                        metadata=metadata || %s::jsonb
                    WHERE id=%s AND embedding IS NULL
                    """,
                    values,
                )
                updated = int(cur.rowcount or 0)
        return updated

    def _select_rows(self, request: MemorySearchRequest, limit: int) -> list[dict]:
        where_clause, params = self._build_where_clause(request)
        query_embedding = self._extract_query_embedding(request)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if query_embedding:
                    serialized_embedding = self._serialize_embedding(query_embedding)
                    vector_where = self._append_where_condition(
                        where_clause,
                        "embedding IS NOT NULL",
                    )
                    cur.execute(
                        f"""
                        SELECT id, scope, kind, content, summary, tags, session_id, turn_id, trace_id,
                               source, stale, metadata, created_at, updated_at,
                               1 - (embedding <=> %s::vector) AS vector_similarity
                        FROM {self._settings.database.postgres_memory_table}
                        {vector_where}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        [serialized_embedding, *params, serialized_embedding, limit],
                    )
                    vector_rows = list(cur.fetchall() or [])
                    fallback_where = self._append_where_condition(
                        where_clause,
                        "embedding IS NULL",
                    )
                    cur.execute(
                        f"""
                        SELECT id, scope, kind, content, summary, tags, session_id, turn_id, trace_id,
                               source, stale, metadata, created_at, updated_at,
                               NULL::double precision AS vector_similarity
                        FROM {self._settings.database.postgres_memory_table}
                        {fallback_where}
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """,
                        [*params, limit],
                    )
                    fallback_rows = list(cur.fetchall() or [])
                    return vector_rows + fallback_rows
                cur.execute(
                    f"""
                    SELECT id, scope, kind, content, summary, tags, session_id, turn_id, trace_id,
                           source, stale, metadata, created_at, updated_at,
                           NULL::double precision AS vector_similarity
                    FROM {self._settings.database.postgres_memory_table}
                    {where_clause}
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    [*params, limit],
                )
                rows = cur.fetchall()
        return list(rows or [])

    @staticmethod
    def _append_where_condition(where_clause: str, condition: str) -> str:
        if where_clause:
            return f"{where_clause} AND {condition}"
        return f"WHERE {condition}"

    def _build_where_clause(self, request: MemorySearchRequest) -> tuple[str, list]:
        conditions: list[str] = []
        params: list = []
        day_buckets = (
            recent_day_buckets(request.day_window) if request.day_window > 0 else []
        )
        if day_buckets:
            conditions.append(
                "to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD') = ANY(%s)"
            )
            params.append(day_buckets)
        if request.session_id is not None:
            conditions.append("session_id = %s")
            params.append(request.session_id)
        scopes = request.scopes or (
            [request.scope] if request.scope is not None else []
        )
        if scopes:
            conditions.append("scope = ANY(%s)")
            params.append(scopes)
        if request.kinds:
            conditions.append("kind = ANY(%s)")
            params.append(request.kinds)
        if request.tags:
            conditions.append("tags && %s")
            params.append(request.tags)
        if not request.include_stale:
            conditions.append("stale != TRUE")
        for key, value in request.metadata_filters.items():
            if str(key).startswith("__"):
                continue
            conditions.append("metadata ->> %s = %s")
            params.extend([str(key), str(value)])
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return where_clause, params

    def _extract_embedding(self, metadata: dict) -> str | None:
        raw = metadata.get("embedding")
        if not isinstance(raw, list) or not raw:
            return None
        values: list[str] = []
        for item in raw:
            try:
                values.append(str(float(item)))
            except (TypeError, ValueError):
                return None
        return "[" + ",".join(values) + "]"

    @staticmethod
    def _serialize_embedding(embedding: list[float]) -> str:
        return "[" + ",".join(str(float(item)) for item in embedding) + "]"

    def _extract_query_embedding(
        self, request: MemorySearchRequest
    ) -> list[float] | None:
        raw = request.metadata_filters.get("__query_embedding")
        if not isinstance(raw, list) or not raw:
            return None
        try:
            return [float(item) for item in raw]
        except (TypeError, ValueError):
            return None

    def _connect(self):
        return postgres_connect(self._settings)


def _match_metadata_filters(metadata: dict, filters: dict) -> bool:
    for key, value in filters.items():
        if str(key).startswith("__"):
            continue
        if metadata.get(key) != value:
            return False
    return True


def _score_document(document: dict, raw_query: str, tokens: list[str]) -> float:
    summary_text = str(document.get("summary") or "")
    content_text = str(document.get("content") or "")
    query = raw_query.strip().lower()
    haystacks = [
        summary_text.lower(),
        content_text.lower(),
        str(document.get("source") or "").lower(),
        " ".join(str(item).lower() for item in document.get("tags") or []),
    ]
    score = 0.0
    if query:
        for haystack in haystacks:
            if query in haystack:
                score += 6.0
    for token in tokens:
        for index, haystack in enumerate(haystacks):
            occurrences = haystack.count(token)
            if occurrences <= 0:
                continue
            if index == 0:
                score += occurrences * 3.0
            elif index == 1:
                score += occurrences * 2.0
            else:
                score += occurrences * 1.0
    return score
