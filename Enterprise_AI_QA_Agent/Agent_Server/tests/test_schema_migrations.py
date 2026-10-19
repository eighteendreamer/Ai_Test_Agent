"""Actual Alembic commands and isolated PostgreSQL schemas, not store initialization."""

from __future__ import annotations

import io
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from src.core.config import get_settings
from src.infrastructure.sqlalchemy_runtime import postgres_engine
from src.runtime.postgres_compaction_record_store import PostgresCompactionRecordStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.tool_job import ToolArtifactRecord, ToolJobRecord, ToolJobStatus
from tests.live_postgres_config import LivePostgresTestConfig

ROOT = Path(__file__).resolve().parents[1]
REVISION = "20261002_0001"
TOOL_REVISION = "20261002_0002"
COMPACTION_REVISION = "20261002_0003"
PLAN_REVISION = "20261019_0004"


def configuration(settings=None, *, output=None):
    config = Config(ROOT / "alembic.ini", output_buffer=output)
    config.attributes["settings"] = settings or get_settings()
    return config


def test_offline_upgrade_contains_real_ddl_and_version_without_a_database_url():
    application_logger = logging.getLogger("src.application.intent.semantic_intent_service")
    assert not application_logger.disabled
    output = io.StringIO()
    command.upgrade(configuration(output=output), REVISION, sql=True)
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
    settings.database.postgres_tool_job_table = f"{schema}.tool_jobs"
    settings.database.postgres_tool_artifact_table = f"{schema}.tool_artifacts"
    settings.database.postgres_compaction_table = f"{schema}.compaction_records"
    settings.database.postgres_event_table = f"{schema}.events"
    settings.database.postgres_test_run_table = f"{schema}.test_runs"
    try:
        yield settings, engine, schema
    finally:
        assert schema.startswith("migration_test_") and len(schema) == 27
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def migrate(connection, settings, schema, revision=REVISION):
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
    config = configuration(settings)
    config.attributes["connection"] = connection
    command.upgrade(config, revision)
    return config


def test_plan_archive_offline_upgrade_preserves_record_and_handles_absent_table():
    output = io.StringIO()
    command.upgrade(configuration(output=output), PLAN_REVISION, sql=True)
    sql = output.getvalue()
    assert "ALTER TABLE IF EXISTS agent_test_runs ADD COLUMN IF NOT EXISTS plan_snapshots" in sql
    assert "UPDATE agent_test_runs" not in sql
    assert PLAN_REVISION in sql


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("table_name", ["test_runs", "TEST_RUNS", '"Test.Runs"'])
def test_plan_archive_migration_keeps_original_records_without_fabricating_history(live_schema, existing, table_name):
    settings, engine, schema = live_schema
    settings.database.postgres_test_run_table = table = f"{schema}.{table_name}"
    with engine.begin() as connection:
        if existing:
            connection.exec_driver_sql(f"CREATE TABLE {table} (id integer, record jsonb)")
            connection.exec_driver_sql(f"INSERT INTO {table} VALUES (1, '{{\"old\":true}}'::jsonb)")
        config = migrate(connection, settings, schema, PLAN_REVISION)
        command.upgrade(config, PLAN_REVISION)
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PLAN_REVISION
        if existing:
            row = connection.exec_driver_sql(f"SELECT record, plan_snapshots FROM {table}").one()
            assert row.record == {"old": True} and row.plan_snapshots == {}
        else:
            assert not inspect(connection).has_table(table_name.strip('"') if table_name.startswith('"') else table_name.lower(), schema=schema)
        with pytest.raises(RuntimeError, match="cannot be destructively downgraded"):
            command.downgrade(config, COMPACTION_REVISION)
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == PLAN_REVISION


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
settings.database.postgres_tool_job_table = schema + ".tool_jobs"
settings.database.postgres_tool_artifact_table = schema + ".tool_artifacts"
settings.database.postgres_compaction_table = schema + ".compaction_records"
with postgres_engine(settings).begin() as connection:
    connection.exec_driver_sql('SET LOCAL search_path TO "' + schema + '"')
    config = Config("alembic.ini")
    config.attributes.update(settings=settings, connection=connection)
    command.upgrade(config, sys.argv[2])
    print(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one())
'''


@pytest.mark.parametrize("revision", [REVISION, TOOL_REVISION, COMPACTION_REVISION])
def test_two_real_migration_processes_serialize_first_upgrade(live_schema, revision):
    _, engine, schema = live_schema
    processes = [subprocess.Popen(
        [sys.executable, "-c", CHILD_MIGRATION, schema, revision], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ) for _ in range(2)]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stdout + stderr
            assert stdout.strip() == revision
        with engine.connect() as connection:
            assert connection.exec_driver_sql(f"SELECT count(*) FROM {schema}.alembic_version").scalar_one() == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)


def test_offline_head_includes_all_three_frozen_revisions():
    output = io.StringIO()
    command.upgrade(configuration(output=output), "head", sql=True)
    sql = output.getvalue()
    assert REVISION in sql and TOOL_REVISION in sql
    assert COMPACTION_REVISION in sql
    assert "CREATE TABLE IF NOT EXISTS agent_tool_jobs" in sql
    assert "CREATE TABLE IF NOT EXISTS agent_tool_artifacts" in sql
    assert "heartbeat_at TIMESTAMPTZ" in sql and "storage_mode TEXT" in sql
    assert "CREATE TABLE IF NOT EXISTS agent_compaction_records" in sql
    assert "last_compacted_event_sequence BIGINT NOT NULL DEFAULT 0" in sql
    assert "postgresql+psycopg" not in sql


def normal_job():
    now = datetime.now(UTC)
    return ToolJobRecord(
        id="normal-job", session_id="normal-session", turn_id="normal-turn",
        trace_id="normal-trace", call_id="normal-call", tool_key="normal-tool",
        tool_name="Normal tool", status=ToolJobStatus.queued,
        input_payload={"text": "正常输入"}, created_at=now, updated_at=now,
    )


def normal_artifact():
    return ToolArtifactRecord(
        id="normal-artifact", tool_job_id="normal-job", session_id="normal-session",
        turn_id="normal-turn", trace_id="normal-trace", tool_key="normal-tool",
        artifact_type="text", path="normal-output.txt", created_at=datetime.now(UTC),
        metadata={"__storage_mode": "inline_text", "__content_text": "完整证据正文", "source": "normal-test"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [
    ("tool_jobs", "tool_artifacts"),
    ("TOOL_JOBS", "TOOL_ARTIFACTS"),
    ('"Tool.Jobs"', '"Tool.Artifacts"'),
])
async def test_tool_revision_supports_real_execution_and_evidence_without_initialize(live_schema, names):
    settings, engine, schema = live_schema
    settings.database.postgres_tool_job_table = f"{schema}.{names[0]}"
    settings.database.postgres_tool_artifact_table = f"{schema}.{names[1]}"
    with engine.begin() as connection:
        migrate(connection, settings, schema, TOOL_REVISION)
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == TOOL_REVISION
        for name, expected in zip(names, (
            {("session_id", "created_at"), ("status", "updated_at")},
            {("tool_job_id", "created_at"), ("session_id", "created_at")},
        ), strict=True):
            actual_name = name.strip('"') if name.startswith('"') else name.lower()
            indexes = inspect(connection).get_indexes(actual_name, schema=schema)
            assert {tuple(index["column_names"]) for index in indexes} == expected
    store = PostgresToolJobStore(settings)
    job = normal_job()
    assert (await store.create_job_if_absent(job)).id == job.id
    assert (await store.create_job_if_absent(job)).id == job.id
    assert len(await store.list_jobs(job.session_id)) == 1
    claimed = await store.claim_job_execution(job.id)
    assert claimed is not None and claimed.status == ToolJobStatus.running
    assert await store.claim_job_execution(job.id) is None
    assert (await store.heartbeat_job_execution(job.id)).heartbeat_at is not None
    artifact = normal_artifact()
    await store.save_artifact(artifact)
    finished = await store.finish_job_execution(
        job.id, status=ToolJobStatus.completed, output_payload={"result": "完整结果"},
        summary="执行完成", error_message=None,
    )
    assert finished is not None and finished.status == ToolJobStatus.completed
    assert finished.output_payload == {"result": "完整结果"}
    assert await store.claim_job_execution(job.id) is None
    persisted = await store.get_artifact(artifact.id)
    assert persisted.metadata == artifact.metadata
    assert [item.id for item in await store.list_artifacts(tool_job_id=job.id)] == [artifact.id]
    assert [item.id for item in await store.list_artifacts_for_sessions([job.session_id])] == [artifact.id]


@pytest.mark.asyncio
async def test_tool_upgrade_preserves_existing_rows_and_outbox_revision(live_schema):
    """Current supported unversioned table shape; not a historical deployment sample."""
    settings, engine, schema = live_schema
    with engine.begin() as connection:
        migrate(connection, settings, schema)
        # Independent fixture copied from the pre-migration runtime store at 83ee431.
        connection.exec_driver_sql(f"""CREATE TABLE {schema}.tool_jobs (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
            trace_id TEXT NOT NULL, call_id TEXT NOT NULL, tool_key TEXT NOT NULL,
            tool_name TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
            summary TEXT NOT NULL DEFAULT '', error_message TEXT,
            artifact_count INTEGER NOT NULL DEFAULT 0,
            input_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            output_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
            heartbeat_at TIMESTAMPTZ, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ
        )""")
        connection.exec_driver_sql(f"""CREATE TABLE {schema}.tool_artifacts (
            id TEXT PRIMARY KEY, tool_job_id TEXT NOT NULL, session_id TEXT NOT NULL,
            turn_id TEXT NOT NULL, trace_id TEXT NOT NULL, tool_key TEXT NOT NULL,
            artifact_type TEXT NOT NULL, label TEXT, path TEXT NOT NULL,
            storage_mode TEXT NOT NULL DEFAULT 'path_only', content_text TEXT NOT NULL DEFAULT '',
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb, created_at TIMESTAMPTZ NOT NULL
        )""")
    store = PostgresToolJobStore(settings)
    job = normal_job().model_copy(update={
        "status": ToolJobStatus.completed, "attempt": 3,
        "output_payload": {"result": "已保存结果"}, "artifact_count": 1,
    })
    await store.save_job(job)
    await store.save_artifact(normal_artifact())
    before_job = await store.get_job(job.id)
    before_artifact = await store.get_artifact("normal-artifact")
    with engine.begin() as connection:
        migrate(connection, settings, schema, TOOL_REVISION)
        migrate(connection, settings, schema, TOOL_REVISION)
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == TOOL_REVISION
    assert await store.get_job(job.id) == before_job
    assert await store.get_artifact("normal-artifact") == before_artifact
    assert await store.claim_job_execution(job.id) is None


def test_tool_revision_rollback_keeps_committed_outbox_version(live_schema):
    settings, engine, schema = live_schema
    with engine.begin() as connection:
        migrate(connection, settings, schema)
    with engine.connect() as connection:
        transaction = connection.begin()
        migrate(connection, settings, schema, TOOL_REVISION)
        transaction.rollback()
    with engine.connect() as connection:
        assert inspect(connection).has_table("task_outbox", schema=schema)
        assert not inspect(connection).has_table("tool_jobs", schema=schema)
        assert not inspect(connection).has_table("tool_artifacts", schema=schema)
        assert connection.exec_driver_sql(f"SELECT version_num FROM {schema}.alembic_version").scalar_one() == REVISION


@pytest.mark.asyncio
async def test_tool_downgrade_refuses_to_remove_saved_results_and_evidence(live_schema):
    settings, engine, schema = live_schema
    with engine.begin() as connection:
        migrate(connection, settings, schema, TOOL_REVISION)
    store = PostgresToolJobStore(settings)
    await store.save_job(normal_job())
    await store.save_artifact(normal_artifact())
    with pytest.raises(RuntimeError, match="cannot be destructively downgraded"), engine.begin() as connection:
        config = migrate(connection, settings, schema, TOOL_REVISION)
        command.downgrade(config, REVISION)
    assert (await store.get_job("normal-job")).id == "normal-job"
    assert (await store.get_artifact("normal-artifact")).metadata["__content_text"] == "完整证据正文"
    with engine.connect() as connection:
        assert connection.exec_driver_sql(f"SELECT version_num FROM {schema}.alembic_version").scalar_one() == TOOL_REVISION


@pytest.mark.asyncio
@pytest.mark.parametrize("table_name", ["compaction_records", "COMPACTION_RECORDS", '"Compaction.Records"'])
async def test_compaction_revision_supports_real_claim_and_receipt_without_initialize(live_schema, table_name):
    settings, engine, schema = live_schema
    settings.database.postgres_compaction_table = f"{schema}.{table_name}"
    with engine.begin() as connection:
        migrate(connection, settings, schema, COMPACTION_REVISION)
        # This is an event dependency fixture, not a session schema migration.
        connection.exec_driver_sql(f"""CREATE TABLE {schema}.events (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, event_sequence BIGSERIAL NOT NULL
        )""")
        connection.exec_driver_sql(f"INSERT INTO {schema}.events (id, session_id) VALUES ('event-1', 'session-1')")
        actual_name = table_name.strip('"') if table_name.startswith('"') else table_name.lower()
        indexes = inspect(connection).get_indexes(actual_name, schema=schema)
        assert {tuple(index["column_names"]) for index in indexes} == {
            ("session_id", "updated_at"), ("status", "updated_at"),
        }
    store = PostgresCompactionRecordStore(settings)
    claim = {"compaction_key": "normal-claim", "session_id": "session-1",
             "last_event_id": "event-1", "compression_version": "v1"}
    assert await store.begin(**claim, owner_id="owner-1")
    assert not await store.begin(**claim, owner_id="owner-2")
    assert not await store.renew("normal-claim", owner_id="owner-2")
    assert await store.renew("normal-claim", owner_id="owner-1")
    assert not await store.complete("normal-claim", ["memory-1"], owner_id="owner-2")
    assert await store.complete("normal-claim", ["memory-1"], owner_id="owner-1")
    persisted = await store.get("normal-claim")
    assert persisted.status == "completed" and persisted.memory_ids == ["memory-1"]
    assert persisted.last_compacted_event_sequence == 1 and persisted.completed_at is not None
    assert not await store.begin(**claim, owner_id="owner-2")
    assert await store.uncompacted_event_count() == 0
    retry = dict(claim, compaction_key="retry-claim")
    assert await store.begin(**retry, owner_id="owner-1")
    assert await store.fail("retry-claim", "normal-error-receipt", owner_id="owner-1")
    assert await store.begin(**retry, owner_id="owner-2")
    assert await store.complete("retry-claim", [], owner_id="owner-2")


def test_compaction_upgrade_preserves_supported_legacy_claims_and_receipts(live_schema):
    """Documented pre-watermark shape; not a captured old deployment incident."""
    settings, engine, schema = live_schema
    with engine.begin() as connection:
        migrate(connection, settings, schema, TOOL_REVISION)
        # Independent fixture of the initializer-supported legacy shape.
        connection.exec_driver_sql(f"""CREATE TABLE {schema}.compaction_records (
            compaction_key TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            last_event_id TEXT NOT NULL, compression_version TEXT NOT NULL,
            status TEXT NOT NULL, memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            last_error TEXT, claimed_by TEXT, claimed_until TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), completed_at TIMESTAMPTZ
        )""")
        connection.exec_driver_sql("""INSERT INTO compaction_records
            (compaction_key, session_id, last_event_id, compression_version, status,
             memory_ids, claimed_by, claimed_until, last_error, completed_at)
            VALUES ('running-claim', 'session-1', 'event-1', 'v1', 'running', '[]',
                    'original-owner', NOW() + INTERVAL '5 minutes', NULL, NULL),
                   ('done-claim', 'session-1', 'event-1', 'v1', 'completed', '["kept-memory"]',
                    NULL, NULL, NULL, NOW()),
                   ('failed-claim', 'session-1', 'event-1', 'v1', 'failed', '[]',
                    NULL, NULL, 'kept-error-type', NULL)""")
        columns = "compaction_key,session_id,last_event_id,compression_version,status,memory_ids,last_error,claimed_by,claimed_until,created_at,updated_at,completed_at"
        before = connection.exec_driver_sql(f"SELECT {columns} FROM compaction_records ORDER BY compaction_key").all()
        migrate(connection, settings, schema, COMPACTION_REVISION)
        migrate(connection, settings, schema, COMPACTION_REVISION)
        after = connection.exec_driver_sql(f"SELECT {columns} FROM compaction_records ORDER BY compaction_key").all()
        assert after == before
        assert connection.exec_driver_sql("SELECT DISTINCT last_compacted_event_sequence FROM compaction_records").all() == [(0,)]
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == COMPACTION_REVISION


def test_compaction_revision_rollback_keeps_committed_tool_tables(live_schema):
    settings, engine, schema = live_schema
    with engine.begin() as connection:
        migrate(connection, settings, schema, TOOL_REVISION)
    with engine.connect() as connection:
        transaction = connection.begin()
        migrate(connection, settings, schema, COMPACTION_REVISION)
        transaction.rollback()
    with engine.connect() as connection:
        assert not inspect(connection).has_table("compaction_records", schema=schema)
        assert inspect(connection).has_table("tool_jobs", schema=schema)
        assert connection.exec_driver_sql(f"SELECT version_num FROM {schema}.alembic_version").scalar_one() == TOOL_REVISION


@pytest.mark.asyncio
async def test_compaction_downgrade_preserves_preexisting_claim(live_schema):
    settings, engine, schema = live_schema
    with engine.begin() as connection:
        migrate(connection, settings, schema, COMPACTION_REVISION)
    store = PostgresCompactionRecordStore(settings)
    assert await store.begin(compaction_key="retained", session_id="session-1",
                             last_event_id="event-1", compression_version="v1", owner_id="retained-owner")
    before = await store.get("retained")
    with pytest.raises(RuntimeError, match="cannot be destructively downgraded"), engine.begin() as connection:
        config = migrate(connection, settings, schema, COMPACTION_REVISION)
        command.downgrade(config, TOOL_REVISION)
    assert await store.get("retained") == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql(f"SELECT version_num FROM {schema}.alembic_version").scalar_one() == COMPACTION_REVISION
