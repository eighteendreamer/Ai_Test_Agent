"""Formal originals -> deterministic application result, not finalization/verdict."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import importlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.framework_event_completion import completion_event_id
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_runtime_result import FrameworkRuntimeResult
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest, submit
from tests.test_controlled_case_execution_lease import delivery
from tests.test_controlled_case_executor import execution, executor
from tests.test_controlled_case_graph import business_bridge
from tests.test_controlled_case_runtime import assembly, prepare, implementation, full_input
from tests.test_framework_chat_models import TRANSPORTS, _config, _provider, _tool_reply, _reply


def reader(tasks):
    return importlib.import_module("src.runtime.controlled_case_result_reader").ControlledCaseResultReader(tasks)


@pytest_asyncio.fixture
async def projected_case(formal_case, tmp_path):
    s = formal_case
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                result = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-case-result", fencing_token=1)
                yield SimpleNamespace(s=s, h=h, result=result, requests=requests, effects=effects, bridge=bridge)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_formal_native_completion_projects_real_application_result_inside_original_sink(formal_case, tmp_path, transport):
    s = formal_case
    with _provider(transport) as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, _config(transport, url), prepare_input=prepare_input) as h:
            async with business_bridge(h, [], approval_required=False) as bridge:
                outcome = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-case-result", fencing_token=1)
                result = outcome.runtime_result
                assert result is not None and result.output_text == "fixture answer"
                assert s.asset.schema_version == 3 and result.snapshot.stage == "completed"
                identity = s.asset.runtime_preparation.identity
                assert (result.snapshot.id, result.snapshot.version, result.snapshot.created_at) == (
                    identity.snapshot_id, identity.snapshot_version, identity.snapshot_created_at)
                summary = result.state["model_response_summary"]
                assert summary["harness"] == "langgraph" and "pilot_stage" not in summary
                assert summary["native_terminal"] == outcome.graph_outcome.native_terminal.proof.model_dump(mode="json")
                terminal = result.events[-1]
                assert terminal.type == "runtime.turn_completed"
                assert terminal.timestamp == datetime.fromisoformat(outcome.graph_outcome.native_terminal.checkpoint_timestamp)
                assert terminal.payload["timestamp_source"] == "native_checkpoint.ts"
                assert terminal.payload["controlled_case_result_version"] == 1
                persisted = await s.h.store.read_runtime_event_completion(s.binding, summary["native_terminal"])
                assert persisted.event_log == result.state["event_log"]
                assert persisted.completion.task_id == h.task.id and persisted.completion.returned_event_offset == 0
                assert result.state["runtime_messages"] == s.asset.runtime_preparation.initial_state["runtime_messages"]
                before = database_digest(s)
                replay = await reader(s.h.tasks).read(h.task.runtime_owner, s.asset, outcome.graph_outcome.native_terminal)
                assert FrameworkRuntimeResult.from_result(replay) == FrameworkRuntimeResult.from_result(result)
                assert database_digest(s) == before and len(requests) == 1
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None
                with pytest.raises(ValueError, match="finalizer"):
                    await s.h.tasks.finish(h.task, status="completed", result_status="completed")
                with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                    for table in (s.h.settings.database.postgres_message_table, s.h.settings.database.postgres_snapshot_table,
                                  f"{s.h.tasks._tasks}_message_stage", s.d.runs._result_table):
                        cur.execute(f"SELECT count(*) AS n FROM {table}")
                        assert cur.fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_tool_result_reuses_original_display_and_runtime_slots_not_jsonb_reencoding(projected_case):
    p = projected_case
    result = p.result.runtime_result
    assert result is not None and len(result.tool_messages) == 1 and len(result.state["tool_results"]) == 1
    record = result.state["tool_results"][0]
    assert record["job_id"] == p.effects[0][2]
    replay = await reader(p.s.h.tasks).read(p.h.task.runtime_owner, p.s.asset, p.result.graph_outcome.native_terminal)
    assert FrameworkRuntimeResult.from_result(result) == FrameworkRuntimeResult.from_result(replay)
    assert len(p.requests) == 2 and len(p.effects) == 1
    assert result.state["tool_messages"][0]["content"] != p.result.graph_outcome.values["messages"][2].content
    assert "output_reference" in result.state["tool_messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["native", "input", "job", "ledger", "event", "declaration", "lease"])
async def test_owned_result_reader_rejects_drift_without_reinvoking_or_writing(projected_case, drift, caplog):
    p, native = projected_case, deepcopy(projected_case.result.graph_outcome.native_terminal)
    s = p.s
    with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
        if drift == "native":
            native.values["messages"][-1].content = "foreign answer"
        elif drift == "input":
            raw = s.asset.model_dump(mode="json")
            raw["runtime_preparation"]["identity"]["snapshot_id"] = "foreign-snapshot"
            raw["asset_sha256"] = approval_value_hash({k: v for k, v in raw.items() if k != "asset_sha256"})
            cur.execute(f"UPDATE {s.h.tasks._tasks} SET input_payload=%s::jsonb WHERE id=%s",
                (json.dumps({"case_execution_input": raw}), p.h.task.id))
        elif drift == "job":
            cur.execute(f"UPDATE {s.h.settings.database.postgres_tool_job_table} SET summary='foreign' WHERE session_id=%s", (s.session.id,))
        elif drift == "ledger":
            cur.execute(f"UPDATE {s.calls.calls_table} SET record=jsonb_set(record,'{{phase}}','\"unknown\"') WHERE binding_id=%s", (s.binding.binding_id,))
        elif drift == "event":
            cur.execute(f"UPDATE {s.h.settings.database.postgres_event_table} SET payload=jsonb_set(payload,'{{message}}','\"foreign\"') WHERE id=%s",
                (p.result.runtime_result.events[0].id,))
        elif drift == "declaration":
            cur.execute(f"DELETE FROM {s.h.settings.database.postgres_event_table} WHERE id=%s", (completion_event_id(s.binding),))
        else:
            cur.execute(f"UPDATE {s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (s.item.id,))
    before = database_digest(s)
    with pytest.raises((ValueError, RuntimeError)):
        await reader(s.h.tasks).read(p.h.task.runtime_owner, s.asset, native)
    assert database_digest(s) == before and len(p.requests) == 2 and len(p.effects) == 1
    assert "controlled_case_result_read_rejected" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["terminal_event", "completion", "completion_ack_lost", "lost_case_lease"])
async def test_result_ack_failure_does_not_return_completed_result_or_replay(formal_case, tmp_path, monkeypatch, failure):
    s = formal_case
    with _provider("openai_chat_completions") as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            async with business_bridge(h, [], approval_required=False) as bridge:
                append, complete = s.h.store.append_event, s.h.store.complete_runtime_events
                invoke, captured = h.adapter.execute_frozen, []
                async def capture(*args, **kwargs):
                    outcome = await invoke(*args, **kwargs)
                    captured.append(outcome)
                    return outcome
                async def append_event(session_id, event, **kwargs):
                    if event.type == "runtime.turn_completed" and failure == "terminal_event":
                        raise RuntimeError("isolated terminal event ACK failure")
                    return await append(session_id, event, **kwargs)
                async def complete_events(*args, **kwargs):
                    if failure == "completion":
                        raise RuntimeError("isolated event collection ACK failure")
                    if failure == "lost_case_lease":
                        with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                            cur.execute(f"UPDATE {s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (s.item.id,))
                    event = await complete(*args, **kwargs)
                    if failure == "completion_ack_lost":
                        raise RuntimeError("isolated post-commit collection ACK loss")
                    return event
                monkeypatch.setattr(h.adapter, "execute_frozen", capture)
                monkeypatch.setattr(s.h.store, "append_event", append_event)
                monkeypatch.setattr(s.h.store, "complete_runtime_events", complete_events)
                runtime = implementation().ControlledCaseRuntime(runtime_service=bridge.runtime)
                with pytest.raises((RuntimeError, ContinuationLeaseLostError)):
                    await runtime.execute(h.task, executor=executor(s, h), resource_id="isolated-case-result", fencing_token=1)
                assert len(requests) == 1 and (await s.h.tasks.get(h.task.id)).status == "running"
                with postgres_connect(s.h.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT id FROM {s.h.settings.database.postgres_event_table} WHERE id=%s", (completion_event_id(s.binding),))
                    assert (cur.fetchone() is not None) == (failure == "completion_ack_lost")
                if failure == "completion_ack_lost":
                    original_read = await reader(s.h.tasks).read(h.task.runtime_owner, s.asset, captured[0].native_terminal)
                    assert original_read.output_text == "fixture answer"
                    assert (await s.d.runs.get_item(s.item.id)).result_id is None
                with pytest.raises((RuntimeError, ValueError)):
                    await runtime.execute(h.task, executor=executor(s, h), resource_id="isolated-case-result", fencing_token=1)
                assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["deny", "missing_handler"])
async def test_preexecution_refusal_is_a_real_result_but_never_external_tool_evidence(formal_case, tmp_path, policy):
    s = formal_case
    with _provider("openai_chat_completions", first_reply=_tool_reply("openai_chat_completions")) as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                descriptor = bridge.runtime._tool_registry.get("inspect_source")
                if policy == "deny":
                    descriptor = descriptor.model_copy(update={"exposure": "internal", "owner_mode_key": "foreign_mode"})
                if policy == "missing_handler":
                    from src.registry.tools import ToolModule

                    bridge.runtime._tool_registry._dynamic_tools[descriptor.key] = ToolModule(descriptor)
                else:
                    bridge.runtime._tool_registry.register_dynamic(descriptor, "inspect_source")
                outcome = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-refused-result", fencing_token=1)
                result = outcome.runtime_result
                record, = result.state["tool_results"]
                assert record["status"] == ("denied" if policy == "deny" else "failed") and record["job_id"] is None
                assert effects == [] and len(requests) == 2 and await bridge.jobs.list_jobs(session_id=s.session.id) == []
                slot = json.loads(result.state["tool_messages"][0]["content"])
                assert slot["output_reference"] is None
                replay = await reader(s.h.tasks).read(h.task.runtime_owner, s.asset, outcome.graph_outcome.native_terminal)
                assert FrameworkRuntimeResult.from_result(result) == FrameworkRuntimeResult.from_result(replay)
                assert (await s.d.runs.get_item(s.item.id)).result_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_rules", "boolean_version", "unknown_contract", "old_reader", "no_assembly"])
async def test_result_contract_is_explicit_and_old_workers_leave_it_unclaimed(full_input, change):
    s, asset = full_input.s, full_input.asset
    assert asset.schema_version == 3
    raw = asset.model_dump(mode="json")
    from src.schemas.controlled_case_task_input import parse_controlled_case_task_input

    if change in {"old_reader", "no_assembly"}:
        s.asset = asset
        s.command = s.command.model_copy(update={"case_execution_input": asset})
        await submit(s)
        before = database_digest(s)
        options = {"controlled_case_input_max_version": 2} if change == "old_reader" else {"turn_context_assembly_enabled": False}
        with pytest.raises(TaskDeferred, match="case_input_format"):
            await s.h.tasks.claim_controlled_case(delivery(s), worker_id="unavailable-result-reader",
                framework_capabilities=FrameworkWorkerCapabilities("a" * 64, True, controlled_case_available=True, **options))
        assert database_digest(s) == before
    else:
        if change == "missing_rules":
            raw.pop("result_preparation")
        elif change == "boolean_version":
            raw["result_preparation"]["schema_version"] = True
        else:
            raw["result_preparation"]["output_contract"] = "guess_from_last_nonempty_answer"
        raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
        with pytest.raises(ValueError):
            parse_controlled_case_task_input(raw)


@pytest.mark.asyncio
async def test_legacy_full_preparation_stays_readable_without_gaining_result_contract(formal_case, tmp_path):
    s = formal_case
    from src.schemas.controlled_case_task_input import parse_controlled_case_task_input

    with _provider("openai_chat_completions") as (url, requests):
        async def prepare_input(models):
            raw = (await prepare(s, assembly(s, tmp_path / "assembly", models))).model_dump(mode="json")
            raw["schema_version"] = 2
            raw.pop("result_preparation")
            raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
            return parse_controlled_case_task_input(raw)
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            async with business_bridge(h, [], approval_required=False) as bridge:
                result = await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-legacy-result", fencing_token=1)
                assert result.runtime_result is None and result.graph_outcome.native_terminal is not None
                assert len(requests) == 1
                assert all(item.type not in {"runtime.turn_completed", "runtime.event_log_completed"}
                    for item in await s.h.store.list_events(s.session.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_actual_nonempty_truncated_model_response_is_not_completed_application_result(formal_case, tmp_path, transport):
    s = formal_case
    reply = _reply(transport)
    if transport == "openai_chat_completions":
        reply["choices"][0]["finish_reason"] = "length"
    elif transport == "openai_responses":
        reply.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    elif transport == "anthropic_messages":
        reply["stop_reason"] = "max_tokens"
    else:
        reply["candidates"][0]["finishReason"] = "MAX_TOKENS"
    with _provider(transport, first_reply=reply) as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, _config(transport, url), prepare_input=prepare_input) as h:
            async with business_bridge(h, [], approval_required=False) as bridge:
                with pytest.raises(ValueError, match="incomplete or blocked"):
                    await implementation().ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                        h.task, executor=executor(s, h), resource_id="isolated-truncated-result", fencing_token=1)
                assert len(requests) == 1 and (await s.h.tasks.get(h.task.id)).status == "running"
                assert all(item.type not in {"runtime.turn_completed", "runtime.event_log_completed"}
                    for item in await s.h.store.list_events(s.session.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["empty_current", "missing_material", "extra_material", "wrong_source", "material_output", "wrong_prefix",
    "truncated", "blocked"])
async def test_pure_result_rules_do_not_fall_back_to_old_answers_or_invent_tool_originals(projected_case, change):
    p = projected_case
    from src.application.runtime.controlled_case_projection import project_controlled_case_result
    from src.schemas.framework_tool_projection import parse_tool_projection, TOOL_PROJECTION_KEY

    native = deepcopy(p.result.graph_outcome.native_terminal)
    materials = [parse_tool_projection(native.values["messages"][2].artifact[TOOL_PROJECTION_KEY])]
    events = deepcopy(p.result.runtime_result.state["event_log"][:-1])
    if change == "empty_current":
        native.values["messages"][1].content = "OLDER_ANSWER_MUST_NOT_BECOME_FINAL"
        native.values["messages"][-1].content = ""
    elif change == "missing_material":
        materials = []
    elif change == "extra_material":
        materials += deepcopy(materials)
    elif change == "wrong_source":
        raw = materials[0].model_dump(mode="json")
        raw["source"]["arguments"] = {"path": "foreign.py"}
        raw["sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "sha256"})
        materials = [parse_tool_projection(raw)]
    elif change == "material_output":
        raw = materials[0].model_dump(mode="json")
        raw["record"]["output"] = {"unrelated": "not the native original"}
        raw["sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "sha256"})
        materials = [parse_tool_projection(raw)]
    elif change in {"truncated", "blocked"}:
        native.values["messages"][-1].response_metadata["finish_reason"] = "length" if change == "truncated" else "content_filter"
    else:
        events[0]["payload"]["message"] = "foreign prefix"
    before = database_digest(p.s)
    with pytest.raises((ValueError, RuntimeError)):
        project_controlled_case_result(p.s.asset, native, p.result.graph_outcome.root_model_budget, events, materials)
    assert database_digest(p.s) == before and len(p.requests) == 2 and len(p.effects) == 1
