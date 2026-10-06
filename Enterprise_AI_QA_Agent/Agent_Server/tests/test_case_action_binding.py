"""Frozen ownership must survive config edits and actual tools/approval recovery."""
from copy import deepcopy
from dataclasses import replace
import json
from html import unescape
from pathlib import Path

import pytest

from test_business_applicability import decisions_environment, save_decision_evidence
from test_business_environment_admission import agent_runtime
from test_agent_case_session import ControlledAgent
from test_business_planning import selection
from test_case_planning import SequenceModel, finish, item
from test_requirement_execution import retention_http
from test_requirement_reviews import LIVE
from src.application.runtime.case_action_binding import PROTOCOL, ARGUMENT, action_input_schema, bind_case_action
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.test_runs.agent_case_context import build_agent_case_context
from src.application.test_runs.agent_case_execution import AgentCaseApprovalWait
from src.application.test_runs.plan_run_service import CasePlanRunService
from src.runtime.postgres_business_planning import PostgresBusinessPlanning
from src.registry.tools import ToolRegistry
from src.schemas.case_planning import CasePlanningPolicy, CasePlanningRequest, CasePlanRunRequest
from src.schemas.model_config import ModelInvocationResult
from src.schemas.run_management import RunItemLeaseRequest, RunItemApprovalWaitRequest
from src.schemas.session import ApprovalDecisionRequest, ToolApprovalStatus
from src.schemas.tool_runtime import ModelToolCall


async def owned_plan(f, multiple=True, *, admission=False, environment=True):
    binding = PostgresBusinessPlanning(applicability=f.decision_store)
    f.env.service._environments = f.runs._environments = binding
    config = await f.env.service.get_policy(f.project.id)
    policy = {**config.policy.model_dump(mode="json"), "action_binding_protocol": PROTOCOL}
    if admission:
        policy["action_admission_protocol"] = "case-action-admission-v1"
    response=await f.env.client.put(f"/projects/{f.project.id}/case-plans/policy", json=policy)
    assert response.status_code==200 and response.json()["policy"]["action_binding_protocol"]==PROTOCOL
    def decision(context):
        target = item(context, f.selected.case.case_key, key="target", depends_on=["seed"] if multiple else [])
        if not multiple:
            return finish(target)
        def prep(key, depends):
            return {"key": key, "kind": "prepare", "reason": "验证原固定执行项关联",
                "depends_on": depends, "evidence_ids": target["evidence_ids"],
                "instructions": {"operation": "读取本次固定目标规则", "expected": {"status": 200}}}
        return finish(prep("seed", []), target, prep("after_read", ["target"]))
    f.env.service._models = SequenceModel(decision)
    plan = await f.env.service.create(f.project.id, CasePlanningRequest(objective="检查完整用例和所属动作",
        environment=selection(f,1) if environment else None, candidate_case_ids=[f.selected.case.id]))
    assert plan.status == "planned"
    creator = CasePlanRunService(run_store=f.runs, suite_store=f.suites, plan_store=f.env.store,
        case_service=f.env.cases, project_service=f.env.projects)
    detail = await creator.create(f.project.id, plan.id, CasePlanRunRequest(creation_key="owned-actions", mode_key="api_testing"))
    return plan,detail


def test_legacy_policy_and_saved_context_keep_original_wire():
    assert "action_binding_protocol" not in CasePlanningPolicy().model_dump(mode="json")
    bundle={"case_execution": {"schema_version":1}, "project_id":"legacy"}
    schema={"type":"object","properties":{"_case_action":{"type":"string"}}}
    assert action_input_schema(schema,bundle) is schema
    assert bind_case_action(bundle=bundle, arguments={"_case_action":"business value","nullable":None},
        session_id="original",turn_id="original") is None
    from test_business_planning import test_saved_normal_regression_hash_and_full_old_execution_context_are_unchanged
    test_saved_normal_regression_hash_and_full_old_execution_context_are_unchanged()


def test_saved_actual_wire_stays_fixed_when_handler_edits_nested_arguments():
    from src.application.runtime.case_action_binding import CaseActionBinding, business_arguments
    saved=Path(__file__).resolve().parents[2]/"docs/upgrade_2026-10-03/p4-action-ownership-boundaries-final/applicability-action-ownership-md/verification.json"
    value=json.loads(saved.read_text(encoding="utf-8"))["received_handler_arguments"][0]
    binding=CaseActionBinding.model_validate(value["binding"])
    original={**deepcopy(value["arguments"]),ARGUMENT:{"item_key":binding.item_key}}
    wire=deepcopy(original)
    business=business_arguments(original,binding)
    business["task"]["task_id"]="handler-local-change"
    assert original==wire and ARGUMENT not in business


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension",["md","docx","pdf"])
async def test_actual_three_owned_phases_fixed_policy_and_unmodified_business_parameters(tmp_path,retention_http,extension):
    async with decisions_environment(tmp_path,retention_http,extension) as f:
        plan,detail=await owned_plan(f)
        entry,attempt=(await f.runs.claim_items(run_id=detail.run.id,worker_id="ownership",limit=1,
            lease_seconds=120,now=detail.run.created_at))[0]
        await f.runs.start_item(entry.id,attempt.lease_token,detail.run.created_at)
        current=await f.runs.get_run(detail.run.id)
        entry=next(i for i in current.items if i.id==entry.id)
        context=build_agent_case_context(current,entry,await f.env.cases.get_version(entry.case_version_id))
        assert [s.phase for s in context.steps]==["before","case","after"]
        content=next(s.content_json for s in context.steps if s.phase=="case")
        assert len(content["steps"])==12 and content==f.selected.version.business_content()
        assert content["expected"]["expired"] is False and content["expected"]["expired_count"]==0
        assert content["expected"]["deleted_at"] is None
        original=context.execution_json()
        await f.env.service.save_policy(f.project.id, CasePlanningPolicy())
        saved=await f.env.service.get(f.project.id,plan.id)
        assert saved.config_snapshot.policy.action_binding_protocol==PROTOCOL
        assert build_agent_case_context(await f.runs.get_run(detail.run.id),entry,
            await f.env.cases.get_version(entry.case_version_id)).execution_json()==original
        unsupported=await f.env.client.put(f"/projects/{f.project.id}/case-plans/policy",
            json={"action_binding_protocol":"unknown-protocol"})
        assert unsupported.status_code==422
        tc=ToolExecutionContext(session_id="ownership-session",turn_id="ownership-turn",trace_id="ownership-trace",
            user_message="关联真实动作",normalized_input="关联真实动作",context_bundle={**context.runtime_context(),
                "case_execution_managed":True,"attempt_id":attempt.id})
        runtime=ToolRuntimeService(tool_job_service=f.jobs)
        handler=runtime._handlers["api-test-runner"]
        received=[]
        async def observed(arguments,call_context):
            received.append({"arguments":deepcopy(arguments),"binding":call_context.case_action_binding.model_dump(mode="json")})
            return await handler(arguments,call_context)
        runtime._handlers["api-test-runner"]=observed
        before=[len(calls) for calls in f.actual_target_calls]
        records=[]
        for index,step in enumerate(context.steps):
            args={ARGUMENT:{"item_key":step.item.key},"worker_action":"execute_task",
                "task":{"task_id":"owned-"+str(index),"method":"GET",
                    "path":f.snapshots[1]["configuration"]["payload"]["base_url"]+"/rule"}}
            raw=deepcopy(args)
            record=await runtime.execute(ToolRegistry().get("api-test-runner"),
                ModelToolCall(id="owned-"+str(index),name="api-test-runner",arguments=args),tc)
            assert record.status=="completed"
            job=await f.jobs.get_job(record.job_id)
            assert job.input_payload==raw and record.input==raw and args==raw
            assert received[-1]["arguments"]=={k:v for k,v in raw.items() if k!=ARGUMENT}
            assert received[-1]["binding"]==job.metadata["case_action_binding"]
            assert job.metadata["case_action_binding"]["phase"]==step.phase
            records.append(record)
        assert [len(calls) for calls in f.actual_target_calls]==[before[0],before[1]+3]
        for selected in ({"item_key":"foreign-item"},{"item_key":"target","phase":"after"},None):
            with pytest.raises(ValueError):
                await runtime.execute(ToolRegistry().get("api-test-runner"),ModelToolCall(id="bad",name="api-test-runner",
                    arguments={ARGUMENT:selected}),tc)
        with pytest.raises(ValueError,match="项目"):
            await runtime.execute(ToolRegistry().get("api-test-runner"),
                ModelToolCall(id="foreign-context",name="api-test-runner",arguments={ARGUMENT:{"item_key":"target"}}),
                replace(tc,context_bundle={**tc.context_bundle,"project_id":"foreign-project"}))
        assert len(received)==3 and [len(calls) for calls in f.actual_target_calls]==[before[0],before[1]+3]
        from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
        manifest=await AgentCaseAssessmentService(f.jobs).evidence_manifest(context=context,tool_records=records,
            session_id=tc.session_id,turn_id=tc.turn_id,attempt_id=attempt.id)
        assert [row["case_action_binding"]["phase"] for row in manifest]==["before","case","after"]
        save_decision_evidence(f,"action-ownership-"+extension,fixed_plan=saved.model_dump(mode="json"),
            original_agent_context=original,received_handler_arguments=received,tool_evidence=manifest,
            actual_HTTP_before=before,actual_HTTP_after=[len(calls) for calls in f.actual_target_calls],
            current_policy=(await f.env.service.get_policy(f.project.id)).model_dump(mode="json"),
            new_owned_action_HTTP=3,invalid_selection_HTTP=0,actual_stateful_cleanup_verified=False,
            applicability_action_gate_implemented=False)


class OwnedRuleAgent(ControlledAgent):
    async def invoke(self,key,request):
        self.requests.append(request)
        tool=next(t for t in request.tools if t["name"]=="api-test-runner")
        assert ARGUMENT in tool["input_schema"]["required"]
        assert tool["input_schema"]["properties"][ARGUMENT]["properties"]["item_key"]["enum"]==["target"]
        section=next(s for s in request.system_prompt_sections if s.key=="frozen_case_execution")
        frozen=json.loads(unescape(section.content.split("\n",1)[1].split("\n</untrusted_content>",1)[0]))
        assert frozen["action_binding_protocol"]==PROTOCOL
        if not any(m.get("role")=="tool" for m in request.messages):
            path=frozen["business_environment"]["snapshot"]["configuration"]["payload"]["base_url"]+"/rule"
            return ModelInvocationResult(text="读取所属目标规则",tool_calls=[ModelToolCall(id="owned-rule",name="api-test-runner",
                arguments={ARGUMENT:{"item_key":"target"},"worker_action":"execute_task",
                    "task":{"task_id":"owned-rule","method":"GET","path":path}})],
                response_summary={"mode":"ok","finish_reason":"tool_calls"})
        return ModelInvocationResult(text=json.dumps({"schema_version":1,"steps":[{"item_key":"target","status":"blocked",
            "summary":"只验证调用关联与审批恢复，未提供业务检查","checks":[]}]}),
            response_summary={"mode":"ok","finish_reason":"stop"})


@pytest.mark.asyncio
@LIVE
async def test_actual_original_graph_approval_recovers_bound_call_and_full_tool_wire(tmp_path,retention_http):
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,detail=await owned_plan(f,False)
        agent=OwnedRuleAgent()
        async with agent_runtime(f,agent) as (runs,adapter,reader,clock):
            entry,attempt=(await f.runs.claim_items(run_id=detail.run.id,worker_id="ownership-graph",limit=1,
                lease_seconds=120,now=clock[0]))[0]
            await runs.start_item(entry.id,RunItemLeaseRequest(lease_token=attempt.lease_token))
            async def args():
                return {"run":await runs.get_record(detail.run.id),"item":await runs.get_item(entry.id),
                    "case":await f.env.cases.get_case(entry.case_id),"version":await f.env.cases.get_version(entry.case_version_id),
                    "attempt_id":(await runs.get_latest_attempt(entry.id)).id}
            before=[len(calls) for calls in f.actual_target_calls]
            wait=await adapter.execute(**await args())
            assert isinstance(wait,AgentCaseApprovalWait)
            approval=next(a for a in await reader.list_approvals(wait.session_id) if a.id==wait.approval_id)
            pending=await f.jobs.get_job(wait.tool_job_id)
            assert pending.metadata["case_action_binding"]["item_key"]=="target"
            assert approval.metadata["case_action_binding"]==pending.metadata["case_action_binding"]
            assert approval.metadata["arguments"]==pending.input_payload
            await runs.mark_waiting_approval(entry.id,RunItemApprovalWaitRequest(lease_token=attempt.lease_token,
                approval_id=wait.approval_id,tool_job_id=wait.tool_job_id,reason=wait.summary,
                approval_scope_hash=approval.metadata["approval_scope_hash"]))
            resumed=await runs.resume_waiting_approval(entry.id,wait.approval_id)
            await runs.start_item(entry.id,RunItemLeaseRequest(lease_token=resumed.lease_token))
            outcome=await adapter.resume_approval(**await args(),approval_id=wait.approval_id,
                payload=ApprovalDecisionRequest(decision=ToolApprovalStatus.approved))
            job=await f.jobs.get_job(wait.tool_job_id)
            assert job.status.value=="completed" and job.input_payload==pending.input_payload
            assert job.metadata["case_action_binding"]==pending.metadata["case_action_binding"]
            assert [len(calls) for calls in f.actual_target_calls]==[before[0],before[1]+1]
            assert outcome.completion.status=="blocked" # fixture deliberately supplies no business checks
            save_decision_evidence(f,"action-ownership-graph",fixed_plan=plan.model_dump(mode="json"),
                pending_job=pending.model_dump(mode="json"),actual_job=job.model_dump(mode="json"),
                approval=approval.model_dump(mode="json"),actual_result=outcome.completion.model_dump(mode="json"),
                model_requests=[r.model_dump(mode="json") for r in agent.requests],controlled_model=True,
                new_owned_action_HTTP=1,autonomous_external_model_verified=False,
                applicability_action_gate_implemented=False)


@pytest.mark.asyncio
@LIVE
async def test_actual_deep_governance_approval_and_terminal_replay_keep_owned_job(tmp_path,retention_http):
    from src.application.permissions.permission_service import PermissionPolicyContext, PermissionService
    from src.application.runtime.tool_governance_service import GovernedToolCallContext, ToolGovernanceService
    from src.application.model_adapters.tool_adapter import LangChainToolAdapter
    from src.schemas.session import SessionMode, RuntimeMode, MessageKind
    async with decisions_environment(tmp_path,retention_http) as f:
        plan,detail=await owned_plan(f,False)
        entry,attempt=(await f.runs.claim_items(run_id=detail.run.id,worker_id="owned-governance",limit=1,
            lease_seconds=120,now=detail.run.created_at))[0]
        await f.runs.start_item(entry.id,attempt.lease_token,detail.run.created_at)
        current=await f.runs.get_run(detail.run.id)
        entry=next(i for i in current.items if i.id==entry.id)
        context=build_agent_case_context(current,entry,await f.env.cases.get_version(entry.case_version_id))
        bundle={**context.runtime_context(),"case_execution_managed":True,"attempt_id":attempt.id}
        execution=ToolExecutionContext(session_id="owned-deep",turn_id="owned-deep-turn",trace_id="owned-deep-trace",
            user_message="读取所属执行项",normalized_input="读取所属执行项",context_bundle=bundle)
        registry=ToolRegistry(); runtime=ToolRuntimeService(tool_job_service=f.jobs)
        governance=ToolGovernanceService(tool_registry=registry,permission_service=PermissionService(),
            tool_runtime_service=runtime,tool_job_service=f.jobs)
        governed=GovernedToolCallContext(execution=execution,policy=PermissionPolicyContext(session_mode=SessionMode.normal,
            runtime_mode=RuntimeMode.interactive,selected_agent_key="coordinator",message_kind=MessageKind.user_input,
            submit_mode="immediate",execution_lane="conversation_turn"),active_mode_key="api_testing",
            available_tool_keys=frozenset({"api-test-runner"}))
        call=ModelToolCall(id="owned-deep-call",name="api-test-runner",arguments={ARGUMENT:{"item_key":"target"},
            "worker_action":"execute_task","task":{"task_id":"owned-deep-task","method":"GET",
                "path":f.snapshots[1]["configuration"]["payload"]["base_url"]+"/rule"}})
        before=[len(calls) for calls in f.actual_target_calls]
        pending=await governance.execute(call,governed,prepare_approval=True)
        assert pending.record.status=="waiting_approval" and [len(calls) for calls in f.actual_target_calls]==before
        approval={**pending.approval,"status":"approved"}
        outcome=await governance.execute(call,governed,approval=approval)
        repeated=await governance.execute(call,governed,approval=approval)
        assert outcome.record.status=="completed" and not outcome.replayed
        assert repeated.replayed and repeated.record.job_id==outcome.record.job_id
        assert [len(calls) for calls in f.actual_target_calls]==[before[0],before[1]+1]
        job=await f.jobs.get_job(outcome.record.job_id)
        assert job.input_payload==call.arguments and job.metadata["case_action_binding"]["item_key"]=="target"
        assert approval["metadata"]["case_action_binding"]==job.metadata["case_action_binding"]
        async def never(*args):
            raise AssertionError("schema inspection must not execute a tool")
        wrapped=LangChainToolAdapter().to_governed_tools([registry.get(call.name)],invoke=never,context_bundle=bundle)[0]
        assert ARGUMENT in wrapped.args and ARGUMENT not in registry.get(call.name).input_schema["properties"]
        save_decision_evidence(f,"action-ownership-deep-governance",fixed_plan=plan.model_dump(mode="json"),
            original_agent_context=context.execution_json(),approval=approval,actual_job=job.model_dump(mode="json"),
            actual_result=outcome.record.model_dump(mode="json"),terminal_replayed=True,new_owned_action_HTTP=1,
            governance_actual_PG_and_HTTP=True,full_deep_agent_model_harness_verified=False,
            applicability_action_gate_implemented=False)
