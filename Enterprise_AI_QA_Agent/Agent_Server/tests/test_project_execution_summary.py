from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from src.api.routes.run_management import router
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.application.test_runs.run_service import TestRunService as RunService
from src.application.test_runs.run_store import InMemoryTestRunStore, PostgresTestRunStore
from src.schemas.project import ProjectCreateRequest
from src.schemas.run_management import TestRunRecord as RunRecord, TestRunItemRecord as ItemRecord, TestRunStats as Stats
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_live_postgres_concurrency import _settings_with_database_overrides, _create_claim_tables, _drop_tables


@pytest_asyncio.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield InMemoryTestRunStore()
        return
    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("set RUN_LIVE_POSTGRES_TESTS=1 for real project aggregation")
    suffix = uuid4().hex[:12]
    settings = _settings_with_database_overrides(
        postgres_test_run_table=f"live_summary_run_{suffix}",
        postgres_test_run_item_table=f"live_summary_item_{suffix}",
        postgres_test_run_attempt_table=f"live_summary_attempt_{suffix}",
    )
    _create_claim_tables(settings)
    try:
        yield PostgresTestRunStore(settings)
    finally:
        _drop_tables(settings, [settings.database.postgres_test_run_attempt_table,
                               settings.database.postgres_test_run_item_table, settings.database.postgres_test_run_table])


@pytest.mark.asyncio
async def test_project_summary_counts_all_pages_and_live_transitions(store):
    projects = ProjectService(store=InMemoryProjectStore())
    project = await projects.create(ProjectCreateRequest(project_key="summary", name="Summary"))
    other = await projects.create(ProjectCreateRequest(project_key="other", name="Other"))
    now = datetime.now(timezone.utc)
    runs = []
    for index in range(27):
        run = RunRecord(id=str(uuid4()), project_id=project.id if index < 26 else other.id,
            suite_id=str(uuid4()), mode_key="api_testing", stats=Stats(total=1, queued=1), created_at=now, updated_at=now)
        item = ItemRecord(id=str(uuid4()), run_id=run.id, case_id=str(uuid4()), case_version_id=str(uuid4()),
            position=1, created_at=now, updated_at=now)
        await store.create_run(run, [item])
        runs.append(run)
    # The last records lie beyond a single 20-run page. Read actual state
    # transitions instead of deriving the dashboard from list_runs pagination.
    for index, reason in ((23, "resource_type:browser"), (24, "resource_type:docker"), (25, None)):
        [(item, attempt)] = await store.claim_items(run_id=runs[index].id, worker_id="summary-worker",
            limit=1, lease_seconds=60, now=now)
        await store.start_item(item.id, attempt.lease_token, now)
        if reason:
            await store.mark_waiting_resource(item.id, attempt.lease_token, reason, now)
    app = FastAPI()
    app.state.test_run_service = RunService(store=store, project_service=projects,
        suite_service=None, test_case_service=None)
    app.include_router(router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/projects/{project.id}/execution-summary")
        assert response.status_code == 200
        body = response.json()
        assert body["stats"]["total"] == 26
        assert body["stats"]["queued"] == 23
        assert body["stats"]["running"] == 1
        assert body["stats"]["waiting_resource"] == 2
        assert body["waiting_reasons"] == {"resource_type:browser": 1, "resource_type:docker": 1}
        assert (await client.get(f"/api/v1/projects/{other.id}/execution-summary")).json()["stats"]["total"] == 1
        assert (await client.get(f"/api/v1/projects/{uuid4()}/execution-summary")).status_code == 404
        await store.cancel_run(runs[23].id, "operator cancel", now)
        updated = (await client.get(f"/api/v1/projects/{project.id}/execution-summary")).json()
        assert updated["stats"]["cancelled"] == 1
        assert updated["waiting_reasons"] == {"resource_type:docker": 1}
        empty = await projects.create(ProjectCreateRequest(project_key="empty", name="Empty"))
        assert (await client.get(f"/api/v1/projects/{empty.id}/execution-summary")).json()["stats"]["total"] == 0
