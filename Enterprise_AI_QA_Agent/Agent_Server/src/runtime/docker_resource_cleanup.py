"""Resolve lost Docker create responses from durable intent and exact labels."""

from __future__ import annotations

import logging
import re

from src.application.docker_management_service import DockerManagementService
from src.runtime.postgres_resource_allocation_store import PostgresResourceAllocationStore

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

    async def __call__(self, lease) -> None:
        if lease.resource_type != "docker":
            raise ValueError(f"Unexpected Docker cleanup resource type: {lease.resource_type}")
        if lease.external_resource_id and not re.fullmatch(r"[a-f0-9]{64}", lease.external_resource_id):
            raise ValueError("Automatic Docker cleanup requires an immutable full container ID")
        allocation = None
        if lease.allocation_id:
            rows = await self._allocations.get_many([lease.allocation_id], namespace=self._namespace)
            allocation = rows.get(lease.allocation_id)
            if allocation is None or any(
                allocation["lease_record"].get(key) != getattr(lease, key)
                for key in ("lease_token", "fencing_token", "resource_id", "resource_type")
            ):
                raise ValueError("Docker cleanup allocation ownership does not match durable intent")
        elif not lease.external_resource_id:
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
