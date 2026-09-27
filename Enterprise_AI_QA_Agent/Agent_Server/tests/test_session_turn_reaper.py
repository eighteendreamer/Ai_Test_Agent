from __future__ import annotations

import asyncio

import pytest

from src.application.sessions.session_service import SessionService
from src.schemas.session import SessionStatus
from tests.test_session_event_delivery import stores


@pytest.mark.asyncio
async def test_expired_turn_recovers_while_service_stays_running(stores) -> None:
    store, reader, session_id, _ = stores
    assert await store.claim_turn_execution(session_id, "abandoned-turn", "worker-a", "old-token", 0)
    assert await reader.claim_turn_execution(session_id, "new-turn", "worker-b", "new-token", 30) is None

    service = SessionService(store, object(), object(), object(), turn_reaper_interval_seconds=0.05)
    await service.start_turn_reaper()
    try:
        async def recovered() -> bool:
            current = await reader.get_session(session_id)
            events = await reader.list_events(session_id)
            return (
                current is not None
                and current.status == SessionStatus.interrupted
                and any(event.type == "turn.recovery_detected" for event in events)
            )

        async with asyncio.timeout(5):
            while not await recovered():
                await asyncio.sleep(0.02)
        current = await reader.get_session(session_id)
        assert current is not None
        assert "turn_lease_token" not in current.metadata
        events = await reader.list_events(session_id)
        assert [event.type for event in events] == ["turn.recovery_detected"]
        assert events[0].payload["recovery_source"] == "periodic_reaper"
        assert await reader.claim_turn_execution(session_id, "new-turn", "worker-b", "new-token", 30)
    finally:
        await service.stop_turn_reaper()
