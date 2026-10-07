"""Formal mixed effects use actual native HTTP and immutable original evidence."""
import base64
from copy import deepcopy
from html import unescape
import json
import os
from pathlib import Path

import pytest

from test_agent_case_execution import adapter_environment, regression_business_http
from test_case_operations import POLICY
from test_case_planning import active_case, item, finish
from test_case_shared_effect_research import SharedPlanningModel
from test_case_cleanup_coverage import receipt_for
from src.application.runtime.case_action_binding import ARGUMENT, CaseActionClaimUnavailable, _digest
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.test_runs.preparation_scopes import normalize_preparation_scopes
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import CasePlanningPolicy, CasePlanRunRequest, CasePlanningRequest, PlanningDecision, PlanningDecisionV2
from src.schemas.case_execution import AgentCaseExecutionContext
from src.schemas.run_management import RunClaimRequest, RunItemLeaseRequest
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.tool_job import ToolJobRecord
from src.application.runtime.case_effect_evidence import capture_returned_evidence
from src.schemas.api_docs import ApiDocVersionUploadRequest

ROOT = Path(__file__).resolve().parents[2]
LIVE = pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_EFFECT_RELATIONS") != "1", reason="actual isolated MySQL/PG/RustFS/native HTTP")


def policy(**extra):
    return CasePlanningPolicy.model_validate({**POLICY.model_dump(mode="json"),
        "preparation_scope_protocol": "preparation-scope-v2", "effect_evidence_protocol": "case-effect-evidence-v2",
        "effect_coverage_protocol": "case-effect-coverage-v2", "planning_stage_protocol": "case-planning-staged-evidence-v2", **extra})


def declaration(role, source_key, formal):
    prefix = ["证据契约"] if formal else []
    return {"declaration_id": "created" if role == "produced" else "deleted", "role": role,
        "sources": [] if role == "produced" else [{"item_key": source_key, "declaration_id": "created"}],
        "object_path_tokens": ["task_result", "response_body", "order.id"], "collection": False,
        "checks": [{"output_path_tokens": ["task_result", "response_status"],
            "expected_path_tokens": prefix + ["created_status" if role == "produced" else "deleted_status"], "operator": "equals"}],
        "select_when": [{"input_path_tokens": ["task", "method"],
            "expected_path_tokens": prefix + ["created_method" if role == "produced" else "deleted_method"]}]}


def document_declaration(role, source_key, source):
    entry = declaration(role, source_key, True)
    created = role == "produced"
    excerpt = next(line for line in source["payload"]["content"].splitlines() if line.startswith("POST")) if created else "DELETE同一路径返回200及刚删除的订单JSON。"
    ref = {"evidence_id": source["id"], "content_hash": source["content_hash"], "payload_path_tokens": ["content"], "excerpt": excerpt}
    entry["checks"][0].pop("expected_path_tokens")
    entry["checks"][0]["expected_source"] = {**ref, "literal": "201" if created else "200", "encoding": "json"}
    entry["select_when"][0].pop("expected_path_tokens")
    entry["select_when"][0]["expected_source"] = {**ref, "literal": "POST" if created else "DELETE", "encoding": "text"}
    return entry


def test_original_default_failure_stays_illegal_but_v2_represents_the_intended_formal_cleanup():
    actual = json.loads((ROOT/"docs/upgrade_2026-10-03/p4-staged-planning-default-first/PG-planning.json").read_bytes())
    original = json.loads(actual["checkpoint"]["validation_feedback"]["candidate_response"])
    with pytest.raises(ValueError, match="引用自身"):
        normalize_preparation_scopes(PlanningDecision.model_validate(original), "preparation-scope-v1")
    # An explicit new plan expresses the captured business intent; the saved
    # failed candidate and formal content are never rewritten.
    revised = deepcopy(original)
    for entry in revised["items"]:
        entry["effect_schema"] = 2
    revised["items"][0].pop("cleanup_sources")
    revised["items"][1]["cleanup_sources"] = [revised["items"][0]["key"]]
    decision = PlanningDecisionV2.model_validate(revised)
    normalize_preparation_scopes(decision, "preparation-scope-v2")
    assert decision.items[1].depends_on == [decision.items[0].key]
    assert all(entry.key not in entry.depends_on for entry in decision.items)
    with pytest.raises(ValueError, match="一致"):
        CasePlanningPolicy.model_validate({**policy().model_dump(mode="json"), "effect_evidence_protocol": "case-effect-evidence-v1"})


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("scenario", ["internal-all", "internal-partial", "external-all", "unknown-create", "unknown-delete", "coverage-budget", "document-internal-all", "document-internal-partial", "prefetch-document-external-all"])
async def test_actual_formal_mixed_effects_and_all_original_objects(tmp_path, regression_business_http, monkeypatch, scenario):
    scenario_key = scenario
    prefetch = scenario.startswith("prefetch-")
    scenario = scenario.removeprefix("prefetch-")
    document_expected = scenario.startswith("document-")
    scenario = scenario.removeprefix("document-")
    business = regression_business_http
    async with adapter_environment(tmp_path, business[0], live=True, start_item=False, planning_policy=POLICY) as values:
        env, _, _, _, runs, _, adapter, _, _, services = values
        base = services["environment"]
        project = await base.projects.get(env[0].project_id)
        selected_policy = policy(effect_coverage_max_bytes=1) if scenario == "coverage-budget" else policy()
        if document_expected:
            selected_policy = policy(effect_expected_source_protocol="case-effect-document-expected-v1")
        if prefetch:
            selected_policy = policy(effect_expected_source_protocol="case-effect-document-expected-v1",
                operation_prefetch_protocol="case-planning-declared-operation-prefetch-v1")
        config = await base.service.save_policy(project.id, selected_policy)
        old = ROOT/"docs/upgrade_2026-10-03/p4-shared-effect-wire/first-consumer-only-left-passed-finished/shared-left.json"
        content = json.loads(old.read_bytes())
        content["objective"] = "完整正式用例的创建查询清理"
        content["steps"][0]["url"] = business[0][0] + "/orders/{id}"
        content["证据契约"] = {"created_method": "POST", "deleted_method": "DELETE", "created_status": 201, "deleted_status": 200}
        if document_expected:
            content = json.loads((ROOT/"docs/upgrade_2026-10-03/p4-formal-effects-default-first/full-case.json").read_bytes())
            content["operations"]["query"] = business[0][0]+"/orders/{id}"
        source_file = tmp_path/"formal-mixed-case.json"
        source_file.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        if document_expected:
            source_file = tmp_path/"requirements.md"
            original_doc = (ROOT/"docs/upgrade_2026-10-03/p4-formal-effects-default-first/requirements.md").read_text(encoding="utf-8")
            source_file.write_text(original_doc.replace("http://127.0.0.1:51735", business[0][0]), encoding="utf-8")
        document = await base.docs.upload_document(project_id=project.id, filename=source_file.name,
            content_base64=base64.b64encode(source_file.read_bytes()).decode())
        assert (await base.objects.read_object_uri(document.storage_uri))["content"] == source_file.read_bytes()
        case = await active_case(base, project, "formal-mixed", content)
        external = scenario == "external-all"
        producer_key = "seed" if external else "target"

        def respond(context):
            if context["planning_stage"] == "logic":
                if not any(e["kind"] == "document" for e in context["evidence"]):
                    return {"action": "query", "summary": "读取实际上传原件",
                        "queries": [{"kind": "document", "source_id": document.id}]}
                target = item(context, "formal-mixed", key="target", effect_schema=3 if document_expected else 2, cleanup_sources=[producer_key],
                    depends_on=["seed"] if external else [])
                if not external:
                    return finish(target)
                if prefetch:
                    assert "返回201" in next(e["payload"]["content"] for e in context["evidence"] if e["kind"] == "document")
                instructions = {"tool": "api-test-runner", "operation": "execute_task", "url": business[0][0]+"/orders", "expected": 201} if prefetch else {
                    **content["证据契约"], "url": business[0][0]+"/orders", "expected": 201}
                return finish({"key": "seed", "kind": "prepare", "effect_schema": 3 if document_expected else 2,
                    "reason": "原文提供创建依据", "evidence_ids": target["evidence_ids"],
                    "instructions": instructions}, target)
            if not any(e["kind"] == "tool" for e in context["evidence"]):
                return {"action": "query", "summary": "读取实际原生工具契约",
                    "queries": [{"kind": "tool", "source_id": "api-test-runner", "operation": "execute_task"}]}
            contract = next(e for e in context["evidence"] if e["kind"] == "tool")
            bindings = [{"item_key": "target", "evidence_ids": [contract["id"]],
                "effect_declarations": ([declaration("produced", producer_key, True)] if not external else [])
                    + [declaration("cleanup", producer_key, True)]}]
            if document_expected:
                source = next(e for e in context["evidence"] if e["kind"] == "document")
                bindings[0]["evidence_ids"].append(source["id"])
                bindings[0]["effect_declarations"] = [document_declaration(entry["role"], producer_key, source)
                    for entry in bindings[0]["effect_declarations"]]
            if external:
                bindings.append({"item_key": "seed", "evidence_ids": [contract["id"]],
                    "effect_declarations": [declaration("produced", producer_key, False)]})
                if document_expected:
                    bindings[-1]["evidence_ids"].append(source["id"])
                    bindings[-1]["effect_declarations"] = [document_declaration("produced", producer_key, source)]
            return {"action": "finish", "summary": "固定逻辑的多效果证据", "bindings": bindings}

        base.service._models = SharedPlanningModel(respond, respond, respond, respond)
        plan = await base.service.create(project.id, CasePlanningRequest(objective="按完整正式JSON测试产生与清理",
            candidate_case_ids=[case.case.id]))
        assert plan.status == "planned", plan.summary
        assert plan.prompt_version == ("case-planning-declared-operation-prefetch-v7" if prefetch else "case-planning-document-expected-v6" if document_expected else "case-planning-staged-evidence-v5")
        assert len(plan.rounds) == (3 if prefetch else 4)
        later_document = None
        if document_expected:
            # A new upload/config must not replace already-frozen expectations.
            later_document = await base.docs.upload_version(document.id, ApiDocVersionUploadRequest(
                filename="changed.md", content_base64=base64.b64encode(b"# changed\nDELETE now returns 409").decode(),
                project_id=project.id, expected_parent_version_id=document.current_version_id, request_id="change-after-plan"))
        detail = await services["creator"].create(project.id, plan.id,
            CasePlanRunRequest(creation_key="mixed-effects", mode_key="api_testing"))
        claim = (await runs.claim(detail.run.id, RunClaimRequest(worker_id="mixed-effects", limit=1))).claims[0]
        running = await runs.start_item(claim.item.id, RunItemLeaseRequest(lease_token=claim.lease_token))
        attempt = await runs.get_latest_attempt(running.id)
        context, checkpoint, attempt = await adapter()._load(run=await runs.get_record(detail.run.id), item=running,
            version=await base.cases.get_version(running.case_version_id), attempt_id=attempt.id)
        await adapter()._save(running, attempt, checkpoint.model_copy(update={"phase": "running"}))
        assert AgentCaseExecutionContext.model_validate(context.execution_json()).execution_json() == context.execution_json()
        assert len(next(step for step in context.steps if step.item.kind == "target").content_json["steps"]) == 8
        if document_expected:
            assert "证据契约" not in next(step for step in context.steps if step.item.kind == "target").content_json
            assert next(e for e in context.evidence if e.kind == "document").version == document.current_version_id
        execution = ToolExecutionContext(session_id=checkpoint.session_id, turn_id=checkpoint.turn_id,
            trace_id="mixed-effects", user_message=context.objective, normalized_input=context.objective,
            context_bundle={**context.runtime_context(), "case_execution_managed": True, "attempt_id": checkpoint.execution_attempt_id})
        runtime, tool = services["tool_runtime"], ToolRegistry().get("api-test-runner")

        async def action(call_id, item_key, method, path, operation_key=None):
            operation_key = operation_key or call_id
            arguments = {ARGUMENT: {"item_key": item_key, "operation_key": operation_key}, "worker_action": "execute_task",
                "task": {"task_id": operation_key, "method": method, "path": path, "full_url": path, "timeout_seconds": 5}}
            return await runtime.execute(tool, ModelToolCall(id=call_id, name=tool.key, arguments=arguments), execution)

        denied_retries = []
        records = [await action("create-first", producer_key, "POST", business[0][0]+"/orders")]
        first = business[2][0]
        records.append(await action("create-second", producer_key, "POST", business[0][0]+"/orders"))
        second = business[2][1]
        handler = runtime._handlers[tool.key]

        async def lost(arguments, tool_context):
            await handler(arguments, tool_context)
            raise ConnectionError("actual side effect completed; reliable return unavailable")

        if scenario == "unknown-create":
            monkeypatch.setitem(runtime._handlers, tool.key, lost)
            with pytest.raises(CaseActionClaimUnavailable):
                await action("unknown-create", producer_key, "POST", business[0][0]+"/orders")
            before = len(business[0][1])
            for call_id in ("unknown-create", "unknown-create-retry"):
                retry = await action(call_id, producer_key, "POST", business[0][0]+"/orders", operation_key="unknown-create")
                assert retry.status == "denied"
                saved_retry = await env[1].get_job(retry.job_id)
                receipt = saved_retry.metadata["case_operation"]
                assert receipt["decision"] == "outcome_unknown" and receipt["action_sent"] is False
                origin = await env[1].get_job(receipt["origin_job_id"])
                assert origin.status.value == "running" and saved_retry.started_at is None
                denied_retries.append(saved_retry.model_dump(mode="json"))
            assert len(business[0][1]) == before
            monkeypatch.setitem(runtime._handlers, tool.key, handler)
        records.append(await action("query-first", "target", "GET", business[0][0]+"/orders/"+first))
        if scenario == "unknown-delete":
            monkeypatch.setitem(runtime._handlers, tool.key, lost)
            with pytest.raises(CaseActionClaimUnavailable):
                await action("unknown-delete", "target", "DELETE", business[0][0]+"/orders/"+first)
            before = len(business[0][1])
            for call_id in ("unknown-delete", "unknown-delete-retry"):
                retry = await action(call_id, "target", "DELETE", business[0][0]+"/orders/"+first, operation_key="unknown-delete")
                assert retry.status == "denied"
                saved_retry = await env[1].get_job(retry.job_id)
                receipt = saved_retry.metadata["case_operation"]
                assert receipt["decision"] == "outcome_unknown" and receipt["action_sent"] is False
                origin = await env[1].get_job(receipt["origin_job_id"])
                assert origin.status.value == "running" and saved_retry.started_at is None
                denied_retries.append(saved_retry.model_dump(mode="json"))
            assert len(business[0][1]) == before
            monkeypatch.setitem(runtime._handlers, tool.key, handler)
        else:
            records.append(await action("delete-first", "target", "DELETE", business[0][0]+"/orders/"+first))
        if scenario != "internal-partial":
            records.append(await action("delete-second", "target", "DELETE", business[0][0]+"/orders/"+second))
        inventory = await env[1].list_case_effect_evidence(project_id=context.project_id, run_id=context.run_id, plan_id=context.plan_id)
        events = [row["returned_evidence"] for row in inventory if row["returned_evidence"] is not None]
        assert len([event for event in events if any(entry["role"] == "produced" and entry["resolution"] == "captured"
            for entry in event["declarations"])]) == 2
        assert all(event["sha256"] == _digest({k:v for k,v in event.items() if k != "sha256"}) for event in events)
        if document_expected:
            proofs = [check["expected_source_proof"] for event in events for entry in event["declarations"] for check in entry["checks"]]
            assert proofs and all(proof["version"] == document.current_version_id for proof in proofs)
        # Current editable policy changes cannot alter the frozen v2 execution.
        await base.service.save_policy(project.id, POLICY)
        assessment = AgentCaseAssessmentService(env[1])
        request = await assessment.build_assessment_request(context=context, tool_records=records,
            session_id=execution.session_id, turn_id=execution.turn_id, attempt_id=checkpoint.execution_attempt_id)
        model_data = json.loads(unescape(request.messages[0]["content"].split("\n",1)[1].split("\n</untrusted_content>",1)[0]))
        assert "effect_inventory" in model_data
        before = len(business[0][1])
        receipt = receipt_for(context, {"seed": "create-first", "target": "query-first"})
        outcome = await assessment.assess(context=context, receipt=receipt, tool_records=records,
            session_id=execution.session_id, turn_id=execution.turn_id, attempt_id=checkpoint.execution_attempt_id,
            model_key="controlled-evidence-receipt", lease_token=claim.lease_token)
        result = await runs.complete_item(running.id, outcome.completion)
        assert len(business[0][1]) == before
        expected = "passed" if scenario in {"internal-all", "external-all"} else "blocked"
        coverage = result.actual["effect_cleanup_coverage"]["cleanups"]["target"]
        assert result.status == coverage["status"] == expected
        assert coverage["known_object_count"] == 2
        if scenario == "internal-partial":
            assert coverage["covered_object_count"] == 1 and len(business[1]) == 1
        if scenario in {"internal-all", "external-all", "unknown-delete", "coverage-budget"}:
            assert not business[1]
        if scenario.startswith("unknown-"):
            assert coverage["unknown"]
            assert any(row["returned_evidence"] is None for row in inventory)
        assert not (await runs.claim(detail.run.id, RunClaimRequest(worker_id="terminal", limit=1))).claims
        artifact_dir = Path(os.environ["QA_EFFECT_RELATIONS_DIR"])/scenario_key
        artifact_dir.mkdir(parents=True, exist_ok=False)
        artifact_dir.joinpath(source_file.name).write_bytes(source_file.read_bytes())
        artifact_dir.joinpath("verification.json").write_text(json.dumps({"baseline": "786b5b88" if document_expected else "d81b25e8", "scenario": scenario_key,
            "actual_config": config.model_dump(mode="json"), "frozen_plan": plan.model_dump(mode="json"),
            "fixed_context": context.execution_json(), "actual_inventory": inventory,
            "actual_origin_evidence": await env[1]._store.effect_evidence.list_run_evidence(context.project_id, context.run_id, context.plan_id),
            "actual_formal_result": result.model_dump(mode="json"), "actual_HTTP": business[0][1],
            "actual_denied_retries": denied_retries, "actual_created_ids": business[2], "actual_remaining_orders": business[1], "original": document.model_dump(mode="json"),
            "RustFS_original_verified": True, "default_autonomous_model_verified": False,
            "later_document": later_document.model_dump(mode="json") if later_document else None,
            "consumption_completion_verified": False, "full_P0_P5_complete": False}, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("fault", ["false-vs-zero", "missing-selection", "role-conflict", "evidence-budget"])
def test_actual_original_return_retains_typed_selection_conflicts_and_limits(fault):
    saved = json.loads((ROOT/"docs/upgrade_2026-10-03/p4-formal-effects-live-final/internal-all/verification.json").read_bytes())
    job = ToolJobRecord.model_validate(saved["actual_origin_evidence"][0]["origin_job"])
    intent = job.metadata["case_effect_evidence_intent"]
    produced, cleanup = intent["definitions"]
    if fault == "false-vs-zero":
        job.input_payload["task"]["typed"] = False
        produced["select_when"] = [{"input_path_tokens": ["task", "typed"], "expected_path_tokens": [], "expected": 0}]
    elif fault == "missing-selection":
        produced["select_when"] = [{"input_path_tokens": ["not-present"], "expected_path_tokens": [], "expected": "POST"}]
    elif fault == "role-conflict":
        cleanup["select_when"] = deepcopy(produced["select_when"])
        cleanup["checks"] = deepcopy(produced["checks"])
    else:
        intent["max_evidence_bytes"] = 1
    intent["sha256"] = _digest({k:v for k,v in intent.items() if k != "sha256"})
    event = capture_returned_evidence(job, job.output_payload, job.metadata["tool_output_observation"])
    first = event["declarations"][0]
    expected = {"false-vs-zero": "not_applicable", "missing-selection": "selection_unknown",
        "role-conflict": "role_conflict", "evidence-budget": "evidence_limit_exceeded"}[fault]
    assert first["resolution"] == expected and first["checks_passed"] is False
    assert event["sha256"] == _digest({k:v for k,v in event.items() if k != "sha256"})
    if fault in {"role-conflict", "evidence-budget"}:
        assert first["observed_object_count"] == 1
    if fault == "role-conflict":
        assert first["objects"] and first["conflicting_declaration_ids"] == ["deleted"]


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("document_expected", [False, True])
async def test_actual_pg_v2_logic_ack_loss_preserves_formal_relation_and_original_seed(tmp_path, monkeypatch, document_expected):
    from test_case_planning_tasks import tasks_environment, delivery, expire
    from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
    from src.schemas.case_planning_task import CasePlanningTaskRequest
    from src.schemas.case_planning_stages import logical_decision_hash
    from src.schemas.project import ProjectCreateRequest
    from src.runtime.planning_task_handler import PlanningTaskHandler
    from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
    from src.infrastructure.postgres_runtime import postgres_connect

    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="formal-v2-recovery", name="正式关系恢复"))
        original_file = ROOT/"docs/upgrade_2026-10-03/p4-formal-effects-live-final/internal-all/formal-mixed-case.json"
        content = json.loads(original_file.read_bytes())
        if document_expected:
            content = json.loads((ROOT/"docs/upgrade_2026-10-03/p4-formal-effects-default-first/full-case.json").read_bytes())
            original_file = ROOT/"docs/upgrade_2026-10-03/p4-formal-effects-default-first/requirements.md"
        case = await active_case(env, project, "durable-formal", content)
        document = await env.docs.upload_document(project_id=project.id, filename=original_file.name,
            content_base64=base64.b64encode(original_file.read_bytes()).decode())
        assert (await env.objects.read_object_uri(document.storage_uri))["content"] == original_file.read_bytes()
        await env.service.save_policy(project.id, policy(effect_expected_source_protocol="case-effect-document-expected-v1") if document_expected else policy())
        task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key="formal-v2",
            objective=content["objective"], candidate_case_ids=[case.case.id]))

        def respond(context):
            if context["planning_stage"] == "logic":
                if not any(e["kind"] == "document" for e in context["evidence"]):
                    return {"action": "query", "summary": "读取原件", "queries": [{"kind": "document", "source_id": document.id}]}
                return finish(item(context, "durable-formal", key="target", effect_schema=3 if document_expected else 2, cleanup_sources=["target"]))
            if not any(e["kind"] == "tool" for e in context["evidence"]):
                return {"action": "query", "summary": "原接口依据",
                    "queries": [{"kind": "tool", "source_id": "api-test-runner", "operation": "execute_task"}]}
            tool = next(e for e in context["evidence"] if e["kind"] == "tool")
            definitions = [declaration("produced", "target", True), declaration("cleanup", "target", True)]
            refs = [tool["id"]]
            if document_expected:
                source = next(e for e in context["evidence"] if e["kind"] == "document")
                refs.append(source["id"])
                definitions = [document_declaration(role, "target", source) for role in ("produced", "cleanup")]
            return {"action": "finish", "summary": "固定关系的声明", "bindings": [{"item_key": "target", "evidence_ids": refs,
                "effect_declarations": definitions}]}

        env.model.replies = [respond]*4
        original = env.jobs.save_checkpoint

        async def lost_ack(claim, checkpoint, *, message):
            saved = await original(claim, checkpoint, message=message)
            if checkpoint.stages.stage == "effects" and checkpoint.next_round == 2 and checkpoint.phase == "ready":
                changed = checkpoint.model_copy(deep=True)
                changed.stages.logical_decision.items[0].cleanup_sources = None
                changed.stages.logical_hash = logical_decision_hash(changed.stages.logical_decision)
                with pytest.raises(ValueError, match="固定|逻辑"):
                    await original(claim, changed, message="不能重写已冻结正式清理来源")
                raise RuntimeError("actual v2 stage committed; acknowledgement lost")
            return saved

        monkeypatch.setattr(env.jobs, "save_checkpoint", lost_ack)
        with pytest.raises(PlanningCheckpointUnavailable):
            await PlanningTaskHandler(env.service, env.jobs, worker_id="before-loss")(delivery(task))
        before = await env.jobs.get(project.id, task.id)
        assert before.plan_id is None and len(env.model.requests) == 2
        if document_expected:
            await env.docs.upload_version(document.id, ApiDocVersionUploadRequest(filename="changed.md",
                content_base64=base64.b64encode(b"DELETE now returns 409").decode(), project_id=project.id,
                expected_parent_version_id=document.current_version_id, request_id="change-before-recovery"))
        await env.service.save_policy(project.id, POLICY)
        monkeypatch.setattr(env.jobs, "save_checkpoint", original)

        def forbidden(*args, **kwargs):
            raise AssertionError("Recovery must use original v2 seed and tool registration")

        monkeypatch.setattr("src.application.test_cases.planning_service.capture_tool_contracts", forbidden)
        monkeypatch.setattr("src.application.test_cases.planning_service.capture_tool_operations", forbidden)
        expire(env, task)
        restored = PostgresPlanningTaskStore(env.settings)
        await PlanningTaskHandler(env.service, restored, worker_id="after-loss")(delivery(task))
        terminal = await restored.get(project.id, task.id)
        assert terminal.status == "completed" and terminal.attempt_no == 2 and terminal.deadline_at == before.deadline_at
        assert len(env.model.requests) == 4
        plan = await env.service.get(project.id, terminal.plan_id)
        assert plan.prompt_version == ("case-planning-document-expected-v6" if document_expected else "case-planning-staged-evidence-v5") and plan.status == "planned"
        assert plan.decision.items[0].cleanup_sources == ["target"] and plan.decision.items[0].depends_on == []
        await PlanningTaskHandler(env.service, restored, worker_id="terminal")(delivery(task))
        assert len(env.model.requests) == 4
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT seed,checkpoint,status,attempt_no,deadline_at FROM {restored._table} WHERE id=%s", (task.id,))
            actual = dict(cur.fetchone())
        actual["deadline_at"] = actual["deadline_at"].isoformat()
        artifact = Path(os.environ["QA_EFFECT_RELATIONS_DIR"])/("document-durable-recovery" if document_expected else "v2-durable-recovery")
        artifact.mkdir(parents=True, exist_ok=False)
        artifact.joinpath(original_file.name).write_bytes(original_file.read_bytes())
        artifact.joinpath("verification.json").write_text(json.dumps({"actual_PG": actual,
            "actual_current_MySQL": (await env.service.get_policy(project.id)).model_dump(mode="json"),
            "actual_model_requests": [r.model_dump(mode="json") for r in env.model.requests],
            "original_RustFS_verified": True, "business_HTTP": 0, "default_model": False}, ensure_ascii=False, indent=2), encoding="utf-8")
