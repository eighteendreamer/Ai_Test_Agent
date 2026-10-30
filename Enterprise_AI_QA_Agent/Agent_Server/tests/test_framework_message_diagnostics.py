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
    assert {"checkpoint_store_missing", "tool_job_store_missing", "stage_recovery_requires_admission"} <= set(report.issues)
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
    assert call.settled_response_id == before.calls[0].settled_response_id
    if phase == "completed":
        assert "model_settled_response_missing" in report.issues
    assert all(item.authoritative is False for item in call.response_observations)
    assert report.model_budget.charged_tokens == before.charged_tokens
    assert report.model_budget.root_activity_seconds is not None
    assert report.model_budget.root_activity_sources_available is False
    assert report.model_budget.root_activity_scope == "persisted_execution_envelope"
    assert "root_activity_execution_envelope_only" in report.issues
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
    assert report.model_budget.root_activity_seconds is None
    assert not report.model_budget.root_activity_sources_available


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["naive_start", "future_start", "owner_binding"])
async def test_invalid_activity_is_visible_without_hiding_other_diagnostic_facts(model_calls_harness, changed):
    from datetime import datetime, timedelta, timezone
    from src.runtime.postgres_runtime_owner import runtime_owner_table

    h = model_calls_harness
    isolate_absent_stores(h)
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    selected = intent()
    await h.calls.prepare(h.owner, selected)
    await h.calls.start(h.owner, selected.attempt_id)
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "owner_binding":
            cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET runtime_binding="
                "jsonb_set(runtime_binding,'{thread_id}',%s::jsonb) WHERE binding_id=%s",
                (json.dumps("changed-thread"), h.binding.binding_id))
        else:
            start_time = (datetime.now() if changed == "naive_start" else
                datetime.now(timezone.utc) + timedelta(minutes=10))
            cur.execute(f"UPDATE {h.calls.calls_table} SET record=jsonb_set(record,'{{started_at}}',%s::jsonb) "
                "WHERE binding_id=%s", (json.dumps(start_time.isoformat()), h.binding.binding_id))
    before = await h.tasks.get(h.task.id)

    report = await diagnose(h, h.task.id)

    assert report.model_budget.reserved_calls == 1
    assert report.model_budget.root_activity_seconds is None
    assert not report.model_budget.root_activity_sources_available
    assert "root_activity_invalid" in report.issues
    assert len(report.model_calls) == 1 and not report.automatic_replay_allowed
    assert await h.tasks.get(h.task.id) == before


@pytest.mark.asyncio
async def test_activity_query_unions_actual_isolated_stores_without_writing_or_shortening_unknown_tail(model_calls_harness):
    from datetime import datetime, timedelta, timezone
    from src.runtime.execution_context import framework_owner_scope
    from src.runtime.framework_tool_authority import execution_reference
    from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
    from src.schemas.tool_job import ToolJobRecord, ToolJobStatus
    from tests.test_live_postgres_concurrency import _drop_tables

    h = model_calls_harness
    suffix = uuid4().hex[:12]
    h.settings.database.postgres_tool_job_table = f"activity_job_{suffix}"
    h.settings.database.postgres_tool_artifact_table = f"activity_artifact_{suffix}"
    jobs = PostgresToolJobStore(h.settings)
    await jobs.initialize()
    try:
        await begin(h)
        await h.calls.freeze(h.owner, policy(h))
        selected = intent()
        await h.calls.prepare(h.owner, selected)
        await h.calls.start(h.owner, selected.attempt_id)
        await h.calls.complete(h.owner, selected.attempt_id, usage=usage())
        base = datetime.now(timezone.utc) - timedelta(minutes=5)
        # Controlled persisted timestamps test SQL selection and arithmetic,
        # not real handler duration or an independent Worker crash.
        for index, (status, offset, end) in enumerate([
            ("completed", 5, 15), ("running", 20, None),
            ("waiting_approval", None, None), ("waiting_resource", 40, None),
            ("waiting_children", 40, None), ("denied", None, 50),
            ("completed", 40, 50), ("completed", 40, 50),
        ]):
            with framework_owner_scope(h.owner):
                await jobs.save_job(ToolJobRecord(id=f"{suffix}-{index}", session_id=h.session_id,
                    turn_id=h.binding.turn_id, trace_id="activity-test", call_id=f"call-{index}",
                    tool_key="fixture", tool_name="Fixture", status=ToolJobStatus(status),
                    created_at=base, updated_at=base,
                    started_at=base + timedelta(seconds=offset) if offset is not None else None,
                    completed_at=base + timedelta(seconds=end) if end is not None else None,
                    metadata={"framework_execution_owner": execution_reference(h.owner)}))
        # This case intentionally exercises the legacy start/end fallback with
        # controlled historical timestamps.  A journal observed at the real
        # current time would be a future fact relative to the explicit as_of.
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} "
                "SET metadata=metadata-'framework_tool_activity' WHERE session_id=%s", (h.session_id,))
        await h.tasks.finish(h.task, status="failed", error_code="activity_fixture_end")
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload="
                "payload-'framework_worker_activity' WHERE session_id=%s AND payload->>'task_id'=%s",
                (h.session_id, h.task.id))
            cur.execute(f"UPDATE {h.tasks._attempts} SET started_at=%s,finished_at=%s WHERE id=%s",
                (base, base + timedelta(seconds=10), h.task.attempt_id))
            call = (await h.calls.snapshot(h.binding)).calls[0].model_copy(update={
                "started_at": base + timedelta(seconds=2), "settled_at": base + timedelta(seconds=4)})
            cur.execute(f"UPDATE {h.calls.calls_table} SET record=%s::jsonb WHERE binding_id=%s",
                (call.model_dump_json(), h.binding.binding_id))
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET metadata="
                "jsonb_set(metadata,'{framework_binding_id}',%s::jsonb) WHERE id=%s",
                (json.dumps("foreign-binding"), f"{suffix}-6"))
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET session_id='foreign-session' "
                "WHERE id=%s", (f"{suffix}-7",))

        before_jobs = await jobs.list_jobs(h.session_id)
        before_calls = await h.calls.snapshot(h.binding)
        observed = []

        def check_snapshot(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("SELECT runtime_binding FROM"):
                cursor.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation')")
                observed.append(cursor.fetchone())

        engine = postgres_engine(h.settings)
        event.listen(engine, "before_cursor_execute", check_snapshot)
        try:
            result = await PostgresFrameworkModelCalls(h.settings).root_activity_intervals(
                h.binding, as_of=base + timedelta(seconds=30))
        finally:
            event.remove(engine, "before_cursor_execute", check_snapshot)

        assert result == [(base, base + timedelta(seconds=15)),
            (base + timedelta(seconds=20), base + timedelta(seconds=30))]
        assert observed == [("on", "repeatable read")]
        report = await diagnose(h, h.task.id)
        assert report.model_budget.root_activity_sources_available
        assert report.model_budget.root_activity_scope == "persisted_execution_envelope"
        assert report.model_budget.root_activity_seconds is not None
        assert report.model_budget.root_activity_seconds > 25
        assert not report.automatic_replay_allowed
        assert await jobs.list_jobs(h.session_id) == before_jobs
        assert await h.calls.snapshot(h.binding) == before_calls
    finally:
        _drop_tables(h.settings, [h.settings.database.postgres_tool_artifact_table,
            h.settings.database.postgres_tool_job_table])


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


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["request", "response", "wire-request", "wire-response"])
@pytest.mark.parametrize("referenced", [False, True])
async def test_upload_journal_diagnostic_preserves_original_reference_scope_without_object_io(
    model_calls_harness, monkeypatch, stage, referenced,
):
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from tests.test_framework_model_uploads import admitted, evidence_store

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h, stage=stage)
    evidence, objects, client = evidence_store(h, monkeypatch)
    if stage.startswith("wire-"):
        target = await evidence._store_bytes(h.owner, selected, b"diagnostic-private-body", stage)
    else:
        target = await evidence._store(h.owner, selected, b'{"fixture":"upload"}', stage)
    original, = await h.calls.evidence_uploads(h.binding)
    observations = []
    if referenced:
        if stage == "request":
            await h.calls.attach_request(h.owner, selected.attempt_id, request=target)
        elif stage == "response":
            observations = [await h.calls.append_response_observation(h.owner, selected.attempt_id,
                response=target, response_kind="model_invocation_result")]
        else:
            observations = [await h.calls.append_wire_observation(h.owner, selected.attempt_id,
                observation={"request" if stage == "wire-request" else "response": target,
                    "attempt_sequence": 1})]
    before = await h.calls.snapshot(h.binding)
    before_task, before_objects = await h.tasks.get(h.task.id), dict(client.objects)

    async def forbidden(*args, **kwargs):
        pytest.fail("Task diagnosis cannot read/upload objects or use another async ledger snapshot")

    monkeypatch.setattr(ArtifactStorageService, "read_object_uri", forbidden)
    monkeypatch.setattr(ArtifactStorageService, "store_uploaded_bytes", forbidden)
    monkeypatch.setattr(type(h.calls), "evidence_uploads", forbidden)
    report = await diagnose(h, h.task.id)
    assert report.model_upload_journal.store_available and report.model_upload_journal.identity_complete
    assert report.model_upload_journal.journal_rows == 1
    fact, = report.model_calls[0].evidence_uploads
    assert fact.upload_id == original.upload_id and fact.stage == stage and fact.target == target
    assert fact.created_at == original.created_at
    assert (fact.origin_task_id, fact.origin_attempt_id, fact.origin_epoch) == (
        h.owner.task_id, h.owner.attempt_id, h.owner.owner_epoch)
    expected = ("request" if stage == "request" else
        "response_observation" if stage == "response" else "wire_observation") if referenced else "unreferenced"
    assert fact.ledger_reference == expected
    assert fact.observation_ids == [item.observation_id for item in observations]
    assert not fact.authoritative and not fact.object_verified
    from src.schemas.framework_message_diagnostic import FrameworkMessageDiagnostic

    legacy = report.model_dump(exclude={"model_upload_journal"})
    for call in legacy["model_calls"]:
        call.pop("evidence_uploads")
    parsed = FrameworkMessageDiagnostic.model_validate(legacy)
    assert not parsed.model_upload_journal.store_available and parsed.model_upload_journal.journal_rows is None
    assert not parsed.model_upload_journal.identity_complete and parsed.model_calls[0].evidence_uploads == []
    assert ("model_upload_unreferenced" in report.issues) is not referenced
    assert not report.evidence_objects_verified and not report.automatic_replay_allowed
    text = report.model_dump_json()
    assert h.owner.owner_token not in text and "observer_token_sha256" not in text
    assert "diagnostic-private-body" not in text and '"fixture":"upload"' not in text
    assert await h.calls.snapshot(h.binding) == before
    assert await h.tasks.get(h.task.id) == before_task and client.objects == before_objects


@pytest.mark.asyncio
async def test_upload_journal_diagnostic_distinguishes_absent_store_empty_store_and_legacy_report(model_calls_harness):
    from src.schemas.framework_message_diagnostic import FrameworkMessageDiagnostic

    h = model_calls_harness
    isolate_absent_stores(h)
    await begin(h)
    await h.calls.freeze(h.owner, policy(h))
    report = await diagnose(h, h.task.id)
    assert report.model_upload_journal.store_available and report.model_upload_journal.identity_complete
    assert report.model_upload_journal.journal_rows == 0
    old = FrameworkMessageDiagnostic.model_validate(report.model_dump(exclude={"model_upload_journal"}))
    assert not old.model_upload_journal.store_available and not old.model_upload_journal.identity_complete
    assert old.model_upload_journal.journal_rows is None
    renamed = f"diagnostic_upload_old_{uuid4().hex[:12]}"
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"ALTER TABLE {h.calls.uploads_table} RENAME TO {renamed}")
    try:
        report = await diagnose(h, h.task.id)
        assert not report.model_upload_journal.store_available and not report.model_upload_journal.identity_complete
        assert report.model_upload_journal.journal_rows is None and report.model_calls == []
        assert "model_upload_store_missing" in report.issues
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) AS relation", (h.calls.uploads_table,))
            assert cur.fetchone()["relation"] is None, "Diagnosis must not create an absent upload table"
    finally:
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"ALTER TABLE {renamed} RENAME TO {h.calls.uploads_table}")


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["schema", "binding", "upload_id", "attempt", "fingerprint", "origin", "intent", "bucket"])
async def test_upload_journal_diagnostic_keeps_invalid_rows_visible_without_exposing_untrusted_record(
    model_calls_harness, monkeypatch, changed,
):
    from tests.test_framework_model_uploads import admitted, evidence_store

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h, stage="wire-request")
    evidence, objects, client = evidence_store(h, monkeypatch)
    await evidence._store_bytes(h.owner, selected, b"diagnostic-private-request", "wire-request")
    await evidence._store_bytes(h.owner, selected, b"diagnostic-private-response", "wire-response")
    broken, good = await h.calls.evidence_uploads(h.binding)
    data = broken.model_dump(mode="json")
    foreign = "diagnostic-private-foreign"
    if changed == "schema":
        data = {"untrusted_payload": foreign}
    elif changed == "binding":
        data["binding_id"] = foreign
    elif changed == "upload_id":
        data["upload_id"] = uuid4().hex
    elif changed == "attempt":
        data["intent"]["attempt_id"] = foreign
    elif changed == "fingerprint":
        data["binding_fingerprint"] = foreign
    elif changed == "origin":
        data["origin"]["observer_token_sha256"] = "f" * 64
    elif changed == "intent":
        data["intent"]["logical_call_id"] = foreign
    else:
        data["target"]["storage_uri"] = data["target"]["storage_uri"].replace(
            f"rustfs://{h.settings.storage.rustfs_bucket}/", f"rustfs://{foreign}/")
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.uploads_table} SET record=%s::jsonb WHERE binding_id=%s AND upload_id=%s",
            (json.dumps(data), h.binding.binding_id, broken.upload_id))
    before = await h.calls.snapshot(h.binding)
    report = await diagnose(h, h.task.id)
    assert report.model_upload_journal.store_available and report.model_upload_journal.journal_rows == 2
    assert not report.model_upload_journal.identity_complete
    fact, = report.model_calls[0].evidence_uploads
    assert fact.upload_id == good.upload_id and fact.target == good.target
    assert "model_upload_identity_invalid" in report.issues
    assert report.model_budget.accounting_complete and report.model_budget.reserved_calls == 1
    text = report.model_dump_json()
    assert foreign not in text and "diagnostic-private-request" not in text and "diagnostic-private-response" not in text
    assert "observer_token_sha256" not in text and h.owner.owner_token not in text
    assert await h.calls.snapshot(h.binding) == before
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {h.calls.uploads_table} WHERE binding_id=%s AND upload_id=%s",
            (h.binding.binding_id, broken.upload_id))
        assert cur.fetchone()["record"] == data, "Diagnosis must not repair a corrupt upload record"


@pytest.mark.asyncio
async def test_upload_journal_diagnostic_uses_the_same_read_only_snapshot_as_ledger_references(model_calls_harness, monkeypatch):
    from tests.test_framework_model_uploads import admitted, evidence_store, upload_target

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h)
    evidence, objects, client = evidence_store(h, monkeypatch)
    target = await evidence.store_request(h.owner, selected, b'{"fixture":"upload"}')
    original, = await h.calls.evidence_uploads(h.binding)
    call, = (await h.calls.snapshot(h.binding)).calls
    another_id = uuid4().hex
    another = original.model_copy(update={"upload_id": another_id,
        "target": upload_target(h, selected, "request", another_id)})
    observed, committed = [], []

    def concurrent_commit(conn, cursor, statement, parameters, context, executemany):
        if h.tasks._tasks in statement and not committed:
            committed.append(True)
            with postgres_connect(h.settings) as other, other.cursor() as cur:
                h.calls._write(cur, call.model_copy(update={"request": target}))
                cur.execute(f"INSERT INTO {h.calls.uploads_table}(binding_id,attempt_id,upload_id,record) "
                    "VALUES(%s,%s,%s,%s::jsonb)",
                    (h.binding.binding_id, selected.attempt_id, another_id, another.model_dump_json()))
        if statement.startswith("SELECT attempt_id,upload_id,record FROM"):
            cursor.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation')")
            observed.append(cursor.fetchone())

    engine = postgres_engine(h.settings)
    event.listen(engine, "before_cursor_execute", concurrent_commit)
    try:
        report = await diagnose(h, h.task.id)
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_commit)
    assert observed == [("on", "repeatable read")]
    assert report.model_upload_journal.journal_rows == 1 and report.model_upload_journal.identity_complete
    fact, = report.model_calls[0].evidence_uploads
    assert fact.upload_id == original.upload_id and fact.ledger_reference == "unreferenced"
    assert report.model_calls[0].request is None
    assert (await h.calls.snapshot(h.binding)).calls[0].request == target
    assert len(await h.calls.evidence_uploads(h.binding)) == 2
    next_report = await diagnose(h, h.task.id)
    references = {item.upload_id: item.ledger_reference for item in next_report.model_calls[0].evidence_uploads}
    assert references == {original.upload_id: "request", another_id: "unreferenced"}
    assert not next_report.automatic_replay_allowed and not next_report.evidence_objects_verified


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["response", "wire-request", "wire-response"])
async def test_upload_journal_diagnostic_does_not_confuse_duplicate_bytes_with_a_bound_address(model_calls_harness, monkeypatch, stage):
    from tests.test_framework_model_uploads import admitted, evidence_store

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h, stage=stage)
    evidence, objects, client = evidence_store(h, monkeypatch)
    store = evidence._store_bytes if stage.startswith("wire-") else evidence._store
    first = await store(h.owner, selected, b"diagnostic-duplicate-fixture", stage)
    second = await store(h.owner, selected, b"diagnostic-duplicate-fixture", stage)
    assert first.sha256 == second.sha256 and first.storage_uri != second.storage_uri
    if stage == "response":
        original = await h.calls.append_response_observation(h.owner, selected.attempt_id,
            response=first, response_kind="model_invocation_result")
        duplicate = await h.calls.append_response_observation(h.owner, selected.attempt_id,
            response=second, response_kind="model_invocation_result")
    else:
        key = "request" if stage == "wire-request" else "response"
        original = await h.calls.append_wire_observation(h.owner, selected.attempt_id,
            observation={key: first, "attempt_sequence": 1})
        duplicate = await h.calls.append_wire_observation(h.owner, selected.attempt_id,
            observation={key: second, "attempt_sequence": 1})
    assert duplicate == original
    before = await h.calls.snapshot(h.binding)
    report = await diagnose(h, h.task.id)
    facts = {item.target.storage_uri: item for item in report.model_calls[0].evidence_uploads}
    assert report.model_upload_journal.journal_rows == 2 and report.model_upload_journal.identity_complete
    assert facts[first.storage_uri].ledger_reference != "unreferenced"
    assert facts[first.storage_uri].observation_ids == [original.observation_id]
    assert facts[second.storage_uri].ledger_reference == "unreferenced"
    assert facts[second.storage_uri].observation_ids == []
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["owner_binding", "parent_call"])
async def test_upload_journal_diagnostic_reports_unverifiable_parent_without_fabricating_zero_uploads(model_calls_harness, monkeypatch, changed):
    from src.runtime.postgres_runtime_owner import runtime_owner_table
    from tests.test_framework_model_uploads import admitted, evidence_store

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h)
    evidence, objects, client = evidence_store(h, monkeypatch)
    await evidence.store_request(h.owner, selected, b'{"fixture":"upload"}')
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        if changed == "owner_binding":
            cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET runtime_binding="
                "jsonb_set(runtime_binding,'{prompt_version}',%s::jsonb) WHERE binding_id=%s",
                (json.dumps("diagnostic-private-foreign"), h.binding.binding_id))
        else:
            cur.execute(f"UPDATE {h.calls.calls_table} SET record=%s::jsonb WHERE binding_id=%s",
                ('{"private_payload":"diagnostic-private-foreign"}', h.binding.binding_id))
    report = await diagnose(h, h.task.id)
    assert report.model_upload_journal.store_available and report.model_upload_journal.journal_rows == 1
    assert not report.model_upload_journal.identity_complete
    assert all(call.evidence_uploads == [] for call in report.model_calls)
    assert ("model_upload_binding_mismatch" if changed == "owner_binding" else "model_upload_identity_invalid") in report.issues
    assert "diagnostic-private-foreign" not in report.model_dump_json()
    assert not report.automatic_replay_allowed and not report.evidence_objects_verified


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["response", "wire-response"])
@pytest.mark.parametrize("phase", ["completed", "unknown"])
async def test_upload_journal_diagnostic_never_promotes_an_unreferenced_terminal_candidate(model_calls_harness, monkeypatch, stage, phase):
    from tests.test_framework_model_uploads import admitted, evidence_store

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h, stage=stage)
    evidence, objects, client = evidence_store(h, monkeypatch)
    store = evidence._store_bytes if stage.startswith("wire-") else evidence._store
    await store(h.owner, selected, b"diagnostic-terminal-fixture", stage)
    if phase == "completed":
        await h.calls.complete(h.owner, selected.attempt_id, usage=usage())
    else:
        await h.calls.mark_unknown(h.owner, selected.attempt_id, code="fixture_unknown")
    before = await h.calls.snapshot(h.binding)
    report = await diagnose(h, h.task.id)
    fact, = report.model_calls[0].evidence_uploads
    assert report.model_calls[0].phase == phase and fact.ledger_reference == "unreferenced"
    assert not fact.authoritative and not fact.object_verified
    assert not report.automatic_replay_allowed and not report.evidence_objects_verified
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_upload_journal_diagnostic_retains_predecessor_origin_after_explicit_transfer(model_calls_harness, monkeypatch):
    from src.runtime.postgres_framework_model_calls import FrameworkModelRecoveryRequired
    from tests.test_framework_model_reservation_transfer import replacement
    from tests.test_framework_model_uploads import admitted, evidence_store

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h, begin_execution=False)
    evidence, objects, client = evidence_store(h, monkeypatch)
    await evidence.store_request(h.owner, selected, b'{"fixture":"upload"}')
    original, = (await h.calls.snapshot(h.binding)).calls
    successor = await replacement(h, h.task)
    await h.calls.transfer_ready(successor.runtime_owner, expected=original)
    before = await h.calls.snapshot(h.binding)
    report = await diagnose(h, h.task.id)
    fact, = report.model_calls[0].evidence_uploads
    assert report.model_upload_journal.identity_complete and fact.origin_attempt_id == h.owner.attempt_id
    assert fact.origin_attempt_id != successor.attempt_id and fact.ledger_reference == "unreferenced"
    assert report.model_calls[0].owner_attempt_id == successor.attempt_id
    with pytest.raises(FrameworkModelRecoveryRequired, match="request_upload_requires_reconciliation"):
        await h.calls.start(successor.runtime_owner, selected.attempt_id)
    assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("written", [False, True])
async def test_upload_journal_diagnostic_after_real_uploader_termination_never_claims_object_success(
    model_calls_harness, monkeypatch, tmp_path, written,
):
    import asyncio
    import multiprocessing
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from tests.test_framework_model_uploads import admitted, upload_worker_until_ack

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h)
    assert await h.tasks.renew_framework_execution(h.task, 120)
    before = await h.calls.snapshot(h.binding)
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=upload_worker_until_ack,
        args=(h.settings.model_dump(mode="json"), h.owner.model_dump(mode="json"),
            selected.model_dump(mode="json"), str(tmp_path), written, entered))
    try:
        process.start()
        assert await asyncio.to_thread(entered.wait, 35), f"Uploader did not reach PUT; exit={process.exitcode}"
        uploads = await h.calls.evidence_uploads(h.binding)
        assert len(uploads) == 1 and process.is_alive()
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive() and process.exitcode != 0

        async def forbidden(*args, **kwargs):
            pytest.fail("Crash diagnosis must not probe or rewrite objects")

        monkeypatch.setattr(ArtifactStorageService, "read_object_uri", forbidden)
        monkeypatch.setattr(ArtifactStorageService, "store_uploaded_bytes", forbidden)
        report = await diagnose(h, h.task.id)
        fact, = report.model_calls[0].evidence_uploads
        assert report.model_upload_journal.identity_complete and report.model_upload_journal.journal_rows == 1
        assert fact.target == uploads[0].target and fact.ledger_reference == "unreferenced"
        assert not fact.object_verified and not fact.authoritative and not report.automatic_replay_allowed
        assert not report.evidence_objects_verified
        assert await h.calls.snapshot(h.binding) == before
        assert await h.calls.evidence_uploads(h.binding) == uploads
        assert len(list(tmp_path.iterdir())) == written
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)


@pytest.mark.asyncio
async def test_upload_journal_diagnostic_never_reads_foreign_turn_canaries(model_calls_harness, monkeypatch):
    from tests.test_framework_model_uploads import admitted, evidence_store
    from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload

    h = model_calls_harness
    isolate_absent_stores(h)
    selected = await admitted(h)
    evidence, objects, client = evidence_store(h, monkeypatch)
    await evidence.store_request(h.owner, selected, b'{"fixture":"upload"}')
    foreign = make_binding(h.session_id, f"foreign-turn-{uuid4().hex}")
    await h.tasks.submit_v2(make_command(h, foreign), make_payload().model_copy(update={"submit_mode": "queued"}), make_message(h, foreign),
        idempotency_key=uuid4().hex, model_budget=policy(h))
    # Corrupt canaries in another admitted Turn must be excluded in SQL before
    # decoding. This does not claim that the foreign row is a valid execution.
    payload = '{"private_payload":"diagnostic-private-foreign"}'
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {h.calls.calls_table}(binding_id,attempt_id,record) VALUES(%s,%s,%s::jsonb)",
            (foreign.binding_id, "foreign-attempt", payload))
        cur.execute(f"INSERT INTO {h.calls.uploads_table}(binding_id,attempt_id,upload_id,record) VALUES(%s,%s,%s,%s::jsonb)",
            (foreign.binding_id, "foreign-attempt", uuid4().hex, payload))
    before = await h.calls.snapshot(h.binding)
    report = await diagnose(h, h.task.id)
    assert report.model_upload_journal.identity_complete and report.model_upload_journal.journal_rows == 1
    assert len(report.model_calls) == len(report.model_calls[0].evidence_uploads) == 1
    assert "model_upload_identity_invalid" not in report.issues
    assert "diagnostic-private-foreign" not in report.model_dump_json() and foreign.binding_id not in report.model_dump_json()
    assert await h.calls.snapshot(h.binding) == before
