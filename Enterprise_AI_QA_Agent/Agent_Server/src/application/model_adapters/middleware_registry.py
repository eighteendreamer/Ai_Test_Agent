from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware


@dataclass(frozen=True)
class MiddlewareRegistration:
    name: str
    middleware: AgentMiddleware[Any, Any]
    order: int
    scopes: frozenset[str]
    enabled: bool = True


class LangChainMiddlewareRegistry:
    """Ordered, scoped registry for official LangChain middleware instances."""

    def __init__(self) -> None:
        self._registrations: dict[str, MiddlewareRegistration] = {}

    def register(
        self,
        middleware: AgentMiddleware[Any, Any],
        *,
        order: int,
        scopes: set[str] | frozenset[str] | None = None,
        enabled: bool = True,
    ) -> None:
        name = str(middleware.name or "").strip()
        if not name:
            raise ValueError("LangChain middleware name must not be empty.")
        if name in self._registrations:
            raise ValueError(f"LangChain middleware '{name}' is already registered.")
        self._registrations[name] = MiddlewareRegistration(
            name=name,
            middleware=middleware,
            order=order,
            scopes=frozenset(scopes or ()),
            enabled=enabled,
        )

    def resolve(self, scope: str) -> list[AgentMiddleware[Any, Any]]:
        selected = [
            item
            for item in self._registrations.values()
            if item.enabled and (not item.scopes or scope in item.scopes)
        ]
        selected.sort(key=lambda item: (item.order, item.name))
        return [item.middleware for item in selected]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "order": item.order,
                "scopes": sorted(item.scopes),
                "enabled": item.enabled,
            }
            for item in sorted(
                self._registrations.values(),
                key=lambda registration: (registration.order, registration.name),
            )
        ]
