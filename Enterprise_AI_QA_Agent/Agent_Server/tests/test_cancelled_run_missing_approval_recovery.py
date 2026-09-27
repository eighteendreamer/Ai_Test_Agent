from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.application.test_runs.run_store import PostgresTestRunStore
from src.core.config import Settings
from src.domain.models import SessionRecord
from src.runtime.store import InMemorySessionStore
from src.schemas.run_management import (
    TestRunItemRecord as _RunItemRecord,
)
from src.schemas.run_management import (
    TestRunRecord as _RunRecord,
)
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _create_claim_tables, _drop_tables
from tests.test_test_run_regression import _ApprovalJobs, _components


@pytest.mark.skipif(
    not LivePostgresTestConfig().run_live_postgres_tests,
    reason="set RUN_LIVE_POSTGRES_TESTS=1 to use local PostgreSQL",
)
@pytest.mark.asyncio
async def test_postgres_restart_cleans_cancelled_job_when_approval_is_gone() -> None:
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
            item.id, attempt.lease_token, "deleted-approval", "job-1", now,
        )
        await store.cancel_run(run.id, "operator cancelled before restart", now)
        service, _, _ = _components(store=store, session_store=sessions, tool_job_service=jobs)

        await service.initialize()

        detail = await store.get_run(run.id)
        assert detail.items[0].status == "cancelled"
        assert detail.items[0].resource_cleanup_completed_at is not None
        assert jobs.status == "cancelled"
    finally:
        _drop_tables(settings, list(reversed(tables)))
