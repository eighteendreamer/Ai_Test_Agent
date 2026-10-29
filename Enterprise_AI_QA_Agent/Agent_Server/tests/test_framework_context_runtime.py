"""Cross-Turn coverage through real framework, PostgreSQL and local official SDK."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio

from src.application.models.framework_model_call_policy import FrameworkModelCallPolicy, model_config_fingerprint
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls
from src.schemas.framework_model_call import FrameworkModelBudget
from src.schemas.framework_approval import approval_value_hash
from src.schemas.project import ProjectCreateRequest
from src.schemas.session import InputAttachment, SessionStatus
from tests.live_postgres_config import LivePostgresTestConfig
from tests.test_agent_task_dispatch import harness
from tests.test_framework_checkpoint_fencing import checkpoint_harness
from tests.test_framework_approval_projection import projection_harness
from tests.test_framework_approval_runtime import build_runtime
from tests.test_framework_context_transcript import context_case, covered_asset
from tests.test_framework_chat_models import _config, _provider
from tests.test_live_postgres_concurrency import _drop_tables
from tests.test_runtime_binding_persistence import make_binding, make_command, make_message, make_payload


@pytest_asyncio.fixture(autouse=True)
async def context_project(monkeypatch):
    if not LivePostgresTestConfig().run_live_postgres_tests:
        pytest.skip("requires real PostgreSQL and Redis")
    from tests import test_agent_task_dispatch as dispatch_module

    settings = dispatch_module.get_settings().model_copy(deep=True)
    settings.database.postgres_project_table = f"agent_test_context_project_{uuid4().hex[:12]}"
    projects = ProjectService(store=PostgresProjectStore(settings))
    try:
        await projects.initialize()
        project = await projects.create(ProjectCreateRequest(project_key="context-test", name="Context fixture"))
        # Autouse runs before the imported harness creates its Session FK;
        # dependent fixtures remove their Session tables before this teardown.
        monkeypatch.setattr(dispatch_module, "get_settings", lambda: settings)
        yield SimpleNamespace(project=project, service=projects)
    finally:
        _drop_tables(settings, [settings.database.postgres_project_table])


@pytest.mark.asyncio
@pytest.mark.parametrize("summarize", [False, True])
@pytest.mark.parametrize("native_prefix", [False, True])
@pytest.mark.parametrize("approval", [False, True])
@pytest.mark.parametrize("runtime_version", ["v2.1.0", "v2.2.0", "v2.3.0"])
async def test_admitted_context_reaches_sdk_and_next_turn_reuses_the_published_native_asset(
    projection_harness, native_prefix, approval, monkeypatch, runtime_version, tmp_path, context_project, summarize,
):
    from tests import test_framework_chat_models as provider_module

    h = projection_harness
    h.sessions._project_service = context_project.service
    await h.tasks.finish(h.owner, status="completed", result_status="completed")
    session = await h.store.get_session(h.session_id)
    session.status = SessionStatus.idle
    if runtime_version in {"v2.2.0", "v2.3.0"}:
        session.project_id = context_project.project.id
    await h.store.save_session(session)
    calls = PostgresFrameworkModelCalls(h.settings)
    await calls.initialize()
    h.tables.extend([calls.budgets_table, calls.calls_table])
    original_reply = provider_module._reply
    reply_ids = []
    agent_reply_ids = []
    if summarize:
        from tests.test_framework_model_policy import force_summary

        force_summary(monkeypatch)

    def unique_reply(transport):
        reply = original_reply(transport)
        reply["id"] = f"chatcmpl_context_{len(reply_ids)}"
        agent_request = bool(requests[-1]["body"].get("tools"))
        if approval and agent_request and not agent_reply_ids:
            reply["choices"][0].update(finish_reason="tool_calls", message={"role": "assistant", "content": None,
                "tool_calls": [{"id": "current-inspect", "type": "function", "function": {
                    "name": "inspect_value", "arguments": '{"value":"A"}'}}]})
        reply_ids.append(reply["id"])
        if agent_request:
            agent_reply_ids.append(reply["id"])
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
        parts = None
        if runtime_version in {"v2.2.0", "v2.3.0"}:
            from tests.test_framework_turn_context import assembly_components, build_assembly

            parts = assembly_components(tmp_path, tools=h.registry)
            h.runtime._turn_context_assembly = build_assembly(parts)
        h.sessions._runtime_service = h.runtime
        if runtime_version == "v2.3.0":
            from src.application.context.memory_runtime_service import MemoryRuntimeService
            from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore

            h.settings.database.postgres_memory_table = f"agent_test_summary_memory_{uuid4().hex[:12]}"
            memory = PostgresVectorMemoryStore(h.settings)
            await memory.initialize()
            h.tables.append(h.settings.database.postgres_memory_table)
            h.sessions._memory_runtime_service = MemoryRuntimeService(memory)
        previous_asset = None
        for index in range(2):
            request_start = len(requests)
            h.binding = make_binding(h.session_id, f"context-turn-{uuid4().hex}", runtime_version=runtime_version,
                model_config_fingerprint=fingerprint)
            budget = FrameworkModelBudget(max_calls=4, max_tokens=500_000,
                deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                model_config_fingerprints={"fake": fingerprint})
            payload = make_payload().model_copy(update={"content": "repeat", "model_key": "fake",
                **({"skill_keys": ["tdd-review"], "agent_key": "context-reviewer"} if parts is not None else {}),
                "attachments": [InputAttachment(name="context.txt", text_excerpt="ATTACHMENT_CONTEXT")],
                "context": {"requested_tool_keys": ["inspect_value"],
                    "framework_transcript_preparation": {"source_input_message_id": "future-user"}}})
            admitted = make_message(h, h.binding).model_copy(update={"content": "repeat"})
            command = make_command(h, h.binding).model_copy(update={"project_id": session.project_id})
            submitted = await h.tasks.submit_v2(command, payload, admitted,
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
                    if parts is not None:
                        original_skill = parts.skill_file.read_text(encoding="utf-8")
                        parts.skill_file.write_text("CHANGED_SKILL_MUST_NOT_RELOAD", encoding="utf-8")
                        original_descriptor = h.registry.get("inspect_value")
                        h.registry.register_dynamic(original_descriptor.model_copy(update={
                            "description": "CHANGED_SCHEMA_MUST_NOT_RELOAD"}), "inspect_value")
                        reads_before_resume = deepcopy(parts.reads)

                        async def forbidden(*args, **kwargs):
                            raise AssertionError("Approval cannot reassemble its context")

                        with monkeypatch.context() as patch:
                            patch.setattr(h.runtime, "_turn_context_assembly", SimpleNamespace(prepare=forbidden))
                            response = await h.sessions.execute_claimed_approval(owner, ownership_monitor=monitor)
                        assert parts.reads == reads_before_resume
                        assert h.adapter_requests[-1].system_prompt == initial_adapter_request.system_prompt
                        assert h.adapter_requests[-1].business_tools[0].description == original_descriptor.description
                        h.registry.register_dynamic(original_descriptor, "inspect_value")
                        parts.skill_file.write_text(original_skill, encoding="utf-8")
                    else:
                        response = await h.sessions.execute_claimed_approval(owner, ownership_monitor=monitor)
                    assert [value for value, job in h.effects] == ["A"]
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            assert response.session.status.value == "completed" and response.output.content == "fixture answer"
            stage = await h.tasks.get_framework_message_stage(submitted.id, session_id=h.session_id)
            assert stage.execution_request["user_message_id"] == admitted.id
            asset = (await h.store.get_session(h.session_id)).metadata["framework_transcript_asset"]
            assert asset["schema_version"] == (3 if runtime_version == "v2.3.0" else 2)
            assert asset["source_input_message_id"] == admitted.id
            assert asset["runtime_version"] == runtime_version
            if runtime_version in {"v2.2.0", "v2.3.0"}:
                assert asset["model_provider"] == config.provider
                assert asset["model_transport"] == config.transport
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
            if parts is not None:
                initial_state = graph_input["runtime_preparation"]["initial_state"]
                assert graph_input["system_prompt"] == initial_adapter_request.system_prompt == initial_state["system_prompt"]
                assert graph_input["context"]["skill_prompt_snapshots"] == initial_state["context_bundle"]["skill_prompt_snapshots"]
                assert len(parts.reads) == 5 * (index + 1)
                for marker in ("FROZEN_SKILL_BODY", "OBSERVATION_CONTEXT", "MEMORY_CONTEXT", "CASE_CONTEXT", "MCP_CONTEXT",
                    "ATTACHMENT_CONTEXT", "# Identity", "# Output Language Preference"):
                    assert initial_adapter_request.system_prompt.count(marker) == 1
                assert "code review pilot" not in initial_adapter_request.system_prompt
                assert "registered `skill`" not in initial_adapter_request.system_prompt
                assert graph_input["business_tool_keys"] == ["inspect_value"]
                frozen_descriptor, = graph_input["context"]["framework_tool_descriptors"]
                assert frozen_descriptor["input_schema"] == initial_adapter_request.business_tools[0].args_schema
                wire_system = json.dumps([item for item in requests[-1]["body"]["messages"] if item["role"] == "system"])
                assert wire_system.count("FROZEN_SKILL_BODY") == wire_system.count("# Identity") == 1
            body = requests[-1]["body"]["messages"]
            turn_requests = requests[request_start:]
            summary_requests = [item for item in turn_requests if not item["body"].get("tools")]
            agent_requests = [item for item in turn_requests if item["body"].get("tools")]
            if summarize:
                assert len(summary_requests) == len(agent_requests) == (2 if approval and index == 0 else 1)
                summary_prompt = summary_requests[0]["body"]["messages"][0]["content"]
                assert "Earlier review" in summary_prompt and "Legacy answer" in summary_prompt
                initial_body = agent_requests[0]["body"]["messages"]
                assert sum(item.get("content") == "repeat" for item in initial_body) == 1
                assert "Earlier review" not in json.dumps(body)
                from langchain_core.messages import messages_to_dict
                from src.schemas.runtime_binding import RuntimeCheckpointSource

                source = RuntimeCheckpointSource.model_validate(owner.runtime_owner.model_dump(exclude={"owner_token"}))
                adapter = h.runtime._deep_agent_bound_runtime_adapter
                async with adapter.open_native_reader(graph_input, source=source) as (agent, saver):
                    terminal = await agent.aget_state({"configurable": {"thread_id": h.binding.thread_id}})
                assert not terminal.next and not terminal.tasks and not terminal.interrupts
                assert messages_to_dict(terminal.values["messages"]) == asset["framework_messages"]
                event = terminal.values["_summarization_event"]
                assert 0 < event["cutoff_index"] < len(asset["framework_messages"])
                assert any(item.get("content") == event["summary_message"].content for item in body)
                assert not any(item["data"]["additional_kwargs"].get("lc_source") == "summarization"
                    for item in asset["framework_messages"])
                if runtime_version == "v2.3.0":
                    coverage = asset["summary_coverage"]
                    covered = asset["framework_messages"][:event["cutoff_index"]]
                    assert coverage["covered_message_ids"] == [item["data"]["id"] for item in covered]
                    assert coverage["covered_messages_sha256"] == approval_value_hash(covered)
                    assert coverage["summary_message"] == messages_to_dict([event["summary_message"]])[0]
                    assert graph_input["feature_flags"]["summary_coverage_version"] == 1
                    assert stage.schema_version >= 28
            else:
                assert summary_requests == []
                if runtime_version == "v2.3.0":
                    assert asset.get("summary_coverage") is None
                assert [item["content"] for item in body if item["role"] == "user"] == [
                    "Earlier review", *(["repeat"] * (index + 2))]
                assert sum(item["content"] == "Legacy answer" for item in body) == 1
            wire = json.dumps(turn_requests, ensure_ascii=False)
            assert "not yet admitted to this Turn" not in wire and "not context" not in wire
            if native_prefix:
                assert asset["framework_messages"][1]["data"]["additional_kwargs"]["provider_signature"] == "fixture-native-signature"
                if not summarize:
                    assert next(item for item in body if item.get("tool_calls"))["tool_calls"][0]["id"] == "previous-call"
                    assert next(item for item in body if item["role"] == "tool")["tool_call_id"] == "previous-call"
                else:
                    assert "previous-call" in summary_requests[0]["body"]["messages"][0]["content"]
                assert asset["framework_messages"][2]["data"]["tool_call_id"] == "previous-call"
            assert sum(item["data"]["id"] == admitted.id for item in asset["framework_messages"]) == 1
            if previous_asset is not None:
                assert asset["framework_messages"][:len(previous_asset["framework_messages"])] == previous_asset["framework_messages"]
            previous_asset = deepcopy(asset)
            ledger = await calls.snapshot(h.binding)
            purposes = ["summary", "agent"] if summarize else ["agent"]
            assert [item.intent.purpose for item in ledger.calls] == purposes * (2 if approval and index == 0 else 1)
            assert all(item.phase == "completed" for item in ledger.calls)
            assert ledger.reserved_calls == len(turn_requests) and ledger.actual_tokens == 10 * len(turn_requests)
            await h.tasks.finish(owner, status="completed", result_status="completed")
        assert len(requests) == len(reply_ids) == (3 if approval else 2) * (2 if summarize else 1)
        assert len(agent_reply_ids) == (3 if approval else 2)
        assert len(h.effects) == (1 if approval else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["old_reader", "legacy_binding", "bad_digest", "old_assembly_reader"])
async def test_real_durable_task_is_not_claimed_when_transcript_capability_is_incompatible(harness, case):
    from src.infrastructure.redis_task_queue import QueuedTask
    from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
    from src.runtime.task_deferred import TaskDeferred

    h = harness
    selected = make_binding(h.session_id, f"context-turn-{uuid4().hex}",
        runtime_version="v2.2.0" if case == "old_assembly_reader" else "v2.1.0")
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
        context_transcript_enabled=case != "old_reader", turn_context_assembly_enabled=case != "old_assembly_reader"))
    before = await h.tasks.get(submitted.id)
    with pytest.raises(TaskDeferred, match="unavailable"):
        await h.dispatcher.handle_v2(QueuedTask("context-incompatible", command.model_dump(mode="json")), worker_id="reader")
    after = await h.tasks.get(submitted.id)
    assert after == before and after.status == "queued" and after.attempt_no == 0
    assert (await h.store.get_session(h.session_id)).metadata["framework_transcript_asset"] == asset


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_version", ["v2.2.0", "v2.3.0"])
@pytest.mark.parametrize("approval", [False, True])
@pytest.mark.parametrize("complete_recovery", [False, True])
async def test_full_context_original_result_reader_never_reloads_prompt_or_skill_sources(
    projection_harness, tmp_path, monkeypatch, approval, context_project, complete_recovery, runtime_version,
):
    from src.schemas.framework_runtime_result import FrameworkRuntimeResult
    from tests.test_framework_approval_continuation import claim_decision
    from tests.test_framework_turn_context import assembly_components, build_assembly
    from tests import test_framework_chat_models as provider_module

    h = projection_harness
    h.sessions._project_service = context_project.service
    await h.tasks.finish(h.owner, status="completed", result_status="completed")
    session = await h.store.get_session(h.session_id)
    session.status = SessionStatus.idle
    session.project_id = context_project.project.id
    await h.store.save_session(session)
    calls = PostgresFrameworkModelCalls(h.settings)
    await calls.initialize()
    h.tables.extend([calls.budgets_table, calls.calls_table])
    first_reply = None
    if runtime_version == "v2.3.0":
        from tests.test_framework_model_policy import force_summary

        force_summary(monkeypatch)
    if approval:
        first_reply = provider_module._tool_reply("openai_chat_completions")
        first_reply["choices"][0]["message"]["tool_calls"] = [{"id": "frozen-inspect", "type": "function",
            "function": {"name": "inspect_value", "arguments": '{"value":"A"}'}}]
    with _provider("openai_chat_completions", first_reply=first_reply if runtime_version == "v2.2.0" else None) as (url, requests):
        if runtime_version == "v2.3.0":
            original_reply = provider_module._reply
            sent_tool = []

            def reply(transport):
                if approval and requests[-1]["body"].get("tools") and not sent_tool:
                    sent_tool.append(True)
                    return deepcopy(first_reply)
                return original_reply(transport)

            monkeypatch.setattr(provider_module, "_reply", reply)
        config = _config("openai_chat_completions", url, key="fake")
        fingerprint = model_config_fingerprint(config)
        h.binding = make_binding(h.session_id, f"assembly-gap-{uuid4().hex}", runtime_version=runtime_version,
            model_config_fingerprint=fingerprint)
        budget = FrameworkModelBudget(max_calls=4, max_tokens=500_000,
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            model_config_fingerprints={"fake": fingerprint})
        payload = make_payload().model_copy(update={"content": "Inspect once", "model_key": "fake",
            "agent_key": "context-reviewer", "skill_keys": ["tdd-review"],
            "attachments": [InputAttachment(name="context.txt", text_excerpt="ATTACHMENT_CONTEXT")],
            "context": {"requested_tool_keys": ["inspect_value"]}})
        admitted = make_message(h, h.binding).model_copy(update={"content": "Inspect once"})
        command = make_command(h, h.binding).model_copy(update={"project_id": session.project_id})
        submitted = await h.tasks.submit_v2(command, payload, admitted,
            idempotency_key=uuid4().hex, model_budget=budget)
        if runtime_version == "v2.3.0":
            from tests.test_framework_context_transcript import message

            session = await h.store.get_session(h.session_id)
            current_created_at = session.messages[-1].created_at
            prior_user = message("summary-old-user", "user", "Prior question", "summary-old-turn").model_copy(
                update={"created_at": current_created_at - timedelta(seconds=2)})
            prior_answer = message("summary-old-answer", "assistant", "Prior evidence", "summary-old-turn").model_copy(
                update={"created_at": current_created_at - timedelta(seconds=1)})
            session.messages = [prior_user, prior_answer, *session.messages]
            await h.store.save_session(session)
        owner = await h.tasks.claim(submitted.id, worker_id="assembly-gap", expected_binding=h.binding)
        await h.tasks.begin(owner, resource_id="assembly-gap", fencing_token=1)
        policy = FrameworkModelCallPolicy(calls=calls, model_config_resolver=lambda key: config)
        h.effects = []
        build_runtime(h, model_call_policy=policy)
        models = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), h.settings)
        h.runtime._model_runtime_service = models
        adapter = h.runtime._deep_agent_bound_runtime_adapter
        adapter._model_resolver = models.resolve_langchain_chat_model
        parts = assembly_components(tmp_path, tools=h.registry)
        h.runtime._turn_context_assembly = build_assembly(parts)
        h.sessions._runtime_service = h.runtime
        monitor = asyncio.create_task(asyncio.Event().wait())
        captured = []
        try:
            if approval:
                waiting = await h.sessions.execute_claimed_message(owner, ownership_monitor=monitor)
                assert waiting.session.status.value == "waiting_approval"
                approvals = await h.store.list_approvals(h.session_id)
                h.proposals = {"A": SimpleNamespace(approval=next(item for item in approvals
                    if item.metadata.get("call_id") == "frozen-inspect"))}
                await h.tasks.finish(owner, status="completed", result_status="waiting_approval")
                owner = await claim_decision(h, key="A")
            method = "resume_framework_approval" if approval else "execute_turn"
            original = getattr(h.runtime, method)

            async def terminal_to_asset_gap(*args, **kwargs):
                async def before_asset(result):
                    captured.append(FrameworkRuntimeResult.from_result(result))
                    raise ConnectionError("injected full context terminal-to-asset gap")

                kwargs["on_runtime_result"] = before_asset
                return await original(*args, **kwargs)

            monkeypatch.setattr(h.runtime, method, terminal_to_asset_gap)
            with pytest.raises(ConnectionError, match="terminal-to-asset gap"):
                if approval:
                    await h.sessions.execute_claimed_approval(owner, ownership_monitor=monitor)
                else:
                    await h.sessions.execute_claimed_message(owner, ownership_monitor=monitor)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        assert len(captured) == 1
        parts.skill_file.unlink()
        before_reads = deepcopy(parts.reads)
        before_events = await h.store.list_events(h.session_id)
        before_stage = await h.tasks.get_framework_message_stage(submitted.id, session_id=h.session_id)
        before_task = await h.tasks.get(owner.id)

        def forbidden(*args, **kwargs):
            raise AssertionError("Original result reader cannot execute or reload mutable context")

        monkeypatch.setattr(h.runtime, "_turn_context_assembly", SimpleNamespace(prepare=forbidden))
        monkeypatch.setattr(parts.prompt_assembly_service, "_general_settings_loader", forbidden)
        monkeypatch.setattr(adapter, "_model_resolver", forbidden)
        if approval:
            result = await h.runtime.read_completed_framework_approval(h.binding, owner.id, tasks=h.tasks)
            graph_input = result.preparation.graph_input_asset
        else:
            result = await h.runtime.read_completed_framework_message(h.binding, owner.id, tasks=h.tasks)
            graph_input = result.graph_input_asset
        assert result.runtime_result.model_dump(mode="json") == captured[0].model_dump(mode="json")
        if runtime_version == "v2.3.0":
            assert result.runtime_result.state["model_response_summary"]["native_summary_coverage"] is not None
        assert graph_input.system_prompt == captured[0].state["system_prompt"]
        for marker in ("FROZEN_SKILL_BODY", "OBSERVATION_CONTEXT", "MEMORY_CONTEXT", "CASE_CONTEXT", "MCP_CONTEXT",
            "ATTACHMENT_CONTEXT", "# Identity"):
            assert graph_input.system_prompt.count(marker) == 1
        assert graph_input.context["skill_prompt_snapshots"] == captured[0].state["context_bundle"]["skill_prompt_snapshots"]
        assert await h.tasks.get_framework_message_stage(submitted.id, session_id=h.session_id) == before_stage
        assert await h.tasks.get(owner.id) == before_task
        assert await h.store.list_events(h.session_id) == before_events
        assert parts.reads == before_reads
        assert len(h.effects) == (1 if approval else 0)
        assert len(requests) == (await calls.snapshot(h.binding)).reserved_calls == (
            (2 if approval else 1) * (2 if runtime_version == "v2.3.0" else 1))
        if not complete_recovery:
            from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
            from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

            other = await context_project.service.create(ProjectCreateRequest(project_key="other-context", name="Other context"))
            changed = await h.store.get_session(h.session_id)
            changed.project_id = other.id
            await h.store.save_session(changed)
            with pytest.raises((FrameworkApprovalRecoveryRequired, FrameworkMessageStageRecoveryRequired),
                match="matching original"):
                if approval:
                    await h.runtime.read_completed_framework_approval(h.binding, owner.id, tasks=h.tasks)
                else:
                    await h.runtime.read_completed_framework_message(h.binding, owner.id, tasks=h.tasks)
            return

        from tests.test_framework_approval_recovery import expire, run_delivery

        h.calls = calls
        monkeypatch.setattr(h.runtime, "execute_turn", forbidden)
        monkeypatch.setattr(h.runtime, "resume_framework_approval", forbidden)
        monkeypatch.setattr(h.sessions._input_orchestrator_service, "orchestrate_async", forbidden)
        monkeypatch.setattr(h.sessions._verification_service, "build_results", forbidden)
        terminal = await expire(h, owner)
        if approval:
            reconciler = await h.tasks.ensure_framework_reconciliation(owner.id)
            assert reconciler is not None
            await run_delivery(h, reconciler.id)
            reconciled = await h.tasks.get(reconciler.id)
            assert reconciled.status == "completed", reconciled.error_code
            successor = await h.tasks.get(reconciled.result_payload["successor_task_id"])
            assert successor.input["recovery_proof"]["scope"] == "application_finalization"
        else:
            successor = await h.tasks.ensure_framework_message_recovery(owner.id, native_reader=adapter.open_native_reader)
            assert successor is not None
        await run_delivery(h, successor.id)
        completed = await h.tasks.get(successor.id)
        assert completed.status == "completed", completed.error_code
        assert await h.tasks.get(owner.id) == terminal
        events = await h.store.list_events(h.session_id)
        assert len([event for event in events if event.type == "turn.completed"
            and event.payload.get("session_status") == "completed"]) == 1
        await run_delivery(h, successor.id)
        assert await h.tasks.get(successor.id) == completed
        assert await h.store.list_events(h.session_id) == events
        assert parts.reads == before_reads and len(h.effects) == (1 if approval else 0)
        assert len(requests) == (2 if approval else 1) * (2 if runtime_version == "v2.3.0" else 1)
