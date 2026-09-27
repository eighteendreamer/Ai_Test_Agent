from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.application.test_runs.run_store import InMemoryTestRunStore, PostgresTestRunStore
from src.core.config import Settings
from src.domain.models import SessionRecord
from src.runtime.store import InMemorySessionStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.run_management import (
    TestRunItemRecord as _RunItemRecord,
)
from src.schemas.run_management import (
    TestRunRecord as _RunRecord,
)
from src.schemas.session import (
    RuntimeMode,
    SessionMode,
    SessionStatus,
    ToolApprovalRequest,
    ToolApprovalStatus,
)
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _create_claim_tables, _drop_tables
from tests.test_test_run_regression import _ApprovalJobs, _components


async def _exercise_reconciliation_pages(store) -> None:
    now = datetime.now(timezone.utc)
    run = _RunRecord(
        id=str(uuid4()), project_id=str(uuid4()), suite_id=str(uuid4()),
        session_id=f"page-session-{uuid4().hex}", mode_key="security_testing",
        created_at=now, updated_at=now,
    )
    items = [
        _RunItemRecord(
            id=str(uuid4()), run_id=run.id, case_id=str(uuid4()),
            case_version_id=str(uuid4()), position=position,
            created_at=now, updated_at=now,
        )
        for position in range(1, 66)
    ]
    await store.create_run(run, items)
    claimed = await store.claim_items(
        run_id=run.id, worker_id="page-worker", limit=65, lease_seconds=90, now=now,
    )
    assert len(claimed) == 65
    for item, attempt in claimed:
        await store.start_item(item.id, attempt.lease_token, now)
        await store.mark_waiting_approval(
            item.id, attempt.lease_token, f"approval-{item.position}",
            f"job-{item.position}", now,
        )

    waiting_first = await store.list_waiting_approval_items(limit=64)
    assert len(waiting_first) == 64
    waiting_second = await store.list_waiting_approval_items(
        limit=64, after=(waiting_first[-1].updated_at, waiting_first[-1].id),
    )
    assert len(waiting_second) == 1
    assert {item.id for item in waiting_first + waiting_second} == {item.id for item in items}
    waiting_cursor_at = waiting_first[-1].updated_at
    if waiting_cursor_at.tzinfo is None:
        waiting_cursor_at = waiting_cursor_at.replace(tzinfo=timezone.utc)
    waiting_cursor_at = waiting_cursor_at.astimezone(timezone(timedelta(hours=8)))
    assert [item.id for item in await store.list_waiting_approval_items(
        limit=64, after=(waiting_cursor_at, waiting_first[-1].id),
    )] == [waiting_second[0].id]

    await store.cancel_run(run.id, "operator cancelled", now)
    cancelled_first = await store.list_cancelled_resource_items(limit=64)
    assert len(cancelled_first) == 64
    cancelled_second = await store.list_cancelled_resource_items(
        limit=64, after=(cancelled_first[-1].updated_at, cancelled_first[-1].id),
    )
    assert len(cancelled_second) == 1
    assert {item.id for item in cancelled_first + cancelled_second} == {item.id for item in items}

    service, _, _ = _components(
        store=store,
        session_store=InMemorySessionStore(),
        tool_job_service=ToolJobService(InMemoryToolJobStore()),
    )
    service._lease_reaper_interval_seconds = 0.05
    await service.initialize()
    assert len(await store.list_cancelled_resource_items()) == 1
    await service.start_lease_reaper()
    try:
        async with asyncio.timeout(5):
            while await store.list_cancelled_resource_items():
                await asyncio.sleep(0.02)
    finally:
        await service.stop_lease_reaper()


@pytest.mark.asyncio
async def test_memory_reconciliation_pages_keep_all_items() -> None:
    await _exercise_reconciliation_pages(InMemoryTestRunStore())


@pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use local PostgreSQL",
)
@pytest.mark.asyncio
async def test_postgres_reconciliation_pages_keep_all_items() -> None:
    suffix = uuid4().hex[:10]
    tables = [f"live_page_{kind}_{suffix}" for kind in ("run", "item", "attempt")]
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
        await _exercise_reconciliation_pages(PostgresTestRunStore(settings))
    finally:
        _drop_tables(settings, list(reversed(tables)))


@pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use local PostgreSQL",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("missing_resource", ["approval", "job"])
async def test_postgres_restart_cleans_cancelled_item_with_missing_resource(missing_resource) -> None:
    suffix = uuid4().hex[:10]
    tables = [f"live_cancel_{kind}_{suffix}" for kind in ("run", "item", "attempt")]
    settings = Settings()
    settings = settings.model_copy(update={
        "database": settings.database.model_copy(update={
            "postgres_test_run_table": tables[0],
            "postgres_test_run_item_table": tables[1],
            "postgres_test_run_attempt_table": tables[2],
        }),
    })
    store = PostgresTestRunStore(settings)
    now = datetime.now(timezone.utc)
    run = _RunRecord(
        id=str(uuid4()), project_id=str(uuid4()), suite_id=str(uuid4()),
        session_id=f"cancel-session-{suffix}", mode_key="security_testing",
        created_at=now, updated_at=now,
    )
    item = _RunItemRecord(
        id=str(uuid4()), run_id=run.id, case_id=str(uuid4()),
        case_version_id=str(uuid4()), position=1, created_at=now, updated_at=now,
    )
    sessions = InMemorySessionStore()
    await sessions.save_session(SessionRecord(
        id=run.session_id, title="cancelled run session", status=SessionStatus.idle,
        session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
        mode_key="security_testing", created_at=now, updated_at=now,
    ))
    if missing_resource == "job":
        await sessions.save_approval(run.session_id, ToolApprovalRequest(
            id="approval-1", session_id=run.session_id,
            tool_key="security-scan-runner", tool_name="Security Scan Runner",
            reason="high risk profile", created_at=now,
        ))
        jobs = ToolJobService(InMemoryToolJobStore())
    else:
        jobs = _ApprovalJobs()
    try:
        _create_claim_tables(settings)
        await store.create_run(run, [item])
        claimed, attempt = (
            await store.claim_items(
                run_id=run.id, worker_id="worker-a", limit=1, lease_seconds=90, now=now,
            )
        )[0]
        await store.start_item(claimed.id, attempt.lease_token, now)
        await store.mark_waiting_approval(
            item.id, attempt.lease_token,
            "approval-1" if missing_resource == "job" else "deleted-approval",
            "job-1", now,
        )
        await store.cancel_run(run.id, "operator cancelled before restart", now)
        service, _, _ = _components(store=store, session_store=sessions, tool_job_service=jobs)

        await service.initialize()

        detail = await store.get_run(run.id)
        assert detail.items[0].status == "cancelled"
        assert detail.items[0].resource_cleanup_completed_at is not None
        if missing_resource == "job":
            assert (await sessions.list_approvals(run.session_id))[0].status == ToolApprovalStatus.denied
            assert await jobs.get_job("job-1") is None
        else:
            assert jobs.status == "cancelled"
    finally:
        _drop_tables(settings, list(reversed(tables)))
