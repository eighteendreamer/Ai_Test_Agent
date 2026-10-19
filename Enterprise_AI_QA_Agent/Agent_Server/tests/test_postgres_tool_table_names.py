"""Migration/runtime agreement and original captured evidence in owned schemas."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from test_case_effect_evidence import captured_job
from test_schema_migrations import live_schema, migrate, TOOL_REVISION
from src.application.runtime.case_action_binding import CaseActionBinding
from src.application.runtime.case_operation import operation_identity
from src.infrastructure.postgres_identifiers import derived_tool_table, postgres_relation_parts
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_case_action_admission import PostgresCaseActionAdmission
from src.runtime.postgres_tool_job_store import PostgresToolJobStore


@pytest.mark.parametrize("jobs", ["agent_tool_jobs", "TOOL_JOBS", "x" * 63])
@pytest.mark.parametrize("role", ["case_ops", "case_eff", "case_end"])
def test_original_unqualified_table_names_are_not_renamed(jobs, role):
    # Frozen original naming contract, shared by all three prior stores.
    original = jobs[:25] + f"_{role}_" + hashlib.sha256(jobs.encode()).hexdigest()[:16]
    assert derived_tool_table(jobs, role) == original


@pytest.mark.parametrize("value", ["", " ", "a..b", "a.", ".b", '""', '"unterminated',
    "jobs; SELECT 1", "jobs -- comment", "jobs/*comment*/", "jobs AS alias", '"a"b', '"a\x00b"'])
def test_invalid_configuration_cannot_enter_tool_table_sql(value):
    with pytest.raises(ValueError, match="PostgreSQL relation identifier"):
        derived_tool_table(value, "case_ops")


@pytest.mark.parametrize("role", ["case_ops", "case_eff", "case_end"])
def test_new_schema_names_are_stable_across_equivalent_spellings(role):
    first = derived_tool_table('PUBLIC.TOOL_JOBS', role)
    assert first == derived_tool_table('public.tool_jobs', role)
    assert first == derived_tool_table('"public" . "tool_jobs"', role)
    assert first != derived_tool_table('"public"."Tool_Jobs"', role)


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [
    ("tool_jobs", "tool_artifacts"),
    ("TOOL_JOBS", "TOOL_ARTIFACTS"),
    ('"Tool.Jobs"', '"Tool.Artifacts"'),
    ('"工具.""Jobs"', '"工具.""Artifacts"'),
])
async def test_migrated_tool_tables_initialize_and_keep_original_evidence(live_schema, captured_job, names):
    settings, engine, schema = live_schema
    settings.database.postgres_tool_job_table = f'{schema}.{names[0]}'
    settings.database.postgres_tool_artifact_table = f'{schema}.{names[1]}'
    item_table = f'{schema}.owned_run_items'
    with engine.begin() as connection:
        migrate(connection, settings, schema, TOOL_REVISION)
        connection.exec_driver_sql(f'CREATE TABLE {item_table} (id UUID PRIMARY KEY)')
    jobs = PostgresToolJobStore(settings)
    await jobs.initialize()
    runs = SimpleNamespace(_settings=settings, _item_table=item_table)
    admission = PostgresCaseActionAdmission(runs=runs, jobs=jobs)
    await admission.initialize()
    job = captured_job.model_copy(deep=True)
    await jobs.save_job(job)
    binding = CaseActionBinding.model_validate(job.metadata['case_action_binding'])
    identity = operation_identity(job, binding)
    with postgres_connect(settings) as conn, conn.cursor() as cursor:
        original = admission.operations.register(cursor, identity, job)
        jobs.effect_evidence.capture(cursor, job, job.output_payload, job.metadata['tool_output_observation'])
        assert admission.operations.read(cursor, identity['operation_id']) == original
        assert admission.operations.validate_origin(original, job) == identity
    records = await jobs.effect_evidence.list_run_evidence(binding.project_id, binding.run_id, binding.plan_id)
    assert len(records) == 1 and records[0]['evidence_state'] == 'captured'
    assert records[0]['returned_evidence']['checks_passed'] is True
    assert records[0]['returned_evidence']['objects'][0]['value'] == job.output_payload['task_result']['response_body']['order.id']
    with engine.connect() as connection:
        before = set(inspect(connection).get_table_names(schema=schema))
        for table, target in ((admission.operations.table, settings.database.postgres_tool_job_table),
            (jobs.effect_evidence.table, settings.database.postgres_tool_job_table),
            (admission.consumption.table, item_table)):
            parts = postgres_relation_parts(table)
            assert parts[0] == schema and len(parts[-1].encode('utf-8')) <= 63
            foreign_keys = inspect(connection).get_foreign_keys(parts[-1], schema=schema)
            assert any(key['referred_schema'] == schema and key['referred_table'] == postgres_relation_parts(target)[-1]
                for key in foreign_keys)
        # Runtime initialize must reuse migration indexes, not add duplicates.
        assert len(inspect(connection).get_indexes(postgres_relation_parts(settings.database.postgres_tool_job_table)[-1],
            schema=schema)) == 2
    await jobs.initialize()
    await admission.initialize()
    restarted = PostgresToolJobStore(settings)
    assert await restarted.get_job(job.id) == job
    assert await restarted.effect_evidence.list_run_evidence(binding.project_id, binding.run_id, binding.plan_id) == records
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names(schema=schema)) == before
