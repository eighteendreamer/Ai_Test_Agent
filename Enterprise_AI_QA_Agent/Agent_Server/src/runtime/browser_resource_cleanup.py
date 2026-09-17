"""Host-local cleanup by durable allocation and psutil's PID-reuse-safe handles.

Playwright BrowserType.launch(env=...) marks browser subprocesses. As with the
Docker adapter, a scanner can recover creation that completed before binding.
"""
from __future__ import annotations

import asyncio
import logging
import re

import psutil

from src.infrastructure.recording_store import PostgresRecordingStore
from src.runtime.postgres_browser_allocation_store import PostgresBrowserAllocationStore

LOGGER = logging.getLogger(__name__)
ALLOCATION_ENV = "QA_HARNESS_BROWSER_ALLOCATION"
NAMESPACE_ENV = "QA_HARNESS_BROWSER_NAMESPACE"
HOST_ENV = "QA_HARNESS_BROWSER_HOST"


def browser_processes(*, namespace, host_id):
    """Inspect only same-account processes; never emit process environment data."""
    owner = psutil.Process().username()
    matches = []
    inaccessible = 0
    for process in psutil.process_iter():
        try:
            if process.username() != owner:
                continue
            env = process.environ()
            allocation_id = env.get(ALLOCATION_ENV, "")
            if (re.fullmatch(r"[a-f0-9]{32}", allocation_id)
                    and env.get(NAMESPACE_ENV) == namespace and env.get(HOST_ENV) == host_id):
                # Materialize identity before any later destructive operation.
                process.create_time()
                matches.append((allocation_id, process))
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            # System/protected processes are not owned by this browser service.
            # Bound PIDs are checked separately and AccessDenied there is fatal.
            inaccessible += 1
    if inaccessible:
        LOGGER.debug("browser_inventory_inaccessible_processes", extra={"process_count": inaccessible, "host_id": host_id})
    return matches


def process_records(processes):
    result = []
    for _, process in processes:
        try:
            result.append({"pid": process.pid, "created_at": process.create_time()})
        except psutil.NoSuchProcess:
            continue
    return result


class BrowserResourceCleanup:
    def __init__(self, settings, *, allocations=None, namespace="qa"):
        self._settings = settings
        self._allocations = allocations or PostgresBrowserAllocationStore(settings)
        self._namespace = namespace
        self._host_id = settings.orchestration.resource_browser_host_id
        self._recording_cursor = 0
        self._recordings = PostgresRecordingStore(settings)

    async def __call__(self, lease):
        if lease.resource_type != "browser" or not lease.allocation_id:
            raise ValueError("Native browser cleanup requires durable allocation identity")
        allocations = await self._allocations.get_many([lease.allocation_id], namespace=self._namespace)
        allocation = allocations.get(lease.allocation_id)
        if allocation is None or allocation["host_id"] != self._host_id or any(
            allocation["lease_record"].get(key) != getattr(lease, key)
            for key in ("lease_token", "fencing_token", "resource_id", "resource_type")
        ):
            raise ValueError("Browser cleanup ownership or host does not match durable intent")
        if lease.external_resource_id and lease.external_resource_id != lease.allocation_id:
            raise ValueError("Browser external identity does not match allocation")
        await asyncio.to_thread(self._cleanup_sync, allocation)
        await self._allocations.mark_session_released(allocation)

    def _cleanup_sync(self, allocation):
        allocation_id = allocation["allocation_id"]
        processes = {process.pid: process for marker, process in browser_processes(
            namespace=self._namespace, host_id=self._host_id) if marker == allocation_id}
        for record in allocation["processes"]:
            try:
                process = psutil.Process(record["pid"])
                if process.create_time() != record["created_at"]:
                    continue  # PID belongs to a new process; never target it.
                env = process.environ()
                if (env.get(ALLOCATION_ENV) != allocation_id or env.get(NAMESPACE_ENV) != self._namespace
                        or env.get(HOST_ENV) != self._host_id):
                    raise ValueError("Browser process marker does not match durable intent")
                processes[process.pid] = process
            except psutil.NoSuchProcess:
                continue
        # Descendants are captured from validated parents, not from process names.
        for process in list(processes.values()):
            try:
                processes.update({child.pid: child for child in process.children(recursive=True)})
            except psutil.NoSuchProcess:
                continue
        for process in processes.values():
            try:
                process.kill()  # psutil rechecks PID reuse before signalling.
            except psutil.NoSuchProcess:
                continue
        _, alive = psutil.wait_procs(list(processes.values()),
                                    timeout=self._settings.orchestration.resource_browser_cleanup_timeout_seconds)
        if alive:
            raise TimeoutError("Browser processes did not exit after cleanup")
        remaining = [p for marker, p in browser_processes(namespace=self._namespace, host_id=self._host_id)
                     if marker == allocation_id]
        if remaining:
            raise RuntimeError("Browser allocation still has live processes; cleanup must retry")
        LOGGER.info("browser_process_cleanup_completed", extra={
            "allocation_id": allocation_id, "host_id": self._host_id, "process_count": len(processes),
        })

    async def reconcile(self, manager):
        if manager.namespace != self._namespace:
            raise ValueError("Browser inventory namespace mismatch")
        processes = await asyncio.to_thread(browser_processes, namespace=self._namespace, host_id=self._host_id)
        allocations = await self._allocations.get_many(list({marker for marker, _ in processes}), namespace=self._namespace)
        count = 0
        for marker, process in processes:
            allocation = allocations.get(marker)
            if allocation is None or allocation["host_id"] != self._host_id:
                LOGGER.warning("browser_inventory_unverified_resource", extra={"allocation_id": marker})
                continue
            try:
                identity = f"{process.pid}_{int(process.create_time() * 1000000)}"
            except psutil.NoSuchProcess:
                continue
            payload = {**allocation["lease_record"], "schema_version": 1, "task_type": "cleanup_task",
                       "task_id": f"task_browser_orphan_{marker}_{identity}", "cleanup_reason": "orphan_inventory",
                       "created_at": allocation["created_at"].isoformat()}
            if await manager.enqueue_orphan_cleanup(payload, stream=self._settings.orchestration.browser_cleanup_task_stream):
                count += 1
        batch_size = self._settings.orchestration.resource_browser_reconcile_batch_size
        recordings = await self._allocations.pending_recordings(namespace=self._namespace, host_id=self._host_id,
            after_generation=self._recording_cursor, limit=batch_size)
        for allocation in recordings:
            marker = allocation["allocation_id"]
            payload = {**allocation["lease_record"], "schema_version": 1, "task_type": "cleanup_task",
                       "task_id": f"task_browser_recording_{marker}", "cleanup_reason": "orphan_inventory",
                       "created_at": allocation["created_at"].isoformat()}
            if await manager.enqueue_orphan_cleanup(payload, stream=self._settings.orchestration.browser_cleanup_task_stream):
                count += 1
            self._recording_cursor = int(allocation["generation"])
        if len(recordings) < batch_size:
            self._recording_cursor = 0
        count += await self._recordings.recover_unbound_launches(
            namespace=self._namespace, host_id=self._host_id, limit=batch_size)
        return count
