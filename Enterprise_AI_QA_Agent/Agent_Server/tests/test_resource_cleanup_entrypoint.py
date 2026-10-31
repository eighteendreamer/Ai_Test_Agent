import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.cli.run_resource_cleanup_worker import build_cleanup_callbacks, main
from src.core.config import Settings
from src.runtime.resource_lease_manager import ExpiredResourceLease
from src.runtime.docker_resource_cleanup import DockerResourceCleanup
from src.application.docker_management_service import DockerCommandError
from src.schemas.resource_cleanup import DockerResourceQueryObservation
from tests.test_resource_cleanup_worker import expired_lease


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


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_field", ["name", "fencing_token", "namespace", "missing_intent"])
async def test_allocation_cleanup_rejects_unverified_container(wrong_field):
    lease = replace(expired_lease("docker", "slot-1"), allocation_id="b" * 32)
    allocation = {
        "allocation_id": lease.allocation_id, "lease_record": lease.__dict__,
        "lease_namespace": "qa:test", "container_name": "original-name", "container_id": None,
    }
    labels = {"enterprise-ai-qa-agent": "managed", "qa.allocation_id": lease.allocation_id,
              "qa.resource_id": lease.resource_id, "qa.fencing_token": str(lease.fencing_token),
              "qa.lease_namespace": "qa:test"}
    if wrong_field == "fencing_token": labels["qa.fencing_token"] = "99"
    if wrong_field == "namespace": labels["qa.lease_namespace"] = "other-deployment"
    container = {"id": "a" * 64, "name": "changed-name" if wrong_field == "name" else "original-name",
                 "labels": ",".join(f"{key}={value}" for key, value in labels.items())}

    class Allocations:
        async def get_many(self, *args, **kwargs):
            return {} if wrong_field == "missing_intent" else {lease.allocation_id: allocation}
    class Docker:
        async def list_containers(self): return [container]
        async def remove_container(self, *args, **kwargs):
            raise AssertionError("Unverified resource must never be removed")
    cleanup = DockerResourceCleanup(Settings(), allocations=Allocations(), namespace="qa:test", docker=Docker())
    with pytest.raises(ValueError, match="intent"):
        await cleanup(lease)


@pytest.mark.asyncio
async def test_inventory_cursor_does_not_starve_orphan_after_live_entries():
    containers, records = [], {}
    for index in range(3):
        allocation_id = f"{index:032x}"
        lease = replace(expired_lease("docker", f"slot-{index}"), allocation_id=allocation_id)
        name = f"container-{index}"
        containers.append({"id": f"{index:064x}", "name": name,
            "labels": f"enterprise-ai-qa-agent=managed,qa.allocation_id={allocation_id},qa.resource_id={lease.resource_id},qa.fencing_token=7,qa.lease_namespace=qa:test"})
        records[allocation_id] = {"allocation_id": allocation_id, "lease_namespace": "qa:test",
            "lease_record": lease.__dict__, "container_name": name, "created_at": datetime.now(timezone.utc)}
    class Allocations:
        async def get_many(self, ids, **kwargs): return {key: records[key] for key in ids}
    class Docker:
        async def list_containers(self): return containers
    class Manager:
        namespace = "qa:test"
        def __init__(self): self.seen = []
        async def enqueue_orphan_cleanup(self, payload):
            self.seen.append(payload["resource_id"])
            return payload["resource_id"] == "slot-2"
    settings = SimpleNamespace(orchestration=SimpleNamespace(resource_docker_reconcile_batch_size=1))
    cleanup = DockerResourceCleanup(settings, allocations=Allocations(), namespace="qa:test", docker=Docker())
    manager = Manager()
    assert [await cleanup.reconcile(manager) for _ in range(4)] == [0, 0, 1, 0]
    assert manager.seen == ["slot-0", "slot-1", "slot-2", "slot-0"]


@pytest.mark.asyncio
@pytest.mark.parametrize("material", ["valid", "present", "query_failed", "query_unconfirmed", "missing_allocation", "short_id",
    "missing_intent", "unbound", "target_drift", "session_drift", "worker_drift", "namespace_drift", "fence_drift"])
async def test_docker_absence_observation_requires_its_bound_allocation(material, caplog):
    lease = replace(expired_lease("docker", "slot-observation", "a" * 64), allocation_id="b" * 32)
    allocation = {"allocation_id": lease.allocation_id, "lease_namespace": "qa:test",
        "lease_record": lease.__dict__.copy(), "container_name": "original", "container_id": "a" * 64}
    if material == "missing_allocation": lease = replace(lease, allocation_id=None)
    if material == "short_id": lease = replace(lease, external_resource_id="a" * 12)
    if material == "unbound": allocation["container_id"] = None
    if material == "target_drift": allocation["container_id"] = "c" * 64
    if material == "session_drift": allocation["lease_record"]["session_id"] = "foreign"
    if material == "worker_drift": allocation["lease_record"]["worker_id"] = "foreign"
    if material == "namespace_drift": allocation["lease_namespace"] = "foreign"
    if material == "fence_drift": allocation["lease_record"]["fencing_token"] = 8
    queries = []
    class Allocations:
        async def get_many(self, ids, *, namespace):
            assert ids == [lease.allocation_id] and namespace == "qa:test"
            return {} if material == "missing_intent" else {lease.allocation_id: allocation}
    class Docker:
        async def container_exists(self, target):
            queries.append(target)
            if material == "query_failed":
                raise DockerCommandError("private-docker-query-error")
            if material == "query_unconfirmed":
                return None
            return material == "present"
        async def remove_container(self, *_args, **_kwargs):
            raise AssertionError("observation must never remove a resource")
    cleanup = DockerResourceCleanup(Settings(), allocations=Allocations(), namespace="qa:test", docker=Docker())
    if material in {"missing_intent", "namespace_drift", "fence_drift"}:
        with pytest.raises(ValueError, match="intent"):
            await cleanup.observe_absence(lease)
    else:
        result = await cleanup.observe_absence(lease)
        if material == "valid":
            assert result.container_id == lease.external_resource_id and result.allocation_id == lease.allocation_id
            assert result.lease_namespace == "qa:test" and result.observed_at.tzinfo is not None
            assert result.scope == "docker_container_absence_only"
        else:
            assert result is None
    assert queries == (["a" * 64] if material in {"valid", "present", "query_failed", "query_unconfirmed"} else [])
    assert "private-docker-query-error" not in caplog.text


@pytest.mark.parametrize("invalid", ["status", "missing_absence", "false_absence", "error_type", "scope", "naive_time", "absence_time"])
def test_docker_query_observation_rejects_incoherent_evidence(invalid):
    from tests.test_resource_cleanup_worker import query_observation
    lease = replace(expired_lease("docker", "slot-model", "a" * 64), allocation_id="b" * 32)
    data = query_observation(lease).model_dump(mode="json")
    if invalid == "status": data["status"] = "present"
    if invalid == "missing_absence": data["absence_observation"] = None
    if invalid == "false_absence": data.update(status="unknown", reason_code="query_unconfirmed")
    if invalid == "error_type": data["error_type"] = "ConnectionError"
    if invalid == "scope": data["scope"] = "command_succeeded"
    if invalid == "naive_time": data["observed_at"] = "2026-10-31T00:00:00"
    if invalid == "absence_time": data["observed_at"] = "2026-10-31T00:00:00Z"
    with pytest.raises(ValueError):
        DockerResourceQueryObservation.model_validate_json(json.dumps(data))
