"""Original formal receipts use the existing evidence evaluator, never a verdict shortcut."""
from copy import deepcopy
import asyncio
import importlib
import json
from threading import Event
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.task_deferred import TaskDeferred
from src.schemas.framework_approval import approval_value_hash
from src.infrastructure.postgres_runtime import postgres_connect
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest
from tests.test_controlled_case_executor import execution, executor
from tests.test_controlled_case_graph import business_bridge
from tests.test_controlled_case_runtime import assembly, prepare, implementation as runtime_implementation
from tests.test_controlled_case_projection_parts import checkpoint
from tests import test_framework_chat_models as providers


def implementation(tasks):
    return importlib.import_module("src.runtime.controlled_case_assessment").ControlledCaseAssessments(tasks)


def terminal_reply(transport, text):
    reply = providers._reply(transport)
    if transport == "openai_chat_completions":
        reply["choices"][0]["message"]["content"] = text
    elif transport == "openai_responses":
        reply["output"][0]["content"][0]["text"] = text
    elif transport == "anthropic_messages":
        reply["content"][0]["text"] = text
    else:
        reply["candidates"][0]["content"]["parts"][0]["text"] = text
    return reply


@pytest_asyncio.fixture
async def assessment_case(formal_case, tmp_path, monkeypatch, request):
    s = formal_case
    options = getattr(request, "param", {})
    if isinstance(options, str):
        options = {"transport": options}
    transport = options.get("transport", "openai_chat_completions")
    reply = providers._tool_reply(transport)
    if transport == "openai_responses":
        reply["id"] = "resp_fixture_assessment_tool_call"
    expected = {step.item.key: deepcopy(step.item.instructions if step.item.kind == "prepare" else step.content_json)
        for step in s.asset.context.steps}
    receipt = {"schema_version": 1, "steps": [{"item_key": key, "status": "passed", "summary": "fixture evidence check",
        "checks": [{"name": "fixed expected source", "expected_path_tokens": [], "operator": "equals",
            "observations": [{"call_id": "awaiting-original-business-call", "output_path_tokens": ["expectations", key]}]}], "outputs": {}}
        for key in expected]}
    terminal = terminal_reply(transport, json.dumps(receipt, ensure_ascii=False))
    monkeypatch.setattr(providers, "_reply", lambda selected: deepcopy(terminal))
    with providers._provider(transport, first_reply=reply) as (url, requests):
        async def prepare_input(models):
            return await prepare(s, assembly(s, tmp_path / "assembly", models))
        async with execution(s, providers._config(transport, url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=False) as bridge:
                async def handle(arguments, context):
                    effects.append(context.tool_job_id)
                    # The formal receipt uses the original business call ID,
                    # not the provider-native tool ID (runtime_service's mapping).
                    for step in receipt["steps"]:
                        step["checks"][0]["observations"][0]["call_id"] = context.call_id
                    kind = options.get("receipt")
                    if kind == "empty_checks":
                        for step in receipt["steps"]:
                            step["checks"] = []
                    elif kind == "missing_step":
                        receipt["steps"].pop()
                    elif kind == "duplicate_step":
                        receipt["steps"].append(deepcopy(receipt["steps"][0]))
                    elif kind == "foreign_call":
                        receipt["steps"][0]["checks"][0]["observations"][0]["call_id"] = "foreign-call"
                    elif kind == "wrong_path":
                        receipt["steps"][0]["checks"][0]["observations"][0]["output_path_tokens"] = ["missing-output"]
                    text = json.dumps(receipt, ensure_ascii=False)
                    if kind == "non_json":
                        text = "not a formal receipt"
                    elif kind == "truncated":
                        text = text[:-8]
                    updated = terminal_reply(transport, text)
                    terminal.clear()
                    terminal.update(updated)
                    actual = deepcopy(expected)
                    if kind == "false_actual":
                        actual[next(iter(actual))] = {"fixture_wrong_actual": True}
                    return {"status": "completed", "summary": "fixture only, not a real target", "expectations": actual,
                        "artifacts": [{"type": "text", "label": "fixture evidence", "content": "original fixture evidence"}]}
                bridge.runtime._tool_runtime_service._handlers["inspect_source"] = handle
                kwargs = {"session_service": s.h.sessions} if options.get("finalize", True) else {}
                if options.get("auto"):
                    kwargs["assessment_service"] = implementation(s.h.tasks)
                result = await runtime_implementation().ControlledCaseRuntime(runtime_service=bridge.runtime,
                    **kwargs).execute(h.task, executor=executor(s, h),
                        resource_id="isolated-formal-assessment", fencing_token=1)
                yield SimpleNamespace(s=s, h=h, result=result, requests=requests, effects=effects,
                    bridge=bridge, receipt=receipt)


async def freeze(p):
    with framework_owner_scope(p.h.task.runtime_owner):
        return await implementation(p.s.h.tasks).freeze(p.h.task.runtime_owner,
            native=p.result.graph_outcome.native_terminal)


@pytest.mark.asyncio
async def test_original_case_assessment_is_retained_once_without_completing_run_item(assessment_case, monkeypatch):
    p = assessment_case
    asset = await freeze(p)
    assert asset.scope == "controlled_case_assessment_preparation_only"
    assert asset.completion.status == "passed" and asset.verification_results
    assert all(v.status.value == "passed" and v.evidence for v in asset.verification_results)
    saved = checkpoint(p)
    assert saved.checkpoint_version == 5
    assert saved.checkpoint_payload["assessment"] == asset.model_dump(mode="json")
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
    before = database_digest(p.s)
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    def forbidden(*args, **kwargs):
        raise AssertionError("retained original assessment must not be rebuilt")
    monkeypatch.setattr(AgentCaseAssessmentService, "assess_from_evidence", forbidden)
    assert await freeze(p) == asset
    assert checkpoint(p) == saved and database_digest(p.s) == before
    assert len(p.requests) == 2 and len(p.effects) == 1
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {p.s.d.runs._result_table}")
        assert cur.fetchone()["n"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{"auto": True, "transport": t} for t in providers.TRANSPORTS], indirect=True)
async def test_explicit_runtime_callback_assesses_inside_original_dual_owner(assessment_case):
    p = assessment_case
    assert p.result.assessment_asset == await freeze(p)
    assert p.result.assessment_asset.completion.status == "passed"
    assert p.result.conversation_response.session.status.value == "completed"
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None
    with pytest.raises(ValueError, match="finalizer"):
        await p.s.h.tasks.finish(p.h.task, status="completed", result_status="completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["completion", "verification_results", "tool_records"])
async def test_unknown_completion_field_is_deferred_without_silent_drop(assessment_case, part):
    p = assessment_case
    await freeze(p)
    attempt = checkpoint(p)
    raw = deepcopy(attempt.checkpoint_payload)
    target = raw["assessment"][part]
    (target if isinstance(target, dict) else target[0])["future_completion_field"] = True
    future = attempt.model_copy(update={"checkpoint_payload": raw})
    before = database_digest(p.s)
    with pytest.raises(TaskDeferred, match="case_checkpoint_format"):
        FrameworkWorkerCapabilities("a" * 64, True).require_case_checkpoint_format(future)
    assert database_digest(p.s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{"receipt": k} for k in ["false_actual", "empty_checks"]], indirect=True)
async def test_model_reported_pass_does_not_replace_actual_evidence(assessment_case):
    p = assessment_case
    asset = await freeze(p)
    assert all(step["status"] == "passed" for step in p.receipt["steps"])
    empty = not p.receipt["steps"][0]["checks"]
    assert asset.completion.status == ("blocked" if empty else "failed")
    assert any(v.status.value == ("not_run" if empty else "failed") for v in asset.verification_results)
    assert len(p.requests) == 2 and len(p.effects) == 1  # no receipt-correction model call


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{"receipt": k} for k in [
    "non_json", "truncated", "missing_step", "duplicate_step", "foreign_call", "wrong_path"]], indirect=True)
async def test_invalid_original_receipts_do_not_gain_assessment_or_trigger_correction(assessment_case):
    p = assessment_case
    before = database_digest(p.s)
    with pytest.raises(ValueError):
        await freeze(p)
    assert checkpoint(p).checkpoint_version == 4 and database_digest(p.s) == before
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_case", [{"finalize": False}], indirect=True)
async def test_assessment_requires_original_application_proof(assessment_case):
    p = assessment_case
    before = database_digest(p.s)
    with pytest.raises(RuntimeError):
        await freeze(p)
    assert checkpoint(p).checkpoint_version == 2 and database_digest(p.s) == before


@pytest.mark.asyncio
async def test_load_does_not_create_missing_original(assessment_case):
    p = assessment_case
    before = database_digest(p.s)
    with framework_owner_scope(p.h.task.runtime_owner), pytest.raises(RuntimeError, match="assessment is missing"):
        await implementation(p.s.h.tasks).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    assert database_digest(p.s) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["scope", "missing_native", "native", "second_owner", "case_lease", "agent_lease"])
async def test_assessment_requires_exact_native_and_current_live_dual_owner(assessment_case, drift):
    p = assessment_case
    owner, native = p.h.task.runtime_owner, deepcopy(p.result.graph_outcome.native_terminal)
    if drift == "native":
        native.values["messages"][-1].content = "foreign"
    if drift == "second_owner":
        owner = owner.model_copy(update={"controlled_case_lease": None})
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        if drift == "case_lease":
            cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
        elif drift == "agent_lease":
            cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s", (p.s.binding.binding_id,))
    before = database_digest(p.s)
    with framework_owner_scope(None if drift == "scope" else owner), pytest.raises((ValueError, RuntimeError)):
        await implementation(p.s.h.tasks).freeze(owner, native=None if drift == "missing_native" else native)
    assert database_digest(p.s) == before and checkpoint(p).checkpoint_version == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["sql_rollback", "ack", "cancel_waiter"])
async def test_assessment_receipt_and_checkpoint_share_real_transaction(assessment_case, monkeypatch, failure, caplog):
    p = assessment_case
    cls = type(implementation(p.s.h.tasks))
    material, append = cls._material, p.s.h.tasks._sessions.append_event_in_transaction
    entered, release, finished = Event(), Event(), Event()
    before = database_digest(p.s)
    def append_part(*args, **kwargs):
        event = args[2]
        value = append(*args, **kwargs)
        if event.type == "test_run.case_assessment_prepared":
            if failure == "sql_rollback":
                raise RuntimeError("isolated assessment SQL rollback")
            if failure == "cancel_waiter":
                entered.set()
                assert release.wait(30)
        return value
    def material_part(self, *args):
        try:
            value = material(self, *args)
            if failure == "ack":
                raise RuntimeError("isolated assessment committed ACK loss")
            return value
        finally:
            finished.set()
    with monkeypatch.context() as patch:
        patch.setattr(cls, "_material", material_part)
        patch.setattr(p.s.h.tasks._sessions, "append_event_in_transaction", append_part)
        operation = asyncio.create_task(freeze(p))
        if failure == "cancel_waiter":
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 30)
        else:
            with pytest.raises(RuntimeError, match="isolated"):
                await operation
    committed = failure != "sql_rollback"
    assert checkpoint(p).checkpoint_version == (5 if committed else 4)
    if not committed:
        assert database_digest(p.s) == before
    assert any(getattr(r, "recovery_reason", None) == "assessment_write_receipt_unknown"
        and getattr(r, "automatic_replay", None) is False for r in caplog.records)
    if committed:
        from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
        def forbidden(*args, **kwargs):
            raise AssertionError("committed assessment must not be rebuilt after unknown ACK")
        monkeypatch.setattr(AgentCaseAssessmentService, "assess_from_evidence", forbidden)
    asset = await freeze(p)
    events = [e for e in await p.s.h.store.list_events(p.s.session.id) if e.type == "test_run.case_assessment_prepared"]
    assert len(events) == 1 and events[0].payload["assessment_sha256"] == asset.asset_sha256
    assert (await p.s.d.runs.get_item(p.s.item.id)).result_id is None


@pytest.mark.asyncio
async def test_concurrent_freeze_keeps_one_original_verification_set(assessment_case, monkeypatch):
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    p = assessment_case
    original, built = AgentCaseAssessmentService.assess_from_evidence, []
    def count(self, **kwargs):
        result = original(self, **kwargs)
        built.append(result)
        return result
    monkeypatch.setattr(AgentCaseAssessmentService, "assess_from_evidence", count)
    a, b = await asyncio.gather(freeze(p), freeze(p))
    assert a == b and len(built) == 1
    assert [v.model_dump(mode="json") for v in a.verification_results] == [
        v.model_dump(mode="json") for v in built[0].verification_results]
    assert len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["case", "agent"])
async def test_dual_leases_are_rechecked_after_evaluator_before_assessment_write(assessment_case, monkeypatch, lease):
    import src.runtime.controlled_case_assessment as assessments
    p = assessment_case
    read, count = assessments.ControlledCaseAssessments._source, []
    before = database_digest(p.s)
    def source(self, cur, owner, native):
        count.append(1)
        if len(count) == 2:
            if lease == "case":
                cur.execute(f"UPDATE {p.s.d.runs._item_table} SET record=jsonb_set(record,'{{lease_expires_at}}','\"2000-01-01T00:00:00+00:00\"') WHERE id=%s", (p.s.item.id,))
            else:
                cur.execute(f"UPDATE {p.s.h.tasks._tasks}_runtime SET expires_at='2000-01-01' WHERE binding_id=%s", (p.s.binding.binding_id,))
        return read(self, cur, owner, native)
    monkeypatch.setattr(assessments.ControlledCaseAssessments, "_source", source)
    with pytest.raises(RuntimeError):
        await freeze(p)
    assert database_digest(p.s) == before and checkpoint(p).checkpoint_version == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["job", "artifact", "hidden_artifact_metadata", "proof", "result", "asset", "audit", "missing_audit"])
async def test_retained_assessment_rejects_original_drift_without_repair(assessment_case, drift):
    p = assessment_case
    asset = await freeze(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        settings = p.s.h.settings
        if drift == "job":
            cur.execute(f"UPDATE {settings.database.postgres_tool_job_table} SET summary='foreign' WHERE id=%s", (p.effects[0],))
        elif drift in {"artifact", "hidden_artifact_metadata"}:
            change = "content_text='foreign'" if drift == "artifact" else "metadata=metadata || '{\"__content_text\":\"masked foreign fact\"}'::jsonb"
            cur.execute(f"UPDATE {settings.database.postgres_tool_artifact_table} SET {change} WHERE tool_job_id=%s", (p.effects[0],))
        elif drift in {"audit", "missing_audit"}:
            event_id = importlib.import_module("src.runtime.controlled_case_assessment").assessment_event(asset).id
            if drift == "missing_audit":
                cur.execute(f"DELETE FROM {settings.database.postgres_event_table} WHERE id=%s", (event_id,))
            else:
                cur.execute(f"UPDATE {settings.database.postgres_event_table} SET payload=payload || '{{\"foreign\":true}}'::jsonb WHERE id=%s", (event_id,))
        else:
            attempt = checkpoint(p)
            raw = deepcopy(attempt.checkpoint_payload)
            if drift == "proof":
                raw["projection_receipts"]["application_projection_commit"]["snapshot_id"] = "foreign"
            elif drift == "result":
                raw["runtime_result_asset"]["runtime_result"]["output_text"] = "foreign"
            else:
                raw["assessment"]["completion"]["summary"] = "foreign"
                values = {k: v for k, v in raw["assessment"].items() if k != "asset_sha256"}
                raw["assessment"]["asset_sha256"] = approval_value_hash(values)
            p.s.d.runs._write_attempt(cur, attempt.model_copy(update={"checkpoint_payload": raw}))
    before = database_digest(p.s)
    with pytest.raises((ValueError, RuntimeError)):
        await freeze(p)
    assert database_digest(p.s) == before and len(p.requests) == 2 and len(p.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["old_reader", "future_field", "future_version"])
async def test_checkpoint5_compatibility_never_grants_execution_or_rewrites_original(assessment_case, change):
    p = assessment_case
    await freeze(p)
    attempt = checkpoint(p)
    if change == "future_field":
        raw = deepcopy(attempt.checkpoint_payload)
        raw["future_assessment_field"] = True
        attempt = attempt.model_copy(update={"checkpoint_payload": raw})
    elif change == "future_version":
        attempt = attempt.model_copy(update={"checkpoint_version": 6})
    before = database_digest(p.s)
    with pytest.raises(TaskDeferred, match="case_checkpoint_format"):
        FrameworkWorkerCapabilities("a" * 64, True,
            controlled_case_checkpoint_max_version=4 if change == "old_reader" else 5).require_case_checkpoint_format(attempt)
    assert database_digest(p.s) == before


@pytest.mark.asyncio
async def test_schema5_preserves_assessment_when_original_parts_and_memory_receipts_are_saved(assessment_case, monkeypatch):
    from src.runtime.framework_projection_source import lock_result_source, save_projection_manifest
    p = assessment_case
    asset = await freeze(p)
    before = checkpoint(p)
    with postgres_connect(p.s.h.settings) as conn, conn.cursor() as cur:
        source, _ = lock_result_source(cur, p.s.h.settings, p.h.task.runtime_owner,
            controlled_case_native=p.result.graph_outcome.native_terminal)
        save_projection_manifest(cur, p.s.h.settings, source, source.source_manifest, message_version=27)
    assert checkpoint(p) == before
    def forbidden(*args, **kwargs):
        raise AssertionError("schema5 Session ACK cannot rebuild or replay Memory")
    monkeypatch.setattr(p.s.h.sessions, "_finalize_runtime_result", forbidden)
    with framework_owner_scope(p.h.task.runtime_owner):
        response = await p.s.h.sessions.finalize_controlled_case_result(p.h.task.runtime_owner,
            native=p.result.graph_outcome.native_terminal)
        loaded = await implementation(p.s.h.tasks).load(p.h.task.runtime_owner, native=p.result.graph_outcome.native_terminal)
    assert response.output.id == p.result.result_asset.assistant_message_id and loaded == asset
    assert checkpoint(p) == before
