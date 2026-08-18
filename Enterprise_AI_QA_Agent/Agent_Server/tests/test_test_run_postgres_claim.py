from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from importlib import import_module

from src.core.config import Settings


def test_postgres_run_refresh_applies_status_delta_without_scanning_items():
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    run = schemas.TestRunRecord(
        id="00000000-0000-0000-0000-000000000101",
        project_id="00000000-0000-0000-0000-000000000102",
        suite_id="00000000-0000-0000-0000-000000000103",
        mode_key="api_testing",
        stats={"total": 2, "queued": 1, "claimed": 1},
        created_at=now,
        updated_at=now,
    )

    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters=None):
            self.calls.append((" ".join(statement.split()), parameters))

        def fetchone(self):
            return {
                "record": run.model_copy(
                    update={
                        "status": "running",
                        "stats": schemas.TestRunStats(total=2, claimed=2),
                        "updated_at": now,
                        "started_at": now,
                    }
                ).model_dump(mode="json")
            }

    cursor = Cursor()
    store = store_module.PostgresTestRunStore(Settings())

    refreshed = store._refresh_run_in_cursor(
        cursor,
        run.id,
        now,
        status_deltas=[("queued", "claimed")],
    )

    assert refreshed.stats.model_dump() == {
        "total": 2,
        "queued": 0,
        "claimed": 2,
        "running": 0,
        "waiting_approval": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
        "blocked": 0,
        "skipped": 0,
        "cancelled": 0,
    }
    assert refreshed.status == "running"
    assert not any("GROUP BY status" in statement for statement, _ in cursor.calls)


def test_postgres_run_delta_refresh_uses_one_atomic_update(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    run = schemas.TestRunRecord(
        id="00000000-0000-0000-0000-000000000111",
        project_id="00000000-0000-0000-0000-000000000112",
        suite_id="00000000-0000-0000-0000-000000000113",
        mode_key="api_testing",
        status="running",
        stats={"total": 1, "running": 1},
        created_at=now,
        updated_at=now,
        started_at=now,
    )
    completed = run.model_copy(
        update={
            "status": "completed",
            "stats": schemas.TestRunStats(total=1, passed=1),
            "updated_at": now,
            "completed_at": now,
        }
    )

    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters=None):
            self.calls.append((" ".join(statement.split()), parameters))

        def fetchone(self):
            return {"record": completed.model_dump(mode="json")}

    cursor = Cursor()
    store = store_module.PostgresTestRunStore(Settings())

    def unexpected_call(*args, **kwargs):
        raise AssertionError("delta refresh must not use a separate run read/write")

    monkeypatch.setattr(store, "_lock_run", unexpected_call)
    monkeypatch.setattr(store, "_write_run", unexpected_call)

    refreshed = store._refresh_run_in_cursor(
        cursor,
        run.id,
        now,
        status_deltas=[("running", "passed")],
    )

    assert refreshed == completed
    assert len(cursor.calls) == 1
    assert cursor.calls[0][0].startswith("UPDATE agent_test_runs")
    assert cursor.calls[0][0].endswith("RETURNING record")


def test_postgres_regression_candidates_query_only_eligible_results(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    run_id = "00000000-0000-0000-0000-000000000011"
    item = schemas.TestRunItemRecord(
        id="00000000-0000-0000-0000-000000000014",
        run_id=run_id,
        case_id="00000000-0000-0000-0000-000000000015",
        case_version_id="00000000-0000-0000-0000-000000000016",
        position=1,
        status="failed",
        result_id="00000000-0000-0000-0000-000000000020",
        created_at=now,
        updated_at=now,
    )
    result = schemas.TestCaseResultRecord(
        id=item.result_id,
        run_id=run_id,
        run_item_id=item.id,
        case_id=item.case_id,
        case_version_id=item.case_version_id,
        attempt_id="00000000-0000-0000-0000-000000000017",
        attempt_no=1,
        status="failed",
        summary="failed",
        payload_hash="a" * 64,
        created_at=now,
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
            self.calls.append((normalized, parameters))
            self.rows = [
                {
                    "result_id": result.id,
                    "run_item_id": result.run_item_id,
                    "case_id": result.case_id,
                    "case_version_id": result.case_version_id,
                    "status": result.status,
                    "run_item_position": item.position,
                }
            ]

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

    candidates = asyncio.run(
        store_module.PostgresTestRunStore(Settings()).list_regression_candidates(
            run_id=run_id,
            result_ids=None,
        )
    )

    statement, parameters = cursor.calls[0]
    assert "FROM agent_test_case_results AS result" in statement
    assert "LEFT JOIN agent_test_run_items AS item" in statement
    assert "result.status IN ('failed', 'error', 'blocked')" in statement
    assert ".record" not in statement
    assert "agent_test_run_attempts" not in statement
    assert parameters == (run_id,)
    assert len(candidates) == 1
    assert candidates[0].model_dump() == {
        "result_id": result.id,
        "run_item_id": result.run_item_id,
        "case_id": result.case_id,
        "case_version_id": result.case_version_id,
        "status": result.status,
        "run_item_position": item.position,
    }


def test_postgres_explicit_regression_candidates_keep_passed_and_filter_invalid_ids(
    monkeypatch,
):
    store_module = import_module("src.application.test_runs.run_store")
    run_id = "00000000-0000-0000-0000-000000000011"
    result_id = "00000000-0000-0000-0000-000000000020"

    class Cursor:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            self.calls.append((" ".join(statement.split()), parameters))

        def fetchall(self):
            return [
                {
                    "result_id": result_id,
                    "run_item_id": "00000000-0000-0000-0000-000000000014",
                    "case_id": "00000000-0000-0000-0000-000000000015",
                    "case_version_id": "00000000-0000-0000-0000-000000000016",
                    "status": "passed",
                    "run_item_position": 1,
                }
            ]

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

    candidates = asyncio.run(
        store_module.PostgresTestRunStore(Settings()).list_regression_candidates(
            run_id=run_id,
            result_ids=[result_id, "not-a-uuid"],
        )
    )

    statement, parameters = cursor.calls[0]
    assert "result.id = ANY(%s::uuid[])" in statement
    assert "result.status IN" not in statement
    assert parameters == (run_id, [result_id])
    assert [candidate.status for candidate in candidates] == ["passed"]


def test_postgres_regression_failure_feed_uses_keyset_and_lateral_summary(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    project_id = "00000000-0000-0000-0000-000000000001"
    cursor_id = "00000000-0000-0000-0000-000000000099"

    class Cursor:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            self.calls.append((" ".join(statement.split()), parameters))

        def fetchall(self):
            rows = []
            for index in range(2):
                rows.append(
                    {
                        "source_result_id": f"00000000-0000-0000-0000-{index + 10:012d}",
                        "source_run_id": "00000000-0000-0000-0000-000000000002",
                        "source_run_status": "completed",
                        "source_run_created_at": now,
                        "case_id": f"00000000-0000-0000-0000-{index + 20:012d}",
                        "case_version_id": f"00000000-0000-0000-0000-{index + 30:012d}",
                        "mode_key": "api_testing",
                        "failure_status": "failed",
                        "summary": "failed",
                        "error_message": None,
                        "failed_at": now,
                        "evidence_count": 1,
                        "artifact_count": 1,
                        "verification_count": 1,
                        "has_actual": True,
                        "regression_batch_count": 1,
                        "latest_run_id": "00000000-0000-0000-0000-000000000003",
                        "latest_run_status": "queued",
                        "latest_run_item_id": "00000000-0000-0000-0000-000000000004",
                        "latest_item_status": "queued",
                        "latest_result_id": None,
                        "latest_result_status": None,
                        "latest_case_version_id": f"00000000-0000-0000-0000-{index + 30:012d}",
                        "latest_run_created_at": now,
                        "latest_item_updated_at": now,
                    }
                )
            return rows

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

    records, has_more = asyncio.run(
        store_module.PostgresTestRunStore(Settings()).list_regression_failures(
            project_id=project_id,
            failure_status="failed",
            mode_key="api_testing",
            cursor_created_at=now,
            cursor_id=cursor_id,
            limit=1,
        )
    )

    statement, parameters = cursor.calls[0]
    assert "JOIN agent_test_runs AS source_run" in statement
    assert "LEFT JOIN LATERAL" in statement
    assert "(result.created_at, result.id) < (%s, %s::uuid)" in statement
    assert "ORDER BY result.created_at DESC, result.id DESC" in statement
    assert "LIMIT %s" in statement
    assert parameters == (project_id, "failed", "api_testing", now, cursor_id, 2)
    assert len(records) == 1
    assert records[0].regression_batch_count == 1
    assert records[0].latest_regression.item_status == "queued"
    assert has_more is True


def test_postgres_regression_batches_use_keyset_timeline(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    source_result_id = "00000000-0000-0000-0000-000000000001"
    cursor_id = "00000000-0000-0000-0000-000000000099"

    class Cursor:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            self.calls.append((" ".join(statement.split()), parameters))

        def fetchall(self):
            return [
                {
                    "run_id": f"00000000-0000-0000-0000-{index + 10:012d}",
                    "run_kind": "regression",
                    "run_status": "queued",
                    "parent_run_id": "00000000-0000-0000-0000-000000000002",
                    "run_item_id": f"00000000-0000-0000-0000-{index + 20:012d}",
                    "item_status": "queued",
                    "result_id": None,
                    "result_status": None,
                    "case_version_id": f"00000000-0000-0000-0000-{index + 30:012d}",
                    "created_at": now,
                    "updated_at": now,
                }
                for index in range(2)
            ]

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

    records, has_more = asyncio.run(
        store_module.PostgresTestRunStore(Settings()).list_regression_batches(
            source_result_id=source_result_id,
            cursor_created_at=now,
            cursor_id=cursor_id,
            limit=1,
        )
    )

    statement, parameters = cursor.calls[0]
    assert "WHERE child_item.regression_source_result_id = %s" in statement
    assert "(child_run.created_at, child_item.id) < (%s, %s::uuid)" in statement
    assert "ORDER BY child_run.created_at DESC, child_item.id DESC" in statement
    assert "LIMIT %s" in statement
    assert parameters == (source_result_id, now, cursor_id, 2)
    assert len(records) == 1
    assert records[0].run_kind == "regression"
    assert records[0].item_status == "queued"
    assert has_more is True


def test_postgres_get_result_reads_one_result_record(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    result_id = "00000000-0000-0000-0000-000000000001"
    result_record = {
        "id": result_id,
        "run_id": "00000000-0000-0000-0000-000000000002",
        "run_item_id": "00000000-0000-0000-0000-000000000003",
        "case_id": "00000000-0000-0000-0000-000000000004",
        "case_version_id": "00000000-0000-0000-0000-000000000005",
        "attempt_id": "00000000-0000-0000-0000-000000000006",
        "attempt_no": 1,
        "status": "failed",
        "summary": "failed",
        "payload_hash": "a" * 64,
        "created_at": now.isoformat(),
    }

    class Cursor:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            self.calls.append((" ".join(statement.split()), parameters))

        def fetchone(self):
            return {"record": result_record}

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

    result = asyncio.run(
        store_module.PostgresTestRunStore(Settings()).get_result(result_id)
    )

    assert cursor.calls == [
        ("SELECT record FROM agent_test_case_results WHERE id = %s", (result_id,))
    ]
    assert result.id == result_id
    assert result.status == "failed"


def test_postgres_initialize_adds_queryable_regression_link_columns(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")

    class Cursor:
        def __init__(self):
            self.statements = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            self.statements.append(" ".join(statement.split()))

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

    store_module.PostgresTestRunStore(Settings())._initialize_sync()

    assert any(
        "ALTER TABLE agent_test_runs ADD COLUMN IF NOT EXISTS parent_run_id UUID NULL"
        in statement
        for statement in cursor.statements
    )
    assert any(
        "ALTER TABLE agent_test_run_items ADD COLUMN IF NOT EXISTS "
        "regression_source_result_id UUID NULL" in statement
        for statement in cursor.statements
    )
    assert any(
        "ALTER TABLE agent_test_case_results ADD COLUMN IF NOT EXISTS "
        "regression_source_result_id UUID NULL" in statement
        for statement in cursor.statements
    )
    assert any(
        "ON agent_test_runs (parent_run_id)" in statement
        for statement in cursor.statements
    )
    assert any(
        "ON agent_test_run_items (regression_source_result_id)" in statement
        for statement in cursor.statements
    )
    assert any(
        "ON agent_test_case_results (regression_source_result_id)" in statement
        for statement in cursor.statements
    )
    assert any(
        "ON agent_test_case_results (run_id, run_item_id, id) "
        "INCLUDE (status, case_id, case_version_id) "
        "WHERE status IN ('failed', 'error', 'blocked')" in statement
        for statement in cursor.statements
    )
    assert any(
        "ON agent_test_case_results (run_id, created_at DESC, id DESC) "
        "INCLUDE (status, case_id, case_version_id, run_item_id) "
        "WHERE status IN ('failed', 'error', 'blocked')" in statement
        for statement in cursor.statements
    )
    assert any(
        "ON agent_test_run_items "
        "(regression_source_result_id, updated_at DESC, id DESC) "
        "INCLUDE (run_id, status, result_id, case_version_id) "
        "WHERE regression_source_result_id IS NOT NULL" in statement
        for statement in cursor.statements
    )


def test_postgres_create_and_update_sync_regression_link_columns(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    parent_run_id = "00000000-0000-0000-0000-000000000010"
    source_result_id = "00000000-0000-0000-0000-000000000020"
    run = schemas.TestRunRecord(
        id="00000000-0000-0000-0000-000000000011",
        project_id="00000000-0000-0000-0000-000000000012",
        suite_id="00000000-0000-0000-0000-000000000013",
        run_kind="regression",
        mode_key="api_testing",
        parent_run_id=parent_run_id,
        stats={"total": 1, "queued": 1},
        created_at=now,
        updated_at=now,
    )
    item = schemas.TestRunItemRecord(
        id="00000000-0000-0000-0000-000000000014",
        run_id=run.id,
        case_id="00000000-0000-0000-0000-000000000015",
        case_version_id="00000000-0000-0000-0000-000000000016",
        position=1,
        regression_source_result_id=source_result_id,
        created_at=now,
        updated_at=now,
    )

    class Cursor:
        def __init__(self):
            self.execute_calls = []
            self.executemany_calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            self.execute_calls.append((" ".join(statement.split()), parameters))

        def executemany(self, statement, parameters):
            self.executemany_calls.append(
                (" ".join(statement.split()), list(parameters))
            )

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
    store = store_module.PostgresTestRunStore(Settings())

    store._create_run_sync(run, [item])
    store._write_run(cursor, run)
    store._write_item(cursor, item)

    run_insert, run_parameters = next(
        call for call in cursor.execute_calls if call[0].startswith("INSERT INTO agent_test_runs")
    )
    item_insert, item_rows = cursor.executemany_calls[0]
    run_update, run_update_parameters = next(
        call for call in cursor.execute_calls if call[0].startswith("UPDATE agent_test_runs")
    )
    item_update, item_update_parameters = next(
        call for call in cursor.execute_calls if call[0].startswith("UPDATE agent_test_run_items")
    )

    assert "parent_run_id" in run_insert
    assert parent_run_id in run_parameters
    assert "regression_source_result_id" in item_insert
    assert source_result_id in item_rows[0]
    assert "parent_run_id = %s" in run_update
    assert parent_run_id in run_update_parameters
    assert "regression_source_result_id = %s" in item_update
    assert source_result_id in item_update_parameters


def test_postgres_completion_persists_regression_source_result_column(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    lease_token = "lease-token"
    source_result_id = "00000000-0000-0000-0000-000000000020"
    run = schemas.TestRunRecord(
        id="00000000-0000-0000-0000-000000000011",
        project_id="00000000-0000-0000-0000-000000000012",
        suite_id="00000000-0000-0000-0000-000000000013",
        run_kind="regression",
        mode_key="api_testing",
        parent_run_id="00000000-0000-0000-0000-000000000010",
        stats={"total": 1, "running": 1},
        status="running",
        created_at=now,
        updated_at=now,
    )
    item = schemas.TestRunItemRecord(
        id="00000000-0000-0000-0000-000000000014",
        run_id=run.id,
        case_id="00000000-0000-0000-0000-000000000015",
        case_version_id="00000000-0000-0000-0000-000000000016",
        position=1,
        status="running",
        attempt_no=1,
        lease_owner="worker-1",
        lease_token=lease_token,
        lease_expires_at=now + timedelta(minutes=1),
        regression_source_result_id=source_result_id,
        created_at=now,
        updated_at=now,
    )
    attempt = schemas.TestRunAttemptRecord(
        id="00000000-0000-0000-0000-000000000017",
        run_id=run.id,
        run_item_id=item.id,
        attempt_no=1,
        worker_id="worker-1",
        lease_token=lease_token,
        status="running",
        claimed_at=now,
        started_at=now,
    )

    class Cursor:
        def __init__(self):
            self.execute_calls = []
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            normalized = " ".join(statement.split())
            self.execute_calls.append((normalized, parameters))
            if "FROM agent_test_run_items" in normalized and "WHERE id" in normalized:
                self.rows = [{"record": item.model_dump(mode="json")}]
            elif "FROM agent_test_run_attempts" in normalized and "run_item_id" in normalized:
                self.rows = [{"record": attempt.model_dump(mode="json")}]
            elif (
                "FROM agent_test_runs" in normalized
                and "FOR NO KEY UPDATE" in normalized
            ):
                self.rows = [{"record": run.model_dump(mode="json")}]
            elif normalized.startswith("UPDATE agent_test_runs"):
                self.rows = [
                    {
                        "record": run.model_copy(
                            update={
                                "status": "completed",
                                "stats": schemas.TestRunStats(total=1, passed=1),
                                "updated_at": now,
                                "completed_at": now,
                            }
                        ).model_dump(mode="json")
                    }
                ]
            elif "GROUP BY status" in normalized:
                self.rows = [{"status": "passed", "total": 1}]
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
    store = store_module.PostgresTestRunStore(Settings())

    result = store._complete_item_sync(
        item.id,
        lease_token,
        schemas.RunItemCompletion(
            status="passed",
            summary="regression passed",
            payload_hash="a" * 64,
        ),
        now,
    )

    result_insert, result_parameters = next(
        call
        for call in cursor.execute_calls
        if call[0].startswith("INSERT INTO agent_test_case_results")
    )
    assert result.regression_source_result_id == source_result_id
    assert "regression_source_result_id" in result_insert
    assert source_result_id in result_parameters


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
            elif normalized.startswith("UPDATE agent_test_runs"):
                self._rows = [
                    {
                        "record": run.model_copy(
                            update={
                                "status": "running",
                                "stats": schemas.TestRunStats(total=2, claimed=2),
                                "updated_at": now,
                                "started_at": now,
                            }
                        ).model_dump(mode="json")
                    }
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


def test_postgres_run_refresh_uses_non_key_update_lock():
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    run = schemas.TestRunRecord(
        id="00000000-0000-0000-0000-000000000021",
        project_id="00000000-0000-0000-0000-000000000022",
        suite_id="00000000-0000-0000-0000-000000000023",
        mode_key="api_testing",
        created_at=now,
        updated_at=now,
    )

    class Cursor:
        def __init__(self):
            self.statement = ""

        def execute(self, statement, parameters=None):
            self.statement = " ".join(statement.split())

        def fetchone(self):
            return {"record": run.model_dump(mode="json")}

    cursor = Cursor()
    locked = store_module.PostgresTestRunStore(Settings())._lock_run(cursor, run.id)

    assert locked.id == run.id
    assert cursor.statement.endswith("FOR NO KEY UPDATE")


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
        if "FROM agent_test_runs" in statement
        and ("FOR UPDATE" in statement or "FOR NO KEY UPDATE" in statement)
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
        if "FROM agent_test_runs" in statement
        and ("FOR UPDATE" in statement or "FOR NO KEY UPDATE" in statement)
    ]
    assert prior_run_locks == []


def test_postgres_waiting_approval_resume_reuses_attempt_and_first_start(monkeypatch):
    store_module = import_module("src.application.test_runs.run_store")
    schemas = import_module("src.schemas.run_management")
    started_at = datetime(2026, 8, 18, tzinfo=timezone.utc)
    resumed_at = started_at + timedelta(minutes=5)
    item = schemas.TestRunItemRecord(
        id="00000000-0000-0000-0000-000000000031",
        run_id="00000000-0000-0000-0000-000000000032",
        case_id="00000000-0000-0000-0000-000000000033",
        case_version_id="00000000-0000-0000-0000-000000000034",
        position=1,
        status="running",
        attempt_no=1,
        lease_owner="worker-1",
        lease_token="lease-approval-1",
        lease_expires_at=resumed_at,
        heartbeat_at=started_at,
        created_at=started_at,
        updated_at=started_at,
        started_at=started_at,
    )
    attempt = schemas.TestRunAttemptRecord(
        id="00000000-0000-0000-0000-000000000035",
        run_id=item.run_id,
        run_item_id=item.id,
        attempt_no=1,
        worker_id="worker-1",
        lease_token=item.lease_token,
        status="running",
        claimed_at=started_at,
        started_at=started_at,
        heartbeat_at=started_at,
    )
    state = {"item": item, "attempt": attempt}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

    class Context:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(store_module, "postgres_connect", lambda settings: Context())
    store = store_module.PostgresTestRunStore(Settings())
    monkeypatch.setattr(store, "_lock_item", lambda cur, item_id: state["item"])
    monkeypatch.setattr(
        store,
        "_lock_attempt",
        lambda cur, item_id, lease_token: state["attempt"],
    )
    monkeypatch.setattr(
        store,
        "_write_item",
        lambda cur, updated: state.__setitem__("item", updated),
    )
    monkeypatch.setattr(
        store,
        "_write_attempt",
        lambda cur, updated: state.__setitem__("attempt", updated),
    )
    monkeypatch.setattr(
        store,
        "_refresh_run_in_cursor",
        lambda cur, run_id, now, **kwargs: None,
    )

    waiting = store._mark_waiting_approval_sync(
        item.id,
        item.lease_token,
        "approval-1",
        "job-1",
        started_at,
    )
    resumed = store._resume_waiting_approval_sync(
        item.id,
        "approval-1",
        90,
        resumed_at,
    )
    replayed = store._resume_waiting_approval_sync(
        item.id,
        "approval-1",
        90,
        resumed_at,
    )
    restarted = store._start_item_sync(item.id, item.lease_token, resumed_at)

    assert waiting.status == "waiting_approval"
    assert waiting.lease_expires_at is None
    assert waiting.approval_id == "approval-1"
    assert waiting.tool_job_id == "job-1"
    assert resumed.status == "claimed"
    assert replayed.lease_token == item.lease_token
    assert state["attempt"].id == attempt.id
    assert state["attempt"].attempt_no == attempt.attempt_no
    assert restarted.started_at == started_at
    assert state["attempt"].started_at == started_at
