"""Atomic dedicated decision admission; no graph resume or public route."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from threading import Event
from uuid import NAMESPACE_URL, uuid5

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import parse_agent_task_command
from src.schemas.framework_approval import approval_value_hash
from src.schemas.session import ApprovalDecisionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_approval_wait import park, facts, implementation as wait_service
from tests.test_controlled_case_execution_lease import delivery, capabilities
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals


def implementation(p):
    return importlib.import_module("src.runtime.controlled_case_approval_submission").ControlledCaseApprovalSubmission(
        wait_service(p))


async def submit(p, approval_id, decision="approved", **changes):
    return await implementation(p).submit(delivery(p.s), approval_id,
        ApprovalDecisionRequest(decision=decision, reason=changes.get("reason", "Fixture human decision."),
            client_request_id=changes.get("request_id")), capabilities=capabilities())


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_decision_receipt_successor_and_outbox_commit_without_resuming_or_settling(pending_formal, decision):
    p = pending_formal
    wait = await park(p)
    before = await p.s.d.runs.get_run(p.s.detail.run.id), native_originals(p), await p.s.calls.snapshot(p.s.binding)
    origin = await p.s.h.tasks.get(p.h.task.id)
    chosen = wait.batch[0]
    result = await submit(p, chosen.approval_id, decision)
    assert result.operation == "case_approval" and result.status == "queued" and result.attempt_id is None
    assert result.parent_task_id == origin.id and result.reference_id == chosen.approval_id
    task = await p.s.h.tasks.get(result.id)
    assert set(task.input) == {"controlled_case_approval_admission"}
    proof = task.input["controlled_case_approval_admission"]
    assert proof["scope"] == "controlled_case_approval_admission_only" and proof["wait_sha256"] == wait.receipt_sha256
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {p.h.settings.database.postgres_approval_table} WHERE id=%s", (chosen.approval_id,))
        approval = cur.fetchone()
        cur.execute(f"SELECT * FROM {p.s.h.tasks._tasks}_decision WHERE approval_id=%s", (chosen.approval_id,))
        receipt = cur.fetchone()
        cur.execute(f"SELECT payload FROM {p.h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            ("agent_turn:" + result.id,))
        wire = cur.fetchone()["payload"]
    assert approval["status"] == decision and approval["resolved_at"] is not None
    assert receipt["state"] == "queued" and receipt["proof"] is receipt["delivered_at"] is receipt["applied_at"] is None
    assert receipt["decision_id"] == proof["decision_id"] and receipt["decision_hash"] == proof["decision_sha256"]
    assert wire["case_approval_reference"]["admission_sha256"] == proof["admission_sha256"]
    assert "case_execution_input" not in wire and "arguments" not in json.dumps(wire) and "a.py" not in json.dumps(wire)
    assert await p.s.h.tasks.get(origin.id) == origin
    assert before == (await p.s.d.runs.get_run(p.s.detail.run.id), native_originals(p), await p.s.calls.snapshot(p.s.binding))
    assert (await p.bridge.jobs.get_job(chosen.tool_job_id)).status.value == "waiting_approval"
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_same_decision_reuses_original_reason_identity_and_audits_opposite_conflicts(pending_formal):
    p = pending_formal
    wait = await park(p)
    chosen = wait.batch[0]
    first = await submit(p, chosen.approval_id, "denied", reason="Original human reason.", request_id="first")
    before = facts(p)
    assert await submit(p, chosen.approval_id, "denied", reason="Changed retry reason.", request_id="retry") == first
    assert facts(p) == before
    with pytest.raises(ValueError, match="different decision"):
        await submit(p, chosen.approval_id, "approved")
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


def admission_wire():
    from src.schemas.controlled_case_approval_submission import case_approval_task_id

    raw = dict(schema_version=1, scope="controlled_case_approval_admission_only",
        task_id=case_approval_task_id("origin", "approval"), origin_task_id="origin", origin_attempt_id="attempt",
        origin_owner_epoch=1, run_item_execution_attempt_id="formal-attempt", binding_fingerprint="a" * 64,
        input_sha256="b" * 64, submission_sha256="c" * 64, wait_sha256="d" * 64,
        approval_id="approval", decision_id="decision", decision="approved", decision_sha256="e" * 64,
        admitted_at="2026-11-03T00:00:00Z")
    return {**raw, "admission_sha256": approval_value_hash(raw)}


@pytest.mark.parametrize("drift", ["valid", "missing_version", "boolean_version", "float_version", "string_version",
    "future_version", "extra_field", "scope", "hash", "task_id", "naive_time", "boolean_epoch"])
def test_admission_codec_is_scoped_versioned_and_not_an_execution_proof(drift):
    from pydantic import ValidationError
    from src.schemas.controlled_case_approval_submission import ControlledCaseApprovalAdmission

    raw = admission_wire()
    if drift == "valid":
        parsed = capabilities().require_case_approval_admission_format(raw)
        assert parsed.model_dump(mode="json") == raw
        with pytest.raises(ValidationError):
            parsed.decision = "denied"
        with pytest.raises(TaskDeferred, match="approval_admission_format_unavailable"):
            replace(capabilities(), controlled_case_approval_admission_max_version=0).require_case_approval_admission_format(raw)
        return
    if drift == "missing_version":
        raw.pop("schema_version")
    else:
        key, value = {"boolean_version": ("schema_version", True), "float_version": ("schema_version", 1.0),
            "string_version": ("schema_version", "1"), "future_version": ("schema_version", 2),
            "extra_field": ("execution_authorized", True), "scope": ("scope", "native_delivery"),
            "hash": ("admission_sha256", "f" * 64), "task_id": ("task_id", "foreign"),
            "naive_time": ("admitted_at", "2026-11-03T00:00:00"), "boolean_epoch": ("origin_owner_epoch", True)}[drift]
        raw[key] = value
        if drift != "hash":
            raw["admission_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "admission_sha256"})
    with pytest.raises(ValidationError):
        ControlledCaseApprovalAdmission.model_validate(raw)
    error = TaskDeferred if drift in {"missing_version", "boolean_version", "float_version", "string_version",
        "future_version", "extra_field", "scope"} else ValidationError
    with pytest.raises(error):
        capabilities().require_case_approval_admission_format(raw)


@pytest.mark.parametrize("drift", ["valid", "missing_reference", "parent", "task_id", "reference_id", "run_id", "item_id",
    "attempt_id", "worker_id", "resource_id", "payload", "other_operation", "reference_version", "runtime_kind"])
def test_reference_wire_is_identity_only_and_cannot_be_repurposed(drift):
    from pydantic import ValidationError
    from src.schemas.agent_task import AgentTaskCommandV2
    from tests.test_runtime_binding_contract import binding

    proof = admission_wire()
    turn = str(uuid5(NAMESPACE_URL, "case-run:run:item:formal-attempt"))
    command = AgentTaskCommandV2(operation="case_approval", task_id=proof["task_id"], session_id="session-1",
        turn_id=turn, request_id="request", trace_id="trace", project_id="project", run_id="run", run_item_id="item",
        parent_task_id=proof["origin_task_id"], reference_id=proof["approval_id"],
        runtime_binding=binding(turn_id=turn, thread_id=turn, runtime_kind="langgraph"),
        case_approval_reference={key: proof[key] for key in ("schema_version", "origin_task_id", "origin_attempt_id",
            "run_item_execution_attempt_id", "admission_sha256")})
    raw = command.model_dump(mode="json")
    if drift == "valid":
        assert parse_agent_task_command(raw) == command and raw["payload"] == {}
        with pytest.raises(TaskDeferred, match="framework_runtime_kind_unavailable"):
            capabilities().require(command.runtime_binding, operation="case_approval")
        return
    if drift == "missing_reference":
        raw.pop("case_approval_reference")
    elif drift == "reference_version":
        raw["case_approval_reference"]["schema_version"] = True
    elif drift == "runtime_kind":
        raw["runtime_binding"] = binding(turn_id=turn, thread_id=turn).model_dump(mode="json")
    else:
        key = {"parent": "parent_task_id", "other_operation": "operation", "item_id": "run_item_id"}.get(drift, drift)
        raw[key] = {"payload": {"arguments": {}}, "other_operation": "message"}.get(drift, "foreign")
    with pytest.raises(ValidationError):
        AgentTaskCommandV2.model_validate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["approval", "receipt", "task", "outbox", "first_audit", "second_audit"])
async def test_each_write_boundary_rolls_back_the_entire_decision_admission(pending_formal, monkeypatch, failure):
    from src.infrastructure.sqlalchemy_runtime import SQLAlchemyCursor

    p = pending_formal
    wait = await park(p)
    before = facts(p)
    original = SQLAlchemyCursor.execute
    patterns = {"approval": f"UPDATE {p.h.settings.database.postgres_approval_table} SET status=",
        "receipt": f"INSERT INTO public.{p.s.h.tasks._tasks}_decision ",
        "task": f"INSERT INTO {p.s.h.tasks._tasks} ",
        "outbox": f"INSERT INTO {p.h.settings.database.postgres_task_outbox_table} ",
        "first_audit": f"INSERT INTO {p.h.settings.database.postgres_event_table} ",
        "second_audit": f"INSERT INTO {p.h.settings.database.postgres_event_table} "}
    matched = 0
    def execute(self, statement, parameters=None):
        nonlocal matched
        result = original(self, statement, parameters)
        if " ".join(statement.split()).startswith(patterns[failure]):
            matched += 1
            if matched == (2 if failure == "second_audit" else 1):
                raise RuntimeError("isolated decision admission rollback")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(SQLAlchemyCursor, "execute", execute)
        with pytest.raises(RuntimeError, match="isolated decision"):
            await submit(p, wait.batch[0].approval_id)
    assert matched > 0 and facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reply_loss", "cancel_waiter"])
async def test_committed_decision_is_recovered_by_exact_retry_not_replayed(pending_formal, monkeypatch, failure, caplog):
    p = pending_formal
    wait = await park(p)
    service = implementation(p)
    entered, release, finished = Event(), Event(), Event()
    original_submit = type(service)._submit
    original_append = p.s.h.tasks._sessions.append_event_in_transaction
    def append(*args, **kwargs):
        result = original_append(*args, **kwargs)
        if args[2].type == "approval.continuation_queued" and failure == "cancel_waiter":
            entered.set()
            assert release.wait(30)
        return result
    def commit(self, *args):
        try:
            result = original_submit(self, *args)
            if failure == "reply_loss":
                raise RuntimeError("isolated committed decision reply loss")
            return result
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(type(service), "_submit", commit)
        patch.setattr(p.s.h.tasks._sessions, "append_event_in_transaction", append)
        task = asyncio.create_task(submit(p, wait.batch[0].approval_id))
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match="reply loss"):
                await task
    before = facts(p)
    first = await submit(p, wait.batch[0].approval_id)
    assert first.status == "queued" and await submit(p, wait.batch[0].approval_id) == first
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []
    assert any(getattr(r, "recovery_reason", None) == "decision_admission_receipt_unknown"
        and getattr(r, "automatic_replay", None) is False for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["same", "opposite", "different_items"])
async def test_concurrent_decisions_have_one_immutable_intent_per_approval(pending_formal, mode):
    p = pending_formal
    wait = await park(p)
    chosen, other = wait.batch
    calls = [submit(p, chosen.approval_id), submit(p,
        other.approval_id if mode == "different_items" else chosen.approval_id,
        "denied" if mode == "opposite" else "approved")]
    results = await asyncio.gather(*calls, return_exceptions=True)
    if mode == "opposite":
        assert sum(isinstance(item, ValueError) for item in results) == 1
        assert len([item for item in results if not isinstance(item, Exception)]) == 1
    else:
        assert all(not isinstance(item, Exception) for item in results)
        assert len({item.id for item in results}) == (2 if mode == "different_items" else 1)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {p.s.h.tasks._tasks} WHERE operation='case_approval'")
        assert cur.fetchone()["n"] == (2 if mode == "different_items" else 1)
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["unparked", "foreign_approval", "project", "pending", "wait_hash", "owner",
    "run_cancelled", "claimed", "missing_wait_audit", "job_started", "native_source", "control", "interrupt", "live_lease"])
async def test_historical_wait_alone_never_grants_a_new_decision(pending_formal, drift):
    p = pending_formal
    wait = await park(p) if drift != "unparked" else None
    approval_id = wait.batch[0].approval_id if wait else "unpublished-approval"
    if drift == "run_cancelled":
        await p.s.d.runs.cancel_run(p.s.detail.run.id, "fixture cancelled before decision", wait.parked_at)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "foreign_approval":
            approval_id = "foreign-approval"
        elif drift in {"project", "pending", "wait_hash"}:
            if drift == "project":
                # NULL avoids referencing another real project, and is still a
                # deterministic mismatch with the frozen original.
                cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET project_id=NULL WHERE id=%s", (p.s.session.id,))
            else:
                path = "{pending_turn,turn_id}" if drift == "pending" else "{pending_turn,controlled_case_wait_sha256}"
                cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,%s,'\"foreign\"') WHERE id=%s",
                    (path, p.s.session.id))
        elif drift == "owner":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET owner_epoch=owner_epoch+1 WHERE binding_id=%s", (p.s.binding.binding_id,))
        elif drift == "claimed":
            store = p.s.d.runs
            item = store._lock_item(cur, p.s.item.id)
            attempt = store._lock_attempt(cur, item.id, item.lease_token)
            store._write_item(cur, item.model_copy(update={"status": "claimed"}))
            store._write_attempt(cur, attempt.model_copy(update={"status": "claimed"}))
        elif drift == "missing_wait_audit":
            cur.execute(f"DELETE FROM {p.h.settings.database.postgres_event_table} WHERE type='test_run.case_approval_waiting'")
        elif drift == "job_started":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} SET started_at=clock_timestamp() WHERE id=%s",
                (wait.batch[0].tool_job_id,))
        elif drift == "native_source":
            cur.execute(f"UPDATE {p.h.settings.deep_agents.checkpoint_schema}.checkpoints SET "
                "metadata=jsonb_set(metadata,'{source}','\"update\"') WHERE thread_id=%s", (p.s.binding.thread_id,))
        elif drift == "control":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
                "'{control}','{\"control_state\":\"cancelled\"}'::jsonb) WHERE id=%s", (p.s.session.id,))
        elif drift == "interrupt":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET interrupt_requested=TRUE WHERE id=%s", (p.h.task.id,))
        elif drift == "live_lease":
            cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=metadata || jsonb_build_object("
                "'turn_lease_expires_at',clock_timestamp()+INTERVAL '60 seconds','turn_lease_turn_id',%s::text) WHERE id=%s",
                (p.s.binding.turn_id, p.s.session.id))
    before = facts(p)
    error = FrameworkMessageStageRecoveryRequired if drift == "missing_wait_audit" else ValueError
    with pytest.raises(error):
        await submit(p, approval_id)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["outbox", "audit", "input_hash", "decision", "missing_receipt"])
async def test_existing_admission_never_repairs_missing_or_conflicting_originals(pending_formal, drift):
    p = pending_formal
    wait = await park(p)
    first = await submit(p, wait.batch[0].approval_id)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "outbox":
            cur.execute(f"DELETE FROM {p.h.settings.database.postgres_task_outbox_table} WHERE event_key=%s", ("agent_turn:" + first.id,))
        elif drift == "audit":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=payload || '{{\"foreign\":true}}'::jsonb "
                "WHERE payload->>'task_id'=%s AND type='approval.resolved'", (first.id,))
        elif drift == "input_hash":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_hash=%s WHERE id=%s", ("0"*64, first.id))
        elif drift == "decision":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_approval_table} SET status='denied' WHERE id=%s", (first.reference_id,))
        else:
            cur.execute(f"DELETE FROM {p.s.h.tasks._tasks}_decision WHERE approval_id=%s", (first.reference_id,))
    before = facts(p)
    error = FrameworkMessageStageRecoveryRequired if drift == "audit" else ValueError
    with pytest.raises(error):
        await submit(p, first.reference_id)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_reference_decode_and_relay_do_not_grant_generic_claim_or_production_execution(pending_formal, monkeypatch):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import RedisTaskQueue, QueuedTask

    p = pending_formal
    wait = await park(p)
    first = await submit(p, wait.batch[0].approval_id)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {p.h.settings.database.postgres_task_outbox_table} WHERE event_key=%s", ("agent_turn:"+first.id,))
        raw = cur.fetchone()["payload"]
    assert parse_agent_task_command(raw).operation == "case_approval"
    queue = RedisTaskQueue(p.h.settings.database.redis_url, stream=p.h.settings.orchestration.agent_task_v2_stream,
        group="formal-decision-admission", client=p.s.h.redis)
    await queue.connect()
    assert await p.s.h.outbox.relay_once(queue) == 2  # Origin and exact new decision, no inline execution.
    before = facts(p)
    with pytest.raises(ValueError, match="dedicated dual-owner"):
        await p.s.h.tasks.claim(first.id, worker_id="generic", expected_binding=p.s.binding)
    def forbidden(*args, **kwargs):
        pytest.fail("Decision format support acquired execution authority")
    monkeypatch.setattr(p.s.h.tasks, "claim", forbidden)
    dispatcher = AgentTaskDispatcher(p.h.settings, p.s.h.tasks, p.s.h.sessions, p.s.h.resources,
        framework_capabilities=capabilities())
    for _ in range(2):
        with pytest.raises(TaskDeferred, match="framework_runtime_kind_unavailable"):
            await dispatcher.handle_v2(QueuedTask("decision", raw), worker_id="disabled-case-consumer")
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_old_reader_cannot_create_decision_and_identical_retry_survives_real_run_cancel(pending_formal):
    p = pending_formal
    wait = await park(p)
    before = facts(p)
    with pytest.raises(TaskDeferred, match="approval_admission_format_unavailable"):
        await implementation(p).submit(delivery(p.s), wait.batch[0].approval_id,
            ApprovalDecisionRequest(decision="approved"),
            capabilities=replace(capabilities(), controlled_case_approval_admission_max_version=0))
    assert facts(p) == before
    first = await submit(p, wait.batch[0].approval_id)
    await p.s.d.runs.cancel_run(p.s.detail.run.id, "fixture cancel after accepted intent", wait.parked_at)
    before = facts(p)
    assert await submit(p, first.reference_id) == first
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_ids", ["missing_chosen", "extra", "duplicate"])
async def test_new_decision_requires_the_whole_current_parked_batch(pending_formal, pending_ids):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    p = pending_formal
    wait = await park(p)
    ids = [entry.approval_id for entry in wait.batch]
    chosen = ids[-1]
    if pending_ids == "missing_chosen":
        ids.pop()
    elif pending_ids == "extra":
        ids.append("foreign-approval")
    else:
        ids.append(ids[0])
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET metadata=jsonb_set(metadata,"
            "'{pending_turn,pending_approval_ids}',%s::jsonb) WHERE id=%s", (json.dumps(ids), p.s.session.id))
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="complete parked batch"):
        await submit(p, chosen)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_historical_ack_survives_later_ledger_but_cannot_admit_a_new_decision(pending_formal):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
    from tests.test_controlled_case_approval_wait import clock

    p = pending_formal
    wait = await park(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT record FROM {p.s.calls.calls_table} WHERE binding_id=%s", (p.s.binding.binding_id,))
        later = deepcopy(cur.fetchone()["record"])
        later["intent"]["attempt_id"] = "fixture-later-reservation"
        later["intent"]["logical_call_id"] = "fixture-later-call"
        later["created_at"] = clock(p).isoformat()
        cur.execute(f"INSERT INTO {p.s.calls.calls_table}(binding_id,attempt_id,record) VALUES (%s,%s,%s::jsonb)",
            (p.s.binding.binding_id, later["intent"]["attempt_id"], json.dumps(later)))
    before = facts(p)
    assert await wait_service(p).acknowledge_delivery(delivery(p.s), capabilities=capabilities()) == wait
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="later model activity"):
        await submit(p, wait.batch[0].approval_id)
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_prior_human_decision_without_dedicated_task_is_never_overwritten_or_requeued(pending_formal):
    from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
    from src.schemas.session import ToolApprovalStatus
    from tests.test_controlled_case_approval_wait import clock

    p = pending_formal
    wait = await park(p)
    approval = next(a for a in await p.h.store.list_approvals(p.s.session.id) if a.id == wait.batch[0].approval_id)
    approval.status, approval.resolved_at = ToolApprovalStatus.denied, clock(p)
    approval.decision_note = "Retained earlier decision; not a continuation proof."
    await p.h.store.save_approval(p.s.session.id, approval)
    before = facts(p)
    with pytest.raises(AgentSubmissionConflict, match="prior decision without"):
        await submit(p, approval.id, "denied")
    assert facts(p) == before and len(p.requests) == 1 and p.effects == []
