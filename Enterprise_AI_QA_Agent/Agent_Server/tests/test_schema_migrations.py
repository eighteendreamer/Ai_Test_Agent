"""Actual Alembic commands and isolated PostgreSQL schemas, not store initialization."""

from __future__ import annotations

import io
import logging
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from src.core.config import get_settings
from src.infrastructure.sqlalchemy_runtime import postgres_engine
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from tests.live_postgres_config import LivePostgresTestConfig

ROOT = Path(__file__).resolve().parents[1]
REVISION = "20261002_0001"


def configuration(settings=None, *, output=None):
    config = Config(ROOT / "alembic.ini", output_buffer=output)
    config.attributes["settings"] = settings or get_settings()
    return config


def test_offline_upgrade_contains_real_ddl_and_version_without_a_database_url():
    application_logger = logging.getLogger("src.application.intent.semantic_intent_service")
    assert not application_logger.disabled
    output = io.StringIO()
    command.upgrade(configuration(output=output), "head", sql=True)
    assert not application_logger.disabled
    sql = output.getvalue()
    assert "CREATE TABLE IF NOT EXISTS agent_task_outbox" in sql
    assert "ADD COLUMN IF NOT EXISTS claimed_by TEXT" in sql
    assert "ADD COLUMN IF NOT EXISTS claimed_until TIMESTAMPTZ" in sql
    assert REVISION in sql and "INSERT INTO alembic_version" in sql
    assert "postgresql+psycopg" not in sql


@pytest.fixture
def live_schema():
    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires actual PostgreSQL; migration-core enables it")
    settings = get_settings().model_copy(deep=True)
    schema = f"migration_test_{uuid4().hex[:12]}"
    engine = postgres_engine(settings)
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    settings.database.postgres_task_outbox_table = f"{schema}.task_outbox"
    try:
        yield settings, engine, schema
    finally:
        assert schema.startswith("migration_test_") and len(schema) == 27
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def migrate(connection, settings, schema):
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
    config = configuration(settings)
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
    return config


def test_current_command_uses_the_real_configured_credentials(live_schema):
    # Same read-only command as the observed masked-password authentication failure.
    settings, _, _ = live_schema
    command.current(configuration(settings))


@pytest.mark.asyncio
@pytest.mark.parametrize("table_name", ["task_outbox", "TASK_OUTBOX", '"Task.Outbox"'])
async def test_fresh_migration_is_usable_by_real_outbox_without_initialize(live_schema, table_name):
    settings, engine, schema = live_schema
    settings.database.postgres_task_outbox_table = f"{schema}.{table_name}"
    actual_name = table_name.strip('"') if table_name.startswith('"') else table_name.lower()
    with engine.begin() as connection:
        migrate(connection, settings, schema)
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == REVISION
        columns = {column["name"] for column in inspect(connection).get_columns(actual_name, schema=schema)}
        assert {"claimed_by", "claimed_until", "event_key", "payload", "published_at"} <= columns
    outbox = PostgresTaskOutbox(settings)
    assert await outbox.enqueue(event_key="normal-event", stream="normal-stream", payload={"value": 1})
    assert not await outbox.enqueue(event_key="normal-event", stream="normal-stream", payload={"value": 1})
    claimed = await outbox.claim(relay_id="normal-relay")
    assert len(claimed) == 1 and claimed[0].payload == {"value": 1}
    assert await outbox.mark_published(claimed[0].id, relay_id="normal-relay")


def test_existing_legacy_rows_survive_upgrade_and_repeated_upgrade(live_schema):
    settings, engine, schema = live_schema
    with engine.begin() as connection:
        connection.exec_driver_sql(f"""CREATE TABLE {schema}.task_outbox (
            id BIGSERIAL PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
            stream TEXT NOT NULL, payload JSONB NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
            published_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
        connection.exec_driver_sql(f"""INSERT INTO {schema}.task_outbox
            (event_key, stream, payload, attempts) VALUES ('legacy', 'legacy-stream', '{{"kept": true}}', 2)""")
        migrate(connection, settings, schema)
        migrate(connection, settings, schema)
        row = connection.exec_driver_sql("SELECT event_key, payload, attempts, claimed_by FROM task_outbox").one()
        assert tuple(row) == ("legacy", {"kept": True}, 2, None)
        assert connection.exec_driver_sql("SELECT count(*) FROM alembic_version").scalar_one() == 1


def test_caller_transaction_rollback_removes_schema_changes_and_version(live_schema):
    """Explicit caller rollback contract, not a claimed application failure."""
    settings, engine, schema = live_schema
    with engine.connect() as connection:
        transaction = connection.begin()
        migrate(connection, settings, schema)
        transaction.rollback()
    with engine.connect() as connection:
        assert not inspect(connection).has_table("task_outbox", schema=schema)
        assert not inspect(connection).has_table("alembic_version", schema=schema)


def test_downgrade_refuses_to_delete_preexisting_durable_work(live_schema):
    settings, engine, schema = live_schema
    with engine.begin() as connection:
        migrate(connection, settings, schema)
        connection.exec_driver_sql("""INSERT INTO task_outbox (event_key,stream,payload)
            VALUES ('kept', 'normal-stream', '{}')""")
    with pytest.raises(RuntimeError, match="cannot be destructively downgraded"), engine.begin() as connection:
        config = migrate(connection, settings, schema)
        command.downgrade(config, "base")
    with engine.connect() as connection:
        assert connection.exec_driver_sql(f"SELECT event_key FROM {schema}.task_outbox").scalar_one() == "kept"
        assert connection.exec_driver_sql(f"SELECT version_num FROM {schema}.alembic_version").scalar_one() == REVISION


CHILD_MIGRATION = '''
import sys
from alembic import command
from alembic.config import Config
from src.core.config import get_settings
from src.infrastructure.sqlalchemy_runtime import postgres_engine
schema = sys.argv[1]
assert schema.startswith("migration_test_") and len(schema) == 27
settings = get_settings().model_copy(deep=True)
settings.database.postgres_task_outbox_table = schema + ".task_outbox"
with postgres_engine(settings).begin() as connection:
    connection.exec_driver_sql('SET LOCAL search_path TO "' + schema + '"')
    config = Config("alembic.ini")
    config.attributes.update(settings=settings, connection=connection)
    command.upgrade(config, "head")
    print(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one())
'''


def test_two_real_migration_processes_serialize_first_upgrade(live_schema):
    _, engine, schema = live_schema
    processes = [subprocess.Popen(
        [sys.executable, "-c", CHILD_MIGRATION, schema], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ) for _ in range(2)]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stdout + stderr
            assert stdout.strip() == REVISION
        with engine.connect() as connection:
            assert connection.exec_driver_sql(f"SELECT count(*) FROM {schema}.alembic_version").scalar_one() == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
