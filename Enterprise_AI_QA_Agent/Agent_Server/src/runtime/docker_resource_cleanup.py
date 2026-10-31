"""Resolve lost Docker create responses from durable intent and exact labels."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from src.application.docker_management_service import DockerCommandError, DockerManagementService, DockerUnavailableError
from src.core.request_context import get_request_context
from src.runtime.postgres_resource_allocation_store import PostgresResourceAllocationStore
from src.runtime.resource_lease_manager import ExpiredResourceLease
from src.schemas.resource_cleanup import DockerResourceAbsenceObservation, DockerResourceQueryObservation

LOGGER = logging.getLogger(__name__)


def _labels(container: dict) -> dict[str, str]:
    return dict(part.split("=", 1) for part in str(container.get("labels") or "").split(",") if "=" in part)


def _matches(container: dict, allocation: dict) -> bool:
    labels = _labels(container)
    lease = allocation["lease_record"]
    return (
        bool(re.fullmatch(r"[a-f0-9]{64}", str(container.get("id") or "")))
        and labels.get("enterprise-ai-qa-agent") == "managed"
        and labels.get("qa.allocation_id") == allocation["allocation_id"]
        and labels.get("qa.lease_namespace") == allocation["lease_namespace"]
        and labels.get("qa.resource_id") == lease["resource_id"]
        and labels.get("qa.fencing_token") == str(lease["fencing_token"])
        and container.get("name") == allocation["container_name"]
        and (not allocation.get("container_id") or container["id"] == allocation["container_id"])
    )


class DockerResourceCleanup:
    def __init__(self, settings, *, allocations=None, namespace="qa", docker=None) -> None:
        self._docker = docker or DockerManagementService(settings)
        self._allocations = allocations or PostgresResourceAllocationStore(settings)
        self._namespace = namespace
        self._batch_size = settings.orchestration.resource_docker_reconcile_batch_size
        self._cursor = ""

    async def observe_absence(self, lease) -> DockerResourceAbsenceObservation | None:
        """Compatibility view of the same query, without inventing absence."""
        return (await self.query_target(lease)).absence_observation

    async def query_target(self, lease) -> DockerResourceQueryObservation:
        """Read only. An unbound allocation may still have a late create response."""
        fields = {"resource_id": lease.resource_id, "allocation_id": lease.allocation_id,
            "container_id": lease.external_resource_id}
        def unconfirmed(reason, *, error_type=None):
            return DockerResourceQueryObservation(status="present" if reason == "container_present" else "unknown",
                reason_code=reason, observed_at=datetime.now(timezone.utc), error_type=error_type)
        if (lease.resource_type != "docker" or not lease.allocation_id
            or not re.fullmatch(r"[a-f0-9]{64}", lease.external_resource_id or "")):
            LOGGER.warning("docker_cleanup_absence_unconfirmed", extra={**fields, "reason": "identity_unavailable"})
            return unconfirmed("identity_unavailable")
        allocation = await self._allocation_for_lease(lease)
        prepared = ExpiredResourceLease.from_payload({**allocation["lease_record"],
            "external_resource_id": allocation.get("container_id"), "allocation_id": allocation["allocation_id"]})
        if prepared != lease:
            LOGGER.warning("docker_cleanup_absence_unconfirmed", extra={**fields, "reason": "allocation_identity_mismatch"})
            return unconfirmed("allocation_identity_mismatch")
        try:
            present = await self._docker.container_exists(lease.external_resource_id)
            if present is not False:
                LOGGER.info("docker_cleanup_absence_unconfirmed", extra={**fields,
                    "reason": "container_present" if present is True else "query_unconfirmed"})
                return unconfirmed("container_present" if present is True else "query_unconfirmed")
        except (DockerCommandError, DockerUnavailableError) as exc:
            LOGGER.warning("docker_cleanup_absence_unconfirmed", extra={**fields,
                "reason": "query_failed", "error_type": type(exc).__name__})
            return unconfirmed("query_failed", error_type=type(exc).__name__)
        context = get_request_context()
        observation = DockerResourceAbsenceObservation(container_id=lease.external_resource_id,
            allocation_id=lease.allocation_id, lease_namespace=self._namespace,
            observer_worker_id=str(context.worker_id if context and context.worker_id else "cleanup"),
            observed_at=datetime.now(timezone.utc))
        LOGGER.info("docker_cleanup_absence_observed", extra=fields)
        return DockerResourceQueryObservation(status="absent", reason_code="container_absent",
            observed_at=observation.observed_at, absence_observation=observation)

    async def _allocation_for_lease(self, lease):
        if not lease.allocation_id:
            return None
        rows = await self._allocations.get_many([lease.allocation_id], namespace=self._namespace)
        allocation = rows.get(lease.allocation_id)
        if (allocation is None or allocation["allocation_id"] != lease.allocation_id
            or allocation["lease_namespace"] != self._namespace or any(
                allocation["lease_record"].get(key) != getattr(lease, key)
                for key in ("lease_token", "fencing_token", "resource_id", "resource_type"))):
            raise ValueError("Docker cleanup allocation ownership does not match durable intent")
        return allocation

    async def __call__(self, lease) -> None:
        if lease.resource_type != "docker":
            raise ValueError(f"Unexpected Docker cleanup resource type: {lease.resource_type}")
        if lease.external_resource_id and not re.fullmatch(r"[a-f0-9]{64}", lease.external_resource_id):
            raise ValueError("Automatic Docker cleanup requires an immutable full container ID")
        allocation = await self._allocation_for_lease(lease)
        if allocation is None and not lease.external_resource_id:
            return
        containers = await self._docker.list_containers()
        for container in containers:
            if lease.external_resource_id:
                if container.get("id") != lease.external_resource_id:
                    continue
                if allocation is not None and not _matches(container, allocation):
                    raise ValueError("Docker container labels do not match durable intent")
            else:
                if _labels(container).get("qa.allocation_id") != lease.allocation_id:
                    continue
                if not _matches(container, allocation):
                    raise ValueError("Docker container labels do not match durable intent")
            # Names and labels identify the intended allocation, but deletion
            # always uses the immutable full ID returned by the Docker daemon.
            await self._docker.remove_container(container["id"], force=True)

    async def reconcile(self, manager) -> int:
        if manager.namespace != self._namespace:
            raise ValueError("Docker inventory namespace mismatch")
        candidates = sorted((c for c in await self._docker.list_containers()
            if _labels(c).get("qa.lease_namespace") == self._namespace
            and _labels(c).get("qa.allocation_id")), key=lambda c: c["id"])
        batch = ([c for c in candidates if c["id"] > self._cursor] or candidates)[:self._batch_size]
        if not batch:
            return 0
        self._cursor = batch[-1]["id"]
        allocations = await self._allocations.get_many(
            [_labels(c)["qa.allocation_id"] for c in batch], namespace=self._namespace)
        enqueued = 0
        for container in batch:
            allocation = allocations.get(_labels(container)["qa.allocation_id"])
            if allocation is None or not _matches(container, allocation):
                LOGGER.warning("docker_inventory_unverified_resource", extra={"container_id": container["id"]})
                continue
            payload = {
                **allocation["lease_record"], "schema_version": 1, "task_type": "cleanup_task",
                "task_id": f"task_cleanup_orphan_{allocation['allocation_id']}_{container['id']}",
                "external_resource_id": container["id"], "cleanup_reason": "orphan_inventory",
                "created_at": allocation["created_at"].isoformat(),
            }
            # The Lua publish checks the current owner atomically and deduplicates
            # repeated scans. Redis failure leaves the durable intent for next time.
            if await manager.enqueue_orphan_cleanup(payload):
                enqueued += 1
                LOGGER.info("docker_inventory_cleanup_enqueued", extra={
                    "allocation_id": allocation["allocation_id"], "container_id": container["id"],
                    "resource_id": payload["resource_id"], "attempt_id": payload.get("attempt_id"),
                })
        return enqueued
