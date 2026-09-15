from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.application.test_runs.run_store import InMemoryTestRunStore
from src.schemas.run_management import (
    TestRunItemRecord as _TestRunItemRecord,
    TestRunRecord as _TestRunRecord,
    TestRunStats as _TestRunStats,
)

_TestRunItemRecord.__test__ = False
_TestRunRecord.__test__ = False
_TestRunStats.__test__ = False


@pytest.mark.asyncio
async def test_waiting_resource_is_reclaimable_and_creates_new_attempt():
    now = datetime.now(timezone.utc)
    store = InMemoryTestRunStore()
    run = _TestRunRecord(
        id="run-1", project_id="project-1", suite_id="suite-1", mode_key="ui_automation",
        stats=_TestRunStats(total=1, queued=1), created_at=now, updated_at=now,
    )
    item = _TestRunItemRecord(
        id="item-1", run_id=run.id, case_id="case-1", case_version_id="version-1",
        position=1, created_at=now, updated_at=now,
    )
    await store.create_run(run, [item])
    claimed = (await store.claim_items(run_id=run.id, worker_id="worker-1", limit=1,
                                       lease_seconds=10, now=now))[0]
    await store.start_item(item.id, claimed[1].lease_token, now)
    waiting = await store.mark_waiting_resource(item.id, claimed[1].lease_token, "browser_capacity", now)
    assert waiting.status == "waiting_resource" and waiting.waiting_reason == "browser_capacity"
    assert (await store.get_run_record(run.id)).stats.waiting_resource == 1
    assert await store.recover_expired(run.id, now + timedelta(seconds=11)) == 1
    recovered = await store.get_item(item.id)
    assert recovered.status == "queued" and recovered.lease_token is None
    reclaimed = await store.claim_items(run_id=run.id, worker_id="worker-2", limit=1,
                                        lease_seconds=10, now=now + timedelta(seconds=12))
    assert reclaimed[0][1].attempt_no == 2
