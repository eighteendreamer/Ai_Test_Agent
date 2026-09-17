"""Docker execution under a renewable Harness lease.

Docker CLI create/start separate allocation from execution. See the installed
`docker container create --help` / `start --help` contracts. Lease retirement
uses the same cleanup queue as expiry recovery; model output is never ownership.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar
from uuid import uuid4

from src.core.request_context import get_request_context
from src.runtime.resource_lease_manager import RedisResourceLeaseManager, ResourceUnavailable
from src.runtime.postgres_resource_allocation_store import PostgresResourceAllocationStore

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class DockerContainer:
    container_id: str
    container_name: str
    allocation_id: str


@dataclass(frozen=True)
class DockerExecutionResult:
    container_id: str
    container_name: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class DockerCommandTimeout(TimeoutError):
    def __init__(self, stdout: str, stderr: str) -> None:
        super().__init__("Docker command timed out")
        self.stdout = stdout
        self.stderr = stderr


class DockerResourceManager:
    """Own one container per lease, including auto-selected Docker backends."""

    def __init__(self, settings, *, lease_manager=None, allocation_store=None) -> None:
        self._settings = settings
        self._lease_manager = lease_manager
        self._allocation_store = allocation_store

    async def execute(self, create_args: list[str], *, name_prefix: str,
                      timeout_seconds: float) -> DockerExecutionResult:
        async def attached(container: DockerContainer) -> DockerExecutionResult:
            try:
                code, stdout, stderr = await self._command(
                    ["start", "--attach", container.container_id], timeout=timeout_seconds)
                return DockerExecutionResult(container.container_id, container.container_name, code, stdout, stderr)
            except DockerCommandTimeout as exc:
                LOGGER.warning("docker_resource_execution_timeout", extra={"container_id": container.container_id})
                return DockerExecutionResult(container.container_id, container.container_name, -1,
                                             exc.stdout, exc.stderr or "timeout", timed_out=True)

        return await self.run(create_args, name_prefix=name_prefix, operation=attached)

    async def run(self, create_args: list[str], *, name_prefix: str,
                  operation: Callable[[DockerContainer], Awaitable[T]]) -> T:
        """Run an operation only while its container lease remains valid.

        Attached commands and multi-call sessions share allocation, fencing,
        heartbeat cancellation and retirement, rather than duplicating them.
        """
        config = self._settings.orchestration
        manager = self._lease_manager or RedisResourceLeaseManager(
            self._settings.database.redis_url,
            socket_timeout_seconds=config.redis_task_socket_timeout_seconds,
            cleanup_stream=config.cleanup_task_stream,
        )
        context = get_request_context()
        allocation_id = uuid4().hex
        fields = {
            name: getattr(context, name, None)
            for name in ("project_id", "session_id", "run_id", "run_item_id", "attempt_id")
        }
        parts = [name_prefix, *(fields[name] for name in ("run_id", "run_item_id", "attempt_id"))]
        name = "-".join(re.sub(r"[^A-Za-z0-9_.-]", "-", part)[:24] for part in parts if part)
        name = f"{name[:90]}-{allocation_id[:12]}"
        lease = None
        container_id = ""
        creation_requested = False
        operation_task = heartbeat = None
        command_timeout = config.resource_docker_command_timeout_seconds
        try:
            try:
                await manager.connect()
                lease = await manager.acquire_first_available(
                    resource_type="docker",
                    resource_ids=[f"docker-slot-{i}" for i in range(config.resource_docker_slots)],
                    worker_id=getattr(context, "worker_id", None) or f"docker-{os.getpid()}-{allocation_id[:12]}",
                    lease_seconds=config.resource_default_lease_seconds,
                    **fields,
                )
            except ResourceUnavailable:
                raise
            except Exception as exc:
                LOGGER.warning("docker_resource_harness_unavailable", extra={"error_type": type(exc).__name__})
                raise ResourceUnavailable("resource_harness_unavailable") from exc

            async def run():
                nonlocal container_id, creation_requested
                allocations = self._allocation_store or PostgresResourceAllocationStore(self._settings)
                await allocations.prepare(allocation_id, lease, namespace=manager.namespace, container_name=name)
                if not await manager.prepare_allocation(lease, allocation_id):
                    raise RuntimeError("Docker lease ownership lost before allocation")
                LOGGER.info("docker_allocation_prepared", extra={
                    **fields, "allocation_id": allocation_id, "resource_id": lease.resource_id,
                    "worker_id": lease.worker_id,
                })
                labels = {"enterprise-ai-qa-agent": "managed", "qa.allocation_id": allocation_id,
                          "qa.lease_namespace": manager.namespace,
                          "qa.resource_id": lease.resource_id, "qa.fencing_token": str(lease.fencing_token)}
                labels.update({f"qa.{key}": value for key, value in fields.items() if value})
                args = ["create", "--name", name]
                for key, value in labels.items():
                    args.extend(["--label", f"{key}={value}"])
                creation_requested = True
                created = await self._command([*args, *create_args], timeout=command_timeout)
                if created[0] != 0:
                    raise RuntimeError(f"Docker create failed (exit_code={created[0]}): {created[2]}")
                container_id = created[1].strip()
                if not re.fullmatch(r"[a-f0-9]{64}", container_id):
                    container_id = ""
                    raise RuntimeError("Docker create did not return an immutable full container ID")
                await allocations.bind(allocation_id, container_id)
                if not await manager.bind(lease, container_id):
                    raise RuntimeError("Docker lease ownership lost before container start")
                LOGGER.info("docker_resource_execution_started", extra={
                    **fields, "resource_id": lease.resource_id, "container_id": container_id,
                    "worker_id": lease.worker_id, "fencing_token": lease.fencing_token,
                })
                return await operation(DockerContainer(container_id, name, allocation_id))

            operation_task = asyncio.create_task(run())
            heartbeat = asyncio.create_task(self._heartbeat(manager, lease))
            await asyncio.wait({operation_task, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat.done():
                await heartbeat  # A lost lease stops the external execution, never reports success.
            return await operation_task
        finally:
            for task in (operation_task, heartbeat):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (operation_task, heartbeat) if task is not None),
                                 return_exceptions=True)
            if lease is not None:
                try:
                    if creation_requested and not container_id:
                        # Creation can finish before the CLI response is delivered. Only
                        # this allocation's UUID label may resolve an unknown result.
                        code, stdout, stderr = await self._command(
                            ["ps", "--all", "--no-trunc", "--filter", f"label=qa.allocation_id={allocation_id}",
                             "--format", "{{.ID}}"], timeout=command_timeout)
                        if code != 0:
                            raise RuntimeError(f"Docker allocation lookup failed: {stderr}")
                        ids = stdout.split()
                        if len(ids) == 1 and re.fullmatch(r"[a-f0-9]{64}", ids[0]):
                            container_id = ids[0]
                            if not await manager.bind(lease, container_id):
                                LOGGER.warning("docker_late_binding_rejected", extra={"resource_id": lease.resource_id})
                    if container_id:
                        code, _, stderr = await self._command(["rm", "--force", container_id], timeout=command_timeout)
                        if code != 0 and "No such container" not in stderr:
                            raise RuntimeError(f"Docker cleanup failed (exit_code={code}): {stderr}")
                except Exception as exc:
                    LOGGER.error("docker_resource_cleanup_deferred", extra={
                        "resource_id": lease.resource_id, "container_id": container_id,
                        "error_type": type(exc).__name__, "error_message": str(exc),
                    })
                try:
                    if not await manager.retire(lease):
                        LOGGER.warning("docker_resource_retire_rejected", extra={"resource_id": lease.resource_id})
                except Exception as exc:
                    LOGGER.error("docker_resource_retire_deferred_to_expiry", extra={
                        "resource_id": lease.resource_id, "error_type": type(exc).__name__,
                    })
            if self._lease_manager is None:
                await manager.close()

    async def _heartbeat(self, manager, lease) -> None:
        seconds = self._settings.orchestration.resource_default_lease_seconds
        while True:
            await asyncio.sleep(seconds / 3)
            try:
                if not await manager.renew(lease, lease_seconds=seconds):
                    raise RuntimeError("Docker resource lease heartbeat rejected")
            except Exception as exc:
                LOGGER.error("docker_resource_heartbeat_failed", extra={
                    "resource_id": lease.resource_id, "error_type": type(exc).__name__,
                })
                raise

    async def _command(self, arguments: list[str], *, timeout: float) -> tuple[int, str, str]:
        """Own the CLI process so cancellation cannot leave an attached client running."""
        # Uvicorn's Windows subprocess/reload mode uses SelectorEventLoop, which
        # cannot run asyncio subprocesses. Retain a Popen handle across to_thread
        # so both Selector and Proactor loops can cancel the real CLI process.
        spawn = asyncio.create_task(asyncio.to_thread(
            subprocess.Popen, ["docker", *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ))
        try:
            proc = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            proc = await spawn
            if proc.poll() is None:
                proc.kill()
            await asyncio.to_thread(proc.communicate)
            raise
        communication = asyncio.create_task(asyncio.to_thread(proc.communicate))
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if proc.poll() is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass  # Process exited between returncode inspection and kill.
            stdout, stderr = await communication
            if isinstance(exc, TimeoutError):
                raise DockerCommandTimeout(stdout.decode("utf-8", errors="replace"),
                                           stderr.decode("utf-8", errors="replace")) from exc
            raise
        return proc.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")
