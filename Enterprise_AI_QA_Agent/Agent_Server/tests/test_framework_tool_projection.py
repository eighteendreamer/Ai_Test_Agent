"""Original tool projection materials are private data, never execution proof."""
from copy import deepcopy
from datetime import datetime
import asyncio
import json
import multiprocessing
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.deep_agents.framework_transcript import deserialize_messages, serialize_messages
from src.application.runtime.tool_messages import build_chat_tool_messages
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.framework_approval import FrameworkToolCallIdentity, approval_value_hash, framework_tool_call_id
from src.schemas.framework_tool_projection import FrameworkToolProjection, TOOL_PROJECTION_KEY
from src.schemas.tool_runtime import ToolExecutionRecord
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_runtime import fresh_runtime_harness
from tests.test_runtime_binding_persistence import make_binding


def sample():
    binding = make_binding("original-session", "original-turn")
    source = FrameworkToolCallIdentity(checkpoint_namespace="", source_message_id="original-ai",
        tool_call_id="provider-call", tool_name="inspect_value", arguments={"value": "original"})
    record = ToolExecutionRecord(call_id=framework_tool_call_id(binding, source), job_id="original-job",
        tool_key=source.tool_name, tool_name="Inspect value", status="completed", summary="original evidence",
        input=source.arguments, trace_id="original-trace", started_at=datetime(2026, 10, 27, 1),
        completed_at=datetime(2026, 10, 27, 1, 2),
        output={"z": "first", "a": {"z": "second", "a": [1, 2]}, "artifact": {"uri": "fixture://original"}})
    display, = build_chat_tool_messages(binding.turn_id, [record.model_dump(mode="python")],
        binding_id=binding.binding_id, hygiene_metadata=TranscriptHygieneService().tool_message_metadata())
    content = json.dumps(record.output, ensure_ascii=False)
    material = FrameworkToolProjection.capture(binding=binding, source=source, record=record,
        message=display, model_content=content)
    return binding, source, material


def test_original_material_survives_official_native_serialization_and_does_not_mutate_inputs():
    binding, source, material = sample()
    original = deepcopy(material)
    message = ToolMessage(content=material.model_content, name=source.tool_name, tool_call_id=source.tool_call_id,
        artifact={TOOL_PROJECTION_KEY: material.model_dump(mode="json")})
    restored, = deserialize_messages(serialize_messages([message]))
    assert restored == message
    assert FrameworkToolProjection.model_validate(restored.artifact[TOOL_PROJECTION_KEY]).require_binding(binding, source) == original
    assert material == original and material.scope == "tool_projection_material_only"


def test_capture_normalizes_interrupt_source_without_relaxing_the_identity_schema():
    from src.schemas.framework_approval import NativeToolInterrupt

    binding, source, material = sample()
    interrupt = NativeToolInterrupt(**source.model_dump(mode="python"), checkpoint_id="original-checkpoint",
        graph_task_id="original-task", task_path="original-path", interrupt_id="original-interrupt", resume_index=0)
    copied = FrameworkToolProjection.capture(binding=binding, source=interrupt, record=material.record,
        message=material.message, model_content=material.model_content)
    assert copied == material
    with pytest.raises(ValueError):
        FrameworkToolCallIdentity.model_validate(interrupt.model_dump(mode="json"))


@pytest.mark.parametrize("changed", ["binding", "source", "call", "tool", "message", "role", "turn", "status", "ordinal", "digest", "future", "scope"])
def test_material_rejects_corruption_or_wrong_bound_source_even_after_rehash(changed):
    binding, source, material = sample()
    raw = material.model_dump(mode="json")
    if changed == "binding":
        raw["binding_fingerprint"] = "other-binding"
    elif changed == "source":
        raw["source"]["source_message_id"] = "reused-id-different-ai"
    elif changed == "call":
        raw["record"]["call_id"] = "other-call"
    elif changed == "tool":
        raw["record"]["tool_key"] = "other-tool"
    elif changed in {"message", "role"}:
        raw["message"]["id" if changed == "message" else "role"] = "other-message" if changed == "message" else "user"
    elif changed in {"turn", "status", "ordinal"}:
        raw["message"]["metadata"][{"turn": "turn_id", "status": "status", "ordinal": "ordinal"}[changed]] = True
    elif changed == "future":
        raw["schema_version"] = 2
    elif changed == "scope":
        raw["scope"] = "allow_recovery"
    if changed != "digest":
        raw["sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "sha256"})
    else:
        raw["model_content"] += "changed"
    with pytest.raises(ValueError):
        FrameworkToolProjection.model_validate(raw).require_binding(binding, source)


@pytest.mark.asyncio
async def test_jsonb_key_reordering_preserves_original_display_string_and_model_content(harness):
    binding, source, material = sample()
    with postgres_connect(harness.settings) as conn, conn.cursor() as cur:
        cur.execute("SELECT %s::jsonb AS original", (material.model_dump_json(),))
        saved = cur.fetchone()["original"]
    restored = FrameworkToolProjection.model_validate(saved).require_binding(binding, source)
    assert restored == material
    assert restored.message.content == material.message.content and restored.model_content == material.model_content
    # The JSON object order is not a safe source for re-rendering the original
    # strings. Preserve both strings instead of weakening display equality.
    assert list(restored.record.output) != list(material.record.output)
    # The normal application consumer must use the preserved string too; a
    # material saved successfully but ignored during rendering is insufficient.
    projected = build_chat_tool_messages(binding.turn_id, [restored.record.model_dump(mode="python")],
        binding_id=binding.binding_id, hygiene_metadata=TranscriptHygieneService().tool_message_metadata(),
        original_materials=[restored])
    assert projected == [material.message]


@pytest.mark.parametrize("changed", ["record", "ordinal", "published", "duplicate", "unbound"])
def test_original_material_consumer_does_not_hide_record_slot_or_published_conflicts(changed):
    from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired

    binding, source, material = sample()
    record = material.record.model_dump(mode="python")
    materials, previous = [material], []
    if changed == "record":
        record["output"]["new"] = "changed"
    elif changed == "ordinal":
        record = [dict(record, call_id="pending", status="waiting_approval"), record]
    elif changed == "published":
        previous = [material.message.model_copy(update={"content": "changed"})]
    elif changed == "duplicate":
        other = material.model_dump(mode="json")
        other["model_content"] += "changed"
        other["sha256"] = approval_value_hash({key: value for key, value in other.items() if key != "sha256"})
        materials.append(FrameworkToolProjection.model_validate(other))
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="Original tool projection"):
        build_chat_tool_messages(binding.turn_id, record if isinstance(record, list) else [record],
            binding_id=None if changed == "unbound" else binding.binding_id,
            hygiene_metadata=TranscriptHygieneService().tool_message_metadata(),
            previous_messages=previous, original_materials=materials)


@pytest.mark.parametrize("changed", ["call", "name", "status", "missing", "future", "source"])
def test_native_material_reader_rejects_wrong_native_identity_or_missing_format(changed):
    binding, source, material = sample()
    message = ToolMessage(content=material.model_content, name=source.tool_name, tool_call_id=source.tool_call_id,
        artifact={TOOL_PROJECTION_KEY: material.model_dump(mode="json")})
    if changed == "call":
        message.tool_call_id = "other-call"
    elif changed == "name":
        message.name = "other-tool"
    elif changed == "status":
        message.status = "error"
    elif changed == "missing":
        message.artifact = None
    elif changed == "future":
        message.artifact[TOOL_PROJECTION_KEY]["schema_version"] = 2
    else:
        source = source.model_copy(update={"source_message_id": "other-ai"})
    with pytest.raises(ValueError):
        FrameworkToolProjection.from_tool_message(message, binding=binding, source=source)


@pytest.mark.asyncio
async def test_official_large_result_offload_preserves_private_original_material(tmp_path):
    from deepagents.backends import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware

    binding, source, material = sample()
    message = ToolMessage(content=material.model_content * 100, name=source.tool_name, tool_call_id=source.tool_call_id,
        artifact={TOOL_PROJECTION_KEY: material.model_dump(mode="json")})
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    middleware = FilesystemMiddleware(backend=backend, tool_token_limit_before_evict=1)
    evicted = await middleware._aintercept_large_tool_result(message)
    assert evicted.content != message.content and evicted.tool_call_id == message.tool_call_id
    assert FrameworkToolProjection.from_tool_message(evicted, binding=binding, source=source) == material


def worker_paused_before_application_tool_result(settings_raw, owner_raw, request_raw, identity_raw, gate):
    """Real SDK/PG tool callback in a process that never exports a Runtime result."""
    from redis.asyncio import Redis
    from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
    from src.application.runtime.tool_job_service import ToolJobService
    from src.core.config import Settings
    from src.registry.tools import ToolRegistry
    from src.runtime.execution_context import framework_owner_scope
    from src.runtime.framework_message_stages import FrameworkMessageStages
    from src.runtime.postgres_agent_task_store import PostgresAgentTaskStore
    from src.runtime.postgres_session_store import PostgresSessionStore
    from src.runtime.postgres_tool_job_store import PostgresToolJobStore
    from src.runtime.session_event_sink import SessionEventSink
    from src.schemas.agent import ToolDescriptor
    from src.schemas.framework_graph_input import FrameworkProjectionIdentity
    from src.schemas.runtime_binding import RuntimeExecutionOwner
    from src.schemas.session import ExecutionRequest
    from tests.test_framework_approval_runtime import build_runtime
    from langchain_core.messages import AIMessage

    async def run():
        settings = Settings.model_validate(settings_raw)
        owner = RuntimeExecutionOwner.model_validate(owner_raw)
        request = ExecutionRequest.model_validate(request_raw)
        identity = FrameworkProjectionIdentity.model_validate(identity_raw)
        store = PostgresSessionStore(settings)
        tasks = PostgresAgentTaskStore(settings, store)
        registry = ToolRegistry()
        registry.register_dynamic(ToolDescriptor(key="inspect_value", name="Inspect value", category="execution",
            description="Inspect a test value", permission_level="safe", input_schema={"type": "object",
                "properties": {"value": {"type": "string"}}, "required": ["value"]}), "inspect_value")
        provider = DeepAgentCheckpointProvider(settings)
        redis = Redis.from_url(settings.database.redis_url, decode_responses=True)
        h = SimpleNamespace(settings=settings, store=store, registry=registry,
            jobs=ToolJobService(PostgresToolJobStore(settings)), provider=provider, effects=[])
        build_runtime(h, responses=[AIMessage(id="kill-source", content="", tool_calls=[{
            "name": "inspect_value", "args": {"value": "original"}, "id": "kill-call", "type": "tool_call"}]),
            AIMessage(content="finished")])
        adapter = h.runtime._deep_agent_bound_runtime_adapter
        execute = adapter.execute

        async def pause(selected):
            result = await execute(selected)
            await redis.set(gate + ":effects", json.dumps(h.effects))
            await redis.rpush(gate, json.dumps(result.metadata["native_terminal"]))
            await asyncio.Event().wait()

        adapter.execute = pause
        sink = SessionEventSink(store, request.session_id, capacity=1, fence={}, runtime_owner=owner)

        async def freeze(raw):
            await FrameworkMessageStages(tasks).record_graph_input(owner, raw)

        try:
            with framework_owner_scope(owner):
                await sink.run(h.runtime.execute_turn, await store.get_session(request.session_id), request,
                    projection_identity=identity, on_graph_input=freeze)
        finally:
            await redis.aclose()
            await provider.close()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_independent_process_kill_retains_original_tool_material_without_runtime_result(fresh_runtime_harness):
    from deepagents import create_deep_agent
    from redis.asyncio import Redis
    from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER
    from src.application.deep_agents.native_terminal import NativeTerminalProof, _native_hash
    from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest
    from src.schemas.framework_graph_input import FrameworkProjectionIdentity
    from src.schemas.session import ExecutionRequest, SendMessageRequest
    from tests.test_framework_tool_bridge import Model

    h = fresh_runtime_harness
    stages = FrameworkMessageStages(h.tasks)
    payload = SendMessageRequest.model_validate(h.owner.input)
    session = await h.store.get_session(h.session_id)
    request = ExecutionRequest(session_id=h.session_id, turn_id=h.binding.turn_id, trace_id=h.owner.trace_id,
        user_message=payload.content, normalized_input=payload.content, mode_key="default", agent_key="coordinator",
        model_key="fake", runtime_binding=h.binding, context={"requested_tool_keys": ["inspect_value"]})
    identity = FrameworkProjectionIdentity(binding=h.binding, assistant_message_id="original-assistant",
        snapshot_id="original-snapshot", snapshot_version=1, snapshot_created_at=datetime(2026, 10, 27))
    await stages.begin(h.owner.runtime_owner, message_source_manifest(session, h.binding, payload))
    await stages.freeze_execution(h.owner.runtime_owner, request)
    await stages.submit_runtime(h.owner.runtime_owner)
    gate = h.prefix + ":native-tool-material"
    process = multiprocessing.get_context("spawn").Process(target=worker_paused_before_application_tool_result,
        args=(h.settings.model_dump(mode="json"), h.owner.runtime_owner.model_dump(mode="json"),
            request.model_dump(mode="json"), identity.model_dump(mode="json"), gate))
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=50)
    try:
        process.start()
        reached = await waiting.blpop(gate, timeout=45)
        assert reached is not None, f"tool worker exit code: {process.exitcode}"
        proof = NativeTerminalProof.model_validate_json(reached[1])
        process.terminate()
        await asyncio.to_thread(process.join, 10)
        assert not process.is_alive() and process.exitcode != 0
        saved_stage = await h.tasks.get_framework_message_stage_version(h.owner.id, session_id=h.session_id)
        assert saved_stage == 14
        before = await h.tasks.get_framework_message_stage(h.owner.id, session_id=h.session_id)
        assert "runtime_result_asset" not in before.source_manifest
        assert await h.store.get_latest_snapshot(h.session_id) is None
        selected = {"configurable": {"thread_id": h.binding.thread_id,
            "checkpoint_ns": h.binding.checkpoint_namespace, "checkpoint_id": proof.checkpoint_id}}
        async with h.provider.open() as saver:
            saved = await saver.aget_tuple(selected)
            assert _native_hash(saver.serde, saved.checkpoint) == proof.checkpoint_sha256
            reader = create_deep_agent(model=Model(responses=[]), checkpointer=saver)
            snapshot = await reader.aget_state({"configurable": {
                **selected["configurable"], CONFIG_KEY_CHECKPOINTER: saver}})
        native, = [message for message in snapshot.values["messages"] if isinstance(message, ToolMessage)]
        material = FrameworkToolProjection.model_validate(native.artifact[TOOL_PROJECTION_KEY])
        source = FrameworkToolCallIdentity(checkpoint_namespace="", source_message_id="kill-source",
            tool_call_id="kill-call", tool_name="inspect_value", arguments={"value": "original"})
        material.require_binding(h.binding, source)
        jobs = await h.jobs.list_jobs(session_id=h.session_id)
        job, = jobs
        assert material.record.job_id == job.id and material.record.status == job.status.value == "completed"
        assert material.record.output["value"] == "original" and material.record.completed_at == job.completed_at
        assert json.loads(await h.redis.get(gate + ":effects")) == [["original", job.id]]
        assert material.message.created_at == job.completed_at and material.message.metadata["ordinal"] == 1
        assert len(await h.jobs.list_jobs(session_id=h.session_id)) == 1
        assert await h.tasks.get_framework_message_stage(h.owner.id, session_id=h.session_id) == before
        # Read-only material retrieval is not full RuntimeTurnResult recovery.
        assert await h.store.get_latest_snapshot(h.session_id) is None
    finally:
        await waiting.aclose()
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
