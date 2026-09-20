from __future__ import annotations

from types import SimpleNamespace

from src.runtime.postgres_compaction_record_store import PostgresCompactionRecordStore


class Cursor:
    def __init__(self, rowcounts):
        self._rowcounts = iter(rowcounts)
        self.rowcount = 0
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters=None):
        self.statements.append((statement, parameters))
        try:
            self.rowcount = next(self._rowcounts)
        except StopIteration:
            self.rowcount = 0

    def fetchone(self):
        return None


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


def test_compaction_record_claim_is_idempotent_and_can_reclaim_failed(monkeypatch):
    cursor = Cursor([1])
    settings = SimpleNamespace(
        database=SimpleNamespace(
            postgres_compaction_table="compaction_records",
            postgres_event_table="agent_flow_events",
        )
    )
    store = PostgresCompactionRecordStore(settings, lease_seconds=30)
    monkeypatch.setattr(
        "src.runtime.postgres_compaction_record_store.postgres_connect",
        lambda _settings: Connection(cursor),
    )

    assert store._begin_sync("s1:e1:v1", "s1", "e1", "v1", "worker-1")
    assert cursor.statements[0][1][0] == "s1:e1:v1"

    cursor._rowcounts = iter([0, 1])
    assert store._begin_sync("s1:e1:v1", "s1", "e1", "v1", "worker-2")
    cursor._rowcounts = iter([1])
    assert store._complete_sync("s1:e1:v1", ["m1"])
    completion = cursor.statements[-1][0]
    assert "status = 'completed'" in completion
    # The watermark is resolved by PostgreSQL, which owns event_sequence.
    assert "last_compacted_event_sequence = COALESCE" in completion
    assert "FROM agent_flow_events event" in completion


def test_compaction_backlog_diffs_durable_events_against_watermark(monkeypatch):
    cursor = Cursor([1])
    cursor.fetchone = lambda: (7,)
    settings = SimpleNamespace(
        database=SimpleNamespace(
            postgres_compaction_table="compaction_records",
            postgres_event_table="agent_flow_events",
        )
    )
    store = PostgresCompactionRecordStore(settings)
    monkeypatch.setattr(
        "src.runtime.postgres_compaction_record_store.postgres_connect",
        lambda _settings: Connection(cursor),
    )

    assert store._uncompacted_event_count_sync() == 7
    statement = " ".join(cursor.statements[-1][0].split())
    assert "MAX(event_sequence)" in statement
    assert "MAX(last_compacted_event_sequence)" in statement
    assert "GREATEST(durable.latest_sequence - COALESCE(compacted.watermark, 0)" in statement
    assert "LEFT JOIN compacted ON compacted.session_id = durable.session_id" in statement
