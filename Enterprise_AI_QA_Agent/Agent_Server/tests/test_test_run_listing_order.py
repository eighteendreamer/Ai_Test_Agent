from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from src.application.test_runs.run_store import (
    InMemoryTestRunStore,
    PostgresTestRunStore,
)
from src.core.config import Settings
from src.schemas.run_management import TestRunRecord as _RunRecord
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _create_claim_tables, _drop_tables


async def _assert_tied_run_order(store) -> None:
    created_at = datetime(2026, 9, 30, tzinfo=timezone.utc)
    project_id = str(uuid4())
    suite_id = str(uuid4())
    low_id = "00000000-0000-0000-0000-000000000001"
    high_id = "00000000-0000-0000-0000-000000000002"
    for run_id in (low_id, high_id):
        await store.create_run(
            _RunRecord(
                id=run_id, project_id=project_id, suite_id=suite_id,
                mode_key="api_testing", created_at=created_at, updated_at=created_at,
            ),
            [],
        )

    first_page, has_more = await store.list_runs(
        project_id=project_id, status=None, limit=1, offset=0,
    )
    second_page, second_has_more = await store.list_runs(
        project_id=project_id, status=None, limit=1, offset=1,
    )
    assert [run.id for run in first_page] == [high_id]
    assert [run.id for run in second_page] == [low_id]
    assert has_more is True
    assert second_has_more is False


@pytest.mark.asyncio
async def test_memory_run_listing_uses_descending_id_for_tied_creation_time() -> None:
    await _assert_tied_run_order(InMemoryTestRunStore())


@pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use local PostgreSQL",
)
@pytest.mark.asyncio
async def test_postgres_run_listing_matches_memory_tie_breaker() -> None:
    suffix = uuid4().hex[:10]
    tables = [f"live_run_order_{kind}_{suffix}" for kind in ("run", "item", "attempt")]
    settings = Settings()
    settings = settings.model_copy(update={
        "database": settings.database.model_copy(update={
            "postgres_test_run_table": tables[0],
            "postgres_test_run_item_table": tables[1],
            "postgres_test_run_attempt_table": tables[2],
        }),
    })
    try:
        _create_claim_tables(settings)
        await _assert_tied_run_order(PostgresTestRunStore(settings))
    finally:
        _drop_tables(settings, list(reversed(tables)))
