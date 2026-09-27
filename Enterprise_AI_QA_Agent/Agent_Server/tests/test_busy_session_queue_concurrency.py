from __future__ import annotations

import asyncio

import pytest

from src.application.orchestration.input_orchestrator_service import (
    InputOrchestratorService,
)
from src.application.sessions.session_service import SessionService
from src.registry.modes import ModeRegistry
from src.schemas.session import SendMessageRequest, SessionStatus
from tests.test_session_event_delivery import stores


@pytest.mark.asyncio
async def test_busy_internal_submissions_keep_both_queued_inputs(stores, monkeypatch) -> None:
    store, reader, session_id, _ = stores
    session = await store.get_session(session_id)
    assert session is not None
    session.status = SessionStatus.running
    await store.save_session(session)

    original_get = store.get_session
    read_count = 0
    both_read = asyncio.Event()

    async def concurrent_get(requested_id):
        nonlocal read_count
        result = await original_get(requested_id)
        read_count += 1
        if read_count <= 2:
            if read_count == 2:
                both_read.set()
            await both_read.wait()
        return result

    monkeypatch.setattr(store, "get_session", concurrent_get)
    modes = ModeRegistry()
    service = SessionService(store, InputOrchestratorService(modes), object(), modes)
    async with service._session_locks[session_id]:
        responses = await asyncio.gather(
            service.send_message(session_id, SendMessageRequest(content="queued-a", submit_mode="queued")),
            service.send_message(session_id, SendMessageRequest(content="queued-b", submit_mode="queued")),
        )
    assert len(responses) == 2
    persisted = await reader.get_session(session_id)
    assert persisted is not None
    payloads = persisted.metadata.get("pending_input_queue", [])
    assert {item["payload"]["content"] for item in payloads} == {"queued-a", "queued-b"}
    assert [event.type for event in await reader.list_events(session_id)] == ["input.queued", "input.queued"]
