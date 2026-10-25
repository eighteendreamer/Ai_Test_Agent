from __future__ import annotations

import asyncio

import httpx
import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.session import ApprovalDecisionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, receipt
from tests.test_framework_approval_runtime import runtime_harness, resume


async def progress(h):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        response = await client.get(f"/sessions/{h.session_id}")
    assert response.status_code == 200, response.text
    return {item["approval_id"]: item for item in response.json()["framework_approval_progress"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_progress_distinguishes_admitted_decision_from_native_application(runtime_harness, decision):
    h = runtime_harness
    before = await progress(h)
    assert len(before) == 2 and {item["phase"] for item in before.values()} == {"awaiting_decision"}
    accepted = await h.dispatcher.submit_approval(h.session_id, h.proposals["B"].approval.id,
        ApprovalDecisionRequest(decision=decision))
    submitted = accepted.session.model_dump(mode="json")["framework_approval_progress"]
    b = next(item for item in submitted if item["approval_id"] == h.proposals["B"].approval.id)
    assert b["phase"] == "decision_submitted" and b["decision"] == decision
    assert b["delivery_state"] == "queued" and b["applied_at"] is None
    assert b["tool_status"] == "waiting_approval" and b["task_status"] == "queued"
    assert h.effects == []
    # Each GET reads database facts; no local SSE history or browser state is required.
    assert (await progress(h))[b["approval_id"]]["phase"] == "decision_submitted"
    assert (await progress(h))[h.proposals["A"].approval.id]["phase"] == "awaiting_decision"


@pytest.mark.asyncio
async def test_progress_tracks_running_then_applied_and_retains_closed_turn(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    entered, release = asyncio.Event(), asyncio.Event()
    tools = h.runtime._tool_runtime_service
    handle = tools._handlers["inspect_value"]

    async def hold(arguments, context):
        entered.set()
        await release.wait()
        return await handle(arguments, context)

    tools._handlers["inspect_value"] = hold
    h.sessions._runtime_service = h.runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    running = asyncio.create_task(h.sessions.execute_claimed_approval(task, ownership_monitor=monitor))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        executing = (await progress(h))[task.reference_id]
        assert executing["phase"] == "executing" and executing["tool_status"] == "running"
        release.set()
        await running
        applied = (await progress(h))[task.reference_id]
        assert applied["phase"] == "applied" and applied["applied_at"] is not None
        assert applied["outcome"] == "tool_result" and applied["tool_status"] == "completed"
        await h.tasks.finish(task, status="completed", result_status="waiting_approval")
        a = await claim_decision(h, key="A", decision="denied")
        await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
        closed = await progress(h)
        assert len(closed) == 2 and {item["phase"] for item in closed.values()} == {"applied"}
        assert closed[a.reference_id]["outcome"] == "rejected" and closed[a.reference_id]["tool_status"] == "denied"
    finally:
        release.set()
        if not running.done():
            running.cancel()
        monitor.cancel()
        await asyncio.gather(running, monitor, return_exceptions=True)
    # Public projection must not expose checkpoint resume payloads or owner tokens.
    assert not any(key in item for item in closed.values()
        for key in ("proof", "owner_token", "runtime_owner", "interrupt_id", "arguments"))


@pytest.mark.asyncio
async def test_terminal_tool_without_applied_proof_is_still_applying(runtime_harness, monkeypatch):
    h = runtime_harness
    task = await claim_decision(h)

    async def fail(*args):
        raise ConnectionError("injected application projection outage")

    monkeypatch.setattr(h.runtime._framework_approval_continuation, "settle", fail)
    with pytest.raises(ConnectionError):
        await resume(h, task)
    assert receipt(h).state == "delivered" and receipt(h).applied_at is None
    projected = (await progress(h))[task.reference_id]
    assert projected["phase"] == "applying_result" and projected["tool_status"] == "completed"
    assert projected["applied_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["expired", "missing_receipt", "output"])
async def test_inconsistent_or_expired_execution_is_shown_for_reconciliation(runtime_harness, changed):
    h = runtime_harness
    task = await claim_decision(h)
    if changed == "output":
        await resume(h, task)
    elif changed == "expired":
        await h.jobs.claim_execution(h.proposals["B"].job.id)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if changed == "expired":
            cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table}_runtime SET expires_at=clock_timestamp() "
                "WHERE binding_id=%s", (h.binding.binding_id,))
        elif changed == "missing_receipt":
            cur.execute(f"DELETE FROM {h.settings.database.postgres_agent_task_table}_decision WHERE approval_id=%s",
                (task.reference_id,))
        else:
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET output_payload='{{}}' WHERE id=%s",
                (h.proposals["B"].job.id,))
    projected = (await progress(h))[task.reference_id]
    assert projected["phase"] == "recovery_required"


@pytest.mark.asyncio
async def test_progress_is_limited_to_the_current_turn_and_preserves_dedicated_approvals(runtime_harness):
    h = runtime_harness
    approval = next(item for item in await h.store.list_approvals(h.session_id) if item.id == h.proposals["B"].approval.id)
    approval.metadata["approval_type"] = "ui_recording"
    await h.store.save_approval(h.session_id, approval)
    assert set(await progress(h)) == {h.proposals["A"].approval.id}
    session = await h.store.get_session(h.session_id)
    session.metadata["pending_turn"] = {"turn_id": "a-new-turn"}
    await h.store.save_session(session)
    assert await progress(h) == {}


@pytest.mark.asyncio
async def test_expired_continuation_before_handler_dispatch_requires_reconciliation(runtime_harness):
    h = runtime_harness
    task = await claim_decision(h)
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table}_runtime SET expires_at=clock_timestamp() "
            "WHERE binding_id=%s", (h.binding.binding_id,))
    assert (await progress(h))[task.reference_id]["phase"] == "recovery_required"
