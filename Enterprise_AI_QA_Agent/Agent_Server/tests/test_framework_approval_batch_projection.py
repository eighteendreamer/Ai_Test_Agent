"""A native sibling batch has one PG transaction, not one commit per approval."""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import threading

import pytest

from src.application.deep_agents import DeepAgentRuntimeResult
from src.application.permissions.permission_service import PermissionService
from src.application.runtime.runtime_service import RuntimeService
from src.application.runtime.tool_runtime_service import ToolRuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import make_json_safe
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_tool_activity import ACTIVITY_KEY, read_tool_activity
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, runtime_owner_table
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.session import ExecutionEvent
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness, prepare, projector, counts
from tests.test_framework_tool_bridge import state_for


def runtime_for(h):
    return RuntimeService(graph=None, model_runtime_service=object(),
        tool_runtime_service=ToolRuntimeService(tool_job_service=h.jobs),
        tool_registry=h.registry, runtime_control=RuntimeControlRegistry(), permission_service=PermissionService(),
        tool_job_service=h.jobs, framework_approval_projection=projector(h))


def native_result(h):
    return DeepAgentRuntimeResult(output_text="", messages=[], native_interrupts=h.identities,
        interrupts=[{"id": item.interrupt_id, "value": h.native.tasks[item.tool_call_id].interrupt.value}
            for item in h.identities])


@pytest.mark.asyncio
async def test_runtime_second_sibling_failure_rolls_back_the_whole_native_batch(projection_harness, monkeypatch):
    h = projection_harness
    runtime, state = runtime_for(h), state_for(h)
    failing_id = prepare(h, 1).approval.id
    append = h.store.append_event_in_transaction
    def fail_second(*args, **kwargs):
        result = append(*args, **kwargs)
        if args[2].type == "approval.created" and args[2].payload["approval_id"] == failing_id:
            raise RuntimeError("isolated second sibling projection failure")
        return result
    monkeypatch.setattr(h.store, "append_event_in_transaction", fail_second)
    before = counts(h)
    with framework_owner_scope(h.owner.runtime_owner), pytest.raises(RuntimeError, match="second sibling"):
        await runtime._record_deep_agent_interrupts(state, native_result(h))
    assert counts(h) == before
    assert "deep_agent_approval_batch" not in state["context_bundle"]
    assert "_deep_agent_waiting_records" not in state and h.native.calls == []


@pytest.mark.asyncio
async def test_explicit_batch_port_projects_both_originals_once(projection_harness):
    h = projection_harness
    service = projector(h)
    proposals = [prepare(h, i) for i in range(2)]
    before = counts(h)
    result = await service.project_many(h.owner.runtime_owner, proposals)
    # The original ToolJob writer adds a server-observed activity journal.
    # Compare the entire candidate plus that verified, persisted addition.
    for saved, proposal in zip(result, proposals, strict=True):
        assert saved.approval == proposal.approval and saved.link == proposal.link
        activity = read_tool_activity(saved.job)
        assert activity.origin_observed and len(activity.events) == 1
        assert activity.events[0].status == "waiting_approval" and not activity.events[0].output_observed
        expected = deepcopy(proposal.job)
        expected.metadata[ACTIVITY_KEY] = activity.model_dump(mode="json")
        assert saved.job == expected == await h.jobs.get_job(saved.job.id)
    assert counts(h) == {key: value+2 for key, value in before.items()}
    # ToolApprovalRequest uses the PG TIMESTAMPTZ row on replay. Compare all
    # fields with the project's existing UTC codec, not naive/aware objects.
    assert make_json_safe(await service.project_many(h.owner.runtime_owner, proposals)) == make_json_safe(result)
    assert counts(h) == {key: value+2 for key, value in before.items()}
    assert h.native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback", [False, True])
async def test_caller_transaction_composes_batch_without_its_own_connection_or_commit(
        projection_harness, monkeypatch, caplog, rollback):
    h, service = projection_harness, projector(projection_harness)
    proposals, before = [prepare(h, i) for i in range(2)], counts(h)
    def forbidden(*args, **kwargs):
        pytest.fail("Caller-owned projection opened its own connection")
    monkeypatch.setattr("src.runtime.framework_approval_projection.postgres_connect", forbidden)
    caplog.set_level("INFO", logger="src.runtime.framework_approval_projection")
    with pytest.raises(RuntimeError, match="after batch") if rollback else nullcontext():
        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, h.settings)
            results = service.project_many_in_transaction(cur, h.owner.runtime_owner, proposals)
            assert [item.link for item in results] == [item.link for item in proposals]
            assert counts(h) == before  # A second connection sees none before caller commit.
            h.store.append_event_in_transaction(cur, h.session_id, ExecutionEvent(
                id="isolated-batch-composition", session_id=h.session_id, type="test.batch_staged",
                timestamp=datetime.now(timezone.utc), payload={"approval_count": 2}),
                turn_lease_token=h.owner.lease_token)
            if rollback:
                raise RuntimeError("isolated caller failure after batch")
    assert counts(h) == (before if rollback else {
        key: value + (3 if key == "events" else 2) for key, value in before.items()})
    assert not any(item.message == "framework_approval_projected" for item in caplog.records)
    staged = [item for item in caplog.records if item.message == "framework_approval_projection_staged"]
    assert len(staged) == 1 and staged[0].transaction_committed is False
    assert h.native.calls == []


@pytest.mark.asyncio
async def test_batch_reuses_decision_and_terminal_original_without_resetting_sibling(projection_harness):
    h, service = projection_harness, projector(projection_harness)
    first = await service.project(h.owner.runtime_owner, prepare(h))
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_approval_table} SET status='denied', "
            "decision_note='original denial', resolved_at=clock_timestamp() WHERE id=%s", (first.approval.id,))
        cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET status='denied', "
            "completed_at=clock_timestamp(), output_payload=%s::jsonb WHERE id=%s",
            ('{"evidence":"original-denial"}', first.job.id))
    original_approval = await service.load(h.owner.runtime_owner, first.approval.id)
    original_job = await h.jobs.get_job(first.job.id)
    before = counts(h)
    result = await service.project_many(h.owner.runtime_owner, [prepare(h, i) for i in range(2)])
    assert result[0].approval == original_approval and result[0].job == original_job
    assert result[1].approval.status.value == "pending" and result[1].job.status.value == "waiting_approval"
    assert counts(h) == {key: value+1 for key, value in before.items()}
    assert h.native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["duplicate", "missing_native", "scope"])
async def test_invalid_second_projection_cannot_publish_the_first(projection_harness, invalid):
    h = projection_harness
    proposals = [prepare(h, i) for i in range(2)]
    if invalid == "duplicate":
        proposals[1] = deepcopy(proposals[0])
    elif invalid == "scope":
        proposals[1].approval.metadata["approval_scope_hash"] = "changed"
    else:
        proposals[1] = proposals[1].model_copy(update={"link":
            proposals[1].link.model_copy(update={"graph_task_id": "missing-native-task"})})
        native = h.identities[1].model_copy(update={"graph_task_id": "missing-native-task"}).model_dump(mode="json")
        proposals[1].approval.metadata["framework_native_identity"] = native
        proposals[1].job.metadata["framework_native_identity"] = native
    before = counts(h)
    with pytest.raises(FrameworkApprovalRecoveryRequired):
        await projector(h).project_many(h.owner.runtime_owner, proposals)
    assert counts(h) == before and h.native.calls == []


@pytest.mark.asyncio
async def test_empty_batch_is_a_fenced_noop(projection_harness):
    h, service = projection_harness, projector(projection_harness)
    before = counts(h)
    assert await service.project_many(h.owner.runtime_owner, []) == []
    assert counts(h) == before
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
    with pytest.raises(ContinuationLeaseLostError):
        await service.project_many(h.owner.runtime_owner, [])
    assert counts(h) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "agent"])
async def test_final_event_followed_by_lease_expiry_rolls_back_whole_batch(
        projection_harness, monkeypatch, lease):
    h = projection_harness
    proposals, append = [prepare(h, i) for i in range(2)], h.store.append_event_in_transaction
    def expire_after_last_event(cur, session_id, event, **kwargs):
        result = append(cur, session_id, event, **kwargs)
        if event.type == "approval.created" and event.payload["approval_id"] == proposals[-1].approval.id:
            if lease == "session":
                cur.execute(f"UPDATE {h.settings.database.postgres_session_table} SET metadata=metadata || "
                    "jsonb_build_object('turn_lease_expires_at', clock_timestamp()-interval '1 second') WHERE id=%s",
                    (h.session_id,))
            else:
                cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET expires_at=clock_timestamp()-interval '1 second' "
                    "WHERE binding_id=%s", (h.binding.binding_id,))
        return result
    monkeypatch.setattr(h.store, "append_event_in_transaction", expire_after_last_event)
    before = counts(h)
    with pytest.raises(ContinuationLeaseLostError):
        await projector(h).project_many(h.owner.runtime_owner, proposals)
    assert counts(h) == before and h.native.calls == []


@pytest.mark.asyncio
async def test_runtime_prepares_all_siblings_before_publishing_any(projection_harness, monkeypatch):
    h = projection_harness
    runtime, state = runtime_for(h), state_for(h)
    prepare_approval = runtime._tool_governance_service.prepare_framework_approval
    def fail_second(binding, native, context):
        if native == h.identities[1]:
            raise FrameworkApprovalRecoveryRequired("isolated second sibling governance change")
        return prepare_approval(binding, native, context)
    monkeypatch.setattr(runtime._tool_governance_service, "prepare_framework_approval", fail_second)
    before = counts(h)
    with framework_owner_scope(h.owner.runtime_owner), pytest.raises(FrameworkApprovalRecoveryRequired, match="governance"):
        await runtime._record_deep_agent_interrupts(state, native_result(h))
    assert counts(h) == before and h.native.calls == []
    assert "deep_agent_approval_batch" not in state["context_bundle"]
    assert "_deep_agent_waiting_records" not in state


@pytest.mark.asyncio
async def test_concurrent_reordered_batches_keep_exactly_two_originals(projection_harness):
    h, service = projection_harness, projector(projection_harness)
    before = counts(h)
    proposals = [prepare(h, i) for i in range(2)]
    results = await asyncio.gather(*(service.project_many(h.owner.runtime_owner,
        proposals if i % 2 == 0 else proposals[::-1]) for i in range(4)))
    expected = {item.approval.id: item for item in results[0]}
    for i, result in enumerate(results):
        assert [item.link for item in result] == [item.link for item in (proposals if i % 2 == 0 else proposals[::-1])]
        assert make_json_safe({item.approval.id: item for item in result}) == make_json_safe(expected)
    assert counts(h) == {key: value+2 for key, value in before.items()}
    assert h.native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_db_thread_uses_captured_batch_and_cancellation_is_unknown_not_rollback(
        projection_harness, monkeypatch, caplog, cancel):
    h, service = projection_harness, projector(projection_harness)
    proposals = [prepare(h, i) for i in range(2)]
    originals, before = deepcopy(proposals), counts(h)
    entered, release, finished = (threading.Event() for _ in range(3))
    project = service._project_many
    def delayed(owner, selected):
        entered.set()
        try:
            assert release.wait(10), "isolated batch thread was not released"
            return project(owner, selected)
        finally:
            finished.set()
    monkeypatch.setattr(service, "_project_many", delayed)
    task = asyncio.create_task(service.project_many(h.owner.runtime_owner, proposals))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        proposals[-1].job.input_payload["value"] = "late mutation"
        proposals.clear()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        release.set()
        if not cancel:
            result = await task
            assert [item.link for item in result] == [item.link for item in originals]
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)
        await asyncio.gather(task, return_exceptions=True)
    assert counts(h) == {key: value+2 for key, value in before.items()}
    for item in originals:
        assert make_json_safe(await service.load(h.owner.runtime_owner, item.approval.id)) == make_json_safe(item.approval)
        assert (await h.jobs.get_job(item.job.id)).input_payload == item.job.input_payload
    unknown = [item for item in caplog.records if item.message == "framework_approval_projection_cancelled"]
    assert len(unknown) == int(cancel)
    if cancel:
        assert unknown[0].recovery_reason == "approval_batch_write_receipt_unknown" and unknown[0].automatic_replay is False
    assert h.native.calls == []


@pytest.mark.asyncio
async def test_cancellation_after_last_pg_write_can_commit_without_publishing_runtime_state(
        projection_harness, monkeypatch, caplog):
    h = projection_harness
    runtime, state = runtime_for(h), state_for(h)
    service = runtime._framework_approval_projection
    entered, release, finished = (threading.Event() for _ in range(3))
    append, project = h.store.append_event_in_transaction, service._project_many
    written, failures = [], []
    def hold_after_last_write(*args, **kwargs):
        result = append(*args, **kwargs)
        if args[2].type == "approval.created":
            written.append(args[2].payload["approval_id"])
            if len(written) == 2:
                entered.set()
                assert release.wait(10), "isolated SQL transaction was not released"
        return result
    def track_transaction(*args):
        try:
            return project(*args)
        except Exception as exc:
            failures.append(type(exc).__name__)
            raise
        finally:
            finished.set()
    monkeypatch.setattr(h.store, "append_event_in_transaction", hold_after_last_write)
    monkeypatch.setattr(service, "_project_many", track_transaction)
    before = counts(h)
    with framework_owner_scope(h.owner.runtime_owner):
        task = asyncio.create_task(runtime._record_deep_agent_interrupts(state, native_result(h)))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert counts(h) == before  # Real writes are still uncommitted.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not finished.is_set() and counts(h) == before
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 10)
        await asyncio.gather(task, return_exceptions=True)
    assert failures == [] and counts(h) == {key: value+2 for key, value in before.items()}
    assert "deep_agent_approval_batch" not in state["context_bundle"]
    assert "_deep_agent_waiting_records" not in state and h.native.calls == []
    assert any(item.message == "framework_approval_projection_cancelled"
        and item.recovery_reason == "approval_batch_write_receipt_unknown" for item in caplog.records)
    # Explicit caller re-entry only reprojects the persisted originals. It does
    # not recover a business wait, deliver a decision or execute either tool.
    original_ids = tuple(written)
    assert len(original_ids) == len(set(original_ids)) == 2
    with framework_owner_scope(h.owner.runtime_owner):
        approvals = await runtime._record_deep_agent_interrupts(state, native_result(h))
    assert tuple(item["id"] for item in approvals) == original_ids
    assert written == [*original_ids, *original_ids]  # Two calls to the same idempotent event writer.
    assert counts(h) == {key: value+2 for key, value in before.items()} and h.native.calls == []
