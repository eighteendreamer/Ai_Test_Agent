import asyncio
from dataclasses import replace

import pytest

from src.cli.run_resource_cleanup_worker import build_cleanup_callbacks, main
from src.core.config import Settings
from src.runtime.resource_lease_manager import ExpiredResourceLease


def test_cleanup_entrypoint_is_importable():
    assert callable(main)


def test_cleanup_entrypoint_registers_forced_docker_cleanup(monkeypatch):
    calls = []

    async def list_containers(self):
        return [{"id": "a" * 64, "name": "qa-run-item-attempt"}]

    async def remove_container(self, container_id, *, force=False):
        calls.append((container_id, force))

    monkeypatch.setattr(
        "src.cli.run_resource_cleanup_worker.DockerManagementService.list_containers",
        list_containers,
    )
    monkeypatch.setattr(
        "src.cli.run_resource_cleanup_worker.DockerManagementService.remove_container",
        remove_container,
    )

    callback = build_cleanup_callbacks(Settings())["docker"]
    lease = ExpiredResourceLease(
        resource_id="docker-slot-0",
        resource_type="docker",
        external_resource_id="a" * 64,
        project_id="project-1",
        session_id="session-1",
        turn_id="turn-1",
        run_id="run-1",
        run_item_id="item-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        lease_token="lease-1",
        fencing_token=3,
        request_id="request-1",
        trace_id="trace-1",
    )
    asyncio.run(callback(lease))

    assert calls == [("a" * 64, True)]
    # Container names may be reused by a later Attempt; stale jobs must not delete them.
    with pytest.raises(ValueError, match="immutable full container ID"):
        asyncio.run(callback(replace(lease, external_resource_id="qa-run-item-attempt")))
    asyncio.run(callback(replace(lease, external_resource_id="b" * 64)))
    assert calls == [("a" * 64, True)]
