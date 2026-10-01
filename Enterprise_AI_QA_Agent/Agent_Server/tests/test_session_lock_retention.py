from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from src.application.orchestration.coordinator_runtime_service import (
    CoordinatorRuntimeService,
)
from src.application.sessions.session_service import SessionService
from src.core.config import Settings
from src.registry.agents import AgentRegistry


@pytest.mark.parametrize("owner_kind", ["session", "coordinator"])
def test_idle_per_session_locks_are_released_without_eviction_races(owner_kind: str) -> None:
    if owner_kind == "session":
        owner = SessionService(None, None, None, None)
        lock_map = owner._session_locks
    else:
        owner = CoordinatorRuntimeService(Settings(), None, None, AgentRegistry())
        lock_map = owner._parent_locks

    held = [lock_map[f"session-{index}"] for index in range(200)]
    refs = [weakref.ref(lock) for lock in held]
    assert len(lock_map) == 200
    assert lock_map["session-0"] is held[0]

    del held
    gc.collect()

    assert all(ref() is None for ref in refs)
    assert len(lock_map) == 0


@pytest.mark.asyncio
async def test_waiting_session_coroutines_keep_the_same_lock() -> None:
    owner = SessionService(None, None, None, None)
    lock = owner._session_locks["shared-session"]
    entered = asyncio.Event()

    async def wait_for_lock() -> None:
        async with owner._session_locks["shared-session"]:
            entered.set()

    async with lock:
        waiter = asyncio.create_task(wait_for_lock())
        await asyncio.sleep(0)
        assert not entered.is_set()
        assert owner._session_locks["shared-session"] is lock

    await asyncio.wait_for(waiter, 1)
    assert entered.is_set()
