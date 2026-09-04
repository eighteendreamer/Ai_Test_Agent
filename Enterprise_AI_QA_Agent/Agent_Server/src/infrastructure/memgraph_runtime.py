from __future__ import annotations

from typing import Any

from src.core.config import Settings


class MemgraphRuntimeProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver = None

    @property
    def backend(self) -> str:
        return "memgraph"

    @property
    def bolt_uri(self) -> str:
        return f"bolt://{self._settings.database.memgraph_host}:{self._settings.database.memgraph_port}"

    def initialize(self) -> None:
        self.driver().verify_connectivity()

    def driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase

            auth = None
            if self._settings.database.memgraph_user:
                auth = (self._settings.database.memgraph_user, self._settings.database.memgraph_password)
            connection_timeout = float(getattr(self._settings, "memgraph_connect_timeout_seconds", 1.0) or 1.0)
            self._driver = GraphDatabase.driver(
                self.bolt_uri,
                auth=auth,
                connection_timeout=max(0.5, connection_timeout),
            )
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def is_available(self) -> bool:
        try:
            self.initialize()
            return True
        except Exception:
            return False

    def execute(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.driver().session() as session:
            result = session.run(query, parameters or {})
            return [dict(record) for record in result]

    def execute_write(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.execute(query, parameters)
