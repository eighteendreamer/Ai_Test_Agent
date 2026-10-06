"""Shared data lifetime research through the original Agent/API and real stores."""
from copy import deepcopy
from html import unescape
import base64
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from test_agent_case_execution import adapter_environment, regression_business_http
from test_agent_case_session import ControlledAgent
from test_case_operations import LIVE, POLICY
from test_case_planning import SequenceModel, active_case, finish, item
from test_case_planning_tasks import SubmissionModel
from src.api.routes.run_management import router
from src.application.test_runs.execution_service import TestRunExecutionService as ExecutionService
from src.application.test_runs.agent_regression_service import AgentRegressionService
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.schemas.model_config import ModelInvocationResult
from src.schemas.run_management import RunClaimRequest, RegressionRunCreateRequest
from src.schemas.tool_runtime import ModelToolCall


class SharedPlanningModel(SubmissionModel):
    async def invoke(self, key, request, *, expected_parameters=None):
        if expected_parameters is not None:
            from src.schemas.case_planning_task import planning_model_parameters
            assert planning_model_parameters(self.get_model_config(key)) == expected_parameters
        return await SequenceModel.invoke(self, key, request)


class SharedAgent(ControlledAgent):
    def __init__(self, early_cleanup=False):
        super().__init__()
        self.early_cleanup = early_cleanup

    async def invoke(self, key, request):
        self.requests.append(request.model_copy(deep=True))
        section = next(s for s in request.system_prompt_sections if s.key == "frozen_case_execution")
        frozen = json.loads(unescape(section.content.split("\n", 1)[1].split("\n</untrusted_content>", 1)[0]))
        assert "LAST-SHARED-STEP" in request.system_prompt
        assert "DO-NOT-INJECT-TEMPLATE" not in request.system_prompt
        previous = [json.loads(m["content"]) for m in request.messages if m.get("role") == "tool"]
        steps = frozen["steps"]
        execution_steps = sorted(steps, key=lambda s: s["phase"] != "after") if self.early_cleanup and frozen["target_key"] == "right" else steps
        if len(previous) < len(steps):
            step = execution_steps[len(previous)]
            source = step["content_json"]["steps"][0] if step["phase"] == "case" else step["item"]["instructions"]
            url = source["url"]
            if "{id}" in url:
                bound = step["inputs"]["id"]
                if bound["resolved"]:
                    actual_id = bound["value"]
                else:
                    # The original message contract keeps provider ID in the
                    # message header, not in its JSON result content. Seed is
                    # the first owned tool operation in this fixed test plan.
                    actual_id = previous[0]["output"]["task_result"]["response_body"]["order.id"]
                url = url.replace("{id}", actual_id)
            item_key = step["item"]["key"]
            call = ModelToolCall(id=item_key, name="api-test-runner", arguments={
                "_case_action": {"item_key": item_key, "operation_key": item_key + ".request"},
                "worker_action": "execute_task", "task": {"task_id": item_key,
                    "method": source["method"], "path": url, "full_url": url, "timeout_seconds": 5}})
            return ModelInvocationResult(text="Use the next complete fixed step and actual data reference.",
                tool_calls=[call], response_summary={"mode": "ok", "finish_reason": "tool_calls"})
        entries = []
        for step in steps:
            item_key = step["item"]["key"]
            expected = ["期望", "code"] if step["phase"] == "case" else ["expected"]
            ref = {"call_id": item_key, "output_path_tokens": ["task_result", "response_status"]}
            entries.append({"item_key": item_key, "status": "passed", "summary": item_key,
                "checks": [{"name": "actual status against fixed source", "operator": "equals",
                    "expected_path_tokens": expected, "observations": [ref]}],
                "outputs": {"body": {"call_id": item_key,
                    "output_path_tokens": ["task_result", "response_body"]}}})
        return ModelInvocationResult(text=json.dumps({"schema_version": 1, "steps": entries}, ensure_ascii=False),
            response_summary={"mode": "ok", "finish_reason": "stop"})


async def save_shared(name, values, business, run_id, source_files, **extra):
    env, _, reader, _, runs, agent, _, _, _, services = values
    base = services["environment"]
    jobs = [j for j in await env[1].list_jobs() if j.metadata.get("run_id") == run_id]
    sessions = sorted({j.session_id for j in jobs})
    detail = await runs.get(run_id)
    payload = {"implementation_baseline": "4cde9a7d", "actual_run_detail": detail.model_dump(mode="json"),
        "actual_MySQL_policy": (await base.service.get_policy(detail.run.project_id)).model_dump(mode="json"),
        "actual_jobs": [j.model_dump(mode="json") for j in jobs],
        "actual_approvals": [a.model_dump(mode="json") for session in sessions for a in await reader.list_approvals(session)],
        "actual_HTTP": deepcopy(business[0][1]), "actual_created_ids": list(business[2]),
        "actual_remaining_orders": deepcopy(business[1]),
        "actual_model_requests": [r.model_dump(mode="json") for r in agent.requests],
        "original_graph_and_API": True, "controlled_model": True,
        "default_external_Agent_verified": False, "effect_lifecycle_implemented": False, **extra}
    payload["preparation_scope_implemented"] = detail.run.plan_snapshot.plan.config_snapshot.policy.preparation_scope_protocol is not None
    directory = Path(os.environ["QA_SHARED_EFFECT_EVIDENCE_DIR"]) / name
    directory.mkdir(parents=True, exist_ok=False)
    for source in source_files:
        directory.joinpath(source.name).write_bytes(source.read_bytes())
    directory.joinpath("verification.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("wait_all,left_fails,observe_wait,scope_enabled,early_cleanup", [(True, False, False, False, False),
    (True, True, False, False, False), (False, False, False, False, False), (False, False, True, False, False),
    (True, False, False, True, False), (True, True, False, True, False), (False, False, False, True, False),
    (False, False, False, True, True)])
async def test_actual_shared_object_survives_until_all_declared_consumers_finish(
    tmp_path, regression_business_http, wait_all, left_fails, observe_wait, scope_enabled, early_cleanup,
):
    business = regression_business_http
    agent = SharedAgent(early_cleanup)
    policy = POLICY.model_copy(update={"preparation_scope_protocol": "preparation-scope-v1"}) if scope_enabled else POLICY
    async with adapter_environment(tmp_path, business[0], live=True, agent=agent,
        start_item=False, planning_policy=policy) as values:
        env, _, reader, _, runs, _, adapter, _, _, services = values
        base = services["environment"]
        project = await base.projects.get(env[0].project_id)
        sources = []
        for key, expected in [("shared-left", 201 if left_fails else 200), ("shared-right", 200)]:
            content = {"objective": key, "期望": {"code": expected},
                "steps": [{"method": "GET", "url": business[0][0] + "/orders/{id}"}] +
                    [{"action": "read complete shared-data basis", "order": i} for i in range(2, 8)] +
                    [{"action": "LAST-SHARED-STEP", "business": {"flag": False, "zero": 0, "nullable": None}}]}
            source = tmp_path / (key + ".json")
            source.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            doc = await base.docs.upload_document(project_id=project.id, filename=source.name,
                content_base64=base64.b64encode(source.read_bytes()).decode())
            assert (await base.objects.read_object_uri(doc.storage_uri))["content"] == source.read_bytes()
            await active_case(base, project, key, json.loads(source.read_text(encoding="utf-8")))
            sources.append(source)

        def plan(c):
            left = item(c, "shared-left", key="left", depends_on=["seed"],
                input_bindings={"id": {"source_item": "seed", "output_path_tokens": ["body", "order.id"]}})
            right = item(c, "shared-right", key="right", depends_on=["seed"],
                input_bindings=deepcopy(left["input_bindings"]))
            refs = left["evidence_ids"] + right["evidence_ids"]
            cleanup = {"key": "cleanup", "kind": "prepare", "reason": "两个消费者使用结束后删除本次对象",
                "evidence_ids": refs, "depends_on": ["left", "right"] if wait_all else ["left"],
                "input_bindings": deepcopy(left["input_bindings"]),
                "instructions": {"method": "DELETE", "url": business[0][0] + "/orders/{id}", "expected": 200}}
            if scope_enabled:
                cleanup["cleanup_sources"] = ["seed"]
            return finish({"key": "seed", "kind": "prepare", "reason": "两个正式消费者共享本次真实数据",
                "evidence_ids": refs, "instructions": {"method": "POST", "url": business[0][0] + "/orders", "expected": 201}},
                left, right, cleanup)

        base.service._models = SharedPlanningModel(plan)
        frozen = await base.service.create(project.id, CasePlanningRequest(objective="实际共享准备及消费结束验证"))
        assert frozen.status == "planned", frozen.summary
        detail = await services["creator"].create(project.id, frozen.id,
            CasePlanRunRequest(creation_key="shared-research", mode_key="api_testing"))
        run_id = detail.run.id
        if scope_enabled:
            assert (await base.service.get_policy(project.id)).policy.preparation_scope_protocol == "preparation-scope-v1"
            seed = await base.service.prepare_task(project.id, CasePlanningRequest(objective="固定共享准备任务输入"))
            assert seed.plan.prompt_version == "case-planning-preparation-scopes-v1" and "cleanup_sources" in seed.system_prompt
            scope = detail.run.plan_snapshot.preparation_scopes["seed"]
            assert scope.source_owner_key == "left" and scope.consumer_keys == ["left", "right"]
            assert scope.cleanup_keys == ["cleanup"] and detail.run.plan_snapshot.owners["cleanup"] == "right"
            assert {"seed", "left", "right"}.issubset(next(n for n in frozen.decision.items if n.key == "cleanup").depends_on)
            declaration = frozen.rounds[-1]["preparation_scope_declaration"]["cleanup"]
            assert declaration == {"cleanup_sources": ["seed"],
                "depends_on": ["left", "right"] if wait_all else ["left"]}
        executor = ExecutionService(run_service=runs, test_case_service=base.cases,
            adapter=None, session_store=reader, tool_job_service=env[1])
        executor.set_agent_execution_adapter(adapter())
        app = FastAPI()
        app.state.test_run_execution_service, app.state.test_run_service = executor, runs
        app.include_router(router, prefix="/api/v1")
        name = ("all-consumers" if wait_all else "first-consumer-only") + ("-left-failed" if left_fails else "-left-passed")
        if observe_wait:
            name += "-cleanup-pending"
        if scope_enabled:
            name += "-scope-v1"
        if early_cleanup:
            name += "-right-cleanup-first"
        if scope_enabled:
            await save_shared(name + "-before-execution", values, business, run_id, sources,
                initial_MySQL_scope_policy_verified=True, planning_task_seed=seed.model_dump(mode="json"))

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://shared") as client:
            async def execute(claim):
                prefix = "/api/v1/run-items/" + claim.item.id
                response = await client.post(prefix + "/execute", json={"lease_token": claim.lease_token})
                assert response.status_code == 200, response.text
                data = response.json()
                for _ in range(4):
                    if data["status"] != "waiting_approval":
                        return data
                    job = await env[1].get_job(data["tool_job_id"])
                    if observe_wait and job.metadata["case_action_binding"]["phase"] == "after":
                        return data
                    response = await client.post(prefix + "/approval",
                        json={"approval_id": data["approval_id"], "decision": "approved"})
                    assert response.status_code == 200, response.text
                    data = response.json()
                raise AssertionError("Shared controlled execution exceeded expected approval count")

            first = await runs.claim(run_id, RunClaimRequest(worker_id="left", limit=10, lease_seconds=60))
            assert [c.item.plan_item_key for c in first.claims] == ["left"]
            left_result = await execute(first.claims[0])
            await save_shared(name + "-left-result", values, business, run_id, sources,
                wait_all=wait_all, left_fails=left_fails, left_result=left_result)
            if observe_wait:
                assert left_result["status"] == "waiting_approval"
                later = await runs.claim(run_id, RunClaimRequest(worker_id="consumer-before-cleanup", limit=10))
                assert not later.claims
                pending = await runs.get(run_id)
                assert {i.plan_item_key: i.status for i in pending.items} == {"left": "waiting_approval", "right": "queued"}
                assert len(business[1]) == 1 and [method for method, path in business[0][1]] == ["POST", "GET"]
                await save_shared(name + "-dependency-observation", values, business, run_id, sources,
                    cleanup_pending_on_original_approval=True, second_consumer_not_claimable=True,
                    hypothetical_extra_wait_would_cycle=True, new_cleanup_wait_guard_implemented=False)
                return
            assert left_result["status"] == ("failed" if left_fails else "passed")
            assert left_result["actual"]["preparations"]["seed"]["status"] == "passed"
            assert len(business[2]) == 1
            remaining_after_left = len(business[1])
            if scope_enabled:
                # Current policy and the next enabled case version cannot
                # replace this run's original frozen scope or complete case.
                await base.service.save_policy(project.id, POLICY)
                node = next(n for n in frozen.decision.items if n.key == "right")
                changed = json.loads(sources[1].read_text(encoding="utf-8"))
                changed["期望"]["code"] = 201
                version = await base.cases.create_version(node.case_id, VersionRequest(content_json=changed,
                    source_refs=[{"source_type": "manual", "source_id": "new-shared-version"}],
                    model_key="manual", prompt_version="manual", skill_versions={"manual": "v1"}))
                await base.cases.submit_review(node.case_id)
                await base.cases.activate(node.case_id)
                replay = await services["creator"].create(project.id, frozen.id,
                    CasePlanRunRequest(creation_key="shared-research", mode_key="api_testing"))
                assert replay.run.plan_snapshot == detail.run.plan_snapshot
                assert next(i for i in replay.items if i.plan_item_key == "right").case_version_id != version.id
            await save_shared(name + "-after-left", values, business, run_id, sources,
                wait_all=wait_all, left_fails=left_fails, left_result=left_result,
                expected_remaining_until_right_finished=1)
            second = await runs.claim(run_id, RunClaimRequest(worker_id="right", limit=10, lease_seconds=60))
            assert [c.item.plan_item_key for c in second.claims] == ["right"]
            right_result = await execute(second.claims[0])
            saved = await save_shared(name + "-finished", values, business, run_id, sources,
                wait_all=wait_all, left_fails=left_fails, left_result=left_result, right_result=right_result,
                remaining_after_left=remaining_after_left, right_cleanup_before_own_target=early_cleanup,
                frozen_scope_does_not_prove_consumption_finished=early_cleanup)
            assert saved["actual_run_detail"]["run"]["status"] == "completed"
            assert not (await runs.claim(run_id, RunClaimRequest(worker_id="terminal", limit=10))).claims
            assert len(business[2]) == 1 and not business[1]
            assert remaining_after_left == 1, "Cleanup removed shared object before its second consumer ran"
            assert right_result["status"] == ("failed" if early_cleanup else "passed")
            if early_cleanup:
                assert [method for method, path in business[0][1]] == ["POST", "GET", "DELETE", "GET"]
            if scope_enabled and left_fails:
                parent = await runs.get(run_id)
                runs.set_agent_regression_service(AgentRegressionService(plan_store=base.store,
                    plan_run_service=services["creator"], planning_service=base.service, run_store=env[7]))
                regression = await runs.create_regression(run_id,
                    RegressionRunCreateRequest(result_ids=[left_result["id"]]))
                assert regression.run.plan_snapshot.preparation_scopes["seed"].consumer_keys == ["left"]
                assert regression.run.plan_snapshot.owners["cleanup"] == "left"
                claim = (await runs.claim(regression.run.id,
                    RunClaimRequest(worker_id="fresh-regression", limit=10))).claims
                assert [c.item.plan_item_key for c in claim] == ["left"]
                result = await execute(claim[0])
                assert result["status"] == "failed" and result["regression_source_result_id"] == left_result["id"]
                assert len(business[2]) == 2 and not business[1]
                assert await runs.get(run_id) == parent
                await save_shared(name + "-new-regression", values, business, regression.run.id, sources,
                    parent_run_and_result_unchanged=True, parent_run_id=run_id,
                    fresh_actual_order_id=business[2][-1], original_actual_order_id=business[2][0])
