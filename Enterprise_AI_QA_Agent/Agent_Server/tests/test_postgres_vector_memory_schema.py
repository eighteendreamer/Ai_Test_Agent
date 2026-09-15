from __future__ import annotations

import asyncio

from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore


def test_initialize_normalizes_existing_vector_column(monkeypatch):
    statements = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, statement, params=None): statements.append(statement)

    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass

    class Context:
        def __enter__(self): return Connection()
        def __exit__(self, *args): pass

    monkeypatch.setattr("src.infrastructure.postgres_vector_memory_store.postgres_connect", lambda settings: Context())
    settings = type("S", (), {"database": type("D", (), {"postgres_memory_table": "memories"})()})()
    asyncio.run(PostgresVectorMemoryStore(settings).initialize())
    assert any("ALTER COLUMN embedding TYPE VECTOR" in statement for statement in statements)
