"""Persist graph events during execution, before exposing them to SSE readers."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

LOGGER = logging.getLogger(__name__)


class SessionEventSink:
    """Bounded producer adapter for append_graph_event's synchronous interface."""

    def __init__(self, store, session_id: str, *, capacity: int, fence: dict) -> None:
        self._store, self._session_id = store, session_id
        self._capacity, self._fence = capacity, fence
        self._pending = deque()
        self._ready = asyncio.Event()
        self._closed = False

    def put_nowait(self, event) -> None:
        if self._closed:
            raise RuntimeError("Session event writer is closed")
        if event.session_id != self._session_id:
            raise ValueError("Session event cannot cross its execution scope")
        if len(self._pending) >= self._capacity:
            raise asyncio.QueueFull
        self._pending.append(event)
        self._ready.set()

    async def _write(self) -> None:
        try:
            while True:
                await self._ready.wait()
                self._ready.clear()
                while self._pending:
                    event = self._pending[0]
                    await self._store.append_event(self._session_id, event, **self._fence)
                    self._pending.popleft()
                if self._closed:
                    return
        except Exception:
            LOGGER.exception("session_event_persistence_failed", extra={"session_id": self._session_id})
            raise

    async def run(self, operation, *args, **kwargs):
        writer = asyncio.create_task(self._write())
        execution = asyncio.create_task(operation(*args, event_queue=self, **kwargs))
        try:
            done, _ = await asyncio.wait({writer, execution}, return_when=asyncio.FIRST_COMPLETED)
            if writer in done:
                await writer
            return await execution
        finally:
            if not execution.done():
                execution.cancel()

            async def settle():
                await asyncio.gather(execution, return_exceptions=True)
                self._closed = True
                self._ready.set()
                await writer

            # A second cancel must not leave a writer using a lease which its
            # caller has already completed. Follow the execution lease cleanup.
            cleanup = asyncio.create_task(settle())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            await cleanup
            if cancelled:
                raise asyncio.CancelledError
