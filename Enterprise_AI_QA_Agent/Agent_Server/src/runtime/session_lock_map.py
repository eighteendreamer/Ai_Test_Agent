"""Per-session asyncio locks that do not retain idle session IDs forever."""

from __future__ import annotations

import asyncio
from weakref import WeakValueDictionary


class SessionLockMap(WeakValueDictionary[str, asyncio.Lock]):
    """Keep a lock while a caller holds it, including while waiting to acquire it."""

    def __getitem__(self, session_id: str) -> asyncio.Lock:
        lock = self.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self[session_id] = lock
        return lock
