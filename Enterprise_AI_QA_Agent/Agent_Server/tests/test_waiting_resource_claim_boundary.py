from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.test_runs.run_store import (
    InMemoryTestRunStore,
    PostgresTestRunStore,
)
from src.core.config import Settings
from src.schemas import run_management
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _create_claim_tables, _drop_tables


async def _exercise_waiting_resource_claim(store) -> None:
    now = datetime.now(timezone.utc)
    run_id, project_id, suite_id, item_id, case_id, version_id = (str(uuid4()) for _ in range(6))
    run = run_management.TestRunRecord(
        id=run_id, project_id=project_id, suite_id=suite_id, mode_key="api_testing",
        stats=run_management.TestRunStats(total=1, queued=1), created_at=now, updated_at=now,
    )
    item = run_management.TestRunItemRecord(
        id=item_id, run_id=run_id, case_id=case_id, case_version_id=version_id,
        position=1, created_at=now, updated_at=now,
    )
    await store.create_run(run, [item])
    first_item, first_attempt = (
        await store.claim_items(run_id=run_id, worker_id="worker-a", limit=1, lease_seconds=30, now=now)
    )[0]
    await store.start_item(item_id, first_attempt.lease_token, now)
    await store.save_checkpoint(
        item_id, first_attempt.lease_token, "work_progress", {"step": 1}, 1, now,
    )
    waiting = await store.mark_waiting_resource(item_id, first_attempt.lease_token, "capacity", now)
    assert waiting.status == "waiting_resource"
    assert waiting.lease_token == first_attempt.lease_token
    assert (await store.get_run_record(run_id)).stats.waiting_resource == 1

    assert await store.claim_items(
        run_id=run_id, worker_id="worker-b", limit=1, lease_seconds=30,
        now=now + timedelta(seconds=1),
    ) == []
    assert (await store.get_latest_attempt(item_id)).id == first_attempt.id

    assert await store.recover_expired(run_id, now + timedelta(seconds=31)) == 1
    assert (await store.get_latest_attempt(item_id)).status == "expired"
    assert (await store.get_run_record(run_id)).stats.queued == 1
    second_item, second_attempt = (
        await store.claim_items(
            run_id=run_id, worker_id="worker-b", limit=1, lease_seconds=30,
            now=now + timedelta(seconds=32),
        )
    )[0]
    assert second_item.id == first_item.id
    assert second_attempt.attempt_no == 2
    assert second_attempt.recovered_from_attempt_id == first_attempt.id
    assert second_attempt.checkpoint_version == 1
    assert second_attempt.checkpoint_payload == {"step": 1}


@pytest.mark.asyncio
async def test_memory_store_waits_for_lease_expiry_before_reclaim() -> None:
    await _exercise_waiting_resource_claim(InMemoryTestRunStore())


@pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use local PostgreSQL",
)
@pytest.mark.asyncio
async def test_postgres_store_waits_for_lease_expiry_before_reclaim() -> None:
    suffix = uuid4().hex[:10]
    tables = [f"live_wait_{kind}_{suffix}" for kind in ("run", "item", "attempt")]
    settings = Settings()
    database = settings.database.model_copy(update={
        "postgres_test_run_table": tables[0],
        "postgres_test_run_item_table": tables[1],
        "postgres_test_run_attempt_table": tables[2],
    })
    settings = settings.model_copy(update={"database": database})
    try:
        _create_claim_tables(settings)
        await _exercise_waiting_resource_claim(PostgresTestRunStore(settings))
    finally:
        _drop_tables(settings, list(reversed(tables)))
