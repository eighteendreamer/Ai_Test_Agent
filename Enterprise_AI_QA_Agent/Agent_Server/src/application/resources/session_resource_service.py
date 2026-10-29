from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

from src.runtime.session_resource_store import SessionResourceStore
from src.schemas.session_resource import (
    SessionResourceCleanupPolicy,
    SessionResourceKind,
    SessionResourceRecord,
    SessionResourceStatus,
    session_resource_cleanup_operation_id,
)

BrowserCleanup = Callable[..., Awaitable[None]]
DockerCleanup = Callable[[SessionResourceRecord], Awaitable[None]]
LOGGER = logging.getLogger(__name__)


class SessionResourceService:
    def __init__(
        self,
        store: SessionResourceStore,
        browser_cleanup: BrowserCleanup | None = None,
    ) -> None:
        self._store = store
        self._browser_cleanup = browser_cleanup
        self._docker_cleanup: DockerCleanup | None = None

    async def initialize(self) -> None:
        await self._store.initialize()

    def set_browser_cleanup(self, cleanup: BrowserCleanup) -> None:
        self._browser_cleanup = cleanup

    def set_docker_cleanup(self, cleanup: DockerCleanup) -> None:
        self._docker_cleanup = cleanup

    async def register(
        self,
        *,
        session_id: str,
        kind: SessionResourceKind,
        resource_key: str,
        cleanup_policy: SessionResourceCleanupPolicy = SessionResourceCleanupPolicy.auto,
        metadata: dict[str, Any] | None = None,
    ) -> SessionResourceRecord | None:
        if not session_id or not resource_key:
            return None
        return await self._store.save(
            SessionResourceRecord(
                session_id=session_id,
                kind=kind,
                resource_key=resource_key,
                cleanup_policy=cleanup_policy,
                metadata=metadata or {},
            )
        )

    async def list_active(self, session_id: str) -> list[SessionResourceRecord]:
        return await self._store.list(session_id, active_only=True)

    async def mark_released(
        self,
        *,
        session_id: str,
        kind: SessionResourceKind,
        resource_key: str,
        reason: str = "",
    ) -> SessionResourceRecord | None:
        for resource in await self.list_active(session_id):
            if resource.kind == kind and resource.resource_key == resource_key:
                return await self._store.mark_status(
                    resource.id,
                    SessionResourceStatus.released,
                    {"release_reason": reason},
                )
        return None

    async def build_context(self, session_id: str) -> dict[str, Any]:
        resources = await self.list_active(session_id)
        return {
            "active": [item.model_dump(mode="json") for item in resources],
            "docker_containers": [
                item.resource_key for item in resources if item.kind == SessionResourceKind.docker_container
            ],
            "browser_sessions": [
                item.resource_key for item in resources if item.kind == SessionResourceKind.browser_session
            ],
            "browser_profiles": [
                item.resource_key for item in resources if item.kind == SessionResourceKind.browser_profile
            ],
        }

    async def cleanup_session(self, session_id: str, reason: str = "") -> list[SessionResourceRecord]:
        resources = [
            item
            for item in await self.list_active(session_id)
            if item.cleanup_policy == SessionResourceCleanupPolicy.auto
        ]
        cleaned: list[SessionResourceRecord] = []
        for resource in resources:
            cleaned.append(await self._cleanup_one(resource, reason))
        return cleaned

    async def _cleanup_one(self, resource: SessionResourceRecord, reason: str) -> SessionResourceRecord:
        operation_id = session_resource_cleanup_operation_id(resource.session_id, resource.id, reason)
        journal = {
            "cleanup_operation_id": operation_id,
            "cleanup_state": "unknown",
            "cleanup_reason": reason,
        }
        if (resource.kind == SessionResourceKind.browser_session and resource.metadata.get("allocation_id")
                and self._browser_cleanup is not None):
            # The browser adapter commits a fenced projection. A generic update
            # by row ID here could overwrite a replacement allocation's status.
            current = next((current for current in await self._store.list(resource.session_id)
                            if current.id == resource.id), None)
            if current is None or current.metadata.get("allocation_id") != resource.metadata.get("allocation_id"):
                return current or resource
            started = await self._store.mark_status(current.id, SessionResourceStatus.active, journal)
            resource = started or current.model_copy(update={"metadata": {**current.metadata, **journal}})
            try:
                await self._browser_cleanup(resource.resource_key, allocation_id=resource.metadata["allocation_id"])
            except Exception as exc:
                LOGGER.error("session_browser_cleanup_failed", extra={
                    "session_id": resource.session_id, "allocation_id": resource.metadata["allocation_id"],
                    "error_type": type(exc).__name__, "cleanup_reason": reason,
                })
                return resource.model_copy(update={"status": SessionResourceStatus.error,
                    "metadata": {**resource.metadata, "cleanup_error_type": type(exc).__name__,
                                 "cleanup_state": "unknown"}})
            current = next((current for current in await self._store.list(resource.session_id)
                            if current.id == resource.id), resource)
            if (
                current.metadata.get("allocation_id") == resource.metadata.get("allocation_id")
                and current.status in {SessionResourceStatus.active, SessionResourceStatus.released,
                                       SessionResourceStatus.missing}
            ):
                final_status = (
                    current.status if current.status in {SessionResourceStatus.released, SessionResourceStatus.missing}
                    else SessionResourceStatus.released
                )
                return await self._store.mark_status(current.id, final_status, {
                    "cleanup_operation_id": operation_id,
                    "cleanup_state": "completed",
                    "cleanup_reason": reason,
                }) or current
            return current
        started = await self._store.mark_status(resource.id, SessionResourceStatus.active, journal)
        resource = started or resource.model_copy(update={"metadata": {**resource.metadata, **journal}})
        try:
            if resource.kind == SessionResourceKind.docker_container:
                if resource.metadata.get("tool_key") == "mock-target-runner":
                    if self._docker_cleanup is None:
                        raise RuntimeError("Managed mock target cleanup is not configured")
                    await self._docker_cleanup(resource)
                    status = SessionResourceStatus.released
                else:
                    status = await self._cleanup_docker_container(resource.resource_key)
            elif resource.kind == SessionResourceKind.browser_session and self._browser_cleanup is not None:
                await self._browser_cleanup(resource.resource_key)
                status = SessionResourceStatus.released
            elif resource.kind == SessionResourceKind.browser_profile:
                status = SessionResourceStatus.released
            else:
                status = SessionResourceStatus.released
            return await self._store.mark_status(
                resource.id,
                status,
                {"cleanup_reason": reason,
                 "cleanup_operation_id": operation_id,
                 "cleanup_state": "completed" if status in {
                     SessionResourceStatus.released, SessionResourceStatus.missing,
                 } else "unknown"},
            ) or resource
        except Exception as exc:
            return await self._store.mark_status(
                resource.id,
                SessionResourceStatus.error,
                {"cleanup_reason": reason, "cleanup_error_type": type(exc).__name__,
                 "cleanup_operation_id": operation_id,
                 "cleanup_state": "unknown"},
            ) or resource

    @staticmethod
    async def _cleanup_docker_container(container_name: str) -> SessionResourceStatus:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if proc.returncode == 0:
            return SessionResourceStatus.released
        if "No such container" in (proc.stderr or ""):
            return SessionResourceStatus.missing
        raise RuntimeError(proc.stderr or proc.stdout or f"docker rm failed for {container_name}")
