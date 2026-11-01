"""Real PostgreSQL admission; planning/configuration/object responses are doubles."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import pytest_asyncio
from langchain_core.messages import HumanMessage

from src.application.deep_agents.framework_transcript import serialize_messages
from src.application.projects.project_store import PostgresProjectStore
from src.application.test_cases.case_store import PostgresTestCaseStore
from src.application.test_cases.planning_store import PostgresCasePlanStore
from src.application.test_runs.controlled_case_preparation import prepare_controlled_case_input
from src.application.test_runs.plan_run_service import CasePlanRunService
from src.application.test_runs.run_store import PostgresTestRunStore
from src.application.test_suites.suite_store import PostgresTestSuiteStore
from src.application.runtime.controlled_case_adapter import ControlledCaseGraphRequest
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.schemas.agent_task import AgentTaskCommandV2
from src.schemas.case_planning import CasePlanRunRequest
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_model_call import FrameworkModelBudget
from src.schemas.model_config import ModelInvocationParameters
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import CreateSessionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_case_plan_run_creation import planned
from tests.test_framework_model_admission import model_configuration
from tests.test_live_postgres_concurrency import _drop_tables
from tests.test_runtime_binding_persistence import make_binding
from tests.test_controlled_case_task_input import source as memory_case


def implementation():
    return importlib.import_module("src.application.test_runs.controlled_case_admission")


def reread_command(raw):
    """Build a different but self-consistent input through the original codecs."""
    asset = raw["case_execution_input"]
    graph = asset["graph_input"]
    graph["binding"] = RuntimeBinding.model_validate({k: v for k, v in graph["binding"].items()
        if k != "fingerprint"}).model_dump(mode="json")
    raw["runtime_binding"] = deepcopy(graph["binding"])
    graph["asset_sha256"] = approval_value_hash({k: v for k, v in graph.items() if k != "asset_sha256"})
    asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
    return AgentTaskCommandV2.model_validate(raw)


@pytest_asyncio.fixture(autouse=True)
async def prepare_domain(request, monkeypatch):
    if "pg_domain" not in request.fixturenames:
        yield None
        return
    from tests import test_agent_task_dispatch as dispatch_module
    planning_module = importlib.import_module("test_case_planning")
    from tests.live_postgres_config import LivePostgresTestConfig
    from tests.test_case_json_templates import _Configs, _Objects
    from src.application.documents.api_doc_store import InMemoryApiDocStore

    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires real PostgreSQL and Redis")
    settings = dispatch_module.get_settings().model_copy(deep=True)
    fields = ("test_case_result", "test_run_attempt", "test_run_item", "test_run",
        "test_suite_item", "test_suite", "case_plan", "test_case_version", "test_case", "project")
    suffix = uuid4().hex[:12]
    tables = [f"case_admit_{index}_{suffix}" for index in range(len(fields))]
    for field, table in zip(fields, tables):
        setattr(settings.database, f"postgres_{field}_table", table)
    projects, cases = PostgresProjectStore(settings), PostgresTestCaseStore(settings)
    plans = PostgresCasePlanStore(settings)
    suites, runs = PostgresTestSuiteStore(settings), PostgresTestRunStore(settings)
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
            assert cur.fetchone()["existing"] is None

    @asynccontextmanager
    async def pg_catalogs(tmp_path, live):
        assert not live  # No MySQL, RustFS or provider calls hidden by this fixture.
        settings.data_dir = str(tmp_path)
        yield settings, projects, cases, _Configs(), InMemoryApiDocStore(), _Objects()

    try:
        await projects.initialize()
        await cases.initialize()
        await plans.initialize()
        await suites.initialize()
        await runs.initialize()
        monkeypatch.setattr(dispatch_module, "get_settings", lambda: settings)
        monkeypatch.setattr(planning_module, "_stores", pg_catalogs)
        yield SimpleNamespace(**locals())
    finally:
        _drop_tables(settings, tables)


@pytest.fixture
def pg_domain(prepare_domain):
    return prepare_domain


@pytest_asyncio.fixture
async def formal_case(pg_domain, harness, tmp_path):
    h = harness
    # The imported harness adds isolated Session/Task table settings. All
    # transaction participants must use that same completed configuration.
    d = SimpleNamespace(**vars(pg_domain))
    d.runs, d.cases = PostgresTestRunStore(h.settings), PostgresTestCaseStore(h.settings)
    calls = PostgresFrameworkModelCalls(h.settings)
    await calls.initialize()
    try:
        # Draft -> review -> active uses the official services with real PG
        # catalogs; the planner/config store/object store stay explicit doubles.
        async with planned(tmp_path) as (env, _, _suites, _creator, plan, _now, _table):
            await d.plans.save(plan)
            creator = CasePlanRunService(run_store=d.runs, suite_store=d.suites, plan_store=d.plans,
                case_service=env.cases, project_service=env.projects)
            detail = await creator.create(plan.project_id, plan.id,
                CasePlanRunRequest(creation_key="case-admission", mode_key="api_testing"))
            now = datetime.now(timezone.utc)
            claims = await d.runs.claim_items(run_id=detail.run.id, worker_id="formal-run-worker",
                limit=20, lease_seconds=120, now=now)
            item, attempt = claims[0]
            item = await d.runs.start_item(item.id, item.lease_token, now)
            detail = await d.runs.get_execution_detail(detail.run.id, item.id, attempt.id)
            attempt = next(entry for entry in detail.attempts if entry.id == attempt.id)
            version = await d.cases.get_version(item.case_version_id)
            h.sessions._project_service = env.projects
            session = await h.sessions.create_internal_session(CreateSessionRequest(
                title="Isolated controlled case admission", project_id=detail.run.project_id,
                mode_key=detail.run.mode_key, metadata={"source": "test_run_agent_execution",
                    "run_id": detail.run.id, "run_item_id": item.id, "parent_session_id": detail.run.session_id}))
            turn = str(uuid5(NAMESPACE_URL, f"case-run:{detail.run.id}:{item.id}:{attempt.id}"))
            snapshot = ModelInvocationParameters.from_configuration(model_configuration())
            binding = make_binding(session.id, turn, runtime_kind="langgraph",
                model_config_fingerprint=snapshot.fingerprint)
            graph_request = ControlledCaseGraphRequest(session_id=session.id, turn_id=turn,
                trace_id="formal-case-admission", model_key="primary", checkpoint_namespace=binding.checkpoint_namespace,
                system_prompt="Prepared test input; full server assembly is a separate acceptance exit.",
                framework_messages=serialize_messages([HumanMessage(id="case-message", content=plan.request.objective)]))
            asset = prepare_controlled_case_input(detail=detail, item=item, version=version,
                attempt_id=attempt.id, binding=binding, graph_request=graph_request)
            command = AgentTaskCommandV2(operation="case_execute", task_id=str(uuid4()), session_id=session.id,
                turn_id=turn, request_id="formal-admission-request", trace_id=graph_request.trace_id,
                project_id=detail.run.project_id, run_id=detail.run.id, run_item_id=item.id,
                reference_id=item.case_version_id, runtime_binding=binding, case_execution_input=asset)
            budget = FrameworkModelBudget(schema_version=2, max_calls=8, max_tokens=1000,
                deadline_at=now + timedelta(minutes=5), model_configurations={"primary": snapshot},
                model_config_fingerprints={"primary": snapshot.fingerprint})
            yield SimpleNamespace(**locals())
    finally:
        _drop_tables(h.settings, [calls.uploads_table, calls.calls_table, calls.budgets_table])


async def submit(s, command=None, **changes):
    service = implementation().ControlledCaseAdmission(run_store=s.d.runs, case_store=s.d.cases,
        task_store=s.h.tasks)
    values = {"command": command or s.command, "lease_token": s.item.lease_token, "model_budget": s.budget}
    values.update(changes)
    return await service.submit(**values)


async def wait_for_session_sql_lock(s, table=None):
    table = table or s.h.tasks._session_table
    deadline = time.monotonic() + 1.5
    while True:
        with postgres_connect(s.h.settings) as probe, probe.cursor() as cur:
            cur.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid<>pg_backend_pid() "
                "AND cardinality(pg_blocking_pids(pid))>0 AND query LIKE %s) AS blocked",
                (f"%FROM {table} WHERE%",))
            if cur.fetchone()["blocked"]:
                return
        assert time.monotonic() < deadline, "admission never waited on the real row lock"
        await asyncio.sleep(.01)


def database_digest(s):
    # Compare complete facts without printing private input or lease material.
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        state = {}
        for table in (*s.d.tables, s.h.tasks._tasks, s.h.tasks._attempts,
            s.h.settings.database.postgres_task_outbox_table, s.h.settings.database.postgres_message_table,
            s.h.settings.database.postgres_event_table, s.h.settings.database.postgres_session_table,
            f"{s.h.tasks._tasks}_runtime", f"{s.h.tasks._tasks}_message_stage", s.calls.budgets_table):
            cur.execute(f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb) AS rows FROM {table} t")
            state[table] = cur.fetchone()["rows"]
        return approval_value_hash(state)


@pytest.mark.asyncio
async def test_admission_commits_formal_task_input_budget_outbox_and_original_attempt_atomically(formal_case):
    s = formal_case
    receipt = await submit(s)
    assert receipt.operation == "case_execute" and receipt.status == "queued"
    assert receipt.attempt_id is None and receipt.attempt_no == 0
    task = await s.h.tasks.get(receipt.id)
    assert task.user_message_id is None and task.input == {"case_execution_input": s.asset.model_dump(mode="json")}
    detail = await s.d.runs.get_execution_detail(s.detail.run.id, s.item.id, s.attempt.id)
    attempt = next(entry for entry in detail.attempts if entry.id == s.attempt.id)
    checkpoint = implementation().read_admission_checkpoint(attempt)
    assert checkpoint.task_id == receipt.id and checkpoint.run_item_execution_attempt_id == s.attempt.id
    assert checkpoint.input_sha256 == s.asset.asset_sha256 and checkpoint.binding == s.binding
    assert attempt.status == "running" and attempt.checkpoint_version == 1
    assert approval_value_hash([i.model_dump(mode="json") for i in detail.items]) == approval_value_hash(
        [i.model_dump(mode="json") for i in s.detail.items])  # No extra claim, lease extension or result reset.
    assert (await s.calls.snapshot(s.binding)).policy == s.budget
    events = [event for event in await s.h.store.list_events(s.session.id) if event.type == "test_run.case_admitted"]
    assert len(events) == 1 and events[0].attempt_id is None
    assert events[0].payload["run_item_execution_attempt_id"] == s.attempt.id
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload,stream FROM {s.h.settings.database.postgres_task_outbox_table}")
        rows = cur.fetchall()
        assert len(rows) == 1
        wire = rows[0]["payload"]
        assert "case_execution_input" not in wire
        assert wire["case_execution_reference"] == {"run_item_execution_attempt_id": s.attempt.id,
            "input_sha256": s.asset.asset_sha256}
        assert s.graph_request.system_prompt not in str(wire) and "framework_messages" not in str(wire)
        assert AgentTaskCommandV2.model_validate(wire).operation == "case_execute"
        assert rows[0]["stream"] == s.h.settings.orchestration.agent_task_v2_stream
        for table in (s.h.tasks._attempts, f"{s.h.tasks._tasks}_message_stage", s.h.settings.database.postgres_message_table):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
    assert s.h.runtime.calls == []


@pytest.mark.asyncio
async def test_eight_concurrent_retries_reuse_one_committed_admission_without_rewriting_facts(formal_case):
    s = formal_case
    commands = [s.command.model_copy(update={"task_id": str(uuid4()), "request_id": f"retry-{index}"}) for index in range(8)]
    receipts = await asyncio.gather(*(submit(s, command) for command in commands))
    assert len({receipt.id for receipt in receipts}) == 1
    before = database_digest(s)
    assert (await submit(s)).id == receipts[0].id
    assert database_digest(s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["budget", "attempt", "event"])
async def test_failure_rolls_back_every_admission_fact(formal_case, monkeypatch, failure):
    s = formal_case
    if failure == "budget":
        target, name = PostgresFrameworkModelCalls, "freeze_submission"
    elif failure == "attempt":
        target, name = s.d.runs, "_write_attempt"
    else:
        target, name = s.h.tasks, "_event"
    original = getattr(target, name)
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("controlled failure after durable write before transaction commit")
    monkeypatch.setattr(target, name, fail)
    before = database_digest(s)
    with pytest.raises(RuntimeError, match="controlled failure"):
        await submit(s)
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_receipt_read_after_expiry_or_cancellation_does_not_grant_execution_or_refresh_budget(formal_case):
    s = formal_case
    first = await submit(s)
    await s.d.runs.cancel_run(s.detail.run.id, "isolated cancel", datetime.now(timezone.utc))
    before = database_digest(s)
    assert (await submit(s)).id == first.id
    assert database_digest(s) == before
    assert (await s.h.tasks.get(first.id)).attempt_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["lease", "cancelled", "legacy_checkpoint", "recovered", "missing_pin",
    "worker", "version", "plan_archive", "context", "busy_session", "session_scope", "deadline", "budget_v1"])
async def test_first_admission_rejects_changed_formal_sources_without_any_writes(formal_case, change, caplog):
    s = formal_case
    options = {}
    if change == "lease":
        options["lease_token"] = "different-lease-test-value"
    elif change == "cancelled":
        await s.d.runs.cancel_run(s.detail.run.id, "isolated cancel", datetime.now(timezone.utc))
    elif change == "legacy_checkpoint":
        await s.d.runs.save_checkpoint(s.item.id, s.item.lease_token, "agent_case_execution", {}, 1, s.now)
    elif change in {"recovered", "missing_pin", "worker"}:
        values = {"recovered": {"recovered_from_attempt_id": str(uuid4())},
            "missing_pin": {"plan_snapshot_identity": None}, "worker": {"worker_id": "another-run-worker"}}[change]
        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
            s.d.runs._write_attempt(cur, s.attempt.model_copy(update=values))
    elif change == "version":
        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
            version = s.version.model_copy(update={"execution_notes": "changed source after preparation"})
            cur.execute(f"UPDATE {s.d.cases._version_table} SET record=%s::jsonb WHERE id=%s",
                (version.model_dump_json(), version.id))
    elif change == "plan_archive":
        changed_run = s.detail.run.model_copy(deep=True)
        changed_run.plan_snapshot.plan.request.objective += " changed current projection"
        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {s.d.runs._run_table} SET plan_snapshots='{{}}'::jsonb,record=%s::jsonb WHERE id=%s",
                (changed_run.model_dump_json(), s.detail.run.id))
    elif change == "context":
        raw = s.command.model_dump(mode="json")
        asset = raw["case_execution_input"]
        asset["context"]["objective"] += " self-consistent but not formal"
        asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
        options["command"] = AgentTaskCommandV2.model_validate(raw)
    elif change in {"busy_session", "session_scope"}:
        session = await s.h.store.get_session(s.session.id)
        if change == "busy_session":
            from src.schemas.session import SessionStatus
            session.status = SessionStatus.running
        else:
            session.metadata["run_item_id"] = str(uuid4())
        await s.h.store.save_session(session)
    elif change == "deadline":
        options["model_budget"] = s.budget.model_copy(update={"deadline_at": s.now - timedelta(seconds=1)})
    else:
        raw = s.budget.model_dump(mode="json", exclude={"model_configurations"})
        options["model_budget"] = FrameworkModelBudget.model_validate({**raw, "schema_version": 1})
    caplog.set_level("INFO")
    before = database_digest(s)
    with pytest.raises(ValueError):
        await submit(s, **options)
    assert database_digest(s) == before
    rejected = next(row for row in caplog.records if row.message == "controlled_case_admission_failed")
    expected = {"lease": "original_execution_identity", "cancelled": "formal_source",
        "legacy_checkpoint": "original_execution_reservation", "recovered": "original_execution_reservation",
        "missing_pin": "formal_input", "worker": "formal_input", "version": "formal_input",
        "plan_archive": "claimed_plan", "context": "formal_input", "busy_session": "dedicated_session",
        "session_scope": "dedicated_session", "deadline": "live_lease_and_deadline", "budget_v1": "model_originals"}
    assert rejected.recovery_reason == expected[change] and rejected.run_id == s.detail.run.id
    assert "lease_token" not in rejected.__dict__ and s.item.lease_token not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["input", "budget", "binding"])
async def test_retry_cannot_rebind_committed_input_budget_or_runtime(formal_case, change):
    s = formal_case
    await submit(s)
    raw, options = s.command.model_dump(mode="json"), {}
    asset = raw["case_execution_input"]
    graph = asset["graph_input"]
    if change == "budget":
        options["model_budget"] = s.budget.model_copy(update={"max_calls": s.budget.max_calls + 1})
    elif change == "input":
        graph["system_prompt"] += " changed after original commit"
    else:
        graph["binding"]["binding_id"] = "different-binding"
        raw["runtime_binding"] = deepcopy(graph["binding"])
    graph["asset_sha256"] = approval_value_hash({k: v for k, v in graph.items() if k != "asset_sha256"})
    asset["asset_sha256"] = approval_value_hash({k: v for k, v in asset.items() if k != "asset_sha256"})
    before = database_digest(s)
    with pytest.raises(AgentSubmissionConflict):
        await submit(s, AgentTaskCommandV2.model_validate(raw), **options)
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_two_different_sessions_cannot_admit_the_same_original_run_attempt(formal_case):
    s = formal_case
    second = await s.h.sessions.create_internal_session(CreateSessionRequest(
        title="Second racing internal Session", project_id=s.detail.run.project_id, mode_key=s.detail.run.mode_key,
        metadata={"source": "test_run_agent_execution", "run_id": s.detail.run.id, "run_item_id": s.item.id}))
    raw = s.command.model_dump(mode="json")
    raw["task_id"], raw["session_id"] = str(uuid4()), second.id
    graph = raw["case_execution_input"]["graph_input"]
    graph["binding"]["session_id"], graph["binding"]["binding_id"] = second.id, str(uuid4())
    second_command = reread_command(raw)
    receipts = await asyncio.gather(submit(s), submit(s, second_command), return_exceptions=True)
    assert sum(isinstance(value, AgentSubmissionConflict) for value in receipts) == 1
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        for table in (s.h.tasks._tasks, s.calls.budgets_table, s.h.settings.database.postgres_task_outbox_table):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("barrier", ["session", "model_owner"])
async def test_database_clock_after_all_waiting_admission_locks_rejects_expired_original_lease(formal_case, barrier):
    s = formal_case
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        if barrier == "model_owner":
            from src.runtime.postgres_runtime_owner import register_runtime_binding
            register_runtime_binding(cur, s.h.settings, s.binding)
        cur.execute("SELECT clock_timestamp() + interval '2 seconds' AS expiry")
        expiry = cur.fetchone()["expiry"]
        s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": expiry}))
    before = database_digest(s)
    with postgres_connect(s.h.settings) as holder, holder.cursor() as lock:
        table = s.h.tasks._session_table if barrier == "session" else f"{s.h.tasks._tasks}_runtime"
        key, identity = ("id", s.session.id) if barrier == "session" else ("binding_id", s.binding.binding_id)
        lock.execute(f"SELECT {key} FROM {table} WHERE {key}=%s FOR UPDATE", (identity,))
        pending = asyncio.create_task(submit(s))
        try:
            # Observe a real blocked SQL statement rather than assume a sleep
            # means the submit thread has opened its transaction.
            await wait_for_session_sql_lock(s, table)
            lock.execute("SELECT clock_timestamp() < %s AS still_active", (expiry,))
            assert lock.fetchone()["still_active"]
            # Host and database clocks are not assumed synchronized. Observe
            # expiry on the same DB clock used by admission, before unlocking.
            deadline = time.monotonic() + 4
            while True:
                lock.execute("SELECT clock_timestamp() >= %s AS expired", (expiry,))
                if lock.fetchone()["expired"]:
                    break
                assert time.monotonic() < deadline, "database lease did not reach its recorded expiry"
                await asyncio.sleep(.01)
            holder.commit()
            with pytest.raises(ValueError, match="Lease expired"):
                await pending
        finally:
            holder.rollback()
            if not pending.done():
                await pending
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_cancelled_waiter_reports_unknown_receipt_and_keeps_captured_input_immutable(formal_case, caplog):
    from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT

    s = formal_case
    command = AgentTaskCommandV2.model_validate(s.command.model_dump(mode="json"))
    budget = FrameworkModelBudget.model_validate(s.budget.model_dump(mode="json"))
    caplog.set_level("INFO")
    with postgres_connect(s.h.settings) as holder, holder.cursor() as lock:
        lock.execute(f"SELECT id FROM {s.h.tasks._session_table} WHERE id=%s FOR UPDATE", (s.session.id,))
        pending = asyncio.create_task(submit(s, command, model_budget=budget))
        try:
            await wait_for_session_sql_lock(s)
            command.case_execution_input.context.objective = "private mutation after captured admission"
            budget.model_configurations.clear()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            holder.commit()
            if not pending.done():
                await pending
    # Cancelling the coroutine did not stop to_thread. Observe its real commit
    # using reads only; never run another command to guess the first outcome.
    deadline = time.monotonic() + 5
    while True:
        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {s.h.tasks._tasks} WHERE id=%s", (s.command.task_id,))
            if cur.fetchone()["n"] == 1:
                break
        assert time.monotonic() < deadline, "cancelled admission did not reach an observable commit"
        await asyncio.sleep(.01)
    task = await s.h.tasks.get(s.command.task_id)
    assert task.input == {"case_execution_input": s.asset.model_dump(mode="json")}
    assert (await s.calls.snapshot(s.binding)).policy == s.budget
    before = database_digest(s)
    assert (await submit(s)).id == task.id and database_digest(s) == before
    records = [r for r in caplog.records if r.message.startswith("controlled_case_admission_")]
    assert any(r.message == "controlled_case_admission_waiter_cancelled"
        and r.recovery_reason == "admission_receipt_unknown" for r in records)
    for record in records:
        assert record.execution_id == s.attempt.id and record.turn_id == s.turn
        ContextFilter().filter(record)
    rendered = "\n".join(ContextFormatter(LOG_FORMAT).format(record) for record in records)
    assert "admission_receipt_unknown" in rendered and s.asset.asset_sha256 in rendered
    assert "private mutation" not in rendered and s.item.lease_token not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["install", "overwrite"])
async def test_generic_pg_checkpoint_writer_cannot_forge_or_overwrite_atomic_case_reservation(formal_case, state):
    s = formal_case
    key = implementation().CONTROLLED_CASE_ADMISSION_KEY
    if state == "overwrite":
        await submit(s)
    before = database_digest(s)
    with pytest.raises(ValueError, match="atomic"):
        await s.d.runs.save_checkpoint(s.item.id, s.item.lease_token,
            "agent_case_execution" if state == "overwrite" else key, {}, 2, datetime.now(timezone.utc))
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_generic_memory_checkpoint_writer_uses_the_same_reserved_namespace_contract(memory_case):
    s = memory_case
    before = approval_value_hash((await s.store.get_run(s.detail.run.id)).model_dump(mode="json"))
    with pytest.raises(ValueError, match="atomic"):
        await s.store.save_checkpoint(s.item.id, s.item.lease_token,
            implementation().CONTROLLED_CASE_ADMISSION_KEY, {}, 1, s.now)
    assert approval_value_hash((await s.store.get_run(s.detail.run.id)).model_dump(mode="json")) == before


@pytest.mark.asyncio
async def test_original_worker_still_defers_real_admitted_case_without_claiming_an_attempt(formal_case):
    from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred

    s = formal_case
    receipt = await submit(s)
    dispatcher = AgentTaskDispatcher(s.h.settings, s.h.tasks, s.h.sessions, s.h.resources,
        framework_model_calls=s.calls, framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True))
    before = database_digest(s)
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT payload FROM {s.h.settings.database.postgres_task_outbox_table}")
        wire = cur.fetchone()["payload"]
    for _ in range(2):
        with pytest.raises(TaskDeferred, match="runtime_kind_unavailable"):
            await dispatcher.handle_v2(QueuedTask("case-admission", wire), worker_id="old-reader")
        assert database_digest(s) == before
    task = await s.h.tasks.get(receipt.id)
    assert task.attempt_id is None and task.attempt_no == 0 and s.h.runtime.calls == []


@pytest.mark.asyncio
async def test_existing_outbox_relay_sends_only_reference_to_real_isolated_redis(formal_case):
    from src.infrastructure.redis_task_queue import RedisTaskQueue

    s = formal_case
    await submit(s)
    queue = RedisTaskQueue(s.h.settings.database.redis_url,
        stream=s.h.settings.orchestration.agent_task_v2_stream, group="case-admission-proof", client=s.h.redis)
    await queue.connect()
    assert await s.h.outbox.relay_once(queue) == 1
    assert await s.h.outbox.relay_once(queue) == 0
    rows = await s.h.redis.xrange(queue.stream)
    assert len(rows) == 1
    wire = json.loads(rows[0][1]["payload"])
    assert "case_execution_input" not in wire and "framework_messages" not in str(wire)
    assert s.graph_request.system_prompt not in str(wire)
    assert wire["case_execution_reference"] == {"run_item_execution_attempt_id": s.attempt.id,
        "input_sha256": s.asset.asset_sha256}
    assert AgentTaskCommandV2.model_validate(wire).runtime_binding == s.binding
    before = database_digest(s)
    with pytest.raises(ValueError, match="only case_execute"):
        await submit(s, AgentTaskCommandV2.model_validate(wire))
    assert database_digest(s) == before  # A reference is not first-admission material.


@pytest.mark.asyncio
@pytest.mark.parametrize("projection", ["missing_archive_matching_current", "revised_current_original_archive"])
async def test_original_claim_pin_preserves_both_existing_compatible_snapshot_read_paths(formal_case, projection):
    s = formal_case
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        if projection == "missing_archive_matching_current":
            cur.execute(f"UPDATE {s.d.runs._run_table} SET plan_snapshots='{{}}'::jsonb WHERE id=%s", (s.detail.run.id,))
        else:
            changed = s.detail.run.model_copy(deep=True)
            changed.plan_snapshot.plan.request.objective += " changed current projection, original archive retained"
            s.d.runs._write_run(cur, changed)
    receipt = await submit(s)
    task = await s.h.tasks.get(receipt.id)
    assert task.input["case_execution_input"] == s.asset.model_dump(mode="json")
    attempt = next(a for a in (await s.d.runs.get_run(s.detail.run.id)).attempts if a.id == s.attempt.id)
    assert implementation().read_admission_checkpoint(attempt).plan_snapshot_identity == s.attempt.plan_snapshot_identity


@pytest.mark.asyncio
async def test_lost_ack_after_real_commit_reuses_every_original_fact(formal_case, monkeypatch):
    s = formal_case
    cls = implementation().ControlledCaseAdmission
    original = cls._submit_sync
    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("controlled acknowledgement lost after actual commit")
    with monkeypatch.context() as scoped:
        scoped.setattr(cls, "_submit_sync", lose_ack)
        with pytest.raises(RuntimeError, match="acknowledgement lost"):
            await submit(s)
    before = database_digest(s)
    receipt = await submit(s, s.command.model_copy(update={"task_id": str(uuid4())}))
    assert receipt.id == s.command.task_id and database_digest(s) == before


@pytest.mark.asyncio
async def test_expired_original_receipt_stays_readable_but_new_run_attempt_cannot_refreeze_it(formal_case):
    s = formal_case
    first = await submit(s)
    future = s.now + timedelta(minutes=3)
    await s.d.runs.recover_expired(s.detail.run.id, future)
    claims = await s.d.runs.claim_items(run_id=s.detail.run.id, worker_id="new-run-worker",
        limit=20, lease_seconds=120, now=future)
    item, attempt = next((item, attempt) for item, attempt in claims if item.id == s.item.id)
    await s.d.runs.start_item(item.id, item.lease_token, future)
    assert attempt.recovered_from_attempt_id == s.attempt.id
    raw = s.command.model_dump(mode="json")
    turn = str(uuid5(NAMESPACE_URL, f"case-run:{item.run_id}:{item.id}:{attempt.id}"))
    raw["task_id"], raw["turn_id"] = str(uuid4()), turn
    asset = raw["case_execution_input"]
    asset["run_item_execution_attempt_id"] = attempt.id
    graph = asset["graph_input"]
    graph["binding"].update(binding_id=str(uuid4()), turn_id=turn, thread_id=turn)
    new_command = reread_command(raw)
    before = database_digest(s)
    with pytest.raises(AgentSubmissionConflict, match="execution intent"):
        await submit(s, new_command, lease_token=item.lease_token)
    assert (await submit(s)).id == first.id
    assert database_digest(s) == before


@pytest.mark.parametrize("module", ["src.application.test_runs.run_store", "src.schemas.controlled_case_admission",
    "src.runtime.postgres_agent_task_store", "src.application.test_runs.controlled_case_admission"])
def test_new_admission_modules_import_in_a_cold_process_without_initialization(module):
    result = subprocess.run([sys.executable, "-c", f"import importlib; importlib.import_module({module!r}); print('cold-ok')"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "cold-ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["valid", "version_bool", "future", "extra", "run_attempt", "pin", "time", "sequence"])
async def test_reservation_reader_is_strict_and_never_grants_an_inherited_execution(memory_case, change):
    from src.schemas.controlled_case_admission import (
        CONTROLLED_CASE_ADMISSION_KEY, ControlledCaseAdmissionCheckpoint, read_admission_checkpoint,
    )

    s = memory_case
    # Codec-only material. This is not installed into a store or represented
    # as a real admitted task; the PG tests above prove actual atomic writes.
    checkpoint = ControlledCaseAdmissionCheckpoint(schema_version=1, project_id=s.detail.run.project_id,
        run_id=s.item.run_id, run_item_id=s.item.id, run_item_execution_attempt_id=s.attempt.id,
        task_id="codec-only-task", binding=s.binding, plan_snapshot_identity=s.attempt.plan_snapshot_identity,
        input_sha256="a" * 64, submission_sha256="b" * 64, admitted_at=s.now)
    payload = checkpoint.model_dump(mode="json")
    if change == "version_bool":
        payload["schema_version"] = True
    elif change == "future":
        payload["schema_version"] = 2
    elif change == "extra":
        payload["unrecognized_authority"] = "not-a-permit"
    elif change == "run_attempt":
        payload["run_item_execution_attempt_id"] = str(uuid4())
    elif change == "pin":
        payload["plan_snapshot_identity"]["plan_id"] = str(uuid4())
    elif change == "time":
        payload["admitted_at"] = s.now.replace(tzinfo=None).isoformat()
    attempt = s.attempt.model_copy(update={"checkpoint_key": CONTROLLED_CASE_ADMISSION_KEY,
        "checkpoint_version": 2 if change == "sequence" else 1,
        "checkpoint_payload": payload, "checkpoint_at": s.now})
    if change == "valid":
        assert read_admission_checkpoint(attempt) == checkpoint
    else:
        with pytest.raises(ValueError):
            read_admission_checkpoint(attempt)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["valid", "both", "missing", "operation", "scope", "attempt", "hash", "extra"])
async def test_case_delivery_reference_is_strict_separate_from_full_input(memory_case, change):
    from tests.test_controlled_case_task_input import prepare, command
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities

    s = memory_case
    asset = prepare(s)
    raw = command(s, asset).model_dump(mode="json")
    raw.pop("case_execution_input")
    reference = {"run_item_execution_attempt_id": s.attempt.id, "input_sha256": asset.asset_sha256}
    raw["case_execution_reference"] = reference
    if change == "both":
        raw["case_execution_input"] = asset.model_dump(mode="json")
    elif change == "missing":
        raw.pop("case_execution_reference")
    elif change == "operation":
        raw["operation"] = "message"
    elif change == "scope":
        raw["run_id"] = None
    elif change == "attempt":
        reference["run_item_execution_attempt_id"] = str(uuid4())
    elif change == "hash":
        reference["input_sha256"] = "not-a-digest"
    elif change == "extra":
        reference["unexpected_authority"] = True
    if change == "valid":
        parsed = FrameworkWorkerCapabilities.require_command_format(raw)
        assert parsed.case_execution_input is None and parsed.case_execution_reference.input_sha256 == asset.asset_sha256
        assert parsed.model_dump(mode="json") == raw
    else:
        with pytest.raises(ValueError):
            AgentTaskCommandV2.model_validate(raw)


@pytest.mark.parametrize("difference", ["unrelated_database_setting", "postgres_database", "postgres_password"])
def test_transaction_participants_compare_postgres_identity_not_unrelated_services(difference):
    from src.core.config import DatabaseConfig

    settings = SimpleNamespace(database=DatabaseConfig(redis_url="redis://127.0.0.1:6379/0"))
    other = deepcopy(settings)
    field = "redis_url" if difference == "unrelated_database_setting" else difference
    setattr(other.database, field, str(uuid4()))
    participants = {"run_store": SimpleNamespace(_settings=other),
        "case_store": SimpleNamespace(_settings=settings), "task_store": SimpleNamespace(_settings=settings)}
    if difference == "unrelated_database_setting":
        implementation().ControlledCaseAdmission(**participants)
    else:
        with pytest.raises(ValueError, match="same PostgreSQL"):
            implementation().ControlledCaseAdmission(**participants)
