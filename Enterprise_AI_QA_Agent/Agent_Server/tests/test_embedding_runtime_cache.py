from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import src.application.context.embedding_runtime_service as embedding_module
from src.application.context.embedding_runtime_service import EmbeddingRuntimeService
from src.schemas.model_config import ModelConfigRecord


class _FakeEmbeddingClient:
    key = "fake_embeddings"

    def __init__(self, dimensions: int = 3) -> None:
        self.calls: list[list[str]] = []
        self.dimensions = dimensions

    async def embed(self, _config, _api_key: str, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [1.0] + [0.0] * (self.dimensions - 1)
            for _ in texts
        ]


class _FakeRedis:
    def __init__(self, data: dict[str, str] | None = None) -> None:
        self.data = data if data is not None else {}
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def get(self, key: str):
        return self.data.get(key)

    async def mget(self, keys: list[str]):
        return [self.data.get(key) for key in keys]

    async def set(self, key: str, value, *, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = str(value)
        return True

    async def aclose(self) -> None:
        self.closed = True


class _UnavailableRedis(_FakeRedis):
    async def mget(self, _keys: list[str]):
        raise OSError("redis unavailable")


def _settings():
    return SimpleNamespace(
        model=SimpleNamespace(llm_request_timeout_seconds=10),
        database=SimpleNamespace(redis_url="redis://unused"),
        orchestration=SimpleNamespace(
            redis_vector_socket_timeout_seconds=1,
            redis_hot_memory_ttl_seconds=120,
        ),
    )


def _config(**updates) -> ModelConfigRecord:
    values = {
        "key": "embedding-model",
        "name": "test-embedding",
        "provider": "test",
        "transport": "openai_chat_completions",
        "model_id": "test-embedding-v1",
        "api_base_url": "https://embedding.example/v1",
        "api_key": "test-key",
        "applications": ["embedding_retrieval"],
    }
    values.update(updates)
    return ModelConfigRecord(**values)


def _service(redis, client) -> EmbeddingRuntimeService:
    return EmbeddingRuntimeService(
        model_config_store=SimpleNamespace(),
        settings=_settings(),
        redis_client=redis,
        cache_size=0,
    )


def test_embedding_redis_l2_cache_is_shared_between_service_instances(monkeypatch) -> None:
    async def run() -> None:
        client = _FakeEmbeddingClient()
        monkeypatch.setattr(
            embedding_module,
            "resolve_embedding_client",
            lambda *_args, **_kwargs: client,
        )
        redis = _FakeRedis()
        config = _config()

        first = _service(redis, client)
        first_result = await first.embed_texts(["login test"], config=config)
        second = _service(redis, client)
        second_result = await second.embed_texts(["login test"], config=config)

        assert first_result.vectors == second_result.vectors
        assert client.calls == [["login test"]]
        expected_key = first._redis_cache_key(
            config.key,
            config.model_id,
            first._embedding_version(config),
            "login test",
        )
        assert expected_key in redis.data
        assert any(key.startswith("qa:embedding:dimension:embedding-model:") for key in redis.data)

        await first.close()
        await second.close()

    asyncio.run(run())


def test_embedding_redis_cache_isolated_by_embedding_version(monkeypatch) -> None:
    async def run() -> None:
        client = _FakeEmbeddingClient()
        monkeypatch.setattr(
            embedding_module,
            "resolve_embedding_client",
            lambda *_args, **_kwargs: client,
        )
        redis = _FakeRedis()
        first = _service(redis, client)
        await first.embed_texts(["same text"], config=_config())

        second = _service(redis, client)
        await second.embed_texts(
            ["same text"],
            config=_config(api_base_url="https://embedding.example/v2"),
        )

        assert client.calls == [["same text"], ["same text"]]
        vector_keys = [key for key in redis.data if key.startswith("qa:embedding:embedding-model:")]
        assert len(vector_keys) == 2

        await first.close()
        await second.close()

    asyncio.run(run())


def test_embedding_redis_cache_ignores_dimension_mismatch_and_refetches(monkeypatch) -> None:
    async def run() -> None:
        client = _FakeEmbeddingClient(dimensions=3)
        monkeypatch.setattr(
            embedding_module,
            "resolve_embedding_client",
            lambda *_args, **_kwargs: client,
        )
        redis = _FakeRedis()
        service = _service(redis, client)
        config = _config()
        version = service._embedding_version(config)
        contract_key = service._redis_dimension_contract_key(
            config.key, config.model_id, version,
        )
        vector_key = service._redis_cache_key(
            config.key, config.model_id, version, "bad cached value",
        )
        redis.data[contract_key] = "3"
        redis.data[vector_key] = json.dumps([1.0, 0.0])

        result = await service.embed_texts(["bad cached value"], config=config)

        assert result.stored_dimension == 3
        assert client.calls == [["bad cached value"]]
        await service.close()

    asyncio.run(run())


def test_embedding_redis_failure_falls_back_to_provider(monkeypatch) -> None:
    async def run() -> None:
        client = _FakeEmbeddingClient()
        monkeypatch.setattr(
            embedding_module,
            "resolve_embedding_client",
            lambda *_args, **_kwargs: client,
        )
        service = _service(_UnavailableRedis(), client)

        result = await service.embed_texts(["fallback"], config=_config())

        assert result.stored_dimension == 3
        assert client.calls == [["fallback"]]
        await service.close()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_embedding_close_closes_injected_redis_client() -> None:
    redis = _FakeRedis()
    service = _service(redis, _FakeEmbeddingClient())

    await service.close()

    assert redis.closed is True
