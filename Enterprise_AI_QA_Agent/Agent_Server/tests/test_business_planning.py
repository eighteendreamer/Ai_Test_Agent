"""Actual documents/targets, fixed plans/runs and unchanged old hash inputs."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_business_applicability import decisions_environment, decision_request, save_decision_evidence
from test_case_planning import SequenceModel, finish, item
from test_case_planning_tasks import SubmissionModel
from test_requirement_execution import retention_http
from test_requirement_reviews import LIVE
from src.application.test_cases.planning_task_service import CasePlanningTaskService
from src.application.test_cases.planning_workflow import CasePlanningWorkflow
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.plan_mapping import build_plan_run, json_digest
from src.application.test_runs.plan_run_service import CasePlanRunService
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.application.test_runs.run_service import TestRunService as RunService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.registry.tools import ToolRegistry
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_business_environment_store import environment_digest
from src.runtime.postgres_business_planning import PostgresBusinessPlanning, fixed_planning_environment
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.business_planning import BusinessEnvironmentSelection
from src.schemas.case_planning import CasePlanRunRequest, CasePlanningRequest, case_plan_json, planning_request_json
from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.schemas.run_management import TestRunDetail as RunDetail
from src.schemas.tool_runtime import ModelToolCall
from test_prompt_history_http import _state, model_http_server
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.graph.nodes.model_invoker import build_model_invoker_node
from src.graph.nodes.prompt_assembler import build_prompt_assembler_node
from src.registry.agents import AgentRegistry
from src.schemas.model_config import ModelConfigRecord
from types import SimpleNamespace


def selection(f, index=1):
    snapshot = f.snapshots[index]
    return {"environment_id": snapshot["environment_id"], "snapshot_id": snapshot["id"],
        "expected_snapshot_sha256": snapshot["sha256"]}


def complete_plan(context):
    formal = next(e for e in context["evidence"] if e["payload"].get("case_key") == "execution-retention")
    assert len(formal["payload"]["content"]["steps"]) == 12
    expected = formal["payload"]["content"]["expected"]
    assert expected["expired"] is False and expected["expired_count"] == 0 and expected["deleted_at"] is None
    return finish(item(context, "execution-retention", key="target"))


def test_old_planning_hash_preserves_null_false_zero_and_absent_selection():
    # Match the exact old request bytes, not exclude_none on arbitrary business JSON.
    old = {"objective": "固定输入", "model_key": None, "candidate_case_ids": [],
        "runtime_context": {"flag": False, "count": 0, "empty": None, "a.b": {"": None}}}
    request = CasePlanningRequest.model_validate(old)
    assert planning_request_json(request) == old
    expected = hashlib.sha256(json.dumps(old, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert PostgresPlanningTaskStore.request_hash(request) == expected
    new = request.model_copy(update={"environment": BusinessEnvironmentSelection(environment_id=str(uuid4()),
        snapshot_id=str(uuid4()), expected_snapshot_sha256="a"*64)})
    assert PostgresPlanningTaskStore.request_hash(new) != expected
    with pytest.raises(ValidationError):
        BusinessEnvironmentSelection(environment_id=str(uuid4()), snapshot_id=str(uuid4()),
            expected_snapshot_sha256="a"*64, base_url="http://injected")


def test_saved_normal_regression_hash_and_full_old_execution_context_are_unchanged():
    original = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p4-execution-product-App-verified/requirement-execution-product.json"
    fixture = json.loads(original.read_bytes())
    kinds = set()
    for raw in fixture["run_details"]:
        detail = RunDetail.model_validate(raw)
        run = detail.run
        _, reproduced, _ = build_plan_run(run.plan_snapshot.plan,
            CasePlanRunRequest(creation_key=run.creation_key, mode_key=run.mode_key, session_id=run.session_id),
            now=run.created_at, created_by=run.created_by, regression=run.plan_snapshot.regression)
        assert reproduced.id == run.id and reproduced.creation_hash == raw["run"]["creation_hash"]
        kinds.add(run.run_kind)
        assert "environment" not in case_plan_json(run.plan_snapshot.plan)
        assert "environment" not in case_plan_json(run.plan_snapshot.plan)["request"]
        entry = detail.items[0]
        frozen = next(e for e in run.plan_snapshot.plan.evidence if e.kind == "case" and e.version == entry.case_version_id)
        # Original product saved complete versions in its case catalog.
        from src.schemas.case_management import TestCaseVersionRecord
        version = TestCaseVersionRecord.model_validate(fixture["selected_case"]["version"])
        assert version.id == entry.case_version_id
        context = build_agent_case_context(detail, entry, version)
        assert "business_environment" not in context.runtime_inputs
        assert "business_environment" not in context.execution_json()
        assert len(frozen.payload["content"]["steps"]) == 12
    assert kinds == {"normal", "regression"}


@pytest.mark.parametrize("first", ["src.schemas.business_tool_observation", "src.schemas.case_planning", "src.api.routes.business_applicability"])
def test_independent_import_orders_and_legacy_http_contract(first):
    script = f"import {first}; from src.schemas.http_observation import HttpResponseObservation as Schema; " \
        "from src.modes.api_testing_mode.http_observation import HttpResponseObservation as Legacy; " \
        "from src.containers import AppContainer; assert Schema is Legacy"
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script], capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_fixed_environment_reaches_real_planner_full_case_run_and_recovery(tmp_path, retention_http, model_http_server, monkeypatch, extension):
    async with decisions_environment(tmp_path, retention_http, extension) as f:
        old = await f.decision_client.post(f.decision_routes[0], json=decision_request(f))
        assert old.status_code == 201, old.text
        new_request = decision_request(f, 1, key="new-case-valid", status="effective", case_status="valid")
        new_request.update(coverage_id=f.coverage["id"], expected_coverage_sha256=f.coverage["sha256"])
        new_request["cases"][0]["case_version_id"] = f.selected.version.id
        new_review = await f.decision_client.post(f.decision_routes[1], json=new_request)
        assert new_review.status_code == 201, new_review.text
        target = PostgresBusinessPlanning(applicability=f.decision_store)
        f.env.service._environments = f.runs._environments = target
        model = SequenceModel(complete_plan, complete_plan)
        f.env.service._models = model
        request = CasePlanningRequest(objective="读取完整十二步核对固定九天目标",
            candidate_case_ids=[f.selected.case.id], environment=selection(f),
            runtime_context={"flag": False, "count": 0, "empty": None, "business_environment": "客户端自报不能覆盖"})
        request_file = tmp_path / "planning-request.json"
        request_file.write_text(json.dumps(request.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
        plan = await f.env.service.create(f.project.id, CasePlanningRequest.model_validate_json(request_file.read_bytes()))
        assert plan.status == "planned", plan.summary
        assert plan.environment.snapshot.model_dump(mode="json") == f.snapshots[1]
        assert [r.id for r in plan.environment.reviews] == [new_review.json()["id"]]
        sent = json.loads(model.requests[0].messages[0]["content"])
        assert sent["business_environment"] == plan.environment.model_dump(mode="json")
        assert "不读取生成模板" in model.requests[0].system_prompt
        assert "business_environment" in model.requests[0].system_prompt
        creator = CasePlanRunService(run_store=f.runs, suite_store=f.suites, plan_store=f.env.store,
            case_service=f.env.cases, project_service=f.env.projects)
        payload = CasePlanRunRequest(creation_key="bound-run", mode_key="api_testing")
        detail = await creator.create(f.project.id, plan.id, payload)
        now = datetime.now(timezone.utc)
        claimed, attempt = (await f.runs.claim_items(run_id=detail.run.id, worker_id="fixed-target", limit=1,
            lease_seconds=60, now=now))[0]
        started = await f.runs.start_item(claimed.id, attempt.lease_token, now)
        stored = await f.runs.get_run(detail.run.id)
        context = build_agent_case_context(stored, started, f.selected.version)
        assert len(context.steps[0].content_json["steps"]) == 12
        assert context.business_environment == plan.environment
        assert context.runtime_inputs == request.runtime_context
        assert context.runtime_inputs["flag"] is False and context.runtime_inputs["count"] == 0 and context.runtime_inputs["empty"] is None
        original_context = context.execution_json()
        original_hash = detail.run.creation_hash
        monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
        monkeypatch.setenv('LANGSMITH_TRACING', 'false')
        model_url, physical_agent_requests = model_http_server
        config = ModelConfigRecord(key='local-model', name='Local', provider='openai',
            transport='openai_chat_completions', model_id='local-model', api_base_url=model_url, api_key=uuid4().hex)
        model_runtime = ModelRuntimeService(model_registry=SimpleNamespace(get_runtime_config=lambda key: config),
            settings=SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=10,
                langchain_model_adapter_enabled=False)))
        prompt_state = _state([])
        prompt_state['context_bundle'] = context.runtime_context()
        build_prompt_assembler_node(PromptAssemblyService(), AgentRegistry())(prompt_state)
        await build_model_invoker_node(model_runtime, ToolRegistry())(prompt_state)
        assert len(physical_agent_requests) == 1
        system = '\n'.join(m['content'] for m in physical_agent_requests[0]['messages'] if m['role'] == 'system')
        assert 'server-bound business_environment fixes the selected target' in system
        assert json.dumps(original_context, ensure_ascii=False, allow_nan=False) in system
        session, turn = str(uuid4()), str(uuid4())
        runtime = ToolRuntimeService(tool_job_service=f.jobs)
        fixed_url = context.business_environment.snapshot.configuration.payload.base_url
        tc = ToolExecutionContext(session_id=session, turn_id=turn, trace_id=turn,
            user_message=context.objective, normalized_input=context.objective,
            context_bundle={**context.runtime_context(), "attempt_id": attempt.id})
        actual_tool = await runtime.execute(ToolRegistry().get("api-test-runner"), ModelToolCall(
            id="fixed-target-case", name="api-test-runner", arguments={"worker_action": "execute_task",
                "task": {"task_id": "fixed-target-case", "method": "GET", "path": fixed_url+"/rule"}}), tc)
        checks = [{"name": field, "operator": "equals", "expected_path_tokens": ["expected", field],
            "observations": [{"call_id": actual_tool.call_id, "output_path_tokens": ["task_result", "response_status"]
                if field == "code" else ["task_result", "response_body", field]}]}
            for field in ["code", "days", "expired_count", "expired", "deleted_at"]]
        outcome = await AgentCaseAssessmentService(f.jobs).assess(context=context, receipt={"schema_version": 1,
            "steps": [{"item_key": "target", "status": "passed", "summary": "对照固定用例的真实九天响应",
                "checks": checks}]}, tool_records=[actual_tool], session_id=session, turn_id=turn,
            attempt_id=attempt.id, model_key="controlled-tool-receipt", lease_token=attempt.lease_token)
        run_service = RunService(store=f.runs, project_service=f.env.projects, suite_service=None,
            test_case_service=f.env.cases, tool_job_service=f.jobs, clock=lambda: datetime.now(timezone.utc))
        result = await run_service.complete_item(started.id, outcome.completion)
        assert result.status == "passed"
        evaluated = result.actual["verification_results"][0]["metadata"]["checks"]
        assert len(evaluated) == 5 and all(check["status"] == "passed" for check in evaluated)
        assert [check["actual"] for check in evaluated] == [200, 9, 0, False, None]
        assert not await f.runs.claim_items(run_id=detail.run.id, worker_id="terminal", limit=1,
            lease_seconds=60, now=datetime.now(timezone.utc))
        # New confirmation affects later planning; it never rewrites the original plan.
        revoke = {**new_request, "request_id": "revoke-after-plan", "expected_review_id": new_review.json()["id"],
            "action": "revoke", "observations": [], "points": [], "cases": []}
        reply = await f.decision_client.post(f.decision_routes[1], json=revoke)
        assert reply.status_code == 201, reply.text
        refreshed = await f.env.service.create(f.project.id, request)
        assert refreshed.environment.reviews[0].request.action == "revoke"
        assert refreshed.environment.sha256 != plan.environment.sha256
        original_plan = await f.env.store.get(f.project.id, plan.id)
        assert original_plan == plan
        await f.decision_client.put(f.env_roots[1]+"/configuration", json={"display_name": "已改址", "base_url": "http://127.0.0.1:1/different"})
        await f.decision_client.post(f.env_roots[1]+"/archive")
        await f.env.projects.archive(f.project.id)
        def forbidden(*args, **kwargs): raise AssertionError("fixed recovery cannot read current config/model/environment")
        monkeypatch.setattr(f.env.configs, "get", forbidden)
        monkeypatch.setattr(target, "freeze", forbidden)
        monkeypatch.setattr(model, "invoke", forbidden)
        replay = await creator.create(f.project.id, plan.id, payload)
        assert replay.run.creation_hash == original_hash and replay.run.id == detail.run.id
        assert build_agent_case_context(replay, replay.items[0], f.selected.version).execution_json() == original_context
        with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {f.env.settings.database.postgres_case_plan_table} WHERE id=%s", (plan.id,))
            sql_plan = cur.fetchone()["record"]
            cur.execute(f"SELECT record FROM {f.env.settings.database.postgres_test_run_table} WHERE id=%s", (detail.run.id,))
            sql_run = cur.fetchone()["record"]
        assert sql_plan["environment"] == plan.environment.model_dump(mode="json")
        assert sql_run["plan_snapshot"]["plan"]["environment"] == sql_plan["environment"]
        save_decision_evidence(f, "planning-"+extension, request=request.model_dump(mode="json"),
            model_context=sent, fixed_plan=sql_plan, refreshed_plan=refreshed.model_dump(mode="json"),
            fixed_run=sql_run, agent_context=original_context, creation_hash=original_hash,
            actual_case_result=result.model_dump(mode="json"),
            actual_case_tool=(await f.jobs.get_job(actual_tool.job_id)).model_dump(mode="json"),
            agent_physical_model_requests=physical_agent_requests,
            execution_from_context_target=True, controlled_receipt_is_test_fixture=True,
            recovered_creation_hash=replay.run.creation_hash, configuration_changed_after_plan=True,
            claim_restrictions_implemented=False, autonomous_model=False)


@pytest.mark.asyncio
@LIVE
async def test_durable_seed_freezes_environment_before_changes_without_refreezing(tmp_path, retention_http, monkeypatch):
    async with decisions_environment(tmp_path, retention_http) as f:
        target = PostgresBusinessPlanning(applicability=f.decision_store)
        f.env.service._environments = target
        model = SubmissionModel(complete_plan)
        f.env.service._models = model
        suffix = uuid4().hex[:12]
        f.env.settings.database.postgres_case_planning_task_table = "up_bp_task_"+suffix
        f.env.settings.database.postgres_task_outbox_table = "up_bp_out_"+suffix
        owned = [f.env.settings.database.postgres_case_planning_task_table, f.env.settings.database.postgres_task_outbox_table]
        with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
            for table in owned:
                cur.execute("SELECT to_regclass(%s) AS existing", (table,)); assert cur.fetchone()["existing"] is None
        jobs = PostgresPlanningTaskStore(f.env.settings, environment_store=target)
        await PostgresTaskOutbox(f.env.settings).initialize()
        tasks = CasePlanningTaskService(store=jobs, planning_service=f.env.service, project_service=f.env.projects,
            dispatch_stream="isolated:business-planning")
        await tasks.initialize()
        try:
            request = CasePlanningTaskRequest(creation_key="fixed-seed", objective="完整用例固定目标规划",
                environment=selection(f), candidate_case_ids=[f.selected.case.id])
            original = await tasks.submit(f.project.id, request)
            assert not model.requests
            await f.decision_client.put(f.env_roots[1]+"/configuration", json={"display_name": "改配置", "base_url": "http://127.0.0.1:1/unused"})
            def forbidden(*args, **kwargs): raise AssertionError("original seed replay cannot refreeze target")
            monkeypatch.setattr(target, "freeze", forbidden)
            assert (await tasks.submit(f.project.id, request)).id == original.id
            claim = await jobs.claim(f.project.id, original.id, worker_id="actual-planning-worker")
            assert claim.seed.plan.environment.snapshot.model_dump(mode="json") == f.snapshots[1]
            tampered = claim.checkpoint.model_copy(deep=True)
            tampered.plan.environment.snapshot.environment_key = "不能替换原固定输入"
            with pytest.raises(ValueError, match="固定输入"):
                await jobs.save_checkpoint(claim, tampered, message="attempted environment replacement")
            async def persist(checkpoint, *, message): await jobs.save_checkpoint(claim, checkpoint, message=message)
            await CasePlanningWorkflow(f.env.service).run(claim.seed, claim.checkpoint,
                deadline_at=claim.task.deadline_at, persist=persist, attempt_id=claim.attempt_id, pin_read_versions=True)
            completed = await jobs.finish(claim, claim.checkpoint)
            assert completed.status == "completed"
            assert len(model.requests) == 1
            saved = await f.env.store.get(f.project.id, completed.plan_id)
            assert saved.environment == claim.seed.plan.environment
            assert (await tasks.submit(f.project.id, request)).plan_id == saved.id
            assert not await jobs.claim(f.project.id, original.id, worker_id="terminal-no-reclaim")
            save_decision_evidence(f, "planning-durable", task=completed.model_dump(mode="json"),
                seed=claim.seed.model_dump(mode="json"), final_plan=saved.model_dump(mode="json"),
                model_context=json.loads(model.requests[0].messages[0]["content"]),
                original_request_digest=jobs.request_hash(request.planning_request()),
                current_config_used_on_replay=False, claim_restrictions_implemented=False)
        finally:
            with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
                for table in owned: cur.execute(f'DROP TABLE "{table}"')


@pytest.mark.asyncio
@LIVE
async def test_false_environment_bases_and_foreign_selection_cannot_enter_run(tmp_path, retention_http):
    async with decisions_environment(tmp_path, retention_http) as f:
        target = PostgresBusinessPlanning(applicability=f.decision_store)
        selected = BusinessEnvironmentSelection(**selection(f))
        with pytest.raises(KeyError): await target.freeze(str(uuid4()), selected)
        with pytest.raises(ValueError, match="哈希"):
            await target.freeze(f.project.id, selected.model_copy(update={"expected_snapshot_sha256": "f"*64}))
        f.env.service._environments = f.runs._environments = target
        f.env.service._models = SequenceModel(complete_plan)
        request = CasePlanningRequest(objective="核对所选环境", environment=selected, candidate_case_ids=[f.selected.case.id])
        plan = await f.env.service.create(f.project.id, request)
        altered = plan.model_copy(deep=True)
        altered.id = str(uuid4())
        altered.environment.snapshot.environment_key = "伪造登记"
        altered.environment.snapshot.sha256 = environment_digest(altered.environment.snapshot.model_dump(mode="json", exclude={"sha256"}))
        altered.request.environment.expected_snapshot_sha256 = altered.environment.snapshot.sha256
        altered.environment.sha256 = environment_digest(altered.environment.model_dump(mode="json", exclude={"sha256"}))
        fixed_planning_environment(f.project.id, altered.request.environment, altered.environment)
        await f.env.store.save(altered)
        creator = CasePlanRunService(run_store=f.runs, suite_store=f.suites, plan_store=f.env.store,
            case_service=f.env.cases, project_service=f.env.projects)
        with pytest.raises(ValueError, match="哈希"):
            await creator.create(f.project.id, altered.id, CasePlanRunRequest(creation_key="false-basis", mode_key="api_testing"))
        with postgres_connect(f.env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {f.env.settings.database.postgres_test_run_table}")
            assert cur.fetchone()["n"] == 0
        save_decision_evidence(f, "planning-boundaries", original_plan=plan.model_dump(mode="json"),
            altered_plan=altered.model_dump(mode="json"), wrong_bases_saved_run_count=0,
            claim_restrictions_implemented=False)
