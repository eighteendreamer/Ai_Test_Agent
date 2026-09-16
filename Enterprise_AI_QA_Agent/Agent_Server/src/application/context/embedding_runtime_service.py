from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from time import perf_counter

from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.application.model_clients.embeddings import resolve_embedding_client
from src.application.models.oauth_token_service import OAuthTokenService
from src.core.config import Settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.model_config import ModelConfigRecord


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingBatchResult:
    vectors: list[list[float]]
    model_name: str
    provider: str
    adapter: str
    original_dimension: int
    stored_dimension: int
    latency_ms: int
    normalized: bool = True
    embedding_version: str = ""


class EmbeddingRuntimeService:
    def __init__(
        self,
        *,
        model_config_store: MySQLModelConfigStore,
        settings: Settings,
        oauth_token_service: OAuthTokenService | None = None,
        cache_size: int = 512,
        redis_client: Redis | object | None = None,
    ) -> None:
        self._model_config_store = model_config_store
        self._settings = settings
        self._oauth_token_service = oauth_token_service
        self._cache_size = max(int(cache_size), 0)
        self._cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._redis_cache = redis_client
        self._redis_cache_lock = asyncio.Lock()
        self._redis_cache_disabled_until = 0.0
        self._embedding_dimensions: dict[str, int] = {}

    async def embed_texts(
        self,
        texts: list[str],
        *,
        config: ModelConfigRecord | None = None,
        use_cache: bool = True,
    ) -> EmbeddingBatchResult:
        prepared = [self._prepare_text(text) for text in texts]
        if not prepared:
            raise ValueError("At least one text is required for embedding.")
        resolved_config = config or await asyncio.to_thread(
            self._model_config_store.get_for_application,
            "embedding_retrieval",
        )
        client = resolve_embedding_client(
            resolved_config,
            timeout_seconds=self._settings.model.llm_request_timeout_seconds,
        )
        embedding_version = self._embedding_version(resolved_config)
        cache_namespace = "|".join(
            (
                resolved_config.key,
                resolved_config.provider,
                resolved_config.name,
                resolved_config.model_id,
                resolved_config.api_base_url,
                embedding_version,
            )
        )
        cache_keys = [(cache_namespace, text) for text in prepared]
        redis_keys = [
            self._redis_cache_key(
                resolved_config.key,
                resolved_config.model_id,
                embedding_version,
                text,
            )
            for text in prepared
        ]
        redis_contract_key = self._redis_dimension_contract_key(
            resolved_config.key,
            resolved_config.model_id,
            embedding_version,
        )
        cached = (
            await self._read_cache(cache_keys)
            if use_cache
            else [None for _ in prepared]
        )
        if use_cache and any(vector is None for vector in cached):
            l2_values = await self._read_redis_cache(
                redis_keys,
                contract_key=redis_contract_key,
            )
            for index, vector in enumerate(l2_values):
                if cached[index] is None and vector is not None:
                    cached[index] = vector
        missing_indexes = [index for index, vector in enumerate(cached) if vector is None]
        started_at = perf_counter()
        original_dimension = 0

        if missing_indexes:
            api_key = await self._resolve_api_key(resolved_config)
            missing_texts = [prepared[index] for index in missing_indexes]
            raw_vectors = await client.embed(resolved_config, api_key, missing_texts)
            if len(raw_vectors) != len(missing_indexes):
                raise ValueError(
                    "Embedding provider returned a different number of vectors than inputs."
                )
            original_dimension = len(raw_vectors[0]) if raw_vectors else 0
            dimensions = {len(vector) for vector in raw_vectors}
            if len(dimensions) != 1 or not dimensions or next(iter(dimensions)) <= 0:
                raise ValueError("Embedding provider returned inconsistent vector dimensions.")
            self._validate_dimension_contract(embedding_version, next(iter(dimensions)))
            normalized_vectors = [self._normalize_vector(vector) for vector in raw_vectors]
            for index, vector in zip(missing_indexes, normalized_vectors):
                cached[index] = vector
            if use_cache:
                await self._write_cache(
                    [cache_keys[index] for index in missing_indexes],
                    normalized_vectors,
                )
                await self._write_redis_cache(
                    [redis_keys[index] for index in missing_indexes],
                    normalized_vectors,
                    contract_key=redis_contract_key,
                )

        vectors = [vector for vector in cached if vector is not None]
        if len(vectors) != len(prepared):
            raise RuntimeError("Embedding cache resolution produced incomplete results.")
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or not dimensions or next(iter(dimensions)) <= 0:
            raise ValueError("Embedding cache returned inconsistent vector dimensions.")
        self._validate_dimension_contract(embedding_version, next(iter(dimensions)))
        return EmbeddingBatchResult(
            vectors=vectors,
            model_name=resolved_config.name,
            provider=resolved_config.provider,
            adapter=client.key,
            original_dimension=original_dimension or len(vectors[0]),
            stored_dimension=len(vectors[0]),
            embedding_version=embedding_version,
            latency_ms=int((perf_counter() - started_at) * 1000),
        )

    async def close(self) -> None:
        client = self._redis_cache
        if client is not None and hasattr(client, "aclose"):
            await client.aclose()
        self._redis_cache = None

    async def _redis_client_or_none(self):
        now = perf_counter()
        if now < self._redis_cache_disabled_until:
            return None
        if self._redis_cache is not None:
            return self._redis_cache
        async with self._redis_cache_lock:
            if self._redis_cache is not None:
                return self._redis_cache
            client = None
            try:
                client = Redis.from_url(
                    self._settings.database.redis_url,
                    decode_responses=True,
                    socket_timeout=self._settings.orchestration.redis_vector_socket_timeout_seconds,
                    socket_connect_timeout=self._settings.orchestration.redis_vector_socket_timeout_seconds,
                )
                await client.ping()
                self._redis_cache = client
                return client
            except Exception as exc:
                self._redis_cache_disabled_until = now + 5.0
                if client is not None and hasattr(client, "aclose"):
                    try:
                        await client.aclose()
                    except Exception:
                        LOGGER.debug(
                            "embedding_redis_cache_close_failed",
                            extra={"error_type": type(exc).__name__},
                            exc_info=True,
                        )
                LOGGER.warning(
                    "embedding_redis_cache_unavailable",
                    extra={"error_type": type(exc).__name__},
                )
                return None

    async def _read_redis_cache(
        self,
        keys: list[str],
        *,
        contract_key: str,
    ) -> list[list[float] | None]:
        if not keys:
            return []
        client = await self._redis_client_or_none()
        if client is None:
            return [None for _ in keys]
        try:
            contract = self._parse_dimension(await client.get(contract_key))
            if contract is None:
                return [None for _ in keys]
            values = await client.mget(keys)
            decoded = [self._decode_cached_vector(value) for value in values]
            return [
                vector if vector is not None and len(vector) == contract else None
                for vector in decoded
            ]
        except (RedisError, OSError, ValueError, TypeError) as exc:
            self._disable_redis_cache()
            LOGGER.warning(
                "embedding_redis_cache_read_failed",
                extra={"error_type": type(exc).__name__},
            )
            return [None for _ in keys]

    async def _write_redis_cache(
        self,
        keys: list[str],
        vectors: list[list[float]],
        *,
        contract_key: str,
    ) -> None:
        if not keys or not vectors:
            return
        client = await self._redis_client_or_none()
        if client is None:
            return
        ttl = max(1, int(self._settings.orchestration.redis_hot_memory_ttl_seconds))
        try:
            dimensions = {len(vector) for vector in vectors}
            if len(dimensions) != 1 or not dimensions or next(iter(dimensions)) <= 0:
                raise ValueError("Embedding cache write received inconsistent dimensions.")
            dimension = next(iter(dimensions))
            raw_contract = await client.get(contract_key)
            existing_dimension = self._parse_dimension(raw_contract)
            if raw_contract is not None and existing_dimension is None:
                raise ValueError(
                    "Embedding Redis cache dimension contract is invalid."
                )
            if raw_contract is None:
                created = await client.set(
                    contract_key,
                    str(dimension),
                    ex=ttl,
                    nx=True,
                )
                if not created:
                    existing_dimension = self._parse_dimension(await client.get(contract_key))
                    if existing_dimension is None:
                        raise ValueError(
                            "Embedding Redis cache dimension contract disappeared."
                        )
            if existing_dimension is not None and existing_dimension != dimension:
                raise ValueError(
                    "Embedding Redis cache dimension contract does not match vector."
                )
            pipeline = client.pipeline(transaction=False) if hasattr(client, "pipeline") else None
            if pipeline is not None:
                for key, vector in zip(keys, vectors):
                    pipeline.set(key, json.dumps(vector, separators=(",", ":")), ex=ttl)
                await pipeline.execute()
            else:
                for key, vector in zip(keys, vectors):
                    await client.set(key, json.dumps(vector, separators=(",", ":")), ex=ttl)
        except ValueError:
            raise
        except (RedisError, OSError, TypeError) as exc:
            self._disable_redis_cache()
            LOGGER.warning(
                "embedding_redis_cache_write_failed",
                extra={"error_type": type(exc).__name__},
            )

    def _disable_redis_cache(self) -> None:
        self._redis_cache_disabled_until = perf_counter() + 5.0

    def _validate_dimension_contract(self, embedding_version: str, dimension: int) -> None:
        existing = self._embedding_dimensions.get(embedding_version)
        if existing is not None and existing != dimension:
            raise ValueError(
                "Embedding version returned inconsistent vector dimensions."
            )
        self._embedding_dimensions[embedding_version] = dimension

    @staticmethod
    def _parse_dimension(value) -> int | None:
        try:
            dimension = int(value)
        except (TypeError, ValueError):
            return None
        return dimension if dimension > 0 else None

    @staticmethod
    def _decode_cached_vector(value) -> list[float] | None:
        if value is None:
            return None
        try:
            decoded = json.loads(value)
            if not isinstance(decoded, list) or not decoded:
                return None
            vector = [float(item) for item in decoded]
            if any(not math.isfinite(item) for item in vector):
                return None
            norm = math.sqrt(sum(item * item for item in vector))
            return vector if norm > 0 else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _redis_cache_key(model_key: str, model_id: str, version: str, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return (
            f"qa:embedding:{_safe_cache_part(model_key)}:"
            f"{_safe_cache_part(model_id)}:{_safe_cache_part(version)}:{digest}"
        )

    @staticmethod
    def _redis_dimension_contract_key(model_key: str, model_id: str, version: str) -> str:
        return (
            f"qa:embedding:dimension:{_safe_cache_part(model_key)}:"
            f"{_safe_cache_part(model_id)}:{_safe_cache_part(version)}"
        )

    async def _resolve_api_key(self, config: ModelConfigRecord) -> str:
        if config.auth_type == "oauth2":
            if self._oauth_token_service is None:
                raise RuntimeError(
                    "OAuthTokenService is required for an OAuth embedding model."
                )
            token = await self._oauth_token_service.get_token(config)
        else:
            token = config.api_key or (
                os.getenv(config.api_key_env) if config.api_key_env else None
            )
        if not token:
            raise RuntimeError(
                f"Embedding model '{config.name}' has no usable API credential."
            )
        return token

    def _normalize_vector(self, vector: list[float]) -> list[float]:
        values = [float(value) for value in vector]
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0:
            raise ValueError("Embedding provider returned a zero vector.")
        return [value / norm for value in values]

    @staticmethod
    def _embedding_version(config: ModelConfigRecord) -> str:
        material = "|".join((config.key, config.provider, config.model_id, config.api_base_url))
        return "emb_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _prepare_text(text: str) -> str:
        normalized = " ".join(str(text or "").split()).strip()
        return (normalized or "empty memory")[:8000]

    async def _read_cache(
        self,
        keys: list[tuple[str, str]],
    ) -> list[list[float] | None]:
        async with self._cache_lock:
            values = []
            for key in keys:
                value = self._cache.get(key)
                if value is not None:
                    self._cache.move_to_end(key)
                    values.append(list(value))
                else:
                    values.append(None)
            return values

    async def _write_cache(
        self,
        keys: list[tuple[str, str]],
        vectors: list[list[float]],
    ) -> None:
        if self._cache_size <= 0:
            return
        async with self._cache_lock:
            for key, vector in zip(keys, vectors):
                self._cache[key] = list(vector)
                self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)


def _safe_cache_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:120] or "unknown"
