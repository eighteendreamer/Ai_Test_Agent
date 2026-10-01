"""Coalesce model chunks into durable SSE deltas within one owned execution."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

LOGGER = logging.getLogger(__name__)


class SessionAssistantStream:
    """Follow SessionEventSink's writer monitoring and cancellation cleanup.

    Persistence remains the publication boundary. The first chunk is committed
    immediately; subsequent chunks share a bounded time/UTF-8 byte window.
    A single oversized upstream chunk may exceed the byte window unchanged.
    """

    def __init__(
        self, store, session_id: str, turn_id: str, message_id: str, make_event,
        *, fence: dict, interval_seconds: float = 0.04,
        batch_bytes: int = 16384, pending_bytes: int = 65536, capacity: int = 256,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._turn_id = turn_id
        self._message_id = message_id
        self._make_event = make_event
        self._fence = fence
        self._interval = interval_seconds
        self._batch_bytes = batch_bytes
        self._pending_limit = pending_bytes
        self._capacity = capacity
        self._pending: deque[tuple[str, int]] = deque()
        self._pending_bytes = 0
        self._condition = asyncio.Condition()
        self._first_committed = asyncio.Event()
        self._failure: BaseException | None = None
        self._closed = False
        self._running = False

    async def __call__(self, chunk: str) -> None:
        if not chunk:
            return
        size = len(chunk.encode("utf-8"))
        async with self._condition:
            if not self._running or self._closed:
                raise RuntimeError("Assistant stream writer is outside its execution scope")
            await self._condition.wait_for(lambda: (
                self._failure is not None or self._closed or
                (len(self._pending) < self._capacity and self._pending_bytes < self._pending_limit)
            ))
            if self._failure is not None:
                raise self._failure
            if self._closed:
                raise RuntimeError("Assistant stream writer is closed")
            self._pending.append((chunk, size))
            self._pending_bytes += size
            self._condition.notify_all()
        # Preserve first-text latency and surface failure before accepting more.
        await self._first_committed.wait()
        if self._failure is not None:
            raise self._failure

    async def _write(self) -> None:
        try:
            while True:
                async with self._condition:
                    await self._condition.wait_for(lambda: self._pending or self._closed)
                    if not self._pending:
                        return
                    first = not self._first_committed.is_set()
                    deadline = asyncio.get_running_loop().time() + self._interval
                    while (
                        not first and not self._closed and
                        self._pending_bytes < self._batch_bytes and
                        len(self._pending) < self._capacity
                    ):
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            break
                        try:
                            await asyncio.wait_for(self._condition.wait(), remaining)
                        except TimeoutError:
                            break
                    chunks = []
                    size = 0
                    while self._pending and len(chunks) < self._capacity:
                        text, chunk_bytes = self._pending[0]
                        if chunks and size + chunk_bytes > self._batch_bytes:
                            break
                        self._pending.popleft()
                        self._pending_bytes -= chunk_bytes
                        chunks.append(text)
                        size += chunk_bytes
                        if first:
                            break
                    self._condition.notify_all()
                context = {"turn_id": self._turn_id, "message_id": self._message_id}
                if first:
                    await self._store.append_event(
                        self._session_id,
                        self._make_event(self._session_id, "assistant.stream.started", context),
                        **self._fence,
                    )
                await self._store.append_event(
                    self._session_id,
                    self._make_event(self._session_id, "assistant.stream.delta", {
                        **context, "delta": "".join(chunks),
                    }),
                    **self._fence,
                )
                self._first_committed.set()
                LOGGER.debug("assistant_stream_batch_committed", extra={
                    "session_id": self._session_id, "turn_id": self._turn_id,
                    "message_id": self._message_id, "chunk_count": len(chunks), "utf8_bytes": size,
                })
        except BaseException as exc:
            self._failure = exc
            self._first_committed.set()
            async with self._condition:
                self._condition.notify_all()
            LOGGER.exception("assistant_stream_persistence_failed", extra={
                "session_id": self._session_id, "turn_id": self._turn_id,
                "message_id": self._message_id, "pending_chunks": len(self._pending),
                "pending_utf8_bytes": self._pending_bytes,
            })
            raise

    async def run(self, operation, *args, **kwargs):
        if self._running or self._closed:
            raise RuntimeError("Assistant stream writer cannot be reused")
        self._running = True
        writer = asyncio.create_task(self._write())
        execution = asyncio.create_task(operation(*args, **kwargs))
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
                async with self._condition:
                    self._closed = True
                    self._condition.notify_all()
                try:
                    await writer
                finally:
                    self._running = False

            # Same lease-safe repeated-cancel cleanup as SessionEventSink.run.
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
