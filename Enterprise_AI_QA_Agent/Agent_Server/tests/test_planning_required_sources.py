"""Replay actual omitted-source decisions against the shared planning loop."""
from copy import deepcopy
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_case_planning import environment, SequenceModel, active_case, finish, item, LIVE
from src.schemas.case_planning import CasePlanningRequest, CasePlanningPolicy, planning_request_json
from src.schemas.case_planning_task import CasePlanningTaskRequest, PlanningTaskSeed, PlanningTaskCheckpoint
from src.schemas.project import ProjectCreateRequest
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.application.test_cases.planning_workflow import CasePlanningWorkflow
from src.application.test_cases.planning_required_sources import validate_required_sources
from datetime import datetime, timedelta, timezone

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


def test_legacy_request_identity_and_new_required_sources():
    original = json.loads((EVIDENCE / "p0-recording-agent-registered/plan.json").read_bytes())["request"]
    parsed = CasePlanningRequest.model_validate(original)
    assert parsed.model_dump(mode="json") == original
    first = CasePlanningRequest.model_validate({**original, "required_sources": [{"kind": "recording", "source_id": "one"}]})
    second = CasePlanningRequest.model_validate({**original, "required_sources": [{"kind": "recording", "source_id": "two"}]})
    assert PostgresPlanningTaskStore.request_hash(first) != PostgresPlanningTaskStore.request_hash(second)
    task = CasePlanningTaskRequest.model_validate({**first.model_dump(), "creation_key": "fixed"})
    assert task.planning_request() == first
    assert "required_sources" not in planning_request_json(parsed)
    with pytest.raises(ValidationError):
        CasePlanningRequest(objective="test", required_sources=[{"kind": "recording", "source_id": "one"}] * 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_actual_omitted_source_candidate_requires_read_and_reference(tmp_path, live):
    captured = json.loads((EVIDENCE / "p0-recording-agent-registered/plan.json").read_bytes())["decision"]
    async with environment(tmp_path, live, model=SequenceModel()) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="required", name="Required source"))
        source = tmp_path / "requirement.md"
        source.write_text("# 展开详情\n点击展开详情后显示已展开及本次DOM确认串。\n", encoding="utf-8")
        doc = await env.docs.upload_document(filename=source.name, content_base64=base64.b64encode(source.read_bytes()).decode(), project_id=project.id)
        target = await active_case(env, project, "details", {"objective": "展开详情"})

        def omitted(context):
            candidate = deepcopy(captured)
            candidate["items"] = [item(context, "details", reason=captured["items"][0]["reason"])]
            return candidate

        def referenced(context):
            document = next(e for e in context["evidence"] if e["kind"] == "document")
            chosen = item(context, "details")
            chosen["evidence_ids"].append(document["id"])
            return finish(chosen)

        env.service._models = model = SequenceModel(omitted,
            {"action": "query", "summary": "读取明确来源", "queries": [{"kind": "document", "source_id": doc.id}]},
            omitted, referenced)
        response = await env.client.post(f"/projects/{project.id}/case-plans", json={"objective": "展开详情",
            "candidate_case_ids": [target.case.id], "required_sources": [{"kind": "document", "source_id": doc.id}]})
        assert response.status_code == 201, response.text
        plan = response.json()
        assert plan["status"] == "planned" and len(model.requests) == 4
        contexts = [json.loads(request.messages[0]["content"]) for request in model.requests]
        assert "必须实际读取并引用明确来源" in contexts[1]["validation_feedback"]["error"]
        assert "必须实际读取并引用明确来源" in contexts[3]["validation_feedback"]["error"]
        assert contexts[3]["required_sources"] == [{"kind": "document", "source_id": doc.id}]
        assert "点击展开详情" in json.dumps(contexts[3], ensure_ascii=False)
        assert all("DO-NOT-INJECT-TEMPLATE" not in request.system_prompt for request in model.requests)
        fixed = await env.service.get(project.id, plan["id"])
        validate_required_sources(fixed, fixed.decision)


@pytest.mark.asyncio
async def test_seed_recovery_uses_frozen_requirement_prompt(tmp_path, monkeypatch):
    from test_case_planning_tasks import SubmissionModel
    import src.application.test_cases.planning_service as service_module
    async with environment(tmp_path, model=SubmissionModel()) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="freeze", name="Freeze"))
        target = await active_case(env, project, "details", {"objective": "展开详情"})
        path = tmp_path / "requirements.md"
        path.write_text("# 固定来源\n真实确认串必须读取。", encoding="utf-8")
        doc = await env.docs.upload_document(filename=path.name, content_base64=base64.b64encode(path.read_bytes()).decode(), project_id=project.id)
        request = CasePlanningRequest(objective="展开详情", candidate_case_ids=[target.case.id],
            required_sources=[{"kind": "document", "source_id": doc.id}])
        seed = await env.service.prepare_task(project.id, request)
        raw = seed.model_dump_json()
        assert seed.required_source_prompt and seed.plan.prompt_version.endswith("+required-sources-v1")
        monkeypatch.setattr(service_module, "REQUIRED_SOURCE_PROMPT", "DO-NOT-USE-NEW-PROMPT")
        recovered = PlanningTaskSeed.model_validate_json(raw)
        checkpoint = PlanningTaskCheckpoint(plan=recovered.plan)

        def referenced(context):
            chosen = item(context, "details")
            chosen["evidence_ids"].extend(e["id"] for e in context["evidence"] if e["kind"] == "document")
            return finish(chosen)

        env.service._models.replies = [{"action": "query", "summary": "读原来源", "queries": [{"kind": "document", "source_id": doc.id}]}, referenced]
        await CasePlanningWorkflow(env.service).run(recovered, checkpoint, deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30))
        assert checkpoint.plan.status == "planned"
        assert PlanningTaskSeed.model_validate_json(raw).model_dump_json() == raw
        assert all("DO-NOT-USE-NEW-PROMPT" not in r.system_prompt for r in env.service._models.requests)
        assert all(recovered.required_source_prompt in r.system_prompt for r in env.service._models.requests)


@pytest.mark.asyncio
async def test_foreign_required_source_is_not_read_or_accepted(tmp_path):
    async with environment(tmp_path, model=SequenceModel()) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="owned", name="当前项目"))
        other = await env.projects.create(ProjectCreateRequest(project_key="foreign", name="其他项目"))
        target = await active_case(env, project, "details", {"objective": "展开详情"})
        doc = await env.docs.upload_document(filename="foreign.md", project_id=other.id,
            content_base64=base64.b64encode(b"FOREIGN-CONTENT-MUST-NOT-LEAK").decode())
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=2))
        env.service._models = model = SequenceModel(
            {"action": "query", "summary": "读取指定来源", "queries": [{"kind": "document", "source_id": doc.id}]},
            lambda c: finish(item(c, "details")))
        response = await env.client.post(f"/projects/{project.id}/case-plans", json={"objective": "展开详情",
            "candidate_case_ids": [target.case.id], "required_sources": [{"kind": "document", "source_id": doc.id}]})
        assert response.status_code == 201
        assert response.json()["status"] == "planning_failed"
        contexts = [json.loads(r.messages[0]["content"]) for r in model.requests]
        assert contexts[1]["query_results"][0]["error"] == "文档不属于当前项目"
        assert "FOREIGN-CONTENT-MUST-NOT-LEAK" not in json.dumps(contexts)
        assert not any(e["kind"] == "document" for e in response.json()["evidence"])
        assert "必须实际读取并引用明确来源" in response.json()["summary"]


@pytest.mark.asyncio
async def test_no_match_keeps_required_source_without_claiming_execution(tmp_path):
    async with environment(tmp_path, model=SequenceModel(finish())) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="empty", name="没有目标"))
        response = await env.client.post(f"/projects/{project.id}/case-plans", json={"objective": "没有目标",
            "required_sources": [{"kind": "recording", "source_id": "unavailable"}]})
        assert response.status_code == 201
        assert response.json()["status"] == "no_match"
        assert response.json()["decision"]["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.skipif(os.getenv("RUN_LIVE_CASE_PLANNING_TASKS") != "1", reason="requires actual isolated MySQL/PG/RustFS")
async def test_required_source_survives_actual_pg_worker_checkpoint_recovery(tmp_path, monkeypatch, staged):
    from test_case_planning_tasks import tasks_environment, delivery, expire
    from test_case_planning_stages import POLICY
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.runtime.planning_task_handler import PlanningTaskHandler
    from src.application.test_cases.planning_workflow import PlanningCheckpointUnavailable
    import src.application.test_cases.planning_service as service_module
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="durable", name="明确来源恢复"))
        target = await active_case(env, project, "details", {"objective": "展开详情"})
        path = tmp_path / "required.md"
        path.write_text("# 固定正文\n恢复后仍读取并引用实际依据。\n", encoding="utf-8")
        doc = await env.docs.upload_document(filename=path.name, project_id=project.id,
            content_base64=base64.b64encode(path.read_bytes()).decode())
        assert (await env.objects.read_object_uri(doc.storage_uri))["content"] == path.read_bytes()
        if staged:
            await env.service.save_policy(project.id, POLICY)
        request = CasePlanningTaskRequest(creation_key="durable-source", objective="展开详情",
            candidate_case_ids=[target.case.id], required_sources=[{"kind": "document", "source_id": doc.id}])
        task = await env.tasks.submit(project.id, request)
        assert (await env.tasks.submit(project.id, request)).id == task.id
        changed_source = request.required_sources[0].model_copy(update={"source_id": "another-source"})
        with pytest.raises(ValueError):
            await env.tasks.submit(project.id, request.model_copy(update={"required_sources": [changed_source]}))

        def referenced(context):
            chosen = item(context, "details")
            chosen["evidence_ids"].extend(e["id"] for e in context["evidence"] if e["kind"] == "document")
            return finish(chosen)
        env.model.replies = [lambda c: finish(item(c, "details")),
            {"action": "query", "summary": "读取固定原件", "queries": [{"kind": "document", "source_id": doc.id}]}, referenced]
        if staged:
            env.model.replies.append({"action": "finish", "summary": "固定目标无额外传值", "bindings": []})
        save_checkpoint = env.jobs.save_checkpoint
        async def lose_ack(claim, checkpoint, *, message):
            result = await save_checkpoint(claim, checkpoint, message=message)
            if checkpoint.phase == "ready" and checkpoint.next_round == 2 and any(e.kind == "document" for e in checkpoint.plan.evidence):
                raise RuntimeError("actual PG source checkpoint committed; acknowledgement lost")
            return result
        monkeypatch.setattr(env.jobs, "save_checkpoint", lose_ack)
        with pytest.raises(PlanningCheckpointUnavailable):
            await PlanningTaskHandler(env.service, env.jobs, worker_id="before-loss")(delivery(task))
        before = await env.jobs.get(project.id, task.id)
        assert len(env.model.requests) == 2 and before.phase == "ready"
        with postgres_connect(env.settings) as conn, conn.cursor() as cursor:
            cursor.execute(f"SELECT seed,checkpoint,request_hash FROM {env.jobs._table} WHERE id=%s", (task.id,))
            persisted = cursor.fetchone()
        frozen = PlanningTaskSeed.model_validate(persisted["seed"])
        assert frozen.plan.request.required_sources == request.required_sources
        assert persisted["request_hash"] == env.jobs.request_hash(request.planning_request())
        assert persisted["checkpoint"]["plan"]["evidence"][-1]["payload"]["content"] == path.read_text(encoding="utf-8").rstrip("\n")
        monkeypatch.setattr(service_module, "REQUIRED_SOURCE_PROMPT", "DO-NOT-USE-NEW-PROMPT")
        await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
        monkeypatch.setattr(env.jobs, "save_checkpoint", save_checkpoint)
        expire(env, task)
        restarted = PostgresPlanningTaskStore(env.settings)
        await PlanningTaskHandler(env.service, restarted, worker_id="after-loss")(delivery(task))
        terminal = await restarted.get(project.id, task.id)
        assert terminal.status == "completed" and terminal.attempt_no == 2
        assert terminal.deadline_at == before.deadline_at
        plan = await env.service.get(project.id, terminal.plan_id)
        assert plan.status == "planned" and plan.prompt_version.endswith("+required-sources-v1")
        validate_required_sources(plan, plan.decision)
        assert len(env.model.requests) == (4 if staged else 3)
        assert all(frozen.required_source_prompt in call.system_prompt for call in env.model.requests)
        assert all("DO-NOT-USE-NEW-PROMPT" not in call.system_prompt for call in env.model.requests)
        await PlanningTaskHandler(env.service, restarted, worker_id="terminal-replay")(delivery(task))
        assert len(env.model.requests) == (4 if staged else 3)

        # Exercise the actual precomputed regression submission/PG/handler
        # branch. Parent/result identities are contract fixtures here; actual
        # failed-run selection is covered by test_regression_planning_tasks.
        from src.application.test_runs.agent_regression_service import AgentRegressionService
        from src.schemas.case_planning import RegressionPlanningInput
        from src.schemas.run_management import RegressionPlanningTaskRequest
        parent_id, result_id = str(uuid4()), str(uuid4())
        context = {"parent_run_id": parent_id, "source_plan_id": plan.id}
        subset = AgentRegressionService._subset(SimpleNamespace(plan=plan, preparation_phases={}),
            {plan.decision.items[0].key: result_id}, context)
        source = RegressionPlanningInput(parent_run_id=parent_id, source_plan_id=plan.id,
            source_results_by_case={target.case.id: result_id}, required_case_versions={target.case.id: target.version.id},
            mode_key="api_testing", submission={"result_ids": [result_id]}, context=context)
        frozen_task = await env.tasks.submit_regression(project.id,
            RegressionPlanningTaskRequest(creation_key="fixed-source-regression", result_ids=[result_id]), source,
            planning_request=subset.request, prepared_plan=subset)
        await PlanningTaskHandler(env.service, restarted, worker_id="fixed-regression")(delivery(frozen_task))
        fixed_terminal = await restarted.get(project.id, frozen_task.id)
        assert fixed_terminal.status == "completed"
        fixed_plan = await env.service.get(project.id, fixed_terminal.plan_id)
        assert fixed_plan.request.required_sources == request.required_sources
        validate_required_sources(fixed_plan, fixed_plan.decision)
        assert len(env.model.requests) == (4 if staged else 3)


@pytest.mark.asyncio
async def test_regression_replan_preserves_explicit_source_and_subset_rechecks_promoted_dependency(tmp_path):
    from src.application.test_runs.agent_regression_service import AgentRegressionService
    async with environment(tmp_path, model=SequenceModel()) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="regression", name="回归来源"))
        target = await active_case(env, project, "details", {"objective": "展开详情"})
        doc = await env.docs.upload_document(filename="required.md", project_id=project.id,
            content_base64=base64.b64encode("# 展开详情\n实际依据。".encode()).decode())
        def referenced(context):
            chosen = item(context, "details")
            chosen["evidence_ids"].extend(e["id"] for e in context["evidence"] if e["kind"] == "document")
            return finish(chosen)
        def replies():
            return [{"action": "query", "summary": "读取明确来源", "queries": [{"kind": "document", "source_id": doc.id}]}, referenced]
        env.service._models.replies = replies()
        original = await env.service.create(project.id, CasePlanningRequest(objective="展开详情",
            candidate_case_ids=[target.case.id], required_sources=[{"kind": "document", "source_id": doc.id}]))
        parent = SimpleNamespace(project_id=project.id, plan_snapshot=SimpleNamespace(plan=original))
        regression = AgentRegressionService(plan_store=env.store, plan_run_service=None,
            planning_service=env.service, run_store=None)
        env.service._models.replies = replies()
        replanned = await regression._replan(parent, {target.case.id: target.version.id}, {})
        assert replanned.request.required_sources == original.request.required_sources
        validate_required_sources(replanned, replanned.decision)
        # A former dependency can become the selected regression target. Its
        # missing required reference must trigger existing replanning.
        dependency = original.decision.items[0].model_copy(deep=True)
        dependency.key, dependency.kind = "former-dependency", "dependency"
        dependency.evidence_ids = [f"case:{target.version.id}"]
        original.decision.items.append(dependency)
        assert AgentRegressionService._subset(SimpleNamespace(plan=original, preparation_phases={}),
            {dependency.key: "failed-result"}, {}) is None
