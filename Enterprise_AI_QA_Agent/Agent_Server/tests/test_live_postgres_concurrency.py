from __future__ import annotations

import asyncio
import math
import multiprocessing
import os
import queue as queue_module
from datetime import datetime, timedelta, timezone
from time import perf_counter
from uuid import uuid4

import pytest

from src.application.test_runs.run_store import PostgresTestRunStore
from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_session_store import PostgresSessionStore
from src.schemas.run_management import (
    TestRunItemRecord as _RunItemRecord,
    TestRunRecord as _RunRecord,
)
from src.schemas.session import ToolApprovalStatus
from tests.live_postgres_config import LivePostgresTestConfig


LIVE_POSTGRES = LivePostgresTestConfig().run_live_postgres_tests
live_postgres = pytest.mark.skipif(
    not LIVE_POSTGRES,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use the local PostgreSQL instance",
)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _drop_tables(settings: Settings, table_names: list[str]) -> None:
    with postgres_connect(settings) as conn:
        with conn.cursor() as cur:
            for table_name in table_names:
                cur.execute(f"DROP TABLE IF EXISTS {table_name}")


def _settings_with_database_overrides(**overrides: object) -> Settings:
    """Apply live-test table overrides to the nested DatabaseConfig contract."""
    base = Settings()
    database = base.database.model_copy(update=overrides)
    return base.model_copy(update={"database": database})


def _create_claim_tables(settings: Settings) -> None:
    """Create isolated run tables with the same columns used by the live Store."""
    with postgres_connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_test_run_table} ("
                "id UUID PRIMARY KEY, project_id UUID NOT NULL, suite_id UUID NOT NULL, "
                "status TEXT NOT NULL, mode_key TEXT NOT NULL, session_id TEXT NULL, "
                "parent_run_id UUID NULL, created_at TIMESTAMPTZ NOT NULL, "
                "updated_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL)"
            )
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_test_run_item_table} ("
                "id UUID PRIMARY KEY, run_id UUID NOT NULL, case_id UUID NOT NULL, "
                "case_version_id UUID NOT NULL, position INTEGER NOT NULL, "
                "status TEXT NOT NULL, attempt_no INTEGER NOT NULL DEFAULT 0, "
                "lease_owner TEXT NULL, lease_token TEXT NULL, lease_expires_at TIMESTAMPTZ NULL, "
                "result_id UUID NULL, regression_source_result_id UUID NULL, "
                "updated_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL, "
                "UNIQUE(run_id, position), UNIQUE(run_id, case_id, case_version_id))"
            )
            cur.execute(
                f"CREATE TABLE {settings.database.postgres_test_run_attempt_table} ("
                "id UUID PRIMARY KEY, run_id UUID NOT NULL, run_item_id UUID NOT NULL, "
                "attempt_no INTEGER NOT NULL, lease_token TEXT NOT NULL, status TEXT NOT NULL, "
                "record JSONB NOT NULL, UNIQUE(run_item_id, attempt_no), UNIQUE(lease_token))"
            )


def _worker_settings(overrides: dict[str, object]) -> Settings:
    return _settings_with_database_overrides(**overrides)


def _claim_checkpoint_then_exit(
    overrides: dict[str, object],
    run_id: str,
    checkpoint_payload: dict[str, object],
    claimed_at_iso: str,
) -> None:
    """First isolated worker: persist a checkpoint, then exit without cleanup code."""
    settings = _worker_settings(overrides)
    now = datetime.fromisoformat(claimed_at_iso)

    async def run_worker() -> None:
        store = PostgresTestRunStore(settings)
        claims = await store.claim_items(
            run_id=run_id,
            worker_id="process-worker-a",
            limit=1,
            lease_seconds=1,
            now=now,
        )
        if len(claims) != 1:
            raise RuntimeError(f"expected one claim, got {len(claims)}")
        item, attempt = claims[0]
        await store.save_checkpoint(
            item.id,
            attempt.lease_token,
            "process_checkpoint",
            checkpoint_payload,
            1,
            now,
        )

    asyncio.run(run_worker())
    os._exit(0)


def _recover_and_claim_in_new_process(
    overrides: dict[str, object],
    run_id: str,
    recover_at_iso: str,
    result_queue,
) -> None:
    """Second isolated worker: recover expired work and claim it with a new Attempt."""
    settings = _worker_settings(overrides)
    recover_at = datetime.fromisoformat(recover_at_iso)

    async def run_worker() -> dict[str, object]:
        store = PostgresTestRunStore(settings)
        recovered = await store.recover_expired(run_id, recover_at)
        claims = await store.claim_items(
            run_id=run_id,
            worker_id="process-worker-b",
            limit=1,
            lease_seconds=60,
            now=recover_at,
        )
        if len(claims) != 1:
            raise RuntimeError(f"expected one recovered claim, got {len(claims)}")
        item, attempt = claims[0]
        return {
            "recovered": recovered,
            "item_id": item.id,
            "attempt_id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "recovered_from_attempt_id": attempt.recovered_from_attempt_id,
            "checkpoint_version": attempt.checkpoint_version,
            "checkpoint_key": attempt.checkpoint_key,
            "checkpoint_payload": attempt.checkpoint_payload,
        }

    try:
        result_queue.put(asyncio.run(run_worker()))
    except Exception as exc:
        result_queue.put({"error": f"{type(exc).__name__}: {exc}"})
        raise


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_claims_each_item_once_without_deadlock():
    suffix = uuid4().hex[:10]
    run_table = f"live_run_{suffix}"
    item_table = f"live_item_{suffix}"
    attempt_table = f"live_attempt_{suffix}"
    result_table = f"live_result_{suffix}"
    settings = _settings_with_database_overrides(
        postgres_pool_size=12,
        postgres_test_run_table=run_table,
        postgres_test_run_item_table=item_table,
        postgres_test_run_attempt_table=attempt_table,
        postgres_test_case_result_table=result_table,
    )
    store = PostgresTestRunStore(settings)
    now = datetime.now(timezone.utc)
    run = _RunRecord(
        id=str(uuid4()),
        project_id=str(uuid4()),
        suite_id=str(uuid4()),
        mode_key="api_testing",
        stats={"total": 480, "queued": 480},
        created_at=now,
        updated_at=now,
    )
    items = [
        _RunItemRecord(
            id=str(uuid4()),
            run_id=run.id,
            case_id=str(uuid4()),
            case_version_id=str(uuid4()),
            position=position,
            created_at=now,
            updated_at=now,
        )
        for position in range(1, 481)
    ]
    claimed_item_ids: list[str] = []
    claim_latencies_ms: list[float] = []

    try:
        _create_claim_tables(settings)
        await store.create_run(run, items)

        async def claim_until_empty(worker_number: int) -> None:
            while True:
                started = perf_counter()
                claims = await store.claim_items(
                    run_id=run.id,
                    worker_id=f"worker-{worker_number}",
                    limit=4,
                    lease_seconds=120,
                    now=datetime.now(timezone.utc),
                )
                claim_latencies_ms.append((perf_counter() - started) * 1000)
                if not claims:
                    return
                claimed_item_ids.extend(item.id for item, _ in claims)

        started = perf_counter()
        await asyncio.gather(*(claim_until_empty(index) for index in range(12)))
        elapsed_seconds = perf_counter() - started

        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS total FROM {attempt_table}")
                attempt_count = int(cur.fetchone()["total"])
                cur.execute(
                    f"SELECT COUNT(DISTINCT run_item_id) AS items, "
                    f"COUNT(DISTINCT lease_token) AS leases FROM {attempt_table}"
                )
                distinct_counts = cur.fetchone()
                cur.execute(
                    f"SELECT status, COUNT(*) AS total FROM {item_table} GROUP BY status"
                )
                status_counts = {
                    row["status"]: int(row["total"]) for row in cur.fetchall()
                }

        assert len(claimed_item_ids) == 480
        assert len(set(claimed_item_ids)) == 480
        assert attempt_count == 480
        assert int(distinct_counts["items"]) == 480
        assert int(distinct_counts["leases"]) == 480
        assert status_counts == {"claimed": 480}
        print(
            "live_postgres_claim "
            f"items=480 workers=12 throughput={480 / elapsed_seconds:.2f}/s "
            f"p50={_percentile(claim_latencies_ms, 0.50):.2f}ms "
            f"p95={_percentile(claim_latencies_ms, 0.95):.2f}ms "
            f"p99={_percentile(claim_latencies_ms, 0.99):.2f}ms"
        )
    finally:
        _drop_tables(settings, [attempt_table, item_table, run_table])


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_process_worker_takeover_restores_checkpoint():
    suffix = uuid4().hex[:10]
    run_table = f"live_takeover_run_{suffix}"
    item_table = f"live_takeover_item_{suffix}"
    attempt_table = f"live_takeover_attempt_{suffix}"
    settings_overrides = {
        "postgres_pool_size": 4,
        "postgres_test_run_table": run_table,
        "postgres_test_run_item_table": item_table,
        "postgres_test_run_attempt_table": attempt_table,
        "postgres_test_case_result_table": f"live_takeover_result_{suffix}",
    }
    settings = _settings_with_database_overrides(**settings_overrides)
    store = PostgresTestRunStore(settings)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    run = _RunRecord(
        id=str(uuid4()),
        project_id=str(uuid4()),
        suite_id=str(uuid4()),
        mode_key="api_testing",
        stats={"total": 1, "queued": 1},
        created_at=now,
        updated_at=now,
    )
    item = _RunItemRecord(
        id=str(uuid4()),
        run_id=run.id,
        case_id=str(uuid4()),
        case_version_id=str(uuid4()),
        position=1,
        created_at=now,
        updated_at=now,
    )
    result_table = settings.database.postgres_test_case_result_table
    process_a = None
    process_b = None
    result_queue = None
    try:
        _create_claim_tables(settings)
        await store.create_run(run, [item])
        context = multiprocessing.get_context("spawn")
        process_a = context.Process(
            target=_claim_checkpoint_then_exit,
            args=(settings_overrides, run.id, {"step": 7, "request_id": "req-7"}, now.isoformat()),
            name="live-worker-a",
        )
        process_a.start()
        process_a.join(timeout=30)
        assert not process_a.is_alive()
        assert process_a.exitcode == 0

        first_attempt = await store.get_latest_attempt(item.id)
        assert first_attempt is not None
        assert first_attempt.attempt_no == 1
        assert first_attempt.checkpoint_payload == {"step": 7, "request_id": "req-7"}

        result_queue = context.Queue()
        recover_at = now.replace(microsecond=0) + timedelta(seconds=2)
        process_b = context.Process(
            target=_recover_and_claim_in_new_process,
            args=(settings_overrides, run.id, recover_at.isoformat(), result_queue),
            name="live-worker-b",
        )
        process_b.start()
        process_b.join(timeout=30)
        assert not process_b.is_alive()
        assert process_b.exitcode == 0
        try:
            takeover = result_queue.get(timeout=5)
        except queue_module.Empty as exc:
            raise AssertionError("recovery worker returned no result") from exc

        assert "error" not in takeover
        assert takeover["recovered"] == 1
        assert takeover["item_id"] == item.id
        assert takeover["attempt_no"] == 2
        assert takeover["attempt_id"] != first_attempt.id
        assert takeover["recovered_from_attempt_id"] == first_attempt.id
        assert takeover["checkpoint_version"] == 1
        assert takeover["checkpoint_key"] == "process_checkpoint"
        assert takeover["checkpoint_payload"] == {"step": 7, "request_id": "req-7"}
    finally:
        if process_a is not None and process_a.is_alive():
            process_a.terminate()
            process_a.join(timeout=5)
        if process_b is not None and process_b.is_alive():
            process_b.terminate()
            process_b.join(timeout=5)
        if result_queue is not None:
            result_queue.close()
        _drop_tables(settings, [result_table, attempt_table, item_table, run_table])


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_approval_cas_has_one_final_decision():
    suffix = uuid4().hex[:10]
    session_table = f"live_session_{suffix}"
    approval_table = f"live_approval_{suffix}"
    settings = _settings_with_database_overrides(
        postgres_pool_size=12,
        postgres_session_table=session_table,
        postgres_approval_table=approval_table,
    )
    store = PostgresSessionStore(settings)
    session_id = f"session-{suffix}"
    approval_id = f"approval-{suffix}"
    now = datetime.now(timezone.utc)

    try:
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE {session_table} ("
                    "id TEXT PRIMARY KEY, updated_at TIMESTAMPTZ NOT NULL)"
                )
                cur.execute(
                    f"CREATE TABLE {approval_table} ("
                    "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, tool_key TEXT NOT NULL, "
                    "tool_name TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, "
                    "created_at TIMESTAMPTZ NOT NULL, resolved_at TIMESTAMPTZ NULL, "
                    "decision_note TEXT NULL, metadata JSONB NOT NULL DEFAULT '{}'::jsonb)"
                )
                cur.execute(
                    f"INSERT INTO {session_table} (id, updated_at) VALUES (%s, %s)",
                    (session_id, now),
                )
                cur.execute(
                    f"INSERT INTO {approval_table} "
                    "(id, session_id, tool_key, tool_name, reason, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, 'pending', %s)",
                    (
                        approval_id,
                        session_id,
                        "security-scan-runner",
                        "Security Scan Runner",
                        "live CAS validation",
                        now,
                    ),
                )

        decisions = [
            ToolApprovalStatus.approved if index % 2 == 0 else ToolApprovalStatus.denied
            for index in range(32)
        ]

        async def decide(status: ToolApprovalStatus):
            try:
                approval = await store.resolve_approval(
                    session_id,
                    approval_id,
                    status,
                    f"decision:{status.value}",
                )
                return approval.status, None
            except Exception as exc:
                return None, exc

        started = perf_counter()
        outcomes = await asyncio.gather(*(decide(status) for status in decisions))
        elapsed_seconds = perf_counter() - started
        successes = [status for status, error in outcomes if error is None]
        errors = [error for status, error in outcomes if error is not None]
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT status FROM {approval_table} WHERE id = %s",
                    (approval_id,),
                )
                final_status = ToolApprovalStatus(cur.fetchone()["status"])

        assert len(successes) == 16
        assert set(successes) == {final_status}
        assert len(errors) == 16
        assert all(isinstance(error, ValueError) for error in errors)
        print(
            "live_postgres_approval_cas "
            f"requests=32 final_status={final_status.value} "
            f"throughput={32 / elapsed_seconds:.2f}/s "
            f"elapsed={elapsed_seconds * 1000:.2f}ms"
        )
    finally:
        _drop_tables(settings, [approval_table, session_table])
