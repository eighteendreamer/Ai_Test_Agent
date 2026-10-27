"""Hard kill after the real native graph returns, before application projection."""
from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from deepagents import create_deep_agent
from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER

from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
from src.application.deep_agents.native_terminal import NativeTerminalProof, _native_hash
from src.application.models.framework_model_call_policy import model_config_fingerprint
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
async def test_native_terminal_and_settled_response_survive_kill_but_do_not_invent_application_result(admission, tmp_path):
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
            h.budget = h.budget.model_copy(update={"model_config_fingerprints": {config.key: h.binding.model_config_fingerprint}})
            original = await submit(h)
            queue = RedisTaskQueue(h.settings.database.redis_url, stream=h.settings.orchestration.agent_task_v2_stream,
                group="projection-kill", client=h.redis)
            await queue.connect()
            assert await h.outbox.relay_once(queue_for_stream=lambda stream: queue if stream == queue.stream else None) == 1
            gate = h.prefix + ":native-graph-returned"
            process = multiprocessing.get_context("spawn").Process(target=worker_waiting_after_application_commit,
                args=(h.settings.model_dump(mode="json"), config.model_dump(mode="json"), h.prefix, gate,
                    "after_graph_return", str(tmp_path)))
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
                graph = create_deep_agent(model=Model(responses=[]), tools=[], checkpointer=saver)
                snapshot = await graph.aget_state({"configurable": {
                    **selected["configurable"], CONFIG_KEY_CHECKPOINTER: saver}})
                assert not snapshot.next and not snapshot.tasks and not snapshot.interrupts
                assert _native_hash(saver.serde, snapshot.values) == proof.state_sha256
                assert snapshot.values["messages"][-1].content == "fixture answer"
            assert before.phase == "runtime_submitted" and "graph_input_asset" in before.source_manifest
            assert "runtime_result_asset" not in before.source_manifest
            # Current input records adapter inputs, not the application runtime
            # event log, result identities, or callback-only subagent metadata.
            assert "event_log" not in before.source_manifest["graph_input_asset"]
            assert await h.store.get_latest_snapshot(h.session_id) is None
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            assert not process.is_alive() and process.exitcode != 0
            assert await h.store.renew_turn_execution(h.session_id, active.lease_token, 0)
            terminal = await h.tasks.claim(original.id, worker_id="terminal-gap-expiry", expected_binding=h.binding)
            assert terminal.status == "recovery_required"
            terminal_record = await h.tasks.get(original.id)
            assert await h.tasks.ensure_framework_message_recovery(original.id) is None
            assert await h.tasks.get(original.id) == terminal_record
            assert (await stage(h, original)).source_manifest == before.source_manifest
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
