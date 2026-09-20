from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.application.model_clients.embeddings import (
    GoogleEmbeddingClient,
    OpenAICompatibleEmbeddingClient,
    resolve_embedding_client,
)
from src.application.model_clients.google_client import GoogleGenAIClient
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.model_config import ModelConfigRecord
from src.schemas.settings import ModelConfigUpdateRequest


def _config(**updates) -> ModelConfigRecord:
    values = {
        "key": "embedding-model",
        "name": "text-embedding-3-small",
        "provider": "openai",
        "transport": "openai_chat_completions",
        "model_id": "text-embedding-3-small",
        "api_base_url": "https://api.openai.com/v1",
        "applications": ["embedding_retrieval"],
    }
    values.update(updates)
    return ModelConfigRecord(**values)


def test_model_application_defaults_and_requires_single_selection() -> None:
    default_request = ModelConfigUpdateRequest(
        model_name="gpt-test",
        provider="openai",
        base_url="https://example.test/v1",
    )
    assert default_request.applications == ["task_execution"]

    with pytest.raises(ValueError, match="Exactly one model application"):
        ModelConfigUpdateRequest(
            model_name="dual-purpose",
            provider="openai",
            base_url="https://example.test/v1",
            applications=["embedding_retrieval", "task_execution"],
        )


def test_embedding_activation_is_independent_from_task_model_activation() -> None:
    task_model = _config(
        key="task-model",
        name="chat-model",
        model_id="chat-model",
        applications=["task_execution"],
        is_active=True,
        active_application="task_execution",
    )
    embedding_model = _config(
        key="embedding-model-2",
        name="Qwen/Qwen3-VL-Embedding-8B",
        model_id="Qwen/Qwen3-VL-Embedding-8B",
        active_application="embedding_retrieval",
    )
    store = MySQLModelConfigStore.__new__(MySQLModelConfigStore)
    store.list_all = lambda: [task_model, embedding_model]

    assert store.get_for_application("task_execution").name == "chat-model"
    assert (
        store.get_for_application("embedding_retrieval").name
        == "Qwen/Qwen3-VL-Embedding-8B"
    )
    assert store.to_public(embedding_model).is_active is True
    assert store.to_public(task_model).is_active is True


def test_embedding_client_dispatch_follows_transport() -> None:
    # openai protocol -> OpenAI-compatible /embeddings, regardless of vendor
    assert isinstance(resolve_embedding_client(_config()), OpenAICompatibleEmbeddingClient)
    assert isinstance(
        resolve_embedding_client(_config(provider="qwen")),
        OpenAICompatibleEmbeddingClient,
    )
    # Google exposing an OpenAI-compatible base URL keeps the openai protocol
    assert isinstance(
        resolve_embedding_client(
            _config(
                provider="google",
                api_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            )
        ),
        OpenAICompatibleEmbeddingClient,
    )
    # google protocol -> native embedContent URL format
    assert isinstance(
        resolve_embedding_client(
            _config(provider="google", transport="google_gemini_generate_content")
        ),
        GoogleEmbeddingClient,
    )


def test_embedding_registry_rejects_anthropic_protocol() -> None:
    with pytest.raises(ValueError, match="does not expose an embeddings API"):
        resolve_embedding_client(
            _config(provider="anthropic", transport="anthropic_messages")
        )


def test_google_base_url_strips_version_suffix() -> None:
    assert (
        GoogleGenAIClient._normalize_base_url(
            "https://generativelanguage.googleapis.com/v1beta"
        )
        == "https://generativelanguage.googleapis.com"
    )
    assert (
        GoogleGenAIClient._normalize_base_url("https://proxy.example.test/v1/")
        == "https://proxy.example.test"
    )
    assert (
        GoogleGenAIClient._normalize_base_url("https://proxy.example.test")
        == "https://proxy.example.test"
    )


def test_openai_embedding_orders_vectors_by_index(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeEmbeddings:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=1, embedding=[0.4, 0.5]),
                    SimpleNamespace(index=0, embedding=[0.1, 0.2]),
                ]
            )

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.embeddings = _FakeEmbeddings()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(
        "src.application.model_clients.embeddings.AsyncOpenAI",
        _FakeAsyncOpenAI,
    )

    vectors = asyncio.run(
        OpenAICompatibleEmbeddingClient().embed(_config(), "secret", ["first", "second"])
    )

    assert vectors == [[0.1, 0.2], [0.4, 0.5]]
    assert captured["model"] == "text-embedding-3-small"
    assert captured["input"] == ["first", "second"]
    assert captured["encoding_format"] == "float"
    client_kwargs = captured["client_kwargs"]
    assert client_kwargs["api_key"] == "secret"
    assert client_kwargs["base_url"] == "https://api.openai.com/v1"


def test_google_embedding_uses_sdk_and_prefixes_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeModels:
        async def embed_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                embeddings=[
                    SimpleNamespace(values=[0.6, 0.8]),
                    SimpleNamespace(values=[1.0, 0.0]),
                ]
            )

    class _FakeGenAIClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.aio = SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr(
        "src.application.model_clients.embeddings.genai.Client",
        _FakeGenAIClient,
    )

    config = _config(
        provider="google",
        transport="google_gemini_generate_content",
        name="gemini-embedding-001",
        model_id="gemini-embedding-001",
        api_base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    vectors = asyncio.run(GoogleEmbeddingClient().embed(config, "secret", ["hello", "world"]))

    assert vectors == [[0.6, 0.8], [1.0, 0.0]]
    assert captured["model"] == "models/gemini-embedding-001"
    assert captured["contents"] == ["hello", "world"]
    client_kwargs = captured["client_kwargs"]
    assert client_kwargs["api_key"] == "secret"
    assert (
        client_kwargs["http_options"].base_url
        == "https://generativelanguage.googleapis.com"
    )
