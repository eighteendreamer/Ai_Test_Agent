"""The admitted root deadline is not renewed by another graph invocation."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from langchain_core.tools import tool

from src.application.deep_agents.checkpoint_provider import DeepAgentCheckpointProvider
from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter
from src.application.runtime.runtime_service import RuntimeService
from src.application.models.framework_model_budget_projection import read_model_budget_material
from src.application.models.framework_model_call_policy import FrameworkExecutionDeadlineExceeded
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.runtime.control import RuntimeControlRegistry
from src.runtime.execution_context import framework_owner_scope
from src.runtime.store import ContinuationLeaseLostError
from tests.test_agent_task_dispatch import harness
from tests.test_deep_agent_runtime_adapter import _ModelRuntimeStub, _runtime_request, _runtime_session
from tests.test_framework_chat_models import _config, _provider, _tool_reply
from tests.test_framework_model_policy import execute, governed


def runtime_with(adapter, *, seconds=30):
    control = RuntimeControlRegistry()
    return RuntimeService(graph=None, model_runtime_service=_ModelRuntimeStub(),
        tool_runtime_service=object(), tool_registry=ToolRegistry(), runtime_control=control,
        deep_agent_runtime_adapter=adapter, deep_agent_enabled=True,
        deep_agent_pilot_mode_keys=["code_review"], deep_agent_turn_timeout_seconds=seconds), control


@pytest.mark.asyncio
async def test_inner_timeout_is_not_misreported_as_the_invocation_deadline():
    error = TimeoutError("controlled inner transport timeout")

    class Adapter:
        async def execute(self, request):
            raise error

    runtime, control = runtime_with(Adapter())
    with pytest.raises(TimeoutError) as raised:
        await runtime.execute_turn(_runtime_session("inner-timeout"), _runtime_request("inner-timeout", "turn"))
    assert raised.value is error
    assert not control.has_interruptible_task("inner-timeout")


def set_deadline(h, seconds):
    # Change only this fixture's frozen policy, as in the existing ledger
    # reserve/send regression. Never mutate a production Turn's policy.
    with postgres_connect(h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {h.calls.budgets_table} SET policy=jsonb_set(policy,'{{deadline_at}}',"
            "to_jsonb(clock_timestamp()+(%s * INTERVAL '1 second'))) WHERE binding_id=%s",
            (seconds, h.binding.binding_id))


async def wait_for_database_deadline(h):
    # asyncio callbacks may run within clock resolution. A second invocation's
    # wall-deadline assertion must sample the database, not infer it from the
    # first invocation's cooperative local timer. Never change the policy.
    _, remaining = await h.calls.execution_deadline(h.owner.runtime_owner)
    await asyncio.sleep(max(0, remaining) + .05)
    assert (await h.calls.execution_deadline(h.owner.runtime_owner))[1] < 0


@pytest.mark.asyncio
async def test_expired_approval_cannot_execute_its_approved_tool_after_adapter_rebuild(harness):
    effects = []

    @tool
    async def inspect_source(path: str) -> str:
        """Return a controlled approval fixture value."""
        effects.append(path)
        return path

    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            request = replace(h.request, business_tools=[inspect_source], interrupt_on={"inspect_source": {
                "allowed_decisions": ["approve", "reject"]}})
            waiting = await execute(h, request)
            pending, = waiting.native_interrupts
            assert len(requests) == 1 and effects == []
            set_deadline(h, -1)
            original = await h.calls.snapshot(h.binding)
            await h.provider.close()
            h.provider = DeepAgentCheckpointProvider(h.settings)
            h.adapter = DeepAgentRuntimeAdapter(model_resolver=h.models.resolve_langchain_chat_model,
                checkpointer_factory=h.provider.open, model_call_policy=h.policy)
            error = None
            try:
                await execute(h, replace(request, resume={pending.interrupt_id: {"type": "approve"}}))
            except Exception as exc:
                error = exc
            assert effects == [], "Expired approval executed its tool before checking the original root deadline"
            assert isinstance(error, TimeoutError), repr(error)
            assert "root" in str(error) and "deadline" in str(error)
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["model", "tool"])
async def test_root_deadline_cancels_inflight_graph_without_reissuing_unknown_work(harness, monkeypatch, boundary):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def block():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    @tool
    async def inspect_source(path: str) -> str:
        """Wait at a controlled tool execution boundary, not a real Runner."""
        await block()
        return path

    reply = _tool_reply("openai_chat_completions") if boundary == "tool" else None
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            request = replace(h.request, business_tools=[inspect_source]) if boundary == "tool" else h.request
            if boundary == "model":
                invoke = h.policy.invoke

                async def pending_response(**kwargs):
                    send = kwargs["send"]

                    async def delayed_visibility():
                        response = await send()
                        await block()
                        return response

                    return await invoke(**{**kwargs, "send": delayed_visibility})

                monkeypatch.setattr(h.policy, "invoke", pending_response)
            set_deadline(h, 3)
            task = asyncio.create_task(execute(h, request))
            try:
                await asyncio.wait_for(started.wait(), timeout=10)
                done, _ = await asyncio.wait({task}, timeout=5)
                assert task in done, "The root deadline did not bound the active graph invocation"
                with pytest.raises(FrameworkExecutionDeadlineExceeded, match="root.*deadline") as raised:
                    await task
                assert cancelled.is_set() and len(requests) == 1
                snapshot = await h.calls.snapshot(h.binding)
                call, = snapshot.calls
                assert call.phase == ("unknown" if boundary == "model" else "completed")
                assert snapshot.charged_tokens == (call.intent.reserved_tokens if boundary == "model" else 10)
                assert read_model_budget_material(h.binding, raised.value.root_model_budget) == snapshot
                await wait_for_database_deadline(h)
                with pytest.raises(TimeoutError, match="root.*deadline"):
                    await execute(h, replace(request, continue_from_checkpoint=True))
                assert len(requests) == 1 and await h.calls.snapshot(h.binding) == snapshot
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["expired", "inflight", "invocation"])
async def test_runtime_stop_distinguishes_deadlines_and_preserves_original_root_budget(harness, monkeypatch, boundary):
    cancelled = asyncio.Event()
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            if boundary != "expired":
                invoke = h.policy.invoke

                async def pending_response(**kwargs):
                    send = kwargs["send"]

                    async def delayed_visibility():
                        response = await send()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cancelled.set()
                        return response

                    return await invoke(**{**kwargs, "send": delayed_visibility})

                monkeypatch.setattr(h.policy, "invoke", pending_response)
            set_deadline(h, {"expired": -1, "inflight": 3, "invocation": 30}[boundary])
            policy = await h.calls.frozen_policy(h.binding)
            runtime, control = runtime_with(h.adapter, seconds=2 if boundary == "invocation" else 30)
            request = _runtime_request(h.session_id, h.binding.turn_id).model_copy(update={
                "model_key": h.request.model_key, "runtime_binding": h.binding})
            with framework_owner_scope(h.owner.runtime_owner):
                result = await runtime.execute_turn(_runtime_session(h.session_id), request)
            summary = result.state["model_response_summary"]
            assert result.state["termination_reason"] == result.snapshot.stage == "interrupted"
            assert result.pending_turn == {} and result.approvals == [] and result.output_text == ""
            assert summary["cause"] == "timeout"
            assert summary["timeout_source"] == ("invocation" if boundary == "invocation" else "root_deadline")
            event, = [item for item in result.events if item.type == "runtime.turn_timed_out"]
            assert event.payload["timeout_source"] == summary["timeout_source"] and not event.payload["resumable"]
            assert not control.has_interruptible_task(h.session_id)
            snapshot = await h.calls.snapshot(h.binding)
            assert snapshot.policy == policy
            assert len(requests) == snapshot.unknown_calls == (0 if boundary == "expired" else 1)
            if boundary != "invocation":
                assert read_model_budget_material(h.binding, summary["root_model_budget"]) == snapshot
            if boundary != "expired":
                assert cancelled.is_set()


@pytest.mark.asyncio
async def test_execution_deadline_rejects_lost_owner_before_resolving_a_model(harness, monkeypatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            resolved = []

            async def resolve(key):
                resolved.append(key)
                return await h.models.resolve_langchain_chat_model(key)

            monkeypatch.setattr(h.adapter, "_model_resolver", resolve)
            assert await h.store.renew_turn_execution(h.session_id, h.owner.lease_token, 0)
            with pytest.raises(ContinuationLeaseLostError):
                await execute(h)
            assert requests == resolved == [] and not (await h.calls.snapshot(h.binding)).calls


@pytest.mark.asyncio
async def test_completed_graph_keeps_its_result_when_readonly_terminal_audit_crosses_deadline(harness, monkeypatch):
    from src.application.deep_agents import native_terminal

    collect = native_terminal.collect_native_terminal

    async def delayed_audit(*args, **kwargs):
        terminal = await collect(*args, **kwargs)
        # The native graph has returned and its terminal is already committed.
        # Read-only audit/projection must not erase that result on wall expiry.
        await asyncio.sleep(3.2)
        return terminal

    monkeypatch.setattr(native_terminal, "collect_native_terminal", delayed_audit)
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            set_deadline(h, 3)
            policy = await h.calls.frozen_policy(h.binding)
            result = await execute(h)
            assert result.output_text == "fixture answer" and len(requests) == 1
            assert result.metadata["native_terminal"]["scope"] == "native_graph_terminal_only"
            snapshot = await h.calls.snapshot(h.binding)
            assert snapshot.policy == policy and snapshot.actual_tokens == 10
            assert read_model_budget_material(h.binding, result.metadata["root_model_budget"]) == snapshot


@pytest.mark.asyncio
async def test_expired_root_is_persisted_as_nonresumable_interruption_not_completed_projection(harness, monkeypatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            runtime, _ = runtime_with(h.adapter)
            h.sessions._runtime_service = runtime
            orchestrate = h.sessions._input_orchestrator_service.orchestrate_async

            async def select_fixture_model(*args, **kwargs):
                request = await orchestrate(*args, **kwargs)
                request.model_key = h.request.model_key
                return request

            monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", select_fixture_model)
            set_deadline(h, -1)
            monitor = asyncio.create_task(asyncio.Event().wait())
            try:
                response = await h.sessions.execute_claimed_message(h.owner, ownership_monitor=monitor)
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            assert response.session.status.value == "interrupted" and not response.session.is_resumable
            saved = await h.store.get_session(h.session_id)
            assert not saved.metadata.get("pending_turn") and requests == []
            snapshot = await h.store.get_latest_snapshot(h.session_id)
            material = snapshot.graph_state["model_response_summary"]["root_model_budget"]
            assert read_model_budget_material(h.binding, material) == await h.calls.snapshot(h.binding)
            stage = await h.tasks.get_framework_message_stage(h.owner.id, session_id=h.session_id)
            assert stage.phase == "runtime_submitted" and "runtime_result_asset" not in stage.source_manifest
            events = await h.store.list_events(h.session_id)
            stopped, = [item for item in events if item.type == "runtime.turn_timed_out"]
            assert stopped.payload["timeout_source"] == "root_deadline" and not stopped.payload["resumable"]
            await h.tasks.finish(h.owner, status="interrupted", result_status="interrupted")
            delivered = await h.tasks.claim(h.owner.id, worker_id="redelivery", expected_binding=h.binding)
            assert delivered.status == "interrupted" and delivered.attempt_no == h.owner.attempt_no
            assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["openai_chat_completions", "openai_responses", "anthropic_messages",
    "google_gemini_generate_content", "langchain_chat"])
async def test_dto_stream_wait_obeys_root_deadline_and_keeps_unknown_reservation(harness, path):
    from tests.test_framework_model_dto import dto_request
    from tests.test_model_sdk_fault_http import fault_gateway

    transport = "openai_chat_completions" if path == "langchain_chat" else path
    with fault_gateway(transport, "cancel") as (url, requests, replies, errors):
        async with governed(harness, _config(transport, url)) as h:
            h.settings.model.langchain_model_adapter_enabled = path == "langchain_chat"
            h.settings.model.llm_request_timeout_seconds = 20
            h.models.set_framework_model_call_policy(h.policy)
            received = asyncio.Event()

            async def chunk(_delta):
                received.set()

            set_deadline(h, 3)
            policy = await h.calls.frozen_policy(h.binding)
            with framework_owner_scope(h.owner.runtime_owner):
                async with h.models.stream_handler(chunk):
                    task = asyncio.create_task(h.models.invoke(h.request.model_key, dto_request(h)))
                    try:
                        await asyncio.wait_for(received.wait(), 10)
                        done, _ = await asyncio.wait({task}, timeout=5)
                        assert task in done, "DTO transport continued waiting past the original root deadline"
                        with pytest.raises(FrameworkExecutionDeadlineExceeded) as raised:
                            await task
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                snapshot = await h.calls.snapshot(h.binding)
                call, = snapshot.calls
                assert snapshot.policy == policy and len(requests) == len(replies) == 1 and not errors
                assert call.phase == "unknown" and call.outcome_code == "request_cancelled"
                assert snapshot.charged_tokens == call.intent.reserved_tokens
                assert read_model_budget_material(h.binding, raised.value.root_model_budget) == snapshot
                # Event-loop timers may wake within clock resolution. Confirm
                # database wall expiry before asserting the next invocation's
                # rejection; replay exactly the same streaming request shape.
                await wait_for_database_deadline(h)
                async with h.models.stream_handler(chunk):
                    with pytest.raises(FrameworkExecutionDeadlineExceeded):
                        await h.models.invoke(h.request.model_key, dto_request(h))
                assert len(requests) == 1 and await h.calls.snapshot(h.binding) == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_dto_deadline_includes_auth_wait_without_sending_or_reserving(harness, monkeypatch, expired):
    from tests.test_framework_model_dto import dto_request

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            entered, cancelled = asyncio.Event(), asyncio.Event()

            async def auth(_config):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            monkeypatch.setattr(h.models, "_resolve_auth_token", auth)
            set_deadline(h, -1 if expired else 2)
            before = await h.calls.snapshot(h.binding)
            with framework_owner_scope(h.owner.runtime_owner):
                task = asyncio.create_task(h.models.invoke(h.request.model_key, dto_request(h)))
                try:
                    done, _ = await asyncio.wait({task}, timeout=4)
                    assert task in done, "DTO authentication wait escaped the root deadline"
                    with pytest.raises(FrameworkExecutionDeadlineExceeded) as raised:
                        await task
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert entered.is_set() is not expired and cancelled.is_set() is not expired
            assert requests == [] and await h.calls.snapshot(h.binding) == before
            assert read_model_budget_material(h.binding, raised.value.root_model_budget) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen", [False, True])
async def test_semantic_root_expiry_is_not_downgraded_to_deterministic_fallback(harness, frozen):
    from src.application.intent.semantic_intent_service import SemanticIntentService
    from src.schemas.intent import IntentDecision

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            semantic = SemanticIntentService(h.models, deterministic_confidence_threshold=1.1)
            prepared = semantic.prepare(message="Inspect the supplied fixture", baseline=IntentDecision(),
                model_key=h.request.model_key, freeze_runtime=frozen,
                trace_context={"session_id": h.session_id, "turn_id": h.binding.turn_id,
                    "logical_call_id": "semantic-deadline-fixture"})
            assert prepared.request is not None
            set_deadline(h, -1)
            with framework_owner_scope(h.owner.runtime_owner):
                with pytest.raises(FrameworkExecutionDeadlineExceeded):
                    await semantic.enrich_prepared(prepared)
            assert requests == [] and not (await h.calls.snapshot(h.binding)).calls


@pytest.mark.asyncio
async def test_pregraph_semantic_deadline_uses_existing_interrupted_projection_and_task_settlement(harness):
    from src.application.intent.semantic_intent_service import SemanticIntentService
    from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
    from src.registry.modes import ModeRegistry
    from tests.test_model_sdk_fault_http import fault_gateway

    with fault_gateway("openai_chat_completions", "cancel") as (url, requests, replies, errors):
        async with governed(harness, _config("openai_chat_completions", url), begin_execution=False) as h:
            h.settings.model.llm_request_timeout_seconds = 20
            h.models.set_framework_model_call_policy(h.policy)
            session = await h.store.get_session(h.session_id)
            session.preferred_model = h.request.model_key
            await h.store.save_session(session)
            h.sessions._input_orchestrator_service = InputOrchestratorService(ModeRegistry(),
                SemanticIntentService(h.models, deterministic_confidence_threshold=1.1))
            runtime, _ = runtime_with(h.adapter)
            h.sessions._runtime_service = runtime
            set_deadline(h, 3)
            policy = await h.calls.frozen_policy(h.binding)
            task = asyncio.create_task(h.dispatcher._execute(h.owner))
            try:
                done, _ = await asyncio.wait({task}, timeout=6)
                assert task in done, "Claimed semantic classification outlived the root deadline"
                await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            saved_task = await h.tasks.get(h.owner.id)
            saved = await h.store.get_session(h.session_id)
            assert saved_task.status == saved.status.value == "interrupted"
            snapshot = await h.store.get_latest_snapshot(h.session_id)
            assert snapshot.stage == "interrupted" and not saved.metadata.get("pending_turn")
            summary = snapshot.graph_state["model_response_summary"]
            budget = await h.calls.snapshot(h.binding)
            assert summary["timeout_source"] == "root_deadline" and summary["cause"] == "timeout"
            assert read_model_budget_material(h.binding, summary["root_model_budget"]) == budget
            assert budget.policy == policy and len(requests) == len(replies) == budget.unknown_calls == 1
            stage = await h.tasks.get_framework_message_stage(h.owner.id, session_id=h.session_id)
            assert stage.phase == "orchestrating" and "runtime_result_asset" not in stage.source_manifest
            events = await h.store.list_events(h.session_id)
            stopped, = [item for item in events if item.type == "runtime.turn_timed_out"]
            assert not stopped.payload["resumable"] and stopped.payload["timeout_source"] == "root_deadline"
            assert not any(item.type in {"runtime.turn_started", "input.orchestrated"} for item in events)
            delivered = await h.tasks.claim(h.owner.id, worker_id="redelivery", expected_binding=h.binding)
            assert delivered.status == "interrupted" and delivered.attempt_no == h.owner.attempt_no
            assert len(requests) == 1 and not errors


@pytest.mark.asyncio
async def test_reliable_dto_response_can_finish_evidence_and_restore_after_root_deadline(harness, monkeypatch):
    from tests.test_framework_model_result_import import wire_evidence
    from tests.test_framework_semantic_result_import import prepared_input, restore_arguments

    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            wire_evidence(h, monkeypatch)
            _, prepared = prepared_input(h)
            store_response = h.policy._evidence.store_response

            async def late_evidence(*args, **kwargs):
                await asyncio.sleep(3.2)
                return await store_response(*args, **kwargs)

            monkeypatch.setattr(h.policy._evidence, "store_response", late_evidence)
            set_deadline(h, 3)
            with framework_owner_scope(h.owner.runtime_owner):
                result = await h.models.invoke(prepared.model_key, prepared.request,
                    transport_options=prepared.transport_options)
                before = await h.calls.snapshot(h.binding)
                call, = before.calls
                assert call.phase == "completed" and before.actual_tokens == 10
                assert (await h.calls.execution_deadline(h.owner.runtime_owner))[1] < 0
                restored = await h.models.restore_completed(prepared.model_key, prepared.request,
                    **restore_arguments(prepared, call))
                assert restored.text == result.text and restored.raw_response == result.raw_response
            assert len(requests) == 1 and await h.calls.snapshot(h.binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "binding", "turn"])
async def test_pregraph_stop_rejects_foreign_expiry_or_missing_owner(harness, mismatch):
    with _provider("openai_chat_completions") as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            runtime, _ = runtime_with(h.adapter)
            request = _runtime_request(h.session_id, h.binding.turn_id).model_copy(update={
                "runtime_binding": h.binding, "model_key": h.request.model_key})
            if mismatch == "turn":
                request.turn_id = "foreign-turn"
            expiry = FrameworkExecutionDeadlineExceeded(
                "foreign-binding" if mismatch == "binding" else h.binding.binding_id,
                await h.policy.capture_root_budget(h.binding))
            with framework_owner_scope(None if mismatch == "owner" else h.owner.runtime_owner):
                with pytest.raises(ContinuationLeaseLostError, match="captured runtime owner"):
                    await runtime.stop_expired_framework_turn(_runtime_session(h.session_id), request, expiry=expiry)
            assert requests == [] and await h.store.get_latest_snapshot(h.session_id) is None


@pytest.mark.asyncio
async def test_reliable_nested_dto_return_does_not_disarm_its_parent_graph_deadline(harness):
    from tests.test_framework_model_dto import dto_request

    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async with governed(harness, _config("openai_chat_completions", url)) as h:
            h.models.set_framework_model_call_policy(h.policy)
            returned, cancelled = asyncio.Event(), asyncio.Event()

            @tool
            async def inspect_source(path: str) -> str:
                """Call a governed DTO, then wait at a controlled tool boundary."""
                result = await h.models.invoke(h.request.model_key, dto_request(h, logical="nested-dto"))
                assert result.text == "fixture answer"
                returned.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            set_deadline(h, 3)
            task = asyncio.create_task(execute(h, replace(h.request, business_tools=[inspect_source])))
            try:
                await asyncio.wait_for(returned.wait(), 10)
                done, _ = await asyncio.wait({task}, timeout=5)
                assert task in done, "DTO completion disarmed the enclosing graph's root timer"
                with pytest.raises(FrameworkExecutionDeadlineExceeded):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            snapshot = await h.calls.snapshot(h.binding)
            assert cancelled.is_set() and len(requests) == snapshot.reserved_calls == 2
            assert snapshot.actual_tokens == 20 and all(call.phase == "completed" for call in snapshot.calls)
