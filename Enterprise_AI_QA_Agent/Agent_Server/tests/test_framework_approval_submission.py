"""v2 decision admission and serial ownership, with real native interrupts."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.exc import DBAPIError

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.runtime.framework_approval_journal import FrameworkApprovalJournal, FrameworkApprovalRecoveryRequired
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import parse_agent_task_command
from src.schemas.session import ApprovalDecisionRequest, SessionStatus
from tests.test_agent_task_dispatch import harness, payload
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_receipts import native_approvals, link_pending_approval, receipt_record
from tests.test_runtime_binding_persistence import make_command


@pytest_asyncio.fixture
async def approval_harness(checkpoint_harness, monkeypatch):
    h = checkpoint_harness
    h.native = await native_approvals(h, monkeypatch)
    h.links = {key: await link_pending_approval(h, h.native, key) for key in ("A", "B")}

    def wait_for_approvals(metadata, status):
        metadata["pending_turn"] = {
            "turn_id": h.binding.turn_id,
            "pending_approval_ids": [item.link.approval_id for item in h.links.values()],
            "context_bundle": {"runtime_binding": h.binding.model_dump(mode="json")},
        }
        metadata["control"] = {"control_state": "waiting_approval"}

    assert await h.store.update_status_and_mutate_metadata(h.session_id,
        status=SessionStatus.waiting_approval, allowed_current_statuses={SessionStatus.running},
        mutate=wait_for_approvals, turn_lease_token=h.owner.lease_token)
    await h.tasks.finish(h.owner, status="completed", result_status="waiting_approval")
    yield h


def approval_command(h, key="B"):
    return make_command(h, h.binding).model_copy(update={
        "operation": "approval_resume", "reference_id": h.links[key].link.approval_id})


async def submit(h, key="B", decision="approved", *, command=None, **kwargs):
    return await h.tasks.submit_approval_v2(command or approval_command(h, key),
        h.links[key].link.approval_id, ApprovalDecisionRequest(decision=decision, **kwargs))


def saved_facts(h):
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT id,status,decision_note FROM {h.settings.database.postgres_approval_table} ORDER BY id")
        approvals = cur.fetchall()
        counts = {}
        for kind, table in {
            "tasks": h.settings.database.postgres_agent_task_table,
            "receipts": h.settings.database.postgres_agent_task_table + "_decision",
            "outbox": h.settings.database.postgres_task_outbox_table,
            "events": h.settings.database.postgres_event_table,
        }.items():
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            counts[kind] = cur.fetchone()["n"]
        return approvals, counts


@pytest.mark.asyncio
async def test_decision_task_receipt_and_v2_outbox_are_one_submission(approval_harness):
    h = approval_harness
    task = await submit(h, reason="approve B")
    assert task.status == "queued" and task.operation == "approval_resume"
    assert task.runtime_binding == h.binding and task.reference_id == h.links["B"].link.approval_id
    receipt = receipt_record(h, h.links["B"])
    assert receipt.state == "queued" and receipt.decision == {"type": "approve"}
    saved = await h.tasks.get(task.id)
    assert saved.input["decision_id"] == receipt.decision_id
    assert saved.input["approval"]["status"] == "approved"
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT stream,payload FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"agent_turn:{task.id}",))
        outbox = cur.fetchone()
    assert outbox["stream"] == h.settings.orchestration.agent_task_v2_stream
    command = parse_agent_task_command(outbox["payload"])
    assert command.schema_version == 2 and command.task_id == task.id
    assert command.runtime_binding == h.binding and command.reference_id == task.reference_id
    assert "lease_token" not in task.model_dump() and h.native.calls == []
    events = await h.store.list_events(h.session_id)
    projected = [event.type for event in events if event.payload.get("task_id") == task.id]
    assert projected == ["approval.resolved", "approval.continuation_queued"]


@pytest.mark.asyncio
async def test_other_decision_can_queue_while_first_approval_is_running(approval_harness):
    h = approval_harness
    first = await submit(h)
    owner = await h.tasks.claim(first.id, worker_id="B-running", expected_binding=h.binding)
    await h.tasks.begin(owner, resource_id="test-B", fencing_token=2)
    before = await h.store.get_session(h.session_id)
    second = await submit(h, "A", "denied", reason="reject A")
    after = await h.store.get_session(h.session_id)
    assert second.id != first.id and second.status == "queued"
    assert receipt_record(h, h.links["A"]).decision == {"type": "reject", "message": "reject A"}
    assert after.status == SessionStatus.running
    assert after.metadata["turn_lease_token"] == before.metadata["turn_lease_token"]
    assert after.metadata["control"] == before.metadata["control"]
    with pytest.raises(TaskDeferred):
        await h.tasks.claim(second.id, worker_id="A-early", expected_binding=h.binding)
    assert (await h.tasks.get(second.id)).attempt_no == 0


@pytest.mark.asyncio
async def test_concurrent_repeated_clicks_with_different_ids_keep_one_intent(approval_harness):
    h = approval_harness
    tasks = await asyncio.gather(*(submit(h, client_request_id=f"click-{index}", reason=f"note-{index}")
        for index in range(6)))
    assert len({task.id for task in tasks}) == 1
    _, counts = saved_facts(h)
    assert counts["tasks"] == counts["outbox"] == 2 and counts["receipts"] == 1
    original = receipt_record(h, h.links["B"])
    with pytest.raises(AgentSubmissionConflict):
        await submit(h, decision="denied", client_request_id="opposite")
    assert receipt_record(h, h.links["B"]) == original


@pytest.mark.asyncio
async def test_later_message_cannot_block_current_turn_approval_continuation(approval_harness):
    h = approval_harness
    later = await h.dispatcher.submit(h.session_id, payload("next turn", key="next", submit_mode="background"))
    task = await submit(h)
    owner = await h.tasks.claim(task.id, worker_id="B-first", expected_binding=h.binding)
    assert owner.runtime_owner.binding == h.binding and owner.runtime_owner.owner_epoch == 2
    with pytest.raises(TaskDeferred):
        await h.tasks.claim(later.task.id, worker_id="next-too-soon")
    assert (await h.tasks.get(later.task.id)).attempt_no == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["receipt", "task", "event", "outbox"])
async def test_submission_failure_rolls_back_all_facts(approval_harness, monkeypatch, failure):
    h = approval_harness
    command = approval_command(h)
    before = saved_facts(h)
    if failure == "receipt":
        original = FrameworkApprovalJournal.queue_decision_in_transaction

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected receipt failure")

        monkeypatch.setattr(FrameworkApprovalJournal, "queue_decision_in_transaction", fail)
    elif failure == "task":
        command = command.model_copy(update={"task_id": h.owner.id})
    elif failure == "event":
        original = h.tasks._event

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected event failure")

        monkeypatch.setattr(h.tasks, "_event", fail)
    else:
        # This trigger is restricted to the UUID inbox and uses the fixture's
        # UUID schema, removed by its existing teardown after the test.
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"CREATE FUNCTION {h.settings.deep_agents.checkpoint_schema}.fail_outbox() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected outbox failure'; END $$")
            cur.execute(f"CREATE TRIGGER fail_test_outbox AFTER INSERT ON {h.settings.database.postgres_task_outbox_table} "
                f"FOR EACH ROW EXECUTE FUNCTION {h.settings.deep_agents.checkpoint_schema}.fail_outbox()")
    with pytest.raises((RuntimeError, DBAPIError)):
        await submit(h, command=command)
    assert saved_facts(h) == before
    assert receipt_record(h, h.links["B"]) is None and h.native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["turn", "binding", "pending_ids", "cancel", "owner", "interrupt"])
async def test_invalid_active_turn_cannot_admit_a_decision(approval_harness, change):
    h = approval_harness

    def mutate(metadata, status):
        pending = metadata["pending_turn"]
        if change == "turn":
            pending["turn_id"] = "another-turn"
        elif change == "binding":
            pending["context_bundle"]["runtime_binding"] = h.binding.model_copy(
                update={"runtime_version": "different-version"}).model_dump(mode="json")
        elif change == "pending_ids":
            pending["pending_approval_ids"] = []
        elif change == "cancel":
            metadata["control"]["control_state"] = "interrupt_requested"

    await h.store.update_status_and_mutate_metadata(h.session_id, status=None, mutate=mutate)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if change == "owner":
            cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table}_runtime "
                "SET execution_state='recovery_required' WHERE binding_id=%s", (h.binding.binding_id,))
        elif change == "interrupt":
            cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} "
                "SET interrupt_requested=TRUE WHERE id=%s", (h.owner.id,))
    before = saved_facts(h)
    with pytest.raises(AgentSubmissionConflict):
        await submit(h)
    assert saved_facts(h) == before and receipt_record(h, h.links["B"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["scope", "arguments", "link", "project", "decision"])
async def test_invalid_approval_identity_cannot_admit_a_decision(approval_harness, change):
    h = approval_harness
    command = approval_command(h)
    decision = "approved"
    if change == "scope":
        approvals = await h.store.list_approvals(h.session_id)
        approval = next(item for item in approvals if item.id == h.links["B"].link.approval_id)
        approval.metadata["approval_scope_hash"] = "changed-scope"
        await h.store.save_approval(h.session_id, approval)
    elif change == "arguments":
        approvals = await h.store.list_approvals(h.session_id)
        approval = next(item for item in approvals if item.id == h.links["B"].link.approval_id)
        approval.metadata["arguments"] = {"value": "C"}
        await h.store.save_approval(h.session_id, approval)
    elif change == "link":
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"DELETE FROM {h.settings.database.postgres_agent_task_table}_interrupt WHERE approval_id=%s",
                (h.links["B"].link.approval_id,))
    elif change == "project":
        command = command.model_copy(update={"project_id": str(uuid4())})
    else:
        decision = "pending"
    before = saved_facts(h)
    with pytest.raises((AgentSubmissionConflict, FrameworkApprovalRecoveryRequired, ValueError)):
        await submit(h, command=command, decision=decision)
    assert saved_facts(h) == before and receipt_record(h, h.links["B"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [
    {"approval_type": "ui_recording"}, {"source": "test_run_case_execution"},
    {"source": "test_run_agent_execution"}, {"proxy_child_session_id": "child"},
    {"proxy_child_approval_id": "child-approval"},
])
async def test_dedicated_approval_cannot_enter_generic_v2_submission(approval_harness, metadata):
    h = approval_harness
    approval = next(item for item in await h.store.list_approvals(h.session_id)
        if item.id == h.links["B"].link.approval_id)
    approval.metadata.update(metadata)
    await h.store.save_approval(h.session_id, approval)
    before = saved_facts(h)
    with pytest.raises(AgentSubmissionConflict, match="dedicated"):
        await submit(h)
    assert saved_facts(h) == before


@pytest.mark.asyncio
async def test_current_turn_continuations_keep_fifo_and_capture_new_owner(approval_harness):
    h = approval_harness
    first, second = await submit(h), await submit(h, "A")
    with pytest.raises(TaskDeferred, match="earlier"):
        await h.tasks.claim(second.id, worker_id="A-too-soon", expected_binding=h.binding)
    owner = await h.tasks.claim(first.id, worker_id="B", expected_binding=h.binding)
    await h.tasks.begin(owner, resource_id="B", fencing_token=2)
    await h.store.update_status_and_mutate_metadata(h.session_id,
        status=SessionStatus.waiting_approval, allowed_current_statuses={SessionStatus.running},
        turn_lease_token=owner.lease_token, mutate=lambda metadata, status: None)
    await h.tasks.finish(owner, status="completed", result_status="waiting_approval")
    following = await h.tasks.claim(second.id, worker_id="A", expected_binding=h.binding)
    assert following.runtime_owner.owner_epoch == owner.runtime_owner.owner_epoch + 1
    assert following.runtime_owner.owner_token != owner.runtime_owner.owner_token


@pytest.mark.asyncio
async def test_cancelled_queued_continuation_is_terminal_without_claim(approval_harness):
    h = approval_harness
    task = await submit(h)
    await h.store.update_status_and_mutate_metadata(h.session_id, status=None,
        mutate=lambda metadata, status: metadata["control"].update(control_state="interrupt_requested"))
    saved = await h.tasks.claim(task.id, worker_id="late-B", expected_binding=h.binding)
    assert saved.status == "cancelled" and saved.attempt_no == 0 and saved.runtime_owner is None
    assert (await h.tasks.claim(task.id, worker_id="duplicate-B", expected_binding=h.binding)).status == "cancelled"
    assert h.native.calls == []


@pytest.mark.asyncio
async def test_corrupted_receipt_cannot_claim_or_bypass_queued_message(approval_harness):
    h = approval_harness
    task = await submit(h)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} "
            "SET input_payload=jsonb_set(input_payload,'{decision_id}',to_jsonb('unknown'::text)) WHERE id=%s", (task.id,))
    saved = await h.tasks.claim(task.id, worker_id="corrupt", expected_binding=h.binding)
    assert saved.status == "recovery_required" and saved.attempt_no == 0 and saved.runtime_owner is None
    assert receipt_record(h, h.links["B"]).state == "queued" and h.native.calls == []


@pytest.mark.asyncio
async def test_repeated_accepted_decision_after_turn_closed_only_returns_original_receipt(approval_harness):
    h = approval_harness
    task = await submit(h)
    await h.store.update_status_and_mutate_metadata(h.session_id, status=None,
        mutate=lambda metadata, status: metadata.update(pending_turn={}))
    before = saved_facts(h)
    repeated = await submit(h, client_request_id="after-closure", reason="different note")
    assert repeated.id == task.id and saved_facts(h) == before


@pytest.mark.asyncio
async def test_command_cannot_save_a_task_for_a_turn_other_than_its_binding(approval_harness):
    h = approval_harness
    # model_copy deliberately bypasses validation, reproducing a stale internal
    # dispatcher mutation at the store boundary rather than invalid HTTP JSON.
    command = approval_command(h).model_copy(update={"turn_id": "another-turn"})
    before = saved_facts(h)
    with pytest.raises(ValueError, match="turn_id"):
        await submit(h, command=command)
    assert saved_facts(h) == before


@pytest.mark.asyncio
async def test_database_also_enforces_one_v2_continuation_per_approval(approval_harness):
    h = approval_harness
    task = await submit(h)
    before = saved_facts(h)
    with pytest.raises(DBAPIError):
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            cur.execute(f"INSERT INTO {h.settings.database.postgres_agent_task_table} "
                "(id,session_id,operation,turn_id,request_id,trace_id,idempotency_key,input_hash,"
                "input_payload,reference_id,runtime_binding) "
                "SELECT %s,session_id,operation,turn_id,request_id,trace_id,%s,input_hash,"
                f"input_payload,reference_id,runtime_binding FROM {h.settings.database.postgres_agent_task_table} "
                "WHERE id=%s", (f"task-{uuid4().hex}", uuid4().hex, task.id))
    assert saved_facts(h) == before


@pytest.mark.asyncio
async def test_malformed_active_binding_requires_reconciliation_without_claim(approval_harness):
    h = approval_harness
    task = await submit(h)

    def corrupt_binding(metadata, status):
        # Retain the old fingerprint so this is an inconsistent saved record,
        # distinct from an intentional switch to another valid binding.
        metadata["pending_turn"]["context_bundle"]["runtime_binding"]["tool_schema_version"] = "changed"

    await h.store.update_status_and_mutate_metadata(h.session_id, status=None, mutate=corrupt_binding)
    saved = await h.tasks.claim(task.id, worker_id="invalid-binding", expected_binding=h.binding)
    assert saved.status == "recovery_required" and saved.attempt_no == 0 and saved.runtime_owner is None
    assert h.native.calls == []
