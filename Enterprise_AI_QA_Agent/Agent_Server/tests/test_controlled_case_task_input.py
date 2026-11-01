"""Reader-first formal case preparation; no case admission or Worker writer."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib
import json
import subprocess
import sys
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import pytest_asyncio
from langchain_core.messages import HumanMessage

from src.application.deep_agents.framework_transcript import serialize_messages
from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher
from src.application.runtime.controlled_case_adapter import ControlledCaseGraphRequest
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import AgentTaskCommandV2, parse_agent_task_command
from src.schemas.case_planning import CasePlanRunRequest
from src.schemas.framework_approval import approval_value_hash
from src.infrastructure.redis_task_queue import QueuedTask
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.runtime_binding import RuntimeBinding
from tests.test_agent_task_dispatch import harness
from tests.test_agent_v2_worker_contract import _Settings, _Tasks, stored_task
from tests.test_case_plan_run_creation import planned
from tests.test_runtime_binding_persistence import make_binding


def codec():
    return importlib.import_module("src.schemas.controlled_case_task_input")


def preparer():
    return importlib.import_module("src.application.test_runs.controlled_case_preparation")


@pytest_asyncio.fixture(autouse=True)
async def prepare_pg_case_project(request, monkeypatch):
    # Same ordering as test_framework_release_binding: configure the isolated
    # official project table before the imported harness builds its Session FK.
    if "pg_case_project" not in request.fixturenames:
        yield None
        return
    from tests import test_agent_task_dispatch as dispatch_module
    from tests.live_postgres_config import LivePostgresTestConfig
    from tests.test_live_postgres_concurrency import _drop_tables
    from src.application.projects.project_store import PostgresProjectStore

    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires real PostgreSQL and Redis")
    settings = dispatch_module.get_settings().model_copy(deep=True)
    settings.database.postgres_project_table = f"agent_test_case_reader_project_{uuid4().hex[:12]}"
    projects = PostgresProjectStore(settings)
    try:
        await projects.initialize()
        monkeypatch.setattr(dispatch_module, "get_settings", lambda: settings)
        yield projects
    finally:
        _drop_tables(settings, [settings.database.postgres_project_table])


@pytest.fixture
def pg_case_project(prepare_pg_case_project):
    return prepare_pg_case_project


@pytest_asyncio.fixture
async def source(tmp_path):
    # Real draft/review/activation, frozen suite/plan and atomic in-memory
    # RunItem claim. Planning replies/object backend are explicit test doubles.
    async with planned(tmp_path) as (env, store, suites, service, plan, now, table):
        detail = await service.create(plan.project_id, plan.id,
            CasePlanRunRequest(creation_key="controlled-reader", mode_key="api_testing"))
        claims = await store.claim_items(run_id=detail.run.id, worker_id="run-worker", limit=20,
            lease_seconds=120, now=now)
        item, attempt = claims[0]
        item = await store.start_item(item.id, item.lease_token, now)
        detail = await store.get_execution_detail(detail.run.id, item.id, attempt.id)
        attempt = next(entry for entry in detail.attempts if entry.id == attempt.id)
        version = await env.cases.get_version(item.case_version_id)
        turn = str(uuid5(NAMESPACE_URL, f"case-run:{detail.run.id}:{item.id}:{attempt.id}"))
        binding = make_binding("controlled-case-session", turn, runtime_kind="langgraph")
        request = ControlledCaseGraphRequest(session_id=binding.session_id, turn_id=turn,
            trace_id="controlled-case-trace", model_key="primary", checkpoint_namespace=binding.checkpoint_namespace,
            system_prompt="Prepared server prompt; not a claim of complete effective input assembly.",
            framework_messages=serialize_messages([HumanMessage(id="case-input", content=plan.request.objective)]))
        yield SimpleNamespace(**locals())


def prepare(s, **changes):
    values = dict(detail=s.detail, item=s.item, version=s.version, attempt_id=s.attempt.id,
        binding=s.binding, graph_request=s.request)
    values.update(changes)
    return preparer().prepare_controlled_case_input(**values)


def command(s, asset):
    return AgentTaskCommandV2(operation="case_execute", task_id=str(uuid4()),
        session_id=s.binding.session_id, turn_id=s.binding.turn_id, request_id="case-request",
        trace_id=s.request.trace_id, runtime_binding=s.binding,
        project_id=s.detail.run.project_id, run_id=s.detail.run.id, run_item_id=s.item.id,
        reference_id=s.item.case_version_id, case_execution_input=asset)


@pytest.mark.asyncio
async def test_formal_source_preparation_preserves_fixed_context_and_separate_execution_identity(source):
    s = source
    before = await s.store.get_run(s.detail.run.id)
    asset = prepare(s)
    assert asset.context.execution_json() == build_agent_case_context(s.detail, s.item, s.version).execution_json()
    assert asset.run_item_execution_attempt_id == s.attempt.id
    assert asset.plan_snapshot_identity == s.attempt.plan_snapshot_identity
    assert asset.graph_input.binding == s.binding
    assert asset.context.steps[-1].content_json == s.version.business_content()
    raw = asset.model_dump(mode="json")
    assert codec().parse_controlled_case_task_input(raw).model_dump(mode="json") == raw
    value = command(s, asset)
    assert value.attempt_id is None and value.case_execution_input.run_item_execution_attempt_id == s.attempt.id
    assert parse_agent_task_command(value.model_dump(mode="json")) == value
    assert await s.store.get_run(s.detail.run.id) == before
    assert "lease_token" not in raw and "runtime_owner" not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["lease", "item_status", "attempt_status", "attempt_id", "version",
    "checkpoint", "recovered", "unpinned", "plan_drift", "graph_turn"])
async def test_preparation_rejects_non_original_or_mismatched_formal_sources_without_writes(source, changed, caplog):
    caplog.set_level("INFO")
    s = source
    detail, item, version, request = deepcopy(s.detail), deepcopy(s.item), deepcopy(s.version), s.request
    attempt = next(entry for entry in detail.attempts if entry.id == s.attempt.id)
    if changed == "lease":
        item.lease_token = "different-run-item-lease"
    elif changed == "item_status":
        item.status = "passed"
    elif changed == "attempt_status":
        attempt.status = "expired"
    elif changed == "attempt_id":
        attempt.run_item_id = str(uuid4())
    elif changed == "version":
        version.execution_notes += " source drift"
    elif changed == "checkpoint":
        attempt.checkpoint_version = 1
        attempt.checkpoint_key = "agent_case_execution"
    elif changed == "recovered":
        attempt.recovered_from_attempt_id = str(uuid4())
    elif changed == "unpinned":
        attempt.plan_snapshot_identity = None
    elif changed == "plan_drift":
        detail.run.plan_snapshot.plan.request.objective += " changed after claim"
    else:
        request = replace(request, turn_id="not-the-original-case-turn")
    before = await s.store.get_run(s.detail.run.id)
    with pytest.raises(ValueError):
        prepare(s, detail=detail, item=item, version=version, graph_request=request)
    assert await s.store.get_run(s.detail.run.id) == before
    rejection = next(row for row in caplog.records if row.message == "controlled_case_input_preparation_rejected")
    expected_reason = {
        "lease": "run_item_attempt_identity_mismatch", "item_status": "run_item_attempt_identity_mismatch",
        "attempt_status": "run_item_attempt_identity_mismatch", "attempt_id": "original_execution_identity_invalid",
        "version": "formal_case_context_invalid", "checkpoint": "execution_already_started",
        "recovered": "execution_already_started", "unpinned": "claimed_plan_pin_missing",
        "plan_drift": "claimed_plan_snapshot_mismatch", "graph_turn": "controlled_graph_input_invalid",
    }
    assert rejection.__dict__.get("recovery_reason") == expected_reason[changed]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["project_id", "run_id", "run_item_id", "reference_id", "trace_id", "attempt_id",
    "worker_id", "resource_id", "operation", "task_type", "missing_input", "payload"])
async def test_case_command_requires_its_complete_scope_and_cannot_donate_runitem_attempt_to_agenttask(source, changed):
    s = source
    raw = command(s, prepare(s)).model_dump(mode="json")
    if changed == "missing_input":
        raw.pop("case_execution_input")
    elif changed == "operation":
        raw["operation"] = "message"
    elif changed == "task_type":
        raw["task_type"] = "coordinator_child"
    elif changed == "payload":
        raw["payload"] = {"context": "a second unsealed input"}
    else:
        raw[changed] = s.attempt.id if changed == "attempt_id" else "foreign"
    with pytest.raises(ValueError):
        parse_agent_task_command(raw)


@pytest.mark.asyncio
async def test_case_input_is_revalidated_and_detached_at_command_read_boundary(source):
    s = source
    asset = prepare(s)
    raw = command(s, asset).model_dump(mode="json")
    parsed = parse_agent_task_command(raw)
    raw["case_execution_input"]["context"]["steps"][0]["content_json"]["objective"] = "caller mutation"
    assert parsed.case_execution_input.context.execution_json() == asset.context.execution_json()
    asset.context.steps[0].content_json["objective"] = "nested object mutation"
    with pytest.raises(ValueError):
        command(s, asset)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [True, "1", None])
async def test_case_input_requires_explicit_integer_version(source, version):
    raw = prepare(source).model_dump(mode="json")
    raw["schema_version"] = version
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    with pytest.raises(ValueError):
        codec().parse_controlled_case_task_input(raw)


@pytest.mark.asyncio
async def test_case_command_checks_complete_binding_not_only_session_and_turn(source):
    raw = command(source, prepare(source)).model_dump(mode="json")
    changed = raw["runtime_binding"]
    changed.pop("fingerprint")
    changed["tool_schema_version"] = "different-tools-same-turn"
    raw["runtime_binding"] = RuntimeBinding.model_validate(changed).model_dump(mode="json")
    with pytest.raises(ValueError, match="frozen scope"):
        parse_agent_task_command(raw)


@pytest.mark.asyncio
async def test_case_input_rejects_non_json_context_and_mutated_prebuilt_input(source):
    raw = prepare(source).model_dump(mode="json")
    raw["context"]["runtime_inputs"]["unpersistable"] = object()
    with pytest.raises(ValueError, match="non-persistable"):
        codec().parse_controlled_case_task_input(raw)
    asset = prepare(source)
    asset.plan_snapshot_identity.sha256 = "f" * 64
    with pytest.raises(ValueError, match="digest"):
        codec().parse_controlled_case_task_input(asset)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["future_version", "extra", "graph_version"])
async def test_case_future_formats_defer_but_digest_corruption_is_a_validation_error(source, changed):
    s = source
    raw = command(s, prepare(s)).model_dump(mode="json")
    material = raw["case_execution_input"]
    if changed == "future_version":
        material["schema_version"] = 3
    elif changed == "extra":
        material["future_field"] = 1
    else:
        material["graph_input"]["schema_version"] = 2
    with pytest.raises(TaskDeferred, match="framework_command_format_unavailable"):
        FrameworkWorkerCapabilities.require_command_format(raw)
    raw = command(s, prepare(s)).model_dump(mode="json")
    raw["case_execution_input"]["context"]["objective"] = "unsealed change"
    with pytest.raises(ValueError):
        FrameworkWorkerCapabilities.require_command_format(raw)


@pytest.mark.asyncio
async def test_current_workers_read_but_cannot_claim_or_execute_formal_case_commands(source):
    s = source
    value = command(s, prepare(s))
    record = stored_task(s.binding)
    record.__dict__.update(id=value.task_id, operation=value.operation, project_id=value.project_id,
        run_id=value.run_id, run_item_id=value.run_item_id, reference_id=value.reference_id,
        request_id=value.request_id, trace_id=value.trace_id, session_id=value.session_id, turn_id=value.turn_id)
    tasks = _Tasks(record)
    dispatcher = AgentTaskDispatcher(_Settings(), tasks, None, None,
        framework_capabilities=FrameworkWorkerCapabilities(s.binding.dependency_lock_sha256, True))
    delivery = QueuedTask("formal-case-delivery", value.model_dump(mode="json"))
    assert FrameworkWorkerCapabilities.require_command_format(delivery.payload) == value
    for _ in range(2):
        with pytest.raises(TaskDeferred, match="framework_runtime_kind_unavailable"):
            await dispatcher.handle_v2(delivery, worker_id="reader-only")
    with pytest.raises(ValueError, match="v2 worker stream"):
        await dispatcher.handle(delivery, worker_id="legacy")
    assert tasks.claimed == [] and tasks.message_recoveries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["plan_id", "turn", "target_missing", "target_duplicate", "target_version", "target_content"])
async def test_self_consistent_digest_does_not_hide_internal_scope_contradictions(source, changed):
    raw = prepare(source).model_dump(mode="json")
    context = raw["context"]
    target = next(step for step in context["steps"] if step["item"]["key"] == context["target_key"])
    if changed == "plan_id":
        raw["plan_snapshot_identity"]["plan_id"] = "another-plan"
    elif changed == "turn":
        graph = raw["graph_input"]
        binding = graph["binding"]
        binding.update(turn_id="another-turn", thread_id="another-turn")
        binding.pop("fingerprint")
        graph["binding"] = RuntimeBinding.model_validate(binding).model_dump(mode="json")
        graph["asset_sha256"] = approval_value_hash({key: value for key, value in graph.items() if key != "asset_sha256"})
    elif changed == "target_missing":
        context["target_key"] = "missing"
    elif changed == "target_duplicate":
        context["steps"].append(deepcopy(target))
    elif changed == "target_version":
        target["item"]["case_version_id"] = ""
    else:
        target["content_json"] = None
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    with pytest.raises(ValueError):
        codec().parse_controlled_case_task_input(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["attempt_no", "worker", "stored_status", "duplicate_item", "duplicate_attempt", "checkpoint_payload"])
async def test_preparation_checks_both_formal_execution_sources(source, changed):
    detail = deepcopy(source.detail)
    current = next(item for item in detail.items if item.id == source.item.id)
    attempt = next(entry for entry in detail.attempts if entry.id == source.attempt.id)
    if changed == "attempt_no":
        attempt.attempt_no += 1
    elif changed == "worker":
        attempt.worker_id = "foreign-worker"
    elif changed == "stored_status":
        current.status = "passed"
    elif changed == "duplicate_item":
        detail.items.append(deepcopy(current))
    elif changed == "duplicate_attempt":
        detail.attempts.append(deepcopy(attempt))
    else:
        attempt.checkpoint_payload = {"legacy": "already prepared"}
    with pytest.raises(ValueError):
        prepare(source, detail=detail)


@pytest.mark.asyncio
async def test_preparation_detaches_all_sources_and_logs_only_scalar_identity(source, caplog):
    from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT

    caplog.set_level("INFO")
    asset = prepare(source)
    original = asset.model_dump(mode="json")
    source.request.framework_messages[0]["data"]["content"] = "private changed graph content"
    source.detail.run.plan_snapshot.plan.request.objective = "private changed plan objective"
    source.version.content_json["objective"] = "private changed case content"
    assert asset.model_dump(mode="json") == original
    with pytest.raises(ValueError):
        prepare(source)
    records = [row for row in caplog.records if row.message.startswith("controlled_case_input_")]
    assert [row.message for row in records] == ["controlled_case_input_prepared", "controlled_case_input_preparation_rejected"]
    for row in records:
        assert row.exc_info is None
        assert "private changed" not in str(row.__dict__)
        assert source.item.lease_token not in str(row.__dict__)
    assert records[1].__dict__.get("recovery_reason") == "claimed_plan_snapshot_mismatch"
    for row in records:
        ContextFilter().filter(row)
    rendered = [ContextFormatter(LOG_FORMAT).format(row) for row in records]
    assert asset.asset_sha256 in rendered[0]
    assert "claimed_plan_snapshot_mismatch" in rendered[1]
    assert "private changed" not in "\n".join(rendered)
    assert source.item.lease_token not in "\n".join(rendered)


@pytest.mark.asyncio
@pytest.mark.parametrize("future_format", [False, True])
async def test_real_pg_reader_refuses_simulated_future_case_without_task_or_session_writes(source, harness, pg_case_project, future_format):
    """Synthetic future row, NOT a real RunItem/AgentTask admission transaction."""
    h, s = harness, source
    selected = make_binding(h.session_id, s.binding.turn_id, runtime_kind="langgraph")
    request = replace(s.request, session_id=h.session_id)
    asset = prepare(s, binding=selected, graph_request=request)
    value = command(SimpleNamespace(**{**s.__dict__, "binding": selected, "request": request}), asset)
    await pg_case_project.create(await s.env.projects.get(value.project_id))
    session = await h.store.get_session(h.session_id)
    session.project_id = value.project_id
    await h.store.save_session(session)
    # Use the existing legal message writer, then explicitly simulate a future
    # database/outbox row. No new production case writer is enabled here.
    from tests.test_runtime_binding_persistence import make_message, make_payload
    message_wire = value.model_dump(mode="json")
    message_wire.pop("case_execution_input")
    message_wire["operation"] = "message"
    message = make_message(h, selected)
    await h.tasks.submit_v2(parse_agent_task_command(message_wire), make_payload(), message,
        idempotency_key="simulated-case-reader")
    wire = value.model_dump(mode="json")
    if future_format:
        wire["case_execution_input"]["schema_version"] = 3
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.tasks._tasks} SET operation='case_execute', input_payload=%s::jsonb WHERE id=%s",
            (json.dumps(wire), value.task_id))
        cur.execute(f"UPDATE {h.settings.database.postgres_task_outbox_table} SET payload=%s::jsonb WHERE event_key=%s",
            (json.dumps(wire), f"agent_turn:{value.task_id}"))
        assert cur.rowcount == 1
    def snapshot():
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            rows = {}
            for table in [*h.tables, pg_case_project._table]:
                cur.execute(f"SELECT * FROM {table} ORDER BY 1")
                original = cur.fetchall()
                # Compare complete facts without exposing lease tokens in a
                # failed assertion's diagnostics (same as reader-admission tests).
                rows[table] = (len(original), approval_value_hash(json.loads(json.dumps(original, default=str))))
            return rows
    before = snapshot()
    async def redis_state():
        return {key: await h.redis.dump(key) async for key in h.redis.scan_iter(match=f"{h.prefix}:*")}
    redis_before = await redis_state()
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities(selected.dependency_lock_sha256, True))
    for _ in range(2):
        reason = "framework_command_format_unavailable" if future_format else "framework_runtime_kind_unavailable"
        with pytest.raises(TaskDeferred, match=reason):
            await h.dispatcher.handle_v2(QueuedTask("simulated-case", wire), worker_id="reader-only")
    assert snapshot() == before
    assert await redis_state() == redis_before
    assert (await h.tasks.get(value.task_id)).attempt_no == 0
    # The public existing message writer still cannot create this command.
    with pytest.raises(ValueError, match="only message agent_turn"):
        await h.tasks.submit_v2(value, make_payload(), message, idempotency_key="no-case-writer")
    assert snapshot() == before


def test_existing_message_wire_does_not_gain_a_null_case_input_field():
    from tests.test_agent_v2_worker_contract import binding, command as old_command

    value = old_command(binding())
    assert "case_execution_input" not in value.model_dump(mode="json")
    assert parse_agent_task_command(value.model_dump(mode="json")) == value


@pytest.mark.parametrize("module", ["src.schemas.agent_task", "src.schemas.controlled_case_task_input",
    "src.schemas.controlled_case_graph_input", "src.runtime.framework_worker_capabilities"])
def test_case_command_readers_import_in_an_independent_cold_process(module):
    result = subprocess.run([sys.executable, "-c",
        "import importlib, sys; importlib.import_module(sys.argv[1]); print('reader_import_ok')", module],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "reader_import_ok"


@pytest.mark.asyncio
async def test_cold_reader_validates_the_frozen_case_and_exports_its_nested_schema(source):
    value = command(source, prepare(source))
    result = subprocess.run([sys.executable, "-c", """
import json, sys
from src.schemas.agent_task import AgentTaskCommandV2, parse_agent_task_command
raw = json.load(sys.stdin)
parsed = parse_agent_task_command(raw)
assert parsed.model_dump(mode="json") == raw
definitions = AgentTaskCommandV2.model_json_schema()["$defs"]
assert {"ControlledCaseTaskInputAsset", "ControlledCaseGraphInputAsset", "AgentCaseExecutionContext"} <= set(definitions)
print("cold_case_validation_ok")
"""], input=json.dumps(value.model_dump(mode="json")), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "cold_case_validation_ok"
