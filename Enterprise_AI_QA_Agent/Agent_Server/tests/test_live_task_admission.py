from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from src.application.resources.resource_quota_service import ResourceQuotaService
from src.application.test_runs.case_execution import CaseExecutionOutcome
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.application.test_runs.run_store import PostgresTestRunStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.resource_quota_store import ResourceQuotaStore
from src.runtime.task_deferred import TaskDeferred
from src.schemas.run_management import RunClaimRequest, RunItemCompleteRequest, RunItemExecuteRequest, TestRunCreateRequest as CreateRun
from tests.test_live_browser_leases import harness
from tests.test_live_postgres_concurrency import _create_claim_tables, _drop_tables
from tests.test_test_run_lifecycle import _build_components

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_BROWSER_TESTS") != "1",
                              reason="requires real Redis/PostgreSQL and Chromium")


@pytest_asyncio.fixture
async def run_store(harness):
    settings = harness.settings.model_copy(deep=True)
    suffix = uuid4().hex[:12]
    tables = [f"live_admission_{kind}_{suffix}" for kind in ("run", "item", "attempt", "result")]
    (settings.database.postgres_test_run_table, settings.database.postgres_test_run_item_table,
     settings.database.postgres_test_run_attempt_table, settings.database.postgres_test_case_result_table) = tables
    try:
        _create_claim_tables(settings)
        # Use isolated tables with production columns, as the existing live
        # claim tests do; project/case fixtures are local to this test.
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            cur.execute(f"""CREATE TABLE {tables[3]} (
                id UUID PRIMARY KEY, run_id UUID NOT NULL, run_item_id UUID NOT NULL UNIQUE,
                case_id UUID NOT NULL, case_version_id UUID NOT NULL, attempt_id UUID NOT NULL,
                status TEXT NOT NULL, payload_hash TEXT NOT NULL, regression_source_result_id UUID NULL,
                created_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL)""")
        yield PostgresTestRunStore(settings)
    finally:
        _drop_tables(settings, list(reversed(tables)))


@pytest_asyncio.fixture
async def quotas(harness):
    settings = harness.settings.model_copy(deep=True)
    table = "live_admission_quota_" + uuid4().hex[:12]
    settings.database.postgres_resource_quota_table = table
    service = ResourceQuotaService(ResourceQuotaStore(settings), harness.manager)
    await service.initialize()
    try:
        yield service
    finally:
        _drop_tables(settings, [table])


async def execution(harness, adapter, run_store):
    _, project, suite, _, runs = await _build_components(case_count=2, run_store=run_store)
    detail = await runs.create_run(suite.suite.id, CreateRun(mode_key="api_testing"))
    claims = (await runs.claim(detail.run.id, RunClaimRequest(worker_id="admission-test", limit=2))).claims
    service = ExecutionService(run_service=runs, test_case_service=runs._cases, adapter=adapter,
        security_settings=harness.settings, lease_heartbeat_interval_seconds=.02)
    service.set_resource_lease_manager(harness.manager)
    return project, detail.run, claims, runs, service


def passed(item):
    return CaseExecutionOutcome(completion=RunItemCompleteRequest(lease_token=item.lease_token,
        status="passed", summary="admission integration"), tool_record=None, verification_results=[])


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", "project", "run"])
async def test_resource_free_execution_obeys_task_quota(harness, quotas, run_store, scope):
    entered, finish = asyncio.Event(), asyncio.Event()
    executions = []

    class Adapter:
        async def execute(self, **kwargs):
            executions.append(kwargs["item"].id)
            entered.set()
            if len(executions) == 1:
                await finish.wait()
            return passed(kwargs["item"])

    project, run, claims, runs, service = await execution(harness, Adapter(), run_store)
    if scope == "global":
        await harness.manager.configure_quota(scope="resource_type", identifier="agent", limit=1)
        reason = "resource_type:agent"
    else:
        scope_id = project.id if scope == "project" else run.id
        await quotas.upsert_scope(scope, scope_id, "agent", 1)
        # Reproduce the old mapping left in Redis by the preceding version.
        await harness.manager.configure_quota(scope=scope, identifier=scope_id, limit=1)
        # Persisted quotas must also survive the Worker bootstrap path.
        await quotas.initialize()
        assert await harness.manager._client.hget(harness.manager._key("quota:limits"), f"{scope}:{scope_id}") == "0"
        reason = f"{scope}_resource_type:{scope_id}:agent"
    first = asyncio.create_task(service.execute_item(claims[0].item.id,
        RunItemExecuteRequest(lease_token=claims[0].lease_token)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(TaskDeferred, match=reason):
            await service.execute_item(claims[1].item.id, RunItemExecuteRequest(lease_token=claims[1].lease_token))
        waiting = await runs.get_item(claims[1].item.id)
        assert waiting.status == "waiting_resource" and waiting.waiting_reason == reason
        assert executions == [claims[0].item.id]
        assert (await harness.manager.usage_snapshot())["resource_type:agent"] == 1
    finally:
        finish.set()
        await first
    assert not await harness.manager.usage_snapshot()
    await run_store.recover_expired(run.id, datetime.now(timezone.utc) + timedelta(seconds=120))
    retry = (await runs.claim(run.id, RunClaimRequest(worker_id="retry-worker"))).claims
    assert len(retry) == 1 and retry[0].item.id == claims[1].item.id
    assert retry[0].attempt.id != claims[1].attempt.id
    result = await service.execute_item(retry[0].item.id, RunItemExecuteRequest(lease_token=retry[0].lease_token))
    assert result.status == "passed"
    assert executions == [claims[0].item.id, claims[1].item.id]
    assert not await harness.manager.usage_snapshot()


@pytest.mark.asyncio
async def test_physical_capacity_wait_releases_admitted_task_slot(harness):
    from tests.test_case_execution_service import _records
    _, run, item, _, version = _records()
    version = version.model_copy(update={"test_data": {"runner_arguments": {"test_account_id": "shared-account"}}})
    occupied = await harness.manager.acquire(resource_type="test_account", resource_id="shared-account",
        worker_id="other-worker", lease_seconds=30)
    service = ExecutionService(run_service=None, test_case_service=None, adapter=None,
        security_settings=harness.settings)
    service.set_resource_lease_manager(harness.manager)
    from src.runtime.resource_lease_manager import ResourceUnavailable
    try:
        with pytest.raises(ResourceUnavailable, match="resource_busy"):
            await service._claim_execution_resources(run=run, item=item, version=version, attempt_id="attempt")
        assert (await harness.manager.usage_snapshot()).get("resource_type:agent", 0) == 0
        assert (await harness.manager.usage_snapshot())["resource_type:test_account"] == 1
    finally:
        await harness.manager.retire(occupied)


@pytest.mark.asyncio
async def test_full_task_quota_allows_its_browser_and_does_not_charge_physical_slots(harness, quotas, run_store):
    browser = harness.service()

    class Adapter:
        async def execute(self, **kwargs):
            run, item = kwargs["run"], kwargs["item"]
            ctx = {"session_id": "admission-session", "turn_id": "admission-turn", "context_bundle": {
                "project_id": run.project_id, "test_run_id": run.id, "run_item_id": item.id,
                "attempt_id": kwargs["attempt_id"],
            }}
            result = await browser.call("browser", "browser-control", {
                "action": "command", "args": ["eval", "40 + 2"], "raw": True,
            }, ctx)
            assert result["status"] == "success" and result["stdout"].strip() == "42"
            usage = await harness.manager.usage_snapshot()
            assert usage["resource_type:agent"] == usage["global:all"] == 1
            assert usage[f"project_resource_type:{run.project_id}:agent"] == 1
            await browser.call("browser", "browser-control", {"action": "command", "args": ["close"]}, ctx)
            return passed(item)

    project, run, claims, _, service = await execution(harness, Adapter(), run_store)
    await harness.manager.configure_quota(scope="global", identifier="all", limit=1)
    await harness.manager.configure_quota(scope="resource_type", identifier="agent", limit=1)
    await quotas.upsert(project.id, "agent", 1)
    await quotas.upsert_scope("run", run.id, "agent", 1)
    result = await service.execute_item(claims[0].item.id, RunItemExecuteRequest(lease_token=claims[0].lease_token))
    assert result.status == "passed", result.error_message
    assert (await harness.manager.usage_snapshot()).get("resource_type:agent", 0) == 0
    await harness.cleanup_all()
    assert not await harness.manager.usage_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["resource", "item"])
async def test_lease_loss_cancels_inflight_execution_before_releasing_task_slot(harness, monkeypatch, run_store, lost):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    during_cleanup = []

    class Adapter:
        async def execute(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                during_cleanup.append((await harness.manager.usage_snapshot()).get("resource_type:agent", 0))
                cancelled.set()

    _, _, claims, runs, service = await execution(harness, Adapter(), run_store)
    task = asyncio.create_task(service.execute_item(claims[0].item.id,
        RunItemExecuteRequest(lease_token=claims[0].lease_token)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if lost == "resource":
            key = harness.manager._lease_key("agent", f"run-item:{claims[0].item.id}")
            await harness.manager._client.delete(key)
        else:
            async def fail(*args):
                raise RuntimeError("injected item lease ownership loss")
            monkeypatch.setattr(runs, "heartbeat_item", fail)
        result = await asyncio.wait_for(asyncio.shield(task), 2)
        assert result.status == "error"
        assert cancelled.is_set() and during_cleanup == [1]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if lost == "resource":
            await harness.manager._client.zadd(harness.manager._key("lease:registry"), {key: 0})
            await harness.manager.reap_expired()


@pytest.mark.asyncio
async def test_repeated_worker_cancellation_waits_for_runner_cleanup(harness, run_store):
    entered, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleanup_completed = []

    class Adapter:
        async def execute(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish.wait()
                cleanup_completed.append(True)

    _, _, claims, _, service = await execution(harness, Adapter(), run_store)
    task = asyncio.create_task(service.execute_item(claims[0].item.id,
        RunItemExecuteRequest(lease_token=claims[0].lease_token)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.wait_for(cleaning.wait(), 5)
        task.cancel()
        await asyncio.sleep(.05)
        assert not task.done()
        assert (await harness.manager.usage_snapshot())["resource_type:agent"] == 1
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
    assert cleanup_completed == [True]
    assert not await harness.manager.usage_snapshot()
