from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx

from src.core.config import Settings
from src.schemas.memory import MemoryPoint, MemorySearchRequest, MemoryWriteRequest


class QdrantMemoryStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.qdrant_url.rstrip("/")
        self._collection = settings.qdrant_collection
        self._headers = {"Content-Type": "application/json"}
        if settings.qdrant_api_key:
            self._headers["api-key"] = settings.qdrant_api_key
        self._available = bool(settings.qdrant_enabled and self._base_url)
        self._backend = "qdrant" if self._available else "local_memory"
        self._vector_size = settings.embedding_vector_size
        self._fallback_points: dict[str, tuple[list[float], MemoryPoint]] = {}

    async def initialize(self) -> None:
        if not self._available:
            self._backend = "local_memory"
            return
        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                response = await client.get(
                    f"{self._base_url}/collections/{self._collection}",
                    headers=self._headers,
                )
                if response.status_code == 404:
                    payload = {
                        "vectors": {
                            "size": self._settings.embedding_vector_size,
                            "distance": self._settings.qdrant_distance,
                        }
                    }
                    create_response = await client.put(
                        f"{self._base_url}/collections/{self._collection}",
                        headers=self._headers,
                        json=payload,
                    )
                    create_response.raise_for_status()
                    self._vector_size = self._settings.embedding_vector_size
                else:
                    response.raise_for_status()
                    config = ((response.json() or {}).get("result") or {}).get("config") or {}
                    params = config.get("params") or {}
                    vectors = params.get("vectors") or {}
                    remote_size = int(vectors.get("size") or self._settings.embedding_vector_size)
                    self._vector_size = remote_size
        except httpx.HTTPError:
            self._available = False
            self._backend = "local_memory"

    @property
    def backend(self) -> str:
        return self._backend

    async def write(
        self,
        request: MemoryWriteRequest,
        vector: list[float],
    ) -> MemoryPoint | None:
        now = datetime.utcnow()
        point = MemoryPoint(
            id=str(uuid4()),
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
        if not self._available:
            self._fallback_points[point.id] = (vector, point)
            return point
        payload = {
            "points": [
                {
                    "id": point.id,
                    "vector": self._fit_vector(vector),
                    "payload": point.model_dump(mode="json"),
                }
            ]
        }
        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                response = await client.put(
                    f"{self._base_url}/collections/{self._collection}/points",
                    headers=self._headers,
                    json=payload,
                    params={"wait": "true"},
                )
                response.raise_for_status()
        except httpx.HTTPError:
            self._available = False
            self._backend = "local_memory"
            self._fallback_points[point.id] = (vector, point)
        return point

    async def search(
        self,
        request: MemorySearchRequest,
        vector: list[float],
    ) -> list[MemoryPoint]:
        if not self._available:
            return self._search_fallback(request, vector)

        payload: dict[str, Any] = {
            "vector": self._fit_vector(vector),
            "limit": request.top_k,
            "with_payload": True,
            "with_vector": False,
        }
        filter_payload = self._build_filter(request)
        if filter_payload:
            payload["filter"] = filter_payload

        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/collections/{self._collection}/points/search",
                    headers=self._headers,
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError:
            self._available = False
            self._backend = "local_memory"
            return self._search_fallback(request, vector)

        results = (response.json() or {}).get("result") or []
        hits: list[MemoryPoint] = []
        for item in results:
            payload_data = item.get("payload") or {}
            if not isinstance(payload_data, dict):
                continue
            payload_data["score"] = float(item.get("score") or 0.0)
            hits.append(MemoryPoint.model_validate(payload_data))
        return hits

    def _build_filter(self, request: MemorySearchRequest) -> dict[str, Any]:
        must: list[dict[str, Any]] = []
        if request.session_id:
            must.append({"key": "session_id", "match": {"value": request.session_id}})
        if request.scope:
            must.append({"key": "scope", "match": {"value": request.scope}})
        if request.kinds:
            must.append({"key": "kind", "match": {"any": request.kinds}})
        if request.tags:
            must.append({"key": "tags", "match": {"any": request.tags}})
        for key, value in request.metadata_filters.items():
            must.append({"key": f"metadata.{key}", "match": {"value": value}})

        must_not: list[dict[str, Any]] = []
        if not request.include_stale:
            must_not.append({"key": "stale", "match": {"value": True}})

        filter_payload: dict[str, Any] = {}
        if must:
            filter_payload["must"] = must
        if must_not:
            filter_payload["must_not"] = must_not
        return filter_payload

    def _search_fallback(
        self,
        request: MemorySearchRequest,
        vector: list[float],
    ) -> list[MemoryPoint]:
        fitted_vector = self._fit_vector(vector)
        hits: list[MemoryPoint] = []
        for stored_vector, point in self._fallback_points.values():
            if request.session_id and point.session_id != request.session_id:
                continue
            if request.scope and point.scope != request.scope:
                continue
            if request.kinds and point.kind not in request.kinds:
                continue
            if request.tags and not set(request.tags).intersection(point.tags):
                continue
            if not request.include_stale and point.stale:
                continue
            score = _dot(fitted_vector, stored_vector)
            hits.append(point.model_copy(update={"score": score}))
        hits.sort(key=lambda item: item.score or 0.0, reverse=True)
        return hits[: request.top_k]

    def _fit_vector(self, vector: list[float]) -> list[float]:
        if len(vector) == self._vector_size:
            return vector
        if len(vector) > self._vector_size:
            return vector[: self._vector_size]
        return [*vector, *([0.0] * (self._vector_size - len(vector)))]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
