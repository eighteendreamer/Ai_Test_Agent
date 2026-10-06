"""Use captured original Jobs and actual native tools/storage to verify evidence."""
import ast
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import types
import sys
from uuid import uuid4

import pytest

from test_agent_case_execution import adapter_environment, regression_business_http
from test_case_operations import POLICY, LIVE
from test_case_planning import active_case, item, finish
from test_case_shared_effect_research import SharedPlanningModel
from src.application.runtime.case_action_binding import ARGUMENT, CaseActionClaimUnavailable, _digest
from src.application.runtime.case_effect_evidence import INTENT_KEY, capture_returned_evidence
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_cases.planning_service import CasePlanningService
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import CasePlanningPolicy, CasePlanRunRequest, CasePlanningRequest, CasePlanExecutionSnapshot
from src.schemas.run_management import RunClaimRequest, RunItemLeaseRequest
from src.schemas.tool_job import ToolJobRecord
from src.schemas.tool_runtime import ModelToolCall

ROOT = Path(__file__).resolve().parents[2]
NEW_POLICY = POLICY.model_copy(update={"preparation_scope_protocol": "preparation-scope-v1",
    "effect_evidence_protocol": "case-effect-evidence-v1"})


def declaration(role):
    checks = [{"output_path_tokens": ["task_result", "response_status"],
        "expected_path_tokens": ["expected"], "operator": "equals"}]
    if role == "cleanup":
        checks.append({"output_path_tokens": ["task_result", "execution_checkpoint", "http_observation_v1", "hops", "0", "method"],
            "expected_path_tokens": ["method"], "operator": "equals"})
    return {"role": role, "object_path_tokens": ["task_result", "response_body", "order.id"],
        "collection": False, "checks": checks}


@asynccontextmanager
async def original_effect_runtime(tmp_path, business, max_bytes=None, formal_source=False,
    coverage=False, coverage_max_bytes=None, select_operations=False, declaration_factory=None,
    plan_transform=None):
    declare = declaration_factory or declaration
    policy = NEW_POLICY if max_bytes is None else NEW_POLICY.model_copy(update={"effect_evidence_max_bytes": max_bytes})
    if coverage:
        policy = policy.model_copy(update={"effect_coverage_protocol": "case-effect-coverage-v1",
            "effect_coverage_max_bytes": coverage_max_bytes})
    async with adapter_environment(tmp_path, business[0], live=True, start_item=False,
        planning_policy=policy) as values:
        env, _, _, _, runs, _, adapter, _, _, services = values
        base = services["environment"]
        project = await base.projects.get(env[0].project_id)
        captured = ROOT / "docs/upgrade_2026-10-03/p4-shared-effect-wire/first-consumer-only-left-passed-finished/shared-left.json"
        content = json.loads(captured.read_text(encoding="utf-8"))
        content["objective"] = "完整对象证据用例"
        content["steps"][0]["url"] = business[0][0] + "/orders/{id}"
        if formal_source:
            content["期望"]["code"] = 201
            content["steps"][0].update(method="POST", url=business[0][0] + "/orders")
        source = tmp_path / "effect-evidence-case.json"
        source.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        doc = await base.docs.upload_document(project_id=project.id, filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await base.objects.read_object_uri(doc.storage_uri))["content"] == source.read_bytes()
        await active_case(base, project, "effect-evidence", json.loads(source.read_text(encoding="utf-8")))
        if formal_source:
            consumer = deepcopy(content)
            consumer["objective"] = "独立完整消费用例"
            consumer["期望"]["code"] = 200
            consumer["steps"][0].update(method="GET", url=business[0][0] + "/orders/{id}")
            await active_case(base, project, "effect-consumer", consumer)

        def plan(c):
            if coverage and not any(e["kind"] == "tool" for e in c["evidence"]):
                return {"action": "query", "summary": "读取固定实际工具接口",
                    "queries": [{"kind": "tool", "source_id": "api-test-runner"}]}
            target = item(c, "effect-consumer" if formal_source else "effect-evidence", key="target", depends_on=["seed"],
                input_bindings={"id": {"source_item": "seed", "output_path_tokens": ["body", "order.id"]}})
            refs = target["evidence_ids"]
            if coverage:
                contract = next(e for e in c["evidence"] if e["kind"] == "tool")
                assert "response_status" in contract["payload"]["output_variants"][0]["schema"]["properties"]
                refs.append(contract["id"])
            producer = {"key": "seed", "kind": "prepare", "reason": "按实际依据记录产生对象",
                "evidence_ids": refs, "instructions": {"expected": 201, "method": "POST", "url": business[0][0] + "/orders"},
                "effect_evidence": declare("produced")}
            if formal_source:
                formal_declaration = declare("produced")
                formal_declaration["checks"][0]["expected_path_tokens"] = ["期望", "code"]
                producer = item(c, "effect-evidence", key="seed", kind="dependency", effect_evidence=formal_declaration)
            if select_operations:
                producer["effect_evidence"]["select_when"] = [{"input_path_tokens": ["task", "method"],
                    "expected_path_tokens": ["steps", "0", "method"] if formal_source else ["method"]}]
            if coverage and formal_source:
                producer["evidence_ids"].append(contract["id"])
            result = finish(producer, target,
                {"key": "cleanup", "kind": "prepare", "reason": "按固定证据核对清理对象",
                    "depends_on": ["target"], "evidence_ids": refs, "cleanup_sources": ["seed"],
                    "instructions": {"expected": 200, "method": "DELETE", "url": business[0][0] + "/orders/{id}"},
                    "effect_evidence": declare("cleanup")})
            return plan_transform(result) if plan_transform else result
        base.service._models = SharedPlanningModel(plan, plan) if coverage else SharedPlanningModel(plan)
        planned = await base.service.create(project.id, CasePlanningRequest(objective="实际原工具对象证据"))
        assert planned.status == "planned", planned.summary
        detail = await services["creator"].create(project.id, planned.id,
            CasePlanRunRequest(creation_key="effect-evidence", mode_key="api_testing"))
        claim = (await runs.claim(detail.run.id, RunClaimRequest(worker_id="effect-evidence", limit=1))).claims[0]
        running = await runs.start_item(claim.item.id, RunItemLeaseRequest(lease_token=claim.lease_token))
        attempt = await runs.get_latest_attempt(claim.item.id)
        context, checkpoint, attempt = await adapter()._load(run=await runs.get_record(detail.run.id), item=running,
            version=await base.cases.get_version(claim.item.case_version_id), attempt_id=attempt.id)
        await adapter()._save(running, attempt, checkpoint.model_copy(update={"phase": "running"}))
        execution = ToolExecutionContext(session_id=checkpoint.session_id, turn_id=checkpoint.turn_id,
            trace_id="actual-effect-evidence", user_message=context.objective, normalized_input=context.objective,
            context_bundle={**context.runtime_context(), "case_execution_managed": True, "attempt_id": checkpoint.execution_attempt_id})
        runtime = services["tool_runtime"]
        tool = ToolRegistry().get("api-test-runner")
        store = env[1]._store.effect_evidence
        async def action(call_id, key, operation, method, path):
            args = {ARGUMENT: {"item_key": key, "operation_key": operation}, "worker_action": "execute_task",
                "task": {"task_id": operation, "method": method, "path": path, "full_url": path, "timeout_seconds": 5}}
            return await runtime.execute(tool, ModelToolCall(id=call_id, name=tool.key, arguments=args), execution)
        async def read():
            return await store.list_run_evidence(context.project_id, context.run_id, context.plan_id)
        async def save(name, **extra):
            directory = Path(os.environ["QA_EFFECT_EVIDENCE_DIR"]) / name
            directory.mkdir(parents=True, exist_ok=False)
            directory.joinpath(source.name).write_bytes(source.read_bytes())
            payload = {"implementation_baseline": "82de0568", "fixed_context": context.execution_json(),
                "actual_run": (await runs.get(context.run_id)).model_dump(mode="json"),
                "actual_MySQL_policy": (await base.service.get_policy(context.project_id)).model_dump(mode="json"),
                "actual_origin_evidence": await read(), "actual_HTTP": deepcopy(business[0][1]),
                "actual_created_ids": list(business[2]), "actual_remaining_orders": deepcopy(business[1]),
                "original_document": doc.model_dump(mode="json"), "original_bytes_verified_in_RustFS": True,
                "default_external_Agent_verified": False, "effect_coverage_result_connected": False,
                "consumption_completion_implemented": False, "full_P0_P5_complete": False, **extra}
            directory.joinpath("verification.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        yield values, runtime, tool, execution, context, store, action, read, save


def test_actual_old_snapshot_and_three_prompts_preserve_wire():
    path = ROOT / "docs/upgrade_2026-10-03/p4-preparation-scope-declarations/first-consumer-only-left-passed-scope-v1-finished/verification.json"
    raw = json.loads(path.read_text(encoding="utf-8"))["actual_run_detail"]["run"]["plan_snapshot"]
    assert CasePlanExecutionSnapshot.model_validate(raw).model_dump(mode="json") == raw
    assert "effect_evidence_protocol" not in CasePlanningPolicy().model_dump(mode="json")
    with pytest.raises(ValueError, match="对象证据"):
        CasePlanningPolicy(effect_evidence_protocol="case-effect-evidence-v1")
    from src.application.test_cases.planning_sources import json_text
    def load_prompt(source, model):
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "CasePlanningService")
        func = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "_system_prompt")
        func.decorator_list = []
        ns = {"PlanningDecision": model, "json_text": json_text}
        exec(compile(ast.Module(body=[func], type_ignores=[]), "<actual_prompt>", "exec"), ns)
        return ns["_system_prompt"]
    def old(path):
        return subprocess.check_output(["git", "show", "186679a8:Enterprise_AI_QA_Agent/" + path], cwd=ROOT).decode("utf-8")
    module = types.ModuleType("effect_evidence_old_schema")
    sys.modules[module.__name__] = module
    exec(compile(old("Agent_Server/src/schemas/case_planning.py"), "<old_schema>", "exec"), module.__dict__)
    before = load_prompt(old("Agent_Server/src/application/test_cases/planning_service.py"), module.PlanningDecision)
    for options in ({}, {"environment": True}, {"preparation_scopes": True}):
        assert before(**options) == CasePlanningService._system_prompt(**options)


def test_captured_formal_source_uses_complete_fixed_case_as_expected_source():
    path = ROOT / "docs/upgrade_2026-10-03/p4-preparation-scope-declarations/first-consumer-only-left-passed-scope-v1-finished/verification.json"
    raw = json.loads(path.read_text(encoding="utf-8"))["actual_run_detail"]["run"]["plan_snapshot"]
    formal = next(n for n in raw["plan"]["decision"]["items"] if n["key"] == "left")
    formal["effect_evidence"] = declaration("produced")
    formal["effect_evidence"]["checks"][0]["expected_path_tokens"] = ["期望", "code"]
    raw["plan"]["config_snapshot"]["policy"]["effect_evidence_protocol"] = "case-effect-evidence-v1"
    snapshot = CasePlanExecutionSnapshot.model_validate(raw)
    from src.application.runtime.case_effect_evidence import validate_effect_declarations
    validate_effect_declarations(snapshot.plan.decision, "case-effect-evidence-v1", snapshot.plan.evidence)
    assert next(n for n in snapshot.plan.decision.items if n.key == "left").effect_evidence.role == "produced"


@pytest.fixture
def captured_job():
    path = ROOT / "docs/upgrade_2026-10-03/p4-preparation-scope-provenance-diagnostic/replay-provenance-input/verification.json"
    job = ToolJobRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))["original_job"])
    intent = {"version": "case-effect-evidence-v1", "binding": deepcopy(job.metadata["case_action_binding"]), "max_evidence_bytes": 512000,
        "definition": {**declaration("produced"), "source_keys": ["seed"], "checks": [{
            **declaration("produced")["checks"][0], "expected": 201}]}}
    job.metadata[INTENT_KEY] = {**intent, "sha256": _digest(intent)}
    return job


@pytest.mark.parametrize("value", [False, 0, None, {"literal.key": [False, 0, None]}])
def test_actual_captured_return_preserves_literal_object_types(captured_job, value):
    output = deepcopy(captured_job.output_payload)
    output["task_result"]["response_body"]["order.id"] = value
    observation = {**captured_job.metadata["tool_output_observation"], "output_hash": _digest(output)}
    record = capture_returned_evidence(captured_job, output, observation)
    assert record["resolution"] == "captured" and record["checks_passed"]
    assert record["objects"][0]["value"] == value
    assert record["objects"][0]["value_sha256"] == _digest(value)


def test_actual_captured_missing_object_is_not_present_null(captured_job):
    output = deepcopy(captured_job.output_payload)
    del output["task_result"]["response_body"]["order.id"]
    observation = {**captured_job.metadata["tool_output_observation"], "output_hash": _digest(output)}
    record = capture_returned_evidence(captured_job, output, observation)
    assert record["resolution"] == "object_path_missing" and not record["objects"]
    with pytest.raises(ValueError, match="返回SHA"):
        capture_returned_evidence(captured_job, output, captured_job.metadata["tool_output_observation"])


@pytest.mark.asyncio
@LIVE
async def test_actual_two_objects_and_replay_keep_original_returns(tmp_path, regression_business_http):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business) as (values, runtime, tool, execution, context, store, action, read, save):
        root = business[0][0]
        first = await action("first", "seed", "seed.first", "POST", root + "/orders")
        origin = await values[0][1].get_job(first.job_id)
        replay = await runtime.execute(tool, ModelToolCall(id="replay", name=tool.key, arguments=deepcopy(origin.input_payload)), execution)
        assert replay.status == "completed" and replay.job_id != first.job_id
        await action("second", "seed", "seed.second", "POST", root + "/orders")
        before = await read()
        assert len(before) == 2 and all(r["returned_evidence"]["checks_passed"] for r in before)
        assert {r["returned_evidence"]["objects"][0]["value"] for r in before} == set(business[2])
        await values[9]["environment"].service.save_policy(context.project_id, POLICY)
        assert await read() == before
        assert not await store.list_run_evidence(str(uuid4()), context.run_id, context.plan_id)
        assert not await store.list_run_evidence(context.project_id, str(uuid4()), context.plan_id)
        assert not await store.list_run_evidence(context.project_id, context.run_id, str(uuid4()))
        for index, created in enumerate(list(business[1])):
            await action("cleanup-" + str(index), "cleanup", "cleanup." + str(index), "DELETE", root + "/orders/" + created)
        evidence = await read()
        cleanup = [r["returned_evidence"] for r in evidence if r["returned_evidence"]["role"] == "cleanup"]
        assert len(cleanup) == 2 and all(r["checks_passed"] for r in cleanup)
        assert not business[1] and len(business[2]) == 2 and len(business[0][1]) == 4
        await save("two-objects-and-original-replay", alias_job=(await values[0][1].get_job(replay.job_id)).model_dump(mode="json"))


@pytest.mark.asyncio
@LIVE
async def test_actual_owned_GET_does_not_satisfy_fixed_cleanup_evidence(tmp_path, regression_business_http):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business) as (_, _, _, _, _, _, action, read, save):
        root = business[0][0]
        await action("first", "seed", "seed.first", "POST", root + "/orders")
        await action("cleanup-read", "cleanup", "cleanup.read", "GET", root + "/orders/" + business[2][0])
        event = (await read())[-1]["returned_evidence"]
        assert event["role"] == "cleanup" and not event["checks_passed"]
        assert event["checks"][0]["status"] == "passed" and event["checks"][1]["status"] == "failed"
        assert event["checks"][1]["actual"] == "GET" and business[1]
        await save("owned-GET-cleanup-check-failed", automatic_result_not_yet_changed=True)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("fault", ["returned_then_interrupted", "return_lost", "capture_rolled_back", "commit_reply_lost"])
async def test_actual_return_observation_faults_preserve_effect_evidence(tmp_path, regression_business_http, monkeypatch, fault):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business) as (values, runtime, tool, execution, context, store, action, read, save):
        jobs = values[0][1]
        if fault == "returned_then_interrupted":
            async def fail_delivery(*args, **kwargs):
                raise RuntimeError("actual return durable; interrupted before settlement")
            monkeypatch.setattr(jobs, "finish_output_processing", fail_delivery)
        elif fault == "return_lost":
            handler = runtime._handlers[tool.key]
            async def lost(raw, ctx):
                await handler(raw, ctx)
                raise ConnectionError("actual POST created object; response lost before observation")
            monkeypatch.setitem(runtime._handlers, tool.key, lost)
        elif fault == "capture_rolled_back":
            capture = store.capture
            def rolled_back(cur, job, output, observation):
                capture(cur, job, output, observation)
                raise RuntimeError("actual effect INSERT and Job output must roll back together")
            monkeypatch.setattr(store, "capture", rolled_back)
        else:
            observe = jobs._store._observe_job_output_sync
            def reply_lost(*args, **kwargs):
                observe(*args, **kwargs)
                raise ConnectionError("actual observation committed; acknowledgement lost")
            monkeypatch.setattr(jobs._store, "_observe_job_output_sync", reply_lost)
        if fault in {"return_lost", "capture_rolled_back"}:
            with pytest.raises(CaseActionClaimUnavailable):
                await action("first", "seed", "seed.first", "POST", business[0][0] + "/orders")
        else:
            await action("first", "seed", "seed.first", "POST", business[0][0] + "/orders")
        events = await read()
        assert len(events) == 1 and len(business[2]) == 1 and len(business[1]) == 1
        known = fault in {"returned_then_interrupted", "commit_reply_lost"}
        assert events[0]["returned_evidence_available"] is known
        if known:
            assert events[0]["returned_evidence"]["objects"][0]["value"] == business[2][0]
        else:
            assert events[0]["evidence_state"] == "awaiting_reliable_return"
            assert events[0]["origin_job"]["status"] == "running" and not events[0]["origin_job"]["output_payload"]
        origin = await jobs.get_job(events[0]["origin_job"]["id"])
        alias = await runtime.execute(tool, ModelToolCall(id="recovery", name=tool.key,
            arguments=deepcopy(origin.input_payload)), execution)
        assert len(business[0][1]) == 1
        assert alias.status == ("completed" if fault == "commit_reply_lost" else "partial" if known else "denied")
        assert (await read())[0]["returned_evidence"] == events[0]["returned_evidence"]
        await save("observation-" + fault, actual_evidence_and_output_transaction_atomic=True,
            original_known_return=known, alias=alias.model_dump(mode="json"))


@pytest.mark.asyncio
@LIVE
async def test_actual_event_corruption_is_detected(tmp_path, regression_business_http):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business) as (_, _, _, _, _, store, action, read, save):
        await action("first", "seed", "seed.first", "POST", business[0][0] + "/orders")
        row = (await read())[0]
        with postgres_connect(store._settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store.table} SET record=jsonb_set(record,'{{objects,0,value}}','\"changed\"'::jsonb) WHERE origin_job_id=%s",
                (row["origin_job"]["id"],))
        with pytest.raises(ValueError, match="固定对象证据"):
            await read()
        with postgres_connect(store._settings) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store.table} SET record=%s::jsonb WHERE origin_job_id=%s",
                (json.dumps(row["returned_evidence"], ensure_ascii=False), row["origin_job"]["id"]))
        assert (await read())[0] == row
        await save("disposable-fixture-corruption-detected", original_event_captured_before_corruption=row)


@pytest.mark.asyncio
@LIVE
async def test_actual_small_evidence_budget_is_explicit_without_blocking_the_return(tmp_path, regression_business_http):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business, max_bytes=1) as (_, _, _, _, context, _, action, read, save):
        result = await action("first", "seed", "seed.first", "POST", business[0][0] + "/orders")
        row = (await read())[0]
        event = row["returned_evidence"]
        assert result.status == "completed" and len(business[2]) == 1
        assert context.effect_evidence_max_bytes == 1
        assert event["resolution"] == "evidence_limit_exceeded" and not event["objects"] and not event["checks_passed"]
        assert event["required_evidence_bytes"] > 1 and event["max_evidence_bytes"] == 1
        assert event["observed_object_count"] == 1
        await save("explicit-budget-unavailable", no_fake_zero_objects=True, actual_tool_return_preserved=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_formal_dependency_records_its_object_from_full_fixed_JSON(tmp_path, regression_business_http):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business, formal_source=True) as (_, _, _, _, context, _, action, read, save):
        assert context.target_key == "seed" and context.steps[0].item.kind == "dependency"
        assert len(context.steps[0].content_json["steps"]) == 8
        assert context.steps[0].content_json["期望"]["code"] == 201
        assert not hasattr(context.steps[0].item, "instructions")
        await action("formal-create", "seed", "seed.first", "POST", business[0][0] + "/orders")
        row = (await read())[0]
        event = row["returned_evidence"]
        assert event["binding"]["phase"] == "case" and event["role"] == "produced"
        assert event["checks_passed"] and event["objects"][0]["value"] == business[2][0]
        assert event["checks"][0]["expected_path_tokens"] == ["期望", "code"]
        await save("formal-dependency-full-case", formal_case_instructions_not_duplicated=True)
