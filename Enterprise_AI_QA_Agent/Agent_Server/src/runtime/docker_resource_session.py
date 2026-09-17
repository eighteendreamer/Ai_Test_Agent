"""Multi-call Docker lifetime built on the existing renewable resource manager.

The supervisor follows MockTargetRunner's start/wait pattern. Commands belong
to that lifetime: losing its lease cancels active CLI clients before retirement.
"""
from __future__ import annotations

import asyncio
import logging

from src.runtime.docker_resource_manager import DockerCommandTimeout, DockerResourceManager

LOGGER = logging.getLogger(__name__)


class DockerResourceSession:
    def __init__(self, manager: DockerResourceManager, *, command_timeout: float, on_closed=None) -> None:
        self._manager = manager
        self._command_timeout = command_timeout
        self._on_closed = on_closed
        self.container = None
        self._task: asyncio.Task | None = None
        self._operations: set[asyncio.Task] = set()
        self._stopping = False
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done() and not self._stopping

    async def start(self, create_args: list[str], *, name_prefix: str, lifetime_seconds: float) -> None:
        if self._task is not None:
            raise RuntimeError("Docker resource session has already been started")
        ready = asyncio.get_running_loop().create_future()

        async def serve(container):
            self.container = container
            code, _, stderr = await self._manager._command(["start", container.container_id],
                timeout=self._command_timeout)
            if code != 0:
                raise RuntimeError(f"Docker session start failed: {stderr}")
            ready.set_result(None)
            try:
                code, stdout, stderr = await self._manager._command(["wait", container.container_id],
                    timeout=lifetime_seconds)
                LOGGER.info("docker_session_container_exited", extra={"container_id": container.container_id,
                    "cli_exit_code": code, "exit_code": stdout.strip(), "error_message": stderr[:2000]})
            except DockerCommandTimeout:
                LOGGER.info("docker_session_lifetime_expired", extra={"container_id": container.container_id})
            finally:
                self._stopping = True
                operations = list(self._operations)
                for operation in operations:
                    operation.cancel()
                await asyncio.gather(*operations, return_exceptions=True)

        self._task = asyncio.create_task(self._manager.run(create_args, name_prefix=name_prefix, operation=serve))
        self._task.add_done_callback(self._finished)
        try:
            await asyncio.wait({ready, self._task}, return_when=asyncio.FIRST_COMPLETED)
            if self._task.done():
                await self._task
                raise RuntimeError("Docker session ended during startup")
            await ready
        except BaseException:
            await self.close()
            raise

    def _finished(self, task):
        self._stopping = True
        if self._on_closed is not None:
            self._on_closed(self)
        if not task.cancelled() and task.exception() is not None:
            LOGGER.error("docker_session_supervisor_failed", extra={"error_type": type(task.exception()).__name__,
                "container_id": self.container.container_id if self.container else ""})

    async def command(self, arguments: list[str], *, timeout: float) -> tuple[int, str, str]:
        """Serialize operations on a stateful container and bind them to its lease."""
        async with self._lock:
            if not self.active:
                raise RuntimeError("Docker resource session is no longer active")
            operation = asyncio.create_task(self._manager._command(arguments, timeout=timeout))
            self._operations.add(operation)
            try:
                await asyncio.wait({operation, self._task}, return_when=asyncio.FIRST_COMPLETED)
                if not self.active:
                    raise RuntimeError("Docker resource session ended during command execution")
                return await operation
            except (DockerCommandTimeout, asyncio.CancelledError):
                # Killing docker exec alone does not kill the process inside a
                # persistent container. Retire the whole session on interruption.
                await self.close()
                raise
            finally:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                self._operations.discard(operation)

    async def close(self) -> None:
        # serve() sets _stopping before the manager's cleanup begins. A second
        # cancellation here would interrupt that cleanup and its queue handoff.
        if self._task is not None and not self._task.done() and not self._stopping:
            self._task.cancel()
        self._stopping = True
        if self._task is not None:
            await asyncio.shield(asyncio.gather(self._task, return_exceptions=True))
