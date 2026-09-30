from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from src.infrastructure.sqlalchemy_runtime import (
    SQLAlchemyCursor,
    SQLAlchemyCursorConnection,
)


class FailingMappings:
    def all(self):
        raise RuntimeError("database read failed")

    def fetchone(self):
        raise RuntimeError("database read failed")

    def fetchall(self):
        raise RuntimeError("database read failed")


class FirstRowMappings:
    def all(self):
        raise AssertionError("fetchone must not fetch the entire result")

    def fetchone(self):
        return {"id": 1}

    def fetchall(self):
        return [{"id": 2}]


def fake_cursor(mappings):
    result = SimpleNamespace(
        returns_rows=True,
        rowcount=-1,
        lastrowid=None,
        mappings=lambda: mappings,
        close=lambda: None,
    )
    connection = SimpleNamespace(exec_driver_sql=lambda statement: result)
    return SQLAlchemyCursor(connection, "postgres")


@pytest.mark.parametrize("fetch_method", ["fetchone", "fetchall"])
def test_database_fetch_failure_is_not_returned_as_empty_result(
    fetch_method: str, caplog: pytest.LogCaptureFixture
):
    cursor = fake_cursor(FailingMappings())

    with pytest.raises(RuntimeError, match="database read failed"), cursor:
        cursor.execute("SELECT id FROM failed_read")
        getattr(cursor, fetch_method)()
    assert caplog.records[-1].operation == fetch_method
    assert caplog.records[-1].exc_info is not None


def test_fetchone_reads_only_first_row():
    with fake_cursor(FirstRowMappings()) as cursor:
        cursor.execute("SELECT id FROM records")
        assert cursor.fetchone() == {"id": 1}
        assert cursor.fetchall() == [{"id": 2}]


def test_sqlite_result_keeps_mapping_rows_and_cursor_reuse():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection, SQLAlchemyCursor(connection, "postgres") as cursor:
            cursor.execute("SELECT 1 AS id UNION ALL SELECT 2 AS id")
            assert cursor.fetchone()["id"] == 1
            assert [row["id"] for row in cursor.fetchall()] == [2]
            assert cursor.fetchone() is None
            cursor.execute("SELECT 3 AS id")
            assert cursor.fetchone()["id"] == 3
    finally:
        engine.dispose()


def test_postgres_select_does_not_probe_unsupported_lastrowid():
    class PostgresSelectResult:
        returns_rows = True
        rowcount = 1

        def __init__(self):
            self.driver_cursor_closed = False

        @property
        def lastrowid(self):
            self.driver_cursor_closed = True
            raise AttributeError("PostgreSQL cursor has no lastrowid")

        def mappings(self):
            return self

        def fetchone(self):
            if self.driver_cursor_closed:
                raise RuntimeError("cursor is closed")
            return {"value": 1}

        def close(self):
            pass

    result = PostgresSelectResult()
    connection = SimpleNamespace(exec_driver_sql=lambda statement: result)

    with SQLAlchemyCursorConnection(connection, "postgres").cursor() as cursor:
        cursor.execute("SELECT 1 AS value")
        assert cursor.fetchone()["value"] == 1
    assert not result.driver_cursor_closed


def test_mysql_insert_keeps_lastrowid():
    result = SimpleNamespace(returns_rows=False, rowcount=1, lastrowid=42, close=lambda: None)
    connection = SimpleNamespace(exec_driver_sql=lambda statement: result)

    with SQLAlchemyCursorConnection(connection, "mysql").cursor() as cursor:
        cursor.execute("INSERT INTO records (value) VALUES (1)")
        assert cursor.rowcount == 1
        assert cursor.lastrowid == 42
