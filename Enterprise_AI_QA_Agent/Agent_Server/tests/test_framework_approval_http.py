from __future__ import annotations

import asyncio

import httpx
import pytest

from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import QueuedTask, RedisTaskQueue
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.redis_task_worker import RedisTaskWorker
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import AgentTaskAccepted, parse_agent_task_command
from src.schemas.session import ApprovalDecisionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_continuation import continuation_harness, claim_decision, receipt
from tests.test_framework_approval_runtime import runtime_harness


def url(h, key="B"):
    return f"/sessions/{h.session_id}/approvals/{h.proposals[key].approval.id}"


@pytest.mark.asyncio
async def test_dispatcher_submits_original_binding_to_v2_outbox(runtime_harness):
    h = runtime_harness
    accepted = await h.dispatcher.submit_approval(h.session_id, h.proposals["B"].approval.id,
        ApprovalDecisionRequest(decision="approved"))
    assert isinstance(accepted, AgentTaskAccepted) and accepted.task.runtime_binding == h.binding
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"SELECT stream,payload FROM {h.settings.database.postgres_task_outbox_table} WHERE event_key=%s",
            (f"agent_turn:{accepted.task.id}",))
        row = cur.fetchone()
    assert row["stream"] == h.settings.orchestration.agent_task_v2_stream
    command = parse_agent_task_command(row["payload"])
    assert command.schema_version == 2 and command.runtime_binding == h.binding
    assert command.reference_id == h.proposals["B"].approval.id and h.effects == []
    assert receipt(h).state == "queued" and receipt(h).applied_at is None


@pytest.mark.asyncio
async def test_http_202_is_idempotent_and_conflicting_decision_is_409(runtime_harness):
    h = runtime_harness
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        first = await client.post(url(h), json={"decision": "approved", "reason": "original", "client_request_id": "one"})
        assert first.status_code == 202, first.text
        again = await client.post(url(h), json={"decision": "approved", "reason": "changed", "client_request_id": "two"})
        assert again.status_code == 202, again.text
        assert first.json()["task"]["id"] == again.json()["task"]["id"]
        conflict = await client.post(url(h), json={"decision": "denied"})
    assert conflict.status_code == 409, conflict.text
    assert receipt(h).state == "queued" and receipt(h).applied_at is None and h.effects == []
    assert (await h.tasks.get(first.json()["task"]["id"])).input["approval"]["decision_note"] == "original"


@pytest.mark.asyncio
async def test_http_accepts_sibling_decision_while_session_is_executing(runtime_harness):
    h = runtime_harness
    b = await claim_decision(h)
    h.sessions._runtime_service = h.runtime
    tools = h.runtime._tool_runtime_service
    handle = tools._handlers["inspect_value"]
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(arguments, context):
        entered.set()
        await release.wait()
        return await handle(arguments, context)

    tools._handlers["inspect_value"] = hold
    monitor = asyncio.create_task(asyncio.Event().wait())
    running = asyncio.create_task(h.sessions.execute_claimed_approval(b, ownership_monitor=monitor))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        before = await h.store.get_session(h.session_id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.post(url(h, "A"), json={"decision": "denied", "reason": "reject A"}), 2)
        assert response.status_code == 202, response.text
        a = await h.tasks.get(response.json()["task"]["id"])
        assert a.status == "queued" and a.runtime_binding == h.binding
        assert receipt(h, "A").decision == {"type": "reject", "message": "reject A"}
        after = await h.store.get_session(h.session_id)
        assert before.metadata["turn_lease_token"] == after.metadata["turn_lease_token"]
        with pytest.raises(TaskDeferred):
            await h.tasks.claim(a.id, worker_id="cannot-overlap", expected_binding=h.binding)
        release.set()
        partial = await asyncio.wait_for(running, 4)
        assert partial.session.status.value == "waiting_approval"
        # Finalizing B must retain A's already-submitted decision and original identity.
        saved = await h.store.get_session(h.session_id)
        assert saved.metadata["pending_turn"]["pending_approval_ids"] == [h.proposals["A"].approval.id]
        a_approval = next(item for item in await h.store.list_approvals(h.session_id) if item.id == a.reference_id)
        assert a_approval.status.value == "denied"
    finally:
        release.set()
        if not running.done():
            running.cancel()
        monitor.cancel()
        await asyncio.gather(running, monitor, return_exceptions=True)
    assert h.effects == [("B", h.proposals["B"].job.id)]


@pytest.mark.asyncio
async def test_http_replays_original_task_after_native_turn_closes(runtime_harness):
    h = runtime_harness
    h.sessions._runtime_service = h.runtime
    monitor = asyncio.create_task(asyncio.Event().wait())
    try:
        b = await claim_decision(h)
        await h.sessions.execute_claimed_approval(b, ownership_monitor=monitor)
        await h.tasks.finish(b, status="completed", result_status="waiting_approval")
        a = await claim_decision(h, key="A", decision="denied")
        await h.sessions.execute_claimed_approval(a, ownership_monitor=monitor)
        await h.tasks.finish(a, status="completed", result_status="completed")
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        result = await client.post(url(h), json={"decision": "approved", "reason": "late repeat"})
    assert result.status_code == 202, result.text
    assert result.json()["task"]["id"] == b.id and result.json()["task"]["status"] == "completed"
    assert h.effects == [("B", h.proposals["B"].job.id)]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["link", "binding"])
async def test_http_missing_canonical_identity_cannot_fall_back_to_legacy(runtime_harness, changed):
    h = runtime_harness
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if changed == "link":
            cur.execute(f"DELETE FROM {h.settings.database.postgres_agent_task_table}_interrupt WHERE approval_id=%s",
                (h.proposals["B"].approval.id,))
        else:
            cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table}_runtime SET runtime_binding="
                "jsonb_set(runtime_binding,'{runtime_version}','\"changed\"') WHERE binding_id=%s", (h.binding.binding_id,))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        response = await client.post(url(h), json={"decision": "approved"})
    assert response.status_code == (409 if changed == "link" else 400), response.text
    assert receipt(h) is None and h.effects == []
    saved = next(item for item in await h.store.list_approvals(h.session_id) if item.id == h.proposals["B"].approval.id)
    assert saved.status.value == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("b_decision", ["approved", "denied"])
async def test_http_outbox_redis_worker_runs_each_decision_once(runtime_harness, b_decision):
    h = runtime_harness
    h.settings.orchestration.agent_task_v2_stream = f"{h.prefix}:agent:v2"
    h.sessions._runtime_service = h.runtime
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities(h.binding.dependency_lock_sha256, True))
    queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
        group="approval-v2", client=h.redis)
    await queue.connect()
    errors, deliveries = [], []

    async def handle(delivery):
        deliveries.append(delivery)
        try:
            await h.dispatcher.handle_v2(delivery, worker_id="real-approval-worker")
        except Exception as exc:
            errors.append(exc)
            raise

    worker = RedisTaskWorker(queue, consumer="real-approval-worker", handler=handle, block_ms=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        b_response = await client.post(url(h), json={"decision": b_decision})
        assert b_response.status_code == 202, b_response.text
        assert await h.outbox.relay_once(queue) == 1
        assert await worker.run_once() == 1
        if errors:
            raise errors[0]
        b = await h.tasks.get(b_response.json()["task"]["id"])
        assert b.status == "completed" and b.result_status == "waiting_approval"
        assert receipt(h).applied_at is not None and receipt(h, "A") is None
        a_response = await client.post(url(h, "A"), json={"decision": "approved"})
        assert a_response.status_code == 202, a_response.text
        assert await h.outbox.relay_once(queue) == 1
        assert await worker.run_once() == 1
        if errors:
            raise errors[0]
        a = await h.tasks.get(a_response.json()["task"]["id"])
    assert a.status == "completed" and a.result_status == "completed"
    assert receipt(h, "A").applied_at is not None
    assert h.effects == ([("B", h.proposals["B"].job.id)] if b_decision == "approved" else []) + [
        ("A", h.proposals["A"].job.id)]
    effects = list(h.effects)
    for delivery in deliveries:
        await h.dispatcher.handle_v2(QueuedTask("duplicate", delivery.payload), worker_id="duplicate-worker")
    assert h.effects == effects and not await queue.pending()
    assert (await h.tasks.get(b.id)).attempt_no == (await h.tasks.get(a.id)).attempt_no == 1
    events = await h.store.list_events(h.session_id)
    assert len([event for event in events if event.type == "approval.decision_applied"]) == 2
    assert len([event for event in events if event.type == "approval.decision_delivered"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["test_run_case_execution", "test_run_agent_execution"])
async def test_http_bound_test_run_approval_keeps_its_dedicated_endpoint(runtime_harness, source):
    h = runtime_harness
    approval = next(item for item in await h.store.list_approvals(h.session_id)
        if item.id == h.proposals["B"].approval.id)
    approval.metadata["source"] = source
    await h.store.save_approval(h.session_id, approval)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        response = await client.post(url(h), json={"decision": "approved"})
    assert response.status_code == 400 and "test run item" in response.json()["detail"]
    assert receipt(h) is None and h.effects == []


@pytest.mark.asyncio
async def test_http_recording_approval_does_not_enter_native_continuation(runtime_harness):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    h = runtime_harness
    approval = next(item for item in await h.store.list_approvals(h.session_id)
        if item.id == h.proposals["B"].approval.id)
    approval.metadata["approval_type"] = "ui_recording"
    await h.store.save_approval(h.session_id, approval)
    apply = AsyncMock()
    h.sessions.set_recording_approval_service(SimpleNamespace(apply_decision=apply))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as client:
        response = await client.post(url(h), json={"decision": "approved"})
    assert response.status_code == 200, response.text
    apply.assert_awaited_once()
    assert receipt(h) is None and h.effects == []
