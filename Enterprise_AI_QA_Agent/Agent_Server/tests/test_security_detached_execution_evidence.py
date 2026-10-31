"""ToolJob termination is not evidence that its external command stopped.

The Docker transport is controlled; its external operation is a real isolated
local Python process. This does not exercise Docker or a security target.
"""
from __future__ import annotations

import asyncio
import logging
import os
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.application.security.execution_environment_service import (
    SecurityCommandExecutionResult,
    SecurityExecutionEnvironmentService,
)
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_job import ToolJobStatus


CONTAINER_ID = "a" * 64
OWNER = SimpleNamespace(session_id="detached-evidence", turn_id="turn-evidence")


def _settings():
    return SimpleNamespace(
        security=SimpleNamespace(security_runner_backend="docker",
            security_runner_detach_poll_interval_seconds=".1"),
        orchestration=SimpleNamespace(resource_docker_command_timeout_seconds=2),
    )


class _ProcessDocker:
    def __init__(self, *, defer_cleanup=False):
        self.defer_cleanup = defer_cleanup
        self.process = None
        self.executing = asyncio.Event()
        self.cleanup_entered = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()
        self.commands = []

    async def run(self, args, *, name_prefix, operation):
        self.mount = Path(args[args.index("-v") + 1].rsplit(":", 1)[0])
        try:
            return await operation(SimpleNamespace(container_id=CONTAINER_ID,
                container_name="evidence-container"))
        finally:
            self.cleanup_entered.set()
            await self.cleanup_release.wait()
            if self.defer_cleanup:
                raise RuntimeError("controlled_cleanup_deferred")
            await self.remove_external_process()

    async def _command(self, args, *, timeout):
        self.commands.append(list(args))
        if args[0] == "wait":
            await asyncio.Event().wait()
        if args[0] == "exec":
            self.process = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c",
                "import time; print('external-ready', flush=True); time.sleep(60)",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            assert (await asyncio.wait_for(self.process.stdout.readline(), 5)).strip() == b"external-ready"
            next((self.mount / ".jobs").glob("*.log")).write_text("external-ready\n", encoding="utf-8")
            self.executing.set()
            # Cancelling the client wait must not kill the external process.
            await asyncio.shield(self.process.wait())
        if args[0] == "ps":
            assert args == ["ps", "--all", "--no-trunc", "--filter",
                f"id={CONTAINER_ID}", "--format", "{{.ID}}"]
            return 0, CONTAINER_ID if self.process.returncode is None else "", ""
        return 0, "", ""

    async def remove_external_process(self):
        if self.process is not None:
            if self.process.returncode is None:
                self.process.kill()
            stdout, stderr = await asyncio.wait_for(self.process.communicate(), 5)
            assert not stderr, (stdout, stderr)


@pytest.mark.asyncio
async def test_cancelled_tool_job_does_not_prove_live_external_command_stopped(tmp_path):
    docker = _ProcessDocker()
    jobs = ToolJobService(InMemoryToolJobStore())
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
    try:
        started = await service.execute_detached(command="sleep 60", command_args=["sleep", "60"],
            timeout_seconds=65, artifact_dir=tmp_path, context=OWNER)
        await asyncio.wait_for(docker.executing.wait(), 5)
        await jobs.cancel_job(started.job_id)
        before = deepcopy((await jobs.get_job(started.job_id)).model_dump())
        result = await service.poll_execution(started.job_id, context=OWNER)
        assert docker.process.returncode is None
        assert result.is_running is None
        assert (await jobs.get_job(started.job_id)).model_dump() == before
    finally:
        await service.close()
        await docker.remove_external_process()


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True], ids=["memory", "postgres"])
async def test_deferred_cleanup_keeps_unknown_until_exact_target_absence(tmp_path, durable):
    if durable and os.environ.get("RUN_LIVE_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_LIVE_POSTGRES_TESTS=1 for durable detached execution observations")
    store = InMemoryToolJobStore()
    if durable:
        from src.core.config import get_settings
        from src.runtime.postgres_tool_job_store import PostgresToolJobStore

        pg_settings = get_settings()
        store = PostgresToolJobStore(pg_settings)
        await store.initialize()
    docker = _ProcessDocker(defer_cleanup=True)
    jobs = ToolJobService(store)
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
    job_id = ""
    try:
        started = await service.execute_detached(command="sleep 60", command_args=["sleep", "60"],
            timeout_seconds=65, artifact_dir=tmp_path, context=OWNER)
        job_id = started.job_id
        task = service._detached_jobs[started.job_id].task
        await asyncio.wait_for(docker.executing.wait(), 5)
        await jobs.cancel_job(started.job_id)
        await asyncio.wait_for(task, 5)
        saved = await jobs.get_job(started.job_id)
        assert saved.status == ToolJobStatus.cancelled
        assert saved.output_payload["is_running"] is None
        assert docker.process.returncode is None
        remote = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
        before = deepcopy(saved.model_dump())
        assert (await remote.poll_execution(started.job_id, context=OWNER)).is_running is None
        await docker.remove_external_process()
        stopped = await remote.poll_execution(started.job_id, context=OWNER)
        assert stopped.is_running is False and stopped.exit_code == 130
        assert (await jobs.get_job(started.job_id)).model_dump() == before
        assert len([args for args in docker.commands if args[0] == "exec"]) == 1
    finally:
        await service.close()
        await docker.remove_external_process()
        if durable and job_id:
            from psycopg import sql
            from src.infrastructure.postgres_runtime import postgres_connect

            with postgres_connect(pg_settings) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(
                    sql.Identifier(pg_settings.database.postgres_tool_job_table)), (job_id,))


@pytest.mark.asyncio
async def test_interrupted_supervisor_does_not_settle_before_cleanup_observation(tmp_path):
    docker = _ProcessDocker()
    docker.cleanup_release.clear()
    jobs = ToolJobService(InMemoryToolJobStore())
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
    try:
        started = await service.execute_detached(command="sleep 60", command_args=["sleep", "60"],
            timeout_seconds=65, artifact_dir=tmp_path, context=OWNER)
        task = service._detached_jobs[started.job_id].task
        await asyncio.wait_for(docker.executing.wait(), 5)
        task.cancel()
        await asyncio.wait_for(docker.cleanup_entered.wait(), 5)
        saved = await jobs.get_job(started.job_id)
        assert not saved.output_payload
        assert docker.process.returncode is None
        assert (await service.poll_execution(started.job_id, context=OWNER)).is_running is None
        docker.cleanup_release.set()
        await asyncio.wait_for(task, 5)
        result = await service.poll_execution(started.job_id, context=OWNER)
        assert result.is_running is False and result.exit_code == 130
    finally:
        docker.cleanup_release.set()
        await service.close()
        await docker.remove_external_process()


async def _historical_job(jobs, *, container_id=CONTAINER_ID, output=False, status="resume_requested"):
    job = await jobs.create_job(ToolDescriptor(key="security-background-command", name="test",
        description="test", category="security"), call_id="historical", session_id=OWNER.session_id,
        turn_id=OWNER.turn_id, trace_id="", input_payload={})
    now = datetime.now(timezone.utc)
    # Persist the actual detached writer's target-bearing argv shape. A full
    # metadata ID alone is not evidence that this snapshot executed on it.
    argv = ["docker", "exec", "-w", "/work", CONTAINER_ID, "sh", "-lc", "sleep 60"]
    payload = asdict(SecurityCommandExecutionResult(backend="docker", command="sleep 60", argv=argv,
        cwd="/work", stdout="original", stderr="original-error", exit_code=130 if output else -1,
        timed_out=False, started_at=now, completed_at=now, job_id=job.id, detached=True,
        is_running=not output, container_name="do-not-query-by-name"))
    # Freeze the actual pre-change snapshot shape, rather than using a new
    # dataclass default as evidence that old records had completion receipts.
    payload.pop("execution_observation", None)
    job.metadata.update(initial_result=payload, container_id=container_id)
    await jobs.mark_running(job)
    if status == "resume_requested":
        await jobs.request_resume(job.id)
    elif status == "cancelled":
        await jobs.cancel_job(job.id)
    if output:
        # PostgreSQL persists these as separate JSON values. Do not let a
        # shared dict make output corruption rewrite the prepared snapshot.
        job.output_payload = deepcopy(payload)
    return job


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [False, True])
@pytest.mark.parametrize("query", ["present", "absent", "failed", "exception", "malformed"])
@pytest.mark.parametrize("status", ["running", "resume_requested", "cancelled"])
async def test_orphan_and_historical_snapshots_require_read_only_target_evidence(output, query, status):
    class Docker:
        queries = 0

        async def _command(self, args, *, timeout):
            self.queries += 1
            assert args == ["ps", "--all", "--no-trunc", "--filter",
                f"id={CONTAINER_ID}", "--format", "{{.ID}}"]
            if query == "exception":
                raise OSError("controlled_daemon_unavailable")
            return {"present": (0, CONTAINER_ID, ""), "absent": (0, "", ""),
                "failed": (1, "", "daemon unavailable"), "malformed": (0, "unexpected", "")}[query]

    jobs = ToolJobService(InMemoryToolJobStore())
    job = await _historical_job(jobs, output=output, status=status)
    before = deepcopy(job.model_dump())
    docker = Docker()
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
    result = await service.poll_execution(job.id, context=OWNER)
    assert result.is_running is (False if query == "absent" else None)
    assert docker.queries == 1
    assert result.stdout == "original"
    assert (await jobs.get_job(job.id)).model_dump() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("container_id", ["", "a" * 12, "do-not-query-by-name"])
async def test_unknown_execution_never_queries_mutable_or_missing_container_identity(container_id):
    calls = []

    class Docker:
        async def _command(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("No immutable target identity")

    jobs = ToolJobService(InMemoryToolJobStore())
    job = await _historical_job(jobs, container_id=container_id)
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=Docker(), tool_jobs=jobs)
    assert (await service.poll_execution(job.id, context=OWNER)).is_running is None
    assert calls == []


@pytest.mark.asyncio
async def test_unknown_target_query_requires_the_original_session_before_io():
    calls = []

    class Docker:
        async def _command(self, *args, **kwargs):
            calls.append((args, kwargs))
            return 0, "", ""

    jobs = ToolJobService(InMemoryToolJobStore())
    job = await _historical_job(jobs)
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=Docker(), tool_jobs=jobs)
    with pytest.raises(ValueError, match="does not belong"):
        await service.poll_execution(job.id, context=SimpleNamespace(session_id="foreign"))
    assert calls == []


@pytest.mark.asyncio
async def test_immediate_shutdown_preserves_non_dispatch_evidence(tmp_path):
    docker = _ProcessDocker()
    jobs = ToolJobService(InMemoryToolJobStore())
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
    started = await service.execute_detached(command="sleep 60", command_args=["sleep", "60"],
        timeout_seconds=65, artifact_dir=tmp_path, context=OWNER)
    await service.close()
    result = await service.poll_execution(started.job_id, context=OWNER)
    assert result.is_running is False and result.execution_observation == "not_dispatched"
    assert result.exit_code == 130
    assert not any(args[0] == "exec" for args in docker.commands)
    assert docker.process is None


@pytest.mark.asyncio
async def test_unknown_target_query_logs_context_without_exception_text(caplog):
    class Docker:
        async def _command(self, args, *, timeout):
            raise OSError("private-query-diagnostic")

    jobs = ToolJobService(InMemoryToolJobStore())
    job = await _historical_job(jobs)
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=Docker(), tool_jobs=jobs)
    with caplog.at_level(logging.WARNING):
        result = await service.poll_execution(job.id, context=OWNER)
    assert result.is_running is None
    record = next(row for row in caplog.records if row.message == "security_background_state_unknown")
    assert record.job_id == job.id and record.container_id == CONTAINER_ID
    assert record.reason == "target_query_failed" and record.error_type == "OSError"
    assert "private-query-diagnostic" not in caplog.text and record.exc_info is None


@pytest.mark.asyncio
@pytest.mark.parametrize("observation", ["command_returned", "container_absent", "not_dispatched"])
@pytest.mark.parametrize("drift", ["job_id", "metadata_target", "snapshot_target", "missing_target",
    "prepared_target", "metadata_and_snapshot_target", "missing_prepared_identity"])
async def test_terminal_observation_cannot_cross_execution_identity(observation, drift):
    queries = []

    class Docker:
        async def _command(self, args, *, timeout):
            queries.append(args)
            return 0, "", ""

    jobs = ToolJobService(InMemoryToolJobStore())
    job = await _historical_job(jobs, output=True)
    job.output_payload["execution_observation"] = observation
    if drift == "job_id":
        job.output_payload["job_id"] = "another-job"
    elif drift == "metadata_target":
        job.metadata["container_id"] = "b" * 64
    elif drift == "snapshot_target":
        job.output_payload["argv"][4] = "b" * 64
    elif drift == "missing_target":
        job.output_payload["argv"] = []
    elif drift == "prepared_target":
        job.metadata["initial_result"]["argv"][4] = "b" * 64
    elif drift == "metadata_and_snapshot_target":
        job.metadata["container_id"] = "b" * 64
        job.output_payload["argv"][4] = "b" * 64
    else:
        job.metadata.pop("initial_result")
    before = deepcopy(job.model_dump())
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=Docker(), tool_jobs=jobs)
    result = await service.poll_execution(job.id, context=OWNER)
    assert result.is_running is None and result.execution_observation == "unknown"
    assert queries == []
    assert (await jobs.get_job(job.id)).model_dump() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("observation", ["command_returned", "container_absent", "not_dispatched"])
async def test_consistent_terminal_observation_reuses_its_original_identity_without_query(observation):
    queries = []

    class Docker:
        async def _command(self, args, *, timeout):
            queries.append(args)
            raise AssertionError("A known observation does not need to query or repeat the command")

    jobs = ToolJobService(InMemoryToolJobStore())
    job = await _historical_job(jobs, output=True)
    job.output_payload["execution_observation"] = observation
    before = deepcopy(job.model_dump())
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=Docker(), tool_jobs=jobs)
    result = await service.poll_execution(job.id, context=OWNER)
    assert result.is_running is False and result.execution_observation == observation
    assert queries == []
    assert (await jobs.get_job(job.id)).model_dump() == before


@pytest.mark.asyncio
async def test_historical_snapshot_without_target_does_not_query_metadata_identity_alone():
    queries = []

    class Docker:
        async def _command(self, args, *, timeout):
            queries.append(args)
            return 0, "", ""

    jobs = ToolJobService(InMemoryToolJobStore())
    job = await _historical_job(jobs)
    job.metadata["initial_result"]["argv"] = []
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=Docker(), tool_jobs=jobs)
    result = await service.poll_execution(job.id, context=OWNER)
    assert result.is_running is None
    assert queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change_snapshot", [False, True])
async def test_live_supervision_does_not_authorize_a_changed_target(tmp_path, change_snapshot):
    docker = _ProcessDocker()
    jobs = ToolJobService(InMemoryToolJobStore())
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
    try:
        started = await service.execute_detached(command="sleep 60", command_args=["sleep", "60"],
            timeout_seconds=65, artifact_dir=tmp_path, context=OWNER)
        await asyncio.wait_for(docker.executing.wait(), 5)
        job = await jobs.get_job(started.job_id)
        job.metadata["container_id"] = "b" * 64
        if change_snapshot:
            service._detached_jobs[started.job_id].result.argv[4] = "b" * 64
        before = deepcopy(job.model_dump())
        result = await service.poll_execution(started.job_id, context=OWNER)
        assert result.is_running is None
        assert docker.process.returncode is None
        assert not any(args[0] == "ps" for args in docker.commands)
        assert (await jobs.get_job(job.id)).model_dump() == before
    finally:
        await service.close()
        await docker.remove_external_process()


@pytest.mark.asyncio
async def test_supervisor_settlement_identity_does_not_follow_mutable_result(tmp_path):
    docker = _ProcessDocker()
    jobs = ToolJobService(InMemoryToolJobStore())
    foreign = await _historical_job(jobs, status="running")
    foreign_before = deepcopy(foreign.model_dump())
    heartbeat = jobs.heartbeat_execution
    heartbeat_seen, heartbeats = asyncio.Event(), []

    async def observe_heartbeat(job_id):
        heartbeats.append(job_id)
        heartbeat_seen.set()
        return await heartbeat(job_id)

    jobs.heartbeat_execution = observe_heartbeat
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
    try:
        started = await service.execute_detached(command="sleep 60", command_args=["sleep", "60"],
            timeout_seconds=65, artifact_dir=tmp_path, context=OWNER)
        await asyncio.wait_for(docker.executing.wait(), 5)
        record = service._detached_jobs[started.job_id]
        record.result = replace(record.result, job_id=foreign.id)
        heartbeats.clear()
        heartbeat_seen.clear()
        await asyncio.wait_for(heartbeat_seen.wait(), 5)
        assert heartbeats and set(heartbeats) == {started.job_id}
        await service.close()
        assert (await jobs.get_job(foreign.id)).model_dump() == foreign_before
        assert (await jobs.get_job(started.job_id)).status == ToolJobStatus.cancelled
        assert (await jobs.get_job(started.job_id)).output_payload["is_running"] is None
    finally:
        await service.close()
        await docker.remove_external_process()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["job_id", "metadata_and_snapshot_target", "prepared_target"])
async def test_postgres_terminal_observation_keeps_prepared_execution_binding(tmp_path, drift):
    if os.environ.get("RUN_LIVE_POSTGRES_TESTS") != "1":
        pytest.skip("set RUN_LIVE_POSTGRES_TESTS=1 for durable execution identity checks")
    from psycopg import sql
    from src.core.config import get_settings
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore

    settings = get_settings()
    store = PostgresToolJobStore(settings)
    await store.initialize()
    jobs = ToolJobService(store)
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=_ProcessDocker(), tool_jobs=jobs)
    queries = []

    class Docker:
        async def _command(self, args, *, timeout):
            queries.append(args)
            return 0, "", ""

    job_id = ""
    try:
        started = await service.execute_detached(command="sleep 60", command_args=["sleep", "60"],
            timeout_seconds=65, artifact_dir=tmp_path, context=OWNER)
        job_id = started.job_id
        await service.close()
        job = await jobs.get_job(job_id)
        assert job.status == ToolJobStatus.cancelled
        assert job.output_payload["execution_observation"] == "not_dispatched"
        if drift == "job_id":
            job.output_payload["job_id"] = "another-job"
        elif drift == "metadata_and_snapshot_target":
            job.metadata["container_id"] = "b" * 64
            job.output_payload["argv"][4] = "b" * 64
            assert job.metadata["initial_result"]["argv"][4] == CONTAINER_ID
        else:
            job.metadata["initial_result"]["argv"][4] = "b" * 64
        await store.save_job(job)
        before = (await jobs.get_job(job_id)).model_dump(mode="json")
        remote = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=Docker(),
            tool_jobs=ToolJobService(PostgresToolJobStore(settings)))
        result = await remote.poll_execution(job_id, context=OWNER)
        assert result.is_running is None and result.execution_observation == "unknown"
        assert queries == []
        assert (await jobs.get_job(job_id)).model_dump(mode="json") == before
    finally:
        await service.close()
        if job_id:
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(
                    sql.Identifier(settings.database.postgres_tool_job_table)), (job_id,))


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["start", "live_poll", "poll"])
async def test_detached_public_views_do_not_expose_owned_mutable_state(tmp_path, boundary):
    docker = _ProcessDocker()
    jobs = ToolJobService(InMemoryToolJobStore())
    service = SecurityExecutionEnvironmentService(settings=_settings(), docker_resources=docker, tool_jobs=jobs)
    try:
        started = await service.execute_detached(command="sleep 60", command_args=["sleep", "60"],
            timeout_seconds=65, artifact_dir=tmp_path, context=OWNER)
        job_id = started.job_id
        await asyncio.wait_for(docker.executing.wait(), 5)
        if boundary == "start":
            record = service._detached_jobs[job_id]
            before = deepcopy(asdict(record.result))
            started.job_id = "view-only-job"
            started.argv[4] = "b" * 64
            started.output_artifacts.append({"path": "view-only"})
            assert asdict(record.result) == before
        elif boundary == "live_poll":
            record = service._detached_jobs[job_id]
            record.result.output_artifacts.append({"metadata": {"source": "owned"}})
            before = deepcopy(asdict(record.result))
            view = await service.poll_execution(job_id, context=OWNER)
            view.argv[4] = "b" * 64
            view.output_artifacts[0]["metadata"]["source"] = "view-only"
            assert asdict(record.result) == before
        else:
            await service.close()
            before = deepcopy((await jobs.get_job(job_id)).model_dump())
            view = await service.poll_execution(job_id, context=OWNER)
            view.argv[4] = "b" * 64
            view.output_artifacts[0]["path"] = "view-only"
            view.output_artifacts.append({"path": "view-only"})
            assert (await jobs.get_job(job_id)).model_dump() == before
    finally:
        await service.close()
        await docker.remove_external_process()
