"""Mock target runner using http-echo image for local link verification.

Per design doc section 10.2.3: local mock target, circuit validation.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, asdict
from typing import Any

from src.application.images.image_resolver import ImageResolver
from src.core.config import Settings, get_settings
from src.core.request_context import get_request_context
from src.runtime.docker_resource_manager import DockerCommandTimeout, DockerResourceManager
from src.runtime.docker_resource_cleanup import DockerResourceCleanup
from src.runtime.postgres_resource_allocation_store import PostgresResourceAllocationStore
from src.runtime.resource_lease_manager import ExpiredResourceLease, RedisResourceLeaseManager

LOGGER = logging.getLogger(__name__)


@dataclass
class MockTargetResult:
    ok: bool
    action: str
    container_name: str = ""
    port: int = 0
    response_text: str = ""
    selected_image_key: str = ""
    selected_image: str = ""
    scenario: str = "local_link_verify"
    reason: str = ""
    stdout: str = ""
    stderr: str = ""
    fallback_used: bool = False

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class MockTargetRunner:
    """Start and stop mock HTTP echo targets for link verification."""

    def __init__(self, settings: Settings | None = None, *, docker_resources=None, lease_manager=None) -> None:
        self._settings = settings or get_settings()
        self._resolver = ImageResolver()
        self._lease_manager = lease_manager
        self._docker = docker_resources or DockerResourceManager(self._settings, lease_manager=lease_manager)
        self._tasks: set[asyncio.Task] = set()
        self._by_name: dict[str, asyncio.Task] = {}
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        for name in [name for name, owner in self._by_name.items() if owner is task]:
            self._by_name.pop(name, None)
        if not task.cancelled() and task.exception() is not None:
            LOGGER.error("mock_target_supervisor_failed", extra={"error_type": type(task.exception()).__name__})

    async def start(
        self,
        *,
        port: int = 18080,
        response_text: str = "ok",
    ) -> MockTargetResult:
        if self._closed:
            raise RuntimeError("Mock target runner is closed")
        if not 1 <= port <= 65535:
            raise ValueError("Mock target port must be between 1 and 65535")
        context = get_request_context()
        if context is None or not context.session_id:
            raise ValueError("Mock target requires a session owner")
        resolution = self._resolver.resolve_image(
            domain="helper",
            scenario="local_link_verify",
            helper_need="mock_target",
        )

        if not resolution.ok:
            return MockTargetResult(
                ok=False,
                action="start",
                port=port,
                response_text=response_text,
                reason=f"image_unavailable: {resolution.reason}",
            )

        docker_cmd = [
            "--label", "performance-engine=helper",
            "-p", f"{port}:5678",
            resolution.selected_image,
            "-text", response_text,
        ]

        ready = asyncio.get_running_loop().create_future()

        async def serve(container):
            code, _, stderr = await self._docker._command(["start", container.container_id],
                timeout=self._settings.orchestration.resource_docker_command_timeout_seconds)
            if code != 0:
                raise RuntimeError(f"Mock target start failed: {stderr}")
            LOGGER.info("mock_target_started", extra={"container_id": container.container_id,
                "session_id": context.session_id, "port": port})
            self._by_name[container.container_name] = supervisor
            ready.set_result(container)
            try:
                code, stdout, stderr = await self._docker._command(["wait", container.container_id],
                    timeout=self._settings.orchestration.resource_mock_target_max_lifetime_seconds)
                if code != 0 or stdout.strip() != "0":
                    LOGGER.warning("mock_target_container_exited", extra={
                        "container_id": container.container_id, "exit_code": stdout.strip(),
                        "cli_exit_code": code, "error_message": stderr[:2000]})
            except DockerCommandTimeout:
                LOGGER.info("mock_target_lifetime_expired", extra={"container_id": container.container_id})

        supervisor = asyncio.create_task(self._docker.run(docker_cmd, name_prefix="qa-perf-mock", operation=serve))
        self._tasks.add(supervisor)
        supervisor.add_done_callback(self._finished)
        try:
            await asyncio.wait({ready, supervisor}, return_when=asyncio.FIRST_COMPLETED)
            if supervisor.done():
                await supervisor
                raise RuntimeError("Mock target stopped before startup completed")
            container = ready.result()
        except BaseException:
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
            raise
        return MockTargetResult(
            ok=True,
            action="start",
            container_name=container.container_name,
            port=port,
            response_text=response_text,
            selected_image_key=resolution.selected_image_key,
            selected_image=resolution.selected_image,
            reason=f"mock target started on port {port}",
            stdout=container.container_id,
        )

    async def stop(self, *, container_name: str, session_id: str | None = None) -> MockTargetResult:
        context = get_request_context()
        session_id = session_id or getattr(context, "session_id", None)
        if not session_id or not container_name.startswith("qa-perf-mock-"):
            raise ValueError("Mock target requires its session and managed container name")
        manager = self._lease_manager or RedisResourceLeaseManager(self._settings.database.redis_url,
            socket_timeout_seconds=self._settings.orchestration.redis_task_socket_timeout_seconds,
            cleanup_stream=self._settings.orchestration.cleanup_task_stream)
        try:
            allocations = PostgresResourceAllocationStore(self._settings)
            allocation = await allocations.get_for_session(container_name, session_id=session_id, namespace=manager.namespace)
            if allocation is None:
                raise ValueError("Mock target does not belong to this session")
            lease = ExpiredResourceLease.from_payload({**allocation["lease_record"],
                "external_resource_id": allocation["container_id"]})
            await manager.connect()
            await DockerResourceCleanup(self._settings, allocations=allocations, namespace=manager.namespace)(lease)
            await manager.retire(lease)
            task = self._by_name.get(container_name)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            if self._lease_manager is None:
                await manager.close()
        LOGGER.info("mock_target_stopped", extra={"container_name": container_name, "session_id": session_id})
        return MockTargetResult(
            ok=True,
            action="stop",
            container_name=container_name,
            reason=f"mock target stopped: {container_name}",
        )
