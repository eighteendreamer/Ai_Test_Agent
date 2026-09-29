from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.test_runs.run_store import InMemoryTestRunStore, PostgresTestRunStore
from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.run_management import (
    RunItemCompletion,
    TestRunItemRecord as _RunItemRecord,
    TestRunRecord as _RunRecord,
    TestRunStats as _RunStats,
)
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _create_claim_tables, _drop_tables


async def _new_running_item(store, now: datetime):
    run_id, project_id, suite_id, item_id, case_id, version_id = (str(uuid4()) for _ in range(6))
    run = _RunRecord(
        id=run_id, project_id=project_id, suite_id=suite_id, mode_key="api_testing",
        stats=_RunStats(total=1, queued=1), created_at=now, updated_at=now,
    )
    item = _RunItemRecord(
        id=item_id, run_id=run_id, case_id=case_id, case_version_id=version_id,
        position=1, created_at=now, updated_at=now,
    )
    await store.create_run(run, [item])
    _, attempt = (await store.claim_items(
        run_id=run_id, worker_id="duration-worker", limit=1, lease_seconds=30, now=now,
    ))[0]
    await store.start_item(item_id, attempt.lease_token, now)
    return run_id, item_id, attempt


async def _approval_duration_matches_run(store) -> None:
    now = datetime.now(timezone.utc)
    run_id, item_id, attempt = await _new_running_item(store, now)
    waiting = await store.mark_waiting_approval(
        item_id, attempt.lease_token, "approval-1", "job-1", now + timedelta(seconds=2),
    )
    assert waiting.active_duration_ms == 2000
    assert (await store.get_run_record(run_id)).active_duration_ms == 2000


async def _expired_waiting_resource_does_not_overlap_retries(store) -> None:
    now = datetime.now(timezone.utc)
    run_id, item_id, first_attempt = await _new_running_item(store, now)
    waiting = await store.mark_waiting_resource(
        item_id, first_attempt.lease_token, "capacity", now + timedelta(seconds=3),
    )
    assert waiting.active_duration_ms == 3000
    assert waiting.active_started_at is None
    assert waiting.waiting_started_at == now + timedelta(seconds=3)

    assert await store.recover_expired(run_id, now + timedelta(seconds=31)) == 1
    recovered = (await store.get_run(run_id)).items[0]
    assert recovered.active_duration_ms == 3000
    assert recovered.waiting_duration_ms == 27000
    assert recovered.active_started_at is None
    assert recovered.waiting_started_at is None
    expired_attempt = await store.get_latest_attempt(item_id)
    assert expired_attempt.status == "expired"
    assert expired_attempt.active_duration_ms == 3000
    assert expired_attempt.waiting_duration_ms == 27000

    _, second_attempt = (await store.claim_items(
        run_id=run_id, worker_id="retry-worker", limit=1, lease_seconds=30,
        now=now + timedelta(seconds=40),
    ))[0]
    await store.start_item(item_id, second_attempt.lease_token, now + timedelta(seconds=40))
    resumed = await store.mark_waiting_approval(
        item_id, second_attempt.lease_token, "approval-2", "job-2",
        now + timedelta(seconds=42),
    )
    assert resumed.active_duration_ms == 5000
    assert resumed.waiting_duration_ms == 27000
    run = await store.get_run_record(run_id)
    assert run.active_duration_ms == 5000
    assert run.waiting_duration_ms == 27000


async def _cancel_settles_open_intervals(store) -> None:
    now = datetime.now(timezone.utc)
    run_id, _, _ = await _new_running_item(store, now)
    detail = await store.cancel_run(run_id, "operator stopped", now + timedelta(seconds=5))
    assert detail.items[0].active_duration_ms == 5000
    assert detail.items[0].active_started_at is None
    assert detail.attempts[0].active_duration_ms == 5000
    assert detail.attempts[0].active_started_at is None
    assert detail.run.active_duration_ms == 5000

    approval_run_id, approval_item_id, approval_attempt = await _new_running_item(store, now)
    await store.mark_waiting_approval(
        approval_item_id, approval_attempt.lease_token,
        "approval-3", "job-3", now + timedelta(seconds=2),
    )
    approval_detail = await store.cancel_run(
        approval_run_id, "operator stopped", now + timedelta(seconds=7),
    )
    assert approval_detail.items[0].active_duration_ms == 2000
    assert approval_detail.items[0].waiting_duration_ms == 5000
    assert approval_detail.items[0].waiting_started_at is None
    assert approval_detail.attempts[0].active_duration_ms == 2000
    assert approval_detail.attempts[0].waiting_duration_ms == 5000
    assert approval_detail.run.active_duration_ms == 2000
    assert approval_detail.run.waiting_duration_ms == 5000


async def _approved_completion_counts_each_interval_once(store) -> None:
    now = datetime.now(timezone.utc)
    run_id, item_id, attempt = await _new_running_item(store, now)
    await store.mark_waiting_approval(
        item_id, attempt.lease_token, "approval-4", "job-4",
        now + timedelta(seconds=2),
    )
    resumed = await store.resume_waiting_approval(
        item_id, "approval-4", lease_seconds=30, now=now + timedelta(seconds=7),
    )
    await store.resume_waiting_approval(
        item_id, "approval-4", lease_seconds=30, now=now + timedelta(seconds=7),
    )
    assert resumed.waiting_duration_ms == 5000
    assert (await store.get_run_record(run_id)).waiting_duration_ms == 5000
    await store.start_item(item_id, attempt.lease_token, now + timedelta(seconds=7))
    await store.complete_item(
        item_id, attempt.lease_token,
        RunItemCompletion(status="passed", summary="approved", payload_hash="a" * 64),
        now + timedelta(seconds=9),
    )
    detail = await store.get_run(run_id)
    assert detail.items[0].active_duration_ms == 4000
    assert detail.items[0].waiting_duration_ms == 5000
    assert detail.run.active_duration_ms == 4000
    assert detail.run.waiting_duration_ms == 5000
    assert detail.run.wall_clock_duration_ms == 9000


async def _denied_approval_settles_waiting(store) -> None:
    now = datetime.now(timezone.utc)
    run_id, item_id, attempt = await _new_running_item(store, now)
    await store.mark_waiting_approval(
        item_id, attempt.lease_token, "approval-5", "job-5",
        now + timedelta(seconds=2),
    )
    completion = RunItemCompletion(
        status="blocked", summary="approval denied", payload_hash="b" * 64,
    )
    result = await store.complete_denied_approval(
        item_id, "approval-5", completion, now + timedelta(seconds=7),
    )
    repeated = await store.complete_denied_approval(
        item_id, "approval-5", completion, now + timedelta(seconds=7),
    )
    assert repeated.id == result.id
    detail = await store.get_run(run_id)
    assert detail.items[0].active_duration_ms == 2000
    assert detail.items[0].waiting_duration_ms == 5000
    assert detail.attempts[0].waiting_duration_ms == 5000
    assert detail.run.active_duration_ms == 2000
    assert detail.run.waiting_duration_ms == 5000
    assert detail.run.wall_clock_duration_ms == 7000


@pytest.mark.asyncio
@pytest.mark.parametrize("exercise", [
    _approval_duration_matches_run,
    _expired_waiting_resource_does_not_overlap_retries,
    _cancel_settles_open_intervals,
    _approved_completion_counts_each_interval_once,
    _denied_approval_settles_waiting,
])
async def test_memory_duration_transitions(exercise) -> None:
    await exercise(InMemoryTestRunStore())


@pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use local PostgreSQL",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("exercise", [
    _approval_duration_matches_run,
    _expired_waiting_resource_does_not_overlap_retries,
    _cancel_settles_open_intervals,
    _approved_completion_counts_each_interval_once,
    _denied_approval_settles_waiting,
])
async def test_postgres_duration_transitions(exercise) -> None:
    suffix = uuid4().hex[:10]
    tables = [f"live_duration_{kind}_{suffix}" for kind in ("run", "item", "attempt", "result")]
    settings = Settings()
    settings = settings.model_copy(update={
        "database": settings.database.model_copy(update={
            "postgres_test_run_table": tables[0],
            "postgres_test_run_item_table": tables[1],
            "postgres_test_run_attempt_table": tables[2],
            "postgres_test_case_result_table": tables[3],
        }),
    })
    try:
        _create_claim_tables(settings)
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE {tables[3]} ("
                    "id UUID PRIMARY KEY, run_id UUID NOT NULL, run_item_id UUID NOT NULL, "
                    "case_id UUID NOT NULL, case_version_id UUID NOT NULL, "
                    "regression_source_result_id UUID NULL, attempt_id UUID NOT NULL, "
                    "status TEXT NOT NULL, payload_hash TEXT NOT NULL, "
                    "created_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL)"
                )
        await exercise(PostgresTestRunStore(settings))
    finally:
        _drop_tables(settings, list(reversed(tables)))
