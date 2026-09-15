from __future__ import annotations

import asyncio

import pytest

from src.runtime.resource_cleanup_worker import ResourceCleanupWorker


class Manager:
    async def reap_expired(self, *, limit): return [("docker", "docker-slot-1"), ("test_account", "account-1")]


@pytest.mark.asyncio
async def test_cleanup_worker_reaps_and_invokes_type_callback():
    calls = []
    async def docker(kind, identifier): calls.append((kind, identifier))
    worker = ResourceCleanupWorker(Manager(), interval_seconds=1, callbacks={"docker": docker})
    assert await worker.run_once() == 2
    assert calls == [("docker", "docker-slot-1")]


def test_cleanup_interval_must_be_positive():
    with pytest.raises(ValueError):
        ResourceCleanupWorker(Manager(), interval_seconds=0)
