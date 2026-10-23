"""Real PostgreSQL verification for the explicit legacy Smoke import boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.application.projects.legacy_smoke_import_preflight import (
    LegacySmokeImportPreflightService,
)
from src.application.projects.legacy_smoke_import_store import (
    PostgresLegacySmokeImportStore,
)
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.application.test_cases.case_store import PostgresTestCaseStore
from src.application.test_runs.run_store import PostgresTestRunStore
from src.application.test_suites.suite_store import PostgresTestSuiteStore
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import dispose_sqlalchemy_engines, postgres_engine
from src.modes.smoke_testing_mode.catalog_store import SmokeCatalogStore
from src.modes.smoke_testing_mode.contracts import SmokeCaseResult, SmokeRunResult
from src.schemas.project import ProjectCreateRequest
from tests.live_postgres_config import LivePostgresTestConfig


pytestmark = pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use the local PostgreSQL instance",
)


def test_real_postgres_legacy_smoke_import_is_explicit_immutable_and_idempotent():
    asyncio.run(_scenario())


async def _scenario() -> None:
    base = get_settings().model_copy(deep=True)
    database = "legacy_import_test_" + uuid4().hex[:16]
    settings = base.model_copy(deep=True)
    settings.database.postgres_database = database
    created_database = False
    try:
        with postgres_engine(base).connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database}" TEMPLATE template0')
            created_database = True

        projects = ProjectService(store=PostgresProjectStore(settings))
        await projects.initialize()
        await PostgresTestCaseStore(settings).initialize()
        await PostgresTestSuiteStore(settings).initialize()
        runs = PostgresTestRunStore(settings)
        await runs.initialize()
        catalog = SmokeCatalogStore(settings)
        await catalog.initialize()
        writer = PostgresLegacySmokeImportStore(settings)

        project = await projects.create(
            ProjectCreateRequest(project_key="legacy-import-live", name="Legacy import live")
        )
        started = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
        legacy = SmokeRunResult(
            run_id="legacy-run-live-001",
            plan_id="legacy-plan-live-001",
            plan_version=3,
            project_scope="legacy-orders-v3",
            target_url="https://legacy.example.test",
            status="passed",
            verdict="ready",
            total_cases=1,
            selected_case_count=1,
            passed_cases=1,
            started_at=started.isoformat(),
            completed_at=(started + timedelta(seconds=2)).isoformat(),
            summary="legacy persisted smoke result",
            case_results=[
                SmokeCaseResult(
                    case_id="legacy-case-001",
                    title="Legacy health check",
                    case_type="health",
                    status="passed",
                    summary="HTTP 200",
                    assertion_count=1,
                    passed_count=1,
                    duration_ms=42,
                    evidence=[{"kind": "legacy", "value": "evidence-1"}],
                )
            ],
        )
        await catalog.save_run(legacy)

        service = LegacySmokeImportPreflightService(
            project_service=projects,
            catalog=catalog,
            projection_index=runs,
            writer=writer,
        )
        preflight = await service.preflight(
            scope_to_project_id={"legacy-orders-v3": project.id},
            page_size=1,
        )
        assert preflight.read_count == 1
        assert preflight.importable_count == 1
        assert preflight.unmapped_count == 0
        assert preflight.invalid_count == 0

        first = await service.apply(
            scope_to_project_id={"legacy-orders-v3": project.id},
            page_size=1,
        )
        assert first.imported_count == 1
        assert first.already_imported_count == 0
        assert first.failed_count == 0
        assert len(first.canonical_run_ids) == 1
        canonical_id = first.canonical_run_ids[0]

        # Recreate all readers/writers to verify the persisted projection, not in-memory state.
        restarted_runs = PostgresTestRunStore(settings)
        restarted_writer = PostgresLegacySmokeImportStore(settings)
        restarted_service = LegacySmokeImportPreflightService(
            project_service=ProjectService(store=PostgresProjectStore(settings)),
            catalog=SmokeCatalogStore(settings),
            projection_index=restarted_runs,
            writer=restarted_writer,
        )
        read_back = await restarted_runs.get_run(canonical_id)
        assert read_back is not None
        assert read_back.run.project_id == project.id
        assert read_back.run.origin == "legacy_smoke_import"
        assert read_back.run.status == "completed"
        assert len(read_back.items) == len(read_back.attempts) == len(read_back.results) == 1
        assert read_back.results[0].actual["legacy_smoke_run_id"] == legacy.run_id
        assert read_back.results[0].actual["read_only"] is True

        second = await restarted_service.apply(
            scope_to_project_id={"legacy-orders-v3": project.id},
            page_size=1,
        )
        assert second.imported_count == 0
        assert second.already_imported_count == 1
        assert second.canonical_run_ids == [canonical_id]

        with postgres_connect(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_hash, canonical_run_id, source_snapshot "
                "FROM agent_legacy_smoke_import_ledger WHERE legacy_run_id=%s",
                (legacy.run_id,),
            )
            ledger = cursor.fetchone()
            cursor.execute(
                f"SELECT count(*) AS total FROM {settings.database.postgres_test_run_table} "
                "WHERE id=%s",
                (canonical_id,),
            )
            run_count = cursor.fetchone()["total"]
        assert ledger is not None
        assert str(ledger["canonical_run_id"]) == canonical_id
        assert ledger["source_snapshot"]["run_id"] == legacy.run_id
        assert run_count == 1

        # A changed source snapshot must not silently overwrite the immutable ledger.
        changed = legacy.model_copy(update={"summary": "tampered legacy snapshot"})
        await catalog.save_run(changed)
        tampered = await restarted_service.apply(
            scope_to_project_id={"legacy-orders-v3": project.id},
            page_size=1,
        )
        assert tampered.imported_count == 0
        assert tampered.already_imported_count == 0
        assert tampered.failed_count == 1
        assert "source snapshot changed after import" in tampered.errors[0]
    finally:
        if created_database:
            dispose_sqlalchemy_engines("postgres")
            with postgres_engine(base).connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                active = connection.exec_driver_sql(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=%s",
                    (database,),
                ).scalar_one()
                assert active == 0
                connection.exec_driver_sql(f'DROP DATABASE "{database}"')
            dispose_sqlalchemy_engines("postgres")
