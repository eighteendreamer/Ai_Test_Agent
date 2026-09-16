from __future__ import annotations

from types import SimpleNamespace

from src.core.config import OrchestrConfig
from src.runtime.postgres_vector_rebuild_job_store import (
    PostgresVectorRebuildJobStore,
)


def test_vector_rebuild_stream_uses_configured_low_priority_stream() -> None:
    config = OrchestrConfig(
        redis_task_outbox_streams=(
            "qa:tasks:compaction,qa:tasks:vector_rebuild,qa:tasks:cleanup"
        )
    )
    assert config.vector_rebuild_task_stream == "qa:tasks:vector_rebuild"


class Cursor:
    def __init__(self, rowcounts):
        self.rowcounts = iter(rowcounts)
        self.rowcount = 0
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters=None):
        self.statements.append((" ".join(statement.split()), parameters))
        self.rowcount = next(self.rowcounts, 0)


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


def _store():
    settings = SimpleNamespace(
        database=SimpleNamespace(
            postgres_vector_rebuild_job_table="vector_jobs",
        )
    )
    return PostgresVectorRebuildJobStore(settings, lease_seconds=30)


def test_vector_job_and_outbox_are_inserted_in_one_transaction(monkeypatch):
    cursor = Cursor([1, 1])
    connection = Connection(cursor)
    monkeypatch.setattr(
        "src.runtime.postgres_vector_rebuild_job_store.postgres_connect",
        lambda _settings: connection,
    )
    store = _store()
    payload = {
        "task_id": "task-1",
        "task_type": "vector_rebuild_task",
        "embedding_version": "emb_v1",
    }

    assert store._enqueue_sync(
        "task-1",
        "emb_v1",
        True,
        "qa:tasks:vector_rebuild",
        "task_outbox",
        payload,
    )

    assert connection.commits == 1
    assert "INSERT INTO vector_jobs" in cursor.statements[0][0]
    assert "INSERT INTO task_outbox" in cursor.statements[1][0]
    assert cursor.statements[1][1][0] == "vector_rebuild:task-1"


def test_vector_job_claim_and_progress_are_owner_fenced(monkeypatch):
    cursor = Cursor([1, 1, 1])
    connection = Connection(cursor)
    monkeypatch.setattr(
        "src.runtime.postgres_vector_rebuild_job_store.postgres_connect",
        lambda _settings: connection,
    )
    store = _store()

    assert store._begin_sync("task-1", "worker-1")
    assert store._progress_sync(
        "task-1", "worker-1", "index_validating", 4096, 10, 10,
    )
    assert store._complete_sync(
        "task-1",
        "worker-1",
        "index_activated",
        4096,
        10,
        10,
        "qa:vector:memory:emb_v1",
    )

    progress_sql = cursor.statements[1][0]
    complete_sql = cursor.statements[2][0]
    assert "worker_id = %s" in progress_sql and "lease_expires_at > NOW()" in progress_sql
    assert "worker_id = %s" in complete_sql and "lease_expires_at > NOW()" in complete_sql


def test_vector_job_outbox_divergence_aborts_transaction(monkeypatch):
    cursor = Cursor([1, 0])
    connection = Connection(cursor)
    monkeypatch.setattr(
        "src.runtime.postgres_vector_rebuild_job_store.postgres_connect",
        lambda _settings: connection,
    )

    try:
        _store()._enqueue_sync(
            "task-1",
            "emb_v1",
            False,
            "qa:tasks:vector_rebuild",
            "task_outbox",
            {"task_id": "task-1"},
        )
    except RuntimeError as exc:
        assert "diverged" in str(exc)
    else:
        raise AssertionError("Divergent job/outbox state must fail the transaction")
    assert connection.commits == 0
