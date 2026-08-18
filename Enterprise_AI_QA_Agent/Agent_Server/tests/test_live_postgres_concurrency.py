from __future__ import annotations

import asyncio
import math
import os
from datetime import datetime, timezone
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


LIVE_POSTGRES = os.getenv("RUN_LIVE_POSTGRES_TESTS") == "1"
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


@live_postgres
@pytest.mark.asyncio
async def test_live_postgres_claims_each_item_once_without_deadlock():
    suffix = uuid4().hex[:10]
    run_table = f"live_run_{suffix}"
    item_table = f"live_item_{suffix}"
    attempt_table = f"live_attempt_{suffix}"
    result_table = f"live_result_{suffix}"
    settings = Settings().model_copy(
        update={
            "postgres_pool_size": 12,
            "postgres_test_run_table": run_table,
            "postgres_test_run_item_table": item_table,
            "postgres_test_run_attempt_table": attempt_table,
            "postgres_test_case_result_table": result_table,
        }
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
        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE {run_table} ("
                    "id UUID PRIMARY KEY, project_id UUID NOT NULL, suite_id UUID NOT NULL, "
                    "status TEXT NOT NULL, mode_key TEXT NOT NULL, session_id TEXT NULL, "
                    "parent_run_id UUID NULL, created_at TIMESTAMPTZ NOT NULL, "
                    "updated_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL)"
                )
                cur.execute(
                    f"CREATE TABLE {item_table} ("
                    f"id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES {run_table}(id), "
                    "case_id UUID NOT NULL, case_version_id UUID NOT NULL, position INTEGER NOT NULL, "
                    "status TEXT NOT NULL, attempt_no INTEGER NOT NULL DEFAULT 0, "
                    "lease_owner TEXT NULL, lease_token TEXT NULL, lease_expires_at TIMESTAMPTZ NULL, "
                    "result_id UUID NULL, regression_source_result_id UUID NULL, "
                    "updated_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL, "
                    "UNIQUE(run_id, position), UNIQUE(run_id, case_id, case_version_id))"
                )
                cur.execute(
                    f"CREATE TABLE {attempt_table} ("
                    f"id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES {run_table}(id), "
                    f"run_item_id UUID NOT NULL REFERENCES {item_table}(id), "
                    "attempt_no INTEGER NOT NULL, lease_token TEXT NOT NULL, "
                    "status TEXT NOT NULL, record JSONB NOT NULL, "
                    "UNIQUE(run_item_id, attempt_no), UNIQUE(lease_token))"
                )
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
async def test_live_postgres_approval_cas_has_one_final_decision():
    suffix = uuid4().hex[:10]
    session_table = f"live_session_{suffix}"
    approval_table = f"live_approval_{suffix}"
    settings = Settings().model_copy(
        update={
            "postgres_pool_size": 12,
            "postgres_session_table": session_table,
            "postgres_approval_table": approval_table,
        }
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
