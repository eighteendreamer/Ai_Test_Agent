from __future__ import annotations

import json
from uuid import uuid4

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import event

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import postgres_engine
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness, config, start
from tests.test_framework_model_admission import admission, counts, submit
from tests.test_framework_model_calls import model_calls_harness, begin, intent, policy, usage
from tests.test_framework_model_evidence import reference
from tests.test_framework_approval_projection import projection_harness, prepare, projector


def isolate_absent_stores(h):
    # A missing optional store must never fall through to production tables.
    h.settings.deep_agents.checkpoint_schema = f"diagnostic_{uuid4().hex}"
    h.settings.database.postgres_tool_job_table = f"diagnostic_job_{uuid4().hex}"


async def diagnose(h, task_id):
    return await h.tasks.diagnose_framework_message(task_id, session_id=h.session_id)


@pytest.mark.asyncio
async def test_admitted_input_without_checkpoint_is_not_replay_permission(admission):
    h = admission
    isolate_absent_stores(h)
    task = await submit(h)
    before = counts(h)
    report = await diagnose(h, task.id)
    assert report.task.status == "queued" and report.task.attempt_id is None
    assert report.binding == h.binding
    assert report.input.payload_hash_matches and report.input.user_message_matches
    assert report.model_budget.policy == h.budget and report.model_budget.reserved_calls == 0
    assert report.checkpoints == [] and report.tool_jobs == []
    assert {"checkpoint_store_missing", "tool_job_store_missing", "stage_recovery_not_implemented"} <= set(report.issues)
    assert report.stage.phase == "submitted" and "stage_admission_unavailable" not in report.issues
    assert report.recovery_status == "recovery_required" and report.automatic_replay_allowed is False
    assert counts(h) == before
    assert (await h.tasks.get(task.id)).status == "queued"
    assert report.observed_at.tzinfo is not None and report.snapshot_id


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ready", "invoking", "unknown", "completed"])
async def test_diagnosis_preserves_model_phase_budget_and_non_authoritative_evidence(model_calls_harness, phase):
    h = model_calls_harness
    isolate_absent_stores(h)
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    request = reference()
    selected = intent(request_sha256=request.sha256)
    await h.calls.prepare(h.owner, selected, request=request)
    if phase != "ready":
        await h.calls.start(h.owner, selected.attempt_id)
        await h.calls.append_response_observation(h.owner, selected.attempt_id,
            response=reference(b'{"fixture":"late-response"}'), response_kind="langchain_messages")
    if phase == "unknown":
        await h.calls.mark_unknown(h.owner, selected.attempt_id, code="transport_unknown")
    if phase == "completed":
        await h.calls.complete(h.owner, selected.attempt_id, usage=usage())
    before = await h.calls.snapshot(h.binding)
    report = await diagnose(h, h.task.id)
    call, = report.model_calls
    assert call.attempt_id == selected.attempt_id and call.phase == phase
    assert call.request == request and call.usage == before.calls[0].usage
    assert all(item.authoritative is False for item in call.response_observations)
    assert report.model_budget.charged_tokens == before.charged_tokens
    assert report.owner.matches_origin and report.owner.lease_active
    assert report.automatic_replay_allowed is False
    assert await h.calls.snapshot(h.binding) == before
    serialized = report.model_dump_json()
    assert h.owner.owner_token not in serialized and "observer_token_sha256" not in serialized


@pytest.mark.asyncio
async def test_missing_user_message_and_changed_input_are_explicit(admission):
    h = admission
    isolate_absent_stores(h)
    task = await submit(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {h.settings.database.postgres_message_table} WHERE id=%s", (h.message.id,))
        cur.execute(f"UPDATE {h.tasks._tasks} SET input_payload=input_payload || %s::jsonb WHERE id=%s",
            (json.dumps({"content": "diagnostic-secret-prompt"}), task.id))
    report = await diagnose(h, task.id)
    assert not report.input.user_message_present and not report.input.payload_hash_matches
    assert {"user_message_missing", "input_hash_mismatch"} <= set(report.issues)
    assert "diagnostic-secret-prompt" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_diagnostic_is_scoped_to_session_and_initial_message(admission):
    h = admission
    isolate_absent_stores(h)
    task = await submit(h)
    with pytest.raises(KeyError):
        await h.tasks.diagnose_framework_message(task.id, session_id="different-session")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks} SET operation='approval_resume' WHERE id=%s", (task.id,))
    with pytest.raises(ValueError, match="initial message"):
        await diagnose(h, task.id)


@pytest.mark.asyncio
async def test_corrupt_root_binding_cannot_probe_another_turn(admission):
    h = admission
    isolate_absent_stores(h)
    task = await submit(h)
    other = h.binding.model_copy(update={"turn_id": "another-turn", "thread_id": "another-turn"})
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks} SET runtime_binding=%s::jsonb WHERE id=%s",
            (other.model_dump_json(), task.id))
    with pytest.raises(ValueError, match="binding identity"):
        await diagnose(h, task.id)


@pytest.mark.asyncio
async def test_terminal_attempt_and_expired_owner_are_observed_without_claim(admission):
    h = admission
    isolate_absent_stores(h)
    task = await submit(h)
    claimed = await h.tasks.claim(task.id, worker_id="diagnostic-origin", expected_binding=h.binding)
    await h.tasks.begin(claimed, resource_id="diagnostic", fencing_token=1)
    assert await h.store.renew_turn_execution(h.session_id, claimed.lease_token, 0)
    await h.tasks.claim(task.id, worker_id="expire-observer", expected_binding=h.binding)
    before = await h.tasks.get(task.id)
    report = await diagnose(h, task.id)
    assert report.task.status == "recovery_required" and report.task.attempt_id == claimed.attempt_id
    assert report.task.attempt_status == "recovery_required"
    assert report.owner.matches_origin and not report.owner.lease_active
    assert "owner_lease_inactive" in report.issues
    assert await h.tasks.get(task.id) == before


@pytest.mark.asyncio
async def test_checkpoint_diagnostic_reads_native_metadata_without_opening_provider(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    h.settings.database.postgres_tool_job_table = f"diagnostic_job_{uuid4().hex}"
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        first = await saver.aput(config(h), empty_checkpoint(), {"step": 1, "source": "loop"}, {})
        latest = await saver.aput(first, empty_checkpoint(), {"step": 2, "source": "loop"}, {})
        await saver.aput_writes(latest, [("__interrupt__", ["diagnostic-secret-interrupt"])], "tool-task")
        nested = await saver.aput(config(h, "child:task"), empty_checkpoint(), {}, {})

    def forbidden(*args, **kwargs):
        pytest.fail("Diagnosis must not initialize a provider or invoke the graph")

    monkeypatch.setattr(h.provider, "open", forbidden)
    report = await diagnose(h, h.owner.id)
    roots = {item.namespace: item for item in report.checkpoints}
    assert set(roots) == {h.binding.checkpoint_namespace, h.binding.checkpoint_namespace + "|child:task"}
    root = roots[h.binding.checkpoint_namespace]
    assert root.checkpoint_id == latest["configurable"]["checkpoint_id"]
    assert root.parent_checkpoint_id == first["configurable"]["checkpoint_id"] and root.step == 2
    assert root.pending_writes[0].channel == "__interrupt__"
    assert roots[h.binding.checkpoint_namespace + "|child:task"].checkpoint_id == nested["configurable"]["checkpoint_id"]
    assert "diagnostic-secret-interrupt" not in report.model_dump_json()
    assert "checkpoint_not_resume_permission" in report.issues


@pytest.mark.asyncio
async def test_diagnostic_uses_one_read_only_repeatable_read_snapshot(admission):
    h = admission
    isolate_absent_stores(h)
    task = await submit(h)
    observed = []

    def concurrent_commit(conn, cursor, statement, parameters, context, executemany):
        if h.tasks._tasks not in statement or observed:
            return
        cursor.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation')")
        observed.append(cursor.fetchone())
        # Commit after the diagnostic's snapshot has been established, before
        # its later budget query. Both facts must still come from one snapshot.
        with postgres_connect(h.settings) as other, other.cursor() as cur:
            cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy, '{{max_calls}}', '99')")

    engine = postgres_engine(h.settings)
    event.listen(engine, "before_cursor_execute", concurrent_commit)
    try:
        report = await diagnose(h, task.id)
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_commit)
    assert observed == [("on", "repeatable read")]
    assert report.model_budget.policy.max_calls == h.budget.max_calls
    assert (await h.calls.snapshot(h.binding)).policy.max_calls == 99


@pytest.mark.asyncio
async def test_corrupt_ledger_is_unknown_accounting_not_zero_calls(admission):
    h = admission
    isolate_absent_stores(h)
    task = await submit(h)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {h.calls.calls_table}(binding_id, attempt_id, record) VALUES(%s, %s, %s::jsonb)",
            (h.binding.binding_id, "broken-record", '{"phase":"invoking"}'))
    report = await diagnose(h, task.id)
    assert "model_call_invalid" in report.issues
    assert report.model_budget.ledger_rows == 1 and not report.model_budget.accounting_complete
    assert report.model_budget.reserved_calls is None and report.model_budget.charged_tokens is None


@pytest.mark.asyncio
async def test_wire_references_preserve_observation_limits_and_hide_headers(model_calls_harness):
    h = model_calls_harness
    isolate_absent_stores(h)
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent()
    await h.calls.prepare(h.owner, selected)
    await h.calls.start(h.owner, selected.attempt_id)
    response = reference()
    await h.calls.append_wire_observation(h.owner, selected.attempt_id, observation={
        "response": response, "response_complete": False,
        "response_body_truncated": True, "attempt_sequence": 1,
        "response_headers": {"x-diagnostic-test": "diagnostic-secret-header"}})
    report = await diagnose(h, h.task.id)
    wire, = report.model_calls[0].wire_observations
    assert wire.response == response and wire.authoritative is False
    assert wire.response_body_truncated and not wire.response_complete
    assert report.evidence_objects_verified is False
    assert "diagnostic-secret-header" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_native_tool_job_and_turn_projection_are_read_without_execution(projection_harness):
    h = projection_harness
    proposal = prepare(h)
    await projector(h).project(h.owner.runtime_owner, proposal)
    before = await h.jobs.get_job(proposal.job.id)
    report = await diagnose(h, h.owner.id)
    job, = report.tool_jobs
    assert job.id == before.id and job.status == "waiting_approval" and job.binding_matches
    assert job.started_at is None and not job.output_present
    assert job.execution_attempt_id is None and report.checkpoints
    assert h.native.calls == [] and await h.jobs.get_job(proposal.job.id) == before


@pytest.mark.asyncio
async def test_started_tool_keeps_original_execution_reference_without_repair(projection_harness):
    from src.runtime.execution_context import framework_owner_scope

    h = projection_harness
    proposal = prepare(h)
    await projector(h).project(h.owner.runtime_owner, proposal)
    with framework_owner_scope(h.owner.runtime_owner):
        claimed = await h.job_store.claim_job_execution(proposal.job.id)
    assert claimed is not None
    report = await diagnose(h, h.owner.id)
    job, = report.tool_jobs
    assert job.status == "running" and job.started_at is not None and not job.output_present
    assert job.execution_task_id == h.owner.id and job.execution_attempt_id == h.owner.attempt_id
    assert job.execution_epoch == h.owner.runtime_owner.owner_epoch
    assert "tool_job_requires_reconciliation" in report.issues
    assert h.native.calls == [] and await h.job_store.get_job(job.id) == claimed
    assert claimed.metadata["execution_owner_token"] not in report.model_dump_json()


@pytest.mark.asyncio
async def test_projection_selects_original_turn_without_exposing_transcript(admission):
    from datetime import datetime, timezone
    from src.schemas.session import SessionSnapshot

    h = admission
    isolate_absent_stores(h)
    task = await submit(h)
    for version, turn in [(1, h.binding.turn_id), (2, "other-turn")]:
        await h.store.save_snapshot(h.session_id, SessionSnapshot(id=uuid4().hex, session_id=h.session_id,
            version=version, stage="completed", created_at=datetime.now(timezone.utc),
            graph_state={"turn_id": turn, "framework_messages": [{"content": "diagnostic-secret-transcript"}]}))
    report = await diagnose(h, task.id)
    assert report.projection.snapshot_version == 1 and report.projection.snapshot_stage == "completed"
    assert report.projection.framework_message_count == 1
    assert "diagnostic-secret-transcript" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_namespace_boundary_excludes_other_threads_and_similar_prefixes(checkpoint_harness):
    from psycopg import sql

    h = checkpoint_harness
    h.settings.database.postgres_tool_job_table = f"diagnostic_job_{uuid4().hex}"
    await start(h)
    async with h.provider.open(owner=h.owner.runtime_owner) as saver:
        root = await saver.aput(config(h), empty_checkpoint(), {}, {})
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        table = sql.Identifier(h.settings.deep_agents.checkpoint_schema, "checkpoints").as_string()
        for thread, namespace in [(h.binding.thread_id, h.binding.checkpoint_namespace + "-other"),
            ("other-thread", h.binding.checkpoint_namespace)]:
            cur.execute(f"INSERT INTO {table}(thread_id,checkpoint_ns,checkpoint_id,checkpoint) VALUES(%s,%s,%s,%s::jsonb)",
                (thread, namespace, empty_checkpoint()["id"], '{}'))
    report = await diagnose(h, h.owner.id)
    checkpoint, = report.checkpoints
    assert checkpoint.checkpoint_id == root["configurable"]["checkpoint_id"]
