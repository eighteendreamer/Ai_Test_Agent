"""Committed writes with lost responses survive real process replacement.

Uses owned PostgreSQL tables and actual local HTTP/ToolRuntime calls. Worker
launching is controlled; this is not external-model or full Agent acceptance.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
from threading import Thread
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from src.application.projects.project_store import PostgresProjectStore
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.core.config import get_settings
from src.domain.models import SessionRecord
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.api_testing_mode.campaign_state import (
    ApiTestCampaign, ApiTestTask, ApiTestingState, AssertionSpec, ExecutionPolicy,
)
from src.modes.api_testing_mode.contracts import PHASE_TASK_RUNNING, STATE_METADATA_KEY
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
from src.modes.api_testing_mode.report_builder import ReportBuilder
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.project import ProjectRecord
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from test_execution_worker_cli import Processes


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf8")


@asynccontextmanager
async def isolated_pg_runtime(tmp_path, evidence):
    # Existing counterparts: test_agent_case_session.session_stores and
    # test_case_json_templates._stores. This service test has no queue dependency.
    settings = get_settings().model_copy(deep=True)
    settings.data_dir = str(tmp_path / "data")
    settings.orchestration.redis_task_dispatch_enabled = False
    settings.orchestration.redis_vector_enabled = False
    settings.langsmith.enabled = False
    suffix = uuid4().hex[:16]
    tables = []
    for kind in ("project", "session", "message", "event", "snapshot", "approval", "tool_job", "tool_artifact"):
        table = f"up_uw_{kind}_{suffix}"
        setattr(settings.database, f"postgres_{kind}_table", table)
        tables.append(table)
    private_tables = PostgresApiCredentialStore(settings).tables
    resource_table = f"up_uw_resource_{suffix}"
    owned_tables = [*private_tables, *reversed(tables), resource_table]
    assert all(re.fullmatch(r"up_uw_[a-z_]+_[0-9a-f]{16}(?:_api_[a-z_]+)?", table) for table in owned_tables)
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        for table in owned_tables:
            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
            assert cur.fetchone()["existing"] is None
    processes = Processes(evidence, settings)
    projects = PostgresProjectStore(settings)
    sessions = PostgresSessionStore(settings)
    jobs = ToolJobService(PostgresToolJobStore(settings))
    references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
    try:
        await projects.initialize()
        await sessions.initialize()
        await jobs.initialize()
        await references.initialize()
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            # No unique business-key constraint: duplicate requests would create
            # duplicate rows rather than being hidden by target idempotency.
            cur.execute(f"CREATE TABLE {resource_table} (id UUID PRIMARY KEY, business_key TEXT NOT NULL)")
        yield settings, processes, projects, sessions, jobs, resource_table
    finally:
        await processes.close()
        with postgres_connect(settings) as conn, conn.cursor() as cur:
            for table in owned_tables:
                cur.execute(f'DROP TABLE IF EXISTS "{table}"')
            for table in owned_tables:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cur.fetchone()["existing"] is None
        write_json(evidence / "cleanup.json", {"owned_tables_removed": owned_tables, "docker_used": False})


@contextmanager
def committed_write_target(settings, resource_table):
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def reply(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            identity = str(uuid4())
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(f"INSERT INTO {resource_table} (id, business_key) VALUES (%s, %s)",
                    (identity, body["business_key"]))
            event = {"method": self.command, "path": self.path, "resource_id": identity,
                "business_key": body["business_key"], "database_committed": True}
            events.append(event)
            # The mutation commits before the client times out waiting for its response.
            time.sleep(.5)
            try:
                self.reply(201, {"id": identity, "business_key": body["business_key"]})
                event["response_socket_error"] = False
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                event["response_socket_error"] = True

        def do_GET(self):
            key = parse_qs(urlsplit(self.path).query)["key"][0]
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT id, business_key FROM {resource_table} WHERE business_key=%s", (key,))
                rows = [dict(row) for row in cur.fetchall()]
            events.append({"method": self.command, "path": self.path, "database_row_count": len(rows)})
            self.reply(200 if len(rows) == 1 else 409, {"id": str(rows[0]["id"]) if rows else None,
                "business_key": key, "count": len(rows)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


async def process_unknown_campaign(parent_id, turn_id, pause_at):
    settings = get_settings()
    sessions = PostgresSessionStore(settings)
    jobs = ToolJobService(PostgresToolJobStore(settings))
    references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
    parent = await sessions.get_session(parent_id)
    assert parent is not None
    state = ApiTestingState.model_validate(parent.metadata[STATE_METADATA_KEY])
    manager = CredentialManager()
    runtime = ToolRuntimeService(tool_job_service=jobs, session_store=sessions, settings=settings,
        api_credential_reference_service=references)
    launcher = ControlledWorkerLauncher(sessions, runtime)
    projection = ApiTestingModeRuntime(api_docs_service=None, settings=settings, session_store=sessions,
        credential_reference_service=references, tool_job_service=jobs)
    projection._credential_manager = manager
    context = ToolExecutionContext(session_id=parent_id, turn_id=turn_id, trace_id=turn_id,
        user_message="验证写请求超时后的只读查证", normalized_input="验证写请求超时后的只读查证", context_bundle={})

    async def persist(tasks, marker, report=None):
        state.campaign.tasks = list(tasks)
        payload = projection._state_payload(state)
        probe = {"pid": os.getpid(), "marker": marker,
            "task_statuses": {task.task_id: task.status for task in tasks}}
        if report is not None:
            probe["report"] = report
        await sessions.mutate_metadata(parent_id,
            lambda metadata: metadata.update({STATE_METADATA_KEY: payload, "unknown_write_probe": probe}))

    async def park(tasks, marker):
        await persist(tasks, marker)
        print(json.dumps({"pid": os.getpid(), "parked_at": marker}), flush=True)
        await asyncio.Event().wait()

    class ParkedCoordinator(ApiSubagentCoordinator):
        async def _merge_session_result(self, task, session):
            if pause_at == "before_merge" and task.task_id == "create":
                await park(self._pool.all_tasks, pause_at)
            return await super()._merge_session_result(task, session)

        async def _dispatch_batch(self, batch):
            await super()._dispatch_batch(batch)
            if pause_at == "after_write" and any(task.task_id == "create" for task in batch):
                await park(self._pool.all_tasks, pause_at)

    coordinator = ParkedCoordinator(pool=ApiTaskPool(state.campaign.tasks), policy=state.campaign.execution_policy,
        coordinator_runtime_service=launcher, session_store=sessions,
        parent_context={"session_id": parent_id, "turn_id": turn_id}, credential_manager=manager,
        credential_reference_service=references, tool_job_service=jobs,
        checkpoint_callback=projection._build_checkpoint_callback(state, context))
    results = await coordinator.run_all()
    report = ReportBuilder().build(campaign=state.campaign)
    await persist(results, "completed", report.model_dump(mode="json"))
    print(json.dumps({"pid": os.getpid(), "task_statuses": [task.status for task in results]}), flush=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_at", ["before_merge", "after_write"])
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_UNKNOWN_WRITE_RECOVERY") != "1",
    reason="requires actual PostgreSQL, local HTTP and real process kill/restart; no Redis/Docker")
async def test_committed_write_timeout_is_reconciled_after_real_process_kill(tmp_path, pause_at):
    evidence = Path(os.getenv("QA_API_UNKNOWN_WRITE_RECOVERY_DIR", str(tmp_path / "evidence"))) / pause_at
    evidence.mkdir(parents=True, exist_ok=False)
    facts = {"scope": "actual PostgreSQL/HTTP/ToolRuntime and process kill; controlled Worker launch",
        "pause_at": pause_at, "external_ai_calls": 0, "docker_used": False, "full_goal_complete": False}
    async with isolated_pg_runtime(tmp_path, evidence) as (settings, processes, projects, sessions, jobs, resource_table):
        with committed_write_target(settings, resource_table) as (base, events):
            try:
                now = datetime.now(UTC)
                project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="unknown-write-recovery",
                    name="Unknown committed write recovery", created_at=now, updated_at=now))
                key = uuid4().hex
                tasks = [
                    ApiTestTask(task_id="create", method="POST", path=base+"/orders", full_url=base+"/orders",
                        execution_mode="write", request_body={"business_key": key}, timeout_seconds=.1, status="ready"),
                    ApiTestTask(task_id="check", method="GET", path=base+"/orders?key="+key,
                        full_url=base+"/orders?key="+key, execution_mode="read", status="blocked",
                        reconciliation_for="create", reconciliation_observation="effect_present", timeout_seconds=3,
                        assertions=[AssertionSpec(kind="status_code", expected=200),
                            AssertionSpec(kind="json_field_equals", path="business_key", expected=key),
                            AssertionSpec(kind="json_field_equals", path="count", expected=1),
                            AssertionSpec(kind="json_field_present", path="id")]),
                    ApiTestTask(task_id="after", method="POST", path=base+"/orders/dependent",
                        full_url=base+"/orders/dependent", execution_mode="write", status="blocked", depends_on=["create"]),
                ]
                state = ApiTestingState(phase=PHASE_TASK_RUNNING,
                    campaign=ApiTestCampaign(campaign_id=str(uuid4()), project_id=project.id, tasks=tasks,
                        execution_policy=ExecutionPolicy(max_workers=1, max_retries=2, request_timeout_seconds=3)))
                parent = SessionRecord(id=str(uuid4()), title="Unknown write parent", project_id=project.id,
                    status=SessionStatus.running, session_mode=SessionMode.background_task,
                    runtime_mode=RuntimeMode.background, mode_key="api_testing", created_at=now, updated_at=now,
                    metadata={STATE_METADATA_KEY: state.model_dump(mode="json")})
                await sessions.save_session(parent)
                args = (str(Path(__file__).resolve()), "--resume-unknown-write", parent.id, str(uuid4()))
                first = processes.start("unknown-before", *args, pause_at)
                deadline = asyncio.get_running_loop().time() + 45
                while True:
                    saved = await sessions.get_session(parent.id)
                    if saved.metadata.get("unknown_write_probe", {}).get("marker") == pause_at:
                        break
                    assert first.poll() is None, (evidence / "unknown-before.stderr").read_text(encoding="utf8")
                    assert asyncio.get_running_loop().time() < deadline, "Unknown-write recovery boundary was not observed"
                    await asyncio.sleep(.1)
                before = ApiTestingState.model_validate(saved.metadata[STATE_METADATA_KEY]).campaign.tasks[0]
                assert before.status == ("running" if pause_at == "before_merge" else "failed")
                assert before.attempts == 1 and before.worker_session_id
                old_jobs = await jobs.list_jobs(session_id=before.worker_session_id)
                assert len(old_jobs) == 1
                original_job = old_jobs[0].model_dump(mode="json")
                assert original_job["status"] == "completed"
                unknown = original_job["output_payload"]["task_result"]
                assert unknown["status"] == "failed" and unknown["request_outcome"] == "unknown"
                assert unknown["response_status"] is None and unknown["attempts"] == 1
                assert [event["method"] for event in events] == ["POST"]
                assert events[0]["database_committed"]
                write_json(evidence / "before-kill.json", {"parent_metadata": saved.metadata, "original_job": original_job})
                first.kill()
                first_exit = await processes.wait(first, timeout=15)
                assert first_exit != 0
                facts["forced_exit_code"] = first_exit

                second = processes.start("unknown-after", *args, "")
                assert await processes.wait(second, timeout=45) == 0, (
                    evidence / "unknown-after.stderr").read_text(encoding="utf8")
                final = await sessions.get_session(parent.id)
                assert final.metadata["unknown_write_probe"]["marker"] == "completed"
                results = ApiTestingState.model_validate(final.metadata[STATE_METADATA_KEY]).campaign.tasks
                assert [task.status for task in results] == ["failed", "completed", "skipped"]
                assert results[0].request_outcome == "unknown" and results[0].reconciliation_status == "effect_present"
                assert [task.attempts for task in results] == [1, 1, 0]
                assert results[1].response_body["id"] == events[0]["resource_id"]
                assert results[1].response_body["business_key"] == key
                assert all(check["passed"] for check in results[1].check_results)
                assert [event["method"] for event in events] == ["POST", "GET"]
                assert (await jobs.get_job(old_jobs[0].id)).model_dump(mode="json") == original_job
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT id, business_key FROM {resource_table} WHERE business_key=%s", (key,))
                    resources = [dict(row) for row in cur.fetchall()]
                assert len(resources) == 1 and str(resources[0]["id"]) == results[1].response_body["id"]
                report = final.metadata["unknown_write_probe"]["report"]
                assert (report["completed"], report["failed"], report["skipped"]) == (1, 1, 1)
                assert report["tasks"][0]["request_outcome"] == "unknown"
                assert report["tasks"][0]["reconciliation_status"] == "effect_present"
                assert report["tasks"][0]["reconciliation_required"] is False
                write_json(evidence / "after-recovery.json", {"parent_metadata": final.metadata,
                    "database_resources": resources, "report": report})

                replay = processes.start("unknown-replay", *args, "")
                assert await processes.wait(replay, timeout=45) == 0, (
                    evidence / "unknown-replay.stderr").read_text(encoding="utf8")
                assert len(events) == 2
                assert (await jobs.get_job(old_jobs[0].id)).model_dump(mode="json") == original_job
                final_jobs = await jobs.list_jobs()
                assert len(final_jobs) == 2
                facts.update({"terminal_replay_extra_http": 0, "original_job_unchanged": True,
                    "business_rows": len(resources), "worker_job_count": len(final_jobs),
                    "task_statuses": [task.status for task in results],
                    "attempts": [task.attempts for task in results], "reconciliation_status": results[0].reconciliation_status})
                write_json(evidence / "original-jobs.json", [job.model_dump(mode="json") for job in final_jobs])
            finally:
                facts["events"] = events
                write_json(evidence / "verification.json", facts)


if __name__ == "__main__":
    if sys.argv[1] == "--resume-unknown-write":
        asyncio.run(process_unknown_campaign(sys.argv[2], sys.argv[3], sys.argv[4]))
