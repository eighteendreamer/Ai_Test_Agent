"""Hard kill after the real native graph returns, before application projection."""
from __future__ import annotations

import asyncio
import multiprocessing
import json
from pathlib import Path
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from deepagents import create_deep_agent
from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER

from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
from src.application.deep_agents.native_terminal import NativeTerminalProof, _native_hash
from src.application.models.framework_model_call_policy import model_config_fingerprint
from src.application.models.framework_model_budget_projection import read_model_budget_material
from src.application.models.framework_model_evidence import FrameworkModelEvidence
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from tests.test_agent_task_dispatch import harness
from tests.test_framework_application_projection import worker_waiting_after_application_commit
from tests.test_framework_chat_models import _config, _provider
from tests.test_framework_model_admission import admission, submit
from tests.test_framework_message_recovery import isolate_recovery_stores
from tests.test_framework_message_stages import stage
from tests.test_framework_native_terminal import Model
from tests.test_runtime_binding_persistence import make_command


class LocalModelObjects:
    """Durable, cross-process test backend; NOT real RustFS acceptance evidence."""
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)

    async def store_uploaded_bytes(self, *, content, filename, object_prefix, content_type):
        key = uuid4().hex + ".bin"
        (self.root / key).write_bytes(content)
        return {"uri": "rustfs://terminal-fixture/" + key}

    async def read_object_uri(self, uri):
        prefix = "rustfs://terminal-fixture/"
        assert uri.startswith(prefix)
        target = (self.root / uri[len(prefix):]).resolve(strict=True)
        assert target.parent == self.root
        return {"content": target.read_bytes()}


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["after_graph_return", "after_graph_events", "after_event_completion"])
async def test_native_terminal_and_settled_response_survive_kill_but_do_not_invent_application_result(admission, tmp_path, window):
    h = admission
    h.settings.orchestration.resource_default_lease_seconds = 30
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=45)
    provider = DeepAgentCheckpointProvider(h.settings)
    process = None
    try:
        with _provider("openai_chat_completions") as (url, requests):
            config = _config("openai_chat_completions", url)
            h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
            h.command = make_command(h, h.binding)
            h.payload = h.payload.model_copy(update={"model_key": config.key, "mode_key": "code_review"})
            if window == "after_graph_events":
                h.payload = h.payload.model_copy(update={"context": {**h.payload.context, "project_root": str(tmp_path)}})
            h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
            original = await submit(h)
            queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
                group="projection-kill", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            gate = h.prefix + ":native-graph-returned"
            process = multiprocessing.get_context("spawn").Process(target=worker_waiting_after_application_commit,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate,
                    window, str(tmp_path)))
            process.start()
            reached = await waiting.blpop(gate, timeout=40)
            assert reached is not None, f"worker exit code: {process.exitcode}"
            proof = NativeTerminalProof.model_validate_json(reached[1])
            assert proof.binding_fingerprint == h.binding.fingerprint
            active = await h.tasks.get(original.id)
            before = await stage(h, original)
            budget = await h.calls.snapshot(h.binding)
            call, = budget.calls
            assert call.phase == "completed" and call.request is not None and call.settled_response_id
            response, = [item for item in call.response_observations if item.observation_id == call.settled_response_id]
            evidence = FrameworkModelEvidence(objects=LocalModelObjects(tmp_path))
            assert await evidence.read(call.request)
            saved_response = await evidence.read_response(h.binding, call.intent, response.response)
            assert saved_response["kind"] == "langchain_messages"
            selected = {"configurable": {"thread_id": h.binding.thread_id,
                "checkpoint_ns": h.binding.checkpoint_namespace, "checkpoint_id": proof.checkpoint_id}}
            async with provider.open() as saver:
                native = await saver.aget_tuple(selected)
                assert native is not None and _native_hash(saver.serde, native.checkpoint) == proof.checkpoint_sha256
                # Messages use DeltaChannel in the pinned real graph. Read
                # through its channel implementation, not channel_values or
                # guessed message deltas. The pinned config override selects
                # the physical namespace without treating it as a subgraph.
                model = Model(responses=[])
                harness_kwargs, cleanup = {}, None
                if window == "after_graph_events":
                    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
                    from src.application.deep_agents.model_call_middleware import configure_model_policy
                    from src.registry.skills import SkillRegistry

                    # Rebuild the producer's channel schema, not the E1 graph:
                    # Skills/subagent middleware contributes additional state.
                    adapter = DeepAgentRuntimeAdapter(model_resolver=None, skill_registry=SkillRegistry())
                    harness_kwargs, cleanup = adapter._configure_harness(model,
                        read_only_filesystem_enabled=True, cognitive_subagents_enabled=True,
                        read_only_max_file_size_mb=10,
                        context=before.source_manifest["graph_input_asset"]["context"])
                    harness_kwargs = configure_model_policy(harness_kwargs,
                        policy=None, owner=active.runtime_owner, model=model, model_key=config.key)
                try:
                    graph = create_deep_agent(model=model, tools=[], checkpointer=saver, **harness_kwargs)
                    snapshot = await graph.aget_state({"configurable": {
                        **selected["configurable"], CONFIG_KEY_CHECKPOINTER: saver}})
                finally:
                    if cleanup is not None:
                        cleanup()
                assert not snapshot.next and not snapshot.tasks and not snapshot.interrupts
                assert _native_hash(saver.serde, snapshot.values) == proof.state_sha256
                assert snapshot.values["messages"][-1].content == "fixture answer"
            assert before.phase == "runtime_submitted" and "graph_input_asset" in before.source_manifest
            prepared = before.source_manifest["graph_input_asset"]
            assert prepared["schema_version"] == 2
            assert before.schema_version >= 12
            identity = prepared["runtime_preparation"]["identity"]
            assert identity["binding"]["binding_id"] == h.binding.binding_id
            assert identity["assistant_message_id"] and identity["snapshot_id"]
            assert identity["snapshot_created_at"] and identity["snapshot_version"] == 1
            initial = prepared["runtime_preparation"]["initial_state"]
            assert initial["session_id"] == h.session_id and initial["turn_id"] == h.binding.turn_id
            assert initial["selected_model_key"] == config.key
            assert initial["context_bundle"]["runtime_binding"] == h.binding.model_dump(mode="json")
            assert initial["event_log"][0]["type"] == "runtime.turn_started"
            assert "runtime_result_asset" not in before.source_manifest
            # Preparation retains the initial event only, not all later events
            # or callback-only subagent facts. It does not admit gap recovery.
            assert len(initial["event_log"]) == 1
            assert await h.store.get_latest_snapshot(h.session_id) is None
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

            if window == "after_event_completion":
                read = await h.store.read_runtime_event_completion(h.binding, proof.model_dump(mode="json"))
                assert read.completion.task_id == active.id and read.completion.attempt_id == active.attempt_id
                assert read.completion.returned_event_offset == 0
                assert read.event_log[0] == initial["event_log"][0]
                assert [item["type"] for item in read.event_log] == ["runtime.turn_started", "runtime.turn_completed"]
                assert all(item["type"] != "runtime.event_log_completed" for item in read.event_log)
                from src.application.deep_agents.result_projection import project_deep_agent_result
                from src.application.deep_agents.framework_transcript import serialize_messages

                # Stage/callback facts come from the verified complete event
                # collection, not caller defaults or native display text.
                assert not any(prepared["feature_flags"][key] for key in (
                    "read_only_filesystem", "cognitive_planning", "cognitive_subagents", "governed_tools"))
                from src.application.runtime.turn_projection import project_deep_agent_callback_facts
                facts = project_deep_agent_callback_facts(read.event_log, turn_id=h.binding.turn_id,
                    returned_event_offset=read.completion.returned_event_offset)
                original_budget = read.event_log[-1]["payload"]["root_model_budget"]
                assert read_model_budget_material(h.binding, original_budget) == await h.calls.snapshot(h.binding)
                projected = project_deep_agent_result(snapshot.values, stage=facts.adapter_stage, subagent_events=facts.subagent_events,
                    native_terminal=proof.model_dump(mode="json"), root_model_budget=original_budget)
                assert projected.output_text == "fixture answer"
                assert projected.framework_messages == serialize_messages(snapshot.values["messages"])
                assert projected.metadata["native_terminal"] == proof.model_dump(mode="json")
                assert projected.todos == [] and projected.subagent_events == []
                from src.application.runtime.turn_projection import (
                    completed_deep_agent_state, project_completed_turn, project_runtime_snapshot,
                )
                from src.schemas.framework_graph_input import parse_graph_input_asset
                from src.schemas.session import ExecutionEvent

                preparation = parse_graph_input_asset(prepared).runtime_preparation
                projected_state = completed_deep_agent_state(preparation.initial_state, projected,
                    tool_records=[], binding=h.binding, pilot_stage=facts.pilot_stage)
                projected_state["event_log"] = read.event_log
                projected_snapshot = project_runtime_snapshot(session_id=h.session_id, state=projected_state,
                    snapshot_id=preparation.identity.snapshot_id, version=preparation.identity.snapshot_version,
                    created_at=preparation.identity.snapshot_created_at)
                projected_turn = project_completed_turn(state=projected_state, snapshot=projected_snapshot,
                    events=[ExecutionEvent(session_id=h.session_id, **entry) for entry in read.event_log], tool_messages=[])
                assert projected_turn.output_text == "fixture answer"
                assert projected_turn.snapshot.id == identity["snapshot_id"]
                assert [event.id for event in projected_turn.events] == [entry["id"] for entry in read.event_log]
                assert projected_turn.state["framework_messages"] == projected.framework_messages
                assert await h.store.get_latest_snapshot(h.session_id) is None
                assert "runtime_result_asset" not in (await stage(h, original)).source_manifest
            else:
                with pytest.raises(FrameworkMessageStageRecoveryRequired, match="missing"):
                    await h.store.read_runtime_event_completion(h.binding, proof.model_dump(mode="json"))
            if window == "after_graph_events":
                from src.schemas.session import ExecutionEvent
                from src.runtime.framework_application_projection import _require_event_intent

                expected = json.loads(await waiting.get(gate + ":event-log"))
                actual = {item.id: item for item in await h.store.list_events(h.session_id)}
                assert len(expected) == 9
                assert all(item["id"] in actual for item in expected), "Emitted audit suffix disappeared before result freezing"
                expected_ids = [item["id"] for item in expected]
                assert [identity for identity in actual if identity in expected_ids] == expected_ids
                for raw in expected:
                    event = ExecutionEvent(session_id=h.session_id, **{k: v for k, v in raw.items() if k in {"id", "type", "timestamp", "payload"}})
                    saved = actual[event.id]
                    # Use the same exact intent comparison as finalization:
                    # PG stores UTC timestamps without timezone and enriches
                    # delivery context separately from the original payload.
                    _require_event_intent(saved.model_dump(), event)
                assert [item["payload"]["subagent_event"]["type"] for item in expected[1:]] == ["started", "completed"] * 4
            assert await h.store.renew_turn_execution(h.session_id, active.lease_token, 0)
            terminal = await h.tasks.claim(original.id, worker_id="terminal-gap-expiry", expected_binding=h.binding)
            assert terminal.status == "recovery_required"
            terminal_record = await h.tasks.get(original.id)
            assert await h.tasks.ensure_framework_message_recovery(original.id) is None
            assert await h.tasks.get(original.id) == terminal_record
            assert (await stage(h, original)).source_manifest == before.source_manifest
            assert (await stage(h, original)).source_manifest["graph_input_asset"] == prepared
            assert await h.calls.snapshot(h.binding) == budget and len(requests) == 1
            assert await evidence.read_response(h.binding, call.intent, response.response) == saved_response
            async with provider.open() as saver:
                retained = await saver.aget_tuple(selected)
                assert _native_hash(saver.serde, retained.checkpoint) == proof.checkpoint_sha256
            assert not [event for event in await h.store.list_events(h.session_id) if event.type == "turn.completed"]
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()
        await provider.close()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")


@pytest.mark.asyncio
async def test_official_subagent_original_callbacks_and_stages_survive_asset_gap_kill(admission, tmp_path, monkeypatch):
    from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
    from src.application.deep_agents.model_call_middleware import configure_model_policy
    from src.application.deep_agents.result_projection import project_deep_agent_result
    from src.application.runtime.turn_projection import (
        completed_deep_agent_state, project_completed_turn, project_runtime_snapshot, project_deep_agent_callback_facts,
    )
    from src.registry.skills import SkillRegistry
    from src.schemas.framework_graph_input import parse_graph_input_asset
    from src.schemas.framework_runtime_result import FrameworkRuntimeResult
    from src.schemas.session import ExecutionEvent
    from tests.test_framework_chat_models import _tool_reply

    h = admission
    h.settings.orchestration.resource_default_lease_seconds = 30
    waiting = Redis.from_url(h.settings.database.redis_url, decode_responses=True, socket_timeout=90)
    provider, process = DeepAgentCheckpointProvider(h.settings), None
    reply = _tool_reply("openai_chat_completions")
    reply["choices"][0]["message"]["tool_calls"][0]["function"] = {"name": "task", "arguments": json.dumps({
        "description": "Inspect the controlled project", "subagent_type": "code-review-researcher"})}
    try:
        with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
            config = _config("openai_chat_completions", url)
            h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
            h.command = make_command(h, h.binding)
            h.payload = h.payload.model_copy(update={"model_key": config.key, "mode_key": "code_review",
                "context": {**h.payload.context, "project_root": str(tmp_path)}})
            h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
            task = await submit(h)
            queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
                group="callback-material", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            gate = h.prefix + ":callback-completed"
            process = multiprocessing.get_context("spawn").Process(target=worker_waiting_after_application_commit,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate,
                    "after_subagent_event_completion", str(tmp_path)))
            process.start()
            reached = await waiting.blpop(gate, timeout=70)
            assert reached is not None, f"worker exit code: {process.exitcode}"
            proof = NativeTerminalProof.model_validate_json(reached[1])
            # The oracle is a test-only observation of the normal Runtime result,
            # never a recovery input or a replacement application result asset.
            oracle = FrameworkRuntimeResult.model_validate_json(await waiting.get(gate + ":oracle"))
            active, before = await h.tasks.get(task.id), await stage(h, task)
            assert before.phase == "runtime_submitted" and "runtime_result_asset" not in before.source_manifest
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            prepared = parse_graph_input_asset(before.source_manifest["graph_input_asset"])
            read = await h.store.read_runtime_event_completion(h.binding, proof.model_dump(mode="json"))
            facts = project_deep_agent_callback_facts(read.event_log, turn_id=h.binding.turn_id,
                returned_event_offset=read.completion.returned_event_offset)
            assert facts.pilot_stage == facts.adapter_stage == "DA-E3"
            assert [item["type"] for item in facts.subagent_events] == ["started", "completed"]
            assert facts.subagent_events[0]["run_id"] == facts.subagent_events[1]["run_id"]
            assert facts.subagent_events == oracle.state["cognitive_subagent_events"]
            budget, saved_events = await h.calls.snapshot(h.binding), await h.store.list_events(h.session_id)
            original_budget = read.event_log[-1]["payload"]["root_model_budget"]
            assert read_model_budget_material(h.binding, original_budget) == budget
            assert oracle.state["model_response_summary"]["root_model_budget"] == original_budget
            assert len(requests) == len(budget.calls) == 3 and all(call.phase == "completed" for call in budget.calls)
            model = Model(responses=[])
            adapter = DeepAgentRuntimeAdapter(model_resolver=None, skill_registry=SkillRegistry())
            kwargs, cleanup = adapter._configure_harness(model, read_only_filesystem_enabled=True,
                cognitive_subagents_enabled=True, read_only_max_file_size_mb=10, context=prepared.context)
            kwargs = configure_model_policy(kwargs, policy=None, owner=active.runtime_owner, model=model, model_key=config.key)
            selected = {"configurable": {"thread_id": h.binding.thread_id, "checkpoint_ns": h.binding.checkpoint_namespace,
                "checkpoint_id": proof.checkpoint_id}}
            try:
                async with provider.open() as saver:
                    graph = create_deep_agent(model=model, tools=[], checkpointer=saver, **kwargs)
                    native = await saver.aget_tuple(selected)
                    restored = await graph.aget_state({"configurable": {**selected["configurable"], CONFIG_KEY_CHECKPOINTER: saver}})
                    assert _native_hash(saver.serde, native.checkpoint) == proof.checkpoint_sha256
                    assert _native_hash(saver.serde, restored.values) == proof.state_sha256
                    def forbidden(*args, **kwargs):
                        pytest.fail("Original callback consumption cannot execute a model or graph")
                    monkeypatch.setattr(graph, "ainvoke", forbidden)
                    monkeypatch.setattr(graph, "astream_events", forbidden)
                    monkeypatch.setattr(model, "_generate", forbidden)
                    projected = project_deep_agent_result(restored.values, stage=facts.adapter_stage,
                        subagent_events=facts.subagent_events, native_terminal=proof.model_dump(mode="json"),
                        root_model_budget=original_budget)
                    preparation = prepared.runtime_preparation
                    state = completed_deep_agent_state(preparation.initial_state, projected, tool_records=[],
                        binding=h.binding, pilot_stage=facts.pilot_stage)
                    state["event_log"] = read.event_log
                    identity = preparation.identity
                    snapshot = project_runtime_snapshot(session_id=h.session_id, state=state, snapshot_id=identity.snapshot_id,
                        version=identity.snapshot_version, created_at=identity.snapshot_created_at)
                    result = project_completed_turn(state=state, snapshot=snapshot, tool_messages=[],
                        events=[ExecutionEvent(session_id=h.session_id, **entry)
                            for entry in read.event_log[read.completion.returned_event_offset:]])
                    assert FrameworkRuntimeResult.from_result(result).model_dump(mode="json") == oracle.model_dump(mode="json")
                    assert await saver.aget_tuple(selected) == native
            finally:
                if cleanup is not None:
                    cleanup()
            assert len(requests) == 3 and await h.calls.snapshot(h.binding) == budget
            assert await h.store.list_events(h.session_id) == saved_events
            assert await h.store.get_latest_snapshot(h.session_id) is None
            assert (await stage(h, task)).source_manifest == before.source_manifest
            assert not [item for item in saved_events if item.type == "turn.completed"]
            assert await h.store.renew_turn_execution(h.session_id, active.lease_token, 0)
            expired = await h.tasks.claim(task.id, worker_id="callback-expiry", expected_binding=h.binding)
            assert expired.status == "recovery_required" and await h.tasks.ensure_framework_message_recovery(task.id) is None
            expired = await h.tasks.get(task.id)
            from src.runtime.framework_runtime_results import FrameworkRuntimeResults

            events_before_read = await h.store.list_events(h.session_id)
            reader = DeepAgentRuntimeAdapter(model_resolver=forbidden, skill_registry=SkillRegistry(),
                checkpointer_factory=provider.open)
            reconstructed = await FrameworkRuntimeResults(h.tasks).read_completed_message(h.binding, task.id,
                native_reader=reader.open_native_reader)
            assert reconstructed.runtime_result.model_dump(mode="json") == oracle.model_dump(mode="json")
            assert reconstructed.source.attempt_id == active.attempt_id
            assert len(requests) == 3 and await h.calls.snapshot(h.binding) == budget
            assert await h.store.list_events(h.session_id) == events_before_read
            assert await h.store.get_latest_snapshot(h.session_id) is None
            assert (await stage(h, task)).source_manifest == before.source_manifest
            successor = await h.tasks.ensure_framework_message_recovery(task.id, native_reader=reader.open_native_reader)
            assert successor is not None
            from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
            from tests.test_framework_partial_projection_recovery import complete_in_new_process

            sealed = FrameworkRuntimeResultAsset.model_validate((await stage(h, task)).source_manifest["runtime_result_asset"])
            assert sealed.runtime_result.model_dump(mode="json") == oracle.model_dump(mode="json")
            await complete_in_new_process(h, successor)
            assert (await h.tasks.get(successor.id)).status == "completed"
            assert (await stage(h, task)).phase == "projected"
            assert await h.tasks.get(task.id) == expired
            assert len(requests) == 3 and await h.calls.snapshot(h.binding) == budget
            assert len([event for event in await h.store.list_events(h.session_id) if event.type == "turn.completed"]) == 1
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        await waiting.aclose()
        await provider.close()
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")


@pytest.mark.asyncio
async def test_normal_worker_result_keeps_prepared_identity_and_stage_version(admission, tmp_path):
    from src.schemas.framework_runtime_result import FrameworkRuntimeResultAsset
    from src.schemas.framework_graph_input import parse_graph_input_asset

    h = admission
    process = None
    try:
        with _provider("openai_chat_completions") as (url, requests):
            config = _config("openai_chat_completions", url)
            h.binding = h.binding.model_copy(update={"model_config_fingerprint": model_config_fingerprint(config)})
            h.command = make_command(h, h.binding)
            h.payload = h.payload.model_copy(update={"model_key": config.key, "mode_key": "code_review"})
            h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
            task = await submit(h)
            queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
                group="projection-kill", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            process = multiprocessing.get_context("spawn").Process(target=worker_waiting_after_application_commit,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix,
                    h.prefix + ":normal-completion", "complete", str(tmp_path)))
            process.start()
            await asyncio.to_thread(process.join, 45)
            assert not process.is_alive() and process.exitcode == 0
            assert (await h.tasks.get(task.id)).status == "completed"
            saved = await stage(h, task)
            assert saved.phase == "projected" and saved.schema_version == 20
            completions = [event for event in await h.store.list_events(h.session_id)
                if event.type == "runtime.event_log_completed"]
            assert len(completions) == 1, "A persisted prefix is not a completed Runtime event collection"
            graph_input = parse_graph_input_asset(saved.source_manifest["graph_input_asset"])
            assert graph_input.feature_flags["tool_projection_version"] == 3
            result = FrameworkRuntimeResultAsset.model_validate(saved.source_manifest["runtime_result_asset"])
            identity = graph_input.runtime_preparation.identity
            assert result.binding == identity.binding == h.binding
            assert result.assistant_message_id == identity.assistant_message_id
            snapshot = result.runtime_result.snapshot
            assert (snapshot.id, snapshot.version, snapshot.created_at) == (
                identity.snapshot_id, identity.snapshot_version, identity.snapshot_created_at)
            assert (await h.store.get_session(h.session_id)).messages[-1].id == identity.assistant_message_id
            assert (await h.store.get_latest_snapshot(h.session_id)).id == identity.snapshot_id
            initial = graph_input.runtime_preparation.initial_state
            assert initial["selected_model_key"] == config.key
            assert initial["selected_model_name"] == config.name
            assert initial["selected_model_provider"] == config.provider
            assert len(initial["event_log"]) == 1 and initial["final_response"] == ""
            assert len(result.runtime_result.events) > 1 and result.runtime_result.output_text == "fixture answer"
            read = await h.store.read_runtime_event_completion(h.binding,
                result.runtime_result.state["model_response_summary"]["native_terminal"])
            assert read.event_log == result.runtime_result.state["event_log"]
            assert [item.event_id for item in read.completion.events] == [item.id for item in result.runtime_result.events]
            assert read.completion.returned_event_offset == 0
            assert completions[0].id not in {item.id for item in result.runtime_result.events}
            from src.application.deep_agents.result_projection import project_deep_agent_result
            from src.application.runtime.turn_projection import (
                completed_deep_agent_state, project_completed_turn, project_runtime_snapshot,
            )
            from src.schemas.framework_runtime_result import FrameworkRuntimeResult
            from src.schemas.session import ExecutionEvent

            proof = NativeTerminalProof.model_validate(result.runtime_result.state["model_response_summary"]["native_terminal"])
            provider = DeepAgentCheckpointProvider(h.settings)
            try:
                async with provider.open() as saver:
                    selected = {"configurable": {"thread_id": h.binding.thread_id,
                        "checkpoint_ns": h.binding.checkpoint_namespace, "checkpoint_id": proof.checkpoint_id,
                        CONFIG_KEY_CHECKPOINTER: saver}}
                    graph = create_deep_agent(model=Model(responses=[]), tools=[], checkpointer=saver)
                    native = await saver.aget_tuple(selected)
                    restored = await graph.aget_state(selected)
                    assert _native_hash(saver.serde, native.checkpoint) == proof.checkpoint_sha256
                    assert _native_hash(saver.serde, restored.values) == proof.state_sha256
                from src.application.runtime.turn_projection import project_deep_agent_callback_facts
                facts = project_deep_agent_callback_facts(read.event_log, turn_id=h.binding.turn_id,
                    returned_event_offset=read.completion.returned_event_offset)
                original_budget = read.event_log[-1]["payload"]["root_model_budget"]
                assert read_model_budget_material(h.binding, original_budget) == await h.calls.snapshot(h.binding)
                projected = project_deep_agent_result(restored.values, stage=facts.adapter_stage, subagent_events=facts.subagent_events,
                    native_terminal=proof.model_dump(mode="json"), root_model_budget=original_budget)
                state = completed_deep_agent_state(initial, projected, tool_records=[], binding=h.binding, pilot_stage=facts.pilot_stage)
                state["event_log"] = read.event_log
                projected_snapshot = project_runtime_snapshot(session_id=h.session_id, state=state,
                    snapshot_id=identity.snapshot_id, version=identity.snapshot_version, created_at=identity.snapshot_created_at)
                projected_turn = project_completed_turn(state=state, snapshot=projected_snapshot, tool_messages=[],
                    events=[ExecutionEvent(session_id=h.session_id, **entry)
                        for entry in read.event_log[read.completion.returned_event_offset:]])
                assert FrameworkRuntimeResult.from_result(projected_turn).model_dump(mode="json") == result.runtime_result.model_dump(mode="json")
            finally:
                await provider.close()
            assert len(requests) == 1
            assert len([event for event in await h.store.list_events(h.session_id) if event.type == "turn.completed"]) == 1
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10)
        with postgres_connect(h.settings) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {h.settings.deep_agents.checkpoint_schema} CASCADE")
