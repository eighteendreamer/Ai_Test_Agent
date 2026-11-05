"""Retained approval results read historical facts, not consumed live parking."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_pending_approvals import pending_formal, native_originals
from tests.test_controlled_case_approval_claim import implementation as continuations, wire
from tests.test_controlled_case_approval_progress import retain
from tests.test_controlled_case_approval_repark import prepared, repark
from tests.test_controlled_case_approval_result import ready, result_caps, terminal_result
from tests.test_controlled_case_approval_runtime import owned, ports, expire_lease
from tests.test_controlled_case_approval_submission import implementation as submissions
from tests.test_controlled_case_approval_successor import successor_caps, submit_a
from tests.test_controlled_case_approval_wait import facts
from src.schemas.session import ApprovalDecisionRequest


def consume_session_parking(p, *, remove=False):
    # Model ONLY the existing finalizer's lawful pending_turn/status change
    # (SessionService._finalize_runtime_result), not an application commit.
    # Keep the original Session/owner/formal leases: result reads are still live.
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        expression = "metadata-'pending_turn'" if remove else "jsonb_set(metadata,'{pending_turn}','{}'::jsonb)"
        cur.execute(f"UPDATE {p.s.h.tasks._session_table} SET status='completed',metadata={expression} WHERE id=%s",
            (p.s.session.id,))


def load_options(t):
    return dict(native=t.outcome.graph_outcome.native_terminal, approval_continuations=t.service, capabilities=t.caps)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"single_tool": True}, {}], indirect=True)
@pytest.mark.parametrize("remove", [False, True])
async def test_retained_result_survives_consumed_session_parking_without_new_effects(pending_formal, monkeypatch, remove):
    from src.application.testing.verification_service import VerificationService

    p = pending_formal
    if len(p.outcome.graph_outcome.native_interrupts) == 1:
        _, task = await owned(p, "denied", path="a.py")
        service, caps = continuations(p), result_caps()
        runtime, worker = ports(p, caps=caps, continuation=service)
        t = SimpleNamespace(p=p, a=task, service=service, caps=caps, runtime=runtime, worker=worker)
    else:
        t = await ready(p)
    t.outcome = await t.runtime.execute(t.a, executor=t.worker, resource_id="retained-history-only", fencing_token=3)
    consume_session_parking(p, remove=remove)
    before = facts(p), native_originals(p)

    def forbidden(*args, **kwargs):
        raise AssertionError("Historical result read must not rebuild Verification or execute the graph")

    monkeypatch.setattr(VerificationService, "build_results", forbidden)
    monkeypatch.setattr(p.h.adapter, "execute_frozen", forbidden)
    results = FrameworkRuntimeResults(p.s.h.tasks)
    first, second = await asyncio.gather(results.load_controlled_case(t.a.runtime_owner, **load_options(t)),
        results.freeze_controlled_case(t.a.runtime_owner, runtime_result=t.outcome.runtime_result, **load_options(t)))
    assert first == second == t.outcome.result_asset and (facts(p), native_originals(p)) == before
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        checkpoint = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        assert checkpoint.checkpoint_version == 9 and "projection_receipts" not in checkpoint.checkpoint_payload
    assert (await p.s.h.tasks.get(t.a.id)).status == "running"
    assert (await p.s.d.runs.get_run(p.s.item.run_id)).results == []
    assert len(p.requests) == 2 and len(p.effects) == (len(p.outcome.graph_outcome.native_interrupts) - 1)
    with pytest.raises(TaskDeferred, match="already owned"):
        await t.service.claim(wire(p, t.a.id), worker_id="no-historical-execution", capabilities=t.caps)
    assert (facts(p), native_originals(p)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["task", "attempt", "repark_audit", "progress_audit", "claim", "return", "decision"])
async def test_consumed_marker_does_not_waive_any_historical_predecessor_original(terminal_result, drift):
    from src.runtime.controlled_case_approval_progress import progress_event
    from src.runtime.controlled_case_approval_repark import repark_event, REPARK_KEY
    from src.schemas.controlled_case_approval_repark import ControlledCaseApprovalReparkReceipt

    t, p = terminal_result, terminal_result.p
    consume_session_parking(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        if drift == "task":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload=NULL WHERE id=%s", (t.b.id,))
        elif drift == "attempt":
            cur.execute(f"UPDATE {p.s.h.tasks._attempts} SET finished_at=NULL WHERE id=%s", (t.b.attempt_id,))
        elif drift in {"repark_audit", "progress_audit"}:
            if drift == "repark_audit":
                task = await p.s.h.tasks.get(t.b.id)
                receipt = ControlledCaseApprovalReparkReceipt.model_validate(task.result_payload[REPARK_KEY])
                event = repark_event(receipt, t.b.runtime_binding)
            else:
                event = progress_event(t.parent)
            cur.execute(f"DELETE FROM {p.h.settings.database.postgres_event_table} WHERE id=%s", (event.id,))
        elif drift in {"claim", "return"}:
            cur.execute(f"DELETE FROM {p.h.settings.database.postgres_event_table} WHERE type=%s AND payload->>'task_id'=%s",
                ("agent_task.claimed" if drift == "claim" else "agent_task.completed", t.b.id))
            assert cur.rowcount == 1
        else:
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_decision SET applied_at=NULL WHERE approval_id=%s", (t.b.reference_id,))
    before = facts(p)
    with pytest.raises((ValueError, RuntimeError)):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner, **load_options(t))
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
async def test_rehashed_predecessor_receipt_and_audit_cannot_replace_its_original_worker_return(terminal_result):
    from src.runtime.controlled_case_approval_repark import repark_event, REPARK_KEY
    from src.schemas.controlled_case_approval_repark import ControlledCaseApprovalReparkReceipt
    from src.schemas.framework_approval import approval_value_hash

    t, p = terminal_result, terminal_result.p
    consume_session_parking(p)
    task = await p.s.h.tasks.get(t.b.id)
    raw = deepcopy(task.result_payload[REPARK_KEY])
    raw["task_return_sha256"] = "0" * 64
    raw["receipt_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "receipt_sha256"})
    event = repark_event(ControlledCaseApprovalReparkReceipt.model_validate(raw), t.b.runtime_binding)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload=%s::jsonb WHERE id=%s", (json.dumps({REPARK_KEY: raw}), t.b.id))
        cur.execute(f"UPDATE {p.h.settings.database.postgres_event_table} SET payload=%s::jsonb WHERE id=%s",
            (json.dumps(event.payload), event.id))
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="original Worker/material lineage"):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner, **load_options(t))
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"third_tool": True}], indirect=True)
async def test_consumed_parking_requires_all_serial_predecessors_not_only_the_latest(pending_formal):
    p = pending_formal
    root, b, _, b_asset = await prepared(p, queued=False)
    await repark(p, b)
    a = await submit_a(p, root, "denied")
    service, caps = continuations(p), result_caps()
    a = await service.claim(wire(p, a.id), worker_id="history-middle-a", capabilities=caps)
    runtime, worker = ports(p, caps=caps, continuation=service)
    middle = await runtime.execute(a, executor=worker, resource_id="history-middle-a", fencing_token=3)
    a_asset = await retain(p, a, middle, caps=caps)
    await repark(p, a, caps=replace(caps, controlled_case_approval_repark_available=True))
    c = await submissions(p).submit(wire(p, p.h.task.id), root.state["tool_results"][2]["approval_id"],
        ApprovalDecisionRequest(decision="approved"), capabilities=caps)
    c = await service.claim(wire(p, c.id), worker_id="history-last-c", capabilities=caps)
    outcome = await runtime.execute(c, executor=worker, resource_id="history-last-c", fencing_token=4)
    t = SimpleNamespace(p=p, a=c, service=service, caps=caps, outcome=outcome)
    consume_session_parking(p)
    results, before = FrameworkRuntimeResults(p.s.h.tasks), facts(p)
    assert await results.load_controlled_case(c.runtime_owner, **load_options(t)) == outcome.result_asset
    assert facts(p) == before
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        checkpoint = p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id)
        assert checkpoint.checkpoint_payload["approval_history"]["progress_assets"] == [
            b_asset.model_dump(mode="json"), a_asset.model_dump(mode="json")]
        cur.execute(f"UPDATE {p.s.h.tasks._tasks} SET result_payload=NULL WHERE id=%s", (b.id,))
    before = facts(p)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="completed successor/receipt is missing"):
        await results.load_controlled_case(c.runtime_owner, **load_options(t))
    assert facts(p) == before and len(p.requests) == 2 and [item[0]["path"] for item in p.effects] == ["b.py", "c.py"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_formal", [{"single_tool": True}, {}], indirect=True)
async def test_first_retention_still_requires_the_current_published_parking(pending_formal, monkeypatch):
    p = pending_formal
    if len(p.outcome.graph_outcome.native_interrupts) == 1:
        _, task = await owned(p, "approved", path="a.py")
        service, caps = continuations(p), result_caps()
        runtime, worker = ports(p, caps=caps, continuation=service)
        t = SimpleNamespace(a=task, service=service, caps=caps, runtime=runtime, worker=worker)
    else:
        t = await ready(p)
    captured = []

    async def before_retention(self, owner, **kwargs):
        captured.append((owner, kwargs))
        raise RuntimeError("isolated before retained result")

    with monkeypatch.context() as patch:
        patch.setattr(FrameworkRuntimeResults, "freeze_controlled_case", before_retention)
        with pytest.raises(RuntimeError, match="isolated before retained result"):
            await t.runtime.execute(t.a, executor=t.worker, resource_id="unretained-history", fencing_token=3)
    consume_session_parking(p)
    before = facts(p)
    owner, kwargs = captured[0]
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="current parking differs"):
        await FrameworkRuntimeResults(p.s.h.tasks).freeze_controlled_case(owner, **kwargs)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        assert p.s.d.runs._get_attempt_by_id(cur, p.s.attempt.id).checkpoint_version in {7, 8}
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_consumed_parking_does_not_waive_live_result_read_authority(terminal_result, lease):
    t, p = terminal_result, terminal_result.p
    consume_session_parking(p)
    with postgres_connect(p.h.settings) as conn, conn.cursor() as cur:
        expire_lease(p, cur, lease)
    before = facts(p)
    with pytest.raises(ContinuationLeaseLostError):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner, **load_options(t))
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["session", "owner", "formal"])
async def test_lease_crossing_last_historical_read_is_rejected_and_rolled_back(terminal_result, monkeypatch, lease):
    from src.runtime.controlled_case_approval_repark import ControlledCaseApprovalReparks

    t, p = terminal_result, terminal_result.p
    consume_session_parking(p)
    read = ControlledCaseApprovalReparks._read_receipt

    def cross(self, cur, *args):
        value = read(self, cur, *args)
        expire_lease(p, cur, lease, delay="0.05 seconds")
        cur.execute("SELECT pg_sleep(0.1)")
        return value

    before = facts(p)
    with monkeypatch.context() as patch:
        patch.setattr(ControlledCaseApprovalReparks, "_read_receipt", cross)
        with pytest.raises(ContinuationLeaseLostError):
            await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner, **load_options(t))
    assert facts(p) == before
    assert await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner, **load_options(t)) == t.outcome.result_asset
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["missing", "disabled", "old_codec", "successor_disabled"])
async def test_consumed_parking_cannot_bypass_explicit_ports_and_format_limits(terminal_result, capability):
    t, p = terminal_result, terminal_result.p
    consume_session_parking(p)
    caps = {"missing": None, "disabled": successor_caps(),
        "old_codec": replace(t.caps, controlled_case_checkpoint_max_version=8),
        "successor_disabled": replace(t.caps, controlled_case_approval_successor_available=False)}[capability]
    kwargs, before = load_options(t), facts(p)
    kwargs["capabilities"] = caps
    with pytest.raises(TaskDeferred, match="case_.*unavailable"):
        await FrameworkRuntimeResults(p.s.h.tasks).load_controlled_case(t.a.runtime_owner, **kwargs)
    assert facts(p) == before and len(p.requests) == 2 and len(p.effects) == 1
