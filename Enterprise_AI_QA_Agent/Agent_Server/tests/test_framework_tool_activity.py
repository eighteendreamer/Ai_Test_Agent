"""Server-observed tool activity survives waits, store rebuilds and rollback."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event

from src.application.runtime.tool_job_service import ToolJobService
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import postgres_engine
from src.runtime.execution_context import framework_owner_scope
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.tool_job import ToolJobRecord, ToolJobStatus
from tests.test_agent_task_dispatch import harness
from tests.test_framework_model_calls import model_calls_harness, begin
from tests.test_live_postgres_concurrency import _drop_tables


@pytest_asyncio.fixture
async def tool_activity_harness(model_calls_harness):
    h = model_calls_harness
    suffix = uuid4().hex[:12]
    h.settings.database.postgres_tool_job_table = f"tool_activity_{suffix}"
    h.settings.database.postgres_tool_artifact_table = f"tool_activity_art_{suffix}"
    h.job_store = PostgresToolJobStore(h.settings)
    h.jobs = ToolJobService(h.job_store)
    await h.job_store.initialize()
    await begin(h)
    now = datetime.now(timezone.utc)
    with framework_owner_scope(h.owner):
        h.job = await h.job_store.save_job(ToolJobRecord(id=uuid4().hex,
            session_id=h.session_id, turn_id=h.binding.turn_id, trace_id="activity-test",
            call_id="activity-call", tool_key="fixture", tool_name="Fixture",
            status=ToolJobStatus.queued, created_at=now, updated_at=now))
    try:
        yield h
    finally:
        _drop_tables(h.settings, [h.settings.database.postgres_tool_artifact_table,
            h.settings.database.postgres_tool_job_table])


@pytest.mark.asyncio
async def test_durable_tool_activity_excludes_child_wait_after_store_rebuild(tool_activity_harness):
    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        await h.jobs.claim_execution(h.job.id)
        await h.jobs.mark_waiting_children(h.job.id, summary="waiting", metadata={"children": ["child"]})
        # Real persistence and real transitions; only the wait is synthetic.
        await asyncio.sleep(0.05)
        h.job_store = PostgresToolJobStore(h.settings)
        h.jobs = ToolJobService(h.job_store)
        await h.jobs.claim_child_wait(h.job.id)
        await h.jobs.observe_output(h.job.id, status="completed", output_payload={"result": "known"})
        saved = await h.jobs.finish_output_processing(h.job.id, status=ToolJobStatus.completed,
            output_payload={"result": "known"}, summary="done")

    has_journal = "framework_tool_activity" in saved.metadata
    assert has_journal
    from src.runtime.framework_tool_activity import read_tool_activity

    journal = read_tool_activity(saved)
    assert journal.origin_observed
    assert [item.status for item in journal.events] == ["queued", "running", "waiting_children",
        "running", "running", "completed"]
    assert all(item.observed_at.tzinfo is not None for item in journal.events)
    assert [item.output_observed for item in journal.events] == [False, False, False, False, True, True]
    assert journal.activity_intervals() == [
        (journal.events[1].observed_at, journal.events[2].observed_at),
        (journal.events[3].observed_at, journal.events[4].observed_at),
    ]
    assert journal.events[3].observed_at - journal.events[2].observed_at >= timedelta(seconds=0.05)
    assert await h.job_store.get_job(h.job.id) == saved


@pytest.mark.asyncio
async def test_cancel_signal_does_not_close_unknown_external_tail(tool_activity_harness):
    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        await h.jobs.claim_execution(h.job.id)
        saved = await h.jobs.cancel_job(h.job.id, reason="stop requested, physical stop unconfirmed")
    has_journal = "framework_tool_activity" in saved.metadata
    assert has_journal
    from src.runtime.framework_tool_activity import read_tool_activity

    journal = read_tool_activity(saved)
    with pytest.raises(ValueError, match="explicit as_of"):
        journal.activity_intervals()
    end = journal.events[-1].observed_at + timedelta(seconds=10)
    assert journal.activity_intervals(as_of=end) == [(journal.events[1].observed_at, end)]


@pytest.mark.asyncio
async def test_candidate_metadata_cannot_forge_a_returned_output_boundary(tool_activity_harness):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        running = await h.jobs.claim_execution(h.job.id)
        candidate = running.model_copy(deep=True)
        candidate.metadata["tool_output_observation"] = {"claim": "not a native output observation"}
        saved = await h.job_store.save_job(candidate)
    journal = read_tool_activity(saved)
    assert not journal.events[-1].output_observed
    end = journal.events[-1].observed_at + timedelta(seconds=5)
    assert journal.activity_intervals(as_of=end) == [(journal.events[1].observed_at, end)]


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting", ["waiting_approval", "waiting_resource"])
async def test_whole_row_wait_transitions_use_server_time_and_keep_both_segments(tool_activity_harness, waiting):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS observed_at")
        before = cur.fetchone()["observed_at"]
    with framework_owner_scope(h.owner):
        running = await h.jobs.claim_execution(h.job.id)
        # Caller timestamps must not rewrite the actual transition clock.
        candidate = running.model_copy(update={"status": ToolJobStatus(waiting),
            "updated_at": before + timedelta(days=10)})
        paused = await h.job_store.save_job(candidate)
        await asyncio.sleep(0.03)
        await h.jobs.mark_running(paused)
        saved = await h.jobs.finish_execution(h.job.id, status=ToolJobStatus.completed,
            output_payload={"result": "known"}, summary="done")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS observed_at")
        after = cur.fetchone()["observed_at"]
    journal = read_tool_activity(saved)
    assert all(before <= event.observed_at <= after for event in journal.events[1:])
    assert [event.status for event in journal.events] == ["queued", "running", waiting, "running", "completed"]
    assert journal.activity_intervals() == [
        (journal.events[1].observed_at, journal.events[2].observed_at),
        (journal.events[3].observed_at, journal.events[4].observed_at),
    ]


@pytest.mark.asyncio
async def test_heartbeat_failed_cas_and_repeated_return_do_not_duplicate_activity(tool_activity_harness):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        await h.jobs.claim_execution(h.job.id)
        assert await h.jobs.claim_execution(h.job.id) is None
        before = await h.jobs.get_job(h.job.id)
        await h.jobs.heartbeat_execution(h.job.id)
        assert (await h.jobs.get_job(h.job.id)).metadata["framework_tool_activity"] == before.metadata["framework_tool_activity"]
        await h.jobs.observe_output(h.job.id, status="completed", output_payload={"result": "known"})
        await h.jobs.observe_output(h.job.id, status="completed", output_payload={"result": "known"})
        saved = await h.jobs.finish_output_processing(h.job.id, status=ToolJobStatus.completed,
            output_payload={"result": "known"}, summary="done")
        assert await h.jobs.claim_child_wait(h.job.id) is None
        assert await h.jobs.finish_execution(h.job.id, status=ToolJobStatus.completed,
            output_payload={"result": "duplicate"}, summary="duplicate") is None
    journal = read_tool_activity(saved)
    assert [event.status for event in journal.events] == ["queued", "running", "running", "completed"]
    unchanged = await h.jobs.get_job(h.job.id) == saved
    assert unchanged


@pytest.mark.asyncio
async def test_candidate_cannot_replace_or_remove_server_owned_history(tool_activity_harness):
    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        running = await h.jobs.claim_execution(h.job.id)
        expected = running.metadata["framework_tool_activity"]
        for replacement in (None, {"schema_version": 99, "events": []}):
            candidate = running.model_copy(deep=True)
            if replacement is None:
                candidate.metadata.pop("framework_tool_activity")
            else:
                candidate.metadata["framework_tool_activity"] = replacement
            saved = await h.job_store.save_job(candidate)
            assert saved.metadata["framework_tool_activity"] == expected
        saved = await h.jobs.mark_waiting_children(h.job.id, summary="wait",
            metadata={"framework_tool_activity": {"claim": "forged"}})
    assert len(saved.metadata["framework_tool_activity"]["events"]) == 3
    assert saved.metadata["framework_tool_activity"]["events"][:2] == expected["events"]


@pytest.mark.asyncio
async def test_activity_and_tool_transition_rollback_in_the_callers_transaction(tool_activity_harness):
    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        running = await h.jobs.claim_execution(h.job.id)
        with pytest.raises(RuntimeError, match="rollback fixture"):
            with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                h.job_store.save_job_in_transaction(cur,
                    running.model_copy(update={"status": ToolJobStatus.waiting_resource}))
                raise RuntimeError("rollback fixture")
    unchanged = await h.jobs.get_job(h.job.id) == running
    assert unchanged


@pytest.mark.asyncio
async def test_existing_row_without_activity_remains_explicitly_incomplete(tool_activity_harness):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} "
            "SET metadata=metadata-'framework_tool_activity' WHERE id=%s", (h.job.id,))
    assert read_tool_activity(await h.jobs.get_job(h.job.id)) is None
    with framework_owner_scope(h.owner):
        saved = await h.jobs.claim_execution(h.job.id)
    journal = read_tool_activity(saved)
    assert not journal.origin_observed
    assert journal.events[0].observed_at == journal.events[1].observed_at


@pytest.mark.asyncio
async def test_invalid_activity_does_not_gate_a_legal_transition_and_remains_visible(tool_activity_harness, caplog):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET metadata="
            "jsonb_set(metadata,'{framework_tool_activity,schema_version}','99'::jsonb) WHERE id=%s", (h.job.id,))
    caplog.set_level(logging.INFO, logger="src.runtime.postgres_tool_job_store")
    with framework_owner_scope(h.owner):
        saved = await h.jobs.claim_execution(h.job.id)
    assert saved.status == ToolJobStatus.running
    assert saved.metadata["framework_tool_activity"]["schema_version"] == 99
    with pytest.raises(ValueError):
        read_tool_activity(saved)
    assert "framework_tool_activity_record_rejected" in caplog.messages
    assert "framework_tool_activity_transition_recorded" not in caplog.messages


@pytest.mark.asyncio
async def test_create_if_absent_initializes_history_once_without_changing_legacy_jobs(tool_activity_harness):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    candidate = h.job.model_copy(deep=True, update={"id": uuid4().hex, "metadata": {}})
    with framework_owner_scope(h.owner):
        created = await h.job_store.create_job_if_absent(candidate)
        duplicate = await h.job_store.create_job_if_absent(candidate)
    assert read_tool_activity(created).origin_observed
    assert created.metadata["framework_tool_activity"] == duplicate.metadata["framework_tool_activity"]
    legacy = await h.job_store.save_job(h.job.model_copy(deep=True,
        update={"id": uuid4().hex, "turn_id": "legacy-turn", "metadata": {}}))
    legacy = await h.job_store.claim_job_execution(legacy.id)
    assert read_tool_activity(legacy) is None


@pytest.mark.asyncio
async def test_failed_without_return_and_resume_signal_preserve_unknown_tail(tool_activity_harness):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        await h.jobs.claim_execution(h.job.id)
        await h.jobs.request_resume(h.job.id, reason="not a physical restart")
        saved = await h.jobs.mark_failed(h.job.id, summary="response unknown", error_message="timeout")
    journal = read_tool_activity(saved)
    with pytest.raises(ValueError, match="explicit as_of"):
        journal.activity_intervals()
    end = journal.events[-1].observed_at + timedelta(seconds=2)
    assert journal.activity_intervals(as_of=end) == [(journal.events[1].observed_at, end)]


@pytest.mark.parametrize("changed", ["future_version", "naive_time", "reverse_time", "forget_return", "false_origin"])
def test_activity_reader_rejects_unsupported_or_contradictory_history(changed):
    from src.schemas.framework_tool_activity import FrameworkToolActivity

    base = datetime(2026, 10, 30, tzinfo=timezone.utc)
    events = [{"status": "queued", "output_observed": False, "observed_at": base},
        {"status": "running", "output_observed": False, "observed_at": base + timedelta(seconds=1)}]
    value = {"job_id": "job", "binding_id": "binding", "session_id": "session", "turn_id": "turn",
        "origin_observed": True, "events": events}
    if changed == "future_version":
        value["schema_version"] = 99
    elif changed == "naive_time":
        events[0]["observed_at"] = base.replace(tzinfo=None)
    elif changed == "reverse_time":
        events[1]["observed_at"] = base - timedelta(seconds=1)
    elif changed == "forget_return":
        events[0]["output_observed"] = True
    else:
        events[0]["status"] = "running"
    with pytest.raises(ValueError):
        FrameworkToolActivity.model_validate(value)


@pytest.mark.asyncio
async def test_concurrent_child_claim_records_only_the_winning_transition(tool_activity_harness):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        await h.jobs.claim_execution(h.job.id)
        await h.jobs.mark_waiting_children(h.job.id, summary="wait", metadata={})
        claimed = await asyncio.gather(h.jobs.claim_child_wait(h.job.id), h.jobs.claim_child_wait(h.job.id))
    assert sum(job is not None for job in claimed) == 1
    journal = read_tool_activity(await h.jobs.get_job(h.job.id))
    assert [item.status for item in journal.events] == ["queued", "running", "waiting_children", "running"]


@pytest.mark.asyncio
async def test_journal_storage_failure_rolls_back_the_actual_tool_claim(tool_activity_harness):
    h = tool_activity_harness
    before = await h.jobs.get_job(h.job.id)
    engine = postgres_engine(h.settings)

    def reject_journal(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"UPDATE {h.settings.database.postgres_tool_job_table} SET metadata=jsonb_set"):
            raise RuntimeError("journal storage fault fixture")

    event.listen(engine, "before_cursor_execute", reject_journal)
    try:
        with framework_owner_scope(h.owner), pytest.raises(RuntimeError, match="journal storage fault"):
            await h.jobs.claim_execution(h.job.id)
    finally:
        event.remove(engine, "before_cursor_execute", reject_journal)
    unchanged = await h.jobs.get_job(h.job.id) == before
    assert unchanged


@pytest.mark.asyncio
async def test_native_returned_failure_closes_local_activity_without_faking_success(tool_activity_harness):
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with framework_owner_scope(h.owner):
        await h.jobs.claim_execution(h.job.id)
        await h.jobs.observe_output(h.job.id, status="failed", output_payload={"error": "known native failure"})
        saved = await h.jobs.finish_output_processing(h.job.id, status=ToolJobStatus.failed,
            output_payload={"error": "known native failure"}, summary="failed")
    journal = read_tool_activity(saved)
    assert saved.status == ToolJobStatus.failed
    assert journal.activity_intervals() == [(journal.events[1].observed_at, journal.events[2].observed_at)]


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["job_id", "binding_id", "session_id", "turn_id"])
async def test_read_activity_rejects_persisted_identity_drift(tool_activity_harness, identity):
    import json
    from src.runtime.framework_tool_activity import read_tool_activity

    h = tool_activity_harness
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET metadata="
            "jsonb_set(metadata,%s,%s::jsonb) WHERE id=%s",
            (["framework_tool_activity", identity], json.dumps("foreign"), h.job.id))
    with pytest.raises(ValueError, match="durable job identity"):
        read_tool_activity(await h.jobs.get_job(h.job.id))


@pytest.mark.asyncio
async def test_case_alias_sql_records_the_same_non_execution_transition(tool_activity_harness, monkeypatch):
    from types import SimpleNamespace
    from src.runtime.framework_tool_activity import read_tool_activity
    from src.runtime.framework_tool_authority import lock_tool_authority
    from src.runtime.postgres_case_action_admission import PostgresCaseActionAdmission

    h = tool_activity_harness
    admission = PostgresCaseActionAdmission(runs=SimpleNamespace(_settings=h.settings), jobs=h.job_store)
    # This isolates the alias SQL writer, not the full domain admission proof.
    monkeypatch.setattr("src.runtime.postgres_case_action_admission.operation_receipt",
        lambda *args, **kwargs: {"fixture": "receipt"})
    origin = h.job.model_copy(update={"id": "original-operation"})
    with framework_owner_scope(h.owner):
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            lock_tool_authority(cur, h.settings, h.job.id)
            saved = admission._operation_alias(cur, h.job, {}, {"operation_id": "fixture"}, origin, "input_conflict")
    journal = read_tool_activity(saved)
    assert [item.status for item in journal.events] == ["queued", "denied"]
    assert saved.started_at is None and journal.activity_intervals() == []
