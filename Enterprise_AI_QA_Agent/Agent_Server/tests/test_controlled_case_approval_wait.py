"""Atomic formal parking, not decision delivery, graph recovery or Redis ACK."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import importlib
import json
from threading import Event

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope, graph_event_scope
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.schemas.framework_approval import approval_value_hash
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals, projected
from tests.test_framework_approval_projection import counts


def implementation(p):
    return importlib.import_module("src.runtime.controlled_case_approval_wait").ControlledCaseApprovalWaits(
        p.s.h.tasks, projection=p.bridge.projection)


def proposals(p):
    owner = p.h.task.runtime_owner
    with framework_owner_scope(owner):
        context = p.bridge.runtime._deep_agent_governance_context(p.outcome.state)
        return [p.bridge.runtime._tool_governance_service.prepare_framework_approval(owner.binding, item, context)
            for item in p.outcome.graph_outcome.native_interrupts]


async def park(p, *, service=None, candidates=None):
    with framework_owner_scope(p.h.task.runtime_owner):
        return await (service or implementation(p)).park(p.h.task.runtime_owner,
            proposals=proposals(p) if candidates is None else candidates)


def clock(p):
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS now")
        return cur.fetchone()["now"]


@pytest.mark.asyncio
async def test_formal_running_worker_is_charged_until_its_actual_return(pending_formal):
    p = pending_formal
    now = clock(p)
    before = await p.s.calls.root_activity_seconds(p.s.binding, as_of=now)
    later = await p.s.calls.root_activity_seconds(p.s.binding, as_of=now + timedelta(seconds=60))
    assert later - before == pytest.approx(60)


@pytest.mark.asyncio
async def test_complete_batch_parks_all_business_owners_and_retains_scoped_receipt(pending_formal):
    p = pending_formal
    native, budget = native_originals(p), await p.s.calls.snapshot(p.s.binding)
    before = await p.s.d.runs.get_run(p.s.detail.run.id)
    receipt = await park(p)
    saved = await p.s.d.runs.get_run(p.s.detail.run.id)
    item = next(i for i in saved.items if i.id == p.s.item.id)
    attempt = next(a for a in saved.attempts if a.id == p.s.attempt.id)
    task = await p.s.h.tasks.get(p.h.task.id)
    session = await p.h.store.get_session(p.s.session.id)
    assert item.status == attempt.status == session.status.value == "waiting_approval"
    assert item.result_id is None and saved.results == [] and attempt.checkpoint_version == 1
    assert item.active_started_at is attempt.active_started_at is None
    assert item.waiting_started_at == attempt.waiting_started_at == receipt.parked_at
    assert item.lease_expires_at is None and item.lease_token == p.s.item.lease_token
    assert saved.run.stats.running == before.run.stats.running - 1
    assert saved.run.stats.waiting_approval == before.run.stats.waiting_approval + 1
    assert task.status == "completed" and task.result_status == "waiting_approval"
    assert task.result_payload == {"controlled_case_approval_wait": receipt.model_dump(mode="json")}
    assert receipt.scope == "controlled_case_atomic_approval_wait_only"
    assert (receipt.task_id, receipt.attempt_id, receipt.run_item_execution_attempt_id) == (
        p.h.task.id, p.h.task.attempt_id, p.s.attempt.id)
    assert len(receipt.batch) == 2 and len({entry.approval_id for entry in receipt.batch}) == 2
    assert {entry.interrupt_id for entry in receipt.batch} == {
        entry.interrupt_id for entry in p.outcome.graph_outcome.native_interrupts}
    assert set(session.metadata["pending_turn"]["pending_approval_ids"]) == {
        entry.approval_id for entry in receipt.batch}
    assert session.metadata["pending_turn"]["controlled_case_wait_sha256"] == receipt.receipt_sha256
    assert all(key not in session.metadata for key in (
        "turn_lease_token", "turn_lease_owner", "turn_lease_turn_id", "turn_lease_expires_at"))
    assert (await p.s.calls.snapshot(p.s.binding)) == budget and native_originals(p) == native
    now = clock(p)
    assert await p.s.calls.root_activity_seconds(p.s.binding, as_of=now) == (
        await p.s.calls.root_activity_seconds(p.s.binding, as_of=now + timedelta(seconds=60)))
    raw = receipt.model_dump(mode="json")
    assert raw["receipt_sha256"] == approval_value_hash({k: v for k, v in raw.items() if k != "receipt_sha256"})
    assert all(word not in json.dumps(raw) for word in ("lease_token", "owner_token", "arguments", "a.py", "b.py"))
    events = await p.h.store.list_events(p.s.session.id)
    returned, = [e for e in events if e.type == "agent_task.completed"]
    assert returned.payload["execution_returned"] is True and returned.payload["result_status"] == "waiting_approval"
    audit, = [e for e in events if e.type == "test_run.case_approval_waiting"]
    assert audit.payload["wait_sha256"] == receipt.receipt_sha256
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_incomplete_batch_cannot_publish_a_partial_business_wait(pending_formal):
    p = pending_formal
    candidates = proposals(p)[:1]
    before = database_digest(p.s), counts(p.h)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="complete pending"):
        await park(p, candidates=candidates)
    assert (database_digest(p.s), counts(p.h)) == before
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"runtime_wait": True}], indirect=True)
async def test_runtime_explicit_wait_composition_runs_after_sink_drain_without_result_or_ack(pending_formal):
    from src.runtime.controlled_case_settlement import ControlledCaseSettlements
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from tests.test_controlled_case_execution_lease import delivery

    p = pending_formal
    receipt = p.outcome.approval_wait_receipt
    assert receipt is not None and p.outcome.settlement_receipt is None
    assert p.outcome.runtime_result is p.outcome.result_asset is p.outcome.assessment_asset is None
    assert p.outcome.conversation_response is None
    assert (await p.s.h.tasks.get(p.h.task.id)).result_status == "waiting_approval"
    assert (await p.s.d.runs.get_item(p.s.item.id)).status == "waiting_approval"
    before = facts(p)
    with pytest.raises(RuntimeError, match="atomic settlement is missing"):
        await ControlledCaseSettlements(p.s.h.tasks).acknowledge_delivery(delivery(p.s),
            capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    assert facts(p) == before
    assert len(p.requests) == 1 and p.effects == []


def facts(p):
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        rows = {}
        for table in (p.h.settings.database.postgres_approval_table, p.h.settings.database.postgres_tool_job_table,
            p.h.settings.database.postgres_tool_artifact_table, p.s.calls.calls_table,
            p.s.h.tasks._tasks + "_interrupt", p.s.h.tasks._tasks + "_decision"):
            cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table,))
            if cur.fetchone()["present"]:
                cur.execute(f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS rows FROM {table} t")
                rows[table] = cur.fetchone()["rows"]
    return database_digest(p.s), approval_value_hash(rows), native_originals(p)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["scope", "sink", "second_owner", "case_lease", "agent_lease", "interrupt",
    "control", "pending", "result", "model_unknown", "empty"])
async def test_parking_rejects_missing_authority_control_conflicts_and_unknown_effects(pending_formal, drift):
    p = pending_formal
    owner = p.h.task.runtime_owner
    if drift == "second_owner":
        owner = owner.model_copy(update={"controlled_case_lease": None})
    candidates = proposals(p) if drift != "empty" else []
    service = implementation(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "case_lease":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                "'\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        elif drift == "agent_lease":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s",
                (p.s.binding.binding_id,))
        elif drift == "interrupt":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET interrupt_requested=true WHERE id=%s", (p.h.task.id,))
        elif drift in {"control", "pending"}:
            value = {"control": {"control_state": "interrupt_requested"}} if drift == "control" else {"pending_turn": {"turn_id": "foreign"}}
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=metadata || %s::jsonb WHERE id=%s",
                (json.dumps(value), p.s.session.id))
        elif drift == "result":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload='{{\"foreign\":true}}'::jsonb WHERE id=%s", (p.h.task.id,))
        elif drift == "model_unknown":
            cur.execute(f"UPDATE {p.s.calls.calls_table} SET record=(record-'settled_response_id') || '{{\"phase\":\"unknown\"}}'::jsonb "
                "WHERE binding_id=%s", (p.s.binding.binding_id,))
    before = facts(p)
    with framework_owner_scope(None if drift == "scope" else owner), graph_event_scope(object() if drift == "sink" else None):
        with pytest.raises((RuntimeError, ValueError)):
            await service.park(owner, proposals=candidates)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["second_projection", "item", "attempt", "totals", "task", "clear_lease", "audit",
    "ack_loss", "cancel_waiter"])
async def test_parking_and_receipt_commit_or_rollback_together(pending_formal, monkeypatch, failure, caplog):
    p = pending_formal
    service, tasks = implementation(p), p.s.h.tasks
    before = facts(p)
    entered, release, finished = Event(), Event(), Event()
    original_park = type(service)._park
    original_append = tasks._sessions.append_event_in_transaction
    def append(*args, **kwargs):
        value = original_append(*args, **kwargs)
        if args[2].type == "test_run.case_approval_waiting":
            if failure == "audit":
                raise RuntimeError("isolated wait rollback")
            if failure == "cancel_waiter":
                entered.set()
                assert release.wait(30)
        return value
    def material(self, *args):
        try:
            value = original_park(self, *args)
            if failure == "ack_loss":
                raise RuntimeError("isolated committed wait receipt loss")
            return value
        finally:
            finished.set()
    def failing_writer(original, *, second=False):
        count = 0
        def write(*args, **kwargs):
            nonlocal count
            result = original(*args, **kwargs)
            count += 1
            if not second or count == 2:
                raise RuntimeError("isolated wait rollback")
            return result
        return write
    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_park", material)
        patch.setattr(tasks._sessions, "append_event_in_transaction", append)
        if failure == "second_projection":
            patch.setattr(p.bridge.projection, "_project_in_transaction",
                failing_writer(p.bridge.projection._project_in_transaction, second=True))
        elif failure in {"item", "attempt", "totals"}:
            cls = type(p.s.d.runs)
            name = {"item": "_write_item", "attempt": "_write_attempt", "totals": "_refresh_run_delta_in_cursor"}[failure]
            patch.setattr(cls, name, failing_writer(getattr(cls, name)))
        elif failure in {"task", "clear_lease"}:
            name = "_update_status" if failure == "task" else "_clear_lease"
            patch.setattr(tasks, name, failing_writer(getattr(tasks, name)))
        operation = asyncio.create_task(park(p, service=service))
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match="isolated"):
                await operation
    committed = failure in {"ack_loss", "cancel_waiter"}
    task = await tasks.get(p.h.task.id)
    assert task.status == ("completed" if committed else "running")
    if not committed:
        assert facts(p) == before
    else:
        assert task.result_payload["controlled_case_approval_wait"]["scope"] == "controlled_case_atomic_approval_wait_only"
        after = facts(p)
        # No blind replay, including after a cancelled waiter whose SQL committed.
        with pytest.raises(RuntimeError):
            await park(p)
        assert facts(p) == after
    assert any(getattr(r, "recovery_reason", None) == "approval_wait_write_receipt_unknown"
        and getattr(r, "automatic_replay", None) is False for r in caplog.records)
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["case", "agent"])
async def test_last_wait_write_cannot_cross_original_locked_lease_deadline(pending_formal, monkeypatch, lease):
    p = pending_formal
    service = implementation(p)
    settled = service._settled_materials
    original_append = p.h.store.append_event_in_transaction
    def shorten(cur, *args):
        value = settled(cur, *args)
        if lease == "case":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}',"
                "to_jsonb(clock_timestamp()+interval '1 second')) WHERE id=%s", (p.s.item.id,))
        else:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at=clock_timestamp()+interval '1 second' "
                "WHERE binding_id=%s", (p.s.binding.binding_id,))
        return value
    def delay(*args, **kwargs):
        value = original_append(*args, **kwargs)
        if args[2].type == "test_run.case_approval_waiting":
            args[0].execute("SELECT pg_sleep(1.1)")
        return value
    before = facts(p)
    monkeypatch.setattr(service, "_settled_materials", shorten)
    monkeypatch.setattr(p.h.store, "append_event_in_transaction", delay)
    with pytest.raises(RuntimeError, match="locked lease deadline"):
        await park(p, service=service)
    assert facts(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["returned_failed", "unknown_failed", "cancelled"])
async def test_prior_real_tool_return_is_distinct_from_an_unconfirmed_terminal_label(pending_formal, kind):
    from src.schemas.tool_job import ToolJobStatus

    p = pending_formal
    with framework_owner_scope(p.h.task.runtime_owner):
        tool = p.bridge.runtime._tool_registry.get("inspect_source")
        job = await p.bridge.jobs.create_job(tool=tool, call_id="fixture:prior-native-return",
            session_id=p.s.session.id, turn_id=p.s.binding.turn_id, trace_id=p.s.graph_request.trace_id,
            input_payload={"fixture": "prior-tool"})
        assert await p.bridge.jobs.claim_execution(job.id) is not None
        if kind == "returned_failed":
            returned = await p.bridge.jobs.observe_output(job.id, status="failed",
                output_payload={"error": "fixture native failure", "status": "failed"})
            saved = await p.bridge.jobs.recover_observed_output(job.id, reason="fixture output delivery interrupted")
            assert saved.status == ToolJobStatus.failed
            assert saved.output_payload != returned.output_payload  # Legitimate application enrichment.
        elif kind == "unknown_failed":
            await p.bridge.jobs.finish_execution(job.id, status=ToolJobStatus.failed,
                output_payload={}, summary="fixture: no returned output")
        else:
            await p.bridge.jobs.cancel_job(job.id, reason="fixture: physical stop unconfirmed")
    if kind == "returned_failed":
        receipt = await park(p)
        assert len(receipt.batch) == 2
        intervals = await p.s.calls.root_activity_intervals(p.s.binding)
        assert intervals  # Every Worker/model/tool tail is durably closed.
    else:
        before = facts(p)
        with pytest.raises(ValueError, match="unsettled business ToolJobs"):
            await park(p)
        assert facts(p) == before
    assert len(p.requests) == 1 and p.effects == []


def test_explicit_wait_alone_does_not_advertise_full_redis_execution_composition():
    from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime

    runtime = ControlledCaseRuntime(runtime_service=None, approval_wait_service=object())
    with pytest.raises(ValueError, match="executor, finalizer, assessment and settlement"):
        runtime.require_dispatch_ready(None, capabilities=None)


@pytest.mark.asyncio
async def test_receipt_codec_rejects_missing_version_duplicate_entries_and_unsupported_authority(pending_formal):
    from src.schemas.controlled_case_approval_wait import ControlledCaseApprovalWaitReceipt

    receipt = await park(pending_formal)
    raw = receipt.model_dump(mode="json")
    for change in ("missing_version", "bool_version", "future_version", "scope", "duplicate", "empty", "naive", "extra", "hash"):
        value = deepcopy(raw)
        if change == "missing_version":
            value.pop("schema_version")
        elif change in {"bool_version", "future_version"}:
            value["schema_version"] = True if change == "bool_version" else 2
        elif change == "scope":
            value["scope"] = "resume_or_delivery_ack"
        elif change == "duplicate":
            value["batch"].append(value["batch"][0])
        elif change == "empty":
            value["batch"] = []
        elif change == "naive":
            value["parked_at"] = receipt.parked_at.replace(tzinfo=None).isoformat()
        elif change == "extra":
            value["automatic_replay"] = True
        else:
            value["receipt_sha256"] = "0" * 64
        if change != "hash":
            value["receipt_sha256"] = approval_value_hash({k: v for k, v in value.items() if k != "receipt_sha256"})
        with pytest.raises(ValueError):
            ControlledCaseApprovalWaitReceipt.model_validate(value)


@pytest.mark.asyncio
async def test_concurrent_parking_has_only_one_atomic_winner(pending_formal):
    p = pending_formal
    results = await asyncio.gather(park(p), park(p), return_exceptions=True)
    receipts = [value for value in results if not isinstance(value, BaseException)]
    rejected = [value for value in results if isinstance(value, BaseException)]
    assert len(receipts) == len(rejected) == 1 and isinstance(rejected[0], RuntimeError)
    events = await p.h.store.list_events(p.s.session.id)
    assert len([event for event in events if event.type == "test_run.case_approval_waiting"]) == 1
    assert len([event for event in events if event.type == "agent_task.completed"]) == 1
    assert counts(p.h)["approvals"] == counts(p.h)["jobs"] == counts(p.h)["links"] == 2
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_parking_retains_existing_human_decision_without_resetting_it(pending_formal):
    p = pending_formal
    async with projected(p) as items:
        chosen = items[0].approval
        from src.schemas.session import ToolApprovalStatus

        chosen.status, chosen.resolved_at = ToolApprovalStatus.denied, clock(p)
        chosen.decision_note = "fixture: retained human decision"
        await p.h.store.save_approval(p.s.session.id, chosen)
    before = facts(p)
    with pytest.raises(ValueError, match="prior human decision"):
        await park(p)
    assert facts(p) == before
    assert (await p.h.store.list_approvals(p.s.session.id))[0].status == ToolApprovalStatus.denied


@pytest.mark.asyncio
async def test_parked_native_batch_is_not_accepted_by_the_ordinary_decision_entry(pending_formal):
    from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
    from src.schemas.session import ApprovalDecisionRequest
    from tests.test_runtime_binding_persistence import make_command

    p = pending_formal
    receipt = await park(p)
    chosen = receipt.batch[0]
    command = make_command(p.h, p.s.binding).model_copy(update={"operation": "approval_resume",
        "reference_id": chosen.approval_id, "project_id": p.s.detail.run.project_id})
    before = facts(p)
    with pytest.raises(AgentSubmissionConflict, match="dedicated"):
        await p.s.h.tasks.submit_approval_v2(command, chosen.approval_id, ApprovalDecisionRequest(decision="approved"))
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []
