"""Replay genuine failures with full frozen files and actual native operations."""
from copy import deepcopy
from html import unescape
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Lock
from uuid import uuid4
import json

import pytest

from test_case_effect_evidence import original_effect_runtime, captured_job, declaration, ROOT
from test_case_operations import LIVE
from src.application.runtime.case_action_binding import ARGUMENT, _digest, CaseActionClaimUnavailable
from src.application.runtime.case_effect_evidence import INTENT_KEY, capture_returned_evidence
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.test_runs.case_cleanup_coverage import cleanup_coverage
from src.schemas.case_execution import AgentCaseExecutionContext
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.run_management import RunClaimRequest, RunItemLeaseRequest
from src.schemas.tool_runtime import ModelToolCall
from test_agent_case_execution import regression_business_http


def receipt_for(context, calls):
    return {"schema_version": 1, "steps": [{"item_key": step.item.key, "status": "passed",
        "summary": "核对本项固定预期", "checks": [{"name": "完整固定状态预期", "operator": "equals",
            "expected_path_tokens": ["expected"] if step.item.kind == "prepare" else ["期望", "code"],
            "observations": [{"call_id": calls[step.item.key], "output_path_tokens": ["task_result", "response_status"]}]}],
        "outputs": {"body": {"call_id": calls[step.item.key], "output_path_tokens": ["task_result", "response_body"]}}}
        for step in context.steps]}


@pytest.fixture
def saved_original_scope():
    p = ROOT / "docs/upgrade_2026-10-03/p4-effect-evidence-final/two-objects-and-original-replay/verification.json"
    saved = json.loads(p.read_text(encoding="utf-8"))
    context = AgentCaseExecutionContext.model_validate({**saved["fixed_context"],
        "effect_coverage_protocol": "case-effect-coverage-v1", "effect_coverage_max_bytes": 1024000,
        "effect_owner_item_ids": saved["actual_run"]["run"]["plan_snapshot"]["run_item_ids"]})
    inventory = [{"origin_job_id": r["origin_job"]["id"], "binding": r["origin_job"]["metadata"]["case_action_binding"],
        "returned_evidence": r["returned_evidence"], "evidence_state": r["evidence_state"]}
        for r in saved["actual_origin_evidence"]]
    return saved, context, inventory


def test_saved_two_original_objects_are_not_reduced_to_the_last_receipt_output(saved_original_scope):
    _, context, inventory = saved_original_scope
    good = cleanup_coverage(context, inventory)["cleanups"]["cleanup"]
    assert good["status"] == "passed" and good["covered_object_count"] == 2
    partial = cleanup_coverage(context, inventory[:-1])["cleanups"]["cleanup"]
    assert partial["status"] == "blocked" and len(partial["uncovered"]) == 1
    assert partial["known_object_count"] == 2


def test_old_wire_is_unchanged_and_new_coverage_cannot_guess_owner_mapping(saved_original_scope):
    saved, context, _ = saved_original_scope
    assert AgentCaseExecutionContext.model_validate(saved["fixed_context"]).execution_json() == saved["fixed_context"]
    raw = context.execution_json()
    raw.pop("effect_owner_item_ids")
    with pytest.raises(ValueError, match="拥有者"):
        AgentCaseExecutionContext.model_validate(raw)


@pytest.mark.parametrize("index", [0, -1])
def test_source_and_cleanup_use_real_run_item_ownership(saved_original_scope, index):
    _, context, inventory = saved_original_scope
    inventory[index]["binding"]["run_item_id"] = "foreign-original-item"
    with pytest.raises(ValueError, match="拥有者"):
        cleanup_coverage(context, inventory)


def test_cleanup_before_creation_does_not_cover_later_source(saved_original_scope):
    _, context, inventory = saved_original_scope
    for row in inventory:
        row["returned_evidence"]["observation"]["observed_at"] = (
            "2026-10-07T08:00:00+08:00" if row["binding"]["item_key"] == "cleanup" else "2026-10-07T00:00:01")
    coverage = cleanup_coverage(context, inventory)["cleanups"]["cleanup"]
    assert coverage["status"] == "blocked" and coverage["covered_object_count"] == 0
    assert coverage["known_object_count"] == 2


@pytest.mark.parametrize("actual,expected,resolution", [(False, 0, "not_applicable"), (0, 0, "captured")])
def test_literal_operation_selection_keeps_typed_false_and_zero(captured_job, actual, expected, resolution):
    intent = captured_job.metadata[INTENT_KEY]
    intent["definition"]["select_when"] = [{"input_path_tokens": ["typed_value"], "expected_path_tokens": [], "expected": expected}]
    intent["sha256"] = _digest({k:v for k,v in intent.items() if k != "sha256"})
    captured_job.input_payload["typed_value"] = actual
    event = capture_returned_evidence(captured_job, captured_job.output_payload,
        captured_job.metadata["tool_output_observation"])
    assert event["resolution"] == resolution


def test_missing_selection_field_retains_unknown_scope(captured_job):
    intent = captured_job.metadata[INTENT_KEY]
    intent["definition"]["select_when"] = [{"input_path_tokens": ["not_present"], "expected_path_tokens": [], "expected": "POST"}]
    intent["sha256"] = _digest({k:v for k,v in intent.items() if k != "sha256"})
    event = capture_returned_evidence(captured_job, captured_job.output_payload,
        captured_job.metadata["tool_output_observation"])
    assert event["resolution"] == "selection_unknown" and not event["checks_passed"]


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("scenario", ["one-of-two", "all-two", "target-read", "cleanup-read", "source-query", "unknown-create", "coverage-budget"])
async def test_actual_full_files_all_origins_and_formal_cleanup_result(tmp_path, regression_business_http, monkeypatch, scenario):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business, coverage=True, select_operations=True,
        coverage_max_bytes=1 if scenario == "coverage-budget" else None) as values:
        services, runtime, tool, execution, context, store, action, read, save = values
        root = business[0][0]
        records = []
        if scenario == "source-query":
            records.append(await action("source-query", "seed", "seed.query", "GET", root + "/orders/not-created"))
        records.append(await action("seed", "seed", "seed.first", "POST", root + "/orders"))
        if scenario in {"one-of-two", "all-two"}:
            records.append(await action("seed-second", "seed", "seed.second", "POST", root + "/orders"))
        if scenario == "unknown-create":
            handler = runtime._handlers[tool.key]
            async def lost(args, ctx):
                await handler(args, ctx)
                raise ConnectionError("second actual object created; return lost")
            monkeypatch.setitem(runtime._handlers, tool.key, lost)
            with pytest.raises(CaseActionClaimUnavailable):
                await action("lost-create", "seed", "seed.unknown", "POST", root + "/orders")
            monkeypatch.setitem(runtime._handlers, tool.key, handler)
        first = business[2][0]
        records.append(await action("target", "target", "target.read", "GET", root + "/orders/" + first))
        cleanup_call = "target" if scenario == "target-read" else "cleanup"
        if scenario != "target-read":
            records.append(await action("cleanup", "cleanup", "cleanup.first",
                "GET" if scenario == "cleanup-read" else "DELETE", root + "/orders/" + first))
        if scenario == "all-two":
            records.append(await action("cleanup-second", "cleanup", "cleanup.second", "DELETE", root + "/orders/" + business[2][1]))
        jobs, runs = services[0][1], services[4]
        assessment = AgentCaseAssessmentService(jobs)
        before_HTTP = len(business[0][1])
        request = await assessment.build_assessment_request(context=context, tool_records=records,
            session_id=execution.session_id, turn_id=execution.turn_id, attempt_id=execution.context_bundle["attempt_id"])
        model_data = json.loads(unescape(request.messages[0]["content"].split("\n", 1)[1].split("\n</untrusted_content>", 1)[0]))
        assert len(model_data["execution_items"][1]["expected_source"]["steps"]) == 8
        if scenario != "coverage-budget":
            assert len(model_data["effect_inventory"]) == len(await read())
        else:
            assert model_data["effect_inventory"]["available"] is False
        # Freeze recovery: the current editable config may change; the context cannot.
        policy = await services[-1]["environment"].service.get_policy(context.project_id)
        old = policy.policy.model_dump(mode="json")
        old.pop("effect_coverage_protocol"); old.pop("effect_coverage_max_bytes")
        await services[-1]["environment"].service.save_policy(context.project_id, CasePlanningPolicy.model_validate(old))
        item = await runs.get_item(context.run_item_id)
        outcome = await assessment.assess(context=context, receipt=receipt_for(context,
            {"seed": "seed", "target": "target", "cleanup": cleanup_call}), tool_records=records,
            session_id=execution.session_id, turn_id=execution.turn_id,
            attempt_id=execution.context_bundle["attempt_id"], model_key="actual-controlled-coverage", lease_token=item.lease_token)
        result = await runs.complete_item(item.id, outcome.completion)
        coverage = result.actual["effect_cleanup_coverage"]["cleanups"]["cleanup"]
        expected = "passed" if scenario in {"all-two", "source-query"} else "failed" if scenario == "cleanup-read" else "blocked"
        assert result.status == coverage["status"] == expected
        assert result.actual["case_status"] == "passed"
        assert len(business[0][1]) == before_HTTP
        if scenario == "one-of-two":
            assert coverage["known_object_count"] == 2 and coverage["covered_object_count"] == 1
            assert len(business[1]) == 1
        elif scenario == "all-two":
            assert coverage["covered_object_count"] == 2 and not business[1]
            assert len(result.evidence_refs) >= len(records)
        elif scenario == "source-query":
            assert len(coverage["excluded_operations"]) == 1 and coverage["known_object_count"] == 1
            assert not business[1]
        elif scenario in {"target-read", "cleanup-read"}:
            assert len(business[1]) == 1
        elif scenario == "unknown-create":
            assert coverage["unknown"] and len(business[1]) == 1
            assert any(r["returned_evidence"] is None and r["origin_job"]["status"] == "running" for r in await read())
        else:
            assert coverage["reason"] == "inventory_limit_exceeded" and coverage["known_object_count"] == 1
        assert not (await runs.claim(context.run_id, RunClaimRequest(worker_id="terminal",limit=1))).claims
        await save("coverage-" + scenario, effect_coverage_result_connected=True,
            actual_formal_result=result.model_dump(mode="json"), actual_assessment_request=request.model_dump(mode="json"),
            actual_model_input=model_data, actual_native_readonly_no_new_HTTP=True)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("clear_all", [False, True])
async def test_formal_full_source_is_counted_across_actual_run_items(tmp_path, regression_business_http, clear_all):
    business = regression_business_http
    async with original_effect_runtime(tmp_path, business, coverage=True,
        select_operations=True, formal_source=True) as values:
        services, runtime, tool, execution, context, _, action, read, save = values
        root = business[0][0]
        first = await action("source-first", "seed", "seed.first", "POST", root + "/orders")
        second = await action("source-second", "seed", "seed.second", "POST", root + "/orders")
        sent = len(business[0][1])
        alias = await action("source-alias", "seed", "seed.first", "POST", root + "/orders")
        assert len(business[0][1]) == sent and len(await read()) == 2
        assert len(context.steps[0].content_json["steps"]) == 8
        jobs, runs = services[0][1], services[4]
        base = services[-1]["environment"]
        assessor = AgentCaseAssessmentService(jobs)
        item = await runs.get_item(context.run_item_id)
        result = await assessor.assess(context=context, receipt=receipt_for(context, {"seed": "source-first"}),
            tool_records=[first, second, alias], session_id=execution.session_id, turn_id=execution.turn_id,
            attempt_id=execution.context_bundle["attempt_id"], model_key="controlled-formal-source", lease_token=item.lease_token)
        source_result = await runs.complete_item(item.id, result.completion)
        assert source_result.status == "passed"
        assert not source_result.actual["effect_cleanup_coverage"]["cleanups"]
        claim = (await runs.claim(context.run_id, RunClaimRequest(worker_id="consumer", limit=1))).claims[0]
        running = await runs.start_item(claim.item.id, RunItemLeaseRequest(lease_token=claim.lease_token))
        attempt = await runs.get_latest_attempt(claim.item.id)
        adapter = services[6]()
        consumer, checkpoint, attempt = await adapter._load(run=await runs.get_record(context.run_id), item=running,
            version=await base.cases.get_version(claim.item.case_version_id), attempt_id=attempt.id)
        await adapter._save(running, attempt, checkpoint.model_copy(update={"phase": "running"}))
        assert consumer.run_item_id != context.run_item_id
        assert consumer.effect_owner_item_ids == context.effect_owner_item_ids
        assert consumer.steps[0].inputs["id"].value == business[2][0]
        assert consumer.steps[0].inputs["id"].result_id == source_result.id
        consumer_execution = ToolExecutionContext(session_id=checkpoint.session_id, turn_id=checkpoint.turn_id,
            trace_id="actual-formal-coverage", user_message=consumer.objective, normalized_input=consumer.objective,
            context_bundle={**consumer.runtime_context(), "case_execution_managed": True, "attempt_id": checkpoint.execution_attempt_id})

        async def owned(call_id, key, method, path):
            return await runtime.execute(tool, ModelToolCall(id=call_id, name=tool.key, arguments={
                ARGUMENT: {"item_key": key, "operation_key": call_id}, "worker_action": "execute_task",
                "task": {"task_id": call_id, "method": method, "path": path, "full_url": path, "timeout_seconds": 5}}), consumer_execution)

        records = [await owned("target", "target", "GET", root + "/orders/" + business[2][0]),
            await owned("cleanup", "cleanup", "DELETE", root + "/orders/" + business[2][0])]
        if clear_all:
            records.append(await owned("cleanup-second", "cleanup", "DELETE", root + "/orders/" + business[2][1]))
        sent = len(business[0][1])
        outcome = await assessor.assess(context=consumer,
            receipt=receipt_for(consumer, {"target": "target", "cleanup": "cleanup"}), tool_records=records,
            session_id=checkpoint.session_id, turn_id=checkpoint.turn_id, attempt_id=checkpoint.execution_attempt_id,
            model_key="controlled-formal-consumer", lease_token=running.lease_token)
        final = await runs.complete_item(running.id, outcome.completion)
        coverage = final.actual["effect_cleanup_coverage"]["cleanups"]["cleanup"]
        assert coverage["known_object_count"] == 2
        assert coverage["covered_object_count"] == (2 if clear_all else 1)
        assert final.status == ("passed" if clear_all else "blocked")
        assert len(business[0][1]) == sent
        assert {obj["origin_job_id"] for obj in coverage["covered"] + coverage["uncovered"]} == {first.job_id, second.job_id}
        assert next(r for r in (await runs.get(context.run_id)).results if r.id == source_result.id) == source_result
        assert not (await runs.claim(context.run_id, RunClaimRequest(worker_id="terminal",limit=1))).claims
        await save("formal-all" if clear_all else "formal-partial", actual_consumer_context=consumer.execution_json(),
            actual_source_result=source_result.model_dump(mode="json"), actual_formal_result=final.model_dump(mode="json"),
            effect_coverage_result_connected=True, actual_alias_no_new_HTTP=True, actual_native_readonly_no_new_HTTP=True)


@pytest.fixture
def collection_business_http():
    requests, orders, created = [], {}, []
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            with lock:
                ids = ["collection-" + uuid4().hex for _ in range(2)]
                created.extend(ids)
                orders.update({identity: {"order.id": identity, "flag": False, "count": 0, "nullable": None} for identity in ids})
                requests.append(("POST", self.path))
            self.reply(201, {"order.id": ids[0], "object_ids": ids})

        def do_GET(self):
            with lock:
                identity = self.path.removeprefix("/orders/")
                exists = identity in orders
                requests.append(("GET", self.path))
            self.reply(200 if exists else 404, {"order.id": identity, "object_ids": [identity]} if exists else {})

        def do_DELETE(self):
            with lock:
                ids = list(orders) if self.path == "/orders/_all" else [self.path.removeprefix("/orders/")]
                removed = [identity for identity in ids if orders.pop(identity, None) is not None]
                requests.append(("DELETE", self.path))
            self.reply(200 if removed else 404, {"object_ids": removed})

        def reply(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (f"http://127.0.0.1:{server.server_port}", requests, None), orders, created
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
        assert not thread.is_alive()


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("multiple_sources,clear_all", [(False, False), (False, True), (True, True)])
async def test_actual_arrays_and_multiple_sources_preserve_every_origin(tmp_path, collection_business_http, multiple_sources, clear_all):
    business = collection_business_http

    def collection_declaration(role):
        value = declaration(role)
        value.update(collection=True, object_path_tokens=["task_result", "response_body", "object_ids"])
        return value

    def add_source(plan):
        if multiple_sources:
            source = deepcopy(plan["items"][0])
            source["key"] = "another-source"
            plan["items"].insert(1, source)
            next(item for item in plan["items"] if item["key"] == "target")["depends_on"].append(source["key"])
            next(item for item in plan["items"] if item["key"] == "cleanup")["cleanup_sources"].append(source["key"])
        return plan

    async with original_effect_runtime(tmp_path, business, coverage=True, select_operations=True,
        declaration_factory=collection_declaration, plan_transform=add_source) as values:
        services, _, _, execution, context, _, action, read, save = values
        root = business[0][0]
        records = [await action("seed", "seed", "seed.collection", "POST", root + "/orders")]
        if multiple_sources:
            records.append(await action("another-source", "another-source", "second.collection", "POST", root + "/orders"))
        records += [await action("target", "target", "target.read", "GET", root + "/orders/" + business[2][0]),
            await action("cleanup", "cleanup", "cleanup.collection", "DELETE", root + "/orders/" + ("_all" if clear_all else business[2][0]))]
        sent = len(business[0][1])
        calls = {step.item.key: step.item.key for step in context.steps}
        item = await services[4].get_item(context.run_item_id)
        outcome = await AgentCaseAssessmentService(services[0][1]).assess(context=context,
            receipt=receipt_for(context, calls), tool_records=records, session_id=execution.session_id,
            turn_id=execution.turn_id, attempt_id=execution.context_bundle["attempt_id"],
            model_key="controlled-array-coverage", lease_token=item.lease_token)
        result = await services[4].complete_item(item.id, outcome.completion)
        coverage = result.actual["effect_cleanup_coverage"]["cleanups"]["cleanup"]
        assert coverage["known_object_count"] == (4 if multiple_sources else 2)
        assert coverage["covered_object_count"] == (coverage["known_object_count"] if clear_all else 1)
        assert result.status == ("passed" if clear_all else "blocked")
        all_objects = coverage["covered"] + coverage["uncovered"]
        assert {obj["value"] for obj in all_objects} == set(business[2])
        assert {obj["output_path_tokens"][-1] for obj in all_objects} == {"0", "1"}
        assert {obj["source_key"] for obj in all_objects} == ({"seed", "another-source"} if multiple_sources else {"seed"})
        assert len(business[0][1]) == sent
        assert len(business[1]) == (0 if clear_all else 1)
        assert len(await read()) == len(records)
        await save(f"array-{multiple_sources}-{clear_all}", effect_coverage_result_connected=True,
            actual_formal_result=result.model_dump(mode="json"), actual_native_readonly_no_new_HTTP=True)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_PLANNING_TASKS") != "1", reason="actual durable MySQL/PG/RustFS planning")
async def test_durable_tool_query_uses_original_seed_after_registry_and_policy_change(tmp_path, monkeypatch):
    from test_case_effect_evidence import NEW_POLICY
    from test_case_planning import active_case, item, finish
    from test_case_planning_tasks import tasks_environment, delivery
    from src.runtime.planning_task_handler import PlanningTaskHandler
    from src.schemas.case_planning_task import CasePlanningTaskRequest
    from src.schemas.project import ProjectCreateRequest
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="fixed-contract", name="固定工具接口"))
        content = {"objective": "完整固定工具依据", "steps": [{"order": n, "action": f"固定步骤{n}"} for n in range(8)], "typed": [False, 0, None]}
        await active_case(env, project, "contract", content)
        await env.service.save_policy(project.id, NEW_POLICY.model_copy(update={"effect_coverage_protocol": "case-effect-coverage-v1"}))
        request = CasePlanningTaskRequest(creation_key="frozen-contract", objective="读取实际工具接口")
        task = await env.tasks.submit(project.id, request)
        def no_capture():
            raise AssertionError("恢复不能重新读取当前工具注册表")
        monkeypatch.setattr("src.application.test_cases.planning_service.capture_tool_contracts", no_capture)
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
        env.model.replies = [{"action": "query", "summary": "读固定工具", "queries": [{"kind": "tool", "source_id": "api-test-runner"}]},
            lambda c: finish(item(c, "contract"))]
        handler = PlanningTaskHandler(env.service, env.jobs, worker_id="original-contract")
        await handler(delivery(task))
        saved = await env.jobs.get(project.id, task.id)
        assert saved.status == "completed" and saved.round_count == 2
        plan = await env.service.get(project.id, saved.plan_id)
        contract = next(e for e in plan.evidence if e.kind == "tool")
        assert "response_status" in contract.payload["output_variants"][0]["schema"]["properties"]
        assert len(next(e for e in plan.evidence if e.kind == "case").payload["content"]["steps"]) == 8
        assert plan.config_snapshot.policy.max_rounds == 5
        assert plan.config_snapshot.policy.effect_coverage_max_bytes == 1024000
        calls = len(env.model.requests)
        assert await env.tasks.submit(project.id, request) == saved
        await handler(delivery(task))
        assert len(env.model.requests) == calls
        from src.infrastructure.postgres_runtime import postgres_connect
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT seed,checkpoint FROM {env.jobs._table} WHERE id=%s", (task.id,))
            original = dict(cur.fetchone())
        assert original["seed"]["tool_contracts"]["api-test-runner"] == contract.payload
        directory = Path(os.environ["QA_EFFECT_EVIDENCE_DIR"])/"durable-tool-contract"
        directory.mkdir(parents=True, exist_ok=False)
        directory.joinpath("verification.json").write_text(json.dumps({"actual_seed_and_checkpoint": original,
            "actual_plan": plan.model_dump(mode="json"), "actual_terminal_task": saved.model_dump(mode="json"),
            "actual_controlled_requests": [r.model_dump(mode="json") for r in env.model.requests],
            "current_MySQL_policy": (await env.service.get_policy(project.id)).model_dump(mode="json"),
            "registry_recapture_prohibited": True, "terminal_no_new_calls": True,
            "default_external_Agent_verified": False}, ensure_ascii=False, indent=2), encoding="utf-8")
