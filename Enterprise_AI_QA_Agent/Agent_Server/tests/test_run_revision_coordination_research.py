"""Live transaction experiments for PLAN 7.103, not a production revision API.

Use the real RunStore claimant with a test-only shared revision-head lock. The
candidate writer only revises dependencies; plans/suites are deliberately not
rewritten by this experiment. This cannot establish full revision support.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from threading import Event, Lock, local
from time import monotonic
from uuid import uuid4

import pytest

from test_run_item_dependencies import setup
from test_case_plan_run_creation import planned
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_planning import CasePlanRunRequest
from src.schemas.run_management import RunItemDependency


pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_RUN_DEPENDENCIES") != "1",
    reason="requires owned PostgreSQL/MySQL/RustFS fixtures for revision coordination research")


class RevisionConflict(ValueError):
    pass


class CandidateCoordination:
    def __init__(self, settings, store, run, items, head, history):
        self.settings, self.store, self.run, self.items = settings, store, run, items
        self.head, self.history = head, history
        self.events, self.pids = {}, {}
        self.guard, self.thread = Lock(), local()
        self.pause_claims = False

    def event(self, name):
        with self.guard:
            return self.events.setdefault(name, Event())

    def register(self, cursor, actor):
        cursor.execute("SET LOCAL statement_timeout = '10s'")
        cursor.execute("SELECT pg_backend_pid() AS pid")
        with self.guard:
            self.pids[actor] = cursor.fetchone()["pid"]

    async def reached(self, name):
        assert await asyncio.to_thread(self.event(name).wait, 10), f"actor did not reach {name}"

    def wait(self, name):
        assert self.event(name).wait(15), f"actor was not released: {name}"

    async def blocked_by(self, actor, owner):
        def observe():
            with postgres_connect(self.settings) as conn, conn.cursor() as cursor:
                cursor.execute("SELECT pg_blocking_pids(%s) AS blockers", (self.pids[actor],))
                return cursor.fetchone()["blockers"]
        deadline = monotonic() + 10
        while monotonic() < deadline:
            if self.pids[owner] in await asyncio.to_thread(observe):
                return
            await asyncio.sleep(0.02)
        pytest.fail(f"PostgreSQL did not observe {actor} blocked by {owner}")

    def revise(self, *, expected=0, pause=False, fail=False):
        with postgres_connect(self.settings) as conn, conn.cursor() as cursor:
            self.register(cursor, "revision")
            self.event("revision-attempted").set()
            cursor.execute(f"SELECT revision FROM {self.head} WHERE run_id=%s FOR UPDATE", (self.run.id,))
            if cursor.fetchone()["revision"] != expected:
                raise RevisionConflict("stale expected revision")
            self.event("revision-locked").set()
            item = self.store._lock_item(cursor, self.items[0].id)
            if item.status != "queued" or item.attempt_no or item.lease_token or item.result_id:
                raise RevisionConflict("item is already claimed or has execution history")
            updated = item.model_copy(update={"dependencies": [RunItemDependency(item_id=self.items[1].id)]})
            self.store._write_item(cursor, updated)
            cursor.execute(f"INSERT INTO {self.history} (run_id,revision,record) VALUES (%s,%s,%s::jsonb)",
                (self.run.id, expected + 1, json.dumps({"before": item.model_dump(mode="json"),
                    "after": updated.model_dump(mode="json"), "reason": "candidate dependency revision"})))
            cursor.execute(f"UPDATE {self.head} SET revision=%s WHERE run_id=%s", (expected + 1, self.run.id))
            self.event("revision-written").set()
            if pause:
                self.wait("release-revision")
            if fail:
                raise RuntimeError("actual failure after dependency/history/head writes")

    def head_and_history(self):
        with postgres_connect(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(f"SELECT revision FROM {self.head} WHERE run_id=%s", (self.run.id,))
            revision = cursor.fetchone()["revision"]
            cursor.execute(f"SELECT record FROM {self.history} WHERE run_id=%s ORDER BY revision", (self.run.id,))
            return revision, [row["record"] for row in cursor.fetchall()]


@asynccontextmanager
async def experiment(tmp_path, monkeypatch):
    async with setup(tmp_path, True) as (env, store, run, items, now):
        await store.create_run(run, items)
        suffix = uuid4().hex[:12]
        head, history = "up_rev_head_" + suffix, "up_rev_history_" + suffix
        with postgres_connect(env.settings) as conn, conn.cursor() as cursor:
            for table in (head, history):
                cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cursor.fetchone()["existing"] is None
            cursor.execute(f"CREATE TABLE {head} (run_id UUID PRIMARY KEY REFERENCES {store._run_table}(id), revision BIGINT NOT NULL)")
            cursor.execute(f"CREATE TABLE {history} (run_id UUID NOT NULL REFERENCES {head}(run_id), revision BIGINT NOT NULL, "
                "record JSONB NOT NULL, PRIMARY KEY(run_id,revision))")
            cursor.execute(f"INSERT INTO {head} VALUES (%s,0)", (run.id,))
        control = CandidateCoordination(env.settings, store, run, items, head, history)
        original_claim, original_read, original_refresh = store._claim_items_sync, store._read_run, store._refresh_run_in_cursor

        def coordinated_claim(*args, **kwargs):
            control.thread.actor = "claim:" + (args[1] if len(args) > 1 else kwargs["worker_id"])
            control.thread.locked = False
            try:
                return original_claim(*args, **kwargs)
            finally:
                control.thread.actor = None

        def shared_before_read(cursor, run_id):
            actor = getattr(control.thread, "actor", None)
            if actor and not control.thread.locked:
                control.register(cursor, actor)
                control.event(actor + ":attempted").set()
                cursor.execute(f"SELECT revision FROM {head} WHERE run_id=%s FOR SHARE", (run_id,))
                assert cursor.fetchone() is not None
                control.thread.locked = True
                control.event(actor + ":locked").set()
            return original_read(cursor, run_id)

        def pause_before_totals(cursor, *args, **kwargs):
            actor = getattr(control.thread, "actor", None)
            if actor and control.pause_claims:
                control.event(actor + ":items-written").set()
                control.wait("release-claims")
            return original_refresh(cursor, *args, **kwargs)

        monkeypatch.setattr(store, "_claim_items_sync", coordinated_claim)
        monkeypatch.setattr(store, "_read_run", shared_before_read)
        monkeypatch.setattr(store, "_refresh_run_in_cursor", pause_before_totals)
        try:
            yield control, store, run, items, now
        finally:
            control.event("release-revision").set()
            control.event("release-claims").set()
            with postgres_connect(env.settings) as conn, conn.cursor() as cursor:
                cursor.execute(f"DROP TABLE {history}")
                cursor.execute(f"DROP TABLE {head}")


@pytest.mark.asyncio
async def test_revision_first_claim_waits_and_uses_new_dependencies(tmp_path, monkeypatch):
    async with experiment(tmp_path, monkeypatch) as (control, store, run, items, now):
        revising = asyncio.create_task(asyncio.to_thread(control.revise, pause=True))
        claiming = None
        try:
            await control.reached("revision-written")
            claiming = asyncio.create_task(store.claim_items(run_id=run.id, worker_id="a", limit=5, lease_seconds=60, now=now))
            await control.reached("claim:a:attempted")
            await control.blocked_by("claim:a", "revision")
        finally:
            control.event("release-revision").set()
            await revising
            claimed = await claiming if claiming else []
        assert {item.id for item, _ in claimed} == {item.id for item in items[1:]}
        assert (await store.get_item(items[0].id)).status == "queued"
        revision, history = await asyncio.to_thread(control.head_and_history)
        assert revision == 1 and len(history) == 1
        assert history[0]["before"]["dependencies"] == []
        assert history[0]["after"]["dependencies"][0]["item_id"] == items[1].id


@pytest.mark.asyncio
async def test_claim_first_revision_waits_then_rejects_without_writing(tmp_path, monkeypatch):
    async with experiment(tmp_path, monkeypatch) as (control, store, run, items, now):
        control.pause_claims = True
        claiming = asyncio.create_task(store.claim_items(run_id=run.id, worker_id="a", limit=1, lease_seconds=60, now=now))
        revising = None
        try:
            await control.reached("claim:a:items-written")
            revising = asyncio.create_task(asyncio.to_thread(control.revise))
            await control.reached("revision-attempted")
            await control.blocked_by("revision", "claim:a")
        finally:
            control.event("release-claims").set()
            claimed = await claiming
            if revising:
                with pytest.raises(RevisionConflict, match="already claimed"):
                    await revising
        assert claimed[0][0].id == items[0].id
        assert await asyncio.to_thread(control.head_and_history) == (0, [])
        assert (await store.get_item(items[0].id)).dependencies == []


@pytest.mark.asyncio
async def test_shared_head_keeps_independent_claimants_concurrent(tmp_path, monkeypatch):
    async with experiment(tmp_path, monkeypatch) as (control, store, run, items, now):
        control.pause_claims = True
        tasks = [asyncio.create_task(store.claim_items(run_id=run.id, worker_id=worker, limit=1, lease_seconds=60, now=now))
            for worker in ("a", "b")]
        try:
            await control.reached("claim:a:items-written")
            await control.reached("claim:b:items-written")
        finally:
            control.event("release-claims").set()
            batches = await asyncio.gather(*tasks)
        assert len(batches[0]) == len(batches[1]) == 1
        assert len({batch[0][0].id for batch in batches}) == 2
        detail = await store.get_run(run.id)
        assert detail.run.stats.claimed == 2 and detail.run.stats.queued == 3
        assert len(detail.attempts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["rollback", "stale"])
async def test_candidate_head_history_and_dependencies_are_atomic(tmp_path, monkeypatch, fault):
    async with experiment(tmp_path, monkeypatch) as (control, store, run, items, now):
        before = await store.get_run(run.id)
        if fault == "rollback":
            with pytest.raises(RuntimeError, match="actual failure"):
                await asyncio.to_thread(control.revise, fail=True)
            assert await asyncio.to_thread(control.head_and_history) == (0, [])
            assert await store.get_run(run.id) == before
        else:
            await asyncio.to_thread(control.revise)
            accepted = await store.get_run(run.id)
            with pytest.raises(RevisionConflict, match="stale"):
                await asyncio.to_thread(control.revise)
            assert await store.get_run(run.id) == accepted
            assert (await asyncio.to_thread(control.head_and_history))[0] == 1


@pytest.mark.asyncio
async def test_changed_queued_sibling_keeps_running_context_on_original_archive(tmp_path):
    """Resolve the full archived envelope while preserving all context checks."""
    async with planned(tmp_path, True) as (env, store, _, service, plan, now, _):
        detail = await service.create(plan.project_id, plan.id,
            CasePlanRunRequest(creation_key="revision-context-boundary", mode_key="api_testing"))
        claims = await store.claim_items(run_id=detail.run.id, worker_id="existing", limit=5, lease_seconds=60, now=now)
        item, attempt = next(claim for claim in claims if claim[0].plan_item_key == "independent")
        item = await store.start_item(item.id, attempt.lease_token, now)
        before = await store.get_run(detail.run.id)
        version = await env.cases.get_version(item.case_version_id)
        context = build_agent_case_context(before, item, version)
        changed = before.run.model_copy(deep=True)
        sibling = next(node for node in changed.plan_snapshot.plan.decision.items if node.key == "left")
        assert next(entry for entry in before.items if entry.plan_item_key == sibling.key).status == "queued"
        sibling.reason = "test-only queued sibling revision"
        # Deliberate raw projection change in owned tables, not an implemented
        # revision API. No task is executed under this altered snapshot.
        with postgres_connect(env.settings) as conn, conn.cursor() as cursor:
            store._write_run(cursor, changed)
        after = await store.get_run(detail.run.id)
        # Keep the old observation reproducible for historical, unpinned data.
        historical = after.model_copy(deep=True)
        for entry in historical.attempts:
            entry.plan_snapshot_identity = None
        assert context.execution_json() == build_agent_case_context(historical, item, version).execution_json()
        with pytest.raises(ValueError, match="领取时固定的计划"):
            build_agent_case_context(after, item, version)
        for supplied in (before.run, after.run):
            fixed = await store.get_execution_detail(item.run_id, item.id, attempt.id, supplied_run=supplied)
            assert fixed.run.plan_snapshot == before.run.plan_snapshot
            assert context.execution_json() == build_agent_case_context(fixed, item, version).execution_json()
        assert (await store.get_latest_attempt(item.id)).checkpoint_version == 0
