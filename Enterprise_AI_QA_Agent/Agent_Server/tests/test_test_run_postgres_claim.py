from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from importlib import import_module

from src.core.config import Settings


def test_postgres_claim_uses_skip_locked_and_one_transaction_connection(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    run = schemas.TestRunRecord(
        id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        suite_id="00000000-0000-0000-0000-000000000003",
        mode_key="api_testing",
        stats={"total": 2, "queued": 2},
        created_at=now,
        updated_at=now,
    )
    items = [
        schemas.TestRunItemRecord(
            id=f"10000000-0000-0000-0000-{index:012d}",
            run_id=run.id,
            case_id=f"20000000-0000-0000-0000-{index:012d}",
            case_version_id=f"30000000-0000-0000-0000-{index:012d}",
            position=index,
            created_at=now,
            updated_at=now,
        )
        for index in range(1, 3)
    ]

    class FakeCursor:
        def __init__(self):
            self.execute_calls = []
            self.executemany_calls = []
            self._rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            normalized = " ".join(statement.split())
            self.execute_calls.append((normalized, parameters))
            if "FOR UPDATE SKIP LOCKED" in normalized:
                self._rows = [
                    {"record": item.model_dump(mode="json")} for item in items
                ]
            elif "GROUP BY status" in normalized:
                self._rows = [{"status": "claimed", "total": 2}]
            elif "FROM agent_test_runs" in normalized and "WHERE id" in normalized:
                self._rows = [{"record": run.model_dump(mode="json")}]
            else:
                self._rows = []

        def executemany(self, statement, parameters):
            self.executemany_calls.append(
                (" ".join(statement.split()), list(parameters))
            )

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return list(self._rows)

    class FakeConnection:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

    class FakeContext:
        def __init__(self, connection):
            self.connection = connection
            self.enter_count = 0

        def __enter__(self):
            self.enter_count += 1
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    cursor = FakeCursor()
    context = FakeContext(FakeConnection(cursor))
    monkeypatch.setattr(store_module, "postgres_connect", lambda settings: context)
    store = store_module.PostgresTestRunStore(Settings())

    claims = asyncio.run(
        store.claim_items(
            run_id=run.id,
            worker_id="worker-a",
            limit=2,
            lease_seconds=60,
            now=now,
        )
    )

    assert context.enter_count == 1
    skip_locked_index = next(
        index
        for index, call in enumerate(cursor.execute_calls)
        if "FOR UPDATE SKIP LOCKED" in call[0]
    )
    initial_run_reads = [
        call[0]
        for call in cursor.execute_calls[:skip_locked_index]
        if "FROM agent_test_runs" in call[0]
    ]
    assert initial_run_reads
    assert all("FOR UPDATE" not in statement for statement in initial_run_reads)
    assert len(claims) == 2
    assert len({attempt.lease_token for _, attempt in claims}) == 2
    assert len(cursor.executemany_calls) == 2
    assert [len(call[1]) for call in cursor.executemany_calls] == [2, 2]


def test_postgres_expiry_recovery_does_not_lock_run_before_items(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    run = schemas.TestRunRecord(
        id="00000000-0000-0000-0000-000000000011",
        project_id="00000000-0000-0000-0000-000000000012",
        suite_id="00000000-0000-0000-0000-000000000013",
        mode_key="api_testing",
        stats={"total": 1, "claimed": 1},
        created_at=now,
        updated_at=now,
    )

    class Cursor:
        def __init__(self):
            self.calls = []
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            normalized = " ".join(statement.split())
            self.calls.append(normalized)
            if "FROM agent_test_runs" in normalized:
                self.rows = [{"record": run.model_dump(mode="json")}]
            else:
                self.rows = []

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return list(self.rows)

    class Connection:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

    class Context:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    cursor = Cursor()
    monkeypatch.setattr(
        store_module,
        "postgres_connect",
        lambda settings: Context(Connection(cursor)),
    )

    recovered = asyncio.run(
        store_module.PostgresTestRunStore(Settings()).recover_expired(run.id, now)
    )

    item_lock_index = next(
        index
        for index, statement in enumerate(cursor.calls)
        if "FROM agent_test_run_items" in statement and "FOR UPDATE SKIP LOCKED" in statement
    )
    prior_run_locks = [
        statement
        for statement in cursor.calls[:item_lock_index]
        if "FROM agent_test_runs" in statement and "FOR UPDATE" in statement
    ]
    assert recovered == 0
    assert prior_run_locks == []


def test_postgres_cancel_uses_the_same_item_then_run_lock_order(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    run = schemas.TestRunRecord(
        id="00000000-0000-0000-0000-000000000021",
        project_id="00000000-0000-0000-0000-000000000022",
        suite_id="00000000-0000-0000-0000-000000000023",
        mode_key="api_testing",
        stats={"total": 1, "queued": 1},
        created_at=now,
        updated_at=now,
    )

    class Cursor:
        def __init__(self):
            self.calls = []
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            normalized = " ".join(statement.split())
            self.calls.append(normalized)
            if "FROM agent_test_runs" in normalized:
                self.rows = [{"record": run.model_dump(mode="json")}]
            elif "GROUP BY status" in normalized:
                self.rows = [{"status": "cancelled", "total": 1}]
            else:
                self.rows = []

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return list(self.rows)

    class Connection:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

    class Context:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    cursor = Cursor()
    monkeypatch.setattr(
        store_module,
        "postgres_connect",
        lambda settings: Context(Connection(cursor)),
    )

    asyncio.run(
        store_module.PostgresTestRunStore(Settings()).cancel_run(
            run.id,
            "operator stop",
            now,
        )
    )

    item_lock_index = next(
        index
        for index, statement in enumerate(cursor.calls)
        if "FROM agent_test_run_items" in statement and "FOR UPDATE" in statement
    )
    prior_run_locks = [
        statement
        for statement in cursor.calls[:item_lock_index]
        if "FROM agent_test_runs" in statement and "FOR UPDATE" in statement
    ]
    assert prior_run_locks == []
