"""Cross-Turn coverage through real framework, PostgreSQL and local official SDK."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
from src.application.models.model_runtime_service import ModelRuntimeService
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.schemas.framework_model_call import FrameworkModelBudget
from src.schemas.session import SessionStatus
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_runtime import build_runtime
from tests.test_framework_context_transcript import context_case, covered_asset
from tests.test_framework_chat_models import _config, _provider
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


@pytest.mark.asyncio
@pytest.mark.parametrize("native_prefix", [False, True])
@pytest.mark.parametrize("approval", [False, True])
async def test_admitted_context_reaches_sdk_and_next_turn_reuses_the_published_native_asset(
    projection_harness, native_prefix, approval, monkeypatch,
):
    from tests import test_framework_chat_models as provider_module

    h = projection_harness
    await h.tasks.finish(h.owner, status="completed", result_status="completed")
    session = await h.store.get_session(h.session_id)
    session.status = SessionStatus.idle
    await h.store.save_session(session)
    calls = PostgresFrameworkModelCalls(h.settings)
    await calls.initialize()
    h.tables.extend([calls.budgets_table, calls.calls_table])
    original_reply = provider_module._reply
    reply_ids = []

    def unique_reply(transport):
        reply = original_reply(transport)
        reply["id"] = f"chatcmpl_context_{len(reply_ids)}"
        if approval and not reply_ids:
            reply["choices"][0].update(finish_reason="tool_calls", message={"role": "assistant", "content": None,
                "tool_calls": [{"id": "current-inspect", "type": "function", "function": {
                    "name": "inspect_value", "arguments": '{"value":"A"}'}}]})
        reply_ids.append(reply["id"])
        return reply

    monkeypatch.setattr(provider_module, "_reply", unique_reply)
    with _provider("openai_chat_completions") as (url, requests):
        config = _config("openai_chat_completions", url, key="fake")
        fingerprint = model_config_fingerprint(config)
        policy = FrameworkModelCallPolicy(calls=calls, model_config_resolver=lambda key: config)
        h.effects = []
        build_runtime(h, model_call_policy=policy)
        models = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), h.settings)
        h.runtime._model_runtime_service = models
        h.runtime._deep_agent_bound_runtime_adapter._model_resolver = models.resolve_langchain_chat_model
        h.sessions._runtime_service = h.runtime
        previous_asset = None
        for index in range(2):
            h.binding = make_binding(h.session_id, f"context-turn-{uuid4().hex}", runtime_version="v2.1.0",
                model_config_fingerprint=fingerprint)
            budget = FrameworkModelBudget(max_calls=4, max_tokens=500_000,
                deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                model_config_fingerprints={"fake": fingerprint})
            payload = make_payload().model_copy(update={"content": "repeat", "model_key": "fake",
                "context": {"requested_tool_keys": ["inspect_value"],
                    "framework_transcript_preparation": {"source_input_message_id": "future-user"}}})
            admitted = make_message(h, h.binding).model_copy(update={"content": "repeat"})
            submitted = await h.tasks.submit_v2(make_command(h, h.binding), payload, admitted,
                idempotency_key=uuid4().hex, model_budget=budget)
            session = await h.store.get_session(h.session_id)
            if index == 0:
                case, _, _ = context_case()
                current = case.messages[3]
                current.id = admitted.id
                current.metadata["turn_id"] = h.binding.turn_id
                session.messages = case.messages
                if native_prefix:
                    session.metadata["framework_transcript_asset"] = covered_asset(session, h.binding)
            else:
                # This intentionally malformed future row in the first sample
                # remains unprocessed; restore its actual queued classification.
                for item in session.messages:
                    if item.id == "future-user":
                        item.metadata.update(delivery_status="queued", context_eligible=False)
            await h.store.save_session(session)
            old_asset = deepcopy(session.metadata.get("framework_transcript_asset"))
            owner = await h.tasks.claim(submitted.id, worker_id="context-sdk", expected_binding=h.binding)
            await h.tasks.begin(owner, resource_id="context-test", fencing_token=1)
            monitor = asyncio.create_task(asyncio.Event().wait())
            try:
                response = await h.sessions.execute_claimed_message(owner, ownership_monitor=monitor)
                initial_adapter_request = h.adapter_requests[-1]
                if approval and index == 0:
                    from tests.test_framework_approval_continuation import claim_decision

                    assert response.session.status.value == "waiting_approval" and h.effects == []
                    waiting = await h.store.get_session(h.session_id)
                    assert waiting.metadata.get("framework_transcript_asset") == old_asset
                    # Approval must use its frozen graph, even if current Session
                    # metadata is no longer a usable source for a new Turn.
                    waiting.metadata["framework_transcript_asset"] = {"schema_version": 999}
                    await h.store.save_session(waiting)
                    approvals = await h.store.list_approvals(h.session_id)
                    h.proposals = {"A": SimpleNamespace(approval=next(item for item in approvals
                        if item.metadata.get("call_id") == "current-inspect"))}
                    await h.tasks.finish(owner, status="completed", result_status="waiting_approval")
                    owner = await claim_decision(h, key="A")
                    response = await h.sessions.execute_claimed_approval(owner, ownership_monitor=monitor)
                    assert [value for value, job in h.effects] == ["A"]
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            assert response.session.status.value == "completed" and response.output.content == "fixture answer"
            stage = await h.tasks.get_framework_message_stage(submitted.id, session_id=h.session_id)
            assert stage.execution_request["user_message_id"] == admitted.id
            asset = (await h.store.get_session(h.session_id)).metadata["framework_transcript_asset"]
            assert asset["schema_version"] == 2 and asset["source_input_message_id"] == admitted.id
            assert asset["source_output_message_ids"] == [response.output.id]
            assert asset["covered_message_ids"][-2:] == [admitted.id, response.output.id]
            assert "future-user" not in asset["covered_message_ids"] and "hidden-error" not in asset["covered_message_ids"]
            preparation = initial_adapter_request.context["framework_transcript_preparation"]
            assert preparation["source_input_message_id"] == admitted.id
            graph_input = stage.source_manifest["graph_input_asset"]
            assert graph_input["context"]["framework_transcript_preparation"] == preparation
            assert graph_input["runtime_preparation"]["initial_state"]["context_bundle"][
                "framework_transcript_preparation"] == preparation
            assert initial_adapter_request.framework_messages[-1]["data"]["id"] == admitted.id
            body = requests[-1]["body"]["messages"]
            assert [item["content"] for item in body if item["role"] == "user"] == [
                "Earlier review", *(["repeat"] * (index + 2))]
            assert sum(item["content"] == "Legacy answer" for item in body) == 1
            wire = json.dumps(body, ensure_ascii=False)
            assert "not yet admitted to this Turn" not in wire and "not context" not in wire
            if native_prefix:
                assert asset["framework_messages"][1]["data"]["additional_kwargs"]["provider_signature"] == "fixture-native-signature"
                assert next(item for item in body if item.get("tool_calls"))["tool_calls"][0]["id"] == "previous-call"
                assert next(item for item in body if item["role"] == "tool")["tool_call_id"] == "previous-call"
            if previous_asset is not None:
                assert asset["framework_messages"][:len(previous_asset["framework_messages"])] == previous_asset["framework_messages"]
            previous_asset = deepcopy(asset)
            assert (await calls.snapshot(h.binding)).reserved_calls == (2 if approval and index == 0 else 1)
            await h.tasks.finish(owner, status="completed", result_status="completed")
        assert len(requests) == len(reply_ids) == (3 if approval else 2)
        assert len(h.effects) == (1 if approval else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["old_reader", "legacy_binding", "bad_digest"])
async def test_real_durable_task_is_not_claimed_when_transcript_capability_is_incompatible(harness, case):
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred

    h = harness
    selected = make_binding(h.session_id, f"context-turn-{uuid4().hex}", runtime_version="v2.1.0")
    asset = covered_asset(await h.store.get_session(h.session_id), selected)
    if case == "legacy_binding":
        selected = selected.model_copy(update={"runtime_version": "v2.0.0"})
    if case == "bad_digest":
        asset["content_sha256"] = "0" * 64
    session = await h.store.get_session(h.session_id)
    session.metadata["framework_transcript_asset"] = asset
    await h.store.save_session(session)
    command = make_command(h, selected)
    submitted = await h.tasks.submit_v2(command, make_payload(), make_message(h, selected), idempotency_key=uuid4().hex)
    h.dispatcher.set_framework_capabilities(FrameworkWorkerCapabilities("a" * 64, True,
        context_transcript_enabled=case != "old_reader"))
    before = await h.tasks.get(submitted.id)
    with pytest.raises(TaskDeferred, match="unavailable"):
        await h.dispatcher.handle_v2(QueuedTask("context-incompatible", command.model_dump(mode="json")), worker_id="reader")
    after = await h.tasks.get(submitted.id)
    assert after == before and after.status == "queued" and after.attempt_no == 0
    assert (await h.store.get_session(h.session_id)).metadata["framework_transcript_asset"] == asset
