"""Approval terminal -> original formal result, not a second finalizer."""
from dataclasses import replace
from copy import deepcopy
import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_claim import implementation as continuations, wire
from tests.test_controlled_case_approval_progress import implementation as progress, retain
from tests.test_controlled_case_approval_repark import prepared, repark, implementation as reparks
from tests.test_controlled_case_approval_runtime import ports, owned, expire_lease
from tests.test_controlled_case_approval_submission import implementation as submissions
from tests.test_controlled_case_approval_successor import successor_caps, submit_a
from tests.test_controlled_case_approval_wait import facts, implementation as waits
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.session import ApprovalDecisionRequest


def result_caps():
    return replace(successor_caps(), controlled_case_approval_result_available=True)


async def ready(p):
    root, b, _, parent = await prepared(p, decision="denied", queued=False)
    await repark(p, b)
    a = await submit_a(p, root)
    service, caps = continuations(p), result_caps()
    a = await service.claim(wire(p, a.id), worker_id="original-terminal-a", capabilities=caps)
    runtime, worker = ports(p, caps=caps, continuation=service)
    return SimpleNamespace(p=p, root=root, b=b, parent=parent, a=a, service=service, caps=caps,
        runtime=runtime, worker=worker)


@pytest_asyncio.fixture
async def terminal_result(pending_formal):
    t = await ready(pending_formal)
    t.outcome = await t.runtime.execute(t.a, executor=t.worker, resource_id="original-terminal-a", fencing_token=3)
    return t


@pytest.mark.asyncio
@pytest.mark.parametrize("b_decision", ["approved", "denied"])
@pytest.mark.parametrize("a_decision", ["approved", "denied"])
async def test_terminal_successor_retains_original_result_without_losing_approval_history(pending_formal, b_decision, a_decision):
    p = pending_formal
    root, b, _, parent = await prepared(p, decision=b_decision, queued=False)
    parked = await repark(p, b)
    a = await submit_a(p, root, a_decision)
    service = continuations(p)
    caps = result_caps()
    a = await service.claim(wire(p, a.id), worker_id="terminal-result-a", capabilities=caps)
    original_tasks = await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(b.id)
    runtime, worker = ports(p, caps=caps, continuation=service)
    outcome = await runtime.execute(a, executor=worker, resource_id="terminal-result-a", fencing_token=3)
    asset = outcome.result_asset
    assert asset is not None and outcome.runtime_result is not None
    assert asset.task_id == a.id and asset.attempt_id == a.attempt_id and asset.owner_epoch == a.runtime_owner.owner_epoch
    assert asset.runtime_result.model_dump(mode="json") == FrameworkRuntimeResult.from_result(outcome.runtime_result).model_dump(mode="json")
    assert asset.runtime_result.state["tool_results"][1] == parent.state["tool_results"][1]
    assert asset.runtime_result.state["tool_messages"][1] == parent.state["tool_messages"][1]
    assert [record["status"] for record in asset.runtime_result.state["tool_results"]] == [
        "completed" if a_decision == "approved" else "denied", "completed" if b_decision == "approved" else "denied"]
    assert asset.runtime_result.state["event_log"][:len(parent.state["event_log"])] == parent.state["event_log"]
    assert asset.runtime_result.state["framework_messages"] == outcome.state["framework_messages"]
    assert asset.runtime_result.state["context_bundle"]["deep_agent_approval_batch"] == []
    assert asset.assistant_message_id == p.s.asset.runtime_preparation.identity.assistant_message_id
    assert asset.projection_at == asset.runtime_result.events[-1].timestamp
    assert asset.runtime_result.state["final_response"] and asset.runtime_result.state["termination_reason"] == "completed"
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        attempt = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        assert attempt.checkpoint_version == 9
        assert attempt.checkpoint_payload["approval_history"]["initial_wait"]["state_asset"] == root.model_dump(mode="json")
        assert attempt.checkpoint_payload["approval_history"]["progress_assets"] == [parent.model_dump(mode="json")]
        assert attempt.checkpoint_payload["approval_admission"] == a.input["controlled_case_approval_admission"]
        assert attempt.checkpoint_payload["runtime_result_asset"] == asset.model_dump(mode="json")
    assert original_tasks == (await p.s.h.tasks.get(p.h.task.id), await p.s.h.tasks.get(b.id))
    assert (await p.s.h.tasks.get(a.id)).status == "running" and (await p.s.d.runs.get_run(p.s.item.run_id)).results == []
    assert outcome.assessment_asset is outcome.settlement_receipt is outcome.conversation_response is None
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=caps) == root
    assert await progress(p).read(wire(p, b.id), capabilities=caps) == parent
    assert await reparks(p).acknowledge_delivery(wire(p, b.id), capabilities=caps) == parked
    before = facts(p), native_originals(p)
    loaded = await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(a.runtime_owner,
        native=outcome.graph_outcome.native_terminal, approval_continuations=service, capabilities=caps)
    assert loaded == asset and (facts(p), native_originals(p)) == before
    assert len(p.requests) == 2 and len(p.effects) == (a_decision == "approved") + (b_decision == "approved")


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"single_tool": True}], indirect=True)
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_single_approval_terminal_retains_schema7_parent_without_inventing_progress(pending_formal, decision):
    p = pending_formal
    root, task = await owned(p, decision, path="a.py")
    caps, service = result_caps(), continuations(p)
    runtime, worker = ports(p, caps=caps, continuation=service)
    outcome = await runtime.execute(task, executor=worker, resource_id="single-original-terminal", fencing_token=2)
    asset = outcome.result_asset
    assert asset is not None and asset.task_id == task.id and asset.owner_epoch > root.owner_epoch
    assert asset.runtime_result.state["tool_results"][0]["status"] == ("completed" if decision == "approved" else "denied")
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        checkpoint = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        history = checkpoint.checkpoint_payload["approval_history"]
        assert checkpoint.checkpoint_version == 9 and history["schema_version"] == 7
        assert history["state_asset"] == root.model_dump(mode="json") and "progress_assets" not in history
    assert await waits(p).read_state(wire(p, p.h.task.id), capabilities=caps) == root
    assert await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(task.runtime_owner,
        native=outcome.graph_outcome.native_terminal, approval_continuations=service, capabilities=caps) == asset
    assert len(p.requests) == 2 and len(p.effects) == (decision == "approved")
    assert (await p.s.d.runs.get_run(p.s.item.run_id)).results == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"third_tool": True}], indirect=True)
async def test_two_serial_parents_survive_last_sibling_result_retention(pending_formal):
    p = pending_formal
    root, b, _, b_asset = await prepared(p, queued=False)
    await repark(p, b)
    a = await submit_a(p, root, "denied")
    service, caps = continuations(p), result_caps()
    a = await service.claim(wire(p, a.id), worker_id="result-middle-a", capabilities=caps)
    runtime, worker = ports(p, caps=caps, continuation=service)
    middle = await runtime.execute(a, executor=worker, resource_id="result-middle-a", fencing_token=3)
    assert middle.result_asset is None and len(middle.graph_outcome.native_interrupts) == 1
    assert len(p.requests) == 1
    a_asset = await retain(p, a, middle, caps=caps)
    await repark(p, a, caps=replace(caps, controlled_case_approval_repark_available=True))
    c = await submissions(p).submit(wire(p, p.h.task.id), root.state["tool_results"][2]["approval_id"],
        ApprovalDecisionRequest(decision="approved"), capabilities=caps)
    c = await service.claim(wire(p, c.id), worker_id="result-last-c", capabilities=caps)
    outcome = await runtime.execute(c, executor=worker, resource_id="result-last-c", fencing_token=4)
    asset = outcome.result_asset
    assert asset is not None and asset.task_id == c.id
    assert asset.runtime_result.state["tool_results"][:2] == a_asset.state["tool_results"][:2]
    assert asset.runtime_result.state["tool_messages"][:2] == a_asset.state["tool_messages"][:2]
    assert asset.runtime_result.state["event_log"][:len(a_asset.state["event_log"])] == a_asset.state["event_log"]
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        checkpoint = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        assert checkpoint.checkpoint_version == 9
        assert checkpoint.checkpoint_payload["approval_history"]["progress_assets"] == [
            b_asset.model_dump(mode="json"), a_asset.model_dump(mode="json")]
    assert await progress(p).read(wire(p, b.id), capabilities=caps) == b_asset
    assert await progress(p).read(wire(p, a.id), capabilities=caps) == a_asset
    assert len(p.requests) == 2 and [item[0]["path"] for item in p.effects] == ["b.py", "c.py"]


def test_result_writer_stays_default_off_independent_of_historical_codec():
    caps = FrameworkWorkerCapabilities.from_artifact(deepagents_available=True)
    assert caps.controlled_case_checkpoint_max_version == 9 and not caps.controlled_case_approval_result_available


@pytest.mark.asyncio
async def test_old_result_codec_defers_before_begin_delivery_or_new_model_call(pending_formal):
    t = await ready(pending_formal)
    runtime, worker = ports(t.p, continuation=t.service, caps=replace(t.caps, controlled_case_checkpoint_max_version=8))
    before = facts(t.p)
    with pytest.raises(TaskDeferred, match="case_checkpoint_format_unavailable"):
        await runtime.execute(t.a, executor=worker, resource_id="no-old-codec-result", fencing_token=3)
    assert facts(t.p) == before and (await t.p.s.h.tasks.get(t.a.id)).status == "claimed"
    assert len(t.p.requests) == 1 and t.p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["missing", "disabled", "old_codec"])
async def test_codec_read_support_never_grants_approval_result_access(terminal_result, capability):
    t, p = terminal_result, terminal_result.p
    caps = {"missing": None, "disabled": successor_caps(),
        "old_codec": replace(t.caps, controlled_case_checkpoint_max_version=8)}[capability]
    before = facts(p)
    with pytest.raises(TaskDeferred, match="case_.*unavailable"):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner,
            native=t.outcome.graph_outcome.native_terminal, approval_continuations=t.service, capabilities=caps)
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["content", "status", "name", "artifact", "missing", "duplicate"])
async def test_result_projection_checks_actual_rejection_messages_not_synthetic_artifacts(terminal_result, change):
    from src.application.runtime.controlled_case_projection import project_controlled_case_result
    from langchain_core.messages import ToolMessage

    t, p = terminal_result, terminal_result.p
    native = deepcopy(t.outcome.graph_outcome.native_terminal)
    message, = [m for m in native.values["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "B"]
    assert message.status == "error" and message.artifact is None
    if change == "missing":
        native.values["messages"].remove(message)
    elif change == "duplicate":
        native.values["messages"].insert(-1, deepcopy(message))
    else:
        setattr(message, change, {"content": "foreign denial", "status": "success", "name": "foreign_tool",
            "artifact": {"not_an_original": True}}[change])
    before = facts(p)
    materials = sorted((*t.parent.tool_materials, t.outcome.approval_tool_projection),
        key=lambda item: item.message.metadata["ordinal"])
    expected = FrameworkMessageStageRecoveryRequired if change in {"missing", "duplicate"} else ValueError
    with pytest.raises(expected):
        project_controlled_case_result(p.s.asset, native, t.outcome.graph_outcome.root_model_budget,
            t.outcome.runtime_result.state["event_log"][:-1], materials, parent=t.parent)
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["native", "receipt", "prior_receipt", "root", "history", "event", "ledger", "job",
    "asset_receipt", "session", "owner", "formal"])
async def test_result_load_requires_full_original_lineage_and_live_leases(terminal_result, drift):
    from src.runtime.controlled_case_results import asset_event_id

    t, p = terminal_result, terminal_result.p
    native = deepcopy(t.outcome.graph_outcome.native_terminal)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "native":
            native.values["messages"][-1].content = "foreign native final answer"
        elif drift in {"receipt", "prior_receipt"}:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET applied_at=NULL WHERE approval_id=%s",
                (t.a.reference_id if drift == "receipt" else t.b.reference_id,))
        elif drift == "root":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET input_hash='foreign-input' WHERE id=%s", (p.h.task.id,))
        elif drift == "history":
            checkpoint = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
            raw = deepcopy(checkpoint.checkpoint_payload)
            raw["approval_history"]["progress_assets"][0]["state"]["tool_messages"][1]["content"] = "foreign prior slot"
            p.s.d.runs._write_attempt(cur, checkpoint.model_copy(update={"checkpoint_payload": raw}))
        elif drift == "event":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=jsonb_set(payload,'{{message}}',"
                "'\"foreign event\"'::jsonb) WHERE id=%s", (t.parent.state["event_log"][0]["id"],))
        elif drift == "ledger":
            cur.execute(f"UPDATE {p.s.calls.calls_table} SET record=jsonb_set(record,'{{phase}}','\"unknown\"') WHERE binding_id=%s",
                (p.s.binding.binding_id,))
        elif drift == "job":
            cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} SET summary='foreign result' WHERE session_id=%s",
                (p.s.session.id,))
        elif drift == "asset_receipt":
            cur.execute(f"DELETE FROM {p.h.settings.database.postgres_event_table} WHERE id=%s", (asset_event_id(p.s.binding),))
        else:
            expire_lease(p, cur, drift)
    before = facts(p)
    with pytest.raises((ValueError, RuntimeError)):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner, native=native,
            approval_continuations=t.service, capabilities=t.caps)
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["admission_time", "receipt_time", "receipt_error"])
async def test_rehashed_result_and_audit_do_not_replace_original_final_decision(terminal_result, change):
    from src.schemas.controlled_case_admission import (
        ControlledCaseApprovalResultCheckpoint, ControlledCaseApprovalProgressCheckpoint, approval_result_source_sha256,
    )
    from src.schemas.controlled_case_approval_submission import ControlledCaseApprovalAdmission
    from src.schemas.framework_approval import FrameworkDecisionReceipt, approval_value_hash
    from src.runtime.controlled_case_results import asset_event_id

    t, p = terminal_result, terminal_result.p
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        attempt = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        raw = deepcopy(attempt.checkpoint_payload)
        if change == "admission_time":
            proof = raw["approval_admission"]
            proof["admitted_at"] = "2000-01-01T00:00:00Z"
            proof["admission_sha256"] = approval_value_hash({k: v for k, v in proof.items() if k != "admission_sha256"})
        else:
            raw["decision_receipt"]["created_at" if change == "receipt_time" else "last_error"] = (
                "2000-01-01T00:00:00Z" if change == "receipt_time" else "foreign final receipt")
        asset = raw["runtime_result_asset"]
        asset["source_sha256"] = approval_result_source_sha256(
            ControlledCaseApprovalProgressCheckpoint.model_validate(raw["approval_history"]),
            ControlledCaseApprovalAdmission.model_validate(raw["approval_admission"]),
            FrameworkDecisionReceipt.model_validate(raw["decision_receipt"]))
        asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
        # Like progress originals: valid hashes and matching outer audit must
        # not replace the real last Task admission or applied journal receipt.
        ControlledCaseApprovalResultCheckpoint.model_validate(raw)
        p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": raw}))
        cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=jsonb_set(payload,"
            "'{runtime_result_asset_sha256}',to_jsonb(%s::text)) WHERE id=%s", (asset["asset_sha256"], asset_event_id(p.s.binding)))
    before = facts(p)
    with pytest.raises(RuntimeError, match="original approval lineage"):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner,
            native=t.outcome.graph_outcome.native_terminal, approval_continuations=t.service, capabilities=t.caps)
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_retained_successor_reentry_is_read_only_and_cannot_use_first_execution_finalizer(terminal_result, monkeypatch):
    from src.application.testing.verification_service import VerificationService
    from src.runtime.framework_projection_source import lock_result_source
    from src.runtime.controlled_case_results import asset_event_id

    t, p = terminal_result, terminal_result.p
    def forbidden(*args, **kwargs):
        raise AssertionError("original successor Verification must not be rebuilt")
    monkeypatch.setattr(VerificationService, "build_results", forbidden)
    results, native = FrameworkRuntimeResults(p.s.h.tasks), t.outcome.graph_outcome.native_terminal
    kwargs = dict(native=native, approval_continuations=t.service, capabilities=t.caps)
    before = facts(p)
    first, second = await asyncio.gather(results.load_controlled_case(t.a.runtime_owner, **kwargs),
        results.freeze_controlled_case(t.a.runtime_owner, runtime_result=t.outcome.runtime_result, **kwargs))
    assert first == second == t.outcome.result_asset and facts(p) == before
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        with pytest.raises(ValueError):
            lock_result_source(cur, p.h.settings, t.a.runtime_owner, controlled_case_native=native)
        cur.execute(f"SELECT count(*) AS n FROM {p.h.settings.database.postgres_event_table} WHERE id=%s", (asset_event_id(p.s.binding),))
        assert cur.fetchone()["n"] == 1
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_final_retention_audit_crossing_lease_rolls_back_asset_and_preserves_applied_decisions(pending_formal, monkeypatch, lease):
    t = await ready(pending_formal)
    p, captured = t.p, []
    freeze, append = FrameworkRuntimeResults.freeze_controlled_case, p.s.h.tasks._sessions.append_event_in_transaction
    async def capture(self, owner, **kwargs):
        captured.append((deepcopy(owner), deepcopy(kwargs), facts(p)))
        return await freeze(self, owner, **kwargs)
    def cross(cur, session_id, event, *args, **kwargs):
        result = append(cur, session_id, event, *args, **kwargs)
        if event.type == "test_run.case_result_prepared":
            expire_lease(p, cur, lease, delay="0.05 seconds")
            cur.execute("SELECT pg_sleep(0.1)")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(FrameworkRuntimeResults, "freeze_controlled_case", capture)
        patch.setattr(p.s.h.tasks._sessions, "append_event_in_transaction", cross)
        with pytest.raises(ContinuationLeaseLostError):
            await t.runtime.execute(t.a, executor=t.worker, resource_id="last-result-audit", fencing_token=3)
    owner, kwargs, before = captured[0]
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        assert p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id).checkpoint_version == 8
    asset = await FrameworkRuntimeResults(p.s.h.tasks).freeze_controlled_case(owner, **kwargs)
    assert asset.task_id == t.a.id and asset.runtime_result.output_text == "fixture answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "reply_loss", "cancel_waiter"])
async def test_result_write_outcome_does_not_authorize_reexecution(pending_formal, monkeypatch, failure):
    from src.runtime.controlled_case_results import ControlledCaseResults

    t = await ready(pending_formal)
    p, captured = t.p, []
    entered, release, finished = Event(), Event(), Event()
    freeze, material = FrameworkRuntimeResults.freeze_controlled_case, ControlledCaseResults._material
    append = p.s.h.tasks._sessions.append_event_in_transaction
    async def capture(self, owner, **kwargs):
        captured.append((deepcopy(owner), deepcopy(kwargs), facts(p)))
        return await freeze(self, owner, **kwargs)
    def write(*args, **kwargs):
        result = append(*args, **kwargs)
        if args[2].type == "test_run.case_result_prepared":
            if failure == "rollback":
                raise RuntimeError("isolated successor result rollback")
            if failure == "cancel_waiter":
                entered.set()
                assert release.wait(30)
        return result
    def complete(self, *args):
        try:
            value = material(self, *args)
            if failure == "reply_loss":
                raise RuntimeError("isolated successor result ACK loss")
            return value
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(FrameworkRuntimeResults, "freeze_controlled_case", capture)
        patch.setattr(ControlledCaseResults, "_material", complete)
        patch.setattr(p.s.h.tasks._sessions, "append_event_in_transaction", write)
        waiter = asyncio.create_task(t.runtime.execute(t.a, executor=t.worker, resource_id="result-unknown-ack", fencing_token=3))
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match="isolated successor result"):
                await waiter
    owner, kwargs, before = captured[0]
    if failure == "rollback":
        assert facts(p) == before
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        assert p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id).checkpoint_version == (8 if failure == "rollback" else 9)
    results = FrameworkRuntimeResults(p.s.h.tasks)
    asset = await results.freeze_controlled_case(owner, **kwargs)
    saved = facts(p)
    assert await results.freeze_controlled_case(owner, **kwargs) == asset and facts(p) == saved
    with pytest.raises(ContinuationLeaseLostError):
        await t.runtime.execute(t.a, executor=t.worker, resource_id="no-unknown-replay", fencing_token=4)
    assert len(p.requests) == 2 and len(p.effects) == 1 and (await p.s.h.tasks.get(t.a.id)).status == "running"
