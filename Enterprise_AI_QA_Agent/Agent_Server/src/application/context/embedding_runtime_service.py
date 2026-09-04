from __future__ import annotations

import asyncio
import math
import os
from collections import OrderedDict
from dataclasses import dataclass
from time import perf_counter

from src.application.model_clients.embeddings import resolve_embedding_client
from src.application.models.oauth_token_service import OAuthTokenService
from src.core.config import Settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.model_config import ModelConfigRecord


@dataclass(frozen=True)
class EmbeddingBatchResult:
    vectors: list[list[float]]
    model_name: str
    provider: str
    adapter: str
    original_dimension: int
    stored_dimension: int
    latency_ms: int


class EmbeddingRuntimeService:
    def __init__(
        self,
        *,
        model_config_store: MySQLModelConfigStore,
        settings: Settings,
        oauth_token_service: OAuthTokenService | None = None,
        cache_size: int = 512,
    ) -> None:
        self._model_config_store = model_config_store
        self._settings = settings
        self._oauth_token_service = oauth_token_service
        self._cache_size = max(int(cache_size), 0)
        self._cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._cache_lock = asyncio.Lock()

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
        cache_namespace = "|".join(
            (
                resolved_config.provider,
                resolved_config.name,
                resolved_config.model_id,
                resolved_config.api_base_url,
            )
        )
        cache_keys = [(cache_namespace, text) for text in prepared]
        cached = (
            await self._read_cache(cache_keys)
            if use_cache
            else [None for _ in prepared]
        )
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
            normalized_vectors = [
                self._normalize_dimension(vector) for vector in raw_vectors
            ]
            for index, vector in zip(missing_indexes, normalized_vectors):
                cached[index] = vector
            if use_cache:
                await self._write_cache(
                    [cache_keys[index] for index in missing_indexes],
                    normalized_vectors,
                )

        vectors = [vector for vector in cached if vector is not None]
        if len(vectors) != len(prepared):
            raise RuntimeError("Embedding cache resolution produced incomplete results.")
        return EmbeddingBatchResult(
            vectors=vectors,
            model_name=resolved_config.name,
            provider=resolved_config.provider,
            adapter=client.key,
            original_dimension=original_dimension or len(vectors[0]),
            stored_dimension=self._settings.database.postgres_vector_dimension,
            latency_ms=int((perf_counter() - started_at) * 1000),
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

    def _normalize_dimension(self, vector: list[float]) -> list[float]:
        target = self._settings.database.postgres_vector_dimension
        values = [float(value) for value in vector[:target]]
        if len(values) < target:
            values.extend([0.0] * (target - len(values)))
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0:
            raise ValueError("Embedding provider returned a zero vector.")
        return [value / norm for value in values]

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
