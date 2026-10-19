"""Actual PostgreSQL claim transactions obey the production revision resource."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic
from uuid import uuid4

import pytest

from src.application.test_runs.plan_revision_lock import lock_plan_projection
from src.infrastructure.postgres_runtime import postgres_connect
from test_run_item_dependencies import setup


pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_RUN_DEPENDENCIES") != "1",
    reason="requires owned actual PostgreSQL/MySQL/RustFS fixtures")


async def waiting_claimant(settings, owner_pid, task):
    def inspect():
        with postgres_connect(settings) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) "
                "AND wait_event_type='Lock' AND wait_event='advisory'", (owner_pid,))
            return cursor.fetchall()

    deadline = monotonic() + 10
    while monotonic() < deadline:
        if rows := await asyncio.to_thread(inspect):
            return rows[0]["pid"]
        assert not task.done(), "claim completed while an exclusive plan revision transaction still held the resource"
        await asyncio.sleep(0.02)
    pytest.fail("PostgreSQL did not observe claimant waiting on the exclusive plan resource")


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["commit", "rollback"])
@pytest.mark.parametrize("alias", [False, True])
async def test_original_claim_waits_for_revision_resource_and_releases_with_transaction(tmp_path, finish, alias):
    async with setup(tmp_path, True) as (env, store, run, items, now):
        await store.create_run(run, items)
        task = None
        try:
            with postgres_connect(env.settings) as writer, writer.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout='10s'")
                cursor.execute("SELECT pg_backend_pid() AS pid")
                owner = cursor.fetchone()["pid"]
                table, run_id = store._run_table, run.id
                if alias:
                    cursor.execute("SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid=c.relnamespace WHERE c.oid=%s::regclass", (table,))
                    relation = cursor.fetchone()
                    table = '.'.join('"' + relation[key].replace('"', '""') + '"' for key in ("nspname", "relname"))
                    run_id = run_id.upper()
                lock_plan_projection(cursor, run_table=table, run_id=run_id, exclusive=True)
                task = asyncio.create_task(store.claim_items(run_id=run.id, worker_id="real-claim",
                    limit=1, lease_seconds=60, now=now))
                assert await waiting_claimant(env.settings, owner, task) != owner
                assert (await store.get_run(run.id)).attempts == []
                if finish == "rollback":
                    writer.rollback()
        finally:
            if task is not None:
                claims = await task
        assert len(claims) == 1
        saved = await store.get_run(run.id)
        assert len(saved.attempts) == 1 and saved.run.stats.claimed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("other_resource", ["run", "table"])
async def test_different_run_or_physical_table_does_not_block_claim(tmp_path, other_resource):
    async with setup(tmp_path, True) as (env, store, run, items, now):
        await store.create_run(run, items)
        with postgres_connect(env.settings) as writer, writer.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout='10s'")
            # The fixture suite is a distinct physical relation in the same DB.
            table = store._run_table if other_resource == "run" else env.settings.database.postgres_test_suite_table
            run_id = str(uuid4()) if other_resource == "run" else run.id
            lock_plan_projection(cursor, run_table=table, run_id=run_id, exclusive=True)
            claims = await asyncio.wait_for(store.claim_items(run_id=run.id, worker_id="unrelated-resource",
                limit=1, lease_seconds=60, now=now), timeout=5)
            assert len(claims) == 1
            assert (await store.get_run(run.id)).run.stats.claimed == 1


LOCK_OWNER_CODE = '''
import sys
from src.core.config import get_settings
from src.application.test_runs.plan_revision_lock import lock_plan_projection
from src.infrastructure.postgres_runtime import postgres_connect
with postgres_connect(get_settings()) as conn, conn.cursor() as cursor:
    lock_plan_projection(cursor, run_table=sys.argv[1], run_id=sys.argv[2], exclusive=True)
    cursor.execute("SELECT pg_backend_pid() AS pid")
    print(cursor.fetchone()["pid"], flush=True)
    sys.stdin.readline()
'''


@pytest.mark.asyncio
async def test_killing_revision_process_releases_resource_without_partial_attempt(tmp_path):
    async with setup(tmp_path, True) as (env, store, run, items, now):
        await store.create_run(run, items)
        # Same subprocess/pipe contract as test_metrics_multiprocess. Only owned
        # relation/Run identities cross argv; credentials stay in get_settings.
        process = subprocess.Popen([sys.executable, "-Xutf8", "-u", "-c", LOCK_OWNER_CODE, store._run_table, run.id],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        task = None
        try:
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=15)
            assert line.strip().isdigit(), "revision process did not publish its PostgreSQL backend PID"
            owner = int(line)
            task = asyncio.create_task(store.claim_items(run_id=run.id, worker_id="after-process-loss",
                limit=1, lease_seconds=60, now=now))
            await waiting_claimant(env.settings, owner, task)
            assert (await store.get_run(run.id)).attempts == []
        finally:
            if process.poll() is None:
                process.kill()
            stdout, stderr = await asyncio.to_thread(process.communicate, timeout=10)
            if task is not None:
                claims = await asyncio.wait_for(task, timeout=10)
            assert process.returncode != 0, (stdout, stderr)
            assert stdout == "" and stderr == "", (stdout, stderr)
        assert len(claims) == 1
        saved = await store.get_run(run.id)
        assert len(saved.attempts) == 1 and saved.run.stats.claimed == 1
