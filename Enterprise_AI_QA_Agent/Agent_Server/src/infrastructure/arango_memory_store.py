from __future__ import annotations

import re
from datetime import datetime
from uuid import uuid4

from src.core.config import Settings
from src.infrastructure.arango_runtime import (
    ArangoRuntimeProvider,
    day_bucket,
    ensure_utc_datetime,
    make_json_safe,
    recent_day_buckets,
    serialize_datetime,
)
from src.schemas.memory import MemoryPoint, MemorySearchRequest, MemoryWriteRequest


class ArangoDocumentMemoryStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = ArangoRuntimeProvider(settings)
        self._backend = "arangodb"

    async def initialize(self) -> None:
        self._provider.initialize()

    async def refresh_connection_status(self) -> str:
        return "arangodb" if self._provider.is_available() else "arangodb_unavailable"

    @property
    def backend(self) -> str:
        return self._backend

    async def write(self, request: MemoryWriteRequest) -> MemoryPoint | None:
        now = datetime.utcnow()
        point_id = str(request.metadata.get("memory_id") or request.metadata.get("id") or uuid4())
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
            metadata=request.metadata,
        )
        document = {
            "_key": point.id,
            "id": point.id,
            "scope": point.scope,
            "kind": point.kind,
            "content": point.content,
            "summary": point.summary,
            "tags": point.tags,
            "session_id": point.session_id,
            "turn_id": point.turn_id,
            "trace_id": point.trace_id,
            "source": point.source,
            "stale": point.stale,
            "created_at": serialize_datetime(point.created_at),
            "updated_at": serialize_datetime(point.updated_at),
            "day_bucket": day_bucket(point.created_at),
            "day_bucket_tz": self._settings.arango_timezone,
            "metadata": make_json_safe(point.metadata),
        }
        collection = self._provider.collection(self._settings.arango_memory_collection)
        if collection.has(point.id):
            collection.replace(document)
        else:
            collection.insert(document)
        return point

    async def search(self, request: MemorySearchRequest) -> list[MemoryPoint]:
        day_buckets = recent_day_buckets(request.day_window) if request.day_window > 0 else []
        scopes = request.scopes or ([request.scope] if request.scope is not None else [])
        bind_vars = {
            "@collection": self._settings.arango_memory_collection,
            "day_buckets": day_buckets,
            "session_id": request.session_id,
            "scope": request.scope,
            "scopes": scopes,
            "kinds": request.kinds,
            "tags": request.tags,
            "include_stale": request.include_stale,
            "limit": max(request.top_k * 8, 40),
        }
        rows = self._provider.execute(
            """
            FOR doc IN @@collection
                FILTER LENGTH(@day_buckets) == 0 OR doc.day_bucket IN @day_buckets
                FILTER @session_id == null OR doc.session_id == @session_id
                FILTER LENGTH(@scopes) == 0 OR doc.scope IN @scopes
                FILTER @scope == null OR doc.scope == @scope
                FILTER LENGTH(@kinds) == 0 OR doc.kind IN @kinds
                FILTER LENGTH(@tags) == 0 OR LENGTH(INTERSECTION(doc.tags, @tags)) > 0
                FILTER @include_stale == true OR doc.stale != true
                SORT doc.updated_at DESC
                LIMIT @limit
                RETURN doc
            """,
            bind_vars=bind_vars,
        )
        tokens = [token for token in re.split(r"\W+", request.query.lower()) if token]
        hits: list[MemoryPoint] = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if not _match_metadata_filters(metadata, request.metadata_filters):
                continue
            score = _score_document(row, request.query, tokens)
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
                    created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
                    updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
                    metadata=metadata,
                )
            )
        hits.sort(key=lambda item: (item.score or 0.0, item.updated_at), reverse=True)
        return hits[: request.top_k]

    async def list_points(self, request: MemorySearchRequest) -> list[MemoryPoint]:
        day_buckets = recent_day_buckets(request.day_window) if request.day_window > 0 else []
        scopes = request.scopes or ([request.scope] if request.scope is not None else [])
        bind_vars = {
            "@collection": self._settings.arango_memory_collection,
            "day_buckets": day_buckets,
            "session_id": request.session_id,
            "scope": request.scope,
            "scopes": scopes,
            "kinds": request.kinds,
            "tags": request.tags,
            "include_stale": request.include_stale,
            "limit": max(request.top_k, 1),
        }
        rows = self._provider.execute(
            """
            FOR doc IN @@collection
                FILTER LENGTH(@day_buckets) == 0 OR doc.day_bucket IN @day_buckets
                FILTER @session_id == null OR doc.session_id == @session_id
                FILTER LENGTH(@scopes) == 0 OR doc.scope IN @scopes
                FILTER @scope == null OR doc.scope == @scope
                FILTER LENGTH(@kinds) == 0 OR doc.kind IN @kinds
                FILTER LENGTH(@tags) == 0 OR LENGTH(INTERSECTION(doc.tags, @tags)) > 0
                FILTER @include_stale == true OR doc.stale != true
                SORT doc.updated_at DESC
                LIMIT @limit
                RETURN doc
            """,
            bind_vars=bind_vars,
        )
        return [
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
                created_at=ensure_utc_datetime(row["created_at"]) or datetime.utcnow(),
                updated_at=ensure_utc_datetime(row["updated_at"]) or datetime.utcnow(),
                metadata=row.get("metadata") or {},
            )
            for row in rows
            if _match_metadata_filters(row.get("metadata") or {}, request.metadata_filters)
        ]

    async def count_documents(self, request: MemorySearchRequest) -> int:
        day_buckets = recent_day_buckets(request.day_window) if request.day_window > 0 else []
        scopes = request.scopes or ([request.scope] if request.scope is not None else [])
        bind_vars = {
            "@collection": self._settings.arango_memory_collection,
            "day_buckets": day_buckets,
            "session_id": request.session_id,
            "scope": request.scope,
            "scopes": scopes,
            "kinds": request.kinds,
            "tags": request.tags,
            "include_stale": request.include_stale,
        }
        rows = self._provider.execute(
            """
            RETURN LENGTH(
                FOR doc IN @@collection
                    FILTER LENGTH(@day_buckets) == 0 OR doc.day_bucket IN @day_buckets
                    FILTER @session_id == null OR doc.session_id == @session_id
                    FILTER LENGTH(@scopes) == 0 OR doc.scope IN @scopes
                    FILTER @scope == null OR doc.scope == @scope
                    FILTER LENGTH(@kinds) == 0 OR doc.kind IN @kinds
                    FILTER LENGTH(@tags) == 0 OR LENGTH(INTERSECTION(doc.tags, @tags)) > 0
                    FILTER @include_stale == true OR doc.stale != true
                    RETURN 1
            )
            """,
            bind_vars=bind_vars,
        )
        return int(rows[0] or 0) if rows else 0


def _match_metadata_filters(metadata: dict, filters: dict) -> bool:
    for key, value in filters.items():
        if metadata.get(key) != value:
            return False
    return True


def _score_document(document: dict, raw_query: str, tokens: list[str]) -> float:
    summary_text = str(document.get("summary") or "")
    content_text = str(document.get("content") or "")
    if "Model invocation failed for '" in summary_text or "Model invocation failed for '" in content_text:
        return 0.0

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
