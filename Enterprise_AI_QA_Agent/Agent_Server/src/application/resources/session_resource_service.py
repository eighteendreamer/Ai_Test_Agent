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
    SessionResourceCleanupReconciliation,
    SessionResourceRecord,
    SessionResourceStatus,
    session_resource_cleanup_operation_id,
)

BrowserCleanup = Callable[..., Awaitable[None]]
DockerCleanup = Callable[[SessionResourceRecord], Awaitable[None]]
LOGGER = logging.getLogger(__name__)


def reconcile_session_resource_cleanup(
    resource: SessionResourceRecord,
    reason: str = "",
) -> SessionResourceCleanupReconciliation:
    """Classify one SessionResource journal without invoking an adapter.

    The operation journal is evidence only.  A missing journal, an in-flight
    or failed operation, and a mismatched identity never grant cleanup retry
    authority; only a matching completed journal and a released/missing row
    are a known terminal cleanup.
    """
    expected_operation_id = session_resource_cleanup_operation_id(resource.session_id, resource.id, reason)
    metadata = resource.metadata or {}
    raw_operation_id = metadata.get("cleanup_operation_id")
    raw_cleanup_state = metadata.get("cleanup_state")
    raw_cleanup_reason = metadata.get("cleanup_reason")
    observed_operation_id = raw_operation_id if isinstance(raw_operation_id, str) else None
    cleanup_state = (
        raw_cleanup_state
        if isinstance(raw_cleanup_state, str) and raw_cleanup_state in {"unknown", "completed"}
        else None
    )

    if raw_operation_id is None:
        status, reason_code = "unknown", "cleanup_not_started"
    elif not isinstance(raw_operation_id, str) or not raw_operation_id:
        status, reason_code = "drifted", "cleanup_operation_drift"
    elif raw_operation_id != expected_operation_id:
        if isinstance(raw_cleanup_reason, str) and raw_cleanup_reason != reason:
            status, reason_code = "unknown", "cleanup_other_operation"
        else:
            status, reason_code = "drifted", "cleanup_operation_drift"
    elif raw_cleanup_reason != reason:
        status, reason_code = "drifted", "cleanup_operation_drift"
    elif raw_cleanup_state == "completed" and resource.status in {
        SessionResourceStatus.released, SessionResourceStatus.missing,
    }:
        status, reason_code = "completed", "cleanup_completed"
    elif raw_cleanup_state == "unknown":
        status, reason_code = "unknown", "cleanup_unknown"
    else:
        status, reason_code = "drifted", "cleanup_state_drift"

    return SessionResourceCleanupReconciliation(
        session_id=resource.session_id,
        resource_id=resource.id,
        resource_key=resource.resource_key,
        kind=resource.kind,
        cleanup_policy=resource.cleanup_policy,
        operation_id=expected_operation_id,
        observed_operation_id=observed_operation_id,
        resource_status=resource.status,
        cleanup_state=cleanup_state,
        status=status,
        reason_code=reason_code,
    )


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

    async def reconcile_session_cleanup(
        self,
        session_id: str,
        reason: str = "",
    ) -> list[SessionResourceCleanupReconciliation]:
        """Read cleanup journal/status without invoking any external resource."""
        resources = await self._store.list(session_id)
        results: list[SessionResourceCleanupReconciliation] = []
        for resource in resources:
            if resource.cleanup_policy != SessionResourceCleanupPolicy.auto:
                continue
            result = reconcile_session_resource_cleanup(resource, reason)
            log = LOGGER.warning if result.status in {"unknown", "drifted"} else LOGGER.info
            log(
                "session_resource_cleanup_reconciled",
                extra={
                    "session_id": result.session_id,
                    "resource_id": result.resource_id,
                    "resource_key": result.resource_key,
                    "operation_id": result.operation_id,
                    "observed_operation_id": result.observed_operation_id,
                    "resource_status": result.resource_status.value,
                    "cleanup_state": result.cleanup_state,
                    "status": result.status,
                    "reason_code": result.reason_code,
                },
            )
            results.append(result)
        return results

    async def _cleanup_one(self, resource: SessionResourceRecord, reason: str) -> SessionResourceRecord:
        operation_id = session_resource_cleanup_operation_id(resource.session_id, resource.id, reason)
        current = next(
            (item for item in await self._store.list(resource.session_id) if item.id == resource.id),
            None,
        )
        if current is None:
            LOGGER.warning(
                "session_resource_cleanup_resource_missing_before_attempt",
                extra={
                    "session_id": resource.session_id,
                    "resource_id": resource.id,
                    "resource_key": resource.resource_key,
                    "operation_id": operation_id,
                    "cleanup_reason": reason,
                },
            )
            return resource
        observed_operation_id = current.metadata.get("cleanup_operation_id")
        observed_reason = current.metadata.get("cleanup_reason")
        if (
            current.metadata.get("cleanup_state") == "unknown"
            and isinstance(observed_operation_id, str)
            and bool(observed_operation_id)
        ):
            LOGGER.warning(
                "session_resource_cleanup_unknown_requires_reconciliation",
                extra={
                    "session_id": current.session_id,
                    "resource_id": current.id,
                    "resource_key": current.resource_key,
                    "operation_id": operation_id,
                    "observed_operation_id": observed_operation_id,
                    "cleanup_reason": reason,
                    "observed_cleanup_reason": observed_reason,
                    "resource_status": current.status.value,
                },
            )
            return current
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
                current = next((current for current in await self._store.list(resource.session_id)
                                if current.id == resource.id), resource)
                if current.metadata.get("allocation_id") == resource.metadata.get("allocation_id"):
                    return await self._store.mark_status(current.id, SessionResourceStatus.error, {
                        "cleanup_operation_id": operation_id,
                        "cleanup_state": "unknown",
                        "cleanup_reason": reason,
                        "cleanup_error_type": type(exc).__name__,
                    }) or current
                return current
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
