from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.infrastructure import postgres_test_case_embedding_store as store_module
from src.infrastructure.postgres_test_case_embedding_store import (
    PostgresTestCaseEmbeddingStore,
)


class _Cursor:
    def __init__(self, commands: list[str]) -> None:
        self._commands = commands

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, _parameters=None):
        self._commands.append(statement.lstrip().split(maxsplit=1)[0].upper())


class _Connection:
    def __init__(self, transactions: list[list[str]]) -> None:
        self._transactions = transactions

    def __enter__(self):
        self.commands: list[str] = []
        self._transactions.append(self.commands)
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return _Cursor(self.commands)


def _store(monkeypatch) -> tuple[PostgresTestCaseEmbeddingStore, list[list[str]]]:
    settings = SimpleNamespace(
        database=SimpleNamespace(
            postgres_test_case_embedding_table="test_case_embeddings",
            postgres_test_case_table="test_cases",
            postgres_test_case_version_table="test_case_versions",
        )
    )
    transactions: list[list[str]] = []
    monkeypatch.setattr(
        store_module,
        "postgres_connect",
        lambda _settings: _Connection(transactions),
    )
    return PostgresTestCaseEmbeddingStore(settings), transactions


def _write(
    version_id: str, *, dimension: int = 2, embedding_version: str = "version-1"
) -> store_module.TestCaseEmbeddingWrite:
    return store_module.TestCaseEmbeddingWrite(
        case_id="case-1",
        case_version_id=version_id,
        project_id="project-1",
        content="sample",
        content_hash="hash",
        embedding=[1.0] + [0.0] * (dimension - 1),
        embedding_model_key="model-key",
        embedding_provider="local",
        embedding_model_id="model-id",
        embedding_version=embedding_version,
        embedding_dimension=dimension,
    )


def test_index_ddl_is_once_per_version_dimension_and_outside_write_transaction(monkeypatch) -> None:
    store, transactions = _store(monkeypatch)

    store._upsert_sync(_write("version-1"))
    store._upsert_sync(_write("version-2"))
    store._upsert_sync(_write("version-3", dimension=3))
    store._upsert_sync(_write("version-4", embedding_version="version-2"))

    assert transactions == [
        ["CREATE"],
        ["INSERT"],
        ["INSERT"],
        ["CREATE"],
        ["INSERT"],
        ["CREATE"],
        ["INSERT"],
    ]


def test_concurrent_writes_prepare_same_index_once(monkeypatch) -> None:
    store, transactions = _store(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(store._upsert_sync, [_write("version-1"), _write("version-2")]))

    assert sum(command == "CREATE" for transaction in transactions for command in transaction) == 1
    assert sum(command == "INSERT" for transaction in transactions for command in transaction) == 2


def test_failed_index_prepare_does_not_start_row_write_or_poison_retry(monkeypatch) -> None:
    store, transactions = _store(monkeypatch)
    prepare = store._ensure_ann_index
    attempts = 0

    def fail_once(cursor, version: str, dimension: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated index preparation failure")
        prepare(cursor, version, dimension)

    monkeypatch.setattr(store, "_ensure_ann_index", fail_once)
    with pytest.raises(OSError, match="index preparation failure"):
        store._upsert_sync(_write("version-1"))
    store._upsert_sync(_write("version-1"))

    assert attempts == 2
    assert transactions == [[], ["CREATE"], ["INSERT"]]
