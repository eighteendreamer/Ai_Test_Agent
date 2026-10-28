"""Read original approval output across the terminal-to-asset gap, not execution."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
import pytest_asyncio
from deepagents import create_deep_agent

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.runtime.framework_runtime_results import FrameworkRuntimeResults
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_runtime import fresh_runtime_harness
from tests.test_framework_approval_graph_input import prepared_approval_runtime, replace_stage
from tests.test_framework_approval_continuation import claim_decision, receipt, service
from tests.test_framework_approval_recovery import expire
from tests.test_framework_tool_bridge import Model


@pytest_asyncio.fixture
async def approval_asset_gap(prepared_approval_runtime, monkeypatch, request):
    h = prepared_approval_runtime
    selected = getattr(request, "param", "approved")
    decision = selected if isinstance(selected, str) else selected["decision"]
    if isinstance(selected, dict) and selected.get("capture_budget"):
        from src.application.models.framework_model_budget_projection import capture_model_budget_material

        execute = h.runtime._deep_agent_bound_runtime_adapter.execute

        async def capture(request):
            result = await execute(request)
            # Use the production codec on the original (empty, controlled-model)
            # root ledger during execution, not a material invented by recovery.
            material = capture_model_budget_material(h.binding, await h.calls.snapshot(h.binding))
            return replace(result, metadata={**result.metadata, "root_model_budget": material})

        monkeypatch.setattr(h.runtime._deep_agent_bound_runtime_adapter, "execute", capture)
    monitor = asyncio.create_task(asyncio.Event().wait())
    captured = []
    verifications = []

    async def fail_before_asset(self, owner, **kwargs):
        # Comparison oracle only. The reader receives neither this result nor
        # any caller-supplied proof, event collection or preparation.
        captured.append(FrameworkRuntimeResult.from_result(kwargs["runtime_result"]))
        verifications.append(deepcopy(kwargs["verification_results"]))
        raise ConnectionError("injected after event seal before approval result asset")

    try:
        h.b = await claim_decision(h, decision=decision)
        await h.sessions.execute_claimed_approval(h.b, ownership_monitor=monitor)
        await h.tasks.finish(h.b, status="completed", result_status="waiting_approval")
        h.a = await claim_decision(h, key="A")
        with monkeypatch.context() as patch:
            patch.setattr(FrameworkRuntimeResults, "freeze", fail_before_asset)
            with pytest.raises(ConnectionError, match="after event seal before approval result asset"):
                await h.sessions.execute_claimed_approval(h.a, ownership_monitor=monitor)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    h.oracle, = captured
    h.verification_oracle, = verifications
    h.expired = await expire(h, h.a)
    assert "runtime_result_asset" not in h.stages.get(h.initial_task_id, h.session_id).source_manifest
    h.result_reader = FrameworkRuntimeResults(h.tasks)
    return h


async def read_original(h, monkeypatch, *, owner=None):
    reader = getattr(h.result_reader, "read_completed_approval", None)
    assert callable(reader), "The asset gap needs a production read-only original-result assembly port"
    raw = h.stages.get(h.initial_task_id, h.session_id).source_manifest["graph_input_asset"]
    flags = raw["feature_flags"]
    model = Model(responses=[])
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    options, cleanup = adapter._configure_harness(model,
        read_only_filesystem_enabled=flags["read_only_filesystem"], read_only_max_file_size_mb=10,
        context=raw["context"], cognitive_planning_enabled=flags["cognitive_planning"],
        cognitive_subagents_enabled=flags["cognitive_subagents"])

    async def forbidden(*args, **kwargs):
        pytest.fail("Read-only result assembly attempted graph/model/tool/finalization execution")

    monkeypatch.setattr(adapter, "execute", forbidden)
    monkeypatch.setattr(adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(model, "_agenerate", forbidden)
    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", forbidden)
    monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
    try:
        async with h.provider.open(owner=owner or h.a.runtime_owner) as saver:
            graph = create_deep_agent(model=model, tools=[], checkpointer=saver, **options)
            monkeypatch.setattr(graph, "ainvoke", forbidden)
            selected = {"configurable": {"thread_id": h.binding.thread_id}}
            before = await saver.aget_tuple(selected)
            try:
                return await reader(h.binding, h.a.id, agent=graph, saver=saver, continuation=service(h))
            finally:
                assert await saver.aget_tuple(selected) == before
    finally:
        if cleanup is not None:
            cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_asset_gap", ["approved", "denied",
    {"decision": "approved", "capture_budget": True}], indirect=True)
async def test_assetless_approval_result_is_assembled_from_durable_originals(approval_asset_gap, monkeypatch):
    h = approval_asset_gap
    stage = h.stages.get(h.initial_task_id, h.session_id)
    events = await h.store.list_events(h.session_id)
    snapshot = await h.store.get_latest_snapshot(h.session_id)
    budget = await h.calls.snapshot(h.binding)
    receipts = [receipt(h, key) for key in ("A", "B")]
    jobs = [await h.jobs.get_job(h.proposals[key].job.id) for key in ("A", "B")]
    effects, adapter_calls = deepcopy(h.effects), len(h.adapter_requests)
    original = await read_original(h, monkeypatch)
    assert original.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert original.preparation.task_id == h.a.id
    assert original.completion.task_id == h.a.id and original.completion.attempt_id == h.a.attempt_id
    assert (await read_original(h, monkeypatch)) == original
    assert await h.tasks.get(h.a.id) == h.expired
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert await h.store.list_events(h.session_id) == events
    assert await h.store.get_latest_snapshot(h.session_id) == snapshot
    assert await h.calls.snapshot(h.binding) == budget
    assert [receipt(h, key) for key in ("A", "B")] == receipts
    assert [await h.jobs.get_job(h.proposals[key].job.id) for key in ("A", "B")] == jobs
    assert h.effects == effects and len(h.adapter_requests) == adapter_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["completion", "event", "producer", "tool", "preparation", "budget"])
async def test_assetless_reader_refuses_changed_original_material(approval_asset_gap, monkeypatch, changed):
    h = approval_asset_gap
    events = await h.store.list_events(h.session_id)
    declaration, = [event for event in events if event.type == "runtime.event_log_completed"]
    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
        if changed == "completion":
            # Missing declarations now have a separate checkpoint-prefix read
            # contract. An existing corrupt declaration must NEVER fall back.
            cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=jsonb_set(payload, "
                "'{completion,completion_sha256}', %s::jsonb) WHERE id=%s", ('"' + '0' * 64 + '"', declaration.id))
        elif changed == "event":
            event_id = declaration.payload["completion"]["events"][-1]["event_id"]
            cur.execute(f"UPDATE {h.settings.database.postgres_event_table} SET payload=payload || %s::jsonb WHERE id=%s",
                ('{"adapter_stage":"DA-E1"}', event_id))
        elif changed == "producer":
            cur.execute(f"UPDATE {h.settings.database.postgres_agent_task_table} SET attempt_id=%s WHERE id=%s",
                (h.b.attempt_id, h.a.id))
        elif changed == "tool":
            cur.execute(f"UPDATE {h.settings.database.postgres_tool_job_table} SET output_payload=%s::jsonb WHERE id=%s",
                ('{"status":"completed","value":"foreign"}', h.proposals["A"].job.id))
        elif changed == "budget":
            cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy, '{{max_calls}}', '99') WHERE binding_id=%s",
                (h.binding.binding_id,))
    if changed == "preparation":
        stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
        stage["source_manifest"]["approval_graph_inputs"].pop(h.a.id)
        from src.schemas.framework_approval import approval_value_hash

        stage["source_sha256"] = approval_value_hash(stage["source_manifest"])
        replace_stage(h, stage)
    before = await h.tasks.get(h.a.id)
    with pytest.raises((FrameworkMessageStageRecoveryRequired, FrameworkApprovalRecoveryRequired, ValueError)):
        await read_original(h, monkeypatch)
    assert await h.tasks.get(h.a.id) == before
    assert "runtime_result_asset" not in h.stages.get(h.initial_task_id, h.session_id).source_manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("reader_kind", ["provided", "production"])
async def test_assetless_reader_rechecks_material_after_native_io(approval_asset_gap, monkeypatch, reader_kind):
    from src.runtime import framework_approval_result_reader as reader
    from src.schemas.framework_approval import approval_value_hash

    h = approval_asset_gap
    collect = reader.collect_native_terminal

    async def read_then_change(*args, **kwargs):
        original = await collect(*args, **kwargs)
        stage = h.stages.get(h.initial_task_id, h.session_id).model_dump(mode="json")
        raw = stage["source_manifest"]["approval_graph_inputs"][h.a.id]["graph_input_asset"]
        raw["runtime_preparation"]["identity"]["snapshot_version"] += 1
        raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
        stage["source_sha256"] = approval_value_hash(stage["source_manifest"])
        replace_stage(h, stage)
        return original

    monkeypatch.setattr(reader, "collect_native_terminal", read_then_change)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="changed during native read"):
        if reader_kind == "production":
            await h.runtime.read_completed_framework_approval(h.binding, h.a.id, tasks=h.tasks)
        else:
            await read_original(h, monkeypatch)
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
async def test_assetless_reader_requires_exact_original_native_producer(approval_asset_gap, monkeypatch):
    h = approval_asset_gap
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="original bound producer"):
        await read_original(h, monkeypatch, owner=h.b.runtime_owner)
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", ["api-tester"], indirect=True)
async def test_production_native_reader_rebuilds_original_without_current_execution_inputs(approval_asset_gap, monkeypatch):
    from src.application.deep_agents import runtime_adapter as module

    h = approval_asset_gap
    adapter = h.runtime._deep_agent_bound_runtime_adapter
    native_reader = getattr(adapter, "open_native_reader", None)
    assert callable(native_reader), "Production reconstruction needs its own non-executing official-schema reader"

    def forbidden(*args, **kwargs):
        pytest.fail("Native reconstruction consulted current execution inputs or executed a runtime port")

    monkeypatch.setattr(adapter, "execute", forbidden)
    monkeypatch.setattr(adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(module, "_project_root_from_context", forbidden)
    monkeypatch.setattr(module, "build_read_only_filesystem_backend", forbidden)
    monkeypatch.setattr(module.shutil, "copytree", forbidden)
    monkeypatch.setattr(h.sessions, "_finalize_runtime_result", forbidden)
    monkeypatch.setattr(h.runtime._tool_runtime_service, "record_from_job", forbidden)
    h.runtime._deep_agent_enabled = False  # New-Turn routing cannot change original read semantics.
    before = h.stages.get(h.initial_task_id, h.session_id)
    events, budget = await h.store.list_events(h.session_id), await h.calls.snapshot(h.binding)
    effects, calls = deepcopy(h.effects), len(h.adapter_requests)
    result = await h.runtime.read_completed_framework_approval(h.binding, h.a.id, tasks=h.tasks)
    assert result.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert result.verification_results == h.verification_oracle and result.verification_results
    assert await h.runtime.read_completed_framework_approval(h.binding, h.a.id, tasks=h.tasks) == result
    assert h.stages.get(h.initial_task_id, h.session_id) == before
    assert await h.store.list_events(h.session_id) == events
    assert await h.calls.snapshot(h.binding) == budget
    assert await h.tasks.get(h.a.id) == h.expired
    assert h.effects == effects and len(h.adapter_requests) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("filesystem,planning,subagents,skills", [
    (False, False, False, False), (False, True, False, False),
    (True, False, False, False), (True, True, True, False), (True, True, True, True)])
async def test_native_schema_reader_preserves_official_channels_without_live_project_or_skills(
    checkpoint_harness, tmp_path, monkeypatch, filesystem, planning, subagents, skills):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.postgres import PostgresSaver
    from src.application.deep_agents import runtime_adapter as module
    from src.application.deep_agents.native_terminal import collect_native_terminal
    from src.registry.skills import SkillRegistry
    from src.runtime.execution_context import framework_owner_scope
    from src.schemas.runtime_binding import RuntimeCheckpointSource
    from tests.test_framework_checkpoint_fencing import start
    from tests.test_framework_graph_input_asset import prepared_graph_input
    from tests.test_framework_approval_graph_input import rehash

    h = checkpoint_harness
    context = {"project_root": str(tmp_path)}
    skill_root = tmp_path / "original-skills"
    skill_file = skill_root / "fixture-skill" / "SKILL.md"
    if skills:
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("---\nname: fixture-skill\ndescription: Original schema fixture\n---\nOriginal instructions", encoding="utf-8")
        context["skill_keys"] = ["fixture-skill"]
    responses = [AIMessage(id="schema-final", content="original completed output")]
    if planning:
        responses.insert(0, AIMessage(id="schema-plan", content="", tool_calls=[{
            "name": "write_todos", "id": "schema-todo", "type": "tool_call",
            "args": {"todos": [{"content": "Read original evidence", "status": "completed"}]}}]))
    model = Model(responses=responses)

    async def resolve(key):
        return model

    adapter = module.DeepAgentRuntimeAdapter(model_resolver=resolve, checkpointer_factory=h.provider.open,
        skill_registry=SkillRegistry(skills_root=skill_root) if skills else None)
    await start(h)
    with framework_owner_scope(h.owner.runtime_owner):
        result = await adapter.execute(module.DeepAgentRuntimeRequest(session_id=h.session_id,
            turn_id=h.binding.turn_id, trace_id=h.owner.trace_id, model_key="primary", system_prompt="original",
            messages=[{"role": "user", "content": "Read original evidence"}],
            checkpoint_namespace=h.binding.checkpoint_namespace, context=context,
            read_only_filesystem_enabled=filesystem, cognitive_planning_enabled=planning,
            cognitive_subagents_enabled=subagents))
    proof = result.metadata["native_terminal"]
    from src.application.deep_agents.model_call_middleware import configure_model_policy
    from typing import get_type_hints
    from typing_extensions import is_typeddict

    def channel_type(value):
        # create_agent allocates a new InputSchema class per graph. Compare all
        # typed fields/metadata and requiredness, not process-local class IDs.
        return ((get_type_hints(value, include_extras=True), value.__required_keys__, value.__optional_keys__)
            if is_typeddict(value) else value)

    def channel_schema(graph):
        return {key: (type(channel), channel_type(channel.ValueType), channel_type(channel.UpdateType))
            for key, channel in graph.channels.items()}

    # Independently compare the real governed middleware schema. No policy is
    # executed here: its request/budget semantics have their existing suites.
    options, cleanup = adapter._configure_harness(model, read_only_filesystem_enabled=filesystem,
        read_only_max_file_size_mb=10, context=context, cognitive_planning_enabled=planning,
        cognitive_subagents_enabled=subagents)
    try:
        governed = create_deep_agent(model=model, tools=[], **configure_model_policy(options,
            policy=object(), owner=h.owner.runtime_owner, model=model, model_key="primary"))
        governed_channels = channel_schema(governed)
    finally:
        if cleanup is not None:
            cleanup()
    raw = prepared_graph_input(h.binding, h.owner.trace_id).model_dump(mode="json")
    raw.update(context=context, feature_flags={"read_only_filesystem": filesystem, "cognitive_planning": planning,
        "cognitive_subagents": subagents, "governed_tools": False})
    raw = rehash(raw)
    source = RuntimeCheckpointSource.model_validate(h.owner.runtime_owner.model_dump(exclude={"owner_token"}))
    await h.provider.close()
    assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)

    def forbidden(*args, **kwargs):
        pytest.fail("Schema-only read resolved current execution material or performed checkpoint setup")

    monkeypatch.setattr(adapter, "_model_resolver", forbidden)
    monkeypatch.setattr(module, "_project_root_from_context", forbidden)
    monkeypatch.setattr(module, "build_read_only_filesystem_backend", forbidden)
    monkeypatch.setattr(module.shutil, "copytree", forbidden)
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(PostgresSaver, "setup", forbidden)
    if skills:
        monkeypatch.setattr(adapter._skill_registry, "get_many", forbidden)
        skill_file.write_text("Current instructions must not replace originals", encoding="utf-8")
    with framework_owner_scope(h.owner.runtime_owner):
        async with adapter.open_native_reader(raw, source=source) as (graph, saver):
            assert channel_schema(graph) == governed_channels
            config = {"configurable": {"thread_id": h.binding.thread_id, "checkpoint_id": proof["checkpoint_id"]}}
            original = await saver.aget_tuple(config)
            native = await collect_native_terminal(graph, saver, config)
            assert native.proof.model_dump(mode="json") == proof
            if planning:
                assert native.values["todos"] == [{"content": "Read original evidence", "status": "completed"}]
            if skills:
                assert native.values["skills_metadata"][0]["description"] == "Original schema fixture"
            assert not hasattr(saver, "runtime_owner")
            with pytest.raises(PermissionError):
                await graph.ainvoke(None, config=config, durability="sync")
            assert await saver.aget_tuple(config) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["unknown", "nonboolean", "missing", "foreign"])
async def test_native_schema_reader_rejects_unknown_topology_before_opening_any_store(changed):
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter, DeepAgentRuntimeError
    from src.schemas.runtime_binding import RuntimeCheckpointSource
    from tests.test_framework_graph_input_asset import prepared_graph_input
    from tests.test_runtime_binding_persistence import make_binding
    from tests.test_framework_approval_graph_input import rehash

    binding = make_binding("schema-session", "schema-turn")
    source = RuntimeCheckpointSource(binding=binding, task_id="original", attempt_id="original-attempt",
        worker_id="original-worker", owner_epoch=1)
    raw = prepared_graph_input(binding, "schema-trace").model_dump(mode="json")
    raw["feature_flags"] = {"read_only_filesystem": False, "cognitive_planning": False,
        "cognitive_subagents": False, "governed_tools": False}
    if changed == "unknown":
        raw["feature_flags"]["unknown_stateful_middleware"] = True
    elif changed == "nonboolean":
        raw["feature_flags"]["cognitive_subagents"] = "false"
    elif changed == "missing":
        raw["feature_flags"].pop("cognitive_planning")
    else:
        source = source.model_copy(update={"binding": make_binding("foreign-session", "foreign-turn")})

    def forbidden(*args, **kwargs):
        pytest.fail("Unknown native schemas must be rejected before model or checkpoint I/O")

    adapter = DeepAgentRuntimeAdapter(model_resolver=forbidden, checkpointer_factory=forbidden)
    with pytest.raises(DeepAgentRuntimeError, match="schema"):
        async with adapter.open_native_reader(rehash(raw), source=source):
            pytest.fail("Unsupported native schema was accepted")


@pytest.mark.asyncio
async def test_assetless_reader_cannot_omit_unprepared_business_job(approval_asset_gap, monkeypatch):
    h = approval_asset_gap
    original = await h.jobs.get_job(h.proposals["A"].job.id)
    # An extra settled business fact cannot silently disappear merely because
    # its original application material is absent from this preparation.
    extra = original.model_copy(update={"id": original.id + "-extra", "call_id": original.call_id + "-extra", "metadata": {}})
    await h.jobs._store.save_job(extra)
    with pytest.raises(FrameworkMessageStageRecoveryRequired, match="unprepared business tool"):
        await read_original(h, monkeypatch)
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", ["api-tester"], indirect=True)
async def test_original_verifications_are_prepared_before_graph_and_rebuilt_without_new_decisions(approval_asset_gap, monkeypatch):
    from src.application.testing import verification_service as module

    h = approval_asset_gap
    manifest = h.stages.get(h.initial_task_id, h.session_id).source_manifest
    initial = manifest["graph_input_asset"]["runtime_preparation"]["initial_state"]["context_bundle"]
    original = manifest["approval_graph_inputs"][h.a.id]["graph_input_asset"]["runtime_preparation"]["initial_state"]["context_bundle"]
    key = "framework_verification_preparation"
    assert key in initial, "Original Verification rule/identity preparation must precede the initial graph"
    assert original[key] == initial[key] and h.verification_oracle

    def forbidden(*args, **kwargs):
        pytest.fail("Read-only verification used a new identity/clock or current security profiles")

    monkeypatch.setattr(module, "uuid4", forbidden)
    monkeypatch.setattr(module, "datetime", type("NoClock", (), {"utcnow": staticmethod(forbidden)}))
    monkeypatch.setattr(module, "get_profile_registry", forbidden)
    rebuilt = await read_original(h, monkeypatch)
    assert rebuilt.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert rebuilt.verification_results == h.verification_oracle
    assert len(rebuilt.verification_results) == 2
    assert all(result.status.value == "passed" for result in rebuilt.verification_results)
    assert await h.tasks.get(h.a.id) == h.expired


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared_approval_runtime", [{"tool_key": "api-tester", "legacy_verification": True}], indirect=True)
async def test_legacy_original_without_verification_preparation_does_not_invent_empty_verdicts(approval_asset_gap, monkeypatch):
    from src.application.testing.verification_service import VerificationService

    h = approval_asset_gap
    stage = h.stages.get(h.initial_task_id, h.session_id)
    assert stage.schema_version == 16 and h.verification_oracle

    def forbidden(*args, **kwargs):
        pytest.fail("Legacy originals cannot reconstruct Verification with today's rules")

    monkeypatch.setattr(VerificationService, "build_results", forbidden)
    rebuilt = await read_original(h, monkeypatch)
    assert rebuilt.verification_results is None
    assert rebuilt.runtime_result.model_dump(mode="json") == h.oracle.model_dump(mode="json")
    assert h.stages.get(h.initial_task_id, h.session_id) == stage
    assert await h.tasks.get(h.a.id) == h.expired
