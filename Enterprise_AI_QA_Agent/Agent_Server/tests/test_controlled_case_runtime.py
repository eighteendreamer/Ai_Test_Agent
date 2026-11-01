"""Formal PG input -> full original assembly -> governed official graph, not a verdict."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.runtime.runtime_service import RuntimeService
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.control import RuntimeControlRegistry
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent import ModelDescriptor, ToolDescriptor
from src.schemas.framework_graph_input import FrameworkProjectionIdentity
from src.schemas.framework_approval import approval_value_hash
from src.schemas.controlled_case_task_input import parse_controlled_case_task_input
from src.schemas.session import ExecutionRequest
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest, submit
from tests.test_controlled_case_execution_lease import claim, delivery
from tests.test_controlled_case_executor import execution, executor
from tests.test_controlled_case_graph import business_bridge
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _tool_reply
from tests.test_framework_model_admission import model_configuration
from tests.test_framework_turn_context import assembly_components, build_assembly


def implementation():
    return importlib.import_module("src.application.runtime.controlled_case_runtime")


def assembly(s, tmp_path, models):
    parts = assembly_components(tmp_path)
    parts.project_service = s.env.projects
    config = models.get_model_config(s.graph_request.model_key)
    parts.model_registry = SimpleNamespace(resolve_for_agent=lambda **kwargs: ModelDescriptor(
        key=config.key, name=config.name, provider=config.provider, summary="Frozen fixture model"))
    parts.tool_registry.register_dynamic(ToolDescriptor(key="inspect_source", name="Inspect source",
        category="execution", description="Read only the fixture source", permission_level="safe",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
        "inspect_source")
    agent = parts.agent_registry.resolve_for_message("").model_copy(update={
        "supported_tools": ["inspect_source", "foreign_internal", "skill"],
        "supported_models": [config.key], "default_model": config.key})
    parts.agent_registry = SimpleNamespace(resolve_for_message=lambda *args, **kwargs: agent, list=lambda: [agent])
    runtime = RuntimeService(graph=None, model_runtime_service=models, tool_runtime_service=SimpleNamespace(),
        tool_registry=parts.tool_registry, runtime_control=RuntimeControlRegistry(),
        turn_context_assembly=build_assembly(parts))
    request = ExecutionRequest(session_id=s.session.id, turn_id=s.binding.turn_id,
        trace_id=s.graph_request.trace_id, runtime_binding=s.binding, user_message=s.detail.run.plan_snapshot.plan.request.objective,
        normalized_input=s.detail.run.plan_snapshot.plan.request.objective, mode_key=s.detail.run.mode_key,
        model_key=config.key, agent_key="context-reviewer", skill_keys=["tdd-review"], context={
            "requested_tool_keys": ["inspect_source", "foreign_internal", "skill"],
            "project_context": {"description": "STALE_PROJECT_MUST_NOT_LEAK"},
            "deep_agent_harness": {"pilot_stage": "DA-E4"}, "framework_result_projection_version": 1,
            "framework_event_log_version": 1, "trusted_security_runtime_direct_execution": True,
            "framework_transcript_preparation": {"source_input_message_id": "fake-user"}})
    identity = FrameworkProjectionIdentity(binding=s.binding, assistant_message_id=str(uuid4()), snapshot_id=str(uuid4()),
        snapshot_version=s.session.snapshot_count + 1, snapshot_created_at=datetime.now(timezone.utc))
    return SimpleNamespace(parts=parts, runtime=runtime, request=request, identity=identity)


async def prepare(s, p):
    return await implementation().ControlledCaseRuntime(runtime_service=p.runtime).prepare_input(
        s.session, p.request, detail=s.detail, item=s.item, version=s.version, attempt_id=s.attempt.id,
        projection_identity=p.identity)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_full_formal_preparation_is_frozen_admitted_and_consumed_without_retrieval_on_worker(formal_case, tmp_path, transport):
    s = formal_case
    before = database_digest(s)
    original = deepcopy(s.session)
    reply = _tool_reply(transport)
    if transport == "openai_responses":
        reply["id"] = "resp_fixture_tool_call"
    with _provider(transport, first_reply=reply) as (url, requests):
        prepared = []
        async def prepare_input(models):
            p = assembly(s, tmp_path / "assembly", models)
            asset = await prepare(s, p)
            assert database_digest(s) == before and s.session == original and requests == []
            assert asset.schema_version == 4 and asset.runtime_preparation.identity == p.identity
            state = asset.runtime_preparation.initial_state
            assert state["context_bundle"]["case_execution"] == asset.context.execution_json()
            assert state["context_bundle"]["attempt_id"] == s.attempt.id
            assert state["model_visible_tool_keys"] == ["inspect_source"]
            for marker in ("FROZEN_SKILL_BODY", "OBSERVATION_CONTEXT", "MEMORY_CONTEXT", "MCP_CONTEXT"):
                assert asset.graph_input.system_prompt.count(marker) == 1
            assert "STALE_PROJECT_MUST_NOT_LEAK" not in asset.graph_input.system_prompt
            assert "CASE_CONTEXT" not in asset.graph_input.system_prompt  # No replacement case retrieval.
            assert "Complete Frozen Case and Preparation Data" in asset.graph_input.system_prompt
            assert len(asset.graph_input.framework_messages) == 1
            assert asset.graph_input.framework_messages[0]["data"]["additional_kwargs"]["source"] == "test_run.agent_execution"
            assert "framework_transcript_preparation" not in state["context_bundle"]
            assert "deep_agent_harness" not in state["context_bundle"]
            assert "framework_result_projection_version" not in state["context_bundle"]
            assert "framework_event_log_version" not in state["context_bundle"]
            assert state["context_bundle"]["trusted_security_runtime_direct_execution"] is False
            assert state["context_bundle"]["project_context"]["id"] == s.detail.run.project_id
            prepared.append((p, asset.model_dump(mode="json")))
            return asset

        async with execution(s, _config(transport, url), prepare_input=prepare_input) as h:
            p, raw = prepared[0]
            p.parts.skill_file.write_text("CHANGED_SKILL_MUST_NOT_RELOAD", encoding="utf-8")
            # A new Worker needs no assembly service; consume only original state.
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                descriptor = bridge.runtime._tool_registry.get("inspect_source").model_copy(update={
                    "description": "CHANGED_DESCRIPTOR_MUST_NOT_REPLACE_FROZEN_SURFACE"})
                bridge.runtime._tool_registry.register_dynamic(descriptor, "inspect_source")
                result = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-formal-runtime", fencing_token=1)
                assert result.graph_outcome.native_terminal is not None
                assert len(effects) == 1 and len(requests) == 2 and len(result.tool_records) == 1
                job, = await bridge.jobs.list_jobs(session_id=s.session.id)
                assert result.tool_records[0]["job_id"] == job.id and job.status.value == "completed"
                assert "FROZEN_SKILL_BODY" in str(requests[0]["body"])
                assert "CHANGED_SKILL_MUST_NOT_RELOAD" not in str(requests)
                assert "CHANGED_DESCRIPTOR_MUST_NOT_REPLACE_FROZEN_SURFACE" not in str(requests)
                assert result.state["context_bundle"]["runtime_binding"]["runtime_kind"] == "langgraph"
                events = await s.h.store.list_events(s.session.id)
                by_id = {event.id: event for event in events}
                for event in raw["runtime_preparation"]["initial_state"]["event_log"]:
                    assert by_id[event["id"]].payload == event["payload"]
                assert s.asset.model_dump(mode="json") == raw
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None
                with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                    for table in (s.h.settings.database.postgres_message_table, f"{s.h.tasks._tasks}_message_stage"):
                        cur.execute(f"SELECT count(*) AS n FROM {table}")
                        assert cur.fetchone()["n"] == 0


@pytest_asyncio.fixture
async def full_input(formal_case, tmp_path):
    s = formal_case
    config = model_configuration()
    models = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), s.h.settings)
    p = assembly(s, tmp_path / "assembly", models)
    asset = await prepare(s, p)
    return SimpleNamespace(s=s, p=p, asset=asset)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["user_row", "history", "project", "mode", "objective", "binding", "snapshot",
    "attempt", "model", "assembly", "project_source", "skill", "router_model"])
async def test_invalid_preparation_preserves_original_facts_and_reports_phase(full_input, invalid, caplog):
    s, p = full_input.s, full_input.p
    before = database_digest(s)
    if invalid == "user_row":
        p.request.user_message_id = "invented-user"
    elif invalid == "history":
        s.session.messages = [SimpleNamespace(content="not formal input")]
    elif invalid == "project":
        s.session.project_id = str(uuid4())
    elif invalid == "mode":
        p.request.mode_key = "default"
    elif invalid == "objective":
        p.request.user_message = "replaced objective"
    elif invalid == "binding":
        p.request.runtime_binding = p.request.runtime_binding.model_copy(update={"runtime_kind": "deepagents"})
    elif invalid == "snapshot":
        p.identity = p.identity.model_copy(update={"snapshot_version": 2})
    elif invalid == "attempt":
        s.attempt.id = "foreign-attempt"
    elif invalid == "model":
        p.runtime._model_runtime_service = SimpleNamespace(get_model_config=lambda key: None)
    elif invalid == "assembly":
        p.runtime._turn_context_assembly = None
    elif invalid == "project_source":
        p.parts.project_service = None
        p.runtime._turn_context_assembly = build_assembly(p.parts)
    elif invalid == "skill":
        p.parts.skill_file.unlink()  # Only this test's generated tmp_path Skill.
    else:
        p.parts.model_registry.resolve_for_agent = lambda **kwargs: ModelDescriptor(
            key="foreign", name="Foreign", provider="openai", summary="Not bound")
        p.runtime._turn_context_assembly = build_assembly(p.parts)
    with pytest.raises((ValueError, RuntimeError, FileNotFoundError, KeyError)):
        await prepare(s, p)
    assert database_digest(s) == before
    assert "controlled_case_runtime_preparation_failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["formal_context", "native_source", "prompt", "descriptor", "approval", "provider",
    "identity", "event_time", "event_id", "event_order", "tool_projection", "managed"])
async def test_rehashed_preparation_cannot_disagree_with_its_graph_or_formal_source(full_input, drift):
    raw = full_input.asset.model_dump(mode="json")
    state = raw["runtime_preparation"]["initial_state"]
    if drift == "formal_context":
        state["context_bundle"]["case_execution"]["objective"] = "foreign"
    elif drift == "native_source":
        raw["graph_input"]["framework_messages"][0]["data"]["additional_kwargs"]["source"] = "session.send_message"
        state["framework_messages"] = deepcopy(raw["graph_input"]["framework_messages"])
    elif drift == "prompt":
        state["system_prompt"] += " foreign"
    elif drift == "descriptor":
        state["context_bundle"]["framework_tool_descriptors"][0]["description"] += " foreign"
    elif drift == "approval":
        state["approval_required_tool_keys"] = ["inspect_source"]
    elif drift == "provider":
        state["selected_model_transport"] = "anthropic_messages"
    elif drift == "identity":
        raw["runtime_preparation"]["identity"]["binding"]["turn_id"] = "foreign"
    elif drift == "event_time":
        state["event_log"][0].pop("timestamp")
    elif drift == "event_id":
        state["event_log"][0].pop("id")
    elif drift == "event_order":
        state["event_log"] = list(reversed(state["event_log"]))
    elif drift == "tool_projection":
        state["context_bundle"]["framework_tool_projection_version"] = 1
    else:
        state["context_bundle"]["case_execution_managed"] = False
    graph = raw["graph_input"]
    graph["asset_sha256"] = approval_value_hash({key: value for key, value in graph.items() if key != "asset_sha256"})
    raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
    before = database_digest(full_input.s)
    with pytest.raises(ValueError):
        parse_controlled_case_task_input(raw)
    assert database_digest(full_input.s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["old_format", "no_assembly", "default_route"])
async def test_incompatible_worker_does_not_claim_prepared_case_or_rewrite_input(full_input, reader):
    s = full_input.s
    s.asset = full_input.asset
    s.command = s.command.model_copy(update={"case_execution_input": s.asset})
    await submit(s)
    before = database_digest(s)
    options = {"controlled_case_available": True}
    options.update({"old_format": {"controlled_case_input_max_version": 1},
        "no_assembly": {"turn_context_assembly_enabled": False}, "default_route": {"controlled_case_available": False}}[reader])
    with pytest.raises(TaskDeferred):
        await s.h.tasks.claim_controlled_case(delivery(s), worker_id="incompatible-prepared-case",
            framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True, **options))
    assert database_digest(s) == before


@pytest.mark.asyncio
async def test_prepared_event_ack_failure_precedes_model_and_does_not_replay_the_running_intent(formal_case, tmp_path, monkeypatch):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                original = s.h.store.append_event
                async def append(session_id, event, **kwargs):
                    if event.type == "graph.context_built":
                        raise RuntimeError("isolated preparation event ACK failure")
                    return await original(session_id, event, **kwargs)
                monkeypatch.setattr(s.h.store, "append_event", append)
                runtime = implementation().ControlledCaseRuntime(runtime_service=bridge.runtime)
                with pytest.raises(RuntimeError, match="preparation event ACK failure"):
                    await runtime.execute(h.task, executor=executor(s, h), resource_id="isolated-prepared-case", fencing_token=1)
                assert requests == [] and effects == [] and await bridge.jobs.list_jobs(session_id=s.session.id) == []
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                before = database_digest(s)
                with pytest.raises(ValueError):
                    await runtime.execute(h.task, executor=executor(s, h), resource_id="isolated-prepared-case", fencing_token=1)
                assert database_digest(s) == before and requests == []


@pytest.mark.asyncio
async def test_application_execution_does_not_upgrade_unprepared_input_or_silently_reject_it(formal_case, caplog):
    s = formal_case
    await submit(s)
    task = await claim(s)
    before = database_digest(s)
    async def forbidden(*args, **kwargs):
        pytest.fail("historical input cannot reach the executor")
    with pytest.raises(ValueError, match="full application preparation"):
        await implementation().ControlledCaseRuntime(runtime_service=SimpleNamespace()).execute(task,
            executor=SimpleNamespace(execute=forbidden), resource_id="isolated-prepared-case", fencing_token=1)
    assert database_digest(s) == before
    assert "controlled_case_runtime_execution_rejected" in caplog.text


@pytest.mark.asyncio
async def test_known_prepared_version_without_preparation_is_invalid_not_an_unknown_format(formal_case):
    raw = formal_case.command.model_dump(mode="json")
    raw["case_execution_input"]["schema_version"] = 2
    with pytest.raises(ValueError, match="runtime_preparation"):
        FrameworkWorkerCapabilities.require_command_format(raw)


@pytest.mark.asyncio
async def test_frozen_prepared_approval_wait_is_native_material_not_business_permission(formal_case, tmp_path):
    s = formal_case
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async def prepare_input(models):
            p = assembly(s, tmp_path / "assembly", models)
            descriptor = p.parts.tool_registry.get("inspect_source").model_copy(update={"permission_level": "ask"})
            p.parts.tool_registry.register_dynamic(descriptor, "inspect_source")
            return await prepare(s, p)
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                result = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-prepared-case", fencing_token=1)
                assert result.graph_outcome.native_terminal is None and len(result.graph_outcome.native_interrupts) == 1
                assert result.runtime_result is None
                assert len(requests) == 1 and effects == [] and result.tool_records == []
                assert await bridge.jobs.list_jobs(session_id=s.session.id) == []
                assert await s.h.store.list_approvals(s.session.id) == []
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None
