from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.application.test_runs.run_store import PostgresTestRunStore
from src.schemas.run_management import (
    TestRunItemRecord as _TestRunItemRecord,
    TestRunRecord as _TestRunRecord,
    TestRunStats as _TestRunStats,
)


class _ContextManager:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *args):
        return False


def test_postgres_run_and_outbox_use_the_same_connection(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            calls.append(("execute", sql, params))

        def executemany(self, sql, params):
            calls.append(("executemany", sql, params))

    class Connection:
        def cursor(self):
            return Cursor()

    connection = Connection()
    monkeypatch.setattr(
        "src.application.test_runs.run_store.postgres_connect",
        lambda settings: _ContextManager(connection),
    )
    settings = SimpleNamespace(
        database=SimpleNamespace(
            postgres_test_run_table="runs",
            postgres_test_run_item_table="run_items",
        )
    )
    store = PostgresTestRunStore(settings)
    now = datetime.now(timezone.utc)
    run = _TestRunRecord(
        id="run-1",
        project_id="project-1",
        suite_id="suite-1",
        mode_key="api_testing",
        stats=_TestRunStats(total=1, queued=1),
        created_at=now,
        updated_at=now,
    )
    item = _TestRunItemRecord(
        id="item-1",
        run_id=run.id,
        case_id="case-1",
        case_version_id="version-1",
        position=1,
        created_at=now,
        updated_at=now,
    )

    detail = store._create_run_with_outbox_sync(
        run,
        [item],
        "outbox",
        "test_run_dispatch:run-1",
        "qa:tasks:test",
        {"task_id": "task_test_run_run-1"},
    )

    assert detail.run.id == run.id
    assert [call[0] for call in calls] == ["execute", "executemany", "execute"]
    assert "INSERT INTO outbox" in calls[-1][1]
    assert "ON CONFLICT (event_key) DO NOTHING" in calls[-1][1]
