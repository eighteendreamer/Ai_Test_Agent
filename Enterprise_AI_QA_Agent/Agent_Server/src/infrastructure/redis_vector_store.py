"""Redis RediSearch vector replica with versioned, dynamic dimensions."""

from __future__ import annotations

import re
import math
import struct
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError


class RedisVectorStore:
    def __init__(
        self, redis_url: str, *, client: Redis | Any | None = None,
        socket_timeout_seconds: float = 5.0,
    ) -> None:
        self._redis_url = redis_url
        self._client = client
        if socket_timeout_seconds <= 0:
            raise ValueError("Redis vector timeout must be positive")
        self._socket_timeout_seconds = socket_timeout_seconds

    async def connect(self) -> None:
        if self._client is None:
            self._client = Redis.from_url(
                self._redis_url, decode_responses=False,
                socket_timeout=self._socket_timeout_seconds,
                socket_connect_timeout=self._socket_timeout_seconds,
            )
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
        except ResponseError as exc:
            if "Index already exists" not in str(exc):
                raise
        await self.validate_index(entity=entity, embedding_version=embedding_version, dimension=dimension)
        return index

    async def validate_index(self, *, entity: str, embedding_version: str, dimension: int) -> None:
        """Verify FT.INFO (RESP2/RESP3), not a cached assumption about an index."""
        info = _mapping(await self._client.execute_command("FT.INFO", self.index_name(entity, embedding_version)))
        definition = _mapping(info.get("index_definition", {}))
        prefixes = [_text(value) for value in definition.get("prefixes", [])]
        vector_fields = [_mapping(value) for value in info.get("attributes", [])]
        field = next((value for value in vector_fields if _text(value.get("attribute")) == "embedding"), {})
        if (prefixes != [self.prefix(entity, embedding_version)]
                or _text(definition.get("key_type")) != "HASH"
                or _text(field.get("type")) != "VECTOR"
                or _text(field.get("algorithm")) != "HNSW"
                or _text(field.get("data_type")) != "FLOAT32"
                or _text(field.get("distance_metric")) != "COSINE"
                or int(field.get("dim", 0)) != dimension):
            raise ValueError("Redis vector index contract does not match requested version/dimension")

    async def upsert(
        self,
        *,
        entity: str,
        embedding_version: str,
        point_id: str,
        vector: list[float],
        metadata: dict[str, str] | None = None,
    ) -> str:
        blob = self.pack_vector(vector)
        await self.validate_index(entity=entity, embedding_version=embedding_version, dimension=len(vector))
        key = f"{self.prefix(entity, embedding_version)}{point_id}"
        fields: dict[str, Any] = {"embedding": blob}
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
        blob = self.pack_vector(vector)
        await self.validate_index(entity=entity, embedding_version=embedding_version, dimension=len(vector))
        index = self.index_name(entity, embedding_version)
        query_filter = "*"
        if filters:
            clauses = [f"@{name}:{{{self._escape_tag(value)}}}" for name, value in filters.items() if name in {"project_id", "case_version_id", "status", "environment"}]
            query_filter = " ".join(clauses) if clauses else "*"
        count = max(1, int(top_k))
        query = f"({query_filter})=>[KNN {count} @embedding $query AS vector_distance]"
        raw = await self._client.execute_command(
            "FT.SEARCH", index, query, "PARAMS", "2", "query", blob,
            "SORTBY", "vector_distance", "LIMIT", "0", str(count),
            "RETURN", "5", "vector_distance", "project_id", "case_version_id", "status", "environment", "DIALECT", "2",
        )
        return self._decode_search(raw)

    @staticmethod
    def pack_vector(vector: list[float]) -> bytes:
        if not vector or any(isinstance(value, bool) or not isinstance(value, (int, float))
                             or not math.isfinite(value) for value in vector):
            raise ValueError("Vector must contain finite numeric values")
        try:
            blob = struct.pack(f"<{len(vector)}f", *vector)
        except (OverflowError, struct.error) as exc:
            raise ValueError("Vector is not representable as FLOAT32") from exc
        if not any(value != 0 for (value,) in struct.iter_unpack("<f", blob)):
            raise ValueError("Cosine vector must be nonzero after FLOAT32 encoding")
        return blob

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
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(value)):
        raise ValueError("Vector entity/version must be a nonempty alphanumeric identifier")
    return str(value)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {_text(key): item for key, item in value.items()}
    if isinstance(value, (list, tuple)) and len(value) % 2 == 0:
        return {_text(value[index]): value[index + 1] for index in range(0, len(value), 2)}
    raise ValueError("Unexpected Redis index metadata response")
