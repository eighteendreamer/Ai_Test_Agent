from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4

import pytest

from test_case_planning import environment, active_case
from src.application.test_runs.run_store import InMemoryTestRunStore, PostgresTestRunStore
from src.application.test_runs.dependencies import output_at_path
from src.application.test_suites.suite_service import TestSuiteService as SuiteService
from src.application.test_suites.suite_store import InMemoryTestSuiteStore, PostgresTestSuiteStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.project import ProjectCreateRequest
from src.schemas.run_management import TestRunRecord as Run, TestRunItemRecord as Item, RunItemCompletion
from src.schemas.suite_management import TestSuiteCreateRequest as SuiteRequest


LIVE = pytest.param(True, marks=pytest.mark.skipif(os.getenv("RUN_LIVE_RUN_DEPENDENCIES") != "1",
    reason="requires isolated real PostgreSQL/MySQL/RustFS"))


@asynccontextmanager
async def setup(tmp_path, live=False):
    async with environment(tmp_path, live=live) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="dependencies", name="依赖领取验收"))
        content = {"objective": "创建与查询", "steps": [{"action": "POST /fixtures", "output": "body.id"},
            {"action": "GET /fixtures/{id}", "requires": "body.id"}], "negative": "保留未登录条件"}
        source = tmp_path / "dependency-requirements.json"
        source.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        cases = [await active_case(env, project, f"item-{index}", {**json.loads(source.read_text(encoding="utf-8")),
            "objective": f"真实依赖样本 {index}"}) for index in range(5)]
        tables = []
        if live:
            suffix = uuid4().hex[:12]
            fields = ["postgres_test_case_result_table", "postgres_test_run_attempt_table", "postgres_test_run_item_table",
                "postgres_test_run_table", "postgres_test_suite_item_table", "postgres_test_suite_table"]
            tables = [f"up_rd_{index}_{suffix}" for index in range(len(fields))]
            for field, table in zip(fields, tables): setattr(env.settings.database, field, table)
            with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                for table in tables:
                    cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                    assert cursor.fetchone()["existing"] is None
            suite_store, store = PostgresTestSuiteStore(env.settings), PostgresTestRunStore(env.settings)
        else:
            suite_store, store = InMemoryTestSuiteStore(), InMemoryTestRunStore()
        try:
            suites = SuiteService(store=suite_store, project_service=env.projects, test_case_service=env.cases)
            await suites.initialize()
            suite = await suites.create(project.id, SuiteRequest(name="固定依赖样本", items=[{
                "case_id": bundle.case.id, "case_version_id": bundle.version.id} for bundle in cases]))
            await store.initialize()
            now = datetime.now(timezone.utc)
            run = Run(id=str(uuid4()), project_id=project.id, suite_id=suite.suite.id, mode_key="api_testing",
                stats={"total": 5, "queued": 5}, created_at=now, updated_at=now)
            items = [Item(id=str(uuid4()), run_id=run.id, case_id=b.case.id, case_version_id=b.version.id,
                position=index + 1, created_at=now, updated_at=now) for index, b in enumerate(cases)]
            yield env, store, run, items, now
        finally:
            if live:
                with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                    for table in tables: cursor.execute(f'DROP TABLE IF EXISTS "{table}"')


async def finish(store, claim, now, actual=None, status="passed"):
    item, attempt = claim
    await store.start_item(item.id, attempt.lease_token, now)
    return await store.complete_item(item.id, attempt.lease_token, RunItemCompletion(status=status,
        summary="实际前置结果", actual=actual or {}, payload_hash="a" * 64), now)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_atomic_claim_waits_for_predecessor_and_real_required_output(tmp_path, live):
    async with setup(tmp_path, live) as (env, store, run, items, now):
        # Display order deliberately puts a dependent before its predecessor.
        items[0] = Item.model_validate({**items[0].model_dump(), "dependencies": [{"item_id": items[1].id,
            "required_output_paths": [["body", "id"]]}]})
        items[2] = Item.model_validate({**items[2].model_dump(), "dependencies": [{"item_id": items[0].id}]})
        items[3] = Item.model_validate({**items[3].model_dump(), "dependencies": [{"item_id": items[1].id,
            "required_output_paths": [["missing"]]}]})
        await store.create_run(run, items)
        async def claim(worker):
            return await store.claim_items(run_id=run.id, worker_id=worker, limit=5, lease_seconds=60, now=now)
        first = sum(await asyncio.gather(claim("worker-a"), claim("worker-b")), [])
        assert {item.id for item, _ in first} == {items[1].id, items[4].id}
        assert len({attempt.lease_token for _, attempt in first}) == 2
        for value in first:
            await finish(store, value, now, {"body": {"id": "owned-real-id", "zero": 0, "disabled": False}})
        second = await claim("worker-c")
        assert [item.id for item, _ in second] == [items[0].id]
        await finish(store, second[0], now)
        third = await claim("worker-d")
        assert [item.id for item, _ in third] == [items[2].id]
        await finish(store, third[0], now)
        assert await claim("worker-e") == []
        record = await store.get_run(run.id)
        blocked = next(i for i in record.items if i.id == items[3].id)
        assert blocked.status == "blocked" and blocked.started_at is None
        assert record.run.stats.passed == 4 and record.run.stats.blocked == 1 and record.run.status == "completed"
        receipt = next(result for result in record.results if result.run_item_id == blocked.id)
        assert receipt.actual["business_execution_started"] is False
        assert receipt.actual["dependency_failures"][0]["reason"] == "required_output_missing"
        count = len(record.results)
        assert await claim("worker-f") == []
        assert len((await store.get_run(run.id)).results) == count
        if live:
            reread = await PostgresTestRunStore(env.settings).get_item(items[0].id)
            assert reread.dependencies[0].required_output_paths == [["body", "id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_failed_null_output_and_zero_false_array_paths_match_storage_semantics(tmp_path, live):
    async with setup(tmp_path, live) as (env, store, run, items, now):
        for index, path in [(1, ["body", "zero"]), (2, ["body", "disabled"]), (3, ["body", "values", "0"]), (4, ["body", "nullable"])]:
            items[index] = Item.model_validate({**items[index].model_dump(), "dependencies": [{"item_id": items[0].id,
                "required_output_paths": [path]}]})
        await store.create_run(run, items)
        claims = await store.claim_items(run_id=run.id, worker_id="worker", limit=5, lease_seconds=60, now=now)
        assert len(claims) == 1 and claims[0][0].id == items[0].id
        await finish(store, claims[0], now, {"body": {"zero": 0, "disabled": False, "values": ["actual"], "nullable": None}})
        claims = await store.claim_items(run_id=run.id, worker_id="worker", limit=5, lease_seconds=60, now=now)
        assert {i.id for i, _ in claims} == {i.id for i in items[1:4]}
        assert (await store.get_item(items[4].id)).status == "blocked"
        # Fresh run with a failed predecessor: independent work still proceeds.
        new_run = run.model_copy(update={"id": str(uuid4())})
        new_items = [Item.model_validate({**i.model_dump(), "id": str(uuid4()), "run_id": new_run.id, "dependencies": []}) for i in items]
        new_items[1] = Item.model_validate({**new_items[1].model_dump(), "dependencies": [{"item_id": new_items[0].id}]})
        await store.create_run(new_run, new_items)
        claims = await store.claim_items(run_id=new_run.id, worker_id="worker", limit=5, lease_seconds=60, now=now)
        assert {i.id for i, _ in claims} == {i.id for i in new_items if i.id != new_items[1].id}
        for claim in claims: await finish(store, claim, now, status="failed")
        assert await store.claim_items(run_id=new_run.id, worker_id="worker", limit=5, lease_seconds=60, now=now) == []
        record = await store.get_run(new_run.id)
        assert record.run.status == "completed" and record.run.stats.failed == 4 and record.run.stats.blocked == 1
        assert next(i for i in record.items if i.id == new_items[1].id).started_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing", "self", "cycle", "cross-run", "duplicate"])
async def test_invalid_dependency_graph_is_rejected_before_any_run_write(tmp_path, defect):
    async with setup(tmp_path) as (_, store, run, items, now):
        parent = items[1].id
        if defect == "missing": parent = str(uuid4())
        if defect == "self": parent = items[0].id
        dependencies = [{"item_id": parent}]
        if defect == "duplicate": dependencies *= 2
        items[0] = Item.model_validate({**items[0].model_dump(), "dependencies": dependencies})
        if defect == "cycle": items[1] = Item.model_validate({**items[1].model_dump(), "dependencies": [{"item_id": items[0].id}]})
        if defect == "cross-run": items[1] = items[1].model_copy(update={"run_id": str(uuid4())})
        with pytest.raises(ValueError, match="依赖|运行"):
            await store.create_run(run, items)
        assert await store.get_run(run.id) is None


@pytest.mark.asyncio
async def test_expired_attempt_keeps_dependency_metadata_and_checkpoint(tmp_path):
    async with setup(tmp_path) as (_, store, run, items, now):
        items[1] = Item.model_validate({**items[1].model_dump(), "dependencies": [{"item_id": items[0].id}]})
        await store.create_run(run, items)
        first = await store.claim_items(run_id=run.id, worker_id="a", limit=1, lease_seconds=60, now=now)
        await finish(store, first[0], now)
        second = await store.claim_items(run_id=run.id, worker_id="a", limit=1, lease_seconds=60, now=now)
        item, attempt = second[0]
        assert item.id == items[1].id
        await store.start_item(item.id, attempt.lease_token, now)
        await store.save_checkpoint(item.id, attempt.lease_token, "side-effect", {"completed": True}, 1, now)
        await store.recover_expired(run.id, now + timedelta(seconds=61))
        recovered = await store.claim_items(run_id=run.id, worker_id="b", limit=1, lease_seconds=60, now=now + timedelta(seconds=61))
        assert recovered[0][0].dependencies == item.dependencies
        assert recovered[0][1].checkpoint_payload == {"completed": True}
        assert recovered[0][1].recovered_from_attempt_id == attempt.id


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_concurrent_failure_receipts_propagate_once_without_touching_independent_work(tmp_path, live):
    async with setup(tmp_path, live) as (_, store, run, items, now):
        for child, parent in [(0, 1), (1, 2), (4, 2)]:
            items[child] = Item.model_validate({**items[child].model_dump(), "dependencies": [{"item_id": items[parent].id}]})
        await store.create_run(run, items)
        first = await store.claim_items(run_id=run.id, worker_id="initial", limit=5, lease_seconds=60, now=now)
        assert {item.id for item, _ in first} == {items[2].id, items[3].id}
        root = next(claim for claim in first if claim[0].id == items[2].id)
        original = await finish(store, root, now, status="failed")
        claims = await asyncio.wait_for(asyncio.gather(*[store.claim_items(run_id=run.id,
            worker_id=f"concurrent-{i}", limit=5, lease_seconds=60, now=now) for i in range(8)]), timeout=15)
        assert not any(claims)
        independent = next(claim for claim in first if claim[0].id == items[3].id)
        await finish(store, independent, now)
        record = await store.get_run(run.id)
        assert record.run.status == "completed" and record.run.stats.blocked == 3
        assert record.run.stats.passed == 1 and record.run.stats.failed == 1
        assert len(record.results) == len(record.attempts) == 5
        assert next(r for r in record.results if r.id == original.id) == original
        assert all(item.started_at is None for item in record.items if item.status == "blocked")
        assert len({r.run_item_id for r in record.results}) == 5


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RUN_DEPENDENCIES") != "1", reason="requires isolated real PostgreSQL")
async def test_blocked_receipt_and_stats_rollback_together_after_insert_failure(tmp_path, monkeypatch):
    async with setup(tmp_path, True) as (_, store, run, items, now):
        items[1] = Item.model_validate({**items[1].model_dump(), "dependencies": [{"item_id": items[0].id,
            "required_output_paths": [["missing"]]}]})
        await store.create_run(run, items)
        claims = await store.claim_items(run_id=run.id, worker_id="first", limit=5, lease_seconds=60, now=now)
        for claim in claims: await finish(store, claim, now)
        write = store._write_item
        def fail_after_result_insert(cur, item):
            raise RuntimeError("fixture failure after blocked result insert")
        monkeypatch.setattr(store, "_write_item", fail_after_result_insert)
        with pytest.raises(RuntimeError, match="fixture failure"):
            await store.claim_items(run_id=run.id, worker_id="broken", limit=5, lease_seconds=60, now=now)
        record = await store.get_run(run.id)
        assert record.run.stats.passed == 4 and record.run.stats.queued == 1
        assert len(record.results) == len(record.attempts) == 4
        assert next(i for i in record.items if i.id == items[1].id).status == "queued"
        monkeypatch.setattr(store, "_write_item", write)
        assert await store.claim_items(run_id=run.id, worker_id="retry", limit=5, lease_seconds=60, now=now) == []
        record = await store.get_run(run.id)
        assert record.run.status == "completed" and record.run.stats.blocked == 1
        assert len(record.results) == len(record.attempts) == 5


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RUN_DEPENDENCIES") != "1", reason="requires isolated real PostgreSQL/MySQL/RustFS")
@pytest.mark.parametrize("transition", ["claim", "complete", "cancel"])
async def test_run_detail_keeps_one_snapshot_across_concurrent_commit(tmp_path, monkeypatch, transition):
    # Reproduce PLAN 7.102's read window with actual store transactions: pause
    # after the reader's first SELECT, then commit a writer before it continues.
    async with setup(tmp_path, True) as (_, store, run, items, now):
        await store.create_run(run, items)
        claims = []
        if transition == "complete":
            claims = await store.claim_items(run_id=run.id, worker_id="initial", limit=5, lease_seconds=60, now=now)
            for item, attempt in claims:
                await store.start_item(item.id, attempt.lease_token, now)
        before = await store.get_run(run.id)
        first_select, writer_done = Event(), Event()
        detail = store._detail_from_cursor
        intercepted = False

        class PausedCursor:
            def __init__(self, cursor):
                self.cursor = cursor
                self.paused = False

            def execute(self, *args, **kwargs):
                result = self.cursor.execute(*args, **kwargs)
                if not self.paused:
                    self.paused = True
                    first_select.set()
                    assert writer_done.wait(15), "concurrent store transaction did not commit while detail read was paused"
                return result

            def __getattr__(self, name):
                return getattr(self.cursor, name)

        def pause_once(cursor, run_id):
            nonlocal intercepted
            if intercepted:
                return detail(cursor, run_id)
            intercepted = True
            return detail(PausedCursor(cursor), run_id)

        monkeypatch.setattr(store, "_detail_from_cursor", pause_once)
        reading = asyncio.create_task(store.get_run(run.id))
        try:
            assert await asyncio.to_thread(first_select.wait, 10), "detail reader did not reach first SELECT"
            if transition == "claim":
                claims = await asyncio.wait_for(store.claim_items(run_id=run.id, worker_id="concurrent", limit=5,
                    lease_seconds=60, now=now), timeout=10)
                assert len(claims) == 5
            elif transition == "complete":
                for item, attempt in claims:
                    await asyncio.wait_for(store.complete_item(item.id, attempt.lease_token, RunItemCompletion(
                        status="passed", summary="actual concurrent completion", actual={"ok": True}, payload_hash="b" * 64), now), timeout=10)
            else:
                await asyncio.wait_for(store.cancel_run(run.id, "actual concurrent cancellation", now), timeout=10)
        finally:
            writer_done.set()
            frozen = await asyncio.wait_for(reading, timeout=10)
        after = await store.get_run(run.id)
        assert before != after
        assert frozen == before, {
            "transition": transition, "before": before.model_dump(mode="json"),
            "mixed": frozen.model_dump(mode="json"), "after": after.model_dump(mode="json"),
        }
        assert [item.id for item in frozen.items] == [item.id for item in before.items]
        assert len(after.attempts) == (0 if transition == "cancel" else 5)
        if transition == "complete":
            assert after.run.status == "completed" and after.run.stats.passed == 5
            assert len(after.results) == 5
        elif transition == "cancel":
            assert after.run.status == "cancelled"
            assert all(item.status == "cancelled" for item in after.items)
        else:
            assert all(item.status == "claimed" for item in after.items)


@pytest.mark.skipif(os.getenv("RUN_LIVE_RUN_DEPENDENCIES") != "1", reason="requires PostgreSQL JSON operators")
def test_literal_output_paths_match_postgres_including_root_empty_key_and_negative_index():
    from src.core.config import get_settings
    actual = {"": "empty-key", "a.b": {"values": [0, False, None]}, "零": 0}
    paths = [[], [""], ["a.b", "values", "0"], ["a.b", "values", "1"],
        ["a.b", "values", "-2"], ["a.b", "values", "2"], ["a.b", "values", "bad"],
        ["a.b", "values", "9" * 256], ["missing"], ["零"]]
    with postgres_connect(get_settings()) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version') AS version")
        print("actual_postgresql_version " + cursor.fetchone()["version"])
        for path in paths:
            cursor.execute("SELECT %s::jsonb #> %s::text[] AS value", (json.dumps(actual), path))
            assert cursor.fetchone()["value"] == output_at_path(actual, path)
