from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.sessions.session_service import SessionService
from src.registry.modes import ModeRegistry
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.session import (
    ResumeSessionRequest, SendMessageRequest, SessionSnapshot,
    ToolApprovalRequest, ToolApprovalStatus,
)
from tests.test_session_event_delivery import stores


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["normal", "manual", "approval"])
@pytest.mark.parametrize("failure", ["lost", "unavailable", "takeover"])
@pytest.mark.parametrize("cancel_during_cleanup", [False, True])
async def test_session_execution_stops_and_waits_for_cleanup_when_heartbeat_fails(
    stores, monkeypatch, path, failure, cancel_during_cleanup,
):
    store, reader, session_id, _ = stores
    entered, renewal_failed = asyncio.Event(), asyncio.Event()
    cancelling, finish_cleanup = asyncio.Event(), asyncio.Event()
    completions = []
    renew_turn = store.renew_turn_execution
    renew_approval = store.renew_approval_continuation

    class Runtime:
        def request_interrupt(self, *_args):
            # Reproduce a tool awaiting I/O: cooperative flags alone do not stop it.
            pass

        async def execute_turn(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await finish_cleanup.wait()

        resume_turn = execute_turn
        resume_after_approval = execute_turn

    async def fail_renewal(*args, **kwargs):
        await entered.wait()
        if failure == "takeover":
            if path == "approval":
                assert await renew_approval(session_id, "test-approval", "test-owner", 0)
                assert await reader.claim_approval_continuation(session_id, "test-approval", "new-worker", "new-owner", 30)
                assert not await renew_approval(session_id, "test-approval", "test-owner", 30)
            else:
                current = await reader.get_session(session_id)
                token, turn_id = current.metadata["turn_lease_token"], current.metadata["turn_lease_turn_id"]
                assert await renew_turn(session_id, token, 0)
                assert await reader.claim_turn_execution(session_id, turn_id, "new-worker", "new-owner", 30)
                assert not await renew_turn(session_id, token, 30)
        renewal_failed.set()
        if failure == "unavailable":
            raise ConnectionError("injected PostgreSQL heartbeat outage")
        return False

    async def record_completion(*args, **kwargs):
        completions.append(True)
        return True

    monkeypatch.setattr(store, "renew_turn_execution", fail_renewal)
    monkeypatch.setattr(store, "renew_approval_continuation", fail_renewal)
    monkeypatch.setattr(store, "complete_turn_execution", record_completion)
    monkeypatch.setattr(store, "complete_approval_continuation", record_completion)
    modes = ModeRegistry()
    service = SessionService(store, InputOrchestratorService(modes), Runtime(), modes,
        turn_execution_heartbeat_seconds=.1, approval_continuation_heartbeat_seconds=.1)
    session = await store.get_session(session_id)
    if path == "normal":
        operation = service.send_message(session_id, SendMessageRequest(content="Reply hello"))
    elif path == "manual":
        session.metadata["pending_turn"] = {"turn_id": "test-turn"}
        await store.save_session(session)
        await store.save_snapshot(session_id, SessionSnapshot(id="test-snapshot", session_id=session_id,
            version=1, stage="interrupted", created_at=datetime.now(timezone.utc), graph_state={"turn_id": "test-turn"}))
        operation = service.resume_session(session_id, ResumeSessionRequest())
    else:
        approval = ToolApprovalRequest(id="test-approval", session_id=session_id,
            tool_key="knowledge-rag", tool_name="Knowledge RAG", reason="lease test",
            created_at=datetime.now(timezone.utc), status=ToolApprovalStatus.approved)
        await store.save_approval(session_id, approval)
        assert await store.claim_approval_continuation(session_id, approval.id, "test-worker", "test-owner", 30)
        operation = service._resume_after_approval_async(session_id, approval, "test-owner")
    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(renewal_failed.wait(), 3)
        await asyncio.wait_for(cancelling.wait(), .8)
        assert not task.done()
        assert completions == []
        if cancel_during_cleanup:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done()
        finish_cleanup.set()
        result = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3))[0]
        if cancel_during_cleanup:
            assert isinstance(result, asyncio.CancelledError)
        elif path != "approval":
            assert isinstance(result, ContinuationLeaseLostError)
        else:
            assert result is None  # Background approval handler records ownership loss locally.
        assert completions == []
        assert not any(event.type == "turn.completed" for event in await reader.list_events(session_id))
        if failure == "takeover":
            current = ((await reader.list_approvals(session_id))[0] if path == "approval"
                else await reader.get_session(session_id))
            key = "continuation_lease_token" if path == "approval" else "turn_lease_token"
            assert current.metadata[key] == "new-owner"
    finally:
        finish_cleanup.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
