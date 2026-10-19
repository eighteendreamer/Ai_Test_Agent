"""Actual allowed execution must retain the confirmation checked at first start."""
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
from contextlib import asynccontextmanager
from html import unescape

import pytest

from test_business_applicability import decisions_environment,decision_request,save_decision_evidence
from test_business_environment_restrictions import prepared,execute_independent
from test_requirement_execution import retention_http
from test_requirement_reviews import LIVE
from src.schemas.case_planning import CasePlanRunRequest
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.runtime.postgres_business_environment_store import environment_digest
from src.schemas.run_management import TestRunAttemptRecord as AttemptRecord
from src.application.test_runs.environment_admission import fixed_environment_admission
from test_agent_case_session import (ControlledAgent,ControlledModels,EmptyMCP,session_stores,
    AgentRegistry,ToolRegistry,SkillRegistry,RuntimeControlRegistry,ToolRuntimeService,build_agent_graph,
    SkillRuntimeService,PermissionService,PromptAssemblyService,RuntimeService,SessionService,
    InputOrchestratorService,ModeRegistry)
from src.application.test_runs.agent_case_execution import AgentCaseExecutionAdapter,AgentCaseApprovalWait
from src.application.test_runs.run_service import TestRunService as RunService
from src.schemas.model_config import ModelInvocationResult
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.run_management import RunItemApprovalWaitRequest,RunItemLeaseRequest
from src.schemas.session import ApprovalDecisionRequest,ToolApprovalStatus
from test_case_plan_run_creation import replace_owned_run


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension",["md","docx","pdf"])
async def test_actual_first_start_freezes_current_unknown_for_independent_case(tmp_path,retention_http,extension):
    async with decisions_environment(tmp_path,retention_http,extension) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="admission",mode_key="api_testing"))
        current = await f.decision_client.post(f.decision_routes[1],json=decision_request(f,1,
            status="unknown",case_status="unknown"))
        assert current.status_code == 201,current.text
        claims = await f.runs.claim_items(run_id=detail.run.id,worker_id="independent-start",limit=2,
            lease_seconds=60,now=datetime.now(timezone.utc))
        claim = next(row for row in claims if row[0].plan_item_key == "independent")
        started = await f.runs.start_item(claim[0].id,claim[1].lease_token,datetime.now(timezone.utc))
        attempt = await f.runs.get_latest_attempt(started.id)
        save_decision_evidence(f,"admission-start-"+extension,run=(await f.runs.get_run(detail.run.id)).model_dump(mode="json"),
            fixed_plan=plan.model_dump(mode="json"),current_review=current.json(),attempt=attempt.model_dump(mode="json"),
            current_confirmation_persisted=getattr(attempt,"environment_admission",None) is not None)
        assert getattr(attempt,"environment_admission",None) is not None,"allowed start did not save current confirmation"
        receipt = attempt.environment_admission
        assert receipt.reviews[0].model_dump(mode="json") == current.json()
        assert receipt.checked_at == attempt.started_at and receipt.execution_attempt_id == attempt.id
        assert receipt.decision == "no_explicit_invalid" and not plan.environment.reviews
        context = build_agent_case_context(await f.runs.get_run(detail.run.id),started,
            await f.env.cases.get_version(started.case_version_id))
        assert context.environment_admission == receipt and context.business_environment == plan.environment
        result,job = await execute_independent(f,claim,started=started)
        assert result.status == "passed" and result.actual["environment_admission"] == receipt.model_dump(mode="json")
        save_decision_evidence(f,"admission-result-"+extension,run=(await f.runs.get_run(detail.run.id)).model_dump(mode="json"),
            fixed_plan=plan.model_dump(mode="json"),current_review=current.json(),attempt=attempt.model_dump(mode="json"),
            agent_context=context.execution_json(),independent_result=result.model_dump(mode="json"),independent_job=job,
            confirmation_is_not_execution_success=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_committed_allowed_start_reply_recovers_without_replacing_confirmation(tmp_path,retention_http,monkeypatch):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="lost-admission",mode_key="api_testing"))
        claim = next(row for row in await f.runs.claim_items(run_id=detail.run.id,worker_id="lost-start",limit=2,
            lease_seconds=60,now=datetime.now(timezone.utc)) if row[0].plan_item_key == "independent")
        original = f.runs._start_item_sync
        def lost(*args): original(*args);raise ConnectionError("actual PG allowed-start reply lost")
        with monkeypatch.context() as patch:
            patch.setattr(f.runs,"_start_item_sync",lost)
            with pytest.raises(ConnectionError): await f.runs.start_item(claim[0].id,claim[1].lease_token,datetime.now(timezone.utc))
        persisted = await f.runs.get_latest_attempt(claim[0].id)
        assert persisted.environment_admission is not None
        changed = await f.decision_client.post(f.decision_routes[1],json=decision_request(f,1,status="unknown",case_status="unknown"))
        assert changed.status_code == 201
        save_decision_evidence(f,"admission-lost-start-before-replay",run=(await f.runs.get_run(detail.run.id)).model_dump(mode="json"),
            fixed_plan=plan.model_dump(mode="json"),persisted_attempt=persisted.model_dump(mode="json"),current_review=changed.json(),
            actual_allowed_commit_reply_lost=True)
        started = await f.runs.start_item(claim[0].id,claim[1].lease_token,datetime.now(timezone.utc))
        assert started.status == "running" and await f.runs.get_latest_attempt(started.id) == persisted
        with pytest.raises(ValueError): await f.runs.start_item(started.id,"wrong-token",datetime.now(timezone.utc))
        with pytest.raises(ValueError): await f.runs.start_item(started.id,claim[1].lease_token,started.lease_expires_at+timedelta(seconds=1))
        save_decision_evidence(f,"admission-lost-start-recovered",run=(await f.runs.get_run(detail.run.id)).model_dump(mode="json"),
            fixed_plan=plan.model_dump(mode="json"),persisted_attempt=persisted.model_dump(mode="json"),current_review=changed.json(),
            actual_allowed_commit_reply_lost=True,new_replay_HTTP=0)


def test_original_captured_attempt_wire_preserves_all_null_false_zero_values():
    folder = Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p4-environment-admission-reproduction/applicability-admission-start-md"
    state = json.loads((folder/"verification.json").read_bytes())
    raw = state["attempt"]
    assert not state["current_confirmation_persisted"] and "environment_admission" not in raw
    assert AttemptRecord.model_validate(raw).model_dump(mode="json") == raw
    assert len(state["full_case"]["version"]["content_json"]["steps"]) == 12


@pytest.mark.asyncio
@LIVE
async def test_started_admission_context_and_checkpoint_survive_later_review_and_expiry(tmp_path,retention_http):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="admission-recovery",mode_key="api_testing"))
        now = datetime.now(timezone.utc)
        entry,attempt = (await f.runs.claim_items(run_id=detail.run.id,worker_id="root-start",limit=1,lease_seconds=60,now=now))[0]
        started = await f.runs.start_item(entry.id,attempt.lease_token,now)
        original = await f.runs.get_latest_attempt(entry.id)
        version = await f.env.cases.get_version(entry.case_version_id)
        context = build_agent_case_context(await f.runs.get_run(detail.run.id),started,version)
        checkpoint = {"context":context.execution_json(),"outcome":"unknown","actual_business_sent":False}
        await f.runs.save_checkpoint(entry.id,attempt.lease_token,"fixed-start-basis",checkpoint,1,now)
        original = await f.runs.get_latest_attempt(entry.id)
        invalid = await f.decision_client.post(f.decision_routes[1],json=decision_request(f,1,status="effective",case_status="invalid"))
        assert invalid.status_code == 201
        changed = detail.run.model_copy(deep=True)
        changed.plan_snapshot.plan.request.runtime_context['later_input'] = 'new current projection'
        replace_owned_run(f.env, f.runs, changed, True)
        replayed = await f.runs.start_item(entry.id, attempt.lease_token, now)
        assert replayed.status == 'running'
        assert (await f.runs.get_latest_attempt(entry.id)).environment_admission == original.environment_admission
        later = now+timedelta(seconds=91)
        assert await f.runs.recover_expired(detail.run.id,later) == 1
        recovered,new_attempt = (await f.runs.claim_items(run_id=detail.run.id,worker_id="recover",limit=1,lease_seconds=60,now=later))[0]
        assert recovered.id == entry.id and new_attempt.environment_admission == original.environment_admission
        restarted = await f.runs.start_item(entry.id,new_attempt.lease_token,later)
        saved = await f.runs.get_execution_detail(detail.run.id,entry.id,new_attempt.id)
        recovered_context = build_agent_case_context(saved,restarted,version)
        assert recovered_context.execution_json() == context.execution_json()
        assert next(row for row in saved.attempts if row.id == original.id).checkpoint_payload == checkpoint
        assert not saved.results
        save_decision_evidence(f,"admission-checkpoint-recovery",run=saved.model_dump(mode="json"),fixed_plan=plan.model_dump(mode="json"),
            original_attempt=original.model_dump(mode="json"),original_context=context.execution_json(),
            recovered_context=recovered_context.execution_json(),current_review=invalid.json(),new_recovery_HTTP=0)


@pytest.mark.asyncio
@LIVE
async def test_actual_admission_tamper_and_foreign_ancestor_are_rejected(tmp_path,retention_http):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="admission-bases",mode_key="api_testing"))
        now = datetime.now(timezone.utc)
        entry,attempt = (await f.runs.claim_items(run_id=detail.run.id,worker_id="bases",limit=1,lease_seconds=60,now=now))[0]
        started = await f.runs.start_item(entry.id,attempt.lease_token,now)
        saved = await f.runs.get_run(detail.run.id)
        original = next(row for row in saved.attempts if row.id == attempt.id)
        for key,value in [("sha256","0"*64),("snapshot_sha256","0"*64),("case_version_id","foreign-version"),
            ("project_id","foreign-project"),("execution_attempt_id","foreign-ancestor")]:
            changed = original.model_copy(deep=True)
            changed.environment_admission = changed.environment_admission.model_copy(update={key:value})
            if key != "sha256":
                changed.environment_admission.sha256 = environment_digest(changed.environment_admission.model_dump(mode="json",exclude={"sha256"}))
            with pytest.raises(ValueError): fixed_environment_admission(saved.run,started,changed,[changed])
        save_decision_evidence(f,"admission-tamper",run=saved.model_dump(mode="json"),fixed_plan=plan.model_dump(mode="json"),
            original_attempt=original.model_dump(mode="json"),rejected_bases=["sha256","snapshot_sha256","case_version_id","project_id","execution_attempt_id"])


class AdmissionAgent(ControlledAgent):
    async def invoke(self,key,request):
        self.requests.append(request)
        section = next(s for s in request.system_prompt_sections if s.key == "frozen_case_execution")
        frozen = json.loads(unescape(section.content.split("\n",1)[1].split("\n</untrusted_content>",1)[0]))
        assert "first start" in request.system_prompt and frozen["environment_admission"]["decision"] == "no_explicit_invalid"
        previous = [json.loads(m["content"]) for m in request.messages if m.get("role") == "tool"]
        if not previous:
            path = frozen["business_environment"]["snapshot"]["configuration"]["payload"]["base_url"]+"/rule"
            return ModelInvocationResult(text="按原目标读取真实规则",tool_calls=[ModelToolCall(id="admission-rule",name="api-test-runner",
                arguments={"worker_action":"execute_task","task":{"task_id":"admission-rule","method":"GET","path":path}})],
                response_summary={"mode":"ok","finish_reason":"tool_calls"})
        checks = [{"name":field,"operator":"equals","expected_path_tokens":["expected",field],
            "observations":[{"call_id":"admission-rule","output_path_tokens":["task_result","response_status"]
                if field == "code" else ["task_result","response_body",field]}]}
            for field in ["code","days","expired_count","expired","deleted_at"]]
        return ModelInvocationResult(text=json.dumps({"schema_version":1,"steps":[{"item_key":"independent","status":"passed",
            "summary":"实际读取与固定预期逐字段核验","checks":checks}]},ensure_ascii=False),
            response_summary={"mode":"ok","finish_reason":"stop"})


@asynccontextmanager
async def agent_runtime(f,agent, *, action_admission=False):
    original_admission = f.jobs._case_action_admission
    if action_admission:
        from src.runtime.postgres_case_action_admission import PostgresCaseActionAdmission
        f.jobs._case_action_admission = PostgresCaseActionAdmission(runs=f.runs, jobs=f.jobs._store)
    async with session_stores(f.jobs,True) as (writer,reader):
        tools,skills,control = ToolRegistry(),SkillRegistry(),RuntimeControlRegistry()
        runtime_tools = ToolRuntimeService(tool_job_service=f.jobs,session_store=writer)
        graph = build_agent_graph(agent_registry=AgentRegistry(),tool_registry=tools,model_registry=ControlledModels(),
            skill_registry=skills,skill_runtime_service=SkillRuntimeService(skills),mcp_runtime_service=EmptyMCP(),
            memory_runtime_service=None,test_case_embedding_service=None,permission_service=PermissionService(),
            prompt_assembly_service=PromptAssemblyService(),model_runtime_service=agent,tool_runtime_service=runtime_tools,
            runtime_control=control,tool_job_service=f.jobs)
        runtime = RuntimeService(graph=graph,model_runtime_service=agent,tool_runtime_service=runtime_tools,
            tool_registry=tools,runtime_control=control,tool_job_service=f.jobs,permission_service=PermissionService())
        sessions = SessionService(writer,InputOrchestratorService(ModeRegistry()),runtime,ModeRegistry(),
            project_service=f.env.projects,turn_execution_lease_seconds=15)
        clock = [datetime.now(timezone.utc)]
        runs = RunService(store=f.runs,project_service=f.env.projects,suite_service=None,test_case_service=f.env.cases,
            session_store=writer,tool_job_service=f.jobs,clock=lambda:clock[0])
        adapter = AgentCaseExecutionAdapter(run_service=runs,session_service=sessions,session_store=reader,tool_job_service=f.jobs)
        try: yield runs,adapter,reader,clock
        finally:
            await sessions.close()
            f.jobs._case_action_admission = original_admission


@pytest.mark.asyncio
@LIVE
async def test_actual_agent_graph_result_and_expired_checkpoint_keep_original_admission(tmp_path,retention_http):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,creator = await prepared(f)
        detail = await creator.create(f.project.id,plan.id,CasePlanRunRequest(creation_key="admission-graph",mode_key="api_testing"))
        agent = AdmissionAgent()
        async with agent_runtime(f,agent) as (runs,adapter,reader,clock):
            claims = await f.runs.claim_items(run_id=detail.run.id,worker_id="graph-start",limit=2,lease_seconds=60,now=clock[0])
            entry,attempt = next(row for row in claims if row[0].plan_item_key == "independent")
            await runs.start_item(entry.id,RunItemLeaseRequest(lease_token=attempt.lease_token))
            async def args():
                current = await runs.get_item(entry.id)
                return {"run":await runs.get_record(detail.run.id),"item":current,
                    "case":await f.env.cases.get_case(entry.case_id),"version":await f.env.cases.get_version(entry.case_version_id),
                    "attempt_id":(await runs.get_latest_attempt(entry.id)).id}
            before = [len(calls) for calls in f.actual_target_calls]
            outcome = await adapter.execute(**await args())
            if isinstance(outcome,AgentCaseApprovalWait):
                approval = next(row for row in await reader.list_approvals(outcome.session_id) if row.id == outcome.approval_id)
                await runs.mark_waiting_approval(entry.id,RunItemApprovalWaitRequest(lease_token=attempt.lease_token,
                    approval_id=outcome.approval_id,tool_job_id=outcome.tool_job_id,reason=outcome.summary,
                    approval_scope_hash=approval.metadata["approval_scope_hash"]))
                resumed = await runs.resume_waiting_approval(entry.id,outcome.approval_id)
                await runs.start_item(entry.id,RunItemLeaseRequest(lease_token=resumed.lease_token))
                outcome = await adapter.resume_approval(**await args(),approval_id=outcome.approval_id,
                    payload=ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
            assert outcome.completion.status == "passed" and [len(calls) for calls in f.actual_target_calls] == [before[0],before[1]+1]
            root = await runs.get_latest_attempt(entry.id)
            context = build_agent_case_context(await runs.get(detail.run.id),await runs.get_item(entry.id),
                await f.env.cases.get_version(entry.case_version_id))
            review = await f.decision_client.post(f.decision_routes[1],json=decision_request(f,1,status="unknown",case_status="unknown"))
            assert review.status_code == 201
            clock[0] += timedelta(seconds=91)
            assert (await runs.recover_expired(detail.run.id)).recovered_count >= 1
            claims = await f.runs.claim_items(run_id=detail.run.id,worker_id="graph-recover",limit=2,lease_seconds=60,now=clock[0])
            recovered = next(row for row in claims if row[0].id == entry.id)
            await runs.start_item(entry.id,RunItemLeaseRequest(lease_token=recovered[1].lease_token))
            HTTP_after_first = [len(calls) for calls in f.actual_target_calls]
            model_calls = len(agent.requests)
            repeated = await adapter.execute(**await args())
            assert repeated.completion.actual["environment_admission"] == root.environment_admission.model_dump(mode="json")
            assert len(agent.requests) == model_calls and [len(calls) for calls in f.actual_target_calls] == HTTP_after_first
            result = await runs.complete_item(entry.id,repeated.completion)
            save_decision_evidence(f,"admission-agent-graph",run=(await runs.get(detail.run.id)).model_dump(mode="json"),
                fixed_plan=plan.model_dump(mode="json"),agent_context=context.execution_json(),root_attempt=root.model_dump(mode="json"),
                current_review=review.json(),actual_result=result.model_dump(mode="json"),new_recovery_HTTP=0,new_recovery_model_calls=0,
                model_requests=[request.model_dump(mode="json") for request in agent.requests],controlled_model=True,
                autonomous_external_model_verified=False)
