"""Persist graph events during execution, before exposing them to SSE readers."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from copy import deepcopy

from src.schemas.runtime_binding import RuntimeExecutionOwner

LOGGER = logging.getLogger(__name__)


class SessionEventSink:
    """Legacy bounded queue plus acknowledged owner-bound event writes."""

    def __init__(self, store, session_id: str, *, capacity: int, fence: dict,
                 runtime_owner: RuntimeExecutionOwner | None = None) -> None:
        if runtime_owner is not None and runtime_owner.binding.session_id != session_id:
            raise ValueError("Session event owner belongs to another Session")
        self._store, self._session_id = store, session_id
        self._capacity, self._fence = capacity, fence
        self._runtime_owner = runtime_owner
        self._write_lock = asyncio.Lock()
        self._pending = deque()
        self._ready = asyncio.Event()
        self._closed = False
        self._runtime_events_completed = False
        self._deferred_to_final_flush = False
        self._checkpoint_preparation = None
        self._acknowledged_events = []

    def bind_checkpoint_preparation(self, preparation) -> None:
        """Bind the already persisted input; this is not a completion declaration."""
        from src.schemas.framework_graph_input import FrameworkPreparedGraphInputAsset, parse_graph_input_asset

        preparation = parse_graph_input_asset(preparation)
        if (self.runtime_owner is None or not isinstance(preparation, FrameworkPreparedGraphInputAsset)
            or preparation.runtime_preparation.identity.binding != self.runtime_owner.binding
            or preparation.feature_flags.get("terminal_event_projection_version") != 1):
            raise ValueError("Checkpoint event prefix requires its original prepared runtime owner")
        if self._checkpoint_preparation is not None:
            raise ValueError("Checkpoint event preparation is already bound")
        self._checkpoint_preparation = deepcopy(preparation)
        LOGGER.debug("framework_checkpoint_event_preparation_bound", extra={
            "session_id": self._session_id, "binding_id": self.runtime_owner.binding.binding_id,
            "task_id": self.runtime_owner.task_id, "attempt_id": self.runtime_owner.attempt_id})

    def checkpoint_event_prefix(self):
        # Called synchronously by the captured saver, including its DB thread.
        # Entries are independent snapshots appended only after the SQL ACK;
        # no awaiting, queue flush or new persistence happens in this accessor.
        if self._checkpoint_preparation is None:
            return None
        return deepcopy((self._checkpoint_preparation, list(self._acknowledged_events)))

    @property
    def runtime_owner(self) -> RuntimeExecutionOwner | None:
        return self._runtime_owner

    @property
    def deferred_to_final_flush(self) -> bool:
        return self._deferred_to_final_flush

    def put_nowait(self, event) -> None:
        if self.runtime_owner is not None:
            raise RuntimeError("Owner-bound graph events require awaited persistence")
        if self._closed:
            raise RuntimeError("Session event writer is closed")
        if event.session_id != self._session_id:
            raise ValueError("Session event cannot cross its execution scope")
        if self._deferred_to_final_flush:
            raise asyncio.QueueFull
        if len(self._pending) >= self._capacity:
            # The runtime result carries the complete event log. Keep the live
            # writer's prefix ordered; finalization will append the suffix.
            self._deferred_to_final_flush = True
            LOGGER.warning(
                "session_event_live_flush_deferred",
                extra={"session_id": self._session_id, "capacity": self._capacity, "event_id": event.id},
            )
            raise asyncio.QueueFull
        self._pending.append(event)
        self._ready.set()

    async def persist(self, event) -> None:
        """Serialize and settle a write before cancellation releases the lease.

        Like run() cleanup, repeated cancellation waits for the in-flight
        to_thread transaction; cancelling its await cannot cancel PostgreSQL.
        """
        if self.runtime_owner is None:
            raise ValueError("Acknowledged graph events require a captured runtime owner")
        if self._closed:
            raise RuntimeError("Session event writer is closed")
        if event.session_id != self._session_id:
            raise ValueError("Session event cannot cross its execution scope")

        async def write():
            async with self._write_lock:
                if self._runtime_events_completed:
                    raise RuntimeError("Runtime graph event collection is already completed")
                try:
                    await self._store.append_event(self._session_id, event,
                        **self._fence, runtime_owner=self.runtime_owner)
                    self._acknowledged_events.append(deepcopy({"id": event.id, "timestamp": event.timestamp.isoformat(),
                        "type": event.type, "payload": event.payload}))
                except Exception:
                    LOGGER.exception("session_event_persistence_failed", extra={
                        "session_id": self._session_id, "event_id": event.id,
                        "binding_id": self.runtime_owner.binding.binding_id})
                    raise

        await self._settle_write(write())

    async def complete_runtime_events(self, event_log, native_terminal, *, returned_event_offset=0):
        """A completion ACK is separate from individual event ACKs and finalization.

        Only this captured producer may close its graph collection. Other
        Session event producers/finalizers continue on their existing ports.
        """
        if self.runtime_owner is None:
            raise ValueError("Runtime event completion requires a captured runtime owner")
        if self._closed:
            raise RuntimeError("Session event writer is closed")

        async def write():
            async with self._write_lock:
                try:
                    event = await self._store.complete_runtime_events(self._session_id, event_log,
                        runtime_owner=self.runtime_owner, native_terminal=native_terminal,
                        returned_event_offset=returned_event_offset, **self._fence)
                    self._runtime_events_completed = True
                    LOGGER.info("runtime_graph_events_completed", extra={"session_id": self._session_id,
                        "binding_id": self.runtime_owner.binding.binding_id, "event_id": event.id,
                        "event_count": len(event_log), "returned_event_offset": returned_event_offset})
                    return event
                except Exception:
                    LOGGER.exception("runtime_graph_event_completion_failed", extra={"session_id": self._session_id,
                        "binding_id": self.runtime_owner.binding.binding_id})
                    raise

        return await self._settle_write(write())

    @staticmethod
    async def _settle_write(operation):
        pending = asyncio.create_task(operation)
        cancelled = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
        result = await pending
        if cancelled:
            raise asyncio.CancelledError
        return result

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
