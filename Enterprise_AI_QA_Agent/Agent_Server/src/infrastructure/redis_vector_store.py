"""Redis RediSearch vector replica with versioned, dynamic dimensions."""

from __future__ import annotations

import re
import struct
from typing import Any

from redis.asyncio import Redis


class RedisVectorStore:
    def __init__(self, redis_url: str, *, client: Redis | Any | None = None) -> None:
        self._redis_url = redis_url
        self._client = client

    async def connect(self) -> None:
        if self._client is None:
            self._client = Redis.from_url(self._redis_url, decode_responses=False)
        await self._client.ping()

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()

    async def create_index(self, *, entity: str, embedding_version: str, dimension: int) -> str:
        dimension = int(dimension)
        if dimension <= 0:
            raise ValueError("Vector index dimension must be greater than zero")
        index = self.index_name(entity, embedding_version)
        prefix = self.prefix(entity, embedding_version)
        try:
            await self._client.execute_command(
                "FT.CREATE", index, "ON", "HASH", "PREFIX", "1", prefix,
                "SCHEMA", "embedding", "VECTOR", "HNSW", "6",
                "TYPE", "FLOAT32", "DIM", str(dimension),
                "DISTANCE_METRIC", "COSINE",
                "project_id", "TAG", "case_version_id", "TAG", "status", "TAG",
                "environment", "TAG",
            )
        except Exception as exc:
            if "Index already exists" not in str(exc):
                raise
        return index

    async def upsert(
        self,
        *,
        entity: str,
        embedding_version: str,
        point_id: str,
        vector: list[float],
        metadata: dict[str, str] | None = None,
    ) -> str:
        if not vector or any(not isinstance(value, (int, float)) for value in vector):
            raise ValueError("Vector must contain numeric values")
        key = f"{self.prefix(entity, embedding_version)}{point_id}"
        fields: dict[str, Any] = {"embedding": struct.pack(f"<{len(vector)}f", *vector)}
        fields.update({name: str(value) for name, value in (metadata or {}).items() if name in {"project_id", "case_version_id", "status", "environment"}})
        await self._client.hset(key, mapping=fields)
        return key

    async def search(
        self,
        *,
        entity: str,
        embedding_version: str,
        vector: list[float],
        top_k: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if not vector:
            raise ValueError("Query vector must not be empty")
        index = self.index_name(entity, embedding_version)
        query_filter = "*"
        if filters:
            clauses = [f"@{name}:{{{self._escape_tag(value)}}}" for name, value in filters.items() if name in {"project_id", "case_version_id", "status", "environment"}]
            query_filter = " ".join(clauses) if clauses else "*"
        query = f"{query_filter}=>[KNN {max(1, int(top_k))} @embedding $query AS vector_distance]"
        raw = await self._client.execute_command(
            "FT.SEARCH", index, query, "PARAMS", "2", "query", struct.pack(f"<{len(vector)}f", *vector),
            "SORTBY", "vector_distance", "RETURN", "5", "vector_distance", "project_id", "case_version_id", "status", "environment", "DIALECT", "2",
        )
        return self._decode_search(raw)

    @staticmethod
    def index_name(entity: str, embedding_version: str) -> str:
        return f"qa:vector:{_safe(entity)}:{_safe(embedding_version)}"

    @staticmethod
    def prefix(entity: str, embedding_version: str) -> str:
        return f"qa:vector:doc:{_safe(entity)}:{_safe(embedding_version)}:"

    @staticmethod
    def _escape_tag(value: str) -> str:
        return re.sub(r"([\\,.<>\\{\\}\[\"':;!@#$%^&*()\-+=~| ])", r"\\\1", str(value))

    @staticmethod
    def _decode_search(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, dict):
            decoded: list[dict[str, Any]] = []
            results = raw.get(b"results", raw.get("results", []))
            for item in results or []:
                if not isinstance(item, dict):
                    continue
                raw_id = item.get(b"id", item.get("id", ""))
                attrs = item.get(b"extra_attributes", item.get("extra_attributes", {})) or {}
                values: dict[str, Any] = {"id": raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)}
                for name, value in attrs.items():
                    key = name.decode() if isinstance(name, bytes) else str(name)
                    values[key] = value.decode() if isinstance(value, bytes) else value
                decoded.append(values)
            return decoded
        if not raw or len(raw) < 2:
            return []
        results: list[dict[str, Any]] = []
        for index in range(1, len(raw), 2):
            key = raw[index]
            fields = raw[index + 1]
            values: dict[str, Any] = {}
            for offset in range(0, len(fields), 2):
                name = fields[offset].decode() if isinstance(fields[offset], bytes) else str(fields[offset])
                value = fields[offset + 1]
                values[name] = value.decode() if isinstance(value, bytes) else value
            values["id"] = key.decode() if isinstance(key, bytes) else str(key)
            results.append(values)
        return results


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_:-]", "_", str(value))
