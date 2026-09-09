from __future__ import annotations

import pytest
from langchain.agents.middleware import AgentMiddleware

from src.application.model_adapters.middleware_registry import LangChainMiddlewareRegistry


class _FirstMiddleware(AgentMiddleware):
    pass


class _SecondMiddleware(AgentMiddleware):
    pass


def test_registry_resolves_enabled_middleware_by_order_and_scope():
    registry = LangChainMiddlewareRegistry()
    first = _FirstMiddleware()
    second = _SecondMiddleware()
    registry.register(second, order=20, scopes={"code_review"})
    registry.register(first, order=10)

    assert registry.resolve("code_review") == [first, second]
    assert registry.resolve("api_testing") == [first]


def test_registry_rejects_duplicate_names_and_omits_disabled_entries():
    registry = LangChainMiddlewareRegistry()
    first = _FirstMiddleware()
    registry.register(first, order=10, enabled=False)

    assert registry.resolve("code_review") == []
    assert registry.describe()[0]["enabled"] is False
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_FirstMiddleware(), order=20)
