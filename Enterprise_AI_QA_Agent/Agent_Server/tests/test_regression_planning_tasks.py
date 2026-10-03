"""Real failed runs must retain fixed regression inputs beyond an HTTP request."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from test_case_plan_run_creation import planned
from test_case_planning import finish, item, active_case
from test_case_planning_tasks import SubmissionModel, delivery, expire
from test_run_item_dependencies import finish as complete
from src.api.routes.case_planning_tasks import router as task_router
from src.api.routes.case_plans import router as plan_router
from src.api.routes.run_management import router as run_router
from src.application.test_cases.planning_task_service import CasePlanningTaskService
from src.application.test_runs.agent_regression_service import AgentRegressionService
from src.application.test_runs.plan_mapping import json_digest
from src.application.test_runs.run_service import TestRunService as RunService
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.modes import ModeRegistry
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.schemas.case_planning import CasePlanRunRequest, CasePlanningPolicy, PlanningDecision, CasePlanningRequest
from src.schemas.run_management import RegressionPlanningTaskRequest
from src.schemas.case_planning_task import CasePlanningTaskRecord


live = pytest.mark.skipif(os.getenv("RUN_LIVE_REGRESSION_PLANNING") != "1",
    reason="requires isolated actual MySQL/PostgreSQL/RustFS")


@pytest.mark.asyncio
async def test_fixed_regression_scope_is_not_an_initial_recall_candidate_list(tmp_path):
    async with planned(tmp_path) as (env, store, suites, creator, plan, now, _):
        project = await env.projects.get(plan.project_id)
        content = next(source.payload["content"] for source in plan.evidence if source.kind == "case")
        bundles = [await active_case(env, project, f"fixed-{index}", content) for index in range(51)]
        plan = plan.model_copy(deep=True)
        plan.id = str(uuid4())
        plan.config_snapshot.policy.max_items = 200
        plan.evidence = [await env.service._sources.case(project.id, bundle.case.id) for bundle in bundles]
        plan.decision = PlanningDecision(action="finish", summary="固定的 51 项目标", items=[{
            "key": f"target-{index}", "kind": "target", "case_id": bundle.case.id,
            "case_version_id": bundle.version.id, "reason": "原运行目标", "evidence_ids": [f"case:{bundle.version.id}"]
        } for index, bundle in enumerate(bundles)])
        plan = await env.store.save(plan)
        parent = await creator.create(project.id, plan.id, CasePlanRunRequest(creation_key="scope-parent", mode_key="api_testing"))
        for claim in await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=100, lease_seconds=60, now=now):
            await complete(store, claim, now, {"case_output": {"verified": False}}, status="failed")
        original = await store.get_run(parent.run.id)
        captured = {}
        async def submit(project_id, request, source, **kwargs):
            captured.update(kwargs, source=source)
            return kwargs["prepared_plan"]
        regression = AgentRegressionService(plan_store=env.store, plan_run_service=creator,
            planning_service=env.service, run_store=store, planning_task_service=SimpleNamespace(submit_regression=submit))
        runs = RunService(store=store, project_service=env.projects, suite_service=None, test_case_service=env.cases)
        request = RegressionPlanningTaskRequest(creation_key="scope-regression")
        parent_record, candidates, session_id = await runs._prepare_regression(parent.run.id, request)
        result = await regression.submit(parent_record, candidates, request, session_id=session_id)
        assert len(captured["source"].required_case_versions) == 51
        assert len(result.decision.items) == 51
        CasePlanningRequest.model_validate(captured["planning_request"].model_dump())
        assert await store.get_run(parent.run.id) == original


@asynccontextmanager
async def regression_environment(tmp_path):
    async with planned(tmp_path, True) as (env, store, suites, creator, plan, now, outbox):
        parent = await creator.create(plan.project_id, plan.id,
            CasePlanRunRequest(creation_key="regression-parent", mode_key="api_testing"))
        for claim in await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=20, lease_seconds=60, now=now):
            await complete(store, claim, now, {"case_output": {"body": {"order.id": "parent-only"}}})
        left = (await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=20, lease_seconds=60, now=now))[0]
        await complete(store, left, now, {"preparations": {"seed": {"status": "passed", "output": {"id": "old-seed"}}}})
        right = (await store.claim_items(run_id=parent.run.id, worker_id="parent", limit=20, lease_seconds=60, now=now))[0]
        failure = await complete(store, right, now, {"case_output": {"verified": False}}, status="failed")
        original = await store.get_run(parent.run.id)
        document_file = tmp_path / "regression-requirements.md"
        document_file.write_text("# 新版本准备依据\nPOST /fixtures 创建本次数据，响应 body.new_id。\n查询后检查末尾验证点，不复用父运行数据。\n", encoding="utf-8")
        document = await env.docs.upload_document(filename=document_file.name,
            content_base64=base64.b64encode(document_file.read_bytes()).decode(), project_id=plan.project_id)
        version = await env.cases.create_version(right[0].case_id, VersionRequest(
            content_json={"objective": "新版本查询", "steps": [{"action": "读取 new_id"}, {"action": "末尾验证点😀"}]},
            source_refs=[{"source_type": "api_doc", "source_id": document.id}], model_key="manual",
            prompt_version="manual", skill_versions={"manual": "v1"}))
        await env.cases.submit_review(right[0].case_id)
        await env.cases.activate(right[0].case_id)
        table = "up_gr_pt_" + uuid4().hex[:12]
        env.settings.database.postgres_case_planning_task_table = table
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
            assert cur.fetchone()["existing"] is None
        jobs = PostgresPlanningTaskStore(env.settings, lease_seconds=2)
        tasks = CasePlanningTaskService(store=jobs, planning_service=env.service,
            project_service=env.projects, dispatch_stream="isolated:case_planning")
        await tasks.initialize()
        runs = RunService(store=store, project_service=env.projects, suite_service=None, test_case_service=env.cases)
        regression = AgentRegressionService(plan_store=env.store, plan_run_service=creator,
            planning_service=env.service, run_store=store, planning_task_service=tasks)
        runs.set_agent_regression_service(regression)
        creator._modes = ModeRegistry()
        env.service._models = SubmissionModel()
        app = FastAPI()
        app.state.test_run_service, app.state.case_planning_task_service = runs, tasks
        app.state.case_planning_service, app.state.case_plan_run_service = env.service, creator
        for router in (run_router, task_router, plan_router): app.include_router(router, prefix="/api/v1")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://regression/api/v1") as client:
                env.jobs, env.tasks, env.client, env.runs, env.run_store, env.creator = jobs, tasks, client, runs, store, creator
                env.parent, env.original, env.failure, env.version = parent, original, failure, version
                env.document, env.document_bytes = document, document_file.read_bytes()
                yield env
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f'DROP TABLE "{table}"')


@pytest.mark.asyncio
@live
async def test_regression_submission_returns_queryable_task_without_model_or_run(tmp_path):
    async with regression_environment(tmp_path) as env:
        path = f"/runs/{env.parent.run.id}/regression-planning-tasks"
        payload = {"creation_key": "durable-regression", "result_ids": [env.failure.id],
            "version_overrides": {env.failure.case_id: env.version.id}}
        (tmp_path / "regression-request.json").write_text(json.dumps(payload), encoding="utf-8")
        response = await env.client.post(path, json=payload)
        assert response.status_code == 202, response.text
        task = response.json()
        assert task["status"] == "queued" and task["plan_id"] is None
        assert env.service._models.requests == []
        queried = await env.client.get(f"/projects/{env.parent.run.project_id}/case-planning-tasks/{task['id']}")
        assert queried.json() == task
        claim = await env.jobs.claim(task["project_id"], task["id"], worker_id="inspect")
        frozen = claim.seed.plan.regression
        assert frozen.required_case_versions == payload["version_overrides"]
        assert frozen.source_results_by_case == {env.failure.case_id: env.failure.id}
        assert frozen.parent_run_id == env.parent.run.id
        assert frozen.context["failures"][0]["result_id"] == env.failure.id
        assert claim.checkpoint.plan.regression == frozen
        changed = claim.checkpoint.model_copy(deep=True)
        changed.plan.regression.mode_key = "ui_automation"
        with pytest.raises(ValueError, match="固定输入"):
            await env.jobs.save_checkpoint(claim, changed, message="attempt to replace frozen provenance")
        legacy_plan = env.parent.run.plan_snapshot.plan.model_dump(mode="json")
        legacy_plan.pop("regression")
        legacy_request = CasePlanRunRequest(creation_key="regression-parent", mode_key="api_testing").model_dump(mode="json")
        assert env.parent.run.creation_hash == json_digest({"plan": legacy_plan, "request": legacy_request})
        assert await env.run_store.get_run(env.parent.run.id) == env.original
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {env.settings.database.postgres_test_run_table}")
            assert cur.fetchone()["n"] == 1


@pytest.mark.asyncio
@live
async def test_regression_response_resume_keeps_scope_document_and_original_failure(tmp_path, monkeypatch):
    async with regression_environment(tmp_path) as env:
        def final(context):
            assert context["constraints"]["required_case_versions"] == {env.failure.case_id: env.version.id}
            assert context["constraints"]["regression"]["failures"][0]["result_id"] == env.failure.id
            assert "回归目标" in context["validation_feedback"]["error"]
            doc = next(entry for entry in context["evidence"] if entry["kind"] == "document")
            assert "body.new_id" in doc["payload"]["content"]
            target = next(entry for entry in context["evidence"] if entry["kind"] == "case")
            assert target["version"] == env.version.id
            assert target["payload"]["content"]["steps"][-1]["action"] == "末尾验证点😀"
            return finish({"key": "seed-new", "kind": "prepare", "reason": "新原文提供准备方法",
                "instructions": {"action": "POST /fixtures", "output": "body.new_id"}, "evidence_ids": [doc["id"]]},
                {"key": "revised", "kind": "target", "case_id": env.failure.case_id,
                    "case_version_id": env.version.id, "evidence_ids": [target["id"]], "reason": "只回归原失败的新版本",
                    "depends_on": ["seed-new"], "input_bindings": {"id": {"source_item": "seed-new", "output_path_tokens": ["body", "new_id"]}}})
        env.service._models.replies = [{"action": "query", "summary": "读取真实变更依据",
            "queries": [{"kind": "document", "source_id": env.document.id}]}, finish(), final]
        path = f"/runs/{env.parent.run.id}/regression-planning-tasks"
        payload = {"creation_key": "resume-regression", "result_ids": [env.failure.id],
            "version_overrides": {env.failure.case_id: env.version.id}}
        response = await env.client.post(path, json=payload)
        assert response.status_code == 202, response.text
        task = CasePlanningTaskRecord.model_validate(response.json())
        save = env.jobs.save_checkpoint
        interrupted = []
        async def stop_after_response(claim, checkpoint, *, message):
            result = await save(claim, checkpoint, message=message)
            if checkpoint.phase == "response":
                interrupted.append((claim, checkpoint.model_copy(deep=True)))
                raise asyncio.CancelledError()
            return result
        monkeypatch.setattr(env.jobs, "save_checkpoint", stop_after_response)
        with pytest.raises(asyncio.CancelledError):
            await PlanningTaskHandler(env.service, env.jobs, worker_id="before-stop")(delivery(task))
        paused = await env.jobs.get(task.project_id, task.id)
        assert paused.phase == "response" and paused.round_count == 1
        assert len(env.service._models.requests) == 1
        await env.service.save_policy(task.project_id, CasePlanningPolicy(max_rounds=1))
        expire(env, task)
        restored = PostgresPlanningTaskStore(env.settings, lease_seconds=2)
        await PlanningTaskHandler(env.service, restored, worker_id="after-stop")(delivery(task))
        completed = await restored.get(task.project_id, task.id)
        assert completed.status == "completed" and completed.attempt_no == 2
        assert completed.round_count == 3 and len(env.service._models.requests) == 3
        with pytest.raises(ContinuationLeaseLostError):
            await save(*interrupted[0], message="expired attempt cannot replace recovered plan")
        plan = await env.service.get(task.project_id, completed.plan_id)
        assert plan.config_snapshot.policy.max_rounds == 5
        assert plan.regression.required_case_versions == {env.failure.case_id: env.version.id}
        assert plan.regression.source_results_by_case == {env.failure.case_id: env.failure.id}
        assert (await env.objects.read_object_uri(env.document.storage_uri))["content"] == env.document_bytes
        assert await env.run_store.get_run(env.parent.run.id) == env.original
        await PlanningTaskHandler(env.service, restored, worker_id="terminal")(delivery(task))
        assert len(env.service._models.requests) == 3
        root = f"/projects/{task.project_id}/case-plans/{completed.plan_id}/runs"
        bad = await env.client.post(root, json={"creation_key": "launch", "mode_key": "ui_automation"})
        assert bad.status_code == 409
        launch = {"creation_key": "launch", "mode_key": "api_testing"}
        create = env.run_store.create_planned_run
        async def lose_ack(*args, **kwargs):
            await create(*args, **kwargs)
            raise RuntimeError("controlled lost acknowledgement after committed regression run")
        monkeypatch.setattr(env.run_store, "create_planned_run", lose_ack)
        lost = await env.client.post(root, json=launch)
        assert lost.status_code == 503
        monkeypatch.setattr(env.run_store, "create_planned_run", create)
        env.creator._outbox_table = env.creator._stream = None
        replay = await env.client.post(root, json=launch)
        assert replay.status_code == 201, replay.text
        detail = replay.json()
        assert detail["run"]["run_kind"] == "regression" and detail["run"]["parent_run_id"] == env.parent.run.id
        assert detail["items"][0]["case_version_id"] == env.version.id
        assert detail["items"][0]["regression_source_result_id"] == env.failure.id
        assert (await env.client.post(root, json=launch)).json()["run"]["id"] == detail["run"]["id"]
        assert await env.run_store.get_run(env.parent.run.id) == env.original
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {env.settings.database.postgres_test_run_table}")
            assert cur.fetchone()["n"] == 2
            cur.execute(f"SELECT count(*) AS n FROM {env.jobs._outbox} WHERE payload->>'run_id'=%s", (detail["run"]["id"],))
            assert cur.fetchone()["n"] == 1


@pytest.mark.asyncio
@live
async def test_regression_scope_validation_rejects_passed_foreign_and_inactive_inputs(tmp_path):
    async with regression_environment(tmp_path) as env:
        path = f"/runs/{env.parent.run.id}/regression-planning-tasks"
        passed = next(result for result in env.original.results if result.status == "passed")
        for index, (payload, expected) in enumerate([
            ({"result_ids": [passed.id]}, 409),
            ({"result_ids": [str(uuid4())]}, 404),
            ({"result_ids": [env.failure.id], "version_overrides": {env.failure.case_id: env.failure.case_version_id}}, 409),
            ({"result_ids": [env.failure.id], "version_overrides": {passed.case_id: passed.case_version_id}}, 409),
            ({"result_ids": [env.failure.id], "version_overrides": {env.failure.case_id: passed.case_version_id}}, 409),
            ({"result_ids": [env.failure.id], "fixed_case_versions": {"invented": "invented"}}, 422),
        ]):
            response = await env.client.post(path, json={"creation_key": f"invalid-{index}", **payload})
            assert response.status_code == expected, response.text
        assert env.service._models.requests == []
        assert await env.run_store.get_run(env.parent.run.id) == env.original
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {env.jobs._table}")
            assert cur.fetchone()["n"] == 0


@pytest.mark.asyncio
@live
async def test_fixed_version_regression_task_does_not_invoke_model_and_replay_ignores_new_config(tmp_path, monkeypatch):
    async with regression_environment(tmp_path) as env:
        path = f"/runs/{env.parent.run.id}/regression-planning-tasks"
        payload = {"creation_key": "fixed-regression", "result_ids": [env.failure.id]}
        submit = env.jobs.submit
        async def lose_ack(**kwargs):
            await submit(**kwargs)
            raise RuntimeError("controlled lost acknowledgement after committed regression task")
        monkeypatch.setattr(env.jobs, "submit", lose_ack)
        assert (await env.client.post(path, json=payload)).status_code == 503
        monkeypatch.setattr(env.jobs, "submit", submit)
        env.tasks._stream = None
        async def forbidden(*args, **kwargs): raise AssertionError("replay must not read current config")
        monkeypatch.setattr(env.service, "prepare_task", forbidden)
        responses = await asyncio.gather(*(env.client.post(path, json=payload) for _ in range(4)))
        assert all(response.status_code == 202 for response in responses)
        assert len({response.json()["id"] for response in responses}) == 1
        task = CasePlanningTaskRecord.model_validate(responses[0].json())
        assert (await env.client.post(path, json={**payload, "version_overrides": {env.failure.case_id: env.version.id}})).status_code == 409
        await PlanningTaskHandler(env.service, env.jobs, worker_id="fixed")(delivery(task))
        completed = await env.jobs.get(task.project_id, task.id)
        assert completed.status == "completed" and completed.round_count == 0
        plan = await env.service.get(task.project_id, completed.plan_id)
        assert plan.regression.required_case_versions == {env.failure.case_id: env.failure.case_version_id}
        assert {node.key for node in plan.decision.items} == {"create", "seed", "right", "cleanup"}
        assert env.service._models.requests == []
        assert await env.run_store.get_run(env.parent.run.id) == env.original
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {env.jobs._table}")
            assert cur.fetchone()["n"] == 1
            cur.execute(f"SELECT count(*) AS n FROM {env.jobs._outbox} WHERE payload->>'task_type'='case_planning'")
            assert cur.fetchone()["n"] == 1
