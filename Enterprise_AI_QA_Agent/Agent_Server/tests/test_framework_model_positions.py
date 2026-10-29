from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from src.runtime.execution_context import framework_owner_scope
from tests.test_agent_task_dispatch import harness
from tests.test_framework_chat_models import _config, _provider
from tests.test_framework_model_dto import dto_request
from tests.test_framework_model_policy import governed


async def invoke_dto(h, request=None):
    with framework_owner_scope(h.owner.runtime_owner):
        return await h.models.invoke(h.request.model_key, request or dto_request(h))


@pytest.mark.asyncio
async def test_dto_input_drift_cannot_turn_the_same_position_into_another_http_request(harness):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            await invoke_dto(h)
            original = await h.calls.snapshot(h.binding)
            changed = dto_request(h).model_copy(update={"messages": [{"role": "user", "content": "changed input"}]})
            with pytest.raises((RuntimeError, ValueError), match="input"):
                await invoke_dto(h, changed)
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == original


@pytest.mark.asyncio
async def test_dto_purpose_drift_cannot_reuse_a_settled_summary_position(harness):
    from src.runtime.postgres_framework_model_calls import FrameworkModelRecoveryRequired

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            with framework_owner_scope(h.owner.runtime_owner):
                await h.models.invoke(h.request.model_key, dto_request(h), purpose="summary")
                original = await h.calls.snapshot(h.binding)
                with pytest.raises(FrameworkModelRecoveryRequired, match="position_input_changed"):
                    await h.models.invoke(h.request.model_key, dto_request(h), purpose="agent")
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == original


@pytest.mark.asyncio
async def test_graph_position_is_independent_of_request_hash(harness):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            model = await h.models.resolve_langchain_chat_model(h.request.model_key)
            execution = SimpleNamespace(thread_id=h.binding.thread_id, checkpoint_ns="model:fixed-task", task_id="fixed-task")

            async def call(text):
                messages = [HumanMessage(content=text)]
                return await h.policy.invoke(owner=h.owner.runtime_owner, model=model, model_key=h.request.model_key,
                    execution_info=execution, branch_name=None, purpose="agent", messages=messages, tools=[], options={},
                    send=lambda: model.ainvoke(messages))

            await call("original input")
            before = await h.calls.snapshot(h.binding)
            with pytest.raises((RuntimeError, ValueError), match="input"):
                await call("changed input")
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["before_start", "after_start", "after_complete"])
async def test_reentry_finds_original_attempt_and_does_not_issue_new_send_permission(harness, monkeypatch, window):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            target = "complete" if window == "after_complete" else "start"
            operation = getattr(h.calls, target)

            async def crash(*args, **kwargs):
                if window != "before_start":
                    await operation(*args, **kwargs)
                raise ConnectionError("injected model admission acknowledgement loss")

            with monkeypatch.context() as patch:
                patch.setattr(h.calls, target, crash)
                with pytest.raises(ConnectionError):
                    await invoke_dto(h)
            before = await h.calls.snapshot(h.binding)
            if window == "before_start":
                assert before.calls[0].phase == "ready" and requests == []
                await invoke_dto(h)
                after = await h.calls.snapshot(h.binding)
                assert after.calls[0].intent == before.calls[0].intent
                assert after.reserved_calls == 1 and after.actual_tokens == 10 and len(requests) == 1
            else:
                with pytest.raises((RuntimeError, ValueError)) as caught:
                    await invoke_dto(h)
                assert caught.value.attempt_id == before.calls[0].intent.attempt_id
                assert len(requests) == (1 if window == "after_complete" else 0)
                assert await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_concurrent_dto_calls_share_one_durable_attempt_and_one_http_request(harness):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            outcomes = await asyncio.gather(*(invoke_dto(h) for _ in range(4)), return_exceptions=True)
            assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
            saved = await h.calls.snapshot(h.binding)
            assert saved.reserved_calls == 1 and saved.actual_tokens == 10 and len(requests) == 1
            rejected = [item for item in outcomes if isinstance(item, BaseException)]
            assert all(item.attempt_id == saved.calls[0].intent.attempt_id for item in rejected)


@pytest.mark.asyncio
async def test_owner_bound_dto_without_a_durable_call_position_is_rejected_before_http(harness):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            request = dto_request(h)
            request.trace_context.pop("logical_call_id")
            with pytest.raises(ValueError, match="position"):
                await invoke_dto(h, request)
            assert requests == [] and (await h.calls.snapshot(h.binding)).reserved_calls == 0


@pytest.mark.asyncio
async def test_legacy_ledger_without_position_version_cannot_authorize_a_new_position(harness):
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.runtime.postgres_framework_model_calls import FrameworkModelRecoveryRequired

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            await invoke_dto(h)
            with postgres_connect(h.settings) as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {h.calls.calls_table} SET record=record #- '{{intent,position_version}}' "
                    "WHERE binding_id=%s", (h.binding.binding_id,))
            before = await h.calls.snapshot(h.binding)
            with pytest.raises(FrameworkModelRecoveryRequired) as caught:
                await invoke_dto(h, dto_request(h, logical="new-position"))
            assert caught.value.reason == "legacy_position_unavailable"
            assert caught.value.attempt_id == before.calls[0].intent.attempt_id
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
async def test_request_asset_upload_follows_durable_intent_and_reentry_does_not_upload_again(harness, monkeypatch):
    from src.application.artifacts.artifact_storage_service import ArtifactStorageService
    from src.application.models.framework_model_evidence import FrameworkModelEvidence
    from tests.test_artifact_storage_service import _RustFSClient

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            objects = ArtifactStorageService(h.settings)
            client = _RustFSClient()
            monkeypatch.setattr(objects, "_rustfs_client", lambda: client)
            h.policy._evidence = FrameworkModelEvidence(objects=objects)
            h.models.set_framework_model_call_policy(h.policy)
            store_request = h.policy._evidence.store_request
            admitted = []

            async def require_admission(owner, intent, envelope):
                saved, = (await h.calls.snapshot(h.binding)).calls
                assert saved.intent == intent and saved.phase == "ready"
                admitted.append(saved.intent.attempt_id)
                return await store_request(owner, intent, envelope)

            monkeypatch.setattr(h.policy._evidence, "store_request", require_admission)
            await invoke_dto(h)
            before = await h.calls.snapshot(h.binding)
            object_count = len(client.objects)
            with pytest.raises((RuntimeError, ValueError)):
                await invoke_dto(h)
            assert len(client.objects) == object_count and len(admitted) == 1
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before
