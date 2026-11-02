"""A reserved formal RunItem cannot be half-completed by a generic writer."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.run_management import router
from src.application.test_runs.run_service import TestRunService as RunService
from src.application.test_runs.run_store import PostgresTestRunStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.controlled_case_admission import CONTROLLED_CASE_ADMISSION_KEY
from src.schemas.run_management import RunItemCompleteRequest
from src.schemas.session import ExecutionEvent
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, submit, database_digest
from tests.test_controlled_case_assessment import assessment_case, freeze
from tests.test_controlled_case_task_input import source as memory_case


def completion(token, status="passed"):
    return RunService.prepare_completion(RunItemCompleteRequest(
        lease_token=token, status=status, summary="isolated completion-boundary fixture"))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["passed", "failed", "blocked"])
async def test_generic_completion_cannot_split_the_original_controlled_case_transaction(formal_case, caplog, status):
    s = formal_case
    await submit(s)
    before = database_digest(s)
    with pytest.raises(ValueError, match="atomic settlement"):
        await s.d.runs.complete_item(s.item.id, s.item.lease_token,
            completion(s.item.lease_token, status), datetime.now(timezone.utc))
    assert database_digest(s) == before
    assert any(r.message == "controlled_case_result_atomic_writer_required"
        and r.recovery_reason == "controlled_case_atomic_settlement_required" for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["transaction", "service", "http"])
async def test_all_generic_entrypoints_keep_the_controlled_reservation(formal_case, entry):
    s = formal_case
    await submit(s)
    before = database_digest(s)
    payload = RunItemCompleteRequest(lease_token=s.item.lease_token, status="passed", summary="fixture")
    service = RunService(store=s.d.runs, project_service=None, suite_service=None, test_case_service=None)
    if entry == "http":
        app = FastAPI()
        app.state.test_run_service = service
        app.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1/run-items/{s.item.id}/complete", json=payload.model_dump(mode="json"))
        assert response.status_code == 409
        assert response.json() == {"detail": "Controlled case result requires its atomic settlement"}
    elif entry == "service":
        with pytest.raises(ValueError, match="atomic settlement"):
            await service.complete_item(s.item.id, payload)
    else:
        with pytest.raises(ValueError, match="atomic settlement"):
            with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                s.d.runs._complete_item_in_transaction(cur, s.item.id, s.item.lease_token,
                    completion(s.item.lease_token), datetime.now(timezone.utc))
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_an_original_assessment_is_not_permission_for_the_generic_writer(assessment_case):
    p = assessment_case
    asset = await freeze(p)
    before = database_digest(p.s)
    with pytest.raises(ValueError, match="atomic settlement"):
        await p.s.d.runs.complete_item(p.s.item.id, p.s.item.lease_token,
            asset.completion, datetime.now(timezone.utc))
    assert database_digest(p.s) == before
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_denied_approval_writer_cannot_half_complete_a_reserved_case(formal_case):
    s = formal_case
    await submit(s)
    now = datetime.now(timezone.utc)
    # The generic wait is only a fixture state, not a native/business approval
    # continuation. The denial writer must still respect the same reservation.
    await s.d.runs.mark_waiting_approval(s.item.id, s.item.lease_token,
        "isolated-boundary-approval", "isolated-boundary-job", now)
    before = database_digest(s)
    with pytest.raises(ValueError, match="atomic settlement"):
        await s.d.runs.complete_denied_approval(s.item.id, "isolated-boundary-approval",
            completion(s.item.lease_token, "blocked"), now)
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["complete", "denied"])
async def test_an_existing_half_result_is_not_a_controlled_settlement_ack(formal_case, entry):
    s = formal_case
    await submit(s)
    now = datetime.now(timezone.utc)
    approval = "isolated-old-half-result"
    # Reproduce the old generic writer's persisted half-result, deliberately
    # using the low-level writer, not pretending it is a legal settlement.
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        item = s.d.runs._lock_item(cur, s.item.id)
        attempt = s.d.runs._lock_attempt(cur, s.item.id, s.item.lease_token)
        s.d.runs._validate_lease(item, s.item.lease_token, now, {"running"})
        s.d.runs._write_item(cur, item.model_copy(update={"approval_id": approval}))
        result = s.d.runs._complete_locked_item_in_transaction(cur,
            item.model_copy(update={"approval_id": approval}), attempt, completion(s.item.lease_token), now)
    before = database_digest(s)
    with pytest.raises(ValueError, match="atomic settlement"):
        if entry == "denied":
            await s.d.runs.complete_denied_approval(s.item.id, approval,
                completion(s.item.lease_token, "blocked"), now)
        else:
            await s.d.runs.complete_item(s.item.id, s.item.lease_token, completion(s.item.lease_token), now)
    assert (await s.d.runs.get_item(s.item.id)).result_id == result.id
    assert (await s.h.tasks.get(s.command.task_id)).status == "queued"
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["item", "attempt", "totals", "caller_after_result"])
async def test_original_completion_kernel_rolls_back_with_its_callers_transaction(formal_case, monkeypatch, failure):
    s = formal_case  # Unreserved original formal source: no AgentTask is submitted.
    store = s.d.runs
    now = datetime.now(timezone.utc)
    original = {"item": store._write_item, "attempt": store._write_attempt, "totals": store._refresh_run_in_cursor}
    names = {"item": "_write_item", "attempt": "_write_attempt", "totals": "_refresh_run_in_cursor"}
    before = database_digest(s)
    if failure != "caller_after_result":
        def fail_after_write(*args, **kwargs):
            original[failure](*args, **kwargs)
            raise RuntimeError("isolated composed transaction rollback")
        monkeypatch.setattr(store, names[failure], fail_after_write)
    with pytest.raises(RuntimeError, match="composed transaction rollback"):
        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
            s.h.tasks._lock_session(cur, s.session.id)
            def no_independent_connection(*args, **kwargs):
                raise AssertionError("completion kernel must not open an independent transaction")
            with monkeypatch.context() as patch:
                patch.setattr("src.application.test_runs.run_store.postgres_connect", no_independent_connection)
                result = store._complete_item_in_transaction(cur, s.item.id, s.item.lease_token,
                    completion(s.item.lease_token), now)
                event = ExecutionEvent(session_id=s.session.id, timestamp=now, type="test.fixture_composed_completion",
                    payload={"result_id": result.id})
                s.h.store.append_event_in_transaction(cur, s.session.id, event)
                raise RuntimeError("isolated composed transaction rollback")
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["passed", "failed", "blocked"])
async def test_original_kernel_commits_unchanged_result_timing_totals_and_event(formal_case, monkeypatch, status):
    s, store = formal_case, formal_case.d.runs
    old = await store.get_run(s.detail.run.id)
    item = await store.get_item(s.item.id)
    attempt = await store.get_latest_attempt(item.id)
    now = item.started_at + timedelta(seconds=2)
    value = completion(item.lease_token, status)
    event_id = str(uuid4())
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        s.h.tasks._lock_session(cur, s.session.id)
        result = store._complete_item_in_transaction(cur, item.id, item.lease_token, value, now)
        event = ExecutionEvent(id=event_id, session_id=s.session.id, timestamp=now, type="test.fixture_composed_completion",
            payload={"result_id": result.id})
        s.h.store.append_event_in_transaction(cur, s.session.id, event)
        assert store._complete_item_in_transaction(cur, item.id, item.lease_token, value, now) == result
    saved = await store.get_run(s.detail.run.id)
    saved_item = next(i for i in saved.items if i.id == item.id)
    saved_attempt = next(a for a in saved.attempts if a.id == attempt.id)
    assert result.model_dump(mode="json", include=set(type(value).model_fields)) == value.model_dump(mode="json")
    assert (result.run_id, result.run_item_id, result.case_id, result.case_version_id,
        result.attempt_id, result.attempt_no, result.regression_source_result_id) == (
        item.run_id, item.id, item.case_id, item.case_version_id, attempt.id, attempt.attempt_no,
        item.regression_source_result_id)
    assert saved_item.status == saved_attempt.status == result.status == status
    assert saved_item.result_id == result.id and saved_item.completed_at == saved_attempt.completed_at == result.created_at == now
    assert saved_item.active_duration_ms == saved_attempt.active_duration_ms == 2000
    assert saved_item.waiting_duration_ms == item.waiting_duration_ms
    assert saved_attempt.waiting_duration_ms == attempt.waiting_duration_ms
    assert saved_item.active_started_at is saved_item.waiting_started_at is None
    assert saved_attempt.active_started_at is saved_attempt.waiting_started_at is None
    assert saved_item.wall_clock_duration_ms == int((now - item.created_at).total_seconds() * 1000)
    assert saved_attempt.wall_clock_duration_ms == int((now - attempt.claimed_at).total_seconds() * 1000)
    expected = old.run.stats.model_dump()
    expected["running"] -= 1
    expected[status] += 1
    assert saved.run.stats.model_dump() == expected
    assert saved.run.active_duration_ms == old.run.active_duration_ms + 2000
    assert [r for r in saved.results if r.id == result.id] == [result]
    events = await s.h.store.list_events(s.session.id)
    assert [e.payload for e in events if e.id == event_id] == [{"result_id": result.id}]
    before = database_digest(s)
    def no_rewrite(*args, **kwargs):
        raise AssertionError("idempotent original completion must not rewrite results or totals")
    monkeypatch.setattr(store, "_complete_locked_item_in_transaction", no_rewrite)
    assert await store.complete_item(item.id, item.lease_token, value, now) == result
    with pytest.raises(ValueError, match="already completed"):
        await store.complete_item(item.id, "foreign-fixture-lease", value, now)
    with pytest.raises(ValueError, match="already completed"):
        await store.complete_item(item.id, item.lease_token,
            value.model_copy(update={"payload_hash": "f" * 64}), now)
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_concurrent_original_completions_use_the_same_kernel_exactly_once(formal_case, monkeypatch):
    s = formal_case
    original, called = PostgresTestRunStore._complete_locked_item_in_transaction, []
    def count(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        called.append(result.id)
        return result
    monkeypatch.setattr(PostgresTestRunStore, "_complete_locked_item_in_transaction", count)
    value, now = completion(s.item.lease_token), datetime.now(timezone.utc)
    results = await asyncio.gather(*(s.d.runs.complete_item(s.item.id, s.item.lease_token, value, now) for _ in range(4)))
    assert len(set(r.id for r in results)) == len(called) == 1
    saved = await s.d.runs.get_run(s.detail.run.id)
    assert saved.run.stats.passed == 1
    assert len([r for r in saved.results if r.run_item_id == s.item.id]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 99])
@pytest.mark.parametrize("entry", ["complete", "denied"])
async def test_memory_writer_respects_the_reserved_key_without_parsing_or_downgrading(memory_case, version, entry):
    s = memory_case
    s.store._attempts[s.attempt.id] = s.attempt.model_copy(update={
        "checkpoint_key": CONTROLLED_CASE_ADMISSION_KEY, "checkpoint_version": version,
        "checkpoint_payload": {"fixture_unknown_format": True}})
    if entry == "denied":
        await s.store.mark_waiting_approval(s.item.id, s.item.lease_token, "fixture-approval", "fixture-job", s.now)
    before = await s.store.get_run(s.detail.run.id)
    with pytest.raises(ValueError, match="atomic settlement"):
        if entry == "denied":
            await s.store.complete_denied_approval(s.item.id, "fixture-approval", completion(s.item.lease_token, "blocked"), s.now)
        else:
            await s.store.complete_item(s.item.id, s.item.lease_token, completion(s.item.lease_token), s.now)
    assert await s.store.get_run(s.detail.run.id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "agent_case_execution", "api_assertion"])
async def test_nonreserved_memory_checkpoint_keeps_original_completion_and_idempotence(memory_case, key):
    s = memory_case
    s.store._attempts[s.attempt.id] = s.attempt.model_copy(update={"checkpoint_key": key})
    value = completion(s.item.lease_token)
    result = await s.store.complete_item(s.item.id, s.item.lease_token, value, s.now)
    before = await s.store.get_run(s.detail.run.id)
    assert await s.store.complete_item(s.item.id, s.item.lease_token, value, s.now) == result
    assert await s.store.get_run(s.detail.run.id) == before
